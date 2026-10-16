"""Resolve Codex's existing display name to one local terminal on user request.

Only names, directories and process identities are read. Conversation bodies,
terminal contents and shared-daemon environment identities are never consulted.
"""

from contextlib import closing
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid

from aiflame.core.events import EventError
from aiflame.core.task_origins import absolute_context_path, validate_context, validate_origin
from aiflame.integrations.cli_tools.origin import processes

FS, RS = chr(31), chr(30)
INCOMPLETE = "终端信息读取不完整，无法确认唯一位置。"
MISSING = "未找到对应终端，请确认原会话仍在运行。"
APP_NAMES = {"Terminal": "Terminal", "iTerm2": "iTerm2", "iTerm": "iTerm2"}

TERMINAL_METADATA = '''set outputText to "OK" & (character id 30)
set fs to character id 31
set rs to character id 30
if application id "com.apple.Terminal" is running then
    tell application id "com.apple.Terminal"
        repeat with w in windows
            try
                set tabList to tabs of w
            on error errorText number errorNumber
                if errorNumber is -1728 then
                    set tabList to {}
                else
                    return "UNREADABLE"
                end if
            end try
            set tabNumber to 0
            repeat with t in tabList
                set tabNumber to tabNumber + 1
                try
                    set outputText to outputText & (tty of t) & fs & (custom title of t) & fs & (id of w as text) & fs & (tabNumber as text) & rs
                on error
                    return "UNREADABLE"
                end try
            end repeat
        end repeat
    end tell
end if
return outputText'''

ITERM_METADATA = '''set outputText to "OK" & (character id 30)
set fs to character id 31
set rs to character id 30
if application id "com.googlecode.iterm2" is running then
    tell application id "com.googlecode.iterm2"
        repeat with w in windows
            set tabNumber to 0
            repeat with t in tabs of w
                set tabNumber to tabNumber + 1
                set paneNumber to 0
                repeat with s in sessions of t
                    set paneNumber to paneNumber + 1
                    try
                        set rawTitle to variable s named "terminalWindowName"
                        if rawTitle is missing value then set rawTitle to ""
                        set outputText to outputText & (tty of s) & fs & rawTitle & fs & (id of w as text) & fs & (tabNumber as text) & fs & (paneNumber as text) & fs & (unique ID of s) & rs
                    on error
                        return "UNREADABLE"
                    end try
                end repeat
            end repeat
        end repeat
    end tell
end if
return outputText'''


@dataclass(frozen=True)
class ResolvedTerminal:
    destination: dict
    title: str
    window_id: str


def _directory(value):
    if (not isinstance(value, str)
            or not absolute_context_path(value)
            or any(ord(c) < 32 for c in value)):
        raise EventError("会话目录信息不可用，暂时无法定位。", 409)
    return os.path.realpath(value)


def _session(context, home=None):
    home = Path(home) if home is not None else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    databases = [(int(m[1]), p) for p in home.glob("state_*.sqlite")
                 if (m := re.fullmatch(r"state_(\d+)\.sqlite", p.name))]
    if not databases:
        raise EventError("无法读取 Codex 会话信息，暂时无法定位。", 409)
    path = max(databases, key=lambda item: item[0])[1]
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5)) as db:
            row = db.execute("SELECT name,cwd FROM threads WHERE id=?", (context["session_id"],)).fetchone()
            if row is None:
                raise EventError("未找到此 Codex 会话，暂时无法定位。", 409)
            name, cwd = row
            if not isinstance(name, str) or not name.strip():
                raise EventError("会话尚未生成名称，暂时无法定位。", 409)
            if len(name) > 512 or any(ord(c) < 32 or ord(c) == 127 for c in name):
                raise EventError("会话名称信息不可用，暂时无法定位。", 409)
            directory = _directory(cwd)
            if directory != _directory(context["cwd"]):
                raise EventError("会话目录已变化，请重新定位当前任务。", 409)
            # The stored session supplies a name, not a live-process binding.
            # Historical names never participate in current terminal uniqueness.
            return name, directory
    except (OSError, sqlite3.Error, UnicodeError) as error:
        raise EventError("无法读取 Codex 会话信息，暂时无法定位。", 409) from error


def _project(directory):
    current = Path(directory)
    for folder in (current, *current.parents):
        if (folder / ".git").exists():
            return folder.name
    return current.name


def _parse_tabs(raw, app):
    if len(raw) > 131072:
        raise EventError(INCOMPLETE, 409)
    records = raw.rstrip("\r\n").split(RS)
    if records[0] != "OK":
        raise EventError(INCOMPLETE, 409)
    result = []
    for record in records[1:]:
        if not record:
            continue
        fields = record.split(FS)
        if (len(fields) != (4 if app == "Terminal" else 6)
                or not re.fullmatch(r"/dev/ttys\d{1,5}", fields[0])
                or not fields[2].isdigit() or not fields[3].isdigit()
                or len(fields[1]) > 2048 or any(ord(c) < 32 for c in fields[1])):
            raise EventError(INCOMPLETE, 409)
        item = {"app": app, "tty": fields[0], "title": fields[1], "window_id": fields[2]}
        if app == "iTerm2":
            try:
                if not fields[4].isdigit():
                    raise ValueError()
                uuid.UUID(fields[5])
            except ValueError as error:
                raise EventError(INCOMPLETE, 409) from error
            item["session_guid"] = fields[5]
        result.append(item)
        if len(result) > 1024:
            raise EventError(INCOMPLETE, 409)
    return result


