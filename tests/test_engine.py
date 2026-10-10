import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiflame.integrations.registry import SOURCES
from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.integrations.cli_tools.providers import CLI_SOURCES


class TestEngine(unittest.TestCase):
    def test_restart_removes_receipts_for_history_expired_at_startup(self):
        self.accept("done", "old")
        self.assertGreater(self.engine.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)
        self.engine.db.close()
        self.wall += 604801
        self.engine = Engine(Path(self.temp.name) / "db", lambda: self.now, lambda: self.wall, sources=SOURCES)
        self.assertEqual(self.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
        self.assertEqual(self.engine.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 0)

    def test_retention_runs_without_new_events(self):
        self.accept("done", "old")
        self.wall += 604801
        self.engine.snapshot()
        self.assertEqual(
            self.engine.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0
        )
        self.assertEqual(
            self.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0
        )

    def test_retention_cleans_inactive_tasks_and_markers_but_preserves_live_work(self):
        self.accept("working", "old", source="codex")
        response, _ = self.accept("done", "old", seq=2, source="codex")
        self.engine.db.execute(
            "INSERT INTO self_tests VALUES (?,?)", (response["event_id"], self.wall)
        )
        self.engine.db.commit()
        self.wall += 604801
        self.accept("working", "live", source="codex")
        self.engine.cleanup(force=True)
        self.assertEqual(
            [r["task"] for r in self.engine.db.execute("SELECT * FROM tasks")], ["live"]
        )
        self.assertEqual(
            self.engine.db.execute("SELECT COUNT(*) FROM self_tests").fetchone()[0], 0
        )
        response, _ = self.accept("done", "old", seq=3, source="codex")
        self.assertEqual(response["result"], "ignored_unregistered_turn")
        self.accept("done", "live", seq=2, source="codex")

    def test_existing_database_keeps_admission_after_removing_duplicate_table(self):
        self.accept("working", "registered", source="codex")
        self.engine.db.execute("CREATE TABLE codex_submissions(task TEXT PRIMARY KEY)")
        self.engine.db.execute("INSERT INTO codex_submissions VALUES ('registered')")
        self.engine.db.commit()
        self.engine.db.close()
        self.engine = Engine(
            Path(self.temp.name) / "db",
            lambda: self.now,
            lambda: self.wall,
            sources=SOURCES,
        )
        self.assertIsNone(
            self.engine.db.execute(
                "SELECT name FROM sqlite_master WHERE name='codex_submissions'"
            ).fetchone()
        )
        self.accept("done", "registered", seq=2, source="codex")
        self.assertEqual(self.engine.desired()["state"], "done")

    def test_generic_engine_accepts_injected_source_policy(self):
        from aiflame.core.events import SourcePolicy

        engine = Engine(
            ":memory:",
            lambda: self.now,
            lambda: self.wall,
            sources=(
                SourcePolicy("custom", "Custom", "Custom task", requires_start=True),
            ),
        )
        self.addCleanup(engine.db.close)
        with self.assertRaises(EventError):
            engine.accept(self.event("done", source="custom", seq=2), "custom")
        engine.accept(self.event("working", source="custom"), "custom")
        engine.accept(self.event("done", source="custom", seq=2), "custom")
        self.assertEqual(engine.desired()["state"], "done")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1000.0
        self.wall = 1770000000.0
        self.engine = Engine(
            Path(self.temp.name) / "db",
            lambda: self.now,
            lambda: self.wall,
            sources=SOURCES,
        )

    def tearDown(self):
        self.engine.db.close()
        self.temp.cleanup()

    def event(self, state, task="t", seq=1, source="manual", **extra):
        e = make_event(source, task, state, seq)
        e["occurred_at"] = dt.datetime.fromtimestamp(
            self.wall, dt.timezone.utc
        ).isoformat()
        e.update(extra)
        return e

    def accept(self, state, task="t", seq=1, **extra):
        e = self.event(state, task, seq, **extra)
        return self.engine.accept(e, e["source_id"])

    def test_hold_completion_waits_for_new_activity_once(self):
        self.engine.update_settings({"hold_until_activity": True})
        self.accept("done")
        self.assertEqual(self.engine.desired()["duration_ms"], 0)
        self.engine.human_activity(self.now - 1)
        self.now += 300
        self.assertEqual(self.engine.desired()["state"], "done")
        self.engine.human_activity(self.now)
        self.assertEqual(self.engine.desired()["duration_ms"], 10000)
        self.now += 5
        self.engine.human_activity(self.now)
        self.assertEqual(self.engine.desired()["duration_ms"], 5000)
        self.now += 5
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_hardware_hold_ack_does_not_start_countdown(self):
        self.engine.update_settings({"hold_until_activity": True})
        self.engine.device["online"] = True
        self.accept("done")
        self.engine.applied(self.engine.desired(), {"result": "applied"})
        self.now += 300
        self.assertEqual(self.engine.desired()["duration_ms"], 0)
        self.engine.human_activity(self.now)
        self.engine.applied(self.engine.desired(), {"result": "applied"})
        self.now += 11
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_error_cannot_extend_activity_countdown(self):
        self.engine.update_settings({"hold_until_activity": True})
        self.accept("done")
        self.engine.desired()
        self.now += 1
        self.engine.human_activity(self.now)
        self.now += 2
        self.accept("error", task="error")
        self.assertEqual(self.engine.desired()["state"], "error")
        self.now += 11
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_hold_does_not_change_error_duration(self):
        self.engine.update_settings({"hold_until_activity": True})
        self.accept("error")
        self.assertEqual(self.engine.desired()["duration_ms"], 10000)
        self.now += 11
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_duplicate_persists(self):
        e = self.event("done")
        self.engine.accept(e, "manual")
        self.assertTrue(self.engine.accept(e, "manual")[0]["duplicate"])
        self.assertEqual(len(self.engine.queue), 1)
        other = Engine(
            Path(self.temp.name) / "db",
            lambda: self.now,
            lambda: self.wall,
            sources=SOURCES,
        )
        self.assertTrue(other.accept(e, "manual")[0]["duplicate"])
        other.db.close()

    def test_terminal_cannot_revert(self):
        self.accept("done")
        with self.assertRaises(EventError):
            self.accept("working", seq=2)

    def test_cancel_never_done(self):
        self.accept("working")
        self.accept("idle", seq=2, reason="cancelled")
        self.assertEqual(self.engine.desired()["state"], "idle")
        with self.assertRaises(EventError):
            self.accept("done", seq=3)

    def test_lease_expires_without_success(self):
        self.accept("working")
        self.now += 121
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.assertEqual(
            self.engine.db.execute("SELECT state FROM tasks").fetchone()[0], "unknown"
        )

    def test_native_hooks_wait_for_explicit_terminal_event(self):
        for source in ("codex", *CLI_SOURCES):
            enabled = {**self.engine.settings["source_enabled"], source: True}
            self.engine.update_settings({"source_enabled": enabled})
            for terminal, reason in (("done", "completed"), ("error", "failed"), ("idle", "cancelled")):
                with self.subTest(source=source, terminal=terminal):
                    task = source + terminal
                    self.accept("working", task, source=source)
                    for interval in (121, 1800):
                        self.now += interval
                        self.assertEqual(self.engine.desired()["state"], "working")
                    self.accept(terminal, task, seq=2, source=source, reason=reason)
                    self.assertNotIn((source, task), self.engine.active)
                    self.assertEqual(self.engine.desired()["state"], terminal)
                    self.now += 11
                    self.assertEqual(self.engine.desired()["state"], "idle")

    def test_web_source_still_expires_without_heartbeat(self):
        self.accept("working", source="chatgpt-web")
        self.now += 121
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_done_returns_to_other_work(self):
        self.accept("working", "a")
        self.accept("done", "b")
        desired = self.engine.desired()
        self.engine.applied(desired, {"result": "applied"})
        self.now += 11
        self.assertEqual(self.engine.desired()["state"], "working")

    def test_priority_and_limit(self):
        for i in range(22):
            self.accept("done", str(i))
        self.accept("error", "failed")
        self.assertLessEqual(len(self.engine.queue), 20)
        self.assertEqual(self.engine.desired()["state"], "error")
        self.assertEqual(self.engine.dropped, 3)

    def test_silent_no_replay(self):
        self.engine.update_settings({"paused": True})
        self.accept("done")
        self.engine.update_settings({"paused": False})
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_expired_and_future(self):
        for offset in (-61, 31):
            e = self.event("done")
            e["occurred_at"] = dt.datetime.fromtimestamp(
                self.wall + offset, dt.timezone.utc
            ).isoformat()
            with self.assertRaises(EventError):
                self.engine.accept(e, "manual")

    def test_no_cross_source(self):
        with self.assertRaises(EventError):
            self.engine.accept(self.event("done"), "local-script")

    def test_pending_expiry(self):
        self.accept("done")
        self.engine.desired()
        self.now += 61
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_restart_does_not_replay(self):
        self.accept("done")
        d = self.engine.desired()
        self.engine.applied(d, {})
        self.engine.session_changed()
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_saved_settings_persist_across_restart(self):
        settings = dict(self.engine.settings)
        settings["brightness"] = 7
        settings["source_enabled"] = {**settings["source_enabled"], "codex": False}
        self.engine.update_settings(settings)
        self.engine.db.close()
        self.engine = Engine(Path(self.temp.name) / "db", sources=SOURCES)
        self.assertEqual(self.engine.settings, settings)


if __name__ == "__main__":
    unittest.main()
