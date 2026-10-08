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
from aiflame.transports.base import DeviceTransport
from aiflame.transports.wifi import SocketLine


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

    def transport(self):
        worker = DeviceTransport(self.engine, threading.RLock(), "hub", "lamp")
        worker.session = "session"
        worker.connection = Mock()
        worker.stopping = Mock(wait=Mock(return_value=False), is_set=Mock(return_value=False))
        return worker

    def test_matrix_receives_working_brightness_without_legacy_cap(self):
        for brightness in (0, 1, 10, 20, 25):
            for reduced in (False, True):
                with self.subTest(brightness=brightness, reduced=reduced):
                    worker = self.transport()
                    worker.receive = Mock(return_value={"result": "applied"})
                    worker.display(
                        {"key": "working", "state": "working", "duration_ms": 0},
                        {"brightness": brightness, "reduced": reduced},
                    )
                    command = json.loads(worker.connection.write.call_args.args[0])
                    self.assertEqual(command["brightness"], brightness)
                    self.assertEqual(command["effect"], "reduced" if reduced else "standard")


    def test_failed_frame_ack_does_not_mark_display_applied(self):
        self.engine.start_preview("first")
        desired = self.engine.desired()
        worker = self.transport()
        worker.receive = Mock(return_value={"result": "error", "error": "matrix-i2c"})
        with self.assertRaisesRegex(ValueError, "灯板未响应"):
            worker.display(desired, self.engine.settings)
        self.assertFalse(self.engine.snapshot()["display"]["device_confirmed"])
        self.assertIsNone(self.engine.last_command)

    def test_device_uses_remaining_phase_duration_and_short_fail_safe(self):
        self.engine.start_preview("first")
        desired = self.engine.desired()
        self.now += 0.25
        worker = self.transport()
        worker.receive = Mock(return_value={"result": "applied"})
        worker.display(desired, self.engine.settings)
        command = json.loads(worker.connection.write.call_args.args[0])
        self.assertEqual(command["state"], self.engine.snapshot()["display"]["state"])
        self.assertEqual(command["duration_ms"], 4750)
        self.assertEqual(command["lease_ms"], 6750)

    def test_retry_drops_old_preview_phase_instead_of_resending(self):
        self.engine.start_preview("first")
        worker = self.transport()

        def timeout(*_, **__):
            self.now += 5.1
            raise TimeoutError()

        worker.receive = Mock(side_effect=timeout)
        self.assertIsNone(worker.display(self.engine.desired(), self.engine.settings))
        self.assertEqual(worker.connection.write.call_count, 1)
        self.assertEqual(self.engine.desired()["state"], "done")

    def test_waiting_for_ack_exits_when_real_task_arrives(self):
        self.engine.start_preview("first")
        worker = self.transport()

        def incoming(*_):
            self.engine.accept(make_event("local-script", "real-task", "working"), "local-script")
            return b""

        worker.connection.read_until.side_effect = incoming
        self.assertIsNone(worker.display(self.engine.desired(), self.engine.settings))
        self.assertEqual(worker.connection.read_until.call_count, 1)
        self.assertEqual(worker.connection.write.call_count, 1)

    def test_blocked_wifi_ack_does_not_delay_preview_cancellation(self):
        self.engine.start_preview("first")
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        left.settimeout(2)
        right.settimeout(1)
        worker = self.transport()
        reading = threading.Event()

        class Stream(SocketLine):
            def read_until(self, mark, limit):
                reading.set()
                return super().read_until(mark, limit)

        worker.connection = Stream(left)
        desired = self.engine.desired()
        result = []
        thread = threading.Thread(target=lambda: result.append(worker.display(desired, self.engine.settings)))
        thread.start()
        self.addCleanup(thread.join, 3)
        self.assertIn(b'"display"', right.recv(4096))
        self.assertTrue(reading.wait(1))
        started = time.monotonic()
        with worker.lock:
            self.engine.cancel_preview()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(result, [None])
        self.assertEqual(left.gettimeout(), 2)


if __name__ == "__main__":
    unittest.main()
