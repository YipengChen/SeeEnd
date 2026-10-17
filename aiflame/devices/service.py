"""Discovery and connection jobs for original-firmware lights and plugs."""
import threading
import time
import uuid
from pathlib import Path
from aiflame.core.events import EventError
from aiflame.core.paths import write_private
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS, MIJIA_PLUG_MODELS, MODELS, CONNECTION_KEYS, hardware_model

class DeviceService:
    def __init__(self, config, runtime, transport, lock, save=None, bind=None, known_devices=()):
        self.config, self.runtime = config, Path(runtime)
        self.transport, self.lock = transport, lock
        self.guard = threading.RLock()
        self.job = None
        self.thread = None
        self.save = save or (lambda: write_private(self.runtime / "credentials.json", self.config))
        self.bind = bind or (lambda device_id: None)
        self.known_devices = tuple(dict(record) for record in known_devices)

    def status(self):
        with self.lock, self.guard:
            return {
                "job": dict(self.job) if self.job else None,
                "transport": self.config.get("transport", "yeelight"),
                "paired_device": self.config.get("device_id", ""),
                "selected_port": self.config.get("port", ""),
                "hardware_model": self.config.get("hardware_model", DEFAULT_MODEL),
                "models": [hardware_model(model["id"]) for model in MODELS],
                "yeelight_host": self.config.get("yeelight_host", ""),
                "miio_host": self.config.get("miio_host", ""),
                "miio_did": self.config.get("miio_did", ""),
                "connection_paused": bool(self.config.get("connection_paused")),
            }

    def start(self, body):
        action = body.get("action")
        model_id = body.get("hardware_model", self.config.get("hardware_model", DEFAULT_MODEL))
        model = hardware_model(model_id)
        if action != "select_model" and model_id != self.config.get("hardware_model", DEFAULT_MODEL):
            raise EventError("设备型号已改变，请重新选择设备", 409)
        if action != "select_model" and not self.transport.engine.settings["hardware_notifications"]:
            raise EventError("请先开启设备提醒", 409)
        if action not in ("discover", "select_model", "connect", "disconnect"):
            raise EventError("不支持的设备操作")
        values = {"action": action, "hardware_model": model_id}
        if action == "discover" and model_id in MIJIA_PLUG_MODELS:
            from aiflame.devices.mijia import lan_host
            try:
                values["host"] = lan_host(body["host"]) if body.get("host") else None
            except ValueError as problem:
                raise EventError(str(problem)) from None
        if action == "connect":
            if model_id in MIJIA_PLUG_MODELS:
                if body.get("transport") != "miio":
                    raise EventError("此插座需要米家局域网连接")
                from aiflame.devices.mijia import lan_host
                host = body.get("host") or self.config.get("miio_host")
                try:
                    host = lan_host(host) if host else None
                except ValueError as problem:
                    raise EventError(str(problem)) from None
                values.update(transport="miio", host=host,
                    did=str(body.get("did") or self.config.get("miio_did", "")), token=body.get("_token"))
                if not values["did"].isdigit() or not 0 < int(values["did"]) < 0xffffffff:
                    raise EventError("插座身份无效，请重新扫码接入")
            elif body.get("transport") != "yeelight":
                raise EventError(f"{model['name']} 仅支持 Yeelight 局域网连接")
            else:
                from aiflame.devices.yeelight import lan_host
                host = body.get("host") or self.config.get("yeelight_host")
                try:
                    values["host"] = lan_host(host)
                except ValueError as error:
                    raise EventError(str(error)) from None
                values["transport"] = "yeelight"
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

    def _work(self, values):
        action = values["action"]
        result = {}
        model = hardware_model(values["hardware_model"])
        try:
            if action == "select_model" and values["hardware_model"] == self.config.get("hardware_model", DEFAULT_MODEL):
                self.update(100, "已选择设备型号", state="succeeded", finished_at=time.time())
                return
            if action == "discover":
                if model["id"] in MIJIA_PLUG_MODELS:
                    from aiflame.devices.mijia import discover
                    self.update(20, "正在扫描局域网米家设备")
                    rows = discover(values.get("host"))
                    self.update(100, "扫描仅用于发现 IP 和身份；请扫码获取插座凭据。", state="succeeded",
                                result={"devices": rows}, finished_at=time.time())
                    return
                from aiflame.devices.yeelight import discover_with_known_devices
                self.update(20, "正在搜索局域网 " + model["name"])
                with self.lock:
                    known_devices = (*self.known_devices, dict(self.config))
                rows = discover_with_known_devices(known_devices, model_id=model["id"])
                self.update(100, "请选择搜索到的灯具，或手动输入 IP。", state="succeeded",
                            result={"devices": rows}, finished_at=time.time())
                return
            self.update(5, "正在释放设备连接")
            self.transport.stop()
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
            if action == "connect" and model["id"] in MIJIA_PLUG_MODELS:
                from aiflame.devices.mijia import discover, PlugClient
                from aiflame.devices.token_store import TokenStore
                store = TokenStore(self.runtime, service=self.config.get("miio_token_ref"))
                physical_id = "miio:" + values["did"]
                token = values.get("token") or store.get(physical_id)
                if not token:
                    raise RuntimeError("请先扫码登录并选择插座")
                self.update(25, "正在验证插座身份和局域网凭据")
                rows = discover(values["host"]) if values["host"] else []
                info = next((row for row in rows if row["did"] == values["did"]), None)
                if not info:
                    info = next((row for row in discover() if row["did"] == values["did"]), None)
                if not info:
                    raise RuntimeError("未发现此插座，请确认电脑和插座在同一局域网")
                if self.config.get("device_id") not in (None, physical_id):
                    raise RuntimeError("插座身份与所选设备不一致")
                client = PlugClient(info["host"], token, values["did"])
                try:
                    client.open()
                    if client.command("miIO.info")["result"].get("model") != model["lan_model"]:
                        raise RuntimeError("设备型号不匹配，未保存凭据")
                    client.power()
                finally:
                    client.close()
                self.bind(physical_id)
                self.update(70, "插座已验证，正在保存受保护的设备凭据")
                store.set(physical_id, token)
                with self.lock:
                    self.config.update(device_id=physical_id, transport="miio", miio_did=values["did"],
                        miio_host=info["host"], miio_token_ref=store.service, connection_paused=False)
                    self.save()
                result = {"device_id": physical_id, "board": model["name"]}
            elif action == "connect" and values["hardware_model"] in YEELIGHT_MODELS:
                from aiflame.devices.yeelight import discover, YeelightClient, verify_control, matches_model
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
                # Maintenance must not create a second Cube UDP session just
                # before the persistent renderer. Closing a local UDP socket
                # does not explicitly end its device-side session. TCP setters work on CubeLite
                # 1.0.0 even though get_prop does not; verify_control uses one.
                client = YeelightClient(info["host"])
                try:
                    self.update(45, "已识别灯具，正在连接控制端口 " + info["host"] + ":55443")
                    client.open()
                    self.update(65, "控制端口已接通，正在验证灯具控制回执")
                    verify_control(client, model["id"])
                finally:
                    client.close()
                self.bind(info["device_id"])
                with self.lock:
                    self.config.update(device_id=info["device_id"], transport="yeelight",
                                       yeelight_host=info["host"], yeelight_port=info["port"],
                                       connection_paused=False)
                    self.save()
                result = {"device_id": info["device_id"], "board": model["name"] + " / " + info["model"]}
            self.update(95, "正在恢复设备连接")
            self.transport.start()
            message = "操作完成；连接状态以首页设备回执为准"
            self.update(
                100, message, state="succeeded", result=result, finished_at=time.time()
            )
        except Exception as error:
            try:
                if not self.transport.worker or not self.transport.worker.is_alive():
                    self.transport.start()
            except Exception:
                pass
            self.update(100, str(error), state="failed", finished_at=time.time())
        finally:
            values.pop("token", None)

    def busy(self):
        with self.guard:
            return bool(self.job and self.job["state"] == "running")
