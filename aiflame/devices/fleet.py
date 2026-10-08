"""Device maintenance targets a physical instance; browsing never disconnects it."""
import threading
import uuid
from pathlib import Path

from aiflame.core.events import EventError
from aiflame.core.paths import write_private
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS, hardware_model
from aiflame.devices.service import DeviceService
from aiflame.devices.light_settings import light_settings, validate_patch


class FleetDeviceService:
    def __init__(self, config, runtime, transport, lock):
        self.config, self.runtime, self.transport, self.lock = config, Path(runtime), transport, lock
        self.guard = threading.RLock()
        self.active = None
        self.upload_service = DeviceService(config, runtime, transport.fallback, lock)
        if transport.records:
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

    def ports(self):
        return self.upload_service.ports()

    def _ports(self):
        return self.upload_service._ports()

    def stage_image(self, data):
        if self.busy():
            raise EventError("设备维护正在进行", 409)
        return self.upload_service.stage_image(data)

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
                          light_settings=light_settings(record or {}),
                          device=dict(manager.engine.device),
                          devices=[item for item in self.transport.snapshot() if item["hardware_model"] == model_id])
            return result

    def update_light_settings(self, body):
        if set(body) != {"hardware_model", "device_id", "settings"} or not isinstance(body["device_id"], str) or not body["device_id"]:
            raise EventError("请先添加并选择要配置的设备", 409)
        validate_patch(body["settings"])
        with self.lock:
            record = self.target(body["hardware_model"], body["device_id"])
            updated = {**light_settings(record), **body["settings"]}
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

    def start(self, body):
        action = body.get("action")
        model_id = body.get("hardware_model", self.config.get("hardware_model", DEFAULT_MODEL))
        hardware_model(model_id)
        if action == "discover" and model_id not in YEELIGHT_MODELS:
            raise EventError("此型号不使用 Yeelight 局域网搜索")
        if action == "select_model":
            # UI selection is not a connection operation and has no shared job.
            return {"id": uuid.uuid4().hex, "action": action, "state": "succeeded", "progress": 100,
                    "message": "已打开设备配置；其他设备继续同步。"}
        with self.guard:
            if self.busy():
                raise EventError("请等待当前设备维护完成", 409)
            record = self.target(model_id, body.get("device_id"))
            port = body.get("port")
            # A USB path belongs to one physical record even when the UI adds another.
            port_record = next((item for item in self.transport.records.values() if port and item.get("port") == port), None)
            if record and port_record and record is not port_record:
                raise EventError("此 USB 串口属于另一台设备，请选择正确设备", 409)
            if not record and port_record:
                if port_record.get("hardware_model", DEFAULT_MODEL) != model_id:
                    raise EventError("此 USB 串口属于另一型号，请先选择对应设备", 409)
                record = port_record
            if action == "disconnect" and not record:
                raise EventError("请先选择要断开的设备", 409)
            record = record if record is not None else {"hardware_model": model_id, "port": port or ""}
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
                    if port:
                        record["port"] = port
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
            service.uploads = self.upload_service.uploads
            self.active = service
            result = service.start({**body, "hardware_model": model_id})
            if service.job:
                service.job.update(device_id=original_id or "", hardware_model=model_id)
            return result
