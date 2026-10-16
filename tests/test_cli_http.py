"""Exercise authenticated CLI configuration and actual helper delivery to a Hub."""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
import subprocess
from unittest.mock import patch

from aiflame.core.paths import load_config
from aiflame.hub.server import Hub
from aiflame.integrations.cli_tools.providers import CLI_SOURCES
from live_client import DashboardClient


class CliHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"AIFLAME_DATA_DIR": str(self.root / "runtime"),
                                          "DSH_HOME": str(self.root / "dsh")})
        self.env.start()
        self.hub = Hub(port=0, config=load_config())
        self.hub.start(hardware=False)
        self.client = DashboardClient(f"http://127.0.0.1:{self.hub.port}")

    def tearDown(self):
        self.hub.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_install_enable_check_and_remove_preserve_external_hooks(self):
        directory = self.root / "claude config"
        directory.mkdir()
        path = directory / "settings.json"
        original = {"env": {"KEEP": "yes"}, "hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "external-hook"}]}]}}
        path.write_text(json.dumps(original))
        self.client.call("/api/integrations/install", {
            "source": "claude-code", "config_dir": str(directory)})
        self.assertTrue(self.client.call("/api/status")["settings"]["source_enabled"]["claude-code"])
        rows = self.client.call("/api/cli-integrations")["integrations"]
        self.assertEqual({row["source_id"] for row in rows}, set(CLI_SOURCES))
        self.assertTrue(next(row for row in rows if row["source_id"] == "claude-code")["configuration_ready"])
        self.client.call("/api/integrations/uninstall", {"source": "claude-code"})
        self.assertEqual(json.loads(path.read_text()), original)
        self.assertFalse(self.client.call("/api/status")["settings"]["source_enabled"]["claude-code"])

    def test_invalid_requests_and_authentication(self):
        code, _ = self.client.request("/api/cli-integrations", authenticated=False)
        self.assertEqual(code, 401)
        for body in ({"source": "unknown"}, {"source": "qoder", "config_dir": []},
                     {"source": "qoder", "extra": True}):
            code, _ = self.client.request("/api/integrations/install", body)
            self.assertEqual(code, 400)

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal", "TERM_SESSION_ID": "seeend-simulation"})
    def test_all_cli_helpers_reach_hub_without_counting_as_real_tasks(self):
        enabled = dict(self.hub.engine.settings["source_enabled"])
        enabled.update({source: True for source in CLI_SOURCES})
        self.client.call("/api/settings", {"source_enabled": enabled})
        for source in CLI_SOURCES:
            with self.subTest(source=source):
                self.client.call("/api/trigger-test", {"source": source})
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    job = self.client.call("/api/trigger-test")["job"]
                    if job["state"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(job["state"], "succeeded", job)
                self.assertEqual(len(job["events"]), 2)
                row = next(row for row in self.client.call("/api/cli-integrations")["integrations"]
                           if row["source_id"] == source)
                self.assertEqual(row["signals"]["start"]["received"], 0)
                self.assertEqual(row["signals"]["complete"]["received"], 0)
                status = self.client.call("/api/status")
                self.assertEqual(status["verification"][source]["complete_tasks"], 0)
                self.assertTrue(all(event["simulated"] for event in status["events"]
                                    if event["source"] == source))

    @patch.dict(os.environ, {"TERM_PROGRAM": "Apple_Terminal", "TERM_SESSION_ID": "seeend-simulation"})
    def test_codex_and_web_self_tests_use_the_ephemeral_hub_port(self):
        self.assertNotEqual(self.hub.port, 8765)
        for source in ('codex', 'chatgpt-web'):
            self.client.call('/api/trigger-test', {'source': source})
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                job = self.client.call('/api/trigger-test')['job']
                if job['state'] != 'running':
                    break
                time.sleep(.02)
            self.assertEqual(job['state'], 'succeeded', job)
            self.assertEqual(len(job['events']), 2)
            self.assertEqual(self.client.call('/api/status')['verification'][source]['complete_tasks'], 0)

    @unittest.skipUnless(os.environ.get("AIFLAME_TEST_FROZEN_HELPER"), "Supply a built App helper to test packaging")
    def test_frozen_helpers_deliver_real_lifecycle(self):
        executable = os.environ["AIFLAME_TEST_FROZEN_HELPER"]
        enabled = {**self.hub.engine.settings["source_enabled"],
                   **{source: True for source in CLI_SOURCES}}
        self.client.call("/api/settings", {"source_enabled": enabled, "duration": 3,
                                           "hold_until_activity": False})
        for source in CLI_SOURCES:
            kinds = (("BeforeAgent", "AfterAgent") if source == "gemini-cli"
                     else ("TurnStart", "TurnEnd") if source in ("opencode", "deepseek-harness")
                     else ("UserPromptSubmit", "Stop"))
            for kind, state in zip(kinds, ("working", "done")):
                payload = {"hook_event_name": kind, "session_id": "frozen-" + source,
                           "turn_id": "1", **({"reason": "completed"} if kind == "TurnEnd" else {})}
                result = subprocess.run([executable, "--cli-hook", source, "--port", str(self.hub.port)],
                                        input=json.dumps(payload), text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "{}\n" if source == "gemini-cli" else "")
                self.assertEqual(result.stderr, "")
                deadline = time.monotonic() + 10
                while True:
                    status = self.client.call("/api/status")
                    events = [event for event in status["events"]
                              if event["source"] == source and event["state"] == state]
                    if events and events[0]["status"] == "screen":
                        break
                    self.assertLess(time.monotonic(), deadline, status)
                    time.sleep(0.02)
                self.assertEqual(status["display"]["state"], state)
                self.assertFalse(events[0]["simulated"])
            row = next(row for row in self.client.call("/api/cli-integrations")["integrations"]
                       if row["source_id"] == source)
            self.assertEqual(row["signals"]["start"]["received"], 1)
            self.assertEqual(row["signals"]["complete"]["received"], 1)
            self.assertEqual(status["verification"][source]["status"], "verified")
            self.assertEqual(status["verification"][source]["complete_tasks"], 1)
            self.assertEqual(status["verification"][source]["delivery"], "screen")


if __name__ == "__main__":
    unittest.main()
