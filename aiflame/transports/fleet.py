"""Independent device sessions consuming one shared AI display schedule."""
import threading
import uuid
import hashlib
import json

from aiflame.core.events import EventError
from aiflame.devices.models import YEELIGHT_CUBE_MODEL
from aiflame.devices.cube_effects import CUBE_MODES
from aiflame.devices.registry import migrate_devices, public_record
from aiflame.devices.light_settings import light_settings, migrate_light_settings
from aiflame.transports.manager import TransportManager
from aiflame.outputs.flame import DEMO_DURATION_SECONDS, demo_display


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


class DisplayRequest(dict):
    """Display values plus immutable, transport-local receipt provenance."""
    def __init__(self, values, context, event_ids):
        super().__init__(values)
        self.delivery_context, self.event_ids = context, event_ids

    def copy(self):
        return DisplayRequest(self, self.delivery_context, self.event_ids)


class DeviceOutput:
    """Transport-facing state; connection changes never reset another lamp."""
    def __init__(self, fleet, record):
        self.fleet, self.record = fleet, record
        self.last_command = None
        self.last_display_key = None
        self.last_delivery_context = None
        self.appearance = None
        self.appearance_version = 0
        self.connection_version = 0
        self.admission_id = None
        self.idle_cache_key = None
        self.idle_recipients = ()
        self.idle_deadline = 0
        self.light_test = None
        self.cube_slots = [None] * 4
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
        self.last_delivery_context = None
        self.fleet.refresh()

    @property
    def token(self):
        return self.device.get("session_id"), self.device.get("connected_at")

    def desired(self):
        result = self._desired()
        if (self.record.get("hardware_model") == YEELIGHT_CUBE_MODEL
                and self.settings.get("reminder_mode") in CUBE_MODES):
            result = self.cube_display(result)
        return self.request(result)

    def request(self, result):
        flame, device_id = self.fleet.engine, self.record.get("device_id")
        event_ids, revision = (), None
        if not any(result.get(field) for field in ("preview", "device_test", "device_muted")):
            if flame.current and flame.current["id"] == result["key"]:
                event_ids = (flame.current["id"],)
                revision = event_ids
            elif result["state"] == "working":
                event_ids = tuple(sorted(item["id"] for item in flame.active.values()
                                         if item.get("device_id") in (None, device_id)))
                revision = event_ids
            elif result["state"] == "idle":
                # The newest admitted event remains the display revision after its
                # receipt is recorded, while the captured recipients stay exact.
                revision = self.admission_id
                cache_key = (self.connection_version, self.token, revision)
                if cache_key != self.idle_cache_key or flame.wall() >= self.idle_deadline:
                    rows = flame.db.execute("""SELECT e.id, json_extract(d.detail,'$.expires') AS expires FROM deliveries d
                        JOIN events e ON e.id=d.event JOIN tasks t ON t.source=e.source AND t.task=e.task AND t.seq=e.seq
                        WHERE d.output=? AND d.status IN ('queued','sent')
                        AND e.state IN ('idle','cancelled','timeout','unknown')
                        AND t.state IN ('idle','cancelled','timeout','unknown')
                        AND json_extract(d.detail,'$.session[0]') IS ?
                        AND json_extract(d.detail,'$.session[1]') IS ?
                        AND json_extract(d.detail,'$.expires')>?""",
                        ("flame:" + device_id, *self.token, flame.wall())).fetchall() if device_id else []
                    self.idle_recipients = tuple(row["id"] for row in rows)
                    self.idle_deadline = min((row["expires"] for row in rows), default=float("inf"))
                    self.idle_cache_key = cache_key
                event_ids = self.idle_recipients
        appearance = json.dumps((light_settings(self.record), flame.settings["hardware_notifications"]),
                                sort_keys=True, separators=(",", ":"))
        if appearance != self.appearance:
            self.appearance, self.appearance_version = appearance, self.appearance_version + 1
        display = {field: result.get(field) for field in
                   ("key", "state", "cube_state", "cube_tasks", "holding", "preview", "device_test", "device_muted")}
        fingerprint = hashlib.sha256(json.dumps(display, sort_keys=True).encode()).hexdigest()
        context = (self.connection_version, self.token, self.appearance_version, fingerprint, revision)
        return DisplayRequest(result, context, event_ids)

    def _desired(self):
        if self.settings["brightness"] == 0:
            self.light_test = None
            return {"key": "device-muted", "state": "idle", "duration_ms": 0, "device_muted": True}
        result = self.fleet.engine.desired().copy()
        if self.light_test:
            test = self.light_test
            flame = self.fleet.engine
            now = flame.clock()
            if ((test["expires"] is not None and now >= test["expires"]) or self.token != test["connection"]
                    or not self.device.get("online") or not flame.settings["hardware_notifications"]
                    or flame.muted() or result["key"] != test["shared_key"]
                    or flame.core.unfinished_task_count()):
                self.light_test = None
            else:
                if test.get("state"):
                    return {"key": test["key"] + ":" + test["state"], "state": test["state"],
                            "duration_ms": 30000, "remaining_ms": 0, "device_test": True,
                            "holding": True, "continuous": True}
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

    def cube_display(self, result):
        """Preserve the current prompt's exact state (including needs_input)."""
        if result["state"] == "idle" or result.get("preview") or result.get("device_test"):
            if result["state"] == "idle":
                self.cube_slots = [None] * 4
            return result
        flame = self.fleet.engine
        current = flame.current
        state = result["state"]
        if current and current["id"] == result["key"]:
            row = flame.db.execute("SELECT state FROM events WHERE id=?", (current["id"],)).fetchone()
            if row:
                state = row["state"]
        result = {**result, "cube_state": state}
        if self.settings.get("reminder_mode") != "status":
            return result
        tasks = {key: "working" for key, item in flame.active.items()
                 if item.get("device_id") in (None, self.record.get("device_id"))}
        prompts = [item for item in ([current] if current else []) + flame.queue
                   if not item.get("screen_only") and
                   item.get("device_sessions", {}).get(self.record.get("device_id")) == self.token]
        if prompts:
            placeholders = ','.join('?' for _ in prompts)
            states = {row["id"]: row["state"] for row in flame.db.execute(
                f"SELECT id, state FROM events WHERE id IN ({placeholders})",
                [item["id"] for item in prompts])}
            tasks.update((item["key"], states.get(item["id"], item["state"])) for item in prompts)
        self.cube_slots = [key if key in tasks else None for key in self.cube_slots]
        # The scheduled prompt must be visible before its normal delivery ACK.
        priority = current["key"] if current in prompts else None
        if priority is not None and priority not in self.cube_slots:
            slot = self.cube_slots.index(None) if None in self.cube_slots else 3
            self.cube_slots[slot] = priority
        for key in tasks:
            if key not in self.cube_slots and None in self.cube_slots:
                self.cube_slots[self.cube_slots.index(None)] = key
        return {**result, "cube_tasks": tuple((key, tasks[key]) if key else None
                                               for key in self.cube_slots)}

    def task_display(self):
        working = any(item.get("device_id") in (None, self.record.get("device_id"))
                      for item in self.fleet.engine.active.values())
        return {"key": "working" if working else "idle", "state": "working" if working else "idle", "duration_ms": 0}

    def applied(self, desired, ack):
        context = getattr(desired, "delivery_context", None)
        current = self.desired()
        if (context is None or context != current.delivery_context
                or not set(desired.event_ids).issubset(current.event_ids)):
            return
        self.last_command, self.last_display_key = ack, desired["key"]
        self.last_delivery_context = context
        if desired.get("device_test") or desired.get("device_muted"):
            return
        flame = self.fleet.engine
        flame.applied(desired, ack, self.record["device_id"])
        for event_id in desired.event_ids:
            output = "flame:" + self.record["device_id"]
            previous = flame.db.execute("SELECT detail FROM deliveries WHERE event=? AND output=?", (event_id, output)).fetchone()
            status = "applied" if ack.get("result") == "applied" else "sent"
            saved = flame.db.execute("SELECT status FROM deliveries WHERE event=? AND output=?", (event_id, output)).fetchone()
            if not (saved and saved["status"] == "applied" and status == "sent"):
                flame.core.router.record(event_id, output, status, previous["detail"] if previous else None)
        self.fleet.refresh()

    def session_changed(self):
        self.last_command = self.last_display_key = None
        self.last_delivery_context = None
        self.connection_version += 1


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

    def test_light(self, device_id, state=None):
        if state not in (None, "working", "needs_input", "done", "stop"):
            raise EventError("请选择有效的预览状态")
        with self.lock:
            output = self.outputs.get(device_id) if isinstance(device_id, str) else None
            if state == "stop":
                if output:
                    output.light_test = None
                return {"device_id": device_id, "remaining_ms": 0}
            if not output or device_id not in self.records or not output.device.get("online"):
                raise EventError("请先连接当前设备", 409)
            if state is None and output.settings["brightness"] == 0:
                raise EventError("此设备亮度为 0%，请在设备配置中提高亮度", 409)
            if not self.engine.settings["hardware_notifications"] or self.engine.muted():
                raise EventError("请开启设备提醒，并恢复亮度和提醒", 409)
            shared = self.engine.desired()
            if self.engine.core.unfinished_task_count():
                raise EventError("有任务尚未结束，暂时无法演示灯效。", 409)
            started = self.engine.clock()
            current = output.light_test
            if (state is not None and current and current.get("state") == state
                    and current["connection"] == output.token
                    and current["shared_key"] == shared["key"]):
                return {"device_id": device_id, "key": current["key"] + ":" + state,
                        "remaining_ms": 0, "continuous": True}
            key = "device-test:" + uuid.uuid4().hex
            output.light_test = {"key": key, "started": started, "expires": None if state else started + DEMO_DURATION_SECONDS,
                                 "connection": output.token, "shared_key": shared["key"], "state": state}
            return {"device_id": device_id, "key": key + ":" + (state or "working"), "remaining_ms": 0 if state else DEMO_DURATION_SECONDS * 1000,
                    "continuous": state is not None}

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
                rows.append(public_record(record, {**output.device, "test_remaining_ms": remaining,
                                                   "test_continuous": bool(desired.get("continuous"))}))
            return rows

    def output_confirmed(self, output, key):
        return (output.record.get("device_id") in self.records and output.device.get("online", False)
                and output.settings["brightness"] > 0 and output.last_display_key == key
                and (output.last_command or {}).get("result") == "applied"
                and output.last_delivery_context == output.desired().delivery_context)

    def confirmed(self, key):
        with self.lock:
            current = self.engine.current
            recipients = current.get("device_sessions") if current and current["id"] == key else None
            online = [output for device_id, output in self.outputs.items()
                      if device_id in self.records and output.device.get("online") and output.settings["brightness"] > 0
                      and (recipients is None or recipients.get(device_id) == output.token)
                      and (key != "working" or output.task_display()["state"] == "working")]
            return bool(online) and all(self.output_confirmed(output, key) for output in online)

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
