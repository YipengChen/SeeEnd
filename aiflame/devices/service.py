"""USB maintenance jobs. Credentials are never returned or logged.

Only enumerated serial ports and our fixed worker commands can be used. Jobs are
serialized and suspend the live transport before acquiring a serial connection.
"""

import hashlib
import json
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from serial.tools import list_ports
from aiflame.core.events import EventError
from aiflame.core.paths import write_private
from aiflame.core.process import run
from aiflame.devices.base_firmware import bundled_image
from aiflame.devices.firmware.version import FIRMWARE_VERSION
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS, MODELS, CONNECTION_KEYS, hardware_model, firmware_files


class DeviceService:
    def __init__(self, config, runtime, transport, lock, save=None, bind=None):
        self.config, self.runtime = config, Path(runtime)
        self.transport, self.lock = transport, lock
        self.guard = threading.RLock()
        self.job = None
        self.thread = None
        self.uploads = {}
        self.save = save or (lambda: write_private(self.runtime / "credentials.json", self.config))
        self.bind = bind or (lambda device_id: None)

    def ports(self):
        with self.lock:
            if self.transport.engine.settings["mode"] != "hardware":
                return []
            return self._ports()

    def _ports(self):
        return [
            {"port": p.device, "description": p.description, "vid": p.vid, "pid": p.pid}
            for p in sorted(
                list_ports.comports(), key=lambda p: p.device != self.config.get("port")
            )
            if p.vid is not None
        ]

    def status(self):
        with self.lock, self.guard:
            network = self.network_status()
            return {
                "job": dict(self.job) if self.job else None,
                "transport": self.config.get("transport", "usb"),
                "paired_device": self.config.get("device_id", ""),
                "selected_port": self.config.get("port", ""),
                "firmware_version": FIRMWARE_VERSION,
                "hardware_model": self.config.get("hardware_model", DEFAULT_MODEL),
                "models": [hardware_model(model["id"]) for model in MODELS],
                "yeelight_host": self.config.get("yeelight_host", ""),
                "wifi_available": bool(self.config.get("device_id") and (self.config.get("wifi_device_secret") or self.config.get("hardware_model") in YEELIGHT_MODELS)) and not (network["wifi_info_current"] and not network["wifi_ssid"]),
                "connection_paused": bool(self.config.get("connection_paused")),
                **network,
            }

    def network_status(self):
        with self.lock:
            device = self.transport.engine.device
            reported = "wifi_ssid" in device and device.get("device_id") == self.config.get("device_id")
            cached = self.config.get("wifi_ssid") if self.config.get("wifi_ssid_device_id") == self.config.get("device_id") else None
            return {
                "wifi_ssid": device.get("wifi_ssid") if reported else cached,
                "wifi_info_current": bool(device["online"] and reported),
                "wifi_info_known": reported or cached is not None,
                "wifi_connected": bool(device["online"] and device.get("wifi_connected")),
            }

    def stage_image(self, data):
        # ESP image header magic and chip ID: 9 is ESP32-S3. No arbitrary paths.
        if (
            not 1024 <= len(data) <= 16 * 1024 * 1024
            or data[0] != 0xE9
            or int.from_bytes(data[12:14], "little") != 9
        ):
            raise EventError(
                "请选择 ESP32-S3 的完整 MicroPython .bin 固件（从 0x0 写入）"
            )
        key = uuid.uuid4().hex
        digest = hashlib.sha256(data).hexdigest()
        directory = self.runtime / "firmware-cache"
        directory.mkdir(exist_ok=True, mode=0o700)
        with self.guard:
            if self.job and self.job["state"] == "running":
                raise EventError("设备维护正在进行", 409)
            for old in directory.glob("*.bin"):
                old.unlink()
            self.uploads.clear()
            path = directory / (key + ".bin")
            path.write_bytes(data)
            path.chmod(0o600)
            self.uploads[key] = path
        return {"image_id": key, "sha256": digest, "bytes": len(data)}

    def start(self, body):
        action, port = body.get("action"), body.get("port")
        model_id = body.get("hardware_model", self.config.get("hardware_model", DEFAULT_MODEL))
        model = hardware_model(model_id)
        if action != "select_model" and model_id != self.config.get("hardware_model", DEFAULT_MODEL):
            raise EventError("设备型号已改变，请重新选择设备", 409)
        if action != "select_model" and self.transport.engine.settings["mode"] != "hardware":
            raise EventError("请先在总览切换至硬件模式", 409)
        if model_id in YEELIGHT_MODELS and action not in ("select_model", "connect", "disconnect", "discover"):
            raise EventError(f"{model['name']} 在 Yeelight App 中配网，使用原厂程序，无需 USB 维护")
        if action not in ("discover", "select_model", "probe", "provision", "install", "flash_base", "connect", "disconnect"):
            raise EventError("不支持的设备操作")
        if action not in ("discover", "select_model", "connect", "disconnect") and port not in {p["port"] for p in self.ports()}:
            raise EventError("USB 设备不存在，请刷新设备列表")
        values = {"action": action, "port": port, "hardware_model": model_id}
        if action == "provision":
            ssid, password = body.get("ssid"), body.get("password")
            if not isinstance(ssid, str) or not 1 <= len(ssid.encode()) <= 32:
                raise EventError("Wi-Fi 名称应为 1–32 字节")
            if not isinstance(password, str) or not (
                8 <= len(password) <= 63
                or password == ""
                and body.get("open_network") is True
            ):
                raise EventError("Wi-Fi 密码应为 8–63 个字符；开放网络需要明确选择")
            values.update(ssid=ssid, password=password)
        if action == "flash_base":
            if body.get("confirm_flash") is not True:
                raise EventError("请确认覆盖设备程序后再安装底层固件")
            if "image_id" in body:
                with self.guard:
                    image = self.uploads.get(body.get("image_id")) if isinstance(body.get("image_id"), str) else None
                if not image or not image.is_file():
                    raise EventError("请先选择并上传底层固件")
            else:
                if body.get("confirm_board") is not True:
                    raise EventError("请确认开发板为 ESP32-S3-N16R8（16 MB Flash / 8 MB Octal PSRAM）")
                image = bundled_image()
                values["bundled"] = True
            values["image"] = image
        if action == "connect" and model_id in YEELIGHT_MODELS:
            if body.get("transport") != "yeelight":
                raise EventError(f"{model['name']} 仅支持 Yeelight 局域网连接")
            from aiflame.devices.yeelight import lan_host
            host = body.get("host") or self.config.get("yeelight_host")
            try:
                values["host"] = lan_host(host)
            except ValueError as error:
                raise EventError(str(error)) from None
            values["transport"] = "yeelight"
        elif action == "connect":
            mode = body.get("transport")
            if mode not in ("wifi", "usb"):
                raise EventError("请选择 USB 或 Wi-Fi")
            if mode == "wifi" and not self.config.get("wifi_device_secret"):
                raise EventError("请先通过 USB 配网")
            if mode == "usb" and port not in {p["port"] for p in self.ports()}:
                raise EventError("请选择有效 USB 设备")
            values["transport"] = mode
        with self.guard:
            if self.job and self.job["state"] == "running":
                raise EventError("已有设备操作正在进行", 409)
            self.job = {
                "id": uuid.uuid4().hex,
                "action": action,
                "state": "running",
                "progress": 0,
                "message": "正在准备",
                "started_at": time.time(),
            }
            self.thread = threading.Thread(
                target=self._work, args=(values,), daemon=True
            )
            self.thread.start()
            return dict(self.job)

    def update(self, progress, message, **extra):
        with self.guard:
            self.job.update(progress=progress, message=message, **extra)

    def mp(self, port, *args, timeout=90):
        result = run("--mpremote", "connect", port, *args, timeout=timeout)
        if result.returncode:
            # mpremote diagnostics may contain echoed Wi-Fi JSON. Do not forward it.
            raise RuntimeError(
                "USB 操作失败：确认数据线、串口权限和 MicroPython 固件；关闭占用串口的程序后重试"
            )
        return result.stdout

    def probe(self, port):
        code = "import machine,binascii,json,os; print(json.dumps({'device_id':binascii.hexlify(machine.unique_id()).decode(),'board':os.uname().machine}))"
        output = self.mp(port, "exec", code)
        for line in reversed(output.splitlines()):
            try:
                info = json.loads(line)
                if isinstance(info, dict) and isinstance(info.get("device_id"), str):
                    if "ESP32S3" not in info.get("board", "").upper().replace("-", ""):
                        raise RuntimeError("当前灯光程序仅支持 ESP32-S3 与所选型号的 IS31FL3731 灯板")
                    self.bind(info["device_id"])
                    return info
            except ValueError:
                pass
        raise RuntimeError("未识别到 MicroPython 设备；ESP32-S3-N16R8 请先在「初始化 / 恢复开发板」安装内置底层固件")

    def wifi_address(self, port):
        # Probe through USB: broadcast can be blocked by VPNs or router settings.
        code = """import network,json,time
w=network.WLAN(network.STA_IF)
c=json.load(open('wifi.json'))
w.active(False)
w.active(True)
w.connect(c['ssid'],c['password'])
for _ in range(40):
 if w.isconnected(): break
 time.sleep_ms(500)
print(json.dumps({'connected':w.isconnected(),'ip':w.ifconfig()[0]}))
"""
        output = self.mp(port, "exec", code, timeout=35)
        for line in reversed(output.splitlines()):
            try:
                result = json.loads(line)
                if (
                    isinstance(result, dict)
                    and result.get("connected")
                    and result.get("ip") != "0.0.0.0"
                ):
                    import ipaddress

                    return str(ipaddress.IPv4Address(result["ip"]))
            except (ValueError, KeyError):
                pass
        raise RuntimeError(
            "配置已保存，但 Wi-Fi 尚未连通，USB 仍可使用；请检查名称、密码和 2.4 GHz 网络"
        )

    def installed_secret(self, port):
        # Inspect files, never import main.py (which starts the device loop).
        code = """import os,json
required=('main.py','candle.py','matrix.py','matrix_map.py','wifi_protocol.py','device_auth.json')
if all(name in os.listdir() for name in required):
 print(json.dumps({'secret':json.load(open('device_auth.json')).get('secret')}))
else:
 print('{}')
"""
        for line in reversed(self.mp(port, "exec", code).splitlines()):
            try:
                secret = json.loads(line).get("secret")
                if isinstance(secret, str) and len(secret) == 64 and all(c in "0123456789abcdefABCDEF" for c in secret):
                    return secret
            except (ValueError, AttributeError):
                pass
        raise RuntimeError("请先在「设备维护与恢复」安装灯光程序，再保存 Wi-Fi；配网不会自动安装程序。")

    def configure_wifi(self, port, values):
        self.update(15, "正在检查设备与灯光程序")
        info = self.probe(port)
        secret = self.installed_secret(port)
        self.update(35, "正在保存 Wi-Fi 名称和密码")
        with tempfile.TemporaryDirectory(prefix="aiflame-wifi-") as directory:
            wifi = Path(directory) / "wifi.json"
            write_private(wifi, {"ssid": values["ssid"], "password": values["password"]})
            self.mp(port, "fs", "cp", str(wifi), ":wifi.json.new", "+", "exec",
                    "import os; os.rename('wifi.json.new', 'wifi.json')")
        # Keep USB usable even if the new Wi-Fi password is wrong.
        with self.lock:
            self.config.update(device_id=info["device_id"], port=port, transport="usb",
                               connection_paused=False, wifi_device_secret=secret,
                               wifi_ssid=values["ssid"], wifi_ssid_device_id=info["device_id"])
            self.config.pop("wifi_last_ip", None)
            self.save()
        self.update(70, "网络已保存，正在验证 Wi-Fi 连接")
        try:
            address = self.wifi_address(port)
            with self.lock:
                self.config.update(wifi_last_ip=address, transport="wifi")
                self.save()
        finally:
            self.mp(port, "reset")
        return info

    def _work(self, values):
        action, port = values["action"], values.get("port")
        result = {}
        acquired = False
        model = hardware_model(values["hardware_model"])
        try:
            if action == "select_model" and values["hardware_model"] == self.config.get("hardware_model", DEFAULT_MODEL):
                self.update(100, "已选择设备型号", state="succeeded", finished_at=time.time())
                return
            if action == "discover":
                from aiflame.devices.yeelight import discover
                self.update(20, "正在搜索局域网 " + model["name"])
                rows = discover(model_id=model["id"])
                self.update(100, "请选择搜索到的灯具，或手动输入 IP。", state="succeeded",
                            result={"devices": rows}, finished_at=time.time())
                return
            self.update(5, "正在释放设备连接")
            self.transport.stop()
            acquired = True
            if action == "select_model":
                with self.lock:
                    previous = self.config.get("hardware_model", DEFAULT_MODEL)
                    profiles = self.config.setdefault("hardware_profiles", {})
                    profiles[previous] = {key: self.config[key] for key in CONNECTION_KEYS if key in self.config}
                    for key in CONNECTION_KEYS:
                        self.config.pop(key, None)
                    self.config.update({key: value for key, value in profiles.get(values["hardware_model"], {}).items() if key in CONNECTION_KEYS})
                    self.config.update(hardware_model=values["hardware_model"], connection_paused=True)
                    self.transport.engine.device = {"online": False, "connecting": False, "status": "已主动断开", "last_error": None}
                    self.save()
                self.update(100, "已切换型号，请连接所选设备。", state="succeeded", finished_at=time.time())
                return
            if action == "disconnect":
                self.config["connection_paused"] = True
                self.save()
                with self.lock:
                    self.transport.engine.device.update(online=False, connecting=False, status="已主动断开", last_error=None)
                self.update(100, "已断开连接；保留配对与网络，可随时重新连接。", state="succeeded", finished_at=time.time())
                return
            if action == "connect" and values["hardware_model"] in YEELIGHT_MODELS:
                from aiflame.devices.yeelight import discover, YeelightClient, channel_properties, matches_model
                self.update(25, "正在识别 Yeelight 灯具")
                rows = [row for row in discover(values["host"], model_id=model["id"]) if matches_model(row, model["id"])]
                if not rows:
                    rows = [row for row in discover(model_id=model["id"]) if row["host"] == values["host"] and matches_model(row, model["id"])]
                if not rows:
                    raise RuntimeError(f"未识别到 {model['name']}，请确认 IP、所选型号、同一局域网，并开启 Yeelight 局域网控制")
                info = rows[0]
                expected = self.config.get("device_id")
                if expected and expected != info["device_id"]:
                    raise RuntimeError("灯具身份与所选设备不一致，请选择添加新设备")
                client = YeelightClient(info["host"])
                try:
                    client.open()
                    response = client.command("get_prop", channel_properties(model["id"]))
                    if len(response["result"]) != 3 or response["result"][0] not in ("on", "off"):
                        raise RuntimeError("灯具未提供所选型号的 RGB 控制")
                finally:
                    client.close()
                self.bind(info["device_id"])
                with self.lock:
                    self.config.update(device_id=info["device_id"], transport="yeelight",
                                       yeelight_host=info["host"], yeelight_port=info["port"],
                                       connection_paused=False)
                    self.save()
                result = {"device_id": info["device_id"], "board": model["name"] + " / " + info["model"]}
            elif action == "connect":
                if not self.config.get("device_id") and values["transport"] == "usb":
                    info = self.probe(port)
                    result = info
                    secret = self.installed_secret(port)
                    self.config.update(device_id=info["device_id"], wifi_device_secret=secret)
                    self.mp(port, "reset")
                self.config["connection_paused"] = False
                self.config["transport"] = values["transport"]
                if port:
                    self.config["port"] = port
                self.save()
            elif action == "provision":
                result = self.configure_wifi(port, values)
            elif action == "flash_base":
                if values.get("bundled"):
                    self.update(10, "正在清除开发板程序和网络配置，请保持 USB 连接")
                    erased = run(
                        "--esptool", "--chip", "esp32s3", "--port", port,
                        "erase-flash", timeout=180,
                    )
                    if erased.returncode:
                        raise RuntimeError("开发板擦除失败，请检查 USB 连接与 BOOT 模式后重试初始化")
                    with self.lock:
                        self.config.update(device_id="", transport="usb", port=port)
                        self.config.pop("wifi_device_secret", None)
                        self.config.pop("wifi_last_ip", None)
                        self.config.pop("wifi_ssid", None)
                        self.config.pop("wifi_ssid_device_id", None)
                        self.save()
                self.update(
                    20,
                    "正在安装底层固件，请保持 USB 连接（可能需要按住 BOOT 后按 RESET）",
                )
                response = run(
                    "--esptool",
                    "--chip",
                    "esp32s3",
                    "--port",
                    port,
                    "--baud",
                    "460800",
                    "write-flash",
                    "0x0",
                    str(values["image"]),
                    timeout=180,
                )
                if response.returncode:
                    raise RuntimeError("底层固件写入失败，请检查 USB 连接与 BOOT 模式")
                self.update(90, "底层固件已安装；下一步请安装灯光程序，再按需配置 Wi-Fi")
            else:
                self.update(15, "正在识别 ESP32-S3")
                info = self.probe(port)
                result = info
                if action == "probe":
                    self.mp(port, "reset")
                else:
                    secret = (
                        self.config.get("wifi_device_secret")
                        if info["device_id"] == self.config.get("device_id")
                        else None
                    )
                    secret = secret or secrets.token_hex(32)
                    self.update(35, "正在传输 AI Flame 灯光程序")
                    with tempfile.TemporaryDirectory(
                        prefix="aiflame-usb-"
                    ) as directory:
                        files = [(path, name) for name, path in firmware_files(values.get("hardware_model", DEFAULT_MODEL)).items()]
                        auth = Path(directory) / "device_auth.json"
                        write_private(auth, {"secret": secret})
                        files.append((auth, "device_auth.json"))
                        args = []
                        for source, target in files:
                            args += [
                                "fs",
                                "cp",
                                str(source),
                                ":" + target + ".new",
                                "+",
                            ]
                        # Stage all files before replacing the running application.
                        commit = "import os\n" + "\n".join(
                            "os.rename(%r, %r)" % (target + ".new", target)
                            for _, target in files
                        )
                        args += ["exec", commit]
                        self.mp(port, *args)
                    self.update(80, "灯光程序已写入，正在保存配对并重启设备")
                    with self.lock:
                        if info["device_id"] != self.config.get("device_id"):
                            self.config.pop("wifi_ssid", None)
                            self.config.pop("wifi_ssid_device_id", None)
                        self.config["connection_paused"] = False
                        self.config.update(
                            device_id=info["device_id"],
                            port=port,
                            wifi_device_secret=secret,
                        )
                        self.save()
                    self.mp(port, "reset")
            self.update(95, "正在恢复设备连接")
            self.transport.start()
            message = (
                "底层固件已安装，请在「设备维护与恢复」安装灯光程序，再按需配置 Wi-Fi"
                if action == "flash_base"
                else "操作完成；连接状态以首页设备回执为准"
            )
            self.update(
                100, message, state="succeeded", result=result, finished_at=time.time()
            )
        except Exception as error:
            if acquired and action in ("probe", "provision", "install"):
                try:
                    self.mp(port, "reset", timeout=5)
                except Exception:
                    pass
            try:
                if not self.transport.worker or not self.transport.worker.is_alive():
                    self.transport.start()
            except Exception:
                pass
            message = (
                "USB 操作超时，请检查连接和 BOOT 模式"
                if isinstance(error, subprocess.TimeoutExpired)
                else str(error)
            )
            self.update(100, message, state="failed", finished_at=time.time())
        finally:
            values.pop("password", None)

    def busy(self):
        with self.guard:
            return bool(self.job and self.job["state"] == "running")
