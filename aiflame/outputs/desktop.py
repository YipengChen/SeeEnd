"""Durable system-notification outbox, independent of the fire light."""
import json
import uuid

from aiflame.core.events import EventError


class DesktopOutput:
    name = "desktop"
    LABELS = {"done": "任务已完成", "needs_input": "任务等待你的输入",
              "cancelled": "任务已取消", "timeout": "任务已超时", "unknown": "任务状态未知"}

    def __init__(self, core):
        self.core, self.db = core, core.db
        self.generation = str(uuid.uuid4())
        self.preference = core.settings["desktop_notifications"]
        self.source_preferences = dict(core.settings["source_enabled"])
        self.source_generations = {}
        self.db.execute("""CREATE TABLE IF NOT EXISTS notification_outbox(
            event TEXT PRIMARY KEY, payload TEXT, expires REAL, claim TEXT)""")
        # Do not replay stale notifications after restarting the service.
        self.db.execute("UPDATE deliveries SET status='interrupted' WHERE output='desktop' AND status IN ('pending','claimed')")
        self.db.execute("DELETE FROM notification_outbox")
        self.db.commit()

    def accept(self, event):
        # Even a claimed request becomes obsolete when its task moves on.
        for row in self.db.execute("""SELECT n.event FROM notification_outbox n JOIN events e ON e.id=n.event
                WHERE e.source=? AND e.task=?""",
                (event["source_id"], event["task_id"])).fetchall():
            self.core.router.record(row["event"], self.name, "superseded")
            self.db.execute("DELETE FROM notification_outbox WHERE event=?", (row["event"],))
        self.db.commit()
        if not self.core.settings["source_enabled"].get(event["source_id"], False):
            return "unmatched"
        if not self.core.settings["desktop_notifications"]:
            return "disabled"
        state = event["state"]
        if state == "unknown" and event.get("reason") == "failed":
            return "not_requested"
        if state not in self.LABELS:
            return "not_requested"
        source = self.core.sources.get(event["source_id"])
        payload = {"event_id": event["event_id"], "source_id": event["source_id"],
                   "task_id": event["task_id"], "seq": event["seq"], "title": "SeeEnd · " + self.LABELS[state],
                   "body": source.name if source else event["source_id"]}
        self.db.execute("INSERT INTO notification_outbox VALUES (?,?,?,NULL)",
                        (event["event_id"], json.dumps(payload), self.core.wall() + max(0, event["expires"] - self.core.clock())))
        self.db.commit()
        return "pending"

    def settings_changed(self):
        preference = self.core.settings["desktop_notifications"]
        if preference != self.preference:
            self.generation = str(uuid.uuid4())
            self.preference = preference
        for source, enabled in self.core.settings["source_enabled"].items():
            if enabled != self.source_preferences.get(source):
                self.source_generations[source] = str(uuid.uuid4())
        self.source_preferences = dict(self.core.settings["source_enabled"])
        for row in self.db.execute("SELECT event FROM notification_outbox").fetchall():
            event = self.db.execute("SELECT source FROM events WHERE id=?", (row["event"],)).fetchone()
            if not self.core.settings["desktop_notifications"] or not event or not self.core.settings["source_enabled"].get(event["source"], False):
                self.core.router.record(row["event"], self.name, "disabled")
                self.db.execute("DELETE FROM notification_outbox WHERE event=?", (row["event"],))
        self.db.commit()

    def expire(self):
        now = self.core.wall()
        for row in self.db.execute("SELECT event, claim FROM notification_outbox WHERE expires<=?", (now,)).fetchall():
            self.core.router.record(row["event"], self.name, "unconfirmed" if row["claim"] else "expired")
        self.db.execute("DELETE FROM notification_outbox WHERE expires<=?", (now,))
        self.db.commit()

    def claim(self):
        self.settings_changed()
        self.expire()
        result = []
        for row in self.db.execute("SELECT * FROM notification_outbox WHERE claim IS NULL ORDER BY rowid LIMIT 10").fetchall():
            token = str(uuid.uuid4())
            self.db.execute("UPDATE notification_outbox SET claim=? WHERE event=?", (token, row["event"]))
            self.core.router.record(row["event"], self.name, "claimed")
            payload = json.loads(row["payload"])
            result.append({**payload, "claim": token,
                           "generation": self.generation + self.source_generations.get(payload["source_id"], ""),
                           "expires": row["expires"]})
        self.db.commit()
        return result

    def validate_claim(self, body):
        if (not isinstance(body, dict) or set(body) != {"event_id", "claim", "generation"}
                or any(not isinstance(value, str) for value in body.values())):
            raise EventError("通知领取验证格式错误")
        self.settings_changed()
        self.expire()
        row = self.db.execute("""SELECT n.claim, e.source, e.seq, t.seq AS latest
            FROM notification_outbox n JOIN events e ON e.id=n.event
            JOIN tasks t ON t.source=e.source AND t.task=e.task WHERE n.event=?""",
            (body["event_id"],)).fetchone()
        return {"valid": bool(row and row["claim"] == body["claim"]
                              and body["generation"] == self.generation + self.source_generations.get(row["source"], "")
                              and row["seq"] == row["latest"])}

    def acknowledge(self, body):
        if set(body) != {"event_id", "claim", "status"} or any(not isinstance(value, str) for value in body.values()) or body["status"] not in ("submitted", "denied", "unavailable", "failed"):
            raise EventError("通知回执格式错误")
        row = self.db.execute("SELECT claim FROM notification_outbox WHERE event=?", (body["event_id"],)).fetchone()
        if not row or not row["claim"] or row["claim"] != body["claim"]:
            raise EventError("通知回执已失效", 409)
        self.core.router.record(body["event_id"], self.name, body["status"])
        self.db.execute("DELETE FROM notification_outbox WHERE event=?", (body["event_id"],))
        self.db.commit()
        return {"status": body["status"]}
