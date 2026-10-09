import json
from aiflame.integrations.codex.registration import EVENTS, quote_command
from aiflame.core.process import command as helper_command
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiflame.integrations.registry import SOURCES
from aiflame.integrations.codex.diagnostics import (
    diagnostics,
    inspect_configuration,
)


class TestDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "带空格的项目 root"
        self.home = Path(self.temp.name) / "codex"
        self.root.mkdir()
        self.home.mkdir()
        self.hooks = {
            "hooks": {
                kind: [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": quote_command(
                                    helper_command("--codex-hook")
                                ),
                            }
                        ]
                    }
                ]
                for kind in EVENTS
            }
        }
        (self.home / "hooks.json").write_text(json.dumps(self.hooks))

    def tearDown(self):
        self.temp.cleanup()

    def test_configuration_does_not_claim_trust(self):
        result = inspect_configuration(self.home)
        self.assertTrue(result["configuration_ready"])
        self.assertEqual(result["hooks_configured"], list(EVENTS))
        self.assertIn("不代表", result["trust_note"])

    def test_missing_hook_detected(self):
        del self.hooks["hooks"]["Interrupt"]
        (self.home / "hooks.json").write_text(json.dumps(self.hooks))
        result = inspect_configuration(self.home)
        self.assertFalse(result["configuration_ready"])
        self.assertTrue(any("Interrupt" in issue for issue in result["issues"]))

    def test_wrong_or_disabled_hook_is_not_ready(self):
        for hook in self.hooks["hooks"]["Stop"][0]["hooks"]:
            hook["enabled"] = False
        self.hooks["hooks"]["Interrupt"][0]["hooks"][0]["command"] = "PRIVATE"
        (self.home / "hooks.json").write_text(json.dumps(self.hooks))
        result = inspect_configuration(self.home)
        self.assertFalse(result["configuration_ready"])
        self.assertNotIn("Stop", result["hooks_configured"])
        self.assertNotIn("PRIVATE", str(result))

    def test_self_tests_excluded_from_real_codex_statistics(self):
        from aiflame.core.engine import Engine
        from aiflame.core.events import make_event

        engine = Engine(self.root / "self-test.sqlite", sources=SOURCES)
        self.addCleanup(engine.db.close)
        event = make_event("codex", "self-test-task", "working", 1)
        engine.db.execute("INSERT INTO self_tests VALUES (?,?)", (event["event_id"], 0))
        engine.db.commit()
        engine.accept(event, "codex")
        data = diagnostics(engine.db, self.home)
        self.assertEqual(data["signals"]["start"]["received"], 0)
        self.assertTrue(engine.snapshot()["events"][0]["simulated"])

    def test_received_is_distinct_from_applied(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE events(source,state,reason,seq,status,created)")
        db.executemany(
            "INSERT INTO events VALUES (?,?,?,?,?,?)",
            [
                ("codex", "working", None, 1, "applied", 10),
                ("codex", "working", None, 2, "queued", 11),
                ("codex", "done", "completed", 2147483646, "applied", 12),
                ("codex", "idle", "cancelled", 2147483647, "suppressed", 13),
                ("manual", "done", None, 1, "applied", 14),
            ],
        )
        result = diagnostics(db, self.home)
        self.assertEqual(result["signals"]["complete"]["received"], 1)
        self.assertEqual(result["signals"]["activity"]["applied"], 0)
        self.assertEqual(result["signals"]["interrupt"]["last_result"], "suppressed")
        self.assertIn("CLI", result["history_note"])


if __name__ == "__main__":
    unittest.main()
