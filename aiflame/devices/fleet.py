"""Device maintenance targets a physical instance; browsing never disconnects it."""
import threading
import uuid
from pathlib import Path

from aiflame.core.events import EventError
from aiflame.core.paths import write_private
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODEL, YEELIGHT_MODELS, SUPPORTED_MODELS, MIJIA_PLUG_MODEL, MIJIA_PLUG_MODELS, CONNECTION_KEYS, hardware_model
from aiflame.devices.service import DeviceService
from aiflame.devices.light_settings import light_settings, validate_patch, validate_flash_cycle


class FleetDeviceService:
    def __init__(self, config, runtime, transport, lock):
        self.config, self.runtime, self.transport, self.lock = config, Path(runtime), transport, lock
        self.guard = threading.RLock()
        self.active = None
        from aiflame.devices.mijia_cloud import MijiaLogin
        self.mijia = MijiaLogin()
        write_private(self.runtime / "credentials.json", self.config)

    @property
    def job(self):
        if self.active:
            with self.active.guard:
                return dict(self.active.job) if self.active.job else None
        return None

    @property
    def thread(self):
        return self.active.thread if self.active else None

    def busy(self):
        return bool(self.active and self.active.busy())




    def target(self, model_id, device_id=None):
        hardware_model(model_id)
        if device_id:
            record = self.transport.records.get(device_id)
            if not record or record.get("hardware_model", DEFAULT_MODEL) != model_id:
                raise EventError("请选择此型号下已添加的设备", 409)
            return record
        if device_id is None:
            return next((record for record in self.transport.records.values()
                         if record.get("hardware_model", DEFAULT_MODEL) == model_id), None)
        return None

    def status(self, model_id=None, device_id=None):
        model_id = model_id or self.config.get("hardware_model", DEFAULT_MODEL)
        with self.lock:
            record = self.target(model_id, device_id)
            manager = self.transport.connection(record["device_id"]) if record else self.transport.connection(
                "new:" + model_id, {"hardware_model": model_id, "connection_paused": True})
            service = DeviceService(record or {"hardware_model": model_id}, self.runtime, manager, self.lock)
            result = service.status()
            result.update(job=self.job, hardware_model=model_id,
                          selected_device_id=record["device_id"] if record else "",
                          light_settings=light_settings(record or {"hardware_model": model_id}),
                          device=dict(manager.engine.device),
                          devices=[item for item in self.transport.snapshot() if item["hardware_model"] == model_id])
            if model_id in MIJIA_PLUG_MODELS:
                result["mijia_login"] = self.mijia.status()
            return result

    def update_light_settings(self, body):
        if set(body) != {"hardware_model", "device_id", "settings"} or not isinstance(body["device_id"], str) or not body["device_id"]:
            raise EventError("请先添加并选择要配置的设备", 409)
        validate_patch(body["settings"], body["hardware_model"])
        with self.lock:
            record = self.target(body["hardware_model"], body["device_id"])
            updated = {**light_settings(record), **body["settings"]}
            if body["hardware_model"] in MIJIA_PLUG_MODELS:
                updated["state_power"] = {**light_settings(record)["state_power"], **body["settings"].get("state_power", {})}
                updated["brightness"] = 100
            if body["hardware_model"] in YEELIGHT_MODELS:
                for field in ("front_state_brightness", "front_state_temperature", "front_flash"):
                    updated[field] = {**light_settings(record)[field], **body["settings"].get(field, {})}
                if "front_flash" in body["settings"]:
                    validate_flash_cycle(updated["front_flash"])
            if "colors" in body["settings"]:
                if body["hardware_model"] not in YEELIGHT_MODELS:
                    raise EventError("此设备不支持 RGB 颜色配置")
                updated["colors"] = {**light_settings(record)["colors"],
                                     **{key: value.upper() for key, value in body["settings"]["colors"].items()}}
            if body["hardware_model"] in YEELIGHT_MODELS:
                updated["reduced"] = False
                if "brightness" in body["settings"]:
                    field = "front_state_brightness" if updated.get("reminder_mode") == "front" else "state_brightness"
                    updated[field] = {key: updated["brightness"] for key in updated[field]}
                if "state_brightness" in body["settings"]:
                    updated["state_brightness"] = {**light_settings(record)["state_brightness"],
                                                   **body["settings"]["state_brightness"]}
                    updated["brightness"] = max(updated["state_brightness"].values())
                if body["hardware_model"] in YEELIGHT_MODELS:
                    active_field = "front_state_brightness" if updated["reminder_mode"] == "front" else "state_brightness"
                    updated["brightness"] = max(updated[active_field].values())
            saved = {**self.config, "devices": {**self.transport.records,
                     body["device_id"]: {**record, "light_settings": updated}}}
            write_private(self.runtime / "credentials.json", saved)
            record["light_settings"] = updated
            if updated["brightness"] == 0:
                flame = self.transport.engine
                for item in flame.queue + ([flame.current] if flame.current else []):
                    item.get("device_sessions", {}).pop(body["device_id"], None)
            self.transport.engine.settings_changed()
            return {"device_id": body["device_id"], "light_settings": updated,
                    "message": "此设备的灯光设置已保存"}

    def delete(self, body):
        if (set(body) != {"hardware_model", "device_id"}
                or body["hardware_model"] not in SUPPORTED_MODELS
                or not isinstance(body["device_id"], str) or not body["device_id"]):
            raise EventError("请选择要删除的设备", 422)
        with self.guard:
            if self.busy():
                raise EventError("请等待当前设备维护完成", 409)
            with self.lock:
                record = self.target(body["hardware_model"], body["device_id"])
                manager = self.transport.connection(record["device_id"])
                was_running = manager.running
            manager.stop()
            try:
                with self.lock:
                    device_id = record["device_id"]
                    saved = {**self.config, "devices": {key: value for key, value in self.transport.records.items() if key != device_id}}
                    if saved.get("device_id") == device_id:
                        for key in CONNECTION_KEYS:
                            saved.pop(key, None)
                    if "hardware_profiles" in saved:
                        saved["hardware_profiles"] = {key: value for key, value in saved["hardware_profiles"].items()
                                                      if value.get("device_id") != device_id}
                    write_private(self.runtime / "credentials.json", saved)
                    if record.get("hardware_model") in MIJIA_PLUG_MODELS:
                        from aiflame.devices.token_store import TokenStore
                        try:
                            TokenStore(self.runtime, service=record["miio_token_ref"]).delete(device_id)
                        except (OSError, RuntimeError):
                            # Keep the record available for a retry if the vault denies cleanup.
                            write_private(self.runtime / "credentials.json", self.config)
                            raise
                    self.config.clear()
                    self.config.update(saved)
                    self.transport.records = self.config["devices"]
                    self.transport.connections.pop(device_id, None)
                    self.transport.outputs.pop(device_id, None)
                    flame = self.transport.engine
                    for item in flame.queue + ([flame.current] if flame.current else []):
                        item.get("device_sessions", {}).pop(device_id, None)
                    self.transport.refresh()
            except (OSError, RuntimeError) as problem:
                if was_running:
                    manager.start()
                if record.get("hardware_model") in MIJIA_PLUG_MODELS:
                    raise EventError("系统未允许清除插座凭据，设备已保留；请允许凭据访问后重试", 409) from None
                raise
            return {**self.status(body["hardware_model"], ""), "deleted_device_id": device_id,
                    "message": "此设备已删除"}

    def start(self, body):
        action = body.get("action")
        model_id = body.get("hardware_model", self.config.get("hardware_model", DEFAULT_MODEL))
        hardware_model(model_id)
        if action == "discover" and model_id not in SUPPORTED_MODELS:
            raise EventError("此型号不使用 Yeelight 局域网搜索")
        if action == "select_model":
            # UI selection is not a connection operation and has no shared job.
            return {"id": uuid.uuid4().hex, "action": action, "state": "succeeded", "progress": 100,
                    "message": "已打开设备配置；其他设备继续同步。"}
        with self.guard:
            if self.busy():
                raise EventError("请等待当前设备维护完成", 409)
            record = self.target(model_id, body.get("device_id"))
            if action == "disconnect" and not record:
                raise EventError("请先选择要断开的设备", 409)
            record = record if record is not None else {"hardware_model": model_id}
            key = record.get("device_id") or "new:" + model_id
            manager = self.transport.connection(key, record)
            # A read-only status request may have created this draft already.
            if key.startswith("new:"):
                manager.config = record
                manager.engine.record = record
            original_id = record.get("device_id")
            current_key = [key]

            def bind(physical_id):
                if original_id and original_id != physical_id:
                    raise RuntimeError("设备身份与所选设备不一致，未修改其他设备")
                other = self.transport.connections.get(physical_id)
                if other and other is not manager:
                    saved = self.transport.records[physical_id]
                    if saved.get("hardware_model", DEFAULT_MODEL) != model_id:
                        raise RuntimeError("设备型号不匹配，请选择正确型号")
                    other.stop()
                    record.update(saved)
                    current_key[0] = self.transport.adopt(current_key[0], record, manager)
                    with self.lock:
                        write_private(self.runtime / "credentials.json", self.config)

            def save():
                if not record.get("device_id") and original_id:
                    record.update(device_id=original_id, connection_paused=True)
                if record.get("device_id"):
                    current_key[0] = self.transport.adopt(current_key[0], record, manager)
                with self.lock:
                    write_private(self.runtime / "credentials.json", self.config)

            service = DeviceService(record, self.runtime, manager, self.lock, save=save, bind=bind)
            self.active = service
            result = service.start({**body, "hardware_model": model_id})
            if service.job:
                service.job.update(device_id=original_id or "", hardware_model=model_id)
            return result

    def mijia_action(self, body):
        if body.get("action") == "cancel" and set(body) == {"action", "id"}:
            if not isinstance(body["id"], str) or not body["id"]:
                raise EventError("扫码会话无效")
            self.mijia.cancel(body["id"])
            return self.mijia.status() or {}
        if not self.transport.engine.settings["hardware_notifications"]:
            raise EventError("请先开启设备提醒", 409)
        action = body.get("action")
        if action == "login" and set(body) == {"action", "region"}:
            return self.mijia.start(body["region"])
        if action == "import" and set(body) == {"action", "id", "did", "host"}:
            if not all(isinstance(body[key], str) for key in ("id", "did", "host")):
                raise EventError("插座接入信息无效")
            if self.busy():
                raise EventError("请等待设备维护完成", 409)
            # Validate the host before consuming the short-lived selection.
            from aiflame.devices.mijia import lan_host
            try:
                host = lan_host(body["host"]) if body["host"] else None
            except ValueError as problem:
                raise EventError(str(problem)) from None
            device = self.mijia.take(body["id"], body["did"])
            try:
                return self.start({"action": "connect", "hardware_model": device.get("hardware_model", MIJIA_PLUG_MODEL), "device_id": "",
                    "transport": "miio", "did": device["did"], "host": host,
                    "_token": device["token"]})
            finally:
                device.clear()
        raise EventError("不支持的米家接入操作")

    def close(self):
        self.mijia.cancel()
