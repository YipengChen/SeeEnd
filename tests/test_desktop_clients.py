import json
import plistlib
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.hub.server import Hub
from aiflame.integrations.desktop_clients.providers import CLIENTS
from aiflame.integrations.desktop_clients.service import DesktopClients
from aiflame.integrations.desktop_clients.macos import MacReader
from aiflame.integrations.desktop_clients.state import Frame, Tracker
from aiflame.integrations.registry import SOURCES


def frame(generating=False, scope=(10, 20, 30), copies=(1,), finals=(2,), **kw):
    return Frame(
        scope, generating, not generating, frozenset(copies), frozenset(finals), **kw
    )


class StateTests(unittest.TestCase):
    def tracker(self, source="doubao-desktop"):
        events = []
        return Tracker(source, events.append), events

    def test_each_client_long_reply_heartbeat_stable_completion_and_no_replay(self):
        for client in CLIENTS:
            with self.subTest(client=client.id):
                tracker, events = self.tracker(client.id)
                tracker.observe(frame(), 0)
                for now in range(1, 721):
                    tracker.observe(frame(True), now)
                self.assertGreater(len(events), 20)
                self.assertTrue(all(event["state"] == "working" for event in events))
                completed = frame(copies=(1, 3), finals=(2, 4))
                tracker.observe(completed, 721)
                tracker.observe(completed, 722)
                self.assertEqual(events[-1]["state"], "working")
                tracker.observe(completed, 723)
                tracker.observe(completed, 724)
                self.assertEqual(sum(e["state"] == "done" for e in events), 1)
                self.assertEqual({e["source_id"] for e in events}, {client.id})
                self.assertEqual(len({e["task_id"] for e in events}), 1)
                self.assertEqual(
                    [e["seq"] for e in events], list(range(1, len(events) + 1))
                )

    def test_existing_history_or_in_progress_reply_is_not_adopted(self):
        tracker, events = self.tracker()
        for now in range(5):
            tracker.observe(frame(), now)
        self.assertFalse(events)
        tracker.observe(frame(True), 5, interrupted=True)
        tracker.observe(frame(copies=(1, 3), finals=(2, 4)), 6)
        tracker.observe(frame(copies=(1, 3), finals=(2, 4)), 9)
        self.assertFalse(events)

    def test_stop_disappearing_and_rebuilt_history_never_means_done(self):
        tracker, events = self.tracker()
        tracker.observe(frame(), 0)
        tracker.observe(frame(True), 1)
        for now in range(2, 12):
            tracker.observe(frame(copies=(3,), finals=(4,)), now)
        self.assertFalse(any(e["state"] == "done" for e in events))

    def test_cancel_missing_scope_or_observation_gap_cannot_complete(self):
        for mode in ("click", "closed", "navigation", "sleep"):
            with self.subTest(mode=mode):
                tracker, events = self.tracker()
                tracker.observe(frame(), 0)
                tracker.observe(frame(True), 1)
                completed = frame(copies=(1, 3), finals=(2, 4))
                if mode == "click":
                    tracker.observe(completed, 2, interrupted=True)
                elif mode == "closed":
                    tracker.observe(None, 2)
                elif mode == "navigation":
                    tracker.observe(
                        frame(scope=(10, 21, 31), copies=(1, 3), finals=(2, 4)), 2
                    )
                else:
                    tracker.observe(completed, 15)
                tracker.observe(completed, 16)
                tracker.observe(completed, 19)
                self.assertEqual(events[-1]["state"], "idle")
                self.assertEqual(events[-1]["reason"], "cancelled")
                self.assertFalse(any(e["state"] == "done" for e in events))

    def test_new_retry_is_failure_and_completion_debounce_resets(self):
        tracker, events = self.tracker()
        tracker.observe(frame(), 0)
        tracker.observe(frame(True), 1)
        tracker.observe(frame(errors=frozenset((7,))), 2)
        self.assertEqual(events[-1]["state"], "error")
        tracker.observe(frame(True), 3)
        done = frame(copies=(1, 3), finals=(2, 4))
        tracker.observe(done, 4)
        tracker.observe(frame(True), 5)
        tracker.observe(done, 6)
        tracker.observe(done, 7)
        self.assertEqual(events[-1]["state"], "working")
        tracker.observe(done, 8)
        self.assertEqual(events[-1]["state"], "done")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(":memory:", sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        self.context = Mock(engine=self.engine, lock=threading.RLock())
        self.reader = Mock(tap_ready=True)
        self.reader.permissions.return_value = {
            "accessibility": True,
            "input_monitoring": True,
        }
        self.reader.installed.side_effect = lambda c: (
            {c.bundle_ids[0]: "1.0"} if c.bundle_ids else {}
        )
        self.reader.running.return_value = {
            "com.bot.pc.doubao": 10,
            "cn.qwenwork.desktop.mac": 11,
        }
        self.reader.take_interruptions.return_value = set()
        self.reader.scan.return_value = frame()
        self.service = DesktopClients(self.context, self.reader, "darwin")

    def enable(self, source="doubao-desktop"):
        self.engine.update_settings(
            {
                "source_enabled": {
                    **self.engine.settings["source_enabled"],
                    source: True,
                }
            }
        )

    def test_default_disabled_sources_never_scan_or_request_permissions(self):
        self.service.poll(0)
        self.reader.scan.assert_not_called()
        self.reader.request_permissions.assert_not_called()
        self.reader.start_tap.assert_not_called()
        self.assertEqual(self.service.status()["clients"][0]["status"], "disabled")

    def test_permission_check_does_not_prompt_when_already_granted(self):
        result = self.service.request_permissions()
        self.reader.request_permissions.assert_not_called()
        self.reader.permissions.assert_called_once_with()
        self.assertTrue(all(result["permissions"].values()))
        self.assertEqual(self.service.status()["permissions"], result["permissions"])

    def test_missing_permission_requests_and_refreshes_diagnostics(self):
        self.reader.permissions.side_effect = [
            {"accessibility": True, "input_monitoring": False},
            {"accessibility": True, "input_monitoring": True},
        ]
        result = self.service.request_permissions()
        self.reader.request_permissions.assert_called_once_with()
        self.assertEqual(self.reader.permissions.call_count, 2)
        self.assertTrue(all(result["permissions"].values()))
        self.assertEqual(self.service.status()["permissions"], result["permissions"])

    def test_source_identity_and_cancel_on_permission_loss(self):
        self.enable()
        self.service.poll(0)
        self.reader.scan.return_value = frame(True)
        self.service.poll(1)
        self.assertEqual(self.engine.snapshot()["active_count"], 1)
        self.reader.permissions.return_value = {
            "accessibility": False,
            "input_monitoring": True,
        }
        self.service.poll(2)
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["active_count"], 0)
        self.assertEqual(snapshot["events"][0]["reason"], "cancelled")
        self.assertEqual(snapshot["events"][0]["source"], "doubao-desktop")
        self.assertNotIn("scope", json.dumps(snapshot))

    def test_chatgpt_desktop_requires_chat_profile_and_uses_separate_source(self):
        self.reader.running.return_value = {
            "com.openai.codex": 12,
        }
        self.enable("chatgpt-desktop")
        self.reader.scan.return_value = frame(scope=(12, 20, 30))
        self.service.poll(0)
        self.reader.scan.return_value = frame(True, scope=(12, 20, 30))
        self.service.poll(1)
        completed = frame(scope=(12, 20, 30), copies=(1, 3), finals=(2, 4))
        self.reader.scan.return_value = completed
        self.service.poll(2)
        self.service.poll(4)
        events = self.engine.snapshot()["events"]
        self.assertEqual({event["source"] for event in events}, {"chatgpt-desktop"})
        self.assertEqual({event["state"] for event in events}, {"working", "done"})
        self.assertTrue(
            all(call.args == (12,) for call in self.reader.scan.call_args_list)
        )
        self.assertTrue(
            all(
                call.kwargs == {"chatgpt": True}
                for call in self.reader.scan.call_args_list
            )
        )

    def test_upgrade_preserves_enabled_desktop_clients_and_codex(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "db"
            old = Engine(
                path, sources=tuple(s for s in SOURCES if s.id != "chatgpt-desktop")
            )
            old.update_settings(
                {
                    "source_enabled": {
                        **old.settings["source_enabled"],
                        "doubao-desktop": True,
                        "codex": True,
                    }
                }
            )
            old.db.close()
            new = Engine(path, sources=SOURCES)
            self.addCleanup(new.db.close)
            self.assertTrue(new.settings["source_enabled"]["doubao-desktop"])
            self.assertTrue(new.settings["source_enabled"]["codex"])
            self.assertFalse(new.settings["source_enabled"]["chatgpt-desktop"])

    def test_interrupt_during_scan_cancels_before_terminal_decision(self):
        self.enable()
        self.service.poll(0)
        self.reader.scan.return_value = frame(True)
        self.service.poll(1)
        self.reader.scan.return_value = frame(copies=(1, 3), finals=(2, 4))
        self.service.poll(2)
        self.reader.take_interruptions.return_value = {10}
        self.service.poll(5)
        self.assertFalse(
            any(e["state"] == "done" for e in self.engine.snapshot()["events"])
        )

    def test_send_click_or_enter_from_idle_does_not_cancel_new_task(self):
        self.enable()
        self.service.poll(0)
        self.reader.take_interruptions.return_value = {10}
        self.reader.scan.return_value = frame(True)
        self.service.poll(1)
        self.assertEqual(self.engine.snapshot()["active_count"], 1)
        self.assertEqual(self.engine.snapshot()["events"][0]["state"], "working")

    def test_native_failure_in_one_client_does_not_interrupt_another(self):
        self.enable()
        self.engine.update_settings(
            {
                "source_enabled": {
                    **self.engine.settings["source_enabled"],
                    "qianwen-desktop": True,
                }
            }
        )
        self.reader.scan.side_effect = lambda pid: frame(scope=(pid, 20, 30))
        self.service.poll(0)

        def scan(pid):
            if pid == 10:
                raise RuntimeError("Native control timeout")
            return frame(True, scope=(pid, 20, 30))

        self.reader.scan.side_effect = scan
        self.service.poll(1)
        self.assertEqual(self.engine.snapshot()["active_count"], 1)
        self.assertEqual(
            self.engine.snapshot()["events"][0]["source"], "qianwen-desktop"
        )
        self.assertEqual(
            self.service.status()["clients"][0]["status"], "controls_unavailable"
        )

    def test_windows_and_linux_do_not_load_native_libraries(self):
        for platform in ("win32", "linux"):
            service = DesktopClients(self.context, platform=platform)
            service.start()
            self.assertIsNone(service.reader)
            self.assertFalse(service.status()["supported"])
            with self.assertRaises(Exception):
                service.request_permissions()

    def test_migration_keeps_existing_settings_and_defaults_new_clients_off(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "db"
            old = Engine(
                path, sources=tuple(s for s in SOURCES if not s.id.endswith("-desktop"))
            )
            old.update_settings(
                {
                    "brightness": 12,
                    "source_enabled": {
                        **old.settings["source_enabled"],
                        "codex": False,
                    },
                }
            )
            old.db.close()
            new = Engine(path, sources=SOURCES)
            self.addCleanup(new.db.close)
            self.assertEqual(new.settings["brightness"], 12)
            self.assertFalse(new.settings["source_enabled"]["codex"])
            self.assertTrue(
                all(not new.settings["source_enabled"][c.id] for c in CLIENTS)
            )


class NativeControlsTests(unittest.TestCase):
    def test_chatgpt_matches_known_bundle_ids_never_filename_alone(self):
        client = next(c for c in CLIENTS if c.id == "chatgpt-desktop")
        reader = object.__new__(MacReader)
        with tempfile.TemporaryDirectory() as root:
            applications = Path(root) / "Applications"
            applications.mkdir()
            app = applications / "ChatGPT.app" / "Contents"
            app.mkdir(parents=True)
            info = app / "Info.plist"
            with patch("aiflame.integrations.desktop_clients.macos.Path") as paths:
                paths.return_value = applications
                paths.home.return_value = Path(root) / "User"
                info.write_bytes(
                    plistlib.dumps({"CFBundleIdentifier": "com.example.chatgpt"})
                )
                self.assertEqual(reader.installed(client), {})
                info.write_bytes(
                    plistlib.dumps(
                        {
                            "CFBundleIdentifier": "com.openai.chat",
                            "CFBundleShortVersionString": "1.0",
                        }
                    )
                )
                self.assertEqual(reader.installed(client), {"com.openai.chat": "1.0"})
                info.write_bytes(
                    plistlib.dumps({"CFBundleIdentifier": "com.openai.codex"})
                )
                self.assertEqual(reader.installed(client), {"com.openai.codex": "未知"})

    def reader(self, nodes):
        reader = object.__new__(MacReader)
        seen = []

        def get(element, name, default=None):
            seen.append((element, name))
            if name == "AXValue" or element in (98, 99):
                raise AssertionError("Private text must not be queried")
            return nodes[element].get(name, default)

        @contextmanager
        def attribute(element, name):
            yield get(element, name)

        reader.create_app = lambda pid: 1
        reader.timeout = Mock()
        reader.release = Mock()
        reader.hash = lambda value: value
        reader.attribute = attribute
        reader.label = lambda element, name: get(element, name, "")
        reader.flag = lambda element, name, default: get(element, name, default)
        reader.array_count = len
        reader.array_item = lambda array, index: array[index]
        reader.window_bounds = lambda window: (0, 0, 100, 100)
        reader.guard = threading.Lock()
        reader.bounds = {}
        return reader, seen

    def test_chatgpt_mode_and_lifecycle_from_static_control_labels_without_text(self):
        for composer, send, stop, final in (
            ("Ask ChatGPT", "Send", "Stop", "Good response"),
            ("询问 ChatGPT", "发送", "停止", "回复优秀"),
            ("問問 ChatGPT", "傳送", "停止", "回應良好"),
        ):
            with self.subTest(composer=composer):
                nodes = {
                    1: {"AXWindows": [2]},
                    2: {"AXRole": "AXWindow", "AXChildren": [3, 4, 5, 6]},
                    3: {
                        "AXRole": "AXTextArea",
                        "AXDescription": composer,
                        "AXChildren": [99],
                    },
                    4: {"AXRole": "AXButton", "AXTitle": send, "AXEnabled": False},
                    5: {"AXRole": "AXButton", "AXDescription": "Copy"},
                    6: {"AXRole": "AXButton", "AXDescription": final},
                }
                reader, seen = self.reader(nodes)
                events = []
                tracker = Tracker("chatgpt-desktop", events.append)
                tracker.observe(reader.scan(12, chatgpt=True), 0)
                nodes[4].update(AXTitle=stop, AXEnabled=True)
                tracker.observe(reader.scan(12, chatgpt=True), 1)
                nodes[4].update(AXTitle=send, AXEnabled=False)
                nodes[7] = {"AXRole": "AXButton", "AXDescription": "Copy"}
                nodes[8] = {"AXRole": "AXButton", "AXDescription": final}
                nodes[2]["AXChildren"].extend([7, 8])
                tracker.observe(reader.scan(12, chatgpt=True), 2)
                tracker.observe(reader.scan(12, chatgpt=True), 4)
                self.assertEqual([e["state"] for e in events], ["working", "done"])
                self.assertFalse(any(name == "AXValue" for _, name in seen))
                self.assertFalse(any(element == 99 for element, _ in seen))

    def test_chatgpt_rejects_work_codex_unknown_and_conflicting_labels(self):
        for description, placeholder in (
            ("Work with ChatGPT", ""),
            ("Do anything", ""),
            ("", ""),
            ("Ask ChatGPT", "Work with ChatGPT"),
        ):
            with self.subTest(description=description, placeholder=placeholder):
                nodes = {
                    1: {"AXWindows": [2]},
                    2: {"AXRole": "AXWindow", "AXChildren": [3, 4]},
                    3: {
                        "AXRole": "AXTextArea",
                        "AXDescription": description,
                        "AXPlaceholderValue": placeholder,
                    },
                    4: {"AXRole": "AXButton", "AXTitle": "Stop"},
                }
                reader, _ = self.reader(nodes)
                self.assertIsNone(reader.scan(12, chatgpt=True))

    def test_chatgpt_unrelated_stop_cannot_start_a_task_when_send_is_present(self):
        nodes = {
            1: {"AXWindows": [2]},
            2: {"AXRole": "AXWindow", "AXChildren": [3, 4, 5]},
            3: {"AXRole": "AXTextArea", "AXDescription": "Ask ChatGPT"},
            4: {"AXRole": "AXButton", "AXTitle": "Send"},
            5: {"AXRole": "AXButton", "AXTitle": "Stop"},
        }
        reader, _ = self.reader(nodes)
        self.assertFalse(reader.scan(12, chatgpt=True).generating)

    def test_editor_and_static_text_are_never_read_and_disabled_send_is_present(self):
        reader = object.__new__(MacReader)
        nodes = {
            1: {"AXWindows": [2]},
            2: {"AXRole": "AXWindow", "AXChildren": [3, 4, 5, 6, 7]},
            3: {"AXRole": "AXTextArea", "AXEnabled": False, "AXChildren": [99]},
            4: {"AXRole": "AXButton", "AXTitle": "发送", "AXEnabled": False},
            5: {"AXRole": "AXButton", "AXDescription": "复制"},
            6: {"AXRole": "AXButton", "AXDescription": "点赞"},
            7: {"AXRole": "AXStaticText", "AXChildren": [98]},
        }
        seen = []

        def get(element, name, default=None):
            seen.append((element, name))
            if element in (98, 99) or name == "AXValue":
                raise AssertionError("Private text must not be queried")
            return nodes[element].get(name, default)

        @contextmanager
        def attribute(element, name):
            yield get(element, name)

        reader.create_app = lambda pid: 1
        reader.timeout = Mock()
        reader.release = Mock()
        reader.hash = lambda value: value
        reader.attribute = attribute
        reader.label = lambda element, name: get(element, name, "")
        reader.flag = lambda element, name, default: get(element, name, default)
        reader.array_count = len
        reader.array_item = lambda array, index: array[index]
        reader.window_bounds = lambda window: (0, 0, 100, 100)
        reader.guard = threading.Lock()
        reader.bounds = {}
        result = reader.scan(10)
        self.assertEqual(result.scope, (10, 2, 3))
        self.assertTrue(result.send)
        self.assertEqual(result.copies, frozenset((5,)))
        self.assertEqual(result.finals, frozenset((6,)))
        self.assertFalse(
            any(
                e in (3, 7) and a in ("AXTitle", "AXDescription", "AXChildren")
                for e, a in seen
            )
        )


class HTTPTests(unittest.TestCase):
    def test_dashboard_only_diagnostics_and_explicit_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            hub = Hub(
                port=0,
                runtime=Path(root),
                config={
                    "dashboard": "test-cookie",
                    "tokens": {"local-script": "test-token"},
                },
            )
            hub.start(hardware=False)
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

                def request(path, dashboard=True, body=None):
                    headers = (
                        {"Cookie": "aiflame=test-cookie"}
                        if dashboard
                        else {"Authorization": "Bearer test-token"}
                    )
                    headers["Content-Type"] = "application/json"
                    return opener.open(
                        urllib.request.Request(
                            f"http://127.0.0.1:{hub.port}" + path,
                            headers=headers,
                            data=json.dumps(body).encode()
                            if body is not None
                            else None,
                        )
                    )

                with request("/api/desktop-clients") as response:
                    clients = json.load(response)["clients"]
                    self.assertEqual(len(clients), 4)
                    self.assertIn("chatgpt-desktop", {c["source_id"] for c in clients})
                with patch.object(
                    hub.desktop_clients,
                    "request_permissions",
                    return_value={"message": "test"},
                ) as prompt:
                    with request(
                        "/api/desktop-clients/permissions", body={}
                    ) as response:
                        self.assertEqual(response.status, 200)
                    for path, body in (
                        ("/api/desktop-clients", None),
                        ("/api/desktop-clients/permissions", {}),
                    ):
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            request(path, dashboard=False, body=body)
                        error.exception.close()
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        request(
                            "/api/desktop-clients/permissions", body={"extra": True}
                        )
                    error.exception.close()
                    prompt.assert_called_once()
            finally:
                hub.stop()


if __name__ == "__main__":
    unittest.main()
