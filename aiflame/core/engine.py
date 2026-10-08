"""Local event lifecycle. No source content is accepted or stored."""

import datetime as dt
import json
import sqlite3
import time
import uuid
from aiflame.core.events import EventError, make_event
from aiflame.outputs.flame import FlameOutput
from aiflame.outputs.router import OutputRouter
from aiflame.outputs.desktop import DesktopOutput
from aiflame.core.verification import verification_summary

class Engine:
    def __init__(self, path, clock=time.monotonic, wall=time.time, *, sources=()):
        self.clock, self.wall = clock, wall
        self.sources = {source.id: source for source in sources}
        self.last_cleanup = None
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, source TEXT, task TEXT, seq INTEGER,
          state TEXT, reason TEXT, created REAL, status TEXT);
        CREATE TABLE IF NOT EXISTS dedup(id TEXT PRIMARY KEY, source TEXT, task TEXT, seq INTEGER,
          expires REAL, UNIQUE(source,task,seq));
        CREATE TABLE IF NOT EXISTS tasks(source TEXT, task TEXT, seq INTEGER, state TEXT,
          updated REAL, PRIMARY KEY(source,task));
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        DROP TABLE IF EXISTS codex_submissions;
        CREATE INDEX IF NOT EXISTS events_created ON events(created);
        CREATE INDEX IF NOT EXISTS tasks_updated ON tasks(updated);
        CREATE TABLE IF NOT EXISTS self_tests(event TEXT PRIMARY KEY, created REAL);
        """)
        self.settings = {
            **FlameOutput.DEFAULTS,
            "desktop_notifications": True,
            "source_enabled": {
                source.id: source.default_enabled for source in self.sources.values()
            },
        }
        saved = self.db.execute(
            "SELECT value FROM settings WHERE key='main'"
        ).fetchone()
        if saved:
            previous = json.loads(saved["value"])
            self.settings = {**self.settings, **previous}
            self.settings["source_enabled"] = {
                source.id: previous.get("source_enabled", {}).get(
                    source.id, source.default_enabled
                )
                for source in self.sources.values()
            }
            self.db.execute(
                "UPDATE settings SET value=? WHERE key='main'",
                (json.dumps(self.settings),),
            )
        self.db.execute("UPDATE tasks SET state='unknown' WHERE state IN ('working','needs_input')")
        self.db.execute(
            "UPDATE events SET status='expired' WHERE status IN ('queued','matched','received')"
        )
        self.db.commit()
        self.cleanup(force=True)
        self.active = {}
        self.router = OutputRouter(self.db, self.wall)
        self.flame = FlameOutput(self)
        self.router.register(self.flame)
        self.desktop = DesktopOutput(self)
        self.router.register(self.desktop)
        self.source_last = {}

    def cleanup(self, force=False):
        """Retain seven days of history; expire idle data even without new events."""
        now = self.wall()
        if (
            not force
            and self.last_cleanup is not None
            and now - self.last_cleanup < 3600
        ):
            return
        cutoff = now - 604800
        self.db.execute("DELETE FROM events WHERE created < ?", (cutoff,))
        self.db.execute(
            "DELETE FROM tasks WHERE updated < ? AND state NOT IN ('working','needs_input')", (cutoff,)
        )
        self.db.execute("DELETE FROM dedup WHERE expires < ?", (now,))
        self.db.execute(
            "DELETE FROM self_tests WHERE event NOT IN (SELECT id FROM events)"
        )
        self.db.commit()
        if hasattr(self, "router"):
            self.db.execute("DELETE FROM deliveries WHERE event NOT IN (SELECT id FROM events)")
            self.db.commit()
        self.last_cleanup = now

    def mark(self, event_id, status):
        self.flame.mark(event_id, status)

    def validate_settings(self, patch):
        if not isinstance(patch, dict) or set(patch) - set(self.settings):
            raise EventError("设置项不受支持")
        data = {**self.settings, **patch}
        self.flame.validate_settings(data)
        if type(data["desktop_notifications"]) is not bool:
            raise EventError("桌面通知设置格式错误")
        enabled = data["source_enabled"]
        if (
            not isinstance(enabled, dict)
            or set(enabled) != set(self.sources)
            or any(type(v) is not bool for v in enabled.values())
        ):
            raise EventError("来源设置错误")
        return data

    def update_settings(self, patch):
        data = self.validate_settings(patch)
        enabled = data["source_enabled"]
        self.settings = data
        self.db.execute(
            "INSERT OR REPLACE INTO settings VALUES (?,?)", ("main", json.dumps(data))
        )
        self.db.commit()
        for key in list(self.active):
            if not enabled.get(key[0], False):
                del self.active[key]
                self.db.execute(
                    "UPDATE tasks SET state='unknown' WHERE source=? AND task=?", key
                )
        self.flame.settings_changed()
        self.desktop.settings_changed()
        self.db.commit()
        return data

    def stop(self):
        self.flame.stop()
        self.active.clear()
        self.db.execute("UPDATE tasks SET state='cancelled' WHERE state IN ('working','needs_input')")
        self.db.commit()

    def accept(self, event, source):
        self.tick()
        required = {
            "schema_version",
            "event_id",
            "source_id",
            "task_id",
            "seq",
            "state",
            "occurred_at",
            "ttl_ms",
        }
        if (
            not isinstance(event, dict)
            or not required <= set(event)
            or set(event) - required - {"reason", "device_id"}
        ):
            raise EventError("事件字段不完整或包含不支持的字段")
        if event["source_id"] != source:
            raise EventError("凭据与来源不匹配", 403)
        if event["schema_version"] != "1.0":
            raise EventError("仅支持协议 1.0")
        try:
            if not isinstance(event["event_id"], str):
                raise ValueError()
            uuid.UUID(event["event_id"])
        except (ValueError, AttributeError):
            raise EventError("event_id 必须为 UUID")
        if (
            not isinstance(event["task_id"], str)
            or not 1 <= len(event["task_id"]) <= 120
        ):
            raise EventError("task_id 格式错误")
        if type(event["seq"]) is not int or not 0 <= event["seq"] <= 2147483647:
            raise EventError("seq 必须为递增整数")
        if event["state"] not in ("idle", "working", "needs_input", "done", "error", "cancelled", "timeout", "unknown"):
            raise EventError("任务状态不受支持")
        if type(event["ttl_ms"]) is not int or not 1000 <= event["ttl_ms"] <= 300000:
            raise EventError("ttl_ms 范围为 1000–300000")
        reason = event.get("reason")
        if reason not in (None, "completed", "failed", "cancelled", "timeout"):
            raise EventError("reason 格式错误")
        if (reason == "cancelled" and event["state"] != "idle") or (
            reason == "timeout" and event["state"] == "done"
        ):
            raise EventError("取消或超时不能映射成功")
        self.router.validate_target(event)
        try:
            occurred = dt.datetime.fromisoformat(
                event["occurred_at"].replace("Z", "+00:00")
            )
            if occurred.tzinfo is None or occurred.utcoffset() != dt.timedelta(0):
                raise ValueError()
            age = self.wall() - occurred.timestamp()
        except (ValueError, TypeError, AttributeError):
            raise EventError("occurred_at 必须是 UTC 时间")
        if age > event["ttl_ms"] / 1000 or age < -30:
            raise EventError("事件过期或设备时间偏差过大", 422)
        self.db.execute("DELETE FROM dedup WHERE expires < ?", (self.wall(),))
        self.db.commit()
        existing = self.db.execute(
            "SELECT * FROM dedup WHERE id=? OR (source=? AND task=? AND seq=?)",
            (event["event_id"], source, event["task_id"], event["seq"]),
        ).fetchone()
        if existing:
            if existing["source"] != source:
                raise EventError("事件标识冲突", 409)
            return {"event_id": existing["id"], "duplicate": True}, 200
        key = (source, event["task_id"])
        policy = self.sources.get(source)
        previous = self.db.execute(
            "SELECT * FROM tasks WHERE source=? AND task=?", key
        ).fetchone()
        submission = event["state"] == "working" and event["seq"] == 1
        if policy and policy.requires_start and not previous and not submission:
            if policy.ignore_unregistered:
                return {
                    "event_id": event["event_id"],
                    "result": "ignored_unregistered_turn",
                }, 200
            raise EventError("任务需要先登记开始事件", 409)
        cancelling = (
            policy
            and policy.allow_late_cancel
            and reason == "cancelled"
            and event["state"] == "idle"
        )
        if previous and (
            event["seq"] <= previous["seq"]
            or (previous["state"] in ("done", "error", "cancelled", "timeout") and not cancelling)
        ):
            raise EventError(
                "旧序号或已结束的任务不能覆盖现有状态，请使用新的 task_id", 409
            )
        now = self.clock()
        record_state = reason if reason in ("cancelled", "timeout") else event["state"]
        eid = event["event_id"]
        self.db.execute(
            "INSERT INTO dedup VALUES (?,?,?,?,?)",
            (eid, source, key[1], event["seq"], self.wall() + 86400),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?)",
            (source, key[1], event["seq"], record_state, self.wall()),
        )
        result = "queued"
        enabled = self.settings["source_enabled"].get(source, False)
        if not enabled:
            result = "unmatched"
        
        self.db.execute(
            "INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
            (
                eid,
                source,
                key[1],
                event["seq"],
                event["state"],
                reason,
                self.wall(),
                result,
            ),
        )
        self.db.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY created DESC LIMIT -1 OFFSET 10000)"
        )
        self.db.commit()
        self.cleanup()
        self.source_last[source] = self.wall()
        if enabled and record_state == "working":
            lease = policy.working_lease_seconds if policy else 120
            self.active[key] = {"until": now + lease if lease is not None else None, "id": eid}
        else:
            self.active.pop(key, None)
        phase = event["state"]
        if phase == "working":
            phase = "started" if not previous else "renewed" if previous["state"] == "working" else "resumed"
        elif reason in ("cancelled", "timeout"):
            phase = reason
        normalized = {**event, "state": record_state, "phase": phase,
                      "expires": now + max(0, event["ttl_ms"] / 1000 - age)}
        deliveries = self.router.dispatch(normalized)
        return {"event_id": eid, "result": deliveries.get("flame", result),
                "phase": phase, "deliveries": deliveries}, 202

    def snapshot(self):
        self.tick()
        display = self.display_status()
        verification = verification_summary(self)
        return {
            "display": display,
            "active_count": len(self.active),
            "queue_count": len(self.queue),
            "dropped": self.dropped,
            "settings": self.settings,
            "brightness_off": self.flame.brightness_off(),
            "emergency": self.emergency,
            "device": self.device,
            "last_ack": self.last_command,
            "verification": verification,
            "sources": [
                {
                    "id": source.id,
                    "name": source.name,
                    "description": source.description,
                    "enabled": self.settings["source_enabled"].get(source.id, False),
                    "last_seen": self.source_last.get(source.id),
                    "status": "available",
                }
                for source in self.sources.values()
            ],
            "deliveries": self.router.snapshot(),
            "events": [
                dict(r)
                for r in self.db.execute(
                    "SELECT events.*, (source IN ('manual','demo') OR EXISTS(SELECT 1 FROM self_tests WHERE event=events.id)) AS simulated FROM events ORDER BY created DESC, rowid DESC LIMIT 50"
                )
            ],
            "tasks": [
                dict(r)
                for r in self.db.execute(
                    "SELECT * FROM tasks ORDER BY updated DESC LIMIT 30"
                )
            ],
        }

    def tick(self):
        for key, item in list(self.active.items()):
            if item["until"] is not None and item["until"] <= self.clock():
                del self.active[key]
                self.db.execute("UPDATE tasks SET state='unknown' WHERE source=? AND task=?", key)
                task = self.db.execute("SELECT seq FROM tasks WHERE source=? AND task=?", key).fetchone()
                event = make_event(key[0], key[1], "unknown", task["seq"], "timeout")
                self.db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                                (event["event_id"], key[0], key[1], task["seq"], "unknown", "timeout", self.wall(), "received"))
                self.router.dispatch({**event, "phase": "lease_expired", "previous_event_id": item["id"], "expires": self.clock() + 60})
        self.db.commit()

    def muted(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.muted(*args, **kwargs)

    def clear_prompts(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.clear_prompts(*args, **kwargs)

    def start_preview(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.start_preview(*args, **kwargs)

    def cancel_preview(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.cancel_preview(*args, **kwargs)

    def preview_connection(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.preview_connection(*args, **kwargs)

    def preview_display(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.preview_display(*args, **kwargs)

    def desired(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.desired(*args, **kwargs)

    def human_activity(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.human_activity(*args, **kwargs)

    def applied(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.applied(*args, **kwargs)

    def session_changed(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.session_changed(*args, **kwargs)

    def display_status(self, *args, **kwargs):
        """Compatibility entry point for the existing HTTP/UI clients."""
        return self.flame.display_status(*args, **kwargs)

    @property
    def queue(self):
        return self.flame.queue

    @queue.setter
    def queue(self, value):
        self.flame.queue = value

    @property
    def current(self):
        return self.flame.current

    @current.setter
    def current(self, value):
        self.flame.current = value

    @property
    def preview(self):
        return self.flame.preview

    @preview.setter
    def preview(self, value):
        self.flame.preview = value

    @property
    def emergency(self):
        return self.flame.emergency

    @emergency.setter
    def emergency(self, value):
        self.flame.emergency = value

    @property
    def dropped(self):
        return self.flame.dropped

    @dropped.setter
    def dropped(self, value):
        self.flame.dropped = value

    @property
    def device(self):
        return self.flame.device

    @device.setter
    def device(self, value):
        self.flame.device = value

    @property
    def last_command(self):
        return self.flame.last_command

    @last_command.setter
    def last_command(self, value):
        self.flame.last_command = value

    @property
    def last_display_key(self):
        return self.flame.last_display_key

    @last_display_key.setter
    def last_display_key(self, value):
        self.flame.last_display_key = value
