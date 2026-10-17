"""User-initiated return requests and profile-scoped, authenticated browser queues."""

from collections import deque
import secrets
import threading
import time
import uuid

from aiflame.core.events import EventError


class TaskReturns:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.clients = {}
        self.requests = {}
        self.closed = False

    def register(self, client_id):
        try:
            uuid.UUID(client_id)
        except (ValueError, TypeError, AttributeError):
            raise EventError("浏览器返回连接标识错误")
        with self.lock:
            self.cleanup()
            if self.closed:
                raise EventError("应用正在退出", 409)
            if client_id not in self.clients and len(self.clients) >= 64:
                raise EventError("浏览器返回连接过多，请稍后重试。", 429)
            # Reconnecting revokes the previous connection's polling capability.
            old = self.clients.pop(client_id, None)
            if old:
                old["wake"].set()
            # Only commands not yet handed to a poll can be delivered safely.
            # A polled request may already have opened its page before reconnect.
            queue = deque(identifier for identifier in old["queue"]
                          if (item := self.requests.get(identifier))
                          and item["status"] == "pending" and item["expires"] > self.clock()) if old else deque()
            self.clients[client_id] = {"connection_id": secrets.token_urlsafe(32),
                "seen": self.clock(), "queue": queue, "wake": threading.Event()}
            if queue:
                self.clients[client_id]["wake"].set()
            return {"connection_id": self.clients[client_id]["connection_id"]}

    def connection(self, client_id, connection_id):
        client = self.clients.get(client_id)
        if not client or not isinstance(connection_id, str) or not secrets.compare_digest(client["connection_id"], connection_id):
            raise EventError("浏览器返回连接已失效，请重新连接扩展。", 403)
        client["seen"] = self.clock()
        return client

    def poll(self, client_id, connection_id, wait=4):
        with self.lock:
            client = self.connection(client_id, connection_id)
            if not client["queue"]:
                client["wake"].clear()
        client["wake"].wait(wait)
        with self.lock:
            client = self.connection(client_id, connection_id)
            commands = []
            while client["queue"]:
                identifier = client["queue"].popleft()
                request = self.requests.get(identifier)
                if request and request["status"] == "pending" and request["expires"] > self.clock():
                    commands.append({"kind": "return-task", "request_id": identifier,
                                     "locator_id": request["locator_id"], "source_id": request["source"],
                                     "timeout_ms": min(15000, int((request["expires"] - self.clock()) * 1000))})
            return {"commands": commands}

    def open(self, event_id, destination, source, is_current=None):
        with self.lock:
            self.cleanup()
            if self.closed:
                raise EventError("应用正在退出", 409)
            existing = next((identifier for identifier, item in self.requests.items()
                             if item["event_id"] == event_id and item["status"] == "pending"), None)
            if existing:
                return {"request_id": existing}
            if len(self.requests) >= 100:
                raise EventError("返回请求过于频繁，请稍后重试。", 429)
            if destination["kind"] == "web":
                client = self.clients.get(destination["client_id"])
                if not client or self.clock() - client["seen"] > 15:
                    raise EventError("原浏览器尚未连接。请打开原浏览器配置，并更新或重新连接 SeeEnd 扩展后重试。", 409)
            identifier = str(uuid.uuid4())
            self.requests[identifier] = {"event_id": event_id, "source": source, "status": "pending",
                "expires": self.clock() + (20 if destination["kind"] == "web" else
                                            180 if destination["kind"] == "codex-session" else 90), **destination}
            if destination["kind"] == "web":
                client["queue"].append(identifier)
                client["wake"].set()
            else:
                threading.Thread(target=self._terminal, args=(identifier, destination, is_current), daemon=True).start()
            return {"request_id": identifier}

    def _terminal(self, identifier, destination, is_current):
        def current():
            with self.lock:
                item = self.requests.get(identifier)
                valid = (not self.closed and item and item["status"] == "pending"
                         and item["expires"] > self.clock())
            return bool(valid and (is_current is None or is_current()))

        try:
            if not current():
                raise EventError("返回请求已失效，请重新定位。", 409)
            if destination["kind"] == "codex-session":
                from aiflame.integrations.codex.return_location import resolve_and_activate
                resolve_and_activate(destination, is_current=current)
            else:
                from aiflame.integrations.terminal_return import activate
                activate(destination, is_current=current)
            result = {"status": "opened"}
        except EventError as error:
            result = {"status": "failed", "message": str(error)}
        except Exception:
            result = {"status": "failed", "message": "无法返回任务，请稍后重试。"}
        with self.lock:
            item = self.requests.get(identifier)
            if item and item["status"] == "pending":
                item.update(result)

    def result(self, client_id, connection_id, request_id, status):
        messages = {"missing": "此网页任务的返回位置已失效，请打开原会话。",
                    "failed": "无法返回网页任务，请检查原浏览器后重试。"}
        if status not in ("opened", "missing", "failed"):
            raise EventError("浏览器返回回执错误")
        with self.lock:
            self.connection(client_id, connection_id)
            item = self.requests.get(request_id)
            if not item or item.get("client_id") != client_id or item["status"] != "pending" or item["expires"] <= self.clock():
                raise EventError("返回请求已失效", 409)
            item.update(status="opened" if status == "opened" else "failed", message=messages.get(status, ""))
        return {"ok": True}

    def status(self, identifier):
        with self.lock:
            self.cleanup()
            item = self.requests.get(identifier)
            if not item:
                raise EventError("返回请求已失效，请重试。", 404)
            return {"status": item["status"], "message": item.get("message", "")}

    def cleanup(self):
        now = self.clock()
        for identifier, item in list(self.requests.items()):
            if item["expires"] < now - 120:
                del self.requests[identifier]
            elif item["expires"] <= now and item["status"] == "pending":
                item.update(status="failed", message="返回任务超时，请检查原应用后重试。")
        for identifier, client in list(self.clients.items()):
            if now - client["seen"] > 60:
                client["wake"].set()
                del self.clients[identifier]

    def close(self):
        with self.lock:
            self.closed = True
            for client in self.clients.values():
                client["wake"].set()
            self.clients.clear()
            self.requests.clear()
