"""Independent device sessions consuming one shared AI display schedule."""
import threading
import uuid

from aiflame.core.events import EventError
from aiflame.devices.registry import migrate_devices, public_record
from aiflame.devices.light_settings import light_settings, migrate_light_settings
from aiflame.transports.manager import TransportManager
from aiflame.outputs.flame import demo_display


class DeviceState(dict):
    def __init__(self, values, changed):
        super().__init__(values)
        self.changed = changed

    def update(self, *args, **kwargs):
        super().update(*args, **kwargs)
        self.changed()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.changed()


class DeviceOutput:
    """Transport-facing state; connection changes never reset another lamp."""
    def __init__(self, fleet, record):
        self.fleet, self.record = fleet, record
        self.last_command = None
        self.last_display_key = None
        self.light_test = None
        self._device = DeviceState({"online": False, "connecting": False,
                                   "status": "未连接设备", "last_error": None}, fleet.refresh)

    @property
    def settings(self):
        return {**self.fleet.engine.settings, **light_settings(self.record)}

    @property
    def device(self):
        return self._device

    @device.setter
    def device(self, value):
        self._device = DeviceState(value, self.fleet.refresh)
        self.last_display_key = None
        self.fleet.refresh()

    @property
    def token(self):
        return self.device.get("session_id"), self.device.get("connected_at")

    def desired(self):
        if self.settings["brightness"] == 0:
            self.light_test = None
            return {"key": "device-muted", "state": "idle", "duration_ms": 0, "device_muted": True}
        result = self.fleet.engine.desired().copy()
        if self.light_test:
            test = self.light_test
            flame = self.fleet.engine
            now = flame.clock()
            if (now >= test["expires"] or self.token != test["connection"]
                    or not self.device.get("online") or flame.settings["mode"] != "hardware"
                    or flame.muted() or result["key"] != test["shared_key"]):
                self.light_test = None
            else:
                return {**demo_display(test["key"], now - test["started"]),
                        "device_test": True}
        current = self.fleet.engine.current
        # Do not replay a completion on a newly attached/rebooted lamp.
        if current and result["key"] == current["id"]:
            recipients = current.get("device_sessions")
            if recipients is not None and recipients.get(self.record.get("device_id")) != self.token:
                return self.task_display()
        if result["state"] == "working" and not result.get("preview"):
            return self.task_display()
        return result

    def task_display(self):
        working = any(item.get("device_id") in (None, self.record.get("device_id"))
                      for item in self.fleet.engine.active.values())
        return {"key": "working" if working else "idle", "state": "working" if working else "idle", "duration_ms": 0}

    def applied(self, desired, ack):
        if self.desired()["key"] != desired["key"]:
            return
        self.last_command, self.last_display_key = ack, desired["key"]
        if desired.get("device_test") or desired.get("device_muted"):
            return
        flame = self.fleet.engine
        flame.applied(desired, ack, self.record["device_id"])
        event_ids = [flame.current["id"]] if flame.current and flame.current["id"] == desired["key"] else (
            [item["id"] for item in flame.active.values() if item.get("device_id") in (None, self.record["device_id"])] if desired["state"] == "working" else
            [row["event"] for row in flame.db.execute("SELECT deliveries.event FROM deliveries JOIN events ON events.id=deliveries.event WHERE output=? AND deliveries.status='queued' AND events.state IN ('idle','cancelled','timeout','unknown')", ("flame:" + self.record["device_id"],)).fetchall()] if desired["state"] == "idle" else [])
        for event_id in event_ids:
            output = "flame:" + self.record["device_id"]
            previous = flame.db.execute("SELECT detail FROM deliveries WHERE event=? AND output=?", (event_id, output)).fetchone()
            flame.core.router.record(event_id, output, "applied", previous["detail"] if previous else None)
        self.fleet.refresh()

    def session_changed(self):
        self.last_command = self.last_display_key = None


