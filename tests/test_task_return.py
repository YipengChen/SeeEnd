"""Destination isolation, expiry, bridge authorization and user-initiated returns."""

import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.core.task_origins import validate_origin
from aiflame.hub.task_returns import TaskReturns
from aiflame.integrations.registry import SOURCES
from aiflame.integrations.cli_tools.lifecycle import from_hooks
from aiflame.integrations.cli_tools.origin import capture, prepare
from aiflame.integrations.codex.events import from_hook


def web(client=None):
    return {"kind": "web", "client_id": client or str(uuid.uuid4()), "locator_id": str(uuid.uuid4())}


def terminal(**changes):
    return {"kind": "terminal", "app": "Terminal", "tty": "/dev/ttys003", "pid": 123,
            "started": "Wed Oct 14 12:00:00 2026", "instance_id": "instance-a", "session_id": "session-a", **changes}


class OriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=SOURCES)
        self.addCleanup(lambda: self.engine.db.close())

    def start(self, task, origin, source="chatgpt-web"):
        event = {**make_event(source, task, "working"), "return_target": origin}
        self.engine.accept(event, source)
        return event

    def test_same_source_tasks_keep_independent_locations_after_completion(self):
        destinations = [web(), web()]
        for index, destination in enumerate(destinations):
            self.start(str(index), destination)
            done = make_event("chatgpt-web", str(index), "done", 2)
            self.engine.accept(done, "chatgpt-web")
            self.assertEqual(self.engine.return_target(done["event_id"])[0], destination)
        self.assertEqual(sum(bool(e["return_to"]) for e in self.engine.snapshot()["events"]), 4)

    def test_display_return_follows_current_prompt_then_latest_active_task(self):
        first = self.start("first", web())
        second = self.start("second", web())
        self.assertEqual(self.engine.display_status()["return_task"]["id"], second["event_id"])
        done = make_event("chatgpt-web", "first", "done", 2)
        self.engine.accept(done, "chatgpt-web")
        display = self.engine.display_status()
        self.assertEqual(display["return_task"]["id"], done["event_id"])
        self.assertEqual(self.engine.return_target(display["return_task"]["id"])[0], first["return_target"])
        self.engine.clear_prompts("expired")
        self.assertEqual(self.engine.display_status()["return_task"]["id"], second["event_id"])
        self.engine.active.clear()
        self.assertIsNone(self.engine.display_status()["return_task"])

    def test_legacy_and_simulated_display_cannot_borrow_other_task_destination(self):
        self.start("first", web())
        legacy = make_event("codex", "legacy", "working")
        self.engine.accept(legacy, "codex")
        display = self.engine.display_status()
        self.assertEqual(display["return_task"], {"id": legacy["event_id"], "return_to": None})
        self.engine.accept(make_event("manual", "demo", "done"), "manual")
        self.assertTrue(self.engine.display_status()["simulated"])
        self.assertIsNone(self.engine.display_status()["return_task"])

    def test_later_event_cannot_redirect_task_and_legacy_events_still_work(self):
        first = self.start("one", web())
        self.engine.accept({**make_event("chatgpt-web", "one", "done", 2), "return_target": web()}, "chatgpt-web")
        self.assertEqual(self.engine.return_target(first["event_id"])[0], first["return_target"])
        self.engine.accept(make_event("codex", "legacy", "working"), "codex")
        event = next(e for e in self.engine.snapshot()["events"] if e["task"] == "legacy")
        self.assertIsNone(event["return_to"])

    def test_origin_survives_restart_and_is_deleted_with_retained_history(self):
        event = self.start("one", web())
        self.engine.db.close()
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=SOURCES)
        self.assertEqual(self.engine.return_target(event["event_id"])[0], event["return_target"])
        self.engine.db.execute("UPDATE events SET created=0")
        self.engine.db.execute("UPDATE tasks SET updated=0")
        self.engine.cleanup(force=True)
        self.assertEqual(self.engine.db.execute("SELECT count(*) FROM task_origins").fetchone()[0], 0)

    def test_switching_conversation_invalidates_previous_task_but_other_instances_survive(self):
        first = self.start("first", terminal(), "codex")
        other = self.start("other", terminal(instance_id="instance-b", pid=456), "codex")
        self.start("new", terminal(session_id="session-b"), "codex")
        with self.assertRaisesRegex(EventError, "其他会话"):
            self.engine.return_target(first["event_id"])
        self.assertEqual(self.engine.return_target(other["event_id"])[0]["pid"], 456)

    def test_rejects_commands_content_cross_kind_and_unsafe_terminal_fields(self):
        invalid = [({**web(), "command": "open anything"}, "chatgpt-web"), (terminal(), "chatgpt-web"),
                   (web(), "codex"), (terminal(tty="/dev/ttys003;open"), "codex"),
                   (terminal(pid=True), "codex"), (terminal(app="Finder"), "codex"),
                   (terminal(app="iTerm2", session_guid="invalid"), "codex")]
        for value, source in invalid:
            with self.subTest(value=value), self.assertRaises(EventError):
                validate_origin(value, source)


