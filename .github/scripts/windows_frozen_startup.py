"""Launch the frozen Windows GUI once, using only a disposable local profile."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "validation/windows-frozen-startup"
sys.path.insert(0, str(ROOT))


def verify_port_available(port=8765):
    # Windows may time out rather than refuse a connection to an unopened port.
    # Bind locally instead: no request is sent to an existing port owner.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
    except OSError as error:
        raise RuntimeError(f"Port {port} is unavailable before this isolated test") from error


def child_windows(pid):
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    windows = []

    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not user.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(user.GetWindowTextLengthW(hwnd) + 1)
        user.GetWindowTextW(hwnd, title, len(title))
        class_name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, class_name, len(class_name))
        rect = wintypes.RECT()
        if user.GetWindowRect(hwnd, ctypes.byref(rect)):
            windows.append({"hwnd": int(hwnd), "title": title.value, "class": class_name.value,
                            "width": rect.right - rect.left, "height": rect.bottom - rect.top})
        return True

    if not user.EnumWindows(callback_type(visit), 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return windows


def screenshot(hwnd, filename):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.processEvents()
    screen = app.primaryScreen()
    if screen is None:
        return False
    image = screen.grabWindow(hwnd)
    return not image.isNull() and image.save(str(filename), "PNG")


def sanitized(text, profile, credentials):
    text = text.replace(str(profile), "[temporary-profile]")
    values = [credentials.get("dashboard"), *credentials.get("tokens", {}).values()]
    for value in values:
        if isinstance(value, str) and value:
            text = text.replace(value, "[redacted]")
    return re.sub(r"(?im)^.*(?:authorization|set-cookie|bearer\s|aiflame=).*$", "[redacted auth line]", text)


def main():
    if sys.platform != "win32":
        raise RuntimeError("This check requires a real Windows runner")
    from aiflame.core.management_auth import SignedOpener
    OUTPUT.mkdir(parents=True, exist_ok=True)
    receipt = json.loads((ROOT / "dist/SeeEnd-build.json").read_text(encoding="utf-8"))
    executable = ROOT / "dist/SeeEnd/SeeEnd.exe"
    report = {"passed": False, "version": receipt["app_version"], "launches": 0,
              "scope": "Frozen executable, disposable profile, real Windows Qt window; automated CI only"}
    process, credentials = None, {}
    with tempfile.TemporaryDirectory(prefix="seeend-frozen-gui-", ignore_cleanup_errors=True) as temporary, tempfile.TemporaryFile() as bootstrap:
        profile = Path(temporary) / "profile"
        profile.mkdir()
        try:
            verify_port_available()
            environment = {**os.environ, "AIFLAME_DATA_DIR": str(profile), "QT_QPA_PLATFORM": "windows"}
            process = subprocess.Popen([str(executable)], env=environment, stdout=bootstrap,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            report.update(launches=1, pid=process.pid)

            def private_token():
                nonlocal credentials
                credentials = json.loads((profile / "credentials.json").read_text(encoding="utf-8"))
                return credentials["dashboard"]

            client = SignedOpener("http://127.0.0.1:8765", private_token)
            deadline, api_ready, window = time.monotonic() + 60, False, None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Frozen GUI exited during startup with code " + str(process.returncode))
                api_ready = False
                try:
                    with client.open("http://127.0.0.1:8765/api/session", timeout=1) as response:
                        metadata = json.load(response)
                    if metadata.get("app_name") != "SeeEnd" or metadata.get("app_version") != receipt["app_version"]:
                        raise RuntimeError("Frozen GUI reported an unexpected application identity")
                    api_ready = True
                except (OSError, ValueError, KeyError):
                    pass
                windows = child_windows(process.pid)
                report["visible_child_windows"] = windows
                window = next((item for item in windows if item["title"] == "SeeEnd"
                               and item["class"].startswith("Qt")
                               and item["width"] >= 700 and item["height"] >= 500), None)
                if api_ready and window:
                    break
                time.sleep(.25)
            if not api_ready or window is None:
                raise RuntimeError("Frozen GUI did not expose both authenticated API and a visible native SeeEnd window")
            report.update(api_ready=True, visible_native_window=window, passed=True)
            try:
                report["screenshot_saved"] = screenshot(window["hwnd"], OUTPUT / "frozen-window.png")
            except Exception as error:
                report["screenshot_saved"] = False
                report["screenshot_error"] = type(error).__name__
        except Exception as error:
            report["error"] = sanitized(str(error), profile, credentials)
            if process and process.poll() is None:
                try:
                    report["visible_child_windows"] = child_windows(process.pid)
                    if report["visible_child_windows"]:
                        screenshot(report["visible_child_windows"][0]["hwnd"], OUTPUT / "failure-window.png")
                except Exception:
                    pass
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            report["child_terminated"] = process is None or process.poll() is not None
            log = profile / "desktop.log"
            if log.exists():
                text = log.read_bytes()[:256 * 1024].decode("utf-8", errors="replace")
                (OUTPUT / "desktop.log").write_text(sanitized(text, profile, credentials), encoding="utf-8")
            bootstrap.seek(0)
            text = bootstrap.read(256 * 1024).decode("utf-8", errors="replace")
            (OUTPUT / "bootstrap.log").write_text(sanitized(text, profile, credentials), encoding="utf-8")
    report["temporary_profile_removed"] = not profile.exists()
    (OUTPUT / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    return 0 if report["passed"] and report["child_terminated"] and report["temporary_profile_removed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
