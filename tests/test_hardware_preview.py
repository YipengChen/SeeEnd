"""One transient preview drives both screen status and device commands."""

import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.integrations.registry import SOURCES


class HardwarePreviewTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.engine = Engine(":memory:", clock=lambda: self.now, sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        self.engine.update_settings({"mode": "hardware"})
        self.engine.device.update(online=True, session_id="session", connected_at=1)

    def test_shared_ten_second_schedule_does_not_create_tasks_or_settings(self):
        settings = dict(self.engine.settings)
        self.engine.start_preview("first")
        for elapsed, state in ((0, "working"), (4.99, "working"), (5, "done"), (9.99, "done")):
            self.now = 1000 + elapsed
            desired = self.engine.desired()
            display = self.engine.snapshot()["display"]
            self.assertEqual(desired["state"], state)
            self.assertEqual(desired["key"], display["key"])
            self.assertFalse(display["device_confirmed"])
            self.engine.applied(desired, {"result": "applied"})
            self.assertTrue(self.engine.snapshot()["display"]["device_confirmed"])
            # Let the next phase independently prove its acknowledgement.
            self.engine.last_display_key = None
        self.now = 1010
        result = self.engine.snapshot()
        self.assertEqual(result["display"]["state"], "idle")
        self.assertIsNone(self.engine.preview)
        self.assertEqual(result["settings"], settings)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["tasks"], [])

    def test_real_event_preempts_and_late_preview_ack_cannot_mark_it_applied(self):
        self.engine.start_preview("first")
        old = self.engine.desired()
        event = make_event("local-script", "real-task", "working")
        self.engine.accept(event, "local-script")
        self.engine.applied(old, {"result": "applied"})
        self.assertIsNone(self.engine.preview)
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["display"]["key"], "working")
        self.assertFalse(snapshot["display"]["device_confirmed"])
        self.assertEqual(snapshot["events"][0]["status"], "queued")
        self.assertIsNone(self.engine.last_command)

    def test_disabled_source_does_not_interrupt_preview(self):
        self.engine.settings["source_enabled"]["local-script"] = False
        self.engine.start_preview("first")
        self.engine.accept(make_event("local-script", "ignored", "working"), "local-script")
        self.assertEqual(self.engine.desired()["preview"], "first")

    def test_start_requires_idle_connected_unmuted_hardware(self):
        for setting in ({"mode": "virtual"}, {"paused": True}, {"brightness": 0},
                        {"quiet_enabled": True, "quiet_start": "12:00", "quiet_end": "12:00"}):
            before = dict(self.engine.settings)
            self.engine.update_settings(setting)
            with self.assertRaises(EventError):
                self.engine.start_preview("first")
            self.engine.update_settings(before)
        self.engine.device["online"] = False
        with self.assertRaises(EventError):
            self.engine.start_preview("first")
        self.engine.device["online"] = True
        self.engine.accept(make_event("local-script", "real-task", "done"), "local-script")
        with self.assertRaises(EventError):
            self.engine.start_preview("first")
        self.assertEqual(self.engine.desired()["state"], "done")

    def test_cancellation_is_scoped_to_preview_id(self):
        self.engine.start_preview("first")
        self.engine.cancel_preview("first")
        self.engine.start_preview("second")
        self.engine.cancel_preview("first")
        self.assertEqual(self.engine.desired()["preview"], "second")
        self.engine.stop()
        self.assertIsNone(self.engine.preview)

    def test_settings_cancel_preview_without_replay(self):
        for patch in ({"mode": "virtual"}, {"paused": True}, {"brightness": 0}):
            before = dict(self.engine.settings)
            self.engine.start_preview("first")
            self.engine.update_settings(patch)
            self.assertIsNone(self.engine.preview)
            self.engine.update_settings(before)
            self.assertEqual(self.engine.desired()["state"], "idle")

    def test_disconnect_and_fast_reconnect_cancel_preview(self):
        self.engine.start_preview("first")
        self.engine.device["online"] = False
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.engine.device["online"] = True
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.engine.start_preview("second")
        self.engine.device["connected_at"] = 2
        self.assertEqual(self.engine.desired()["state"], "idle")










if __name__ == "__main__":
    unittest.main()
