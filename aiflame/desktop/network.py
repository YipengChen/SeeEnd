"""Bounded loopback requests on one reusable worker, with Qt UI delivery only."""

import json
import queue
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Qt, Signal, Slot
from aiflame.core.paths import dashboard_token
from aiflame.core.management_auth import NoRedirect, SignedOpener


class LocalAPI(QObject):
    error = Signal(str)
    completed = Signal(object)

    def __init__(self, parent=None, port=8765):
        super().__init__(parent)
        self.base = f"http://127.0.0.1:{port}"
        self.pending = {}
        self.sequence = 0
        self.closed = False
        self.jobs = queue.Queue(maxsize=32)
        self.stopping = threading.Event()
        self.completed.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def request(
        self, path, callback=None, body=None, raw=False, content_type=None, failed=None, error_callback=None
    ):
        if self.closed:
            return
        if body is None and any(record[0] == path for record in self.pending.values()):
            return
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            content_type = "application/json"
        self.sequence += 1
        identifier = self.sequence
        self.pending[identifier] = (path, callback, failed, error_callback)
        try:
            self.jobs.put_nowait((identifier, path, body, raw, content_type))
        except queue.Full:
            self.pending.pop(identifier)
            if error_callback:
                error_callback("请求过于频繁，请稍后重试")
            else:
                self.error.emit("请求过于频繁，请稍后重试")
            if failed:
                failed()

    def _run(self):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
        )
        while True:
            job = self.jobs.get()
            try:
                if job is None:
                    return
                try:
                    result = self._perform(opener, job)
                except Exception:
                    result = job[0], None, "本地服务未连接，请重新打开 SeeEnd 后重试。"
                if not self.stopping.is_set():
                    self.completed.emit(result)
                # Release bodies/results before blocking for the next job.
                del job, result
            finally:
                self.jobs.task_done()

    def _perform(self, opener, job):
        identifier, path, body, raw, content_type = job
        headers = (
            {"Content-Type": content_type or "application/octet-stream"}
            if body is not None
            else {}
        )
        request = urllib.request.Request(self.base + path, data=body, headers=headers)
        try:
            if getattr(self, "signed_opener", None) is None or self.signed_opener.opener is not opener:
                self.signed_opener = SignedOpener(self.base, dashboard_token, opener=opener)
            timeout = 35 if path in ("/api/integrations/install", "/api/integrations/uninstall", "/api/reset", "/api/device/delete") else 5
            with self.signed_opener.open(request, timeout=timeout) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError()
            return identifier, data if raw else json.loads(data), None
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read(4096)).get("error", "请求失败")
            except (ValueError, UnicodeError):
                message = "请求失败"
            finally:
                error.close()
            return identifier, None, message
        except (OSError, ValueError, UnicodeError):
            return identifier, None, "本地服务未连接，请重新打开 SeeEnd 后重试。"

    @Slot(object)
    def _finished(self, result):
        identifier, payload, error = result
        record = self.pending.pop(identifier, None)
        if self.closed or record is None:
            return
        _, callback, failed, error_callback = record
        if error:
            if error_callback:
                error_callback(error)
            else:
                self.error.emit(error)
            if failed:
                failed()
        elif callback:
            callback(payload)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stopping.set()
        self.pending.clear()
        while True:
            try:
                self.jobs.get_nowait()
                self.jobs.task_done()
            except queue.Empty:
                break
        self.jobs.put_nowait(None)
        self.worker.join(timeout=6)
