"""Launch the browser in which the user installed the extension."""
import os
from pathlib import Path
import shutil
import subprocess
import sys


def open_in_chrome(url):
    """Request Chrome explicitly; never silently route setup to Safari/Edge."""
    if sys.platform == "darwin":
        command = ["/usr/bin/open", "-b", "com.google.Chrome", url]
    elif sys.platform == "win32":
        candidates = [Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe"
                      for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
                      if os.environ.get(key)]
        executable = next((str(path) for path in candidates if path.is_file()), None) or shutil.which("chrome")
        if not executable:
            return False
        command = [executable, url]
    else:
        executable = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
        if not executable:
            return False
        command = [executable, url]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