def terminal_tabs(table):
    apps = {APP_NAMES[Path(item["command"]).name] for item in table.values()
            if Path(item["command"]).name in APP_NAMES}
    result = []
    for app in sorted(apps):
        script = TERMINAL_METADATA if app == "Terminal" else ITERM_METADATA
        try:
            query = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True,
                                   text=True, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise EventError("读取终端超时，请处理系统授权弹窗后重试。", 409) from error
        except (OSError, UnicodeError) as error:
            raise EventError(INCOMPLETE, 409) from error
        if query.returncode:
            if "-1743" in query.stderr:
                raise EventError("请在系统设置 → 隐私与安全性 → 自动化中，允许 SeeEnd 控制此终端，然后重试。", 409)
            raise EventError(INCOMPLETE, 409)
        result.extend(_parse_tabs(query.stdout, app))
    return result


def _owner(pid, table):
    seen = set()
    while pid in table and pid not in seen and len(seen) < 64:
        seen.add(pid)
        item = table[pid]
        command = Path(item["command"]).name
        if command in ("tmux", "screen", "ssh", "sshd"):
            return None
        if command in APP_NAMES:
            return APP_NAMES[command]
        pid = item["ppid"]
    return None


def process_cwds(pids):
    if not pids:
        return {}
    if len(pids) > 128:
        raise EventError(INCOMPLETE, 409)
    try:
        query = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(map(str, pids)),
                                "-d", "cwd", "-Fpn"], capture_output=True, text=True,
                               encoding="utf-8", timeout=5,
                               # Finder-launched apps may have no locale. In the
                               # C locale lsof escapes Chinese path bytes as \\xNN.
                               env={**os.environ, "LC_ALL": "en_US.UTF-8"})
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        raise EventError(INCOMPLETE, 409) from error
    result = {}
    pid = None
    for line in query.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n/") and pid in pids:
            result[pid] = _directory(line[1:])
    if set(result) != set(pids):
        raise EventError(INCOMPLETE, 409)
    return result


def resolve(context, *, home=None, table=None, tabs=None, cwds=None):
    context = validate_context({key: context.get(key) for key in ("session_id", "turn_id", "cwd")}, "codex")
    if sys.platform != "darwin":
        raise EventError("返回 Terminal 和 iTerm2 目前仅支持 macOS。", 409)
    name, directory = _session(context, home)
    try:
        table = processes() if table is None else table
        tabs = terminal_tabs(table) if tabs is None else tabs
    except (OSError, subprocess.SubprocessError) as error:
        raise EventError(INCOMPLETE, 409) from error
    project = _project(directory)
    titles = {name + " | " + project, name + " / " + project, name + "/" + project}
    cli = {pid: item for pid, item in table.items()
           if Path(item["command"]).name == "codex" and item["tty"].startswith("ttys")
           and _owner(pid, table) is not None}
    # An unreadable terminal with a live local CLI means the enumeration cannot
    # establish uniqueness. Non-terminal Settings windows have no such process.
    positions = {(tab["app"], tab["tty"]) for tab in tabs}
    if any((_owner(pid, table), "/dev/" + item["tty"]) not in positions for pid, item in cli.items()):
        raise EventError(INCOMPLETE, 409)
    candidates = []
    for tab in tabs:
        if tab["title"] not in titles:
            continue
        owners = [pid for pid, item in cli.items() if "/dev/" + item["tty"] == tab["tty"]
                  and _owner(pid, table) == tab["app"]]
        if len(owners) > 1:
            raise EventError(INCOMPLETE, 409)
        if owners:
            candidates.append((tab, owners[0]))
    if not candidates:
        raise EventError(MISSING, 409)
    pids = list({pid for _, pid in candidates})
    cwds = process_cwds(pids) if cwds is None else cwds
    if any(pid not in cwds for pid in pids):
        raise EventError(INCOMPLETE, 409)
    candidates = [(tab, pid) for tab, pid in candidates if _directory(cwds[pid]) == directory]
    if len(candidates) > 1:
        raise EventError(f"会话「{name}」对应 {len(candidates)} 个终端位置，无法唯一定位。", 409,
                         "ambiguous_terminal")
    if not candidates:
        raise EventError(MISSING, 409)
    candidate, pid = candidates[0]
    started = cli[pid]["started"]
    destination = {"kind": "terminal", "app": candidate["app"], "tty": candidate["tty"], "pid": pid,
                   "started": started, "instance_id": hashlib.sha256(f"{pid}:{started}".encode()).hexdigest(),
                   "session_id": context["session_id"], "project": project}
    if candidate["app"] == "iTerm2":
        destination["session_guid"] = candidate["session_guid"]
    return ResolvedTerminal(validate_origin(destination, "codex"), candidate["title"], candidate["window_id"])


def resolve_and_activate(context, is_current=None):
    resolved = resolve(context)

    def recheck():
        if is_current is not None and not is_current():
            return False
        fresh = resolve(context)
        return fresh == resolved

    from aiflame.integrations.terminal_return import activate
    return activate(resolved.destination, is_current=recheck, expected_title=resolved.title,
                    window_id=resolved.window_id)