class CaptureTests(unittest.TestCase):
    def test_process_birth_identity_is_independent_of_terminal_locale_and_timezone(self):
        from aiflame.integrations.cli_tools.origin import processes
        with patch.dict(os.environ, {"TZ":"Asia/Shanghai", "LC_ALL":"zh_CN.UTF-8"}), patch("aiflame.integrations.cli_tools.origin.subprocess.run", return_value=SimpleNamespace(stdout="123 4 ttys003 Wed Oct 14 12:00:00 2026 /usr/bin/node\n")) as run:
            self.assertEqual(processes()[123]["started"], "Wed Oct 14 12:00:00 2026")
            self.assertEqual(run.call_args.kwargs["env"]["TZ"], "UTC")
            self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_capture_skips_hook_shell_and_retains_only_project_name(self):
        records = {7: {"ppid": 123, "tty": "ttys003", "started": "shell", "command": "/bin/sh"},
                   123: {"ppid": 4, "tty": "ttys003", "started": "started", "command": "/usr/bin/node"}}
        with patch("aiflame.integrations.cli_tools.origin.sys.platform", "darwin"):
            value = capture({"session_id": "s", "cwd": "/private/company/SeeEnd", "prompt": "PRIVATE"},
                            environ={"TERM_PROGRAM": "Apple_Terminal"}, records=records, parent_pid=7)
        self.assertEqual(value["pid"], 123)
        self.assertEqual(value["project"], "SeeEnd")
        self.assertNotIn("PRIVATE", json.dumps(value))
        self.assertNotIn("company", json.dumps(value))

    def test_iterm_guid_and_remote_or_multiplexed_sessions(self):
        records = {123: {"ppid": 4, "tty": "ttys003", "started": "started", "command": "codex"}}
        env = {"TERM_PROGRAM": "iTerm.app", "ITERM_SESSION_ID": "w0t1p2:" + str(uuid.uuid4())}
        with patch("aiflame.integrations.cli_tools.origin.sys.platform", "darwin"):
            self.assertEqual(capture({"session_id": "s"}, environ=env, records=records, parent_pid=123)["session_guid"], env["ITERM_SESSION_ID"].split(":")[1])
            for extra in ({"TMUX": "socket"}, {"SSH_TTY": "/dev/pts/1"}, {"TERM_PROGRAM": "vscode"}):
                self.assertIsNone(capture({"session_id": "s"}, environ={**env, **extra}, records=records, parent_pid=123))

    def test_unavailable_location_metadata_never_blocks_lifecycle_translation(self):
        records = {123: {"ppid":4, "tty":"ttys003", "started":"started", "command":"codex"}}
        payload = {"hook_event_name":"UserPromptSubmit", "session_id":"oversized-" + "x"*256, "turn_id":"turn"}
        with patch("aiflame.integrations.cli_tools.origin.sys.platform", "darwin"):
            self.assertIsNone(capture(payload, environ={"TERM_PROGRAM":"Apple_Terminal"}, records=records, parent_pid=123))
        self.assertEqual(from_hook(payload)["state"], "working")

    def test_process_inspection_failure_does_not_change_hook_task_namespace(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"AIFLAME_DATA_DIR": root, "TERM_PROGRAM": "Apple_Terminal", "TERM_SESSION_ID": "terminal-tab"}), patch("aiflame.integrations.cli_tools.origin.sys.platform", "darwin"), patch("aiflame.integrations.cli_tools.origin.capture", side_effect=[terminal(), None, terminal()]):
            values = [prepare({"session_id": "s"})[0]["instance_id"] for _ in range(3)]
        self.assertEqual(len(set(values)), 1)

    def test_self_tests_do_not_capture_or_change_simulated_task_identity(self):
        with patch("aiflame.integrations.cli_tools.origin.capture") as collect:
            payload = {"session_id":"aiflame-self-test:fixture"}
            self.assertEqual(prepare(payload), (payload, None))
            collect.assert_not_called()

    def test_two_instances_of_same_conversation_and_turn_remain_separate(self):
        with tempfile.TemporaryDirectory() as root:
            for source in ("codex", "claude-code"):
                events = []
                for instance in ("A", "B"):
                    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "same", "turn_id": "same", "instance_id": instance}
                    events.append(from_hook(payload, root) if source == "codex" else from_hooks(source, payload, root)[0])
                self.assertNotEqual(events[0]["task_id"], events[1]["task_id"])

    def test_missing_native_turn_cancellation_is_scoped_to_instance(self):
        with tempfile.TemporaryDirectory() as root:
            start = {"hook_event_name": "UserPromptSubmit", "session_id": "same"}
            a = from_hooks("claude-code", {**start, "instance_id": "A"}, root)[0]
            b = from_hooks("claude-code", {**start, "instance_id": "B"}, root)[0]
            newer = from_hooks("claude-code", {**start, "instance_id": "A"}, root)
            self.assertEqual(newer[0]["task_id"], a["task_id"])
            self.assertNotEqual(newer[0]["task_id"], b["task_id"])


class ReturnQueueTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.service = TaskReturns(clock=lambda: self.now)
        self.addCleanup(self.service.close)

    def test_profile_isolation_reconnect_revocation_and_completion(self):
        a, b = web(), web()
        ca = self.service.register(a["client_id"])["connection_id"]
        cb = self.service.register(b["client_id"])["connection_id"]
        request = self.service.open("event", a, "chatgpt-web")["request_id"]
        self.assertEqual(self.service.poll(b["client_id"], cb, wait=0)["commands"], [])
        commands = self.service.poll(a["client_id"], ca, wait=0)["commands"]
        self.assertEqual(commands[0]["locator_id"], a["locator_id"])
        with self.assertRaises(EventError):
            self.service.result(b["client_id"], cb, request, "opened")
        self.service.result(a["client_id"], ca, request, "opened")
        self.assertEqual(self.service.status(request)["status"], "opened")
        self.service.register(a["client_id"])
        with self.assertRaises(EventError):
            self.service.poll(a["client_id"], ca, wait=0)

    def test_no_background_return_deduplicated_clicks_and_timeout(self):
        a = web()
        ca = self.service.register(a["client_id"])["connection_id"]
        self.assertEqual(self.service.poll(a["client_id"], ca, wait=0)["commands"], [])
        first = self.service.open("event", a, "chatgpt-web")
        self.assertEqual(self.service.open("event", a, "chatgpt-web"), first)
        self.now = 21
        self.assertEqual(self.service.status(first["request_id"])["status"], "failed")
        self.assertEqual(self.service.poll(a["client_id"], ca, wait=0)["commands"], [])

    def test_disconnected_profile_is_actionable_failure(self):
        with self.assertRaisesRegex(EventError, "原浏览器"):
            self.service.open("event", web(), "chatgpt-web")

    def test_reconnect_delivers_unpolled_request_once_and_keeps_its_receipt(self):
        destination = web()
        client = destination["client_id"]
        previous = self.service.register(client)["connection_id"]
        request = self.service.open("event", destination, "chatgpt-web")
        for _ in range(2):
            connection = self.service.register(client)["connection_id"]
        with self.assertRaises(EventError):
            self.service.poll(client, previous, wait=0)
        self.assertEqual(self.service.open("event", destination, "chatgpt-web"), request)
        commands = self.service.poll(client, connection, wait=0)["commands"]
        self.assertEqual([item["request_id"] for item in commands], [request["request_id"]])
        self.assertEqual(self.service.poll(client, connection, wait=0)["commands"], [])
        self.service.result(client, connection, request["request_id"], "opened")
        self.assertEqual(self.service.status(request["request_id"])["status"], "opened")

    def test_reconnect_never_replays_a_polled_or_expired_request(self):
        destination = web()
        client = destination["client_id"]
        connection = self.service.register(client)["connection_id"]
        request = self.service.open("delivered", destination, "chatgpt-web")
        self.assertEqual(len(self.service.poll(client, connection, wait=0)["commands"]), 1)
        self.service.open("expired", destination, "chatgpt-web")
        self.now = 19
        connection = self.service.register(client)["connection_id"]
        commands = self.service.poll(client, connection, wait=0)["commands"]
        self.assertEqual(len(commands), 1)
        self.assertNotEqual(commands[0]["request_id"], request["request_id"])
        self.service.open("also-expired", destination, "chatgpt-web")
        self.now = 40
        connection = self.service.register(client)["connection_id"]
        self.assertEqual(self.service.poll(client, connection, wait=0)["commands"], [])


