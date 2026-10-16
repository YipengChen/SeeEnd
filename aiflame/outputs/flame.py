"""Fire light output: scheduling, display preferences, device state and acknowledgements."""
import json
from aiflame.core.events import EventError

DEMO_DURATION_SECONDS = 15


def demo_display(key, elapsed):
    """Shared working, waiting-for-input, completed sequence: five seconds each."""
    if elapsed >= DEMO_DURATION_SECONDS:
        return None
    state, end = ("working", 5) if elapsed < 5 else ("needs_input", 10) if elapsed < 10 else ("done", 15)
    return {
        "key": f"{key}:{state}",
        "state": state,
        "duration_ms": max(1, int((end - elapsed) * 1000)),
        "remaining_ms": max(1, int((DEMO_DURATION_SECONDS - elapsed) * 1000)),
    }


class FlameOutput:
    name = "flame"
    DEFAULTS = {
        "hardware_notifications": False,
        "brightness": 20,
        "duration": 10,
        "hold_until_activity": True,
        "reduced": False,
    }

    @staticmethod
    def validate_settings(data):
        if type(data["hardware_notifications"]) is not bool:
            raise EventError("设备提醒设置格式错误")
        if type(data["brightness"]) is not int or not 0 <= data["brightness"] <= 25:
            raise EventError("样机亮度范围为 0–25%")
        if type(data["duration"]) is not int or not 3 <= data["duration"] <= 60:
            raise EventError("提示时长范围为 3–60 秒")
        for key in ("reduced", "hold_until_activity"):
            if type(data[key]) is not bool:
                raise EventError("设置格式错误")


    def __init__(self, core):
        self.core = core
        self.clock, self.wall, self.db = core.clock, core.wall, core.db
        self.queue = []
        self.current = None
        self.preview = None
        self.emergency = False
        self.dropped = 0
        self.device = {"online": False, "status": "未连接设备", "last_error": None}
        self.last_command = None
        self.last_display_key = None
        self.transport = None

    @property
    def settings(self):
        return self.core.settings

    @property
    def active(self):
        return self.core.active

    def cleanup(self):
        self.core.cleanup()
        self.core.tick()

    def mark(self, event_id, status):
        previous = self.db.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
        # Ending a displayed prompt cannot undo an acknowledgement already received.
        if not (previous and previous["status"] == "applied" and status in
                ("suppressed", "superseded", "expired", "ended_on_reconnect", "dropped")):
            self.core.router.record(event_id, self.name, status)
        if status not in ("queued", "applied", "screen"):
            self.settle_targets(event_id, status)

    def settle_targets(self, event_id, status):
        rows = self.db.execute("SELECT output, detail FROM deliveries WHERE event=? AND output LIKE 'flame:%' AND status='queued'", (event_id,)).fetchall()
        for row in rows:
            self.core.router.record(event_id, row["output"], status, row["detail"])

    def record_targets(self, event_id, result, target=None):
        from aiflame.devices.models import hardware_model, DEFAULT_MODEL
        from aiflame.devices.light_settings import light_settings
        for key, record in getattr(self.transport, "records", {}).items():
            if target is not None and key != target:
                continue
            output = self.transport.outputs.get(key)
            status = result
            if result == "queued":
                status = ("screen" if not self.settings["hardware_notifications"] else
                          "disabled" if record.get("connection_paused") else
                          "muted" if light_settings(record)["brightness"] == 0 else
                          "offline" if not output or not output.device.get("online") else "queued")
            elif result == "suppressed" and light_settings(record)["brightness"] == 0:
                status = "muted"
            detail = json.dumps({"name": hardware_model(record.get("hardware_model", DEFAULT_MODEL))["name"]}, ensure_ascii=False)
            self.core.router.record(event_id, "flame:" + key, status, detail)

    def validate_target(self, event):
        target = event.get("device_id")
        if target is None:
            return
        if not isinstance(target, str) or (target != self.device.get("device_id") and not (
                self.transport and target in getattr(self.transport, "records", {}))):
            raise EventError("未知目标设备", 422)

    def stop(self):
        self.emergency = True
        self.cancel_preview()
        self.clear_prompts("suppressed")

    def accept(self, event):
        key = (event["source_id"], event["task_id"])
        eid, now = event["event_id"], self.clock()
        # An older state of this task is no longer eligible for an ACK.
        old = self.db.execute("SELECT id, status FROM events WHERE source=? AND task=? AND id!=? AND (status='queued' OR EXISTS(SELECT 1 FROM deliveries WHERE event=events.id AND output LIKE 'flame:%' AND status='queued'))", (*key, eid)).fetchall()
        for row in old:
            if row["status"] == "queued":
                self.mark(row["id"], "superseded")
            else:
                self.settle_targets(row["id"], "superseded")
        if not self.settings["source_enabled"].get(key[0], False):
            self.mark(eid, "unmatched")
            self.record_targets(eid, "unmatched", event.get("device_id"))
            return "unmatched"
        result = "suppressed" if self.muted() else "queued"
        self.cancel_preview()
        if event["phase"] == "lease_expired":
            self.mark(event["previous_event_id"], "lease_expired")
        if event["state"] in ("idle", "unknown", "cancelled", "timeout", "working", "done", "needs_input"):
            for item in list(self.queue):
                if item["key"] == key:
                    self.queue.remove(item)
                    self.mark(item["id"], "superseded")
            if self.current and self.current["key"] == key:
                # Keep a confirmed receipt as history when its prompt ends.
                if not self.current["applied"]:
                    self.mark(self.current["id"], "superseded")
                self.current = None
        if result == "queued" and event["state"] in ("done", "needs_input"):
            state = event["state"]
            item = {
                "id": eid, "key": key, "state": state,
                "priority": 2 if state == "needs_input" else 1,
                "arrived": now, "expiry": event["expires"],
                "duration": self.settings["duration"],
                "holding": state == "done" and self.settings["hold_until_activity"],
                "deadline": None, "applied": False,
                "screen_only": not self.device["online"],
            }
            if self.transport and hasattr(self.transport, "sessions"):
                item["device_sessions"] = (self.transport.enabled_sessions() if hasattr(self.transport, "enabled_sessions") else self.transport.sessions())
                if event.get("device_id") is not None:
                    item["device_sessions"] = {key: token for key, token in item["device_sessions"].items()
                                               if key == event["device_id"]}
            self.queue.append(item)
            if len(self.queue) > 20:
                victim = min(self.queue, key=lambda x: (x["priority"], x["arrived"]))
                self.queue.remove(victim)
                self.mark(victim["id"], "dropped")
                self.dropped += 1
                if victim["id"] == eid:
                    result = "dropped"
        self.mark(eid, result)
        self.record_targets(eid, result, event.get("device_id"))
        return result

    def settings_changed(self):
        enabled = self.settings["source_enabled"]
        if not self.settings["hardware_notifications"] or self.muted():
            self.cancel_preview()
        if self.muted():
            self.clear_prompts("suppressed")
        for item in list(self.queue):
            if not enabled.get(item["key"][0], False):
                self.queue.remove(item)
                self.mark(item["id"], "unmatched")
        if self.current and not enabled.get(self.current["key"][0], False):
            self.mark(self.current["id"], "unmatched")
            self.current = None

    def brightness_off(self):
        records = getattr(self.transport, "records", {})
        if self.settings["hardware_notifications"] and records:
            from aiflame.devices.light_settings import light_settings
            return all(light_settings(record)["brightness"] == 0 for record in records.values())
        return self.settings["brightness"] == 0

    def muted(self):
        return self.emergency or self.brightness_off()

    def clear_prompts(self, result):
        for item in self.queue + ([self.current] if self.current else []):
            if item:
                self.mark(item["id"], result)
        self.queue = []
        self.current = None

    def start_preview(self, identifier):
        self.cleanup()
        if self.core.unfinished_task_count():
            raise EventError("有任务尚未结束，暂时无法演示灯效。", 409)
        if not self.settings["hardware_notifications"] or not self.device["online"]:
            raise EventError("请先连接设备，再开始同步灯效演示", 409)
        if self.muted():
            raise EventError("灯光提醒不可用，请检查灯光亮度或重新打开 SeeEnd。", 409)
        display = self.desired()
        if self.preview or display["state"] != "idle":
            raise EventError("当前有任务或演示正在进行，请结束后再试", 409)
        self.preview = {
            "id": identifier,
            "started": self.clock(),
            "connection": self.preview_connection(),
        }

    def cancel_preview(self, identifier=None):
        if self.preview and (identifier is None or self.preview["id"] == identifier):
            self.preview = None

    def preview_connection(self):
        return self.device.get("session_id"), self.device.get("connected_at")

    def preview_display(self):
        if not self.preview:
            return None
        elapsed = self.clock() - self.preview["started"]
        if (
            elapsed >= DEMO_DURATION_SECONDS or self.muted() or not self.settings["hardware_notifications"]
            or not self.device["online"]
            or self.preview["connection"] != self.preview_connection()
        ):
            self.cancel_preview()
            return None
        return {
            **demo_display(f"preview:{self.preview['id']}", elapsed),
            "preview": self.preview["id"],
        }

    def desired(self):
        self.cleanup()
        now = self.clock()
        # Reconcile queued starts left by older versions or rapid state changes.
        active_ids = {item["id"] for item in self.active.values()}
        for row in self.db.execute("SELECT events.id, events.state, events.seq, tasks.seq AS latest FROM events JOIN tasks ON events.source=tasks.source AND events.task=tasks.task WHERE events.status='queued' AND events.state IN ('working','idle','cancelled','timeout','unknown')").fetchall():
            if row["seq"] < row["latest"] or (row["state"] == "working" and row["id"] not in active_ids):
                self.mark(row["id"], "superseded")
        # Offline prompts belong to the app. Never replay them on a newly
        # connected lamp, and never manufacture a device acknowledgement.
        if self.device["online"]:
            for item in list(self.queue):
                if item["screen_only"]:
                    self.queue.remove(item)
                    self.mark(item["id"], "ended_on_reconnect")
            if self.current and self.current["screen_only"]:
                self.mark(self.current["id"], "ended_on_reconnect")
                self.current = None
        for item in list(self.queue):
            if (item.get("deadline") and item["deadline"] <= now) or (not item.get("holding") and not item.get("deadline") and item["expiry"] <= now):
                self.queue.remove(item)
                self.mark(item["id"], "expired")
        if self.muted():
            self.cancel_preview()
            self.clear_prompts("suppressed")
            return {"key": "muted", "state": "idle", "duration_ms": 0}
        preview = self.preview_display()
        if preview:
            return preview
        if self.current:
            if self.current["deadline"] and now >= self.current["deadline"]:
                self.settle_targets(self.current["id"], "expired")
                self.current = None
            elif (
                not self.current["deadline"]
                and not self.current["applied"]
                and (not self.current.get("holding") or not self.current["screen_only"])
                and now >= self.current["expiry"]
            ):
                self.mark(self.current["id"], "expired")
                self.current = None
        if self.current and self.current.get("device_sessions") and not self.current["screen_only"]:
            recipients = self.current["device_sessions"]
            sessions = (self.transport.enabled_sessions() if hasattr(self.transport, "enabled_sessions") else self.transport.sessions())
            if not any(sessions.get(key) == token for key, token in recipients.items()):
                self.mark(self.current["id"], "ended_on_reconnect")
                self.current = None
        if (
            self.current
            and self.current["state"] == "done"
            and any(x["state"] == "needs_input" for x in self.queue)
        ):
            old = self.current
            remaining = (old["deadline"] - now) if old["deadline"] else old["duration"]
            if remaining > 0:
                if not old.get("activity_countdown"):
                    old.update(duration=remaining, deadline=None, applied=False)
                self.queue.append(old)
            self.current = None
        if not self.current and self.queue:
            self.queue.sort(key=lambda x: (-x["priority"], x["arrived"]))
            self.current = self.queue.pop(0)
        if self.current:
            item = self.current
            if not self.device["online"] and not item["deadline"]:
                item["screen_only"] = True
                if not item.get("holding"):
                    item["deadline"] = now + item["duration"]
                self.mark(item["id"], "screen")
            seconds = (
                max(0.05, item["deadline"] - now)
                if item["deadline"]
                else item["duration"]
            )
            return {
                "key": item["id"],
                "state": item["state"],
                "duration_ms": 0 if item.get("holding") else int(seconds * 1000),
                "holding": item.get("holding", False),
            }
        if not self.device["online"]:
            for row in self.db.execute("SELECT id FROM events WHERE status='queued' AND state IN ('working','idle','cancelled','timeout','unknown')").fetchall():
                self.mark(row["id"], "screen")
        return {
            "key": "working" if self.active else "idle",
            "state": "working" if self.active else "idle",
            "duration_ms": 0,
        }

    def human_activity(self, occurred_at):
        """Start one countdown, only for input after a completion arrived."""
        for item in ([self.current] if self.current else []) + self.queue:
            if item.get("holding") and occurred_at > item["arrived"]:
                item["holding"] = False
                item["activity_countdown"] = True
                item["deadline"] = self.clock() + item["duration"]

    def applied(self, desired, ack, device_id=None):
        if desired.get("preview"):
            current = self.preview_display()
            if current and current["key"] == desired["key"]:
                self.last_command = ack
                self.last_display_key = desired["key"]
            return
        self.last_command = ack
        self.last_display_key = desired["key"]
        if self.current and self.current["id"] == desired["key"]:
            if not self.current["applied"]:
                self.current["applied"] = True
                if not self.current.get("holding") and not self.current["deadline"]:
                    self.current["deadline"] = self.clock() + self.current["duration"]
            self.mark(self.current["id"], "applied")
        elif desired["state"] == "working":
            for item in self.active.values():
                if device_id is None or item.get("device_id") in (None, device_id):
                    self.mark(item["id"], "applied")
        elif desired["state"] == "idle":
            for row in self.db.execute("SELECT id FROM events WHERE state IN ('idle','cancelled','timeout','unknown') AND status='queued'").fetchall():
                if device_id is None or self.db.execute("SELECT 1 FROM deliveries WHERE event=? AND output=? AND status='queued'", (row["id"], "flame:" + device_id)).fetchone():
                    self.mark(row["id"], "applied")

    def session_changed(self):
        self.cancel_preview()
        if self.current and self.current["applied"]:
            self.mark(self.current["id"], "ended_on_reconnect")
            self.current = None

    def display_status(self):
        display = self.desired()
        if display.get("preview"):
            display["task_state"] = display["state"]
            display["simulated"] = True
            display["device_confirmed"] = self.transport.confirmed(display["key"]) if self.transport and hasattr(self.transport, "confirmed") and self.transport.records else self.last_display_key == display["key"]
            return display
        ids = (
            [self.current["id"]]
            if self.current
            else [item["id"] for item in self.active.values()]
            if display["state"] == "working"
            else []
        )
        records = [
            row
            for event_id in ids
            if (
                row := self.db.execute(
                    "SELECT state, status, (source IN ('manual','demo') OR EXISTS(SELECT 1 FROM self_tests WHERE event=events.id)) AS simulated FROM events WHERE id=?",
                    (event_id,),
                ).fetchone()
            )
        ]
        # Physical lights share the error pattern for input requests. Desktop
        # indicators retain the task meaning without changing that pattern.
        display["task_state"] = (
            records[0]["state"]
            if self.current and records and self.current["id"] == display["key"]
            else display["state"]
        )
        display["simulated"] = any(row["simulated"] for row in records)
        display["device_confirmed"] = bool(
            self.device["online"]
            and (
                all(row["status"] == "applied" for row in records)
                and len(records) == len(ids)
                if ids
                else self.last_display_key == display["key"]
            )
        )
        if self.transport and hasattr(self.transport, "confirmed") and self.transport.records:
            display["device_confirmed"] = self.transport.confirmed(display["key"])
            display["confirmed_count"] = sum(output.device.get("online", False) and output.last_display_key == display["key"] for output in self.transport.outputs.values())
            display["connected_count"] = len(self.transport.sessions())
        return display
