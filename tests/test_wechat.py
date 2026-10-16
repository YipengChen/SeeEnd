"""iLink protocol and optional output lifecycle, without real Weixin traffic."""
import base64
from collections import deque
from contextlib import contextmanager
import json
from email.message import Message
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, SourcePolicy, make_event
from aiflame.integrations.wechat.api import ILinkClient, NoRedirect, ORIGIN, WechatError, qr_image, trusted_origin
from aiflame.integrations.wechat.credentials import WechatCredentials
from aiflame.integrations.wechat.service import WechatService
from aiflame.outputs.wechat import WechatOutput

ACCOUNT = {"token": "private-bot-token", "user_id": "owner", "bot_id": "bot",
           "base_url": ORIGIN, "context": "private-context", "cursor": "private-cursor"}


class MemoryCredentials:
    def __init__(self, saved=None):
        self.saved = dict(saved or {})
        self.loads = 0
    def has_account(self): return bool(self.saved)
    def load(self):
        self.loads += 1
        return dict(self.saved)
    def save(self, value): self.saved = dict(value)
    def clear(self): self.saved = {}


class FakeClient:
    def __init__(self):
        self.statuses, self.messages = deque(), deque()
        self.sent, self.calls = [], []
        self.send_gate = None
        self.send_error = None
        self.poll_gate = None
    def new_qr(self):
        self.calls.append("qr")
        return {"qrcode": "private-qr-code", "qrcode_img_content": "https://weixin.qq.com/example"}
    def qr_status(self, origin, code, verify=None):
        self.calls.append((origin, code, verify))
        return self.statuses.popleft() if self.statuses else {"status": "wait"}
    def updates(self, account, cursor):
        self.calls.append("updates")
        if self.poll_gate:
            self.poll_gate.wait(3)
        time.sleep(.02)
        return {"msgs": self.messages.popleft() if self.messages else []}
    def send(self, account, context, text, client_id):
        self.sent.append((dict(account), context, text, client_id))
        if self.send_gate:
            self.send_gate.wait(3)
        if self.send_error:
            raise WechatError(self.send_error)


