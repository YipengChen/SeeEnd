"""Bounded loopback requests on one reusable worker, with Qt UI delivery only."""

import http.cookiejar
import json
import queue
import threading
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Qt, Signal, Slot


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
        self, path, callback=None, body=None, raw=False, content_type=None, failed=None
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
        self.pending[identifier] = (path, callback, failed)
        try:
            self.jobs.put_nowait((identifier, path, body, raw, content_type))
        except queue.Full:
            self.pending.pop(identifier)
            self.error.emit("请求过于频繁，请稍后重试")
            if failed:
                failed()

    def _run(self):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
        )
        while True:
            job = self.jobs.get()
            try:
                if job is None:
                    return
                try:
                    result = self._perform(opener, job)
                except Exception:
                    result = job[0], None, "Hub 未连接，请检查 SeeEnd 服务"
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
            timeout = 35 if path in ("/api/integrations/install", "/api/integrations/uninstall") else 5
            with opener.open(request, timeout=timeout) as response:
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
            return identifier, None, "Hub 未连接，请检查 SeeEnd 服务"

    @Slot(object)
    def _finished(self, result):
        identifier, payload, error = result
        record = self.pending.pop(identifier, None)
        if self.closed or record is None:
            return
        _, callback, failed = record
        if error:
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
