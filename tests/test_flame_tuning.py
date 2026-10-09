"""Parameter validation, per-device isolation, leases and firmware bounds."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from aiflame.core.engine import Engine
from aiflame.core.events import EventError
from aiflame.devices.firmware.candle import Renderer, TUNING_FIELDS, validate_tuning
from aiflame.hub.tuning import update, status
from aiflame.transports.fleet import FleetTransportManager
from aiflame.transports.base import DeviceTransport


class TuningTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.engine = Engine(':memory:', clock=lambda: self.now)
        self.addCleanup(self.engine.db.close)
        self.engine.update_settings({'mode': 'hardware'})
        self.fleet = FleetTransportManager(self.engine.flame, threading.RLock(), {
            'hub_id': 'test', 'devices': {key: {'device_id': key, 'hardware_model': 'ai-flame-v01'} for key in ('one', 'two')}})
        self.hub = SimpleNamespace(transport=self.fleet, engine=self.engine)
        for output in self.fleet.outputs.values():
            output.device.update(online=True, capabilities=['flame-tuning-v1'], session_id='s', connected_at=1)
        self.output = self.fleet.outputs['one']

    def apply(self, **extra):
        return update(self.hub, {'action': 'apply', 'device_id': 'one', 'params': {'height': 9}, **extra})

    def test_isolation_confirmation_and_stop_restore_normal_schedule(self):
        response = self.apply()
        desired = self.output.desired()
        self.assertEqual(desired['tuning']['height'], 9)
        self.assertNotIn('tuning', self.fleet.outputs['two'].desired())
        self.output.applied(desired, {'result': 'applied'})
        self.assertTrue(status(self.hub)['devices'][0]['confirmed'])
        self.assertIsNone(self.engine.flame.last_command)
        update(self.hub, {'action': 'stop', 'device_id': 'one', 'session': 'wrong'})
        self.assertIn('tuning', self.output.desired())
        update(self.hub, {'action': 'stop', 'device_id': 'one', 'session': response['session']})
        self.assertNotIn('tuning', self.output.desired())

    def test_ownership_stale_ack_and_lease(self):
        first = self.apply()
        old = self.output.desired()
        with self.assertRaises(EventError):
            self.apply()
        self.apply(session=first['session'], params={'height': 11})
        self.output.applied(old, {'result': 'applied'})
        self.assertFalse(status(self.hub)['devices'][0]['confirmed'])
        self.now += 6
        self.assertNotIn('tuning', self.output.desired())
        self.apply()

    def test_disconnect_reconnect_and_emergency_cancel(self):
        for change in ({'online': False}, {'session_id': 'new'}, {'connected_at': 2}):
            self.output.device.update(online=True, session_id='s', connected_at=1)
            self.apply()
            self.output.device.update(change)
            self.assertNotIn('tuning', self.output.desired())
        self.output.device.update(online=True)
        self.apply()
        self.engine.flame.stop()
        self.assertNotIn('tuning', self.output.desired())

    def test_old_firmware_and_malformed_values_rejected(self):
        for patch in ({'device_id': []}, {'params': {'height': float('nan')}}, {'params': {'bad': 1}}, {'params': []}, {'params': {'brightness': True}}):
            with self.assertRaises(EventError):
                self.apply(**patch)
        self.output.device['capabilities'] = []
        with self.assertRaisesRegex(EventError, '0.4.8'):
            self.apply()

    def test_wire_parameters_and_safety_duration(self):
        self.apply(params={'brightness': 13, 'height': 9})
        worker = DeviceTransport(self.output, threading.RLock(), 'test', 'one')
        worker.session = 's'
        worker.connection = Mock()
        worker.receive = Mock(return_value={'result': 'applied'})
        worker.display(self.output.desired(), {'brightness': 25, 'reduced': True})
        packet = json.loads(worker.connection.write.call_args.args[0])
        self.assertEqual(packet['brightness'], 13)
        self.assertEqual(packet['effect'], 'standard')
        self.assertEqual(packet['lease_ms'], 7000)
        self.assertEqual(packet['duration_ms'], 5000)
        self.assertLess(len(json.dumps(packet)), 4096)

    def test_uniform_minimum_maximum_and_default_profiles_bounded_for_both_matrices(self):
        for dimensions in ((9, 16), (7, 15)):
            renderer = Renderer(*dimensions)
            for index in (2, 3, 5):
                renderer.tuning = validate_tuning({f[0]: f[index] for f in TUNING_FIELDS})
                for t in range(0, 34000, 133):
                    pixels = renderer.render(t, 'done', 25)
                    self.assertEqual(len(pixels), dimensions[0]*dimensions[1])
                    self.assertLessEqual(max(pixels), 16)
                self.assertFalse(any(renderer.render(34000, 'idle', 25)))
                self.assertFalse(any(renderer.render(35000, 'done', 0)))

if __name__ == '__main__':
    unittest.main()
