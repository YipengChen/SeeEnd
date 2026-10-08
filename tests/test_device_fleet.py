"""Parallel transports, receipt isolation and maintenance of one lamp."""
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.core.events import make_event, EventError
from aiflame.devices.registry import migrate_devices
from aiflame.devices.fleet import FleetDeviceService
from aiflame.integrations.registry import SOURCES
from aiflame.transports.base import DeviceTransport
from aiflame.transports.fleet import FleetTransportManager


class FakeLamp(DeviceTransport):
    def __init__(self, engine, lock, hub_id, expected_device, *args):
        super().__init__(engine, lock, hub_id, expected_device)
        self.frames = []
        self.appearances = []
        self.fail = False

    def connect(self):
        self.connection = Mock()
        self.session = "session-" + self.expected_device
        self.engine.device = {"online": True, "session_id": self.session,
                              "connected_at": time.monotonic(), "port": "usb-" + self.expected_device,
                              "status": "已连接", "last_error": None, "device_id": self.expected_device}

    def display(self, desired, settings):
        if self.fail:
            raise OSError("fixture unplugged")
        self.frames.append(dict(desired))
        self.appearances.append(dict(settings))
        return {"command_id": self.expected_device + ":" + str(len(self.frames)), "result": "applied"}

    def receive(self, *args, **kwargs):
        return {"type": "pong", "session_id": self.session, "state": self.engine.desired()["state"]}


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=SOURCES)
        self.engine.update_settings({"mode": "hardware"})
        self.lock = threading.RLock()
        self.config = {"hub_id": "fixture", "devices": {
            "one": {"device_id": "one", "port": "COM1", "hardware_model": "ai-flame-v01"},
            "two": {"device_id": "two", "port": "COM2", "hardware_model": "ai-flame-v02"}}}
        self.fleet = FleetTransportManager(self.engine.flame, self.lock, self.config)
        self.engine.flame.transport = self.fleet
        self.factory = patch("aiflame.transports.manager.USBTransport", FakeLamp)
        self.factory.start()
        self.fleet.start()
        self.wait(lambda: len(self.fleet.sessions()) == 2)
        self.service = FleetDeviceService(self.config, self.temp.name, self.fleet, self.lock)

    def tearDown(self):
        if self.service.thread:
            self.service.thread.join(3)
        self.fleet.stop()
        self.factory.stop()
        self.engine.db.close()
        self.temp.cleanup()

    def wait(self, predicate, seconds=3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            with self.lock:
                if predicate():
                    return
            time.sleep(.01)
        self.fail("Timed out waiting for independent device state")

    def emit(self, state, task="task", seq=1):
        with self.lock:
            event = make_event("manual", task, state, seq)
            self.engine.accept(event, "manual")
        return event["event_id"]

    def light_settings(self, device_id, **settings):
        return self.service.update_light_settings({"device_id": device_id,
            "hardware_model": self.config["devices"][device_id]["hardware_model"], "settings": settings})

    def test_appearance_is_per_instance_persistent_and_applied_without_reconnecting(self):
        workers = {key: manager.worker for key, manager in self.fleet.connections.items()}
        self.light_settings("one", brightness=8, reduced=True)
        self.wait(lambda: workers["one"].appearances[-1]["brightness"] == 8)
        self.assertTrue(workers["one"].appearances[-1]["reduced"])
        self.assertEqual(self.fleet.outputs["two"].settings["brightness"], 20)
        self.assertFalse(self.fleet.outputs["two"].settings["reduced"])
        self.assertEqual(self.engine.settings["brightness"], 20)
        self.assertTrue(all(manager.worker is workers[key] for key, manager in self.fleet.connections.items()))
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text())
        restarted = FleetTransportManager(self.engine.flame, self.lock, saved)
        self.assertEqual(restarted.outputs["one"].settings["brightness"], 8)
        self.assertTrue(restarted.outputs["one"].settings["reduced"])
        self.assertEqual(self.service.status("ai-flame-v01", "one")["light_settings"], {"brightness": 8, "reduced": True})

    def test_zero_brightness_only_suppresses_its_lamp_and_does_not_replay_completion(self):
        self.light_settings("one", brightness=0)
        event_id = self.emit("done")
        self.wait(lambda: self.fleet.outputs["two"].last_display_key == event_id)
        self.assertTrue(self.fleet.confirmed(event_id))
        self.assertEqual(self.fleet.outputs["one"].desired()["key"], "device-muted")
        receipts = self.engine.router.snapshot()
        self.assertFalse(any(row["event"] == event_id and row["output"] == "flame:one" and row["status"] == "applied" for row in receipts))
        with self.assertRaises(EventError):
            self.fleet.test_light("one")
        self.light_settings("one", brightness=12)
        self.assertEqual(self.fleet.outputs["one"].desired()["state"], "idle")
        self.assertEqual(self.fleet.outputs["two"].desired()["key"], event_id)

    def test_muting_and_restoring_a_lamp_during_completion_never_replays_it(self):
        event_id = self.emit("done")
        self.wait(lambda: self.fleet.confirmed(event_id))
        self.light_settings("one", brightness=0)
        self.light_settings("one", brightness=15)
        self.assertEqual(self.fleet.outputs["one"].desired()["state"], "idle")
        self.assertEqual(self.fleet.outputs["two"].desired()["key"], event_id)

    def test_legacy_settings_migrate_once_and_offline_edit_survives_restart(self):
        self.engine.update_settings({"brightness": 0, "reduced": True})
        config = {"devices": {"legacy": {"device_id": "legacy", "hardware_model": "yeelight-yldp005", "connection_paused": True}}}
        fleet = FleetTransportManager(self.engine.flame, self.lock, config)
        service = FleetDeviceService(config, self.temp.name, fleet, self.lock)
        self.assertEqual(service.status("yeelight-yldp005", "legacy")["light_settings"], {"brightness": 0, "reduced": True})
        self.engine.flame.transport = fleet
        self.assertTrue(self.engine.flame.brightness_off())
        service.update_light_settings({"hardware_model": "yeelight-yldp005", "device_id": "legacy", "settings": {"brightness": 7}})
        self.assertFalse(self.engine.flame.brightness_off())
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text())
        restarted = FleetTransportManager(self.engine.flame, self.lock, saved)
        self.assertEqual(restarted.outputs["legacy"].settings["brightness"], 7)
        self.assertTrue(restarted.outputs["legacy"].settings["reduced"])
        self.engine.flame.transport = self.fleet

    def test_invalid_or_wrong_device_light_settings_are_rejected(self):
        original = self.fleet.outputs["one"].settings.copy()
        for settings in ({"brightness": True}, {"brightness": 26}, {"reduced": 1}, {"paused": True}, {}, []):
            with self.assertRaises(EventError):
                self.light_settings("one", **settings) if isinstance(settings, dict) else self.service.update_light_settings({"device_id": "one", "hardware_model": "ai-flame-v01", "settings": settings})
        with self.assertRaises(EventError):
            self.service.update_light_settings({"device_id": "one", "hardware_model": "ai-flame-v02", "settings": {"brightness": 9}})
        self.assertEqual(self.fleet.outputs["one"].settings, original)

    def test_two_lamps_receive_same_task_and_separate_acknowledgements(self):
        self.emit("working")
        self.wait(lambda: all(any(frame["state"] == "working" for frame in connection.worker.frames)
                             for connection in self.fleet.connections.values()))
        event_id = self.emit("done", seq=2)
        self.wait(lambda: self.fleet.confirmed(event_id))
        self.assertEqual(self.engine.device["connected_count"], 2)
        with self.lock:
            records = self.engine.router.snapshot()
            self.assertEqual({row["output"] for row in records if row["event"] == event_id and row["status"] == "applied"},
                             {"flame", "flame:one", "flame:two"})
            self.assertTrue(self.engine.snapshot()["display"]["device_confirmed"])
        self.assertNotEqual(self.fleet.outputs["one"].last_command, self.fleet.outputs["two"].last_command)

    def test_one_failure_does_not_block_other_lamp(self):
        self.fleet.connection("one").worker.fail = True
        self.emit("working")
        self.wait(lambda: not self.fleet.outputs["one"].device["online"])
        event_id = self.emit("done", seq=2)
        self.wait(lambda: self.fleet.outputs["two"].last_display_key == event_id)
        self.assertTrue(self.fleet.outputs["two"].device["online"])
        self.assertEqual(self.engine.device["connected_count"], 1)

    def test_selection_and_scoped_disconnect_leave_other_worker_running(self):
        workers = {key: connection.worker for key, connection in self.fleet.connections.items()}
        result = self.service.start({"action": "select_model", "hardware_model": "ai-flame-v02"})
        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(all(worker.is_alive() and not worker.stopping.is_set() for worker in workers.values()))
        self.service.start({"action": "disconnect", "hardware_model": "ai-flame-v01", "device_id": "one"})
        self.wait(lambda: not self.service.busy())
        self.assertFalse(workers["one"].is_alive())
        self.assertTrue(workers["two"].is_alive())
        self.emit("working")
        self.wait(lambda: any(frame["state"] == "working" for frame in workers["two"].frames))
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text())
        self.assertTrue(saved["devices"]["one"]["connection_paused"])
        self.assertFalse(saved["devices"]["two"].get("connection_paused", False))

    def test_reconnect_does_not_cancel_another_devices_completion(self):
        event_id = self.emit("done")
        self.wait(lambda: self.fleet.confirmed(event_id))
        self.fleet.connection("one").stop()
        self.fleet.connection("one").start()
        self.wait(lambda: self.fleet.outputs["one"].device["online"])
        with self.lock:
            self.assertEqual(self.engine.current["id"], event_id)
            self.assertEqual(self.fleet.outputs["two"].desired()["state"], "done")
            self.assertEqual(self.fleet.outputs["one"].desired()["state"], "idle")

    def test_maintenance_only_pauses_target_and_keeps_fanout_alive(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def probe(_service, _port):
            entered.set()
            release.wait(2)
            return {"device_id": "one", "board": "ESP32-S3"}
        with patch("aiflame.devices.service.DeviceService._ports", return_value=[{"port": "COM1"}]), \
             patch("aiflame.devices.service.DeviceService.probe", probe), \
             patch("aiflame.devices.service.DeviceService.mp", return_value=""):
            self.service.start({"action": "probe", "hardware_model": "ai-flame-v01", "device_id": "one", "port": "COM1"})
            self.assertTrue(entered.wait(1))
            healthy = self.fleet.connection("two").worker
            self.emit("working")
            self.wait(lambda: any(frame["state"] == "working" for frame in healthy.frames))
            self.assertTrue(healthy.is_alive())
            release.set()
            self.wait(lambda: not self.service.busy())
            self.assertEqual(self.service.job["state"], "succeeded")

    def test_probe_existing_identity_at_new_port_has_exactly_one_visible_owner(self):
        old_worker = self.fleet.connection("one").worker
        with patch("aiflame.devices.service.DeviceService._ports", return_value=[{"port": "NEW"}]), \
             patch("aiflame.devices.service.DeviceService.mp", return_value=json.dumps({"device_id": "one", "board": "ESP32-S3"})):
            self.service.start({"action": "probe", "hardware_model": "ai-flame-v01", "device_id": "", "port": "NEW"})
            self.wait(lambda: not self.service.busy())
            self.assertEqual(self.service.job["state"], "succeeded")
            self.wait(lambda: len(self.fleet.sessions()) == 2)
            self.assertFalse(old_worker.is_alive())
            self.assertEqual(set(self.fleet.connections), {"one", "two"})
            self.assertIs(self.fleet.records["one"], self.fleet.connection("one").config)
            self.assertEqual(self.fleet.records["one"]["port"], "NEW")
            self.emit("working")
            self.wait(lambda: all(output.last_display_key == "working" for output in self.fleet.outputs.values()))

    def test_same_model_instances_are_distinct_and_status_has_no_secrets(self):
        self.config["devices"]["two"]["hardware_model"] = "ai-flame-v01"
        self.config["devices"]["two"]["wifi_device_secret"] = "private-secret"
        data = self.service.status("ai-flame-v01", "two")
        self.assertEqual(len(data["devices"]), 2)
        self.assertEqual(data["selected_device_id"], "two")
        self.assertNotIn("private-secret", json.dumps(data))
        with self.assertRaises(EventError):
            self.service.start({"action": "install", "device_id": "one", "port": "COM2", "hardware_model": "ai-flame-v01"})

    def test_light_targets_one_lamp_without_changing_shared_task_or_receipts(self):
        self.emit("working")
        self.wait(lambda: self.fleet.confirmed(self.engine.flame.desired()["key"]))
        with self.lock:
            shared = self.engine.flame.desired().copy()
            current_ack = self.engine.flame.last_command
            preview = self.fleet.test_light("two")
            self.assertEqual(preview["remaining_ms"], 10000)
            remaining = next(item for item in self.fleet.snapshot() if item["device_id"] == "two")["test_remaining_ms"]
            self.assertGreater(remaining, 9000)
            self.assertLessEqual(remaining, 10000)
        self.wait(lambda: self.fleet.outputs["two"].last_display_key == preview["key"])
        with self.lock:
            self.assertEqual(self.fleet.outputs["one"].desired(), shared)
            self.assertEqual(self.engine.flame.desired(), shared)
            self.assertIs(self.engine.flame.last_command, current_ack)
            self.assertFalse(any(frame.get("device_test") for frame in self.fleet.connections["one"].worker.frames))
            self.fleet.outputs["two"].light_test["expires"] = self.engine.flame.clock() - 1
        self.wait(lambda: self.fleet.outputs["two"].last_display_key == shared["key"])

    def test_pending_light_ack_is_cancelled_when_real_task_arrives(self):
        with self.lock:
            self.fleet.test_light("two")
            output = self.fleet.outputs["two"]
            desired = output.desired()
            transport = DeviceTransport(output, self.lock, "fixture", "two")
            transport.send = Mock()
            def receive(predicate, **options):
                self.emit("working")
                self.assertTrue(options["cancelled"]())
                return None
            transport.receive = receive
            self.assertIsNone(transport.display(desired, output.settings))
            transport.send.assert_called_once()
            self.assertLessEqual(transport.send.call_args.args[0]["duration_ms"], 10000)

    def test_light_cancels_on_real_state_change_and_rejects_unavailable_targets(self):
        with self.lock:
            with self.assertRaises(EventError):
                self.fleet.test_light("unknown")
            self.fleet.test_light("two")
            self.emit("working")
            self.assertNotIn("device_test", self.fleet.outputs["two"].desired())
            self.assertIsNone(self.fleet.outputs["two"].light_test)
            self.engine.update_settings({"paused": True})
            with self.assertRaises(EventError):
                self.fleet.test_light("two")

    def test_legacy_profiles_migrate_by_identity_without_enabling_paused_devices(self):
        old = {"device_id": "a", "hardware_model": "ai-flame-v01", "port": "A", "wifi_device_secret": "latest",
               "hardware_profiles": {"ai-flame-v01": {"device_id": "a", "wifi_device_secret": "old"},
                                     "ai-flame-v02": {"device_id": "b", "connection_paused": True}}}
        records = migrate_devices(old)
        self.assertEqual(set(records), {"a", "b"})
        self.assertEqual(records["a"]["wifi_device_secret"], "latest")
        self.assertTrue(records["b"]["connection_paused"])
        self.assertIs(migrate_devices(old), records)
