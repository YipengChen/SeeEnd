"""Hub-owned polling and diagnostics; no third-party client content retained."""

import sys
import threading
import time

from aiflame.core.events import EventError
from .providers import CLIENTS
from .state import Tracker


class DesktopClients:
    def __init__(self, context, reader=None, platform=None):
        self.context = context
        self.platform = platform or sys.platform
        self.reader = reader
        self.guard = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.rows = {}
        self.permissions = {"accessibility": False, "input_monitoring": False}
        self.trackers = {
            client.id: Tracker(client.id, self.accept) for client in CLIENTS
        }
        self.installed = {}
        self.last_inventory = None

    def accept(self, event):
        with self.context.lock:
            self.context.engine.accept(event, event["source_id"])

    def _reader(self):
        with self.guard:
            if self.reader is None:
                from .macos import MacReader

                self.reader = MacReader()
            return self.reader

    def start(self):
        if self.platform != "darwin" or self.thread:
            return
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()  # Finish native reads before the event database closes.
        for tracker in self.trackers.values():
            tracker.reset(time.monotonic())
        if self.reader:
            self.reader.close()

    def request_permissions(self):
        if self.platform != "darwin":
            raise EventError("桌面客户端接入目前仅支持 macOS", 409)
        try:
            reader = self._reader()
            permissions = reader.permissions()
            if not all(permissions.values()):
                reader.request_permissions()
                permissions = reader.permissions()
        except (OSError, RuntimeError, AttributeError):
            raise EventError("无法请求权限，请在系统设置中检查 SeeEnd 的权限", 409)
        with self.guard:
            self.permissions = permissions
        return {
            "permissions": dict(permissions),
            "message": (
                "桌面客户端权限已就绪，可以发起新的普通 Chat 消息。"
                if all(permissions.values())
                else "已申请接入权限。请在系统设置中为 SeeEnd 开启「辅助功能」和「输入监控」，然后从菜单退出并重新打开 SeeEnd。"
            ),
        }

    def status(self):
        with self.guard:
            return {
                "supported": self.platform == "darwin",
                "experimental": True,
                "permissions": dict(self.permissions),
                "clients": [
                    {
                        "source_id": client.id,
                        "name": client.name,
                        **self.rows.get(
                            client.id,
                            {
                                "status": "checking"
                                if self.platform == "darwin"
                                else "unsupported"
                            },
                        ),
                    }
                    for client in CLIENTS
                ],
            }

    def poll(self, now):
        reader = self._reader()
        permissions = reader.permissions()
        if self.last_inventory is None or now - self.last_inventory >= 30:
            self.installed = {client.id: reader.installed(client) for client in CLIENTS}
            self.last_inventory = now
        running = reader.running()
        with self.context.lock:
            settings = dict(self.context.engine.settings["source_enabled"])
            emergency = self.context.engine.emergency
        any_enabled = any(settings.get(client.id, False) for client in CLIENTS)
        if all(permissions.values()) and any_enabled:
            reader.start_tap()
        else:
            reader.stop_tap()
        rows, frames = {}, {}
        for client in CLIENTS:
            tracker = self.trackers[client.id]
            installed = self.installed[client.id]
            ids = set(installed) | set(client.bundle_ids)
            pids = [pid for bundle, pid in running.items() if bundle in ids]
            row = {
                "installed": bool(installed or pids),
                "running": bool(pids),
                "versions": sorted(set(installed.values())),
                "status": "waiting",
            }
            frame = None
            if not row["installed"]:
                row["status"] = "not_installed"
            elif not settings.get(client.id, False):
                row["status"] = "disabled"
            elif emergency:
                row["status"] = "paused"
            elif not all(permissions.values()):
                row["status"] = "permission_required"
            elif not reader.tap_ready:
                row["status"] = "input_unavailable"
            elif len(pids) > 1:
                row["status"] = "ambiguous"
            elif pids:
                try:
                    frame = (
                        reader.scan(pids[0], chatgpt=True)
                        if client.id == "chatgpt-desktop"
                        else reader.scan(pids[0])
                    )
                except (OSError, RuntimeError, AttributeError):
                    frame = None
                if not reader.tap_ready:
                    frame = None
                row["status"] = (
                    "generating"
                    if frame and frame.generating
                    else "ready"
                    if frame and frame.send
                    else "controls_unavailable"
                )
            rows[client.id], frames[client.id] = row, frame
        # Sample all clients before consuming interruptions and making terminal
        # decisions. An idle client's send click starts a task; only interaction
        # with an already tracked generation interrupts it.
        interrupted = reader.take_interruptions()
        for client in CLIENTS:
            tracker = self.trackers[client.id]
            cancelled = bool(
                tracker.task
                and tracker.previous
                and tracker.previous.scope[0] in interrupted
            )
            tracker.observe(frames[client.id], now, interrupted=cancelled)
            row = rows[client.id]
            row["tracking"] = bool(tracker.task)
        with self.guard:
            self.permissions, self.rows = permissions, rows

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.poll(time.monotonic())
            except Exception:
                # Native timeouts, missing controls and permission loss cannot
                # turn a pending reply into a successful completion.
                for tracker in self.trackers.values():
                    tracker.reset(time.monotonic())
                with self.guard:
                    self.rows = {
                        client.id: {"status": "controls_unavailable"}
                        for client in CLIENTS
                    }
            self.stopping.wait(0.75)
