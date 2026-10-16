"""Bounded, content-free task destinations. Destinations are data, never commands."""

import json
from pathlib import Path
import re
import uuid

from aiflame.core.events import EventError

TERMINAL_SOURCES = {"codex", "claude-code", "qoder", "deepseek-harness", "gemini-cli", "opencode"}


def validate_origin(value, source):
    def invalid():
        raise EventError("任务返回位置格式错误")

    if not isinstance(value, dict):
        invalid()
    kind = value.get("kind")
    if kind == "web":
        if not source.endswith("-web") or set(value) != {"kind", "client_id", "locator_id"}:
            invalid()
        for key in ("client_id", "locator_id"):
            try:
                if not isinstance(value[key], str):
                    invalid()
                uuid.UUID(value[key])
            except (ValueError, AttributeError):
                invalid()
    elif kind == "terminal":
        required = {"kind", "app", "tty", "pid", "started", "instance_id", "session_id"}
        if source not in TERMINAL_SOURCES or not required <= set(value) or set(value) - required - {"session_guid", "project"}:
            invalid()
        if value["app"] not in ("Terminal", "iTerm2") or not isinstance(value["tty"], str) or not re.fullmatch(r"/dev/ttys\d{1,5}", value["tty"]):
            invalid()
        if type(value["pid"]) is not int or not 1 < value["pid"] < 2**31:
            invalid()
        for key, limit in (("started", 80), ("instance_id", 128), ("session_id", 256), ("project", 120)):
            text = value.get(key)
            if key == "project" and text is None:
                continue
            if not isinstance(text, str) or not 1 <= len(text) <= limit or any(ord(c) < 32 for c in text):
                invalid()
        if value["app"] == "iTerm2":
            try:
                uuid.UUID(value.get("session_guid", ""))
            except (ValueError, AttributeError, TypeError):
                invalid()
        elif "session_guid" in value:
            invalid()
    else:
        invalid()
    return dict(value)


def validate_context(value, source):
    """A Codex identity to resolve on demand, never an asserted terminal location."""
    if (source != "codex" or not isinstance(value, dict)
            or set(value) != {"session_id", "turn_id", "cwd"}):
        raise EventError("任务定位信息格式错误")
    for key, limit in (("session_id", 36), ("turn_id", 256), ("cwd", 512)):
        text = value[key]
        if (not isinstance(text, str) or not 1 <= len(text) <= limit
                or any(ord(c) < 32 or ord(c) == 127 for c in text)):
            raise EventError("任务定位信息格式错误")
    try:
        if str(uuid.UUID(value["session_id"])) != value["session_id"]:
            raise ValueError()
    except ValueError as error:
        raise EventError("任务定位信息格式错误") from error
    if not value["cwd"].startswith("/") or len(json.dumps(value).encode()) > 3000:
        raise EventError("任务定位信息格式错误")
    return dict(value)


class TaskOrigins:
    def __init__(self, db):
        self.db = db
        db.execute("""CREATE TABLE IF NOT EXISTS task_origins(
            source TEXT, task TEXT, destination TEXT, PRIMARY KEY(source,task))""")
        db.execute("""CREATE TABLE IF NOT EXISTS terminal_instances(
            instance TEXT PRIMARY KEY, session TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS task_return_contexts(
            source TEXT, task TEXT, context TEXT, PRIMARY KEY(source,task))""")

    def remember_context(self, source, task, context):
        self.db.execute("INSERT OR IGNORE INTO task_return_contexts VALUES (?,?,?)",
                        (source, task, json.dumps(context, ensure_ascii=False)))

    def context(self, source, task):
        row = self.db.execute("SELECT context FROM task_return_contexts WHERE source=? AND task=?",
                              (source, task)).fetchone()
        return json.loads(row[0]) if row else None

    def remember(self, source, task, destination):
        # A later event cannot redirect an already registered task.
        self.db.execute("INSERT OR IGNORE INTO task_origins VALUES (?,?,?)",
                        (source, task, json.dumps(destination, ensure_ascii=False)))
        if destination["kind"] == "terminal":
            self.db.execute("INSERT OR REPLACE INTO terminal_instances VALUES (?,?)",
                            (destination["instance_id"], destination["session_id"]))

    def get(self, source, task):
        row = self.db.execute("SELECT destination FROM task_origins WHERE source=? AND task=?", (source, task)).fetchone()
        return json.loads(row[0]) if row else None

    def cleanup(self):
        self.db.execute("""DELETE FROM task_return_contexts WHERE NOT EXISTS (
            SELECT 1 FROM events WHERE events.source=task_return_contexts.source
            AND events.task=task_return_contexts.task)""")
        self.db.execute("""DELETE FROM task_origins WHERE NOT EXISTS (
            SELECT 1 FROM events WHERE events.source=task_origins.source AND events.task=task_origins.task)""")
        self.db.execute("""DELETE FROM terminal_instances WHERE instance NOT IN (
            SELECT json_extract(destination,'$.instance_id') FROM task_origins
            WHERE json_extract(destination,'$.kind')='terminal')""")

    def current(self, value):
        if value["kind"] != "terminal":
            return True
        row = self.db.execute("SELECT session FROM terminal_instances WHERE instance=?", (value["instance_id"],)).fetchone()
        return row is not None and row[0] == value["session_id"]

    def summary(self, source, task):
        value = self.get(source, task)
        if not value:
            context = self.context(source, task)
            if context:
                return {"kind": "codex-session", "app": "", "project": Path(context["cwd"]).name,
                        "available": True, "requires_resolution": True}
            return None
        return {"kind": value["kind"], "app": value.get("app", ""), "project": value.get("project", ""),
                "available": self.current(value)}
