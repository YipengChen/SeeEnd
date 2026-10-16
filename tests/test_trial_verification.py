"""Real task evidence and screen delivery without a connected lamp."""

import datetime as dt
from pathlib import Path
import tempfile
import unittest

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.core.verification import begin_verification
from aiflame.integrations.registry import SOURCES


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now, self.wall = 1000.0, 1770000000.0
        self.open_engine()

    def open_engine(self):
        self.engine = Engine(
            Path(self.temp.name) / "events.sqlite",
            lambda: self.now,
            lambda: self.wall,
            sources=SOURCES,
        )

    def tearDown(self):
        self.engine.db.close()
        self.temp.cleanup()

    def advance(self, seconds):
        self.now += seconds
        self.wall += seconds

    def send(
        self, state, task="trial", seq=1, source="codex", simulated=False, **extra
    ):
        event = make_event(source, task, state, seq)
        event["occurred_at"] = dt.datetime.fromtimestamp(
            self.wall, dt.timezone.utc
        ).isoformat()
        event.update(extra)
        if simulated:
            self.engine.db.execute(
                "INSERT INTO self_tests VALUES (?,?)", (event["event_id"], self.wall)
            )
        self.engine.accept(event, source)
        return event["event_id"]

    def summary(self, source="codex"):
        return self.engine.snapshot()["verification"][source]

    def test_offline_completion_uses_duration_and_never_fakes_device_ack(self):
        self.engine.update_settings({"hold_until_activity": False})
        self.send("working")
        self.send("working", task="other")
        self.assertEqual(self.engine.snapshot()["display"]["state"], "working")
        self.send("done", seq=2, ttl_ms=1000)
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["display"]["state"], "done")
        self.assertEqual(snapshot["events"][0]["status"], "screen")
        self.assertIsNone(snapshot["last_ack"])
        self.advance(2)
        self.assertEqual(self.engine.desired()["duration_ms"], 8000)
        self.advance(9)
        self.assertEqual(self.engine.desired()["state"], "working")

    def test_device_connection_does_not_replay_offline_prompts(self):
        self.engine.update_settings({"hold_until_activity": False})
        self.send("done", source="local-script")
        self.engine.snapshot()
        self.send("done", task="queued", source="local-script")
        self.engine.device["online"] = True
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.send("done", task="new", source="local-script")
        desired = self.engine.desired()
        self.assertEqual(desired["state"], "done")
        self.assertIsNone(self.engine.current["deadline"])
        self.engine.applied(desired, {"result": "applied"})
        self.assertEqual(self.engine.snapshot()["events"][0]["status"], "applied")
        self.advance(11)
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_manual_demo_and_adapter_diagnostics_never_verify(self):
        for source in ("manual", "demo", "codex"):
            for seq, state in ((1, "working"), (2, "done")):
                self.send(state, source=source, seq=seq, simulated=source == "codex")
        snapshot = self.engine.snapshot()
        self.assertTrue(all(e["simulated"] for e in snapshot["events"]))
        self.assertEqual(self.summary()["status"], "waiting")
        self.assertNotIn("manual", snapshot["verification"])

    def test_same_task_pair_required_and_errors_cancels_do_not_pass(self):
        self.send("working", task="a", source="local-script")
        self.send("done", task="b", seq=2, source="local-script")
        self.assertEqual(self.summary("local-script")["status"], "incomplete")
        self.send("working")
        self.send("unknown", seq=2)
        self.assertEqual(self.summary()["status"], "unknown")
        self.send("working", task="cancel")
        self.send("done", task="cancel", seq=2)
        self.assertEqual(self.summary()["status"], "verified")
        self.send("idle", task="cancel", seq=3, reason="cancelled")
        self.assertEqual(self.summary()["status"], "cancelled")
        self.assertEqual(self.summary()["complete_tasks"], 0)

    def test_evidence_persists_beyond_recent_fifty_and_across_restart(self):
        self.send("working")
        self.send("done", seq=2)
        for i in range(55):
            self.send("idle", task=str(i), source="manual")
        self.assertTrue(
            all(e["source"] == "manual" for e in self.engine.snapshot()["events"])
        )
        self.engine.db.close()
        self.open_engine()
        self.assertEqual(self.summary()["status"], "verified")
        self.assertEqual(self.summary()["complete_tasks"], 1)

    def test_fresh_window_keeps_history_but_requires_new_start(self):
        self.send("working")
        self.advance(1)
        begin_verification(self.engine, "codex")
        self.send("done", seq=2)
        self.assertEqual(self.summary()["status"], "incomplete")
        self.assertEqual(len(self.engine.snapshot()["events"]), 2)
        self.engine.db.close()
        self.open_engine()
        self.assertEqual(self.summary()["since"], self.wall)
        self.send("working", task="new")
        self.send("done", task="new", seq=2)
        self.assertEqual(self.summary()["status"], "verified")

    def test_muted_real_evidence_separates_detection_and_delivery(self):
        self.engine.stop()
        self.send("working")
        self.send("done", seq=2)
        summary = self.summary()
        self.assertEqual(summary["status"], "verified")
        self.assertEqual(summary["delivery"], "suppressed")
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_restart_stop_and_source_disable_never_leave_phantom_work(self):
        for action in ("restart", "stop", "disable"):
            with self.subTest(action=action):
                self.engine.emergency = False
                self.send("working", task=action)
                self.engine.snapshot()
                if action == "restart":
                    self.engine.db.close()
                    self.open_engine()
                elif action == "stop":
                    self.engine.stop()
                else:
                    enabled = dict(self.engine.settings["source_enabled"])
                    self.engine.update_settings(
                        {"source_enabled": {**enabled, "codex": False}}
                    )
                    self.engine.update_settings({"source_enabled": enabled})
                self.assertEqual(self.summary()["status"], "timeout")

    def test_disabled_source_and_invalid_reset_cannot_qualify(self):
        enabled = dict(self.engine.settings["source_enabled"])
        self.engine.update_settings({"source_enabled": {**enabled, "codex": False}})
        self.send("working")
        self.send("done", seq=2)
        self.engine.update_settings({"source_enabled": enabled})
        self.assertEqual(self.summary()["complete_tasks"], 0)
        for source in ("manual", "demo", "missing", [], None):
            with self.assertRaises(EventError):
                begin_verification(self.engine, source)

    def test_disabling_source_cancels_already_queued_prompt(self):
        self.send("working")
        self.send("done", seq=2)
        self.engine.snapshot()
        enabled = {**self.engine.settings["source_enabled"], "codex": False}
        self.engine.update_settings({"source_enabled": enabled})
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_previous_same_state_ack_does_not_confirm_new_prompt(self):
        self.engine.update_settings({"hold_until_activity": False})
        self.engine.device["online"] = True
        self.send("done", source="local-script")
        self.engine.applied(
            self.engine.desired(), {"state": "done", "result": "applied"}
        )
        self.assertTrue(self.engine.snapshot()["display"]["device_confirmed"])
        self.advance(11)
        self.engine.desired()
        self.send("done", task="new", source="local-script")
        self.assertFalse(self.engine.snapshot()["display"]["device_confirmed"])
        self.engine.applied(
            self.engine.desired(), {"state": "done", "result": "applied"}
        )
        self.assertTrue(self.engine.snapshot()["display"]["device_confirmed"])


if __name__ == "__main__":
    unittest.main()