class FleetTransportManager:
    def __init__(self, engine, lock, config):
        self.engine, self.lock, self.config = engine, lock, config
        self.records = migrate_devices(config)
        self.connections = {}
        self.outputs = {}
        self.guard = threading.RLock()
        # Keep the existing single-connection lifecycle and its cancellation rules.
        for key, record in self.records.items():
            migrate_light_settings(record)
            self.connection(key, record)
        self.fallback = TransportManager(engine, lock, config)
        self.guard = self.primary().guard

    def connection(self, key, record=None):
        with self.lock:
            if key not in self.connections:
                record = record if record is not None else self.records[key]
                output = DeviceOutput(self, record)
                connection = TransportManager(output, self.lock, record)
                connection.hub_id = self.config.get("hub_id", "")
                self.outputs[key] = output
                self.connections[key] = connection
            return self.connections[key]

    def adopt(self, key, record, connection):
        migrate_light_settings(record)
        device_id = record["device_id"]
        existing = self.connections.get(device_id)
        if existing and existing is not connection:
            existing.stop()
        with self.lock:
            self.records[device_id] = record
            if key != device_id:
                self.connections.pop(key, None)
                self.outputs.pop(key, None)
            self.connections[device_id] = connection
            self.outputs[device_id] = connection.engine
            self.refresh()
        return device_id

    def sessions(self):
        with self.lock:
            return {key: output.token for key, output in self.outputs.items()
                    if key in self.records and output.device.get("online")}

    def enabled_sessions(self):
        return {key: token for key, token in self.sessions().items()
                if self.outputs[key].settings["brightness"] > 0}

    def test_light(self, device_id):
        with self.lock:
            output = self.outputs.get(device_id) if isinstance(device_id, str) else None
            if not output or device_id not in self.records or not output.device.get("online"):
                raise EventError("请先连接当前设备", 409)
            if output.settings["brightness"] == 0:
                raise EventError("此设备亮度为 0%，请在设备配置中提高亮度", 409)
            if self.engine.settings["mode"] != "hardware" or self.engine.muted():
                raise EventError("请切换到硬件模式，并恢复亮度和提醒", 409)
            shared = self.engine.desired()
            key = "device-test:" + uuid.uuid4().hex
            started = self.engine.clock()
            output.light_test = {"key": key, "started": started, "expires": started + 10,
                                 "connection": output.token, "shared_key": shared["key"]}
            return {"device_id": device_id, "key": key + ":working", "remaining_ms": 10000}

    def refresh(self):
        with self.lock:
            states = [dict(output.device) for key, output in self.outputs.items() if key in self.records]
            if not states:
                self.engine.device = {"online": False, "connecting": False, "status": "未连接设备",
                                      "last_error": None, "connected_count": 0, "device_count": 0}
                return
            online = [state for state in states if state.get("online")]
            connecting = any(state.get("connecting") for state in states)
            primary = (online or states)[0]
            self.engine.device = {**primary, "online": bool(online), "connecting": not online and connecting,
                                  "connected_count": len(online), "device_count": len(states),
                                  "status": f"已连接 {len(online)} 台设备" if len(online) > 1 else primary.get("status", "未连接设备")}

    def snapshot(self):
        with self.lock:
            rows = []
            for key, record in self.records.items():
                output = self.connection(key).engine
                desired = output.desired()
                remaining = desired["remaining_ms"] if desired.get("device_test") else 0
                rows.append(public_record(record, {**output.device, "test_remaining_ms": remaining}))
            return rows

    def confirmed(self, key):
        with self.lock:
            current = self.engine.current
            recipients = current.get("device_sessions") if current and current["id"] == key else None
            online = [output for device_id, output in self.outputs.items()
                      if device_id in self.records and output.device.get("online") and output.settings["brightness"] > 0
                      and (recipients is None or recipients.get(device_id) == output.token)
                      and (key != "working" or output.task_display()["state"] == "working")]
            return bool(online) and all(output.last_display_key == key for output in online)

    def start(self):
        # Each lamp owns an independent LAN connection.
        for key, record in list(self.records.items()):
            connection = self.connection(key, record)
            connection.start()
        if not self.records:
            self.fallback.start()

    def suspend(self):
        for connection in list(self.connections.values()) or [self.fallback]:
            connection.suspend()
            with self.lock:
                connection.engine.device.update(online=False, connecting=False)

    def stop(self):
        connections = list(self.connections.values()) or [self.fallback]
        # Signal all workers first so shutdown latency is not multiplied by devices.
        for connection in connections:
            with connection.guard:
                connection.running = False
                with self.lock:
                    if connection.worker:
                        connection.worker.stopping.set()
        errors = []
        for connection in connections:
            try:
                connection.stop()
            except RuntimeError as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def primary(self):
        key = self.config.get("device_id")
        return self.connections.get(key) or next(iter(self.connections.values()), self.fallback)

    # Compatibility for single-device diagnostics and callers. New UI uses IDs.
    @property
    def worker(self):
        return self.primary().worker

    @worker.setter
    def worker(self, value):
        self.primary().worker = value

    @property
    def reaper(self):
        return self.primary().reaper

    @property
    def running(self):
        return self.primary().running

    @running.setter
    def running(self, value):
        self.primary().running = value
