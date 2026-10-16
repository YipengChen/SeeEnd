"""Parallel transports, receipt isolation and maintenance of one lamp."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import make_event, EventError
from aiflame.devices.registry import migrate_devices
from aiflame.devices.fleet import FleetDeviceService
from aiflame.integrations.registry import SOURCES
from aiflame.transports.fleet import FleetTransportManager




class FakeLamp(threading.Thread):
    def __init__(self, engine, lock, record):
        super().__init__(daemon=True)
        self.engine, self.lock, self.record = engine, lock, record
        self.expected_device = record['device_id']
        self.stopping = threading.Event()
        self.frames, self.appearances = [], []
        self.fail = False

    def run(self):
        self.engine.device = {'online': True, 'session_id': 'session-' + self.expected_device,
                              'connected_at': time.monotonic(), 'port': 'wifi://' + self.expected_device,
                              'status': '已连接', 'last_error': None, 'device_id': self.expected_device}
        previous = None
        while not self.stopping.is_set():
            with self.lock:
                if self.fail:
                    self.engine.device.update(online=False, status='设备离线')
                else:
                    desired, settings = self.engine.desired(), self.engine.settings
                    signature = (desired, settings)
                    if signature != previous:
                        self.frames.append(dict(desired))
                        self.appearances.append(dict(settings))
                        self.engine.applied(desired, {'result': 'applied', 'command_id': self.expected_device})
                        previous = deepcopy(signature)
            self.stopping.wait(.01)
        self.engine.device.update(online=False)


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=SOURCES)
        self.engine.update_settings({"hardware_notifications": True})
        self.lock = threading.RLock()
        self.config = {"hub_id": "fixture", "devices": {
            "one": {"device_id": "one", "port": "wifi://192.0.2.1:55443", "hardware_model": "yeelight-yltd003"},
            "two": {"device_id": "two", "port": "wifi://192.0.2.2:55443", "hardware_model": "yeelight-yldp005"}}}
        self.fleet = FleetTransportManager(self.engine.flame, self.lock, self.config)
        self.engine.flame.transport = self.fleet
        self.factory = patch("aiflame.transports.manager.YeelightTransport", FakeLamp)
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

    def test_event_target_routes_only_to_selected_device_and_preserves_other_work(self):
        def targeted(state, task, seq, target):
            event = make_event("manual", task, state, seq)
            event["device_id"] = target
            with self.lock:
                self.engine.accept(event, "manual")
            return event["event_id"]

        second_start = targeted("working", "second", 1, "two")
        self.wait(lambda: self.fleet.outputs["two"].desired()["state"] == "working")
        self.wait(lambda: self.fleet.confirmed('working'))
        with self.lock:
            self.assertEqual(self.fleet.outputs["one"].desired()["state"], "idle")
        first_start = targeted("working", "first", 1, "one")
        self.wait(lambda: all(self.fleet.outputs[key].desired()["state"] == "working" for key in ("one", "two")))
        completion = targeted("done", "first", 2, "one")
        self.wait(lambda: self.fleet.outputs["one"].last_display_key == completion)
        with self.lock:
            second_state = self.fleet.outputs["two"].desired()["state"]
            rows = self.engine.router.snapshot()
            snapshot = self.engine.snapshot()
        self.assertEqual(second_state, "working")
        for event_id, target in ((first_start, "one"), (second_start, "two"), (completion, "one")):
            self.assertEqual({row["output"] for row in rows if row["event"] == event_id and row["output"].startswith("flame:")}, {"flame:" + target})
        self.assertEqual(snapshot["active_count"], 1)
        self.assertTrue(snapshot["display"]["device_confirmed"])
        with self.lock:
            self.engine.clear_prompts("superseded")
        broadcast = self.emit("done", "broadcast")
        self.wait(lambda: self.fleet.confirmed(broadcast))

    def test_targeted_lease_timeout_keeps_its_device_receipt_scope(self):
        for target in ('one', 'two'):
            event = make_event('manual', target, 'working')
            event['device_id'] = target
            with self.lock:
                self.engine.accept(event, 'manual')
        with self.lock:
            self.engine.active[('manual', 'one')]['until'] = self.engine.clock() - 1
            self.engine.tick()
            timeout = next(row for row in self.engine.snapshot()['events'] if row['reason'] == 'timeout')
            self.assertEqual({row['output'] for row in self.engine.router.snapshot()
                              if row['event'] == timeout['id'] and row['output'].startswith('flame:')}, {'flame:one'})
            self.assertEqual(self.fleet.outputs['one'].desired()['state'], 'idle')
            self.assertEqual(self.fleet.outputs['two'].desired()['state'], 'working')


    def test_appearance_is_per_instance_persistent_and_applied_without_reconnecting(self):
        workers = {key: manager.worker for key, manager in self.fleet.connections.items()}
        self.light_settings("one", brightness=8, reduced=True)
        self.wait(lambda: workers["one"].appearances[-1]["brightness"] == 8)
        self.assertFalse(workers["one"].appearances[-1]["reduced"])
        self.assertEqual(self.fleet.outputs["two"].settings["brightness"], 50)
        self.assertFalse(self.fleet.outputs["two"].settings["reduced"])
        self.assertEqual(self.engine.settings["brightness"], 20)
        self.assertTrue(all(manager.worker is workers[key] for key, manager in self.fleet.connections.items()))
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text(encoding="utf-8"))
        restarted = FleetTransportManager(self.engine.flame, self.lock, saved)
        self.assertEqual(restarted.outputs["one"].settings["brightness"], 8)
        self.assertFalse(restarted.outputs["one"].settings["reduced"])
        self.assertEqual(self.service.status("yeelight-yltd003", "one")["light_settings"]["brightness"], 8)

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
        with self.lock:
            self.engine.update_settings({"brightness": 0, "reduced": True})
        config = {"devices": {"legacy": {"device_id": "legacy", "hardware_model": "yeelight-yltd003", "connection_paused": True}}}
        fleet = FleetTransportManager(self.engine.flame, self.lock, config)
        service = FleetDeviceService(config, self.temp.name, fleet, self.lock)
        self.assertEqual(service.status("yeelight-yltd003", "legacy")["light_settings"]["brightness"], 50)
        self.engine.flame.transport = fleet
        self.assertFalse(self.engine.flame.brightness_off())
        service.update_light_settings({"hardware_model": "yeelight-yltd003", "device_id": "legacy", "settings": {"brightness": 7}})
        self.assertFalse(self.engine.flame.brightness_off())
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text(encoding="utf-8"))
        restarted = FleetTransportManager(self.engine.flame, self.lock, saved)
        self.assertEqual(restarted.outputs["legacy"].settings["brightness"], 7)
        self.assertFalse(restarted.outputs["legacy"].settings["reduced"])
        self.engine.flame.transport = self.fleet

    def test_invalid_or_wrong_device_light_settings_are_rejected(self):
        original = self.fleet.outputs["one"].settings.copy()
        for settings in ({"brightness": True}, {"brightness": 101}, {"reduced": 1}, {"paused": True}, {}, []):
            with self.assertRaises(EventError):
                self.light_settings("one", **settings) if isinstance(settings, dict) else self.service.update_light_settings({"device_id": "one", "hardware_model": "yeelight-yltd003", "settings": settings})
        with self.assertRaises(EventError):
            self.service.update_light_settings({"device_id": "one", "hardware_model": "yeelight-yldp005", "settings": {"brightness": 9}})
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
        self.config["devices"]["one"]["wifi_device_secret"] = "one-secret"
        self.config["devices"]["two"]["wifi_device_secret"] = "two-secret"
        original = deepcopy(self.config)
        sessions = self.fleet.sessions()
        credentials = Path(self.temp.name) / "credentials.json"
        saved_before = credentials.read_bytes()
        for model in ("yeelight-yldp005", "yeelight-yltd003"):
            result = self.service.start({"action": "select_model", "hardware_model": model})
            self.assertEqual(result["state"], "succeeded")
            self.assertEqual(self.config, original)
            self.assertEqual(credentials.read_bytes(), saved_before)
            self.assertEqual(self.fleet.sessions(), sessions)
            self.assertTrue(all(manager.worker is workers[key] for key, manager in self.fleet.connections.items()))
        with self.assertRaises(EventError):
            self.service.start({"action": "select_model", "hardware_model": "missing"})
        self.assertEqual(self.config, original)
        self.assertTrue(all(worker.is_alive() and not worker.stopping.is_set() for worker in workers.values()))
        self.service.start({"action": "disconnect", "hardware_model": "yeelight-yltd003", "device_id": "one"})
        self.wait(lambda: not self.service.busy())
        self.assertFalse(workers["one"].is_alive())
        self.assertTrue(workers["two"].is_alive())
        self.emit("working")
        self.wait(lambda: any(frame["state"] == "working" for frame in workers["two"].frames))
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text(encoding="utf-8"))
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



    def test_same_model_instances_are_distinct_and_status_has_no_secrets(self):
        self.config["devices"]["two"]["hardware_model"] = "yeelight-yltd003"
        self.config["devices"]["two"]["wifi_device_secret"] = "private-secret"
        data = self.service.status("yeelight-yltd003", "two")
        self.assertEqual(len(data["devices"]), 2)
        self.assertEqual(data["selected_device_id"], "two")
        self.assertNotIn("private-secret", json.dumps(data))
        with self.assertRaises(EventError):
            self.service.start({"action": "install", "device_id": "one", "port": "wifi://192.0.2.2:55443", "hardware_model": "yeelight-yltd003"})

    def test_selected_state_preview_is_pinned_renewable_and_cancellable(self):
        with self.lock:
            shared = self.engine.flame.desired().copy()
            preview = self.fleet.test_light("two", "done")
            output = self.fleet.outputs["two"]
            output.light_test["started"] -= 3600
            self.assertIsNone(output.light_test["expires"])
            self.assertTrue(output.desired()["holding"])
            self.assertTrue(preview["continuous"])
            with patch.object(self.engine.flame, "clock", return_value=self.engine.flame.clock() + 3600):
                self.assertEqual(output.desired()["state"], "done")
                self.assertTrue(output.desired()["continuous"])
            row = next(row for row in self.fleet.snapshot() if row["device_id"] == "two")
            self.assertTrue(row["test_continuous"])
            self.assertEqual(row["test_remaining_ms"], 0)
            self.assertEqual(output.desired()["state"], "done")
            self.assertEqual(self.fleet.outputs["one"].desired(), shared)
            self.assertEqual(self.fleet.test_light("two", "done")["key"], preview["key"])
            self.assertNotEqual(self.fleet.test_light("two", "working")["key"], preview["key"])
            self.assertEqual(output.desired()["state"], "working")
            self.fleet.test_light("two", "stop")
            self.assertEqual(output.desired(), shared)
            self.fleet.test_light("two", "needs_input")
            output.device["online"] = False
            self.assertEqual(output.desired(), shared)
            output.device["online"] = True
            self.fleet.test_light("two", "done")
            self.emit("working", "real-task")
            self.assertFalse(output.desired().get("device_test"))
            with self.assertRaises(EventError):
                self.fleet.test_light("two", "done")
            with self.assertRaises(EventError):
                self.fleet.test_light("two", "invalid")

    def test_light_targets_one_lamp_without_changing_shared_task_or_receipts(self):
        self.wait(lambda: self.fleet.confirmed(self.engine.flame.desired()["key"]))
        with self.lock:
            shared = self.engine.flame.desired().copy()
            current_ack = self.engine.flame.last_command
            preview = self.fleet.test_light("two")
            self.assertEqual(preview["remaining_ms"], 15000)
            remaining = next(item for item in self.fleet.snapshot() if item["device_id"] == "two")["test_remaining_ms"]
            self.assertGreater(remaining, 14000)
            self.assertLessEqual(remaining, 15000)
        self.wait(lambda: self.fleet.outputs["two"].last_display_key == preview["key"])
        with self.lock:
            self.assertEqual(self.fleet.outputs["one"].desired(), shared)
            self.assertEqual(self.engine.flame.desired(), shared)
            self.assertIs(self.engine.flame.last_command, current_ack)
            self.assertFalse(any(frame.get("device_test") for frame in self.fleet.connections["one"].worker.frames))
            self.fleet.outputs["two"].light_test["expires"] = self.engine.flame.clock() - 1
        self.wait(lambda: self.fleet.outputs["two"].last_display_key == shared["key"])

    def test_any_unfinished_task_blocks_each_device_test_and_cancels_an_existing_test(self):
        with self.lock:
            self.fleet.test_light("two")
            self.emit("working", "other-task")
            self.assertIsNone(self.fleet.outputs["two"].desired().get("device_test"))
            for state, seq in (("working", 1), ("needs_input", 2)):
                if seq > 1:
                    self.emit(state, "other-task", seq)
                # A visible completion must not hide another unfinished task.
                self.emit("done", "completed-" + state)
                self.assertEqual(self.engine.desired()["state"], "done" if state == "working" else "needs_input")
                for device_id in ("one", "two"):
                    with self.subTest(state=state, device_id=device_id), self.assertRaisesRegex(EventError, "有任务尚未结束"):
                        self.fleet.test_light(device_id)
                    self.assertIsNone(self.fleet.outputs[device_id].light_test)
            self.emit("cancelled", "other-task", 3)
            self.assertEqual(self.engine.snapshot()["unfinished_count"], 0)
            self.fleet.test_light("two")
            self.assertTrue(self.fleet.outputs["two"].desired().get("device_test"))

    def test_light_plays_working_waiting_done_and_returns_to_shared_state(self):
        with self.lock:
            now = [100.0]
            with patch.object(self.engine.flame, "clock", lambda: now[0]):
                shared = self.engine.flame.desired().copy()
                self.fleet.test_light("two")
                output = self.fleet.outputs["two"]
                working_key = output.desired()["key"]
                for elapsed, state, duration, remaining in (
                    (0, "working", 5000, 15000),
                    (4, "working", 1000, 11000),
                    (5, "needs_input", 5000, 10000),
                    (9, "needs_input", 1000, 6000),
                    (10, "done", 5000, 5000),
                    (14, "done", 1000, 1000),
                ):
                    now[0] = 100 + elapsed
                    desired = output.desired()
                    self.assertEqual(desired["state"], state)
                    self.assertEqual(desired["duration_ms"], duration)
                    self.assertEqual(desired["remaining_ms"], remaining)
                    self.assertEqual(desired["key"] == working_key, state == "working")
                    self.assertEqual(self.fleet.outputs["one"].desired(), shared)
                now[0] = 115
                self.assertEqual(output.desired(), shared)
                self.assertIsNone(output.light_test)


    def test_light_cancels_on_real_state_change_and_rejects_unavailable_targets(self):
        with self.lock:
            with self.assertRaises(EventError):
                self.fleet.test_light("unknown")
            self.fleet.test_light("two")
            self.emit("working")
            self.assertNotIn("device_test", self.fleet.outputs["two"].desired())
            self.assertIsNone(self.fleet.outputs["two"].light_test)
            self.engine.stop()
            with self.assertRaises(EventError):
                self.fleet.test_light("two")

    def test_delete_yeelight_stops_only_target_and_persists_removal(self):
        self.fleet.records['two']['hardware_model'] = 'yeelight-yltd003'
        self.emit('working')
        self.wait(lambda: self.fleet.confirmed(self.engine.flame.desired()['key']))
        other = self.fleet.connections['one'].worker
        target = self.fleet.connections['two'].worker
        result = self.service.delete({'hardware_model': 'yeelight-yltd003', 'device_id': 'two'})
        self.assertFalse(target.is_alive())
        self.assertIs(self.fleet.connections['one'].worker, other)
        self.assertTrue(other.is_alive())
        self.assertNotIn('two', self.fleet.records)
        self.assertNotIn('two', self.fleet.connections)
        self.assertNotIn('two', self.fleet.outputs)
        self.assertEqual(result['selected_device_id'], '')
        self.assertTrue(self.engine.flame.device['online'])
        saved = json.loads((Path(self.temp.name) / 'credentials.json').read_text(encoding="utf-8"))
        self.assertEqual(set(saved['devices']), {'one'})
        self.assertEqual(set(FleetTransportManager(self.engine.flame, self.lock, saved).records), {'one'})
        self.fleet.records['one']['hardware_model'] = 'yeelight-yldp005'
        self.service.delete({'hardware_model': 'yeelight-yldp005', 'device_id': 'one'})
        self.assertFalse(other.is_alive())
        self.assertFalse(self.engine.flame.device['online'])
        self.assertEqual(self.engine.flame.device['device_count'], 0)
        self.assertEqual(self.fleet.records, {})

    def test_failed_delete_keeps_record_and_resumes_target_connection(self):
        self.fleet.records['two']['hardware_model'] = 'yeelight-yltd003'
        other = self.fleet.connections['one'].worker
        with patch('aiflame.devices.fleet.write_private', side_effect=OSError('disk full')), \
             patch('aiflame.transports.manager.YeelightTransport',
                   FakeLamp):
            with self.assertRaises(OSError):
                self.service.delete({'hardware_model': 'yeelight-yltd003', 'device_id': 'two'})
            self.wait(lambda: self.fleet.outputs['two'].device['online'])
        self.assertIn('two', self.config['devices'])
        self.assertIs(self.fleet.connections['one'].worker, other)
        for body in ({'hardware_model': 'ai-flame-v01', 'device_id': 'one'},
                     {'hardware_model': 'yeelight-yldp005', 'device_id': 'two'},
                     {'hardware_model': 'yeelight-yltd003', 'device_id': ''},
                     {'hardware_model': 'yeelight-yltd003', 'device_id': 'unknown'}):
            with self.subTest(body=body), self.assertRaises(EventError):
                self.service.delete(body)
        self.assertEqual(set(self.fleet.records), {'one', 'two'})

    def test_legacy_profiles_migrate_by_identity_without_enabling_paused_devices(self):
        old = {"device_id": "a", "hardware_model": "yeelight-yltd003", "port": "A", "wifi_device_secret": "latest",
               "hardware_profiles": {"yeelight-yltd003": {"device_id": "a", "wifi_device_secret": "old"},
                                     "yeelight-yldp005": {"device_id": "b", "connection_paused": True}}}
        records = migrate_devices(old)
        self.assertEqual(set(records), {"a", "b"})
        self.assertEqual(records["a"]["wifi_device_secret"], "latest")
        self.assertTrue(records["b"]["connection_paused"])
        self.assertIs(migrate_devices(old), records)


class FleetReceiptTests(unittest.TestCase):
    """Control ACKs and time explicitly; no transport threads or physical devices."""
    def setUp(self):
        self.now = 1000.0
        self.engine = Engine(":memory:", clock=lambda: self.now, sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        self.engine.update_settings({"hardware_notifications": True})
        self.fleet = FleetTransportManager(self.engine.flame, threading.RLock(), {"devices": {
            "one": {"device_id": "one", "hardware_model": "yeelight-yltd003"},
            "two": {"device_id": "two", "hardware_model": "yeelight-yldp005"}}})
        self.engine.flame.transport = self.fleet
        for key, output in self.fleet.outputs.items():
            output.device.update(online=True, session_id="session-" + key, connected_at=1)

    def emit(self, state, task="task", seq=1, source="manual", **extra):
        event = make_event(source, task, state, seq)
        event.update(extra)
        self.engine.accept(event, source)
        return event["event_id"]

    def receipts(self, event_id):
        return {row["output"]: row["status"] for row in self.engine.router.snapshot()
                if row["event"] == event_id}

    def ack(self, key):
        output = self.fleet.outputs[key]
        output.applied(output.desired(), {"result": "applied", "command_id": key})

    def test_admission_records_each_online_offline_disabled_and_muted_target(self):
        for key, fields in (("offline", {}), ("disabled", {"connection_paused": True}),
                            ("muted", {"light_settings": {"brightness": 0, "reduced": False}})):
            record = {"device_id": key, "hardware_model": "yeelight-yltd003", **fields}
            self.fleet.records[key] = record
            self.fleet.connection(key, record)
        event_id = self.emit("working")
        receipts = self.receipts(event_id)
        self.assertEqual({key: status for key, status in receipts.items() if key.startswith("flame:")},
                         {"flame:one": "queued", "flame:two": "queued", "flame:offline": "offline",
                          "flame:disabled": "disabled", "flame:muted": "muted"})

    def test_partial_ack_summary_keeps_each_target_identity_and_status(self):
        event_id = self.emit("done")
        self.ack("one")
        event = next(row for row in self.engine.snapshot()["events"] if row["id"] == event_id)
        self.assertEqual([(row["device_id"], row["name"], row["status"])
                          for row in event["delivery_summary"]["devices"]],
                         [("one", "Yeelight 屏幕挂灯 Pro", "applied"), ("two", "Yeelight W3 彩色灯泡", "queued")])
        self.assertFalse(self.engine.snapshot()["display"]["device_confirmed"])

    def test_idle_and_cancelled_acks_are_recorded_for_each_device(self):
        for state in ("idle", "cancelled"):
            with self.subTest(state=state):
                self.emit("working", task=state)
                event_id = self.emit(state, task=state, seq=2)
                self.ack("one")
                self.assertEqual(self.receipts(event_id)["flame:one"], "applied")
                self.assertEqual(self.receipts(event_id)["flame:two"], "queued")
                self.ack("two")
                self.assertEqual(self.receipts(event_id)["flame:two"], "applied")

    def test_new_task_state_settles_unconfirmed_targets_without_erasing_ack_history(self):
        event_id = self.emit("working")
        self.ack("one")
        self.emit("done", seq=2)
        receipts = self.receipts(event_id)
        self.assertEqual(receipts["flame"], "applied")
        self.assertEqual(receipts["flame:one"], "applied")
        self.assertEqual(receipts["flame:two"], "superseded")

    def test_done_before_start_ack_settles_start_and_rejects_its_late_ack(self):
        started = self.emit("working")
        old = self.fleet.outputs["one"].desired()
        completed = self.emit("done", seq=2)
        for output in self.fleet.outputs.values():
            output.applied(old, {"result": "applied", "command_id": "late-start"})
        self.assertEqual({status for key, status in self.receipts(started).items()
                          if key.startswith("flame")}, {"superseded"})
        self.assertEqual({status for key, status in self.receipts(completed).items()
                          if key.startswith("flame")}, {"queued"})

    def test_shutdown_preserves_confirmed_completion_and_settles_other_lamp(self):
        event_id = self.emit("done")
        self.ack("one")
        self.engine.stop()
        self.assertEqual(self.engine.desired()["state"], "idle")
        receipts = self.receipts(event_id)
        self.assertEqual(receipts["flame"], "applied")
        self.assertEqual(receipts["flame:one"], "applied")
        self.assertEqual(receipts["flame:two"], "suppressed")

    def test_prompt_deadline_expires_only_targets_without_ack(self):
        self.engine.update_settings({"hold_until_activity": False})
        event_id = self.emit("done")
        self.ack("one")
        self.now += self.engine.settings["duration"] + 1
        self.assertEqual(self.engine.desired()["state"], "idle")
        receipts = self.receipts(event_id)
        self.assertEqual(receipts["flame"], "applied")
        self.assertEqual(receipts["flame:one"], "applied")
        self.assertEqual(receipts["flame:two"], "expired")

    def test_recent_fifty_events_keep_all_receipts_beyond_old_row_limit(self):
        ids = [self.emit("working", task="task-" + str(index)) for index in range(51)]
        snapshot = self.engine.snapshot()
        self.assertEqual({event["id"] for event in snapshot["events"]}, set(ids[1:]))
        self.assertGreater(len(snapshot["deliveries"]), 150)
        for event in snapshot["events"]:
            self.assertEqual({row["device_id"] for row in event["delivery_summary"]["devices"]}, {"one", "two"})
        self.assertNotIn(ids[0], {row["event"] for row in snapshot["deliveries"]})

    def test_unacknowledged_held_prompt_expires_instead_of_remaining_queued(self):
        self.engine.update_settings({"hold_until_activity": True})
        event_id = self.emit("done", ttl_ms=1000)
        self.assertTrue(self.engine.desired()["holding"])
        self.now += 1.1
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.assertIsNone(self.engine.current)
        self.assertEqual({status for key, status in self.receipts(event_id).items()
                          if key.startswith("flame")}, {"expired"})

    def test_reconnecting_all_original_sessions_ends_held_prompt_without_replay(self):
        self.engine.update_settings({"hold_until_activity": True})
        event_id = self.emit("done")
        self.assertTrue(self.engine.desired()["holding"])
        for output in self.fleet.outputs.values():
            output.device.update(session_id="rebooted", connected_at=2)
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.assertIsNone(self.engine.current)
        self.assertTrue(all(output.desired()["state"] == "idle" for output in self.fleet.outputs.values()))
        self.assertEqual({status for key, status in self.receipts(event_id).items()
                          if key.startswith("flame")}, {"ended_on_reconnect"})