class ProtocolTests(unittest.TestCase):
    def test_only_trusted_https_origins_and_no_http_redirects(self):
        self.assertEqual(trusted_origin(ORIGIN + "/"), ORIGIN)
        for origin in ("http://ilinkai.weixin.qq.com", "https://weixin.qq.com.evil.test", "https://evil.test",
                       ORIGIN + "/path", ORIGIN + "?token=private", "https://u:p@ilinkai.weixin.qq.com", ORIGIN + ":444"):
            with self.subTest(origin=origin), self.assertRaises(WechatError):
                trusted_origin(origin)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil.test"))

    def test_headers_qr_content_and_outbound_payload(self):
        requests = []
        @contextmanager
        def open_response(request, timeout):
            requests.append((request, timeout))
            yield SimpleNamespace(status=200, read=lambda maximum: b'{"ret":0,"msgs":[]}')
        client = ILinkClient(SimpleNamespace(open=open_response))
        client.request(ORIGIN, "/ilink/bot/get_bot_qrcode", body={"local_token_list": []}, query={"bot_type": 3})
        request = requests[-1][0]
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(json.loads(request.data), {"local_token_list": []})
        client.send(ACCOUNT, ACCOUNT["context"], "SeeEnd test", "stable-id")
        request = requests[-1][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer " + ACCOUNT["token"])
        self.assertEqual(request.get_header("Ilink-app-clientversion"), "132106")
        self.assertTrue(base64.b64decode(request.get_header("X-wechat-uin")).isdigit())
        body = json.loads(request.data)
        self.assertEqual(body["msg"]["to_user_id"], "owner")
        self.assertEqual(body["msg"]["context_token"], ACCOUNT["context"])
        self.assertEqual(body["msg"]["client_id"], "stable-id")
        self.assertEqual(body["msg"]["message_type"], 2)
        self.assertEqual(body["base_info"]["channel_version"], "2.4.10")
        self.assertTrue(body["base_info"]["bot_agent"].startswith("SeeEnd/"))
        png = base64.b64decode(qr_image("https://weixin.qq.com/example"))
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_explicit_errors_and_invalid_codes_never_claim_success(self):
        for result in ({"ret": -14}, {"ret": True}, {"ret": 0, "errcode": -2},
                       {"ret": "0"}, {"ret": None}, {"errcode": False}):
            @contextmanager
            def response(request, timeout):
                yield SimpleNamespace(status=200, read=lambda maximum: json.dumps(result).encode())
            with self.subTest(result=result), self.assertRaises(WechatError):
                ILinkClient(SimpleNamespace(open=response)).send(ACCOUNT, "context", "text")
        @contextmanager
        def timeout(request, timeout):
            raise TimeoutError()
            yield
        self.assertEqual(ILinkClient(SimpleNamespace(open=timeout)).updates(ACCOUNT, "cursor"), {"msgs": []})

    def test_receive_owner_context_when_success_omits_ret(self):
        result = {"msgs": [{"from_user_id": "owner", "message_type": 1, "context_token": "new-context"}],
                  "get_updates_buf": "next-cursor"}
        @contextmanager
        def response(request, timeout):
            yield SimpleNamespace(status=200, read=lambda maximum: json.dumps(result).encode())
        updates = ILinkClient(SimpleNamespace(open=response)).updates(ACCOUNT, "old-cursor")
        self.assertEqual(updates["get_updates_buf"], "next-cursor")
        self.assertEqual(updates["msgs"][0]["context_token"], "new-context")

    def test_empty_optional_message_list_keeps_cursor(self):
        for result in ({}, {"get_updates_buf": "next"}, {"msgs": None, "get_updates_buf": "next"}):
            @contextmanager
            def response(request, timeout):
                yield SimpleNamespace(status=200, read=lambda maximum: json.dumps(result).encode())
            with self.subTest(result=result):
                updates = ILinkClient(SimpleNamespace(open=response)).updates(ACCOUNT, "old")
                self.assertEqual(updates["msgs"], [])
                self.assertEqual(updates.get("get_updates_buf"), result.get("get_updates_buf"))

    def test_send_success_can_omit_ret_but_transport_and_schema_errors_fail(self):
        for result in ({}, {"errmsg": ""}, {"msg_id": 18446744073709551615}):
            @contextmanager
            def response(request, timeout):
                yield SimpleNamespace(status=200, read=lambda maximum: json.dumps(result).encode())
            with self.subTest(result=result):
                ILinkClient(SimpleNamespace(open=response)).send(ACCOUNT, "context", "text")
        for status, payload in ((200, b'[]'), (200, b'null'), (200, b'not JSON'), (503, b'{}')):
            @contextmanager
            def response(request, timeout):
                yield SimpleNamespace(status=status, read=lambda maximum: payload)
            with self.subTest(status=status, payload=payload), self.assertRaises(WechatError):
                ILinkClient(SimpleNamespace(open=response)).send(ACCOUNT, "context", "text")

    def test_private_credential_reference_disconnect_cannot_recover_denied_keychain(self):
        with tempfile.TemporaryDirectory() as root, patch("aiflame.integrations.wechat.credentials.sys.platform", "darwin"):
            store = WechatCredentials(Path(root))
            values = {}
            def call(action, account, value=None):
                if action == "set": values[account] = value
                elif action == "get": return values.get(account)
                else: raise RuntimeError("denied")
            store._call = call
            store.save(ACCOUNT)
            self.assertEqual(store.reference.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(ACCOUNT["token"], store.reference.read_text())
            self.assertEqual(store.load(), ACCOUNT)
            with self.assertRaises(RuntimeError): store.clear()
            fresh = WechatCredentials(Path(root))
            fresh._call = call
            self.assertEqual(fresh.load(), {})

    def test_corrupt_reference_is_removable_and_rebindable(self):
        with tempfile.TemporaryDirectory() as root, patch("aiflame.integrations.wechat.credentials.sys.platform", "linux"):
            store = WechatCredentials(root)
            store.reference.parent.mkdir()
            for value in ("broken JSON", "[]", '{"account":null}'):
                store.reference.write_text(value)
                store.clear()
                self.assertFalse(store.has_account())
            store.save(ACCOUNT)
            self.assertEqual(store.load(), ACCOUNT)

    def test_late_keychain_save_cannot_publish_profile_after_reset(self):
        with tempfile.TemporaryDirectory() as root, patch("aiflame.integrations.wechat.credentials.sys.platform", "darwin"):
            old = WechatCredentials(root)
            entered, release = threading.Event(), threading.Event()
            writes = []
            def old_call(action, account, value=None):
                if action == "set":
                    entered.set()
                    release.wait(3)
                writes.append((action, account))
            old._call = old_call
            thread = threading.Thread(target=old.save, args=(ACCOUNT,))
            thread.start()
            self.assertTrue(entered.wait(1))
            old.invalidate()
            fresh = WechatCredentials(root)
            fresh._call = lambda *args: None
            fresh.save(dict(ACCOUNT, token="fresh"))
            marker = fresh.reference.read_text()
            release.set()
            thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(fresh.reference.read_text(), marker)
            self.assertEqual(writes[-1][0], "delete")
            old.clear()
            self.assertEqual(fresh.reference.read_text(), marker)

    def test_quit_retains_existing_keychain_binding_when_save_finishes_late(self):
        with tempfile.TemporaryDirectory() as root, patch("aiflame.integrations.wechat.credentials.sys.platform", "darwin"):
            store = WechatCredentials(root)
            store._call = lambda *args: None
            store.save(ACCOUNT)
            entered, release = threading.Event(), threading.Event()
            calls = []
            def delayed(action, account, value=None):
                if action == "set":
                    entered.set()
                    release.wait(3)
                calls.append(action)
            store._call = delayed
            worker = threading.Thread(target=store.save, args=(ACCOUNT,))
            worker.start()
            self.assertTrue(entered.wait(1))
            store.invalidate()
            release.set()
            worker.join(1)
            self.assertTrue(store.has_account())
            self.assertNotIn("delete", calls)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=[
            SourcePolicy("test", "Test App", "test"), SourcePolicy("manual", "Manual", "manual")])
        self.hub = SimpleNamespace(engine=self.engine, lock=threading.RLock(), runtime=Path(self.temp.name), stopping=threading.Event())
        self.client, self.store = FakeClient(), MemoryCredentials()
        self.service = WechatService(self.hub, self.client, self.store)
        self.engine.router.register(WechatOutput(self.service))
    def tearDown(self):
        self.service.stop()
        self.engine.db.close()
        self.temp.cleanup()
    def wait_for(self, condition, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition(): return
            time.sleep(.01)
        self.fail("Background work did not complete")
    def enable(self, context=True):
        self.service.account = dict(ACCOUNT, context=ACCOUNT["context"] if context else "")
        self.service.bound = True
        self.engine.update_settings({"wechat_notifications": True})
        self.service.start()
    def accept(self, state, task="task", seq=1, source="test"):
        with self.hub.lock:
            return self.engine.accept(make_event(source, task, state, seq), source)[0]
    def receipt(self, event_id):
        with self.hub.lock:
            return self.engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output='wechat'", (event_id,)).fetchone()[0]

    def test_default_off_no_network_and_strict_setting(self):
        self.store.saved = dict(ACCOUNT)
        self.service.start()
        self.assertFalse(self.service.snapshot()["enabled"])
        self.assertEqual(self.accept("done")["deliveries"]["wechat"], "disabled")
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.store.loads, 0)
        with self.assertRaises(EventError): self.engine.update_settings({"wechat_notifications": 1})

    def test_root_states_and_dedup_independent_of_lamp(self):
        self.enable()
        self.engine.update_settings({"brightness": 0})
        self.assertEqual(self.accept("working")["deliveries"]["wechat"], "not_requested")
        self.assertEqual(self.accept("working", seq=2)["deliveries"]["wechat"], "not_requested")
        event = make_event("test", "task", "done", 3)
        with self.hub.lock:
            result = self.engine.accept(event, "test")[0]
            self.assertTrue(self.engine.accept(event, "test")[0]["duplicate"])
        self.wait_for(lambda: self.receipt(result["event_id"]) == "submitted")
        self.assertEqual(len(self.client.sent), 1)
        self.assertEqual(self.client.sent[0][2].splitlines()[0], "SeeEnd · 任务已完成")
        self.assertEqual(self.accept("done", task="manual", source="manual")["deliveries"]["wechat"], "not_requested")
        self.engine.stop()
        self.assertEqual(self.accept("needs_input", task="shutdown")["deliveries"]["wechat"], "suppressed")

    def test_prepare_receives_first_message_before_notifications_are_enabled(self):
        self.store.saved = dict(ACCOUNT, context="")
        self.service.bound = True
        self.service.action({"action": "prepare"})
        self.wait_for(lambda: self.service.snapshot()["needs_message"])
        self.assertFalse(self.service.snapshot()["available"])
        self.assertFalse(self.service.snapshot()["enabled"])
        self.client.messages.append([{"from_user_id": "owner", "message_type": 1, "context_token": "setup-context"}])
        self.wait_for(lambda: self.service.snapshot()["available"])
        self.assertEqual(self.client.sent, [])
        self.assertEqual(self.accept("done")["deliveries"]["wechat"], "disabled")
        self.assertFalse(self.service.snapshot()["ready"])
        self.engine.update_settings({"wechat_notifications": True})
        self.service.settings_changed()
        self.wait_for(lambda: self.service.snapshot()["ready"])
        self.assertEqual(self.store.saved["context"], "setup-context")

    def test_prepare_recovers_when_saved_credentials_are_missing(self):
        self.service.bound = True
        self.service.action({"action": "prepare"})
        self.wait_for(lambda: not self.service.snapshot()["busy"])
        self.assertFalse(self.service.snapshot()["available"])
        self.assertFalse(self.service.snapshot()["preparing"])
        self.assertIn("凭据", self.service.snapshot()["error"])

    def test_context_only_from_bound_user_and_chat_is_not_exposed(self):
        self.enable(context=False)
        self.assertEqual(self.accept("done")["deliveries"]["wechat"], "context_missing")
        with self.assertRaises(EventError): self.service.action({"action": "test"})
        self.client.messages.append([{"from_user_id": "stranger", "message_type": 1, "context_token": "wrong"}])
        self.wait_for(lambda: not self.client.messages)
        self.assertFalse(self.service.snapshot()["ready"])
        self.client.messages.append([{"from_user_id": "owner", "message_type": 1, "context_token": "fresh-context",
                                      "item_list": [{"text_item": {"text": "private incoming chat"}}]}])
        self.wait_for(lambda: self.service.snapshot()["ready"])
        self.service.action({"action": "test"})
        self.wait_for(lambda: self.service.snapshot()["test_status"] == "submitted")
        serialized = json.dumps(self.service.snapshot()) + json.dumps(self.store.saved)
        self.assertNotIn("private incoming chat", serialized)
        self.assertEqual(self.client.sent[0][1], "fresh-context")
        for secret in (ACCOUNT["token"], "fresh-context", ACCOUNT["cursor"]):
            self.assertNotIn(secret, json.dumps(self.service.snapshot()))

    def test_hello_enables_test_alert_with_default_fields_omitted(self):
        incoming = deque([{"msgs": [{"from_user_id": "owner", "message_type": 1,
                                      "context_token": "hello-context"}], "get_updates_buf": "hello-cursor"}])
        sent = []
        @contextmanager
        def response(request, timeout):
            if request.full_url.endswith("/getupdates"):
                result = incoming.popleft() if incoming else {}
            else:
                sent.append(json.loads(request.data)["msg"])
                result = {}
            yield SimpleNamespace(status=200, read=lambda maximum: json.dumps(result).encode())
        self.service.client = ILinkClient(SimpleNamespace(open=response))
        self.enable(context=False)
        self.wait_for(lambda: self.service.snapshot()["ready"])
        self.assertEqual(self.service.account["cursor"], "hello-cursor")
        self.service.action({"action": "test"})
        self.wait_for(lambda: self.service.snapshot()["test_status"] == "submitted")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["to_user_id"], "owner")
        self.assertEqual(sent[0]["context_token"], "hello-context")
        self.assertEqual(self.service.snapshot()["error"], "")

    def test_toggle_off_releases_queued_work_and_late_send_is_unconfirmed(self):
        self.client.send_gate = threading.Event()
        self.enable()
        first = self.accept("done")
        self.wait_for(lambda: bool(self.client.sent))
        second = self.accept("needs_input", task="second")
        self.engine.update_settings({"wechat_notifications": False})
        self.service.settings_changed()
        self.assertEqual(self.receipt(second["event_id"]), "disabled")
        self.client.send_gate.set()
        self.wait_for(lambda: self.receipt(first["event_id"]) == "unconfirmed")
        self.assertEqual(len(self.client.sent), 1)
        self.assertTrue(self.service.snapshot()["bound"])
        self.assertFalse(self.service.snapshot()["ready"])

    def test_timeout_is_not_replayed(self):
        self.client.send_error = "timeout"
        self.enable()
        event = self.accept("done")
        self.wait_for(lambda: self.receipt(event["event_id"]) == "unconfirmed")
        time.sleep(.3)
        self.assertEqual(len(self.client.sent), 1)

    def test_expired_login_rejects_send_and_stops_channel(self):
        self.client.send_error = -14
        self.enable()
        event = self.accept("done")
        self.wait_for(lambda: self.receipt(event["event_id"]) == "failed")
        self.assertFalse(self.service.snapshot()["ready"])
        self.assertIn("登录已失效", self.service.snapshot()["error"])
        self.assertEqual(self.accept("needs_input", task="expired-login")["deliveries"]["wechat"], "unconnected")

    def test_qr_confirmation_and_cancelled_reply(self):
        self.client.statuses.append({"status": "confirmed", "bot_token": ACCOUNT["token"],
                                     "ilink_bot_id": "bot", "ilink_user_id": "owner", "baseurl": ORIGIN})
        self.service.action({"action": "login"})
        self.wait_for(lambda: self.service.snapshot()["bound"])
        self.assertEqual(self.store.saved["context"], "")
        self.assertFalse(self.service.snapshot()["ready"])
        self.assertEqual(self.service.snapshot()["login"]["image"], "")
        self.assertNotIn(ACCOUNT["token"], json.dumps(self.service.snapshot()))
        self.service.action({"action": "disconnect"})
        self.wait_for(lambda: not self.service.snapshot()["busy"])
        self.assertFalse(self.store.saved)
        gate = threading.Event()
        original = self.client.qr_status
        def late(*args):
            gate.wait(3)
            return {"status": "confirmed", "bot_token": "late", "ilink_bot_id": "bot", "ilink_user_id": "owner", "baseurl": ORIGIN}
        self.client.qr_status = late
        self.service.action({"action": "login"})
        self.wait_for(lambda: self.service.snapshot()["login"]["state"] == "wait")
        self.service.action({"action": "cancel"})
        gate.set()
        time.sleep(.1)
        self.assertFalse(self.store.saved)
        self.assertFalse(self.service.snapshot()["bound"])

    def test_stop_discards_late_poll_without_database_or_credential_write(self):
        self.client.poll_gate = threading.Event()
        self.client.messages.append([{"from_user_id": "owner", "message_type": 1, "context_token": "late"}])
        self.enable(context=False)
        self.wait_for(lambda: "updates" in self.client.calls)
        self.service.stop()
        self.client.poll_gate.set()
        time.sleep(.1)
        self.assertNotEqual(self.service.account.get("context"), "late")
        self.assertEqual(self.store.saved, {})


