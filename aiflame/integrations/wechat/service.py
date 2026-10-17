"""Optional Weixin notification channel. All network I/O runs off the hub thread."""
from collections import deque
from datetime import datetime
import threading
import time
import uuid

from aiflame.core.events import EventError
from .api import ILinkClient, ORIGIN, WechatError, bounded_string, qr_image, trusted_origin
from .credentials import WechatCredentials

LABELS = {"done": "任务已完成", "needs_input": "任务等待你的输入"}
POLL_HEALTH_GRACE = 90
ERRORS = {
    "network": "无法连接微信，请检查网络后重试。",
    "http": "微信服务暂不可用，请稍后重试。",
    "timeout": "微信请求超时，请稍后重试。",
    "invalid_response": "微信返回了无法识别的结果，请重试。",
    "untrusted_host": "微信返回的服务地址未通过验证，请重新扫码。",
    "credentials": "无法访问微信凭据，请允许系统凭据访问或重新绑定。",
    -14: "微信登录已失效，请解除绑定后重新扫码。",
}


class WechatService:
    def __init__(self, hub, client=None, credentials=None, encoder=qr_image):
        self.hub, self.lock = hub, hub.lock
        self.client = client or ILinkClient()
        self.credentials = credentials or WechatCredentials(hub.runtime)
        self.encoder = encoder
        self.storage_lock = threading.Lock()
        self.account = {}
        self.bound = self.credentials.has_account()
        self.closed = False
        self.generation = 0
        self.run_stop = threading.Event()
        self.run_stop.set()
        self.login_stop = threading.Event()
        self.login_stop.set()
        self.wake = threading.Event()
        self.queue = deque()
        self.threads = []
        self.qr = {"state": "idle", "image": ""}
        self.login_id = None
        self.verify_code = None
        self.error = ""
        self.test_status = ""
        self.receiving = False
        self.busy = False
        self.connection_checked = False
        self._last_poll_success = None
        self.preparing = False
        self.prepare_deadline = 0

    @property
    def core(self):
        return self.hub.engine

    def enabled(self):
        return self.core.settings["wechat_notifications"]

    def _muted(self):
        return self.core.emergency

    def _spawn(self, target, *args):
        self.threads = [thread for thread in self.threads if thread.is_alive()]
        thread = threading.Thread(target=target, args=args, daemon=True, name="SeeEnd-WeChat")
        self.threads.append(thread)
        thread.start()

    def _active(self, generation, stop):
        return not self.closed and generation == self.generation and not stop.is_set()

    def _health_current(self):
        return (self.connection_checked and self._last_poll_success is not None
                and time.monotonic() - self._last_poll_success <= POLL_HEALTH_GRACE)

    def snapshot(self):
        with self.lock:
            healthy = self._health_current()
            available = bool(self.bound and self.account.get("context") and healthy
                             and not self.closed and not self.error and not self.busy and not self.preparing)
            return {"enabled": self.enabled(), "bound": self.bound,
                    "available": available,
                    "ready": bool(self.enabled() and available and not self.run_stop.is_set()),
                    "receiving": bool(self.receiving and healthy), "busy": self.busy,
                    "preparing": self.preparing,
                    "needs_message": bool(self.account and not self.account.get("context")),
                    "error": self.error, "test_status": self.test_status, "login": dict(self.qr)}

    def start(self):
        self.settings_changed()

    def _revoke(self, status="disabled"):
        self.generation += 1
        self.run_stop.set()
        self.receiving = False
        self.preparing = False
        self.prepare_deadline = 0
        while self.queue:
            self._receipt(self.queue.popleft(), status)
        self.wake.set()

    def settings_changed(self):
        with self.lock:
            if self.closed:
                return
            if not self.enabled() and not self.preparing:
                self._revoke()
                return
            # Re-check queued events against changed source/quiet preferences.
            if not self.run_stop.is_set():
                self.wake.set()
                return
            if self.busy:
                return
            if not self._health_current():
                self.connection_checked = False
                self.receiving = False
            self.run_stop = threading.Event()
            generation, stop = self.generation, self.run_stop
            if self.account:
                self._workers(generation, stop)
            elif self.bound:
                self.busy = True
                self._spawn(self._load, generation, stop)

    def _load(self, generation, stop):
        try:
            with self.storage_lock:
                saved = self.credentials.load()
            with self.lock:
                if not self._active(generation, stop):
                    self.busy = False
                    self.settings_changed()
                    return
                self.busy = False
                self.account = saved
                self.bound = bool(saved)
                self.error = "" if saved else ERRORS["credentials"]
                if saved:
                    self._workers(generation, stop)
                else:
                    self.preparing = False
                    self.run_stop.set()
        except Exception:
            with self.lock:
                if not self.closed:
                    self.busy = False
                    self.error = ERRORS["credentials"]
                    self.preparing = False
                    self.run_stop.set()

    def _workers(self, generation, stop):
        self._spawn(self._receive, generation, stop)
        if self.enabled():
            self._spawn(self._send, generation, stop)

    def action(self, body):
        action = body.get("action")
        if set(body) != ({"action", "code"} if action == "verify" else {"action"}):
            raise EventError("微信操作格式错误")
        with self.lock:
            if self.closed or self.hub.stopping.is_set():
                raise EventError("应用正在退出", 409)
            if action == "cancel":
                self.login_stop.set()
                self.login_id = None
                self.qr = {"state": "idle", "image": ""}
            elif action == "login":
                if self.bound or self.busy:
                    raise EventError("请先解除当前微信绑定", 409)
                self.login_stop.set()
                self.login_stop = threading.Event()
                self.login_id = str(uuid.uuid4())
                self.verify_code = None
                self.error = ""
                self.qr = {"state": "loading", "image": ""}
                self._spawn(self._login, self.login_id, self.login_stop)
            elif action == "verify":
                code = body.get("code")
                if (not isinstance(code, str) or not code.isascii() or not code.isdigit() or not 1 <= len(code) <= 16
                        or self.qr["state"] != "need_verifycode"):
                    raise EventError("请输入微信显示的验证码")
                self.verify_code = code
                self.qr["state"] = "verifying"
            elif action == "disconnect":
                if self.busy:
                    raise EventError("请等待当前微信操作结束", 409)
                self.login_stop.set()
                self.login_id = None
                self._revoke("unconnected")
                self.account = {}
                self.connection_checked = False
                self.busy = True
                self.qr = {"state": "idle", "image": ""}
                self._spawn(self._disconnect)
            elif action == "prepare":
                if not self.bound or self.busy or self.preparing:
                    raise EventError("请先完成微信绑定或等待当前连接操作结束", 409)
                self._revoke()
                self.connection_checked = False
                self.preparing = not self.enabled()
                self.prepare_deadline = time.monotonic() + 480
                self.settings_changed()
            elif action == "test":
                if not self.snapshot()["ready"]:
                    raise EventError("请开启微信通知，并先在微信中给机器人发一条消息", 409)
                self.test_status = "pending"
                self._enqueue({"event_id": None, "state": "done", "text": "SeeEnd · 微信通知测试\n微信提醒通道已连接。",
                               "expires": self.core.clock() + 60, "source_id": None})
            else:
                raise EventError("不支持的微信操作")
            return self.snapshot()

    def _login_current(self, login_id, stop):
        return not self.closed and self.login_id == login_id and not stop.is_set()

    def _login(self, login_id, stop):
        try:
            result = self.client.new_qr()
            code, image, origin = result["qrcode"], self.encoder(result["qrcode_img_content"]), ORIGIN
            with self.lock:
                if not self._login_current(login_id, stop):
                    return
                self.qr = {"state": "wait", "image": image}
            deadline, redirects = time.monotonic() + 480, 0
            while not stop.is_set() and time.monotonic() < deadline:
                with self.lock:
                    if not self._login_current(login_id, stop):
                        return
                    verify = self.verify_code
                    waiting_code = self.qr["state"] == "need_verifycode" and not verify
                if waiting_code:
                    stop.wait(0.2)
                    continue
                try:
                    result = self.client.qr_status(origin, code, verify)
                except WechatError as error:
                    if error.code == "timeout":
                        continue
                    raise
                state = result.get("status")
                if state == "scaned_but_redirect":
                    redirects += 1
                    if redirects > 3:
                        raise WechatError("invalid_response")
                    host = result.get("redirect_host", "")
                    origin = trusted_origin(host if host.startswith("https://") else "https://" + host)
                    continue
                if state == "confirmed":
                    account = {"token": result.get("bot_token"), "bot_id": result.get("ilink_bot_id"),
                               "user_id": result.get("ilink_user_id"), "base_url": trusted_origin(result.get("baseurl", ORIGIN)),
                               "context": "", "cursor": ""}
                    if any(not bounded_string(account[key], 8192 if key == "token" else 256) for key in ("token", "bot_id", "user_id")):
                        raise WechatError("invalid_response")
                    with self.storage_lock:
                        with self.lock:
                            if not self._login_current(login_id, stop):
                                return
                        self.credentials.save(account)
                        with self.lock:
                            current = self._login_current(login_id, stop)
                            if current:
                                self.account, self.bound, self.error = account, True, ""
                                self.connection_checked = False
                                self.qr = {"state": "confirmed", "image": ""}
                                # An enabled but previously unbound channel has no workers.
                                self.run_stop.set()
                                self.preparing = not self.enabled()
                                self.prepare_deadline = time.monotonic() + 480
                                self.settings_changed()
                        if not current:
                            self.credentials.clear()
                    return
                if state not in ("wait", "scaned", "need_verifycode", "verify_code_blocked", "expired", "binded_redirect"):
                    raise WechatError("invalid_response")
                with self.lock:
                    if not self._login_current(login_id, stop):
                        return
                    self.qr["state"] = state
                    if state == "scaned":
                        self.verify_code = None
                    if state in ("expired", "verify_code_blocked", "binded_redirect"):
                        self.qr["image"] = ""
                        return
                stop.wait(0.5)
            with self.lock:
                if self._login_current(login_id, stop):
                    self.qr = {"state": "expired", "image": ""}
        except Exception as error:
            with self.lock:
                if self._login_current(login_id, stop):
                    self.error = ERRORS.get(error.code, ERRORS["http"]) if isinstance(error, WechatError) else ERRORS["credentials"]
                    self.qr = {"state": "failed", "image": ""}

    def _disconnect(self):
        try:
            with self.storage_lock:
                self.credentials.clear()
            error = ""
        except Exception:
            error = "绑定已解除，但系统凭据清理失败。请允许凭据访问后再试。"
        with self.lock:
            if not self.closed:
                self.bound, self.busy, self.error, self.test_status = False, False, error, ""

    def _persist(self, generation, stop):
        with self.storage_lock:
            with self.lock:
                if not self._active(generation, stop):
                    return
                saved = dict(self.account)
            self.credentials.save(saved)

    def _receive(self, generation, stop):
        failures = 0
        while not stop.is_set():
            with self.lock:
                if not self._active(generation, stop):
                    return
                if self.preparing and time.monotonic() >= self.prepare_deadline:
                    self._revoke()
                    return
                account = dict(self.account)
            try:
                result = self.client.updates(account, account.get("cursor", ""))
                if result is None:
                    with self.lock:
                        if not self._active(generation, stop):
                            return
                        # Allow ordinary long-poll timeouts briefly, but never
                        # keep stale health indefinitely or establish it here.
                        if not self._health_current():
                            self.connection_checked = False
                            self.receiving = False
                            self.error = ERRORS["timeout"]
                    stop.wait(0.2)
                    continue
                with self.lock:
                    if not self._active(generation, stop):
                        return
                    changed = False
                    cursor = result.get("get_updates_buf")
                    if bounded_string(cursor, 65536) and cursor != self.account.get("cursor"):
                        self.account["cursor"], changed = cursor, True
                    for message in result.get("msgs", []):
                        if (isinstance(message, dict) and message.get("from_user_id") == account["user_id"]
                                and message.get("message_type") == 1 and bounded_string(message.get("context_token"), 65536)):
                            self.account["context"], changed = message["context_token"], True
                    self.receiving, self.error, self.connection_checked = True, "", True
                    self._last_poll_success = time.monotonic()
                    self.wake.set()
                if changed:
                    self._persist(generation, stop)
                with self.lock:
                    if self._active(generation, stop) and self.preparing and not self.enabled() and self.account.get("context"):
                        self._revoke()
                        return
                failures = 0
                stop.wait(0.2)  # Bound fast empty responses as well as long polling.
            except Exception as error:
                with self.lock:
                    if not self._active(generation, stop):
                        return
                    self.receiving = False
                    self.connection_checked = False
                    self.error = ERRORS.get(error.code, ERRORS["http"]) if isinstance(error, WechatError) else ERRORS["credentials"]
                    if isinstance(error, WechatError) and error.code == -14:
                        self._revoke("unconnected")
                        return
                failures += 1
                stop.wait(30 if failures >= 3 else 2)

    def accept(self, event):
        if not self.enabled():
            return "disabled"
        if event["source_id"] in ("manual", "demo") or self.core.db.execute(
                "SELECT 1 FROM self_tests WHERE event=?", (event["event_id"],)).fetchone():
            return "not_requested"
        if not self.core.settings["source_enabled"].get(event["source_id"], False):
            return "unmatched"
        # Supersede pending input notifications when the task moves on.
        for pending in list(self.queue):
            if (pending.get("source_id") == event["source_id"] and pending.get("task_id") == event["task_id"]
                    and pending["state"] == "needs_input"):
                self.queue.remove(pending)
                self._receipt(pending, "superseded")
        if event["state"] not in LABELS:
            return "not_requested"
        if self._muted():
            return "suppressed"
        if not self.account or self.run_stop.is_set():
            return "unconnected"
        if not self.account.get("context"):
            return "context_missing"
        source = self.core.sources.get(event["source_id"])
        name = source.name if source else event["source_id"]
        text = "SeeEnd · " + LABELS[event["state"]] + "\n" + name + " · " + datetime.now().strftime("%H:%M")
        self._enqueue({**event, "text": text})
        return "pending"

    def _enqueue(self, event):
        if len(self.queue) >= 100:
            self._receipt(self.queue.popleft(), "dropped")
        self.queue.append({**event, "client_id": str(uuid.uuid4())})
        self.wake.set()

    def _receipt(self, event, status):
        if event["event_id"]:
            self.core.router.record(event["event_id"], "wechat", status)
        else:
            self.test_status = status

    def _send(self, generation, stop):
        while not stop.is_set():
            self.wake.wait(0.5)
            self.wake.clear()
            with self.lock:
                if not self._active(generation, stop):
                    return
                if not self.queue:
                    continue
                event = self.queue.popleft()
                status = None
                if not self.enabled():
                    status = "disabled"
                elif event["expires"] <= self.core.clock():
                    status = "expired"
                elif event["source_id"] and not self.core.settings["source_enabled"].get(event["source_id"], False):
                    status = "unmatched"
                elif event["source_id"] and self._muted():
                    status = "suppressed"
                elif not self.account.get("context"):
                    status = "context_missing"
                if status:
                    self._receipt(event, status)
                    continue
                account = dict(self.account)
            try:
                self.client.send(account, account["context"], event["text"], event["client_id"])
                status = "submitted"
            except WechatError as error:
                # A timed-out POST may already have been accepted. Never replay it.
                status = "unconfirmed" if error.code in ("timeout", "network") else "failed"
                with self.lock:
                    if self._active(generation, stop):
                        self.error = ERRORS.get(error.code, ERRORS["http"])
                        if error.code == -14:
                            self._receipt(event, "failed")
                            self._revoke("unconnected")
                            return
            except Exception:
                status = "failed"
            with self.lock:
                if self._active(generation, stop):
                    self._receipt(event, status)
                    if status == "submitted":
                        self.error = ""
                elif not self.closed:
                    self._receipt(event, "unconfirmed")

    def stop(self):
        with self.lock:
            self._revoke("interrupted")
            self.login_stop.set()
            self.closed = True
        invalidate = getattr(self.credentials, "invalidate", None)
        if invalidate:
            invalidate()
        # In-flight HTTPS can take 35s; generation checks prevent late writes.
        deadline = time.monotonic() + 1
        for thread in list(self.threads):
            thread.join(timeout=max(0, deadline - time.monotonic()))
