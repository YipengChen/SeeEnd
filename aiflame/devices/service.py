"""Yeelight discovery and connection jobs for original-firmware lamps."""
import threading
import time
import uuid
from pathlib import Path
from aiflame.core.events import EventError
from aiflame.core.paths import write_private
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS, MODELS, CONNECTION_KEYS, hardware_model

class DeviceService:
    def __init__(self, config, runtime, transport, lock, save=None, bind=None):
        self.config, self.runtime = config, Path(runtime)
        self.transport, self.lock = transport, lock
        self.guard = threading.RLock()
        self.job = None
        self.thread = None
        self.save = save or (lambda: write_private(self.runtime / "credentials.json", self.config))
        self.bind = bind or (lambda device_id: None)

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
                "connection_paused": bool(self.config.get("connection_paused")),
            }

    def start(self, body):
        action = body.get("action")
        model_id = body.get("hardware_model", self.config.get("hardware_model", DEFAULT_MODEL))
        model = hardware_model(model_id)
        if action != "select_model" and model_id != self.config.get("hardware_model", DEFAULT_MODEL):
            raise EventError("设备型号已改变，请重新选择设备", 409)
        if action != "select_model" and self.transport.engine.settings["mode"] != "hardware":
            raise EventError("请先在总览切换至硬件模式", 409)
        if action not in ("discover", "select_model", "connect", "disconnect"):
            raise EventError("不支持的设备操作")
        values = {"action": action, "hardware_model": model_id}
        if action == "connect":
            if body.get("transport") != "yeelight":
                raise EventError(f"{model['name']} 仅支持 Yeelight 局域网连接")
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
                from aiflame.devices.yeelight import discover
                self.update(20, "正在搜索局域网 " + model["name"])
                rows = discover(model_id=model["id"])
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
                    self.update(45, "已识别灯具，正在连接控制端口 " + info["host"] + ":55443")
                    client.open()
                    self.update(65, "控制端口已接通，正在等待灯具状态回执")
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

    def busy(self):
        with self.guard:
            return bool(self.job and self.job["state"] == "running")