class WechatHttpHandlerTests(unittest.TestCase):
    """Exercise real authentication and action routing without opening a port."""
    def setUp(self):
        from aiflame.hub.server import Hub
        from aiflame.core.paths import load_config
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"AIFLAME_DATA_DIR": self.temp.name})
        self.env.start()
        self.hub = Hub(runtime=Path(self.temp.name), config=load_config())
        self.hub.wechat.client = FakeClient()
    def tearDown(self):
        self.hub.stop()
        self.env.stop()
        self.temp.cleanup()
    def post(self, body, auth="dashboard", origin=None):
        from aiflame.hub.server import Handler
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(context=self.hub)
        handler.path = "/api/wechat"
        handler.headers = Message()
        handler.headers["Host"] = "127.0.0.1:8765"
        handler.headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode()
        handler.headers["Content-Length"] = str(len(payload))
        if auth == "dashboard": handler.headers["Cookie"] = "aiflame=" + self.hub.config["dashboard"]
        elif auth: handler.headers["Authorization"] = "Bearer " + self.hub.config["tokens"][auth]
        if origin: handler.headers["Origin"] = origin
        handler.rfile = BytesIO(payload)
        replies = []
        handler.reply = lambda status, result, **kwargs: replies.append((status, result))
        handler.do_POST()
        return replies[0]
    def test_dashboard_only_exact_body_and_same_origin(self):
        self.assertEqual(self.post({"action": "login"}, auth=None)[0], 401)
        self.assertEqual(self.post({"action": "login"}, auth="codex")[0], 403)
        self.assertEqual(self.post({"action": "login"}, origin="https://evil.test")[0], 403)
        self.assertEqual(self.post({"action": "login", "token": "injected"})[0], 400)
        self.assertEqual(self.hub.wechat.client.calls, [])
        self.assertEqual(self.post({"action": "cancel"})[0], 200)
        self.assertEqual(self.post({"action": "test"})[0], 409)
        self.assertEqual(self.hub.active_posts, 0)


if __name__ == "__main__": unittest.main()
