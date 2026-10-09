"""Run the same helpers from a checkout or a frozen application on every OS."""

import os
import subprocess
import sys
from pathlib import Path
from aiflame.core.paths import PROJECT


def command(mode, *args):
    if mode == "--esptool":
        if getattr(sys, "frozen", False):
            root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
            tool = root / "third_party/esptool" / ("esptool.exe" if os.name == "nt" else "esptool")
            return [str(tool)] + list(args)
        return [sys.executable, "-m", "esptool"] + list(args)
    executable = sys.executable
    if getattr(sys, "frozen", False) and os.name == "nt":
        executable = str(Path(executable).with_name("SeeEnd Helper.exe"))
    if getattr(sys, "frozen", False):
        prefix = [executable]
    elif (PROJECT / "app.py").is_file():
        prefix = [sys.executable, str(PROJECT / "app.py")]
    else:
        prefix = [sys.executable, "-m", "aiflame"]
    return prefix + [mode] + list(args)


def run(mode, *args, timeout=60, input=None, binary=False):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    return subprocess.run(
        command(mode, *args),
        input=input,
        capture_output=True,
        text=not binary,
        timeout=timeout,
        **options
    )
