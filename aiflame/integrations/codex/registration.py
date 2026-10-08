"""Stable ownership for lifecycle hooks, including earlier unmarked commands."""

import os
from pathlib import Path
import shlex
import subprocess

EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Interrupt", "Stop")
STATUS_MESSAGE = "SeeEnd：同步灯光状态"


def quote_command(args):
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def owns_hook(hook, wanted):
    if hook.get("type") != "command":
        return False
    return owns_command(hook.get("command", ""), wanted, ("--codex-hook",))


def owns_command(command, wanted, suffix):
    """Recognize our executable, never ownership based on a display label alone."""
    if command == wanted:
        return True
    if not isinstance(command, str):
        return False
    try:
        # Windows list2cmdline quotes are retained by non-POSIX shlex.
        windows = "\\" in command or ".exe" in command.lower()
        args = [part.strip('"') for part in shlex.split(command, posix=not windows)]
    except ValueError:
        return False
    paths = [part.replace("\\", "/") for part in args]
    python = bool(args) and Path(paths[0]).name.lower().startswith("python")
    if suffix == ("--codex-hook",) and python and len(args) == 2:
        script = Path(paths[1])
        return (
            script.name == "codex_hook.py"
            and script.parent.name == "prototype"
            and script.parent.parent.name in {"AI Flame", "SeeEnd"}
        )
    if args[-len(suffix):] != list(suffix):
        return False
    prefix = args[:-len(suffix)]
    if len(prefix) == 1:
        return Path(paths[0]).name.lower() in {
            "seeend", "seeend.exe", "seeend helper.exe",
            "ai flame",
            "ai flame.exe",
            "ai flame helper.exe",
        }
    if python and prefix == [args[0], "-m", "aiflame"]:
        return True
    if python and len(prefix) == 2:
        script = Path(paths[1])
        return script.name == "app.py" and script.parent.name in {"AI Flame", "SeeEnd"}
    return False
