"""Capture the host CLI, not the short-lived hook or its redirected stdin."""

import hashlib
import os
import json
from pathlib import Path
import subprocess
import sqlite3
import sys
import uuid
from contextlib import closing
from aiflame.core.events import EventError


def processes():
    result = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,tty=,lstart=,comm="],
                            capture_output=True, text=True, timeout=0.35, check=True,
                            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
    records = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 8)
        if len(fields) == 9:
            try:
                records[int(fields[0])] = {"ppid": int(fields[1]), "tty": fields[2],
                    "started": " ".join(fields[3:8]), "command": fields[8]}
            except ValueError:
                continue
    return records


def capture(payload, *, environ=None, records=None, parent_pid=None):
    env = os.environ if environ is None else environ
    if sys.platform != "darwin" or not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
        return None
    if env.get("TMUX") or env.get("SSH_CONNECTION") or env.get("SSH_TTY"):
        return None  # These need their own end-to-end resolver.
    app = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm2"}.get(env.get("TERM_PROGRAM"))
    if not app:
        return None
    try:
        table = processes() if records is None else records
        pid = os.getppid() if parent_pid is None else parent_pid
        visited = set()
        while pid in table and pid not in visited:
            visited.add(pid)
            item = table[pid]
            command = Path(item["command"]).name
            if command not in {"sh", "bash", "zsh", "fish", "dash", "env"}:
                break
            pid = item["ppid"]
        else:
            return None
        if command in {"Terminal", "iTerm2", "iTerm", "sshd", "tmux", "launchd"} or not item["tty"].startswith("ttys"):
            return None
        identity = hashlib.sha256(f"{pid}:{item['started']}".encode()).hexdigest()
        value = {"kind": "terminal", "app": app, "tty": "/dev/" + item["tty"],
                 "pid": pid, "started": item["started"], "instance_id": identity,
                 "session_id": payload["session_id"]}
        if app == "iTerm2":
            guid = env.get("ITERM_SESSION_ID", "").rsplit(":", 1)[-1]
            value["session_guid"] = str(uuid.UUID(guid))
        cwd = payload.get("cwd")
        if isinstance(cwd, str) and cwd:
            project = Path(cwd).name[:120]
            if project and all(ord(c) >= 32 for c in project):
                value["project"] = project
        from aiflame.core.task_origins import validate_origin
        # Source validation happens at the receiving Hub; validate bounded fields here.
        return validate_origin(value, "codex")
    except (OSError, ValueError, EventError, subprocess.SubprocessError):
        return None


def prepare(payload, source="codex"):
    # Keep a hook's task namespace stable when process inspection temporarily
    # fails. Terminal-provided identities survive hooks and distinguish panes.
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        return payload, None
    if payload["session_id"].startswith("aiflame-self-test"):
        return payload, None
    origin = capture(payload)
    native = os.environ.get("ITERM_SESSION_ID") or os.environ.get("TERM_SESSION_ID")
    if native and sys.platform == "darwin" and os.environ.get("TERM_PROGRAM") in ("Apple_Terminal", "iTerm.app") and not any(os.environ.get(k) for k in ("TMUX", "SSH_CONNECTION", "SSH_TTY")):
        try:
            from aiflame.core.paths import ensure_runtime
            path = ensure_runtime() / "cli-origin-cache.sqlite"
            with closing(sqlite3.connect(path, timeout=0.3)) as db:
                db.execute("CREATE TABLE IF NOT EXISTS origins(key TEXT PRIMARY KEY, instance TEXT, destination TEXT, updated REAL)")
                key = hashlib.sha256(json.dumps([source, native, payload["session_id"]]).encode()).hexdigest()
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM origins WHERE updated < strftime('%s','now')-604800")
                row = db.execute("SELECT instance,destination FROM origins WHERE key=?", (key,)).fetchone()
                cached = json.loads(row[1]) if row and row[1] else None
                changed = origin and cached and (origin["pid"], origin["started"]) != (cached["pid"], cached["started"])
                instance = row[0] if row and not changed else (origin["instance_id"] if origin else str(uuid.uuid4()))
                if origin:
                    origin["instance_id"] = instance
                saved = origin or (cached if not changed else None)
                db.execute("INSERT OR REPLACE INTO origins VALUES (?,?,?,strftime('%s','now'))", (key, instance, json.dumps(saved) if saved else None))
                db.commit()
            path.chmod(0o600)
            return {**payload, "instance_id": instance}, origin
        except (OSError, sqlite3.Error, ValueError):
            # Do not switch namespaces to a new inspected ID on cache failure.
            return payload, None
    return payload, None
