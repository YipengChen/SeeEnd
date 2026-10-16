"""Translate lifecycle metadata without retaining prompt or tool content."""

import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import closing

from aiflame.core.paths import ensure_runtime, runtime_dir
from aiflame.integrations.cli_tools.providers import PROVIDERS

TERMINAL_SEQ = 2147483646
START = {"UserPromptSubmit", "TurnStart", "BeforeAgent"}
ACTIVITY = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "Activity", "TurnActivity", "BeforeModel", "BeforeTool", "AfterTool"}
END = {"Stop", "TurnEnd", "Interrupt", "Error", "StopFailure", "SessionEnd", "AfterAgent", "Unknown"}
WAIT = {"NeedsInput", "Notification"}


def session_key(payload):
    session = payload.get("session_id")
    instance = payload.get("instance_id")
    return session + ":instance:" + instance if isinstance(session, str) and isinstance(instance, str) and instance else session


def from_hook(source, payload, runtime=None):
    if not isinstance(source, str) or source not in PROVIDERS or not isinstance(payload, dict):
        return None
    kind = payload.get("hook_event_name")
    session = session_key(payload)
    if not isinstance(kind, str) or kind not in START | ACTIVITY | END | WAIT or not isinstance(session, str) or not session:
        return None
    if kind == "Notification" and payload.get("notification_type") != "ToolPermission":
        return None
    if kind == "TurnEnd" and payload.get("reason") in ("interrupted", "forked"):
        return None
    turn = payload.get("turn_id", payload.get("turn"))
    if turn is not None and (not isinstance(turn, (str, int)) or isinstance(turn, bool) or not str(turn)):
        return None
    root = Path(runtime) if runtime is not None else ensure_runtime()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "cli-lifecycle.sqlite", timeout=0.5)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS sessions(source TEXT, session TEXT, turn TEXT, PRIMARY KEY(source,session))")
        db.execute("CREATE TABLE IF NOT EXISTS turns(source TEXT, session TEXT, turn TEXT, seq INTEGER, terminal INTEGER, updated REAL, PRIMARY KEY(source,session,turn))")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM turns WHERE terminal=1 AND updated < ?", (time.time() - 86400,))
        db.execute("DELETE FROM sessions WHERE NOT EXISTS (SELECT 1 FROM turns WHERE turns.source=sessions.source AND turns.session=sessions.session AND turns.turn=sessions.turn)")
        if turn is None:
            row = db.execute("SELECT turn FROM sessions WHERE source=? AND session=?", (source, session)).fetchone()
            if kind in START:
                turn = str(uuid.uuid4())
            elif row:
                turn = row[0]
            else:
                return None
        turn = str(turn)
        row = db.execute("SELECT seq,terminal FROM turns WHERE source=? AND session=? AND turn=?", (source, session, turn)).fetchone()
        if kind in START:
            seq, state, reason, suffix = 1, "working", None, "start"
            if not row:
                db.execute("INSERT OR REPLACE INTO sessions VALUES (?,?,?)", (source, session, turn))
        elif not row:
            return None
        elif kind == "SessionEnd" and row[1]:
            return None
        elif kind in WAIT:
            if row[1]:
                return None
            seq, state, reason = row[0] + 1, "needs_input", None
            suffix = "input:" + str(seq)
        elif kind in ACTIVITY:
            if row[1]:
                return None
            seq, state, reason = row[0] + 1, "working", None
            suffix = "activity:" + str(seq)
        else:
            reason = payload.get("reason")
            if kind == "Unknown":
                seq, state, reason, suffix = TERMINAL_SEQ, "unknown", None, "unknown"
            elif kind in ("Interrupt", "SessionEnd") or reason in ("cancelled", "canceled", "interrupt", "interrupted", "abort", "aborted"):
                seq, state, reason, suffix = TERMINAL_SEQ + 1, "idle", "cancelled", "cancel"
            elif kind in ("Error", "StopFailure") or reason in ("failed", "error", "timeout", "blocked", "max-tokens") or (kind == "TurnEnd" and reason != "completed"):
                seq, state, reason, suffix = TERMINAL_SEQ, "unknown", "timeout" if reason == "timeout" else "failed", "ended"
            else:
                seq, state, reason, suffix = TERMINAL_SEQ, "done", "completed", "complete"
        if not row or seq > row[0]:
            db.execute("INSERT OR REPLACE INTO turns VALUES (?,?,?,?,?,?)", (source, session, turn, seq, state not in ("working", "needs_input"), time.time()))
        db.commit()
    finally:
        db.close()
        if os.name != "nt":
            (root / "cli-lifecycle.sqlite").chmod(0o600)
    task = str(uuid.uuid5(uuid.NAMESPACE_URL, f"aiflame:{source}:{session}:{turn}"))
    event = {"schema_version": "1.0", "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"aiflame:event:{task}:{suffix}")), "source_id": source, "task_id": task, "seq": seq, "state": state, "occurred_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"), "ttl_ms": 60000}
    if reason:
        event["reason"] = reason
    return event


def from_hooks(source, payload, runtime=None):
    """A new native prompt cancels its previous unfinished session turn first."""
    events = []
    if isinstance(source, str) and source in PROVIDERS and isinstance(payload, dict) and isinstance(payload.get("hook_event_name"), str) and payload.get("hook_event_name") in START and payload.get("turn_id", payload.get("turn")) is None:
        root = Path(runtime) if runtime is not None else runtime_dir()
        path = root / "cli-lifecycle.sqlite"
        if path.exists() and isinstance(payload.get("session_id"), str):
            with closing(sqlite3.connect(path, timeout=0.5)) as db:
                row = db.execute("SELECT sessions.turn FROM sessions JOIN turns USING(source,session,turn) WHERE source=? AND session=? AND terminal=0", (source, session_key(payload))).fetchone()
            if row:
                cancel = from_hook(source, {"hook_event_name": "Interrupt", "session_id": payload["session_id"], "turn_id": row[0], "instance_id": payload.get("instance_id")}, runtime)
                if cancel:
                    events.append(cancel)
    event = from_hook(source, payload, runtime)
    if event:
        events.append(event)
    return events


def deliver(event, port=8765):
    if event is None:
        return
    from aiflame.integrations.local_api.client import send
    config = json.loads((ensure_runtime() / "credentials.json").read_text())
    return send(event, config["tokens"][event["source_id"]], port=port, timeout=0.6)
