"""Content-free Codex lifecycle hook mapping."""

import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from aiflame.core.paths import ensure_runtime

TERMINAL_SEQ = 2147483646


def event_for(session, turn, state, seq, suffix, reason=None):
    if (
        not isinstance(session, str)
        or not session
        or not isinstance(turn, str)
        or not turn
    ):
        return None
    task = str(uuid.uuid5(uuid.NAMESPACE_URL, "aiflame:codex:" + session + ":" + turn))
    key = "aiflame:event:" + task + ":" + suffix
    event = {
        "schema_version": "1.0",
        "event_id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
        "source_id": "codex",
        "task_id": task,
        "seq": seq,
        "state": state,
        "occurred_at": dt.datetime.now(dt.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "ttl_ms": 60000,
    }
    if reason:
        event["reason"] = reason
    return event


def from_hook(payload, runtime=None):
    kind = payload.get("hook_event_name")
    session, turn = payload.get("session_id"), payload.get("turn_id")
    if kind == "UserPromptSubmit":
        return event_for(session, turn, "working", 1, "start")
    if kind == "Interrupt":
        return event_for(
            session, turn, "idle", TERMINAL_SEQ + 1, "interrupt", "cancelled"
        )
    if kind == "Stop":
        return event_for(session, turn, "done", TERMINAL_SEQ, "complete", "completed")
    if kind not in ("PreToolUse", "PostToolUse") or not session or not turn:
        return None
    # Only a real tool event renews working. No background timer infers activity.
    event = event_for(session, turn, "working", 1, "activity")
    if not event:
        return None
    runtime = Path(runtime) if runtime else ensure_runtime()
    runtime.mkdir(exist_ok=True)
    path = runtime / "codex-sequences.sqlite"
    db = sqlite3.connect(str(path), timeout=0.3)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS counters(task TEXT PRIMARY KEY, seq INTEGER, updated REAL)"
        )
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM counters WHERE updated < ?", (time.time() - 86400,))
        row = db.execute(
            "SELECT seq FROM counters WHERE task=?", (event["task_id"],)
        ).fetchone()
        seq = row[0] + 1 if row else 2
        if seq >= TERMINAL_SEQ:
            return None
        db.execute(
            "INSERT OR REPLACE INTO counters VALUES (?,?,?)",
            (event["task_id"], seq, time.time()),
        )
        db.commit()
        os.chmod(path, 0o600)
        return event_for(session, turn, "working", seq, "activity:" + str(seq))
    finally:
        db.close()


def deliver(event):
    if event is None:
        return
    from aiflame.integrations.local_api.client import send

    config = json.loads((ensure_runtime() / "credentials.json").read_text())
    return send(event, config["tokens"]["codex"], timeout=0.6)