class TerminalReturnTests(unittest.TestCase):
    def test_reused_pid_or_tty_never_invokes_automation(self):
        from aiflame.integrations.terminal_return import activate
        for item in (None, {"started": "new", "tty": "ttys003"}, {"started": terminal()["started"], "tty": "ttys004"}):
            with patch("aiflame.integrations.terminal_return.sys.platform", "darwin"), patch("aiflame.integrations.terminal_return.processes", return_value={123:item} if item else {}), patch("aiflame.integrations.terminal_return.subprocess.run") as run:
                with self.assertRaises(EventError):
                    activate(terminal())
                run.assert_not_called()

    def test_selector_uses_arguments_and_reports_automation_denial(self):
        from aiflame.integrations.terminal_return import activate
        with patch("aiflame.integrations.terminal_return.sys.platform", "darwin"), patch("aiflame.integrations.terminal_return.processes", return_value={123:{"started":terminal()["started"], "tty":"ttys003"}}), patch("aiflame.integrations.terminal_return.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="Not authorized (-1743)", stdout="")) as run:
            with self.assertRaisesRegex(EventError, "自动化"):
                activate(terminal())
            self.assertEqual(run.call_args.args[0], ["/usr/bin/osascript", "-e", 'tell application id "com.apple.Terminal" to get count of windows'])

    def test_session_is_revalidated_after_authorization_before_selecting(self):
        from aiflame.integrations.terminal_return import activate, TERMINAL_SCRIPT
        with patch("aiflame.integrations.terminal_return.sys.platform", "darwin"), patch("aiflame.integrations.terminal_return.processes", return_value={123:{"started":terminal()["started"], "tty":"ttys003"}}), patch("aiflame.integrations.terminal_return.subprocess.run", return_value=SimpleNamespace(returncode=0, stderr="", stdout="opened")) as run:
            with self.assertRaisesRegex(EventError, "其他会话"):
                activate(terminal(), is_current=lambda: False)
            self.assertEqual(run.call_count, 1)
            run.reset_mock()
            self.assertEqual(activate(terminal(), is_current=lambda: True), "opened")
            self.assertEqual(run.call_args.args[0], ["/usr/bin/osascript", "-e", TERMINAL_SCRIPT, "/dev/ttys003"])


class ReturnHttpTests(unittest.TestCase):
    def setUp(self):
        from aiflame.core.paths import load_config
        from aiflame.hub.server import Hub
        from live_client import DashboardClient
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"AIFLAME_DATA_DIR": self.temp.name})
        self.env.start()
        self.hub = Hub(port=0, config=load_config())
        self.hub.start(hardware=False)
        self.client = DashboardClient(f"http://127.0.0.1:{self.hub.port}")

    def tearDown(self):
        self.hub.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_authenticated_browser_to_app_to_browser_round_trip(self):
        target = web()
        headers = {"Authorization": "Bearer " + self.hub.config["tokens"]["chatgpt-web"]}
        code, connection = self.client.request("/v1/task-return/connect", {"client_id":target["client_id"]}, headers, authenticated=False)
        self.assertEqual(code, 200)
        event = {**make_event("chatgpt-web", target["locator_id"], "working"), "return_target":target}
        self.assertEqual(self.client.request("/v1/events", event, headers, authenticated=False)[0], 202)
        request = self.client.call("/api/task-return", {"event_id":event["event_id"]})["request_id"]
        from urllib.parse import urlencode
        query = urlencode({"client_id":target["client_id"], **connection})
        code, result = self.client.request("/v1/task-return/poll?" + query, headers=headers, authenticated=False)
        self.assertEqual(code, 200)
        self.assertEqual(result["commands"][0]["request_id"], request)
        code, _ = self.client.request("/v1/task-return/result", {"client_id":target["client_id"], **connection, "request_id":request, "status":"opened"}, headers, authenticated=False)
        self.assertEqual(code, 200)
        self.assertEqual(self.client.call("/api/task-return?request_id=" + request)["status"], "opened")

    def test_sources_cannot_initiate_return_or_claim_browser_connection(self):
        target = web()
        headers = {"Authorization": "Bearer " + self.hub.config["tokens"]["codex"]}
        self.assertEqual(self.client.request("/api/task-return", {"event_id":"e"}, headers, authenticated=False)[0], 403)
        self.assertEqual(self.client.request("/v1/task-return/connect", {"client_id":target["client_id"]}, headers, authenticated=False)[0], 403)
        self.assertEqual(self.client.request("/api/task-return", {"event_id":"e"}, authenticated=False)[0], 401)

    def test_codex_helper_metadata_reaches_authenticated_hub_and_explicit_return(self):
        import subprocess
        import time
        from aiflame.core.process import command
        value = {"session_id": str(uuid.uuid4()), "turn_id": str(uuid.uuid4()), "cwd": self.temp.name}
        payload = {**value, "hook_event_name": "UserPromptSubmit", "prompt": "PRIVATE BODY"}
        argv = command("--codex-hook", "--port", str(self.hub.port))
        if helper := os.environ.get("AIFLAME_TEST_FROZEN_HELPER"):
            argv = [helper, "--codex-hook", "--port", str(self.hub.port)]
        result = subprocess.run(argv, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
        events = self.client.call("/api/status")["events"]
        event = next(item for item in events if item["source"] == "codex")
        with self.hub.lock:
            self.assertEqual(self.hub.engine.origins.context("codex", event["task"]), value)
        self.assertTrue(event["return_to"]["requires_resolution"])
        self.assertNotIn("PRIVATE BODY", json.dumps(events))
        with patch("aiflame.integrations.codex.return_location.resolve_and_activate", return_value="opened") as resolve:
            request = self.client.call("/api/task-return", {"event_id": event["id"]})["request_id"]
            deadline = time.monotonic() + 3
            while (status := self.client.call("/api/task-return?request_id=" + request))["status"] == "pending":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.01)
            self.assertEqual(status["status"], "opened")
            self.assertEqual(resolve.call_args.args[0], {"kind": "codex-session", **value})
        headers = {"Authorization": "Bearer " + self.hub.config["tokens"]["chatgpt-web"]}
        invalid = {**make_event("chatgpt-web", "wrong-source", "working"), "return_context": value}
        self.assertEqual(self.client.request("/v1/events", invalid, headers, authenticated=False)[0], 400)

    def test_actual_native_helper_delivers_return_commands_and_receipts(self):
        import queue
        import subprocess
        import threading
        from aiflame.core.process import command
        from aiflame.integrations.chatgpt_web.host import extension_origins, read_message, write_message
        argv = command("--browser-host", extension_origins()[0], "--port", str(self.hub.port))
        helper = os.environ.get("AIFLAME_TEST_FROZEN_HELPER")
        if helper:
            argv = [helper, "--browser-host", extension_origins()[0], "--port", str(self.hub.port)]
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        received = queue.Queue()

        def read():
            try:
                while True:
                    item = read_message(process.stdout)
                    if item is None:
                        break
                    received.put(item)
            except (OSError, ValueError):
                pass

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            target = web()
            write_message(process.stdin, {"kind":"return-hello", "client_id":target["client_id"]})
            ready = received.get(timeout=5)
            self.assertEqual(ready, {"kind":"return-ready", "ok":True})
            event = {**make_event("chatgpt-web", target["locator_id"], "working"), "return_target":target}
            with self.hub.lock:
                self.hub.engine.accept(event, "chatgpt-web")
            request = self.client.call("/api/task-return", {"event_id":event["event_id"]})["request_id"]
            outgoing = received.get(timeout=5)
            self.assertEqual(outgoing["request_id"], request)
            self.assertEqual(outgoing["locator_id"], target["locator_id"])
            self.assertNotIn("connection_id", outgoing)
            write_message(process.stdin, {"kind":"return-result", "request_id":request, "status":"opened"})
            self.assertEqual(received.get(timeout=5)["kind"], "return-ack")
            self.assertEqual(self.client.call("/api/task-return?request_id=" + request)["status"], "opened")
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            reader.join(timeout=2)
            process.stdout.close()
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
