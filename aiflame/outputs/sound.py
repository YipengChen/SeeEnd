"""One-shot sound requests consumed by the native desktop application."""
import json
import uuid

from aiflame.core.events import EventError


class SoundOutput:
    name = "sound"
    CATEGORIES = ("chime", "pop", "cat")
    MAX_DELAY_SECONDS = 15

    def __init__(self, core):
        self.core, self.db = core, core.db
        self.generation = str(uuid.uuid4())
        self.preference = (core.settings["sound_notifications"], core.settings["sound_category"])
        self.source_preferences = dict(core.settings["source_enabled"])
        self.source_generations = {}
        self.db.execute("""CREATE TABLE IF NOT EXISTS sound_outbox(
            event TEXT PRIMARY KEY, payload TEXT, expires REAL, claim TEXT)""")
        # Audio belongs to a live event, never to a previous app session.
        self.db.execute("UPDATE deliveries SET status='interrupted' WHERE output='sound' AND status IN ('pending','claimed')")
        self.db.execute("DELETE FROM sound_outbox")
        self.db.commit()

    def accept(self, event):
        # An unplayed input prompt is obsolete when its task changes state.
        rows = self.db.execute("""SELECT s.event FROM sound_outbox s JOIN events e ON e.id=s.event
            WHERE e.source=? AND e.task=?""",
            (event["source_id"], event["task_id"])).fetchall()
        for row in rows:
            self._remove(row["event"], "superseded")
        if not self.core.settings["source_enabled"].get(event["source_id"], False):
            return "unmatched"
        if not self.core.settings["sound_notifications"]:
            return "disabled"
        if (event["state"] not in ("done", "needs_input")
                or event["source_id"] in ("manual", "demo")
                or self.db.execute("SELECT 1 FROM self_tests WHERE event=?", (event["event_id"],)).fetchone()):
            return "not_requested"
        remaining = min(event["expires"] - self.core.clock(), self.MAX_DELAY_SECONDS)
        if remaining <= 0:
            return "expired"
        payload = {"event_id": event["event_id"], "source_id": event["source_id"],
                   "task_id": event["task_id"], "seq": event["seq"],
                   "category": self.core.settings["sound_category"]}
        self.db.execute("INSERT INTO sound_outbox VALUES (?,?,?,NULL)",
                        (event["event_id"], json.dumps(payload), self.core.wall() + remaining))
        self.db.commit()
        return "pending"

    def _remove(self, event_id, status):
        self.core.router.record(event_id, self.name, status)
        self.db.execute("DELETE FROM sound_outbox WHERE event=?", (event_id,))
        self.db.commit()

    def settings_changed(self):
        preference = (self.core.settings["sound_notifications"], self.core.settings["sound_category"])
        if preference != self.preference:
            self.generation = str(uuid.uuid4())
            self.preference = preference
        for source, enabled in self.core.settings["source_enabled"].items():
            if enabled != self.source_preferences.get(source):
                self.source_generations[source] = str(uuid.uuid4())
        self.source_preferences = dict(self.core.settings["source_enabled"])
        for row in self.db.execute("""SELECT s.event, e.source FROM sound_outbox s
                LEFT JOIN events e ON e.id=s.event""").fetchall():
            if (not self.core.settings["sound_notifications"]
                    or not self.core.settings["source_enabled"].get(row["source"], False)):
                self._remove(row["event"], "disabled")

    def expire(self):
        for row in self.db.execute("SELECT event, claim FROM sound_outbox WHERE expires<=?", (self.core.wall(),)).fetchall():
            self._remove(row["event"], "unconfirmed" if row["claim"] else "expired")

    def claim(self):
        self.settings_changed()
        self.expire()
        result = []
        for row in self.db.execute("SELECT * FROM sound_outbox WHERE claim IS NULL ORDER BY rowid LIMIT 1").fetchall():
            token = str(uuid.uuid4())
            self.db.execute("UPDATE sound_outbox SET claim=? WHERE event=?", (token, row["event"]))
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
            raise EventError("音效领取验证格式错误")
        self.settings_changed()
        self.expire()
        row = self.db.execute("""SELECT s.claim, e.source, e.seq, t.seq AS latest
            FROM sound_outbox s JOIN events e ON e.id=s.event
            JOIN tasks t ON t.source=e.source AND t.task=e.task WHERE s.event=?""",
            (body["event_id"],)).fetchone()
        return {"valid": bool(row and row["claim"] == body["claim"]
                              and body["generation"] == self.generation + self.source_generations.get(row["source"], "")
                              and row["seq"] == row["latest"])}

    def acknowledge(self, body):
        if (not isinstance(body, dict) or set(body) != {"event_id", "claim", "status"}
                or any(not isinstance(value, str) for value in body.values())
                or body["status"] not in ("submitted", "unavailable", "failed")):
            raise EventError("音效回执格式错误")
        self.expire()
        row = self.db.execute("SELECT claim FROM sound_outbox WHERE event=?", (body["event_id"],)).fetchone()
        if not row or not row["claim"] or row["claim"] != body["claim"]:
            raise EventError("音效回执已失效", 409)
        self._remove(body["event_id"], body["status"])
        return {"status": body["status"]}
