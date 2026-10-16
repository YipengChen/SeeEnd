import datetime as dt
import json
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
    def test_upgrade_removes_saved_pause_and_quiet_hours_without_changing_other_preferences(self):
        self.engine.update_settings({"duration": 17, "desktop_notifications": True})
        expected = dict(self.engine.settings)
        previous = {**expected, "paused": True, "quiet_enabled": True,
                    "quiet_start": "00:00", "quiet_end": "00:00"}
        self.engine.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", ("main", json.dumps(previous)))
        self.engine.db.commit()
        self.engine.db.close()
        self.engine = Engine(Path(self.temp.name) / "db", lambda: self.now, lambda: self.wall, sources=SOURCES)
        self.assertEqual(self.engine.settings, expected)
        saved = json.loads(self.engine.db.execute("SELECT value FROM settings WHERE key='main'").fetchone()["value"])
        self.assertEqual(saved, expected)
        result, _ = self.accept("done", "after-upgrade")
        self.assertNotEqual(result["deliveries"]["flame"], "suppressed")
        self.assertEqual(result["deliveries"]["desktop"], "pending")
        self.assertEqual(self.engine.desired()["state"], "done")

    def test_removed_pause_and_quiet_settings_are_rejected(self):
        for key, value in (("paused", True), ("quiet_enabled", True),
                           ("quiet_start", "22:00"), ("quiet_end", "08:00")):
            with self.subTest(key=key), self.assertRaises(EventError):
                self.engine.update_settings({key: value})

    def test_unfinished_count_includes_tasks_outside_recent_list_and_ignores_disabled_sources(self):
        self.accept("needs_input", "older")
        for index in range(31):
            self.wall += 1
            self.accept("done", f"recent-{index}")
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["active_count"], 0)
        self.assertNotIn("older", {task["task"] for task in snapshot["tasks"]})
        self.assertEqual(snapshot["unfinished_count"], 1)
        self.engine.update_settings({"source_enabled": {**self.engine.settings["source_enabled"], "manual": False}})
        self.assertEqual(self.engine.snapshot()["unfinished_count"], 0)
        self.engine.update_settings({"source_enabled": {**self.engine.settings["source_enabled"], "manual": True}})
        self.accept("cancelled", "older", seq=2)
        self.assertEqual(self.engine.snapshot()["unfinished_count"], 0)

    def test_task_history_keeps_fifty_tasks_with_all_retained_events(self):
        for index in range(51):
            self.wall += 1
            self.accept("working", f"task-{index}")
            self.accept("needs_input" if index == 0 else "done", f"task-{index}", seq=2)
        snapshot = self.engine.snapshot()
        self.assertEqual(len(snapshot["events"]), 50)
        history = snapshot["task_events"]
        self.assertEqual(len(history), 100)
        self.assertEqual({r["event"] for r in snapshot["deliveries"]}, {e["id"] for e in history})
        self.assertEqual({e["task"] for e in history}, {f"task-{i}" for i in range(1, 51)})
        self.assertEqual([e["state"] for e in history[:2]], ["done", "working"])
        # An older task becomes recent again; all its earlier events return.
        self.wall += 1
        self.accept("working", "task-0", seq=3)
        history = self.engine.snapshot()["task_events"]
        self.assertEqual(history[0]["task"], "task-0")
        self.assertEqual(len([e for e in history if e["task"] == "task-0"]), 3)
        self.assertNotIn("task-1", {e["task"] for e in history})

    def test_large_snapshot_receipts_are_linear_and_origins_refresh_next_poll(self):
        from unittest.mock import patch
        from aiflame.core.delivery import delivery_summary
        for task in range(5):
            for seq in range(1, 21):
                self.accept('working' if seq < 20 else 'done', f'busy-{task}', seq=seq, source='codex')
        visits = []
        def summarize(flame, event, receipts):
            visits.append(len(receipts))
            return delivery_summary(flame, event, receipts)
        with patch('aiflame.core.engine.delivery_summary', side_effect=summarize), \
             patch.object(self.engine.origins, 'summary', return_value={'available': True}) as origins:
            first = self.engine.snapshot()
            self.assertLessEqual(origins.call_count, 11)
            self.assertLessEqual(sum(visits), 2 * len(first['deliveries']))
            self.assertEqual(len(first['task_events']), 100)
            for event in first['events'] + first['task_events']:
                self.assertEqual(event['delivery_summary'], delivery_summary(self.engine.flame, event, first['deliveries']))
                self.assertTrue(event['return_to']['available'])
            origins.reset_mock()
            origins.return_value = {'available': False}
            second = self.engine.snapshot()
            self.assertLessEqual(origins.call_count, 11)
            self.assertTrue(all(not event['return_to']['available'] for event in second['task_events']))

    def test_live_snapshot_keeps_current_state_without_building_history(self):
        from unittest.mock import patch
        self.accept('working', 'active')
        with patch('aiflame.core.engine.delivery_summary', side_effect=AssertionError('history scanned')):
            live = self.engine.snapshot(include_history=False)
        self.assertEqual(live['active_count'], 1)
        self.assertEqual(live['unfinished_count'], 1)
        for field in ('events', 'task_events', 'deliveries'):
            self.assertNotIn(field, live)
        full = self.engine.snapshot()
        self.assertTrue(full['events'])
        self.assertTrue(full['task_events'])

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
        self.assertTrue(self.engine.settings["hold_until_activity"])
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
        self.accept("needs_input", task="needs_input")
        self.assertEqual(self.engine.desired()["state"], "needs_input")
        self.now += 11
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_hold_does_not_change_error_duration(self):
        self.engine.update_settings({"hold_until_activity": True})
        self.accept("needs_input")
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
        self.engine.update_settings({"hold_until_activity": False})
        for source in ("codex", *CLI_SOURCES):
            enabled = {**self.engine.settings["source_enabled"], source: True}
            self.engine.update_settings({"source_enabled": enabled})
            for terminal, reason in (("done", "completed"), ("needs_input", "failed"), ("idle", "cancelled")):
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
        self.engine.update_settings({"hold_until_activity": False})
        self.accept("working", "a")
        self.accept("done", "b")
        desired = self.engine.desired()
        self.engine.applied(desired, {"result": "applied"})
        self.now += 11
        self.assertEqual(self.engine.desired()["state"], "working")

    def test_priority_and_limit(self):
        for i in range(22):
            self.accept("done", str(i))
        self.accept("needs_input", "failed")
        self.assertLessEqual(len(self.engine.queue), 20)
        self.assertEqual(self.engine.desired()["state"], "needs_input")
        self.assertEqual(self.engine.dropped, 3)

    def test_zero_brightness_does_not_replay_suppressed_alerts(self):
        self.engine.update_settings({"brightness": 0})
        self.accept("done")
        self.engine.update_settings({"brightness": 20})
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
        self.engine.update_settings({"hold_until_activity": False})
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
        settings["desktop_flame_enabled"] = False
        settings["source_enabled"] = {**settings["source_enabled"], "codex": False, "claude-code": False}
        self.engine.update_settings(settings)
        self.engine.db.close()
        self.engine = Engine(Path(self.temp.name) / "db", sources=SOURCES)
        self.assertEqual(self.engine.settings, settings)

    def test_missing_coding_sources_default_to_enabled_on_restart(self):
        import json
        self.assertTrue(all(self.engine.settings["source_enabled"][source]
                            for source in ("codex", *CLI_SOURCES)))
        self.engine.db.execute("INSERT INTO settings VALUES ('main', ?)", (
            json.dumps({"source_enabled": {"codex": False}}),
        ))
        self.engine.db.commit()
        self.engine.db.close()
        self.engine = Engine(Path(self.temp.name) / "db", sources=SOURCES)
        self.assertFalse(self.engine.settings["source_enabled"]["codex"])
        self.assertTrue(all(self.engine.settings["source_enabled"][source]
                            for source in CLI_SOURCES))

    def test_desktop_flame_rejects_non_boolean_settings_without_changing_preferences(self):
        self.assertTrue(self.engine.settings["desktop_flame_enabled"])
        for value in (0, 1, "false", None, [], {}):
            with self.assertRaises(EventError):
                self.engine.update_settings({"desktop_flame_enabled": value})
            self.assertTrue(self.engine.settings["desktop_flame_enabled"])


if __name__ == "__main__":
    unittest.main()
