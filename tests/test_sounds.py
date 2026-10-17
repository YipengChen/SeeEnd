"""Sound lifecycle behavior without opening an audio device."""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, SourcePolicy, make_event
from aiflame.core.paths import load_config
from aiflame.hub.server import Hub
from tests.live_client import DashboardClient


class SoundOutputTests(unittest.TestCase):
    def setUp(self):
        self.now, self.wall = 1000, 1770000000
        self.sources = [SourcePolicy(name, name, name) for name in ("test", "manual", "demo")]
        self.engine = self.create_engine(":memory:")
        self.addCleanup(lambda: self.engine.db.close())

    def create_engine(self, path):
        return Engine(path, lambda: self.now, lambda: self.wall, sources=self.sources)

    def event(self, state="done", seq=1, task="task", source="test"):
        event = make_event(source, task, state, seq)
        event["occurred_at"] = dt.datetime.fromtimestamp(self.wall, dt.timezone.utc).isoformat()
        return event

    def send(self, state="done", seq=1, task="task", source="test"):
        return self.engine.accept(self.event(state, seq, task, source), source)[0]

    def enable(self, **patch):
        self.engine.update_settings({"sound_notifications": True, **patch})

    def test_defaults_and_invalid_settings(self):
        self.assertFalse(self.engine.settings["sound_notifications"])
        self.assertEqual(self.engine.settings["sound_category"], "chime")
        self.assertEqual(self.send()["deliveries"]["sound"], "disabled")
        self.assertEqual(self.engine.sound.claim(), [])
        for patch in ({"sound_notifications": 1}, {"sound_category": "missing"}, {"sound_category": []}):
            with self.assertRaises(EventError):
                self.engine.update_settings(patch)

    def test_real_input_and_done_are_independent_of_lamp_and_desktop(self):
        self.enable(brightness=0, desktop_notifications=False, sound_category="cat")
        self.send("working")
        self.assertEqual(self.engine.sound.claim(), [])
        for state, seq in (("needs_input", 2), ("done", 3)):
            result = self.send(state, seq)
            self.assertEqual(result["deliveries"]["sound"], "pending")
            message, = self.engine.sound.claim()
            self.assertEqual(message["category"], "cat")
            self.assertGreater(message["expires"], self.wall)
            self.assertEqual(self.engine.sound.claim(), [])
            receipt = self.engine.sound.acknowledge({**{key: message[key] for key in ("event_id", "claim")}, "status": "submitted"})
            self.assertEqual(receipt, {"status": "submitted"})

    def test_duplicates_and_invalid_claims_never_replay(self):
        self.enable()
        event = self.event()
        self.engine.accept(event, "test")
        self.assertTrue(self.engine.accept(event, "test")[0]["duplicate"])
        message, = self.engine.sound.claim()
        with self.assertRaises(EventError):
            self.engine.sound.acknowledge({"event_id": message["event_id"], "claim": "wrong", "status": "submitted"})
        self.assertEqual(self.engine.sound.claim(), [])
        for body in ({}, [], {"event_id": "e", "claim": "c", "status": "played"}):
            with self.assertRaises(EventError):
                self.engine.sound.acknowledge(body)

    def test_simulated_and_non_prompt_events_are_silent(self):
        self.enable()
        for source in ("manual", "demo"):
            self.assertEqual(self.send(source=source)["deliveries"]["sound"], "not_requested")
        event = self.event(task="self-test")
        self.engine.db.execute("INSERT INTO self_tests VALUES (?,?)", (event["event_id"], self.wall))
        self.assertEqual(self.engine.accept(event, "test")[0]["deliveries"]["sound"], "not_requested")
        for state in ("working", "idle", "unknown", "cancelled", "timeout"):
            self.assertEqual(self.send(state, task=state)["deliveries"]["sound"], "not_requested")
        self.assertEqual(self.engine.sound.claim(), [])

    def test_light_preview_does_not_request_sound(self):
        self.enable(hardware_notifications=True)
        self.engine.device["online"] = True
        self.engine.start_preview("sound-test")
        self.engine.desired()
        self.assertEqual(self.engine.sound.claim(), [])

    def test_new_task_state_supersedes_unplayed_input(self):
        self.enable()
        previous = self.send("needs_input")
        self.send("working", 2)
        self.assertEqual(self.engine.sound.claim(), [])
        receipt = self.engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output='sound'", (previous["event_id"],)).fetchone()
        self.assertEqual(receipt["status"], "superseded")

    def test_claim_returns_one_sound_at_a_time(self):
        self.enable()
        self.send(task="first")
        self.send(task="second")
        first, = self.engine.sound.claim()
        second, = self.engine.sound.claim()
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(self.engine.sound.claim(), [])

    def test_pending_sound_expires_after_fifteen_seconds_even_with_long_event_ttl(self):
        self.enable()
        event = self.event()
        event["ttl_ms"] = 300000
        self.engine.accept(event, "test")
        self.wall += 15
        self.assertEqual(self.engine.sound.claim(), [])
        row = self.engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output='sound'", (event["event_id"],)).fetchone()
        self.assertEqual(row["status"], "expired")

    def test_disabled_setting_or_source_clears_pending_audio(self):
        for patch in ({"sound_notifications": False}, {"source_enabled": dict.fromkeys(("test", "manual", "demo"), False)}):
            self.enable(source_enabled=dict.fromkeys(("test", "manual", "demo"), True))
            self.send(task=str(patch))
            self.engine.update_settings(patch)
            self.assertEqual(self.engine.sound.claim(), [])
        self.assertEqual(self.send(task="disabled")["deliveries"]["sound"], "unmatched")

    def test_expired_audio_cannot_be_claimed_or_acknowledged(self):
        self.enable()
        self.send()
        self.wall += 61
        self.assertEqual(self.engine.sound.claim(), [])
        self.send(task="claimed")
        message, = self.engine.sound.claim()
        self.wall += 61
        with self.assertRaises(EventError):
            self.engine.sound.acknowledge({"event_id": message["event_id"], "claim": message["claim"], "status": "submitted"})
        event = self.event(task="expired")
        self.assertEqual(self.engine.sound.accept({**event, "expires": self.now}), "expired")

    def test_preferences_persist_but_pending_audio_does_not_replay_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            self.engine.db.close()
            path = Path(folder) / "events.sqlite"
            self.engine = self.create_engine(path)
            self.enable(sound_category="pop")
            result = self.send()
            self.engine.db.close()
            self.engine = self.create_engine(path)
            self.assertTrue(self.engine.settings["sound_notifications"])
            self.assertEqual(self.engine.settings["sound_category"], "pop")
            self.assertEqual(self.engine.sound.claim(), [])
            row = self.engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output='sound'", (result["event_id"],)).fetchone()
            self.assertEqual(row["status"], "interrupted")


class SoundHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"AIFLAME_DATA_DIR": self.temp.name})
        self.env.start()
        self.hub = Hub(port=0, config=load_config())
        try:
            self.hub.start(hardware=False)
        except PermissionError as error:
            self.hub.engine.db.close()
            self.env.stop()
            self.temp.cleanup()
            self.skipTest(f"Loopback socket binding is blocked by this environment: {error}")
        self.client = DashboardClient(f"http://127.0.0.1:{self.hub.port}")

    def tearDown(self):
        self.hub.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_claim_ack_require_auth_and_preserve_other_output_receipts(self):
        self.assertEqual(self.client.request("/api/sounds/claim", {}, authenticated=False)[0], 401)
        self.client.call("/api/settings", {"sound_notifications": True, "sound_category": "cat", "brightness": 0})
        with self.hub.lock:
            self.hub.engine.accept(make_event("local-script", "sound-http", "done"), "local-script")
        message, = self.client.call("/api/sounds/claim", {})["sounds"]
        self.assertEqual(message["category"], "cat")
        self.assertEqual(self.client.call("/api/sounds/claim", {})["sounds"], [])
        receipt = {"event_id": message["event_id"], "claim": message["claim"], "status": "submitted"}
        self.assertEqual(self.client.request("/api/sounds/ack", receipt, authenticated=False)[0], 401)
        self.assertEqual(self.client.call("/api/sounds/ack", receipt), {"status": "submitted"})
        self.assertEqual(self.client.request("/api/sounds/ack", receipt)[0], 409)
        receipts = {row["output"]: row["status"] for row in self.client.call("/api/status")["deliveries"]}
        self.assertEqual(receipts["sound"], "submitted")
        self.assertEqual(receipts["flame"], "suppressed")
