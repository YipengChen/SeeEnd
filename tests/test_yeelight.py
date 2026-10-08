"""Yeelight wire framing, native effects, physical identity and fleet isolation."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.integrations.registry import SOURCES
from aiflame.core.events import EventError, make_event
from aiflame.devices.downloads import firmware_archive
from aiflame.devices.fleet import FleetDeviceService
from aiflame.devices.models import YEELIGHT_MODEL, YEELIGHT_BULB_MODEL, hardware_model
from aiflame.devices.yeelight import YeelightClient, lan_host, parse_discovery, discover
from aiflame.transports.fleet import FleetTransportManager
from aiflame.transports.yeelight import COLORS, light_flow, YeelightTransport

HOST = '192.168.1.44'
IDENTITY = 'yeelight:0x123456'
INFO = {'device_id': IDENTITY, 'host': HOST, 'port': 55443, 'model': 'lamp15',
        'name': 'YLTD003', 'firmware_version': '38',
        'support': ['get_prop', 'bg_set_power', 'bg_start_cf', 'bg_set_scene']}
REPLY = (f'HTTP/1.1 200 OK\r\nLocation: yeelight://{HOST}:55443\r\n'
         'id: 0x123456\r\nmodel: lamp15\r\nfw_ver: 38\r\n'
         'support: get_prop bg_set_power bg_start_cf bg_set_scene\r\n\r\n').encode()

BULB_ID = 'yeelight:0x654321'
BULB_INFO = {**INFO, 'device_id': BULB_ID, 'model': 'colorb', 'name': 'YLDP005',
             'support': ['get_prop', 'set_power', 'start_cf', 'set_scene']}
BULB_REPLY = REPLY.replace(b'lamp15', b'colorb').replace(b'0x123456', b'0x654321').replace(b'bg_', b'')


class YeelightTests(unittest.TestCase):
    def test_discovery_uses_physical_identity_and_filters_model_support_sender(self):
        self.assertEqual(parse_discovery(REPLY, HOST)['device_id'], IDENTITY)
        for packet, sender in [(REPLY.replace(b'lamp15', b'color4'), HOST),
                               (REPLY.replace(b'bg_start_cf', b'no_flow'), HOST),
                               (REPLY, '192.168.1.45')]:
            self.assertIsNone(parse_discovery(packet, sender))
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock()
        sock.recvfrom.side_effect = [(REPLY, (HOST, 1982)), TimeoutError()]
        with patch('aiflame.devices.yeelight.socket.socket', return_value=sock):
            self.assertEqual(discover(HOST)[0]['device_id'], IDENTITY)
        sock.sendto.assert_called_once()
        self.assertEqual(sock.sendto.call_args.args[1], (HOST, 1982))
        self.assertTrue(sock.sendto.call_args.args[0].endswith(b'\r\n\r\n'))

    def test_manual_address_validation(self):
        self.assertEqual(lan_host(HOST), HOST)
        for value in [None, '', 'localhost', '127.0.0.1', '0.0.0.0', '239.255.255.250', '255.255.255.255', HOST + ':55443']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                lan_host(value)

    def test_crlf_framing_fragmented_reply_and_async_notifications(self):
        client = YeelightClient(HOST)
        client.sock = Mock()
        client.sock.recv.side_effect = [b'{"method":"props","params":{"bg_power":"on"}}\r\n{"id":1,',
                                       b'"result":["ok"]}\r\n']
        response = client.command('bg_set_power', ['on', 'smooth', 300])
        self.assertEqual(response, {'id': 1, 'result': ['ok']})
        wire = client.sock.sendall.call_args.args[0]
        self.assertTrue(wire.endswith(b'\r\n'))
        self.assertEqual(json.loads(wire), {'id': 1, 'method': 'bg_set_power', 'params': ['on', 'smooth', 300]})
        client.sock.recv.side_effect = [b'{"id":2,"error":{"code":-1,"message":"unsupported"}}\r\n']
        with self.assertRaisesRegex(ValueError, '拒绝'):
            client.command('bg_start_cf', [1, 2, '1000,1,100,10'])

    def test_native_flows_end_off_respect_brightness_reduced_and_hold(self):
        settings = {'brightness': 20, 'reduced': False}
        for state in ('working', 'done', 'error'):
            desired = {'key': state, 'state': state, 'duration_ms': 10000}
            count, action, expression = light_flow(desired, settings)
            self.assertEqual(action, 2)  # Official action 2 turns off; 0 restores previous state.
            values = list(map(int, expression.split(',')))
            tuples = [values[index:index + 4] for index in range(0, len(values), 4)]
            self.assertGreater(count, 0)
            self.assertTrue(all(row[1:3] == [1, COLORS[state]] for row in tuples))
            self.assertTrue(all(1 <= row[3] <= 25 for row in tuples))
            total = sum(tuples[index % len(tuples)][0] for index in range(count))
            self.assertLessEqual(total, 30000 if state == 'working' else 10000)
            reduced = light_flow(desired, {**settings, 'reduced': True})
            self.assertEqual(reduced[0], 2)
            self.assertEqual(len(reduced[2].split(',')), 8)
        held = light_flow({'state': 'done', 'duration_ms': 0, 'holding': True}, {**settings, 'reduced': True})
        self.assertEqual(held, [2, 2, f'50,1,{COLORS["done"]},20,29950,7,0,0'])
        self.assertIsNone(light_flow({'state': 'idle'}, settings))
        self.assertIsNone(light_flow({'state': 'done'}, {**settings, 'brightness': 0}))
        preview = light_flow({'state': 'working', 'preview': 'preview', 'duration_ms': 5000}, {**settings, 'reduced': True})
        self.assertEqual(preview[2].split(',')[4], '4950')

    def test_display_only_controls_rear_channel_and_falls_back_when_scene_absent(self):
        engine = Mock(settings={'brightness': 20, 'reduced': False})
        worker = YeelightTransport(engine, threading.RLock(), {})
        desired = {'state': 'working', 'key': 'working', 'duration_ms': 30000}
        worker.command = Mock(return_value={'id': 1, 'result': ['ok']})
        worker.support = {'bg_set_scene'}
        worker.display(desired, engine.settings)
        self.assertEqual(worker.command.call_args.args[0], 'bg_set_scene')
        self.assertEqual(worker.command.call_args.args[1][0:3], ['cf', 20, 2])
        worker.command.reset_mock()
        worker.support = set()
        worker.display(desired, engine.settings)
        self.assertEqual([call.args[0] for call in worker.command.call_args_list], ['bg_set_power', 'bg_start_cf'])
        worker.command.reset_mock()
        worker.display({'state': 'idle'}, engine.settings)
        self.assertEqual(worker.command.call_args.args, ('bg_set_power', ['off', 'sudden', 0]))

    def test_rate_limit_checks_supersession_before_sending(self):
        engine = Mock(settings={'brightness': 20, 'reduced': False})
        worker = YeelightTransport(engine, threading.RLock(), {})
        worker.client = Mock()
        worker.client.command.return_value = {'id': 1, 'result': ['ok']}
        worker.stopping = Mock()
        worker.stopping.wait.return_value = False
        with patch('aiflame.transports.yeelight.time.monotonic', return_value=100):
            worker.command('bg_set_power', ['off', 'sudden', 0])
            worker.command('bg_set_power', ['off', 'sudden', 0])
        self.assertAlmostEqual(worker.stopping.wait.call_args.args[0], 1.1)
        engine.desired.return_value = {'key': 'idle', 'state': 'idle'}
        worker.client.command.reset_mock()
        response = worker.command('bg_start_cf', [], desired={'key': 'old', 'state': 'done'}, settings=engine.settings)
        self.assertIsNone(response)
        worker.client.command.assert_not_called()

    def _worker_applies_events(self, model_id, info, prefix):
        identity = info["device_id"]
        class FastTransport(YeelightTransport):
            def command(self, *args, **kwargs):
                self.next_command = 0  # Quota behavior has a separate deterministic test.
                return super().command(*args, **kwargs)

        engine = Engine(":memory:", sources=SOURCES)
        self.addCleanup(engine.db.close)
        engine.update_settings({"mode": "hardware"})
        record = {"device_id": identity, "hardware_model": model_id,
                  "transport": "yeelight", "yeelight_host": HOST}
        lock = threading.RLock()
        fleet = FleetTransportManager(engine.flame, lock, {"devices": {identity: record}})
        engine.flame.transport = fleet
        client = Mock()
        client.open.return_value = client
        client.command.side_effect = lambda method, params: {"id": client.command.call_count, "result":
            ["off", "20", "0"] if method == "get_prop" and len(params) == 3 else
            ["on"] if method == "get_prop" else ["ok"]}

        def wait_for(predicate):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with lock:
                    if predicate():
                        return
                time.sleep(.01)
            self.fail("Yeelight worker did not confirm display")

        with patch('aiflame.transports.yeelight.discover', return_value=[info]), \
             patch('aiflame.transports.yeelight.YeelightClient', return_value=client), \
             patch('aiflame.transports.manager.YeelightTransport', FastTransport):
            fleet.start()
            try:
                wait_for(lambda: len(fleet.sessions()) == 1)
                with lock:
                    engine.accept(make_event("manual", "task", "working", 1), "manual")
                wait_for(lambda: fleet.outputs[identity].last_display_key == "working")
                with lock:
                    event = make_event("manual", "task", "done", 2)
                    engine.accept(event, "manual")
                wait_for(lambda: fleet.confirmed(event["event_id"]))
                self.assertTrue(engine.flame.current["applied"])
                self.assertTrue(any(call.args[0] == prefix + 'set_scene' and str(COLORS['done']) in call.args[1][-1]
                                    for call in client.command.call_args_list))
                with lock:
                    engine.update_settings({"paused": True})
                wait_for(lambda: fleet.outputs[identity].last_display_key == "muted")
            finally:
                fleet.stop()
        self.assertEqual(client.command.call_args.args, (prefix + 'set_power', ['off', 'sudden', 0]))
        self.assertTrue(all(call.args[0] == 'get_prop' or call.args[0] in (prefix + 'set_power', prefix + 'set_scene', prefix + 'start_cf')
                            for call in client.command.call_args_list))

    def test_worker_applies_real_events_and_shutdown_turns_off_only_backlight(self):
        self._worker_applies_events(YEELIGHT_MODEL, INFO, 'bg_')

    def test_bulb_worker_applies_events_and_shutdown_uses_main_channel(self):
        self._worker_applies_events(YEELIGHT_BULB_MODEL, BULB_INFO, '')

    def test_no_firmware_or_usb_operations_for_original_firmware_device(self):
        self.assertEqual(hardware_model(YEELIGHT_MODEL)['connection'], 'yeelight')
        with self.assertRaisesRegex(EventError, '原厂'):
            firmware_archive(YEELIGHT_MODEL)
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            engine.update_settings({'mode': 'hardware'})
            fleet = FleetTransportManager(engine.flame, threading.RLock(), {'devices': {}})
            service = FleetDeviceService(fleet.config, directory, fleet, fleet.lock)
            for action in ('install', 'flash_base', 'provision', 'probe'):
                with self.subTest(action=action), self.assertRaisesRegex(EventError, '原厂'):
                    service.start({'hardware_model': YEELIGHT_MODEL, 'action': action, 'device_id': ''})

    def _fleet_connect(self, model_id, info):
        identity = info["device_id"]
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            engine.update_settings({'mode': 'hardware'})
            config = {'devices': {'matrix': {'device_id': 'matrix', 'hardware_model': 'ai-flame-v01', 'connection_paused': True}}}
            lock = threading.RLock()
            fleet = FleetTransportManager(engine.flame, lock, config)
            service = FleetDeviceService(config, directory, fleet, lock)
            other = fleet.connection('matrix')
            other.stop = Mock()
            client = Mock()
            client.command.return_value = {'id': 1, 'result': ['off', '20', '0']}
            with patch('aiflame.devices.yeelight.discover', return_value=[info]), \
                 patch('aiflame.devices.yeelight.YeelightClient', return_value=client), \
                 patch('aiflame.transports.manager.YeelightTransport') as transport:
                service.start({'action': 'discover', 'hardware_model': model_id, 'device_id': ''})
                service.thread.join(2)
                self.assertEqual(service.job['state'], 'succeeded')
                self.assertEqual(service.job['result']['devices'], [info])
                other.stop.assert_not_called()
                service.start({'action': 'connect', 'hardware_model': model_id, 'device_id': '', 'transport': 'yeelight', 'host': HOST})
                service.thread.join(2)
                self.assertEqual(service.job['state'], 'succeeded', service.job)
                self.assertEqual(service.job['result']['device_id'], identity)
                transport.assert_called_once()
                other.stop.assert_not_called()
                saved = json.loads((Path(directory) / 'credentials.json').read_text())
                self.assertEqual(saved['devices'][identity]['yeelight_host'], HOST)
                self.assertEqual(service.status(model_id, identity)['transport'], 'yeelight')
                self.assertEqual(fleet.records['matrix']['hardware_model'], 'ai-flame-v01')
                changed = {**info, 'device_id': 'yeelight:another'}
                with patch('aiflame.devices.yeelight.discover', return_value=[changed]):
                    service.start({'action': 'connect', 'hardware_model': model_id, 'device_id': identity, 'transport': 'yeelight', 'host': HOST})
                    service.thread.join(2)
                self.assertEqual(service.job['state'], 'failed')
                self.assertEqual(fleet.records[identity]['device_id'], identity)

    def test_fleet_connect_persists_identity_and_search_does_not_stop_other_lamps(self):
        self._fleet_connect(YEELIGHT_MODEL, INFO)

    def test_bulb_fleet_connect_persists_identity_and_keeps_other_lamps(self):
        self._fleet_connect(YEELIGHT_BULB_MODEL, BULB_INFO)

    def test_bulb_discovery_is_filtered_by_model_and_native_capabilities(self):
        bulb = parse_discovery(BULB_REPLY, HOST)
        self.assertEqual(bulb['model'], 'colorb')
        self.assertEqual(bulb['hardware_model'], YEELIGHT_BULB_MODEL)
        self.assertEqual(bulb['device_id'], BULB_ID)
        self.assertIsNone(parse_discovery(BULB_REPLY, HOST, YEELIGHT_MODEL))
        self.assertIsNone(parse_discovery(REPLY, HOST, YEELIGHT_BULB_MODEL))
        self.assertIsNone(parse_discovery(BULB_REPLY.replace(b'start_cf', b'no_flow'), HOST))
        self.assertIsNone(parse_discovery(BULB_REPLY.replace(b'colorb', b'color4'), HOST))
        with self.assertRaisesRegex(EventError, '原厂'):
            firmware_archive(YEELIGHT_BULB_MODEL)

    def test_bulb_rejects_screen_light_before_connecting_or_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            engine.update_settings({'mode': 'hardware'})
            config = {'devices': {}}
            fleet = FleetTransportManager(engine.flame, threading.RLock(), config)
            service = FleetDeviceService(config, directory, fleet, fleet.lock)
            with patch('aiflame.devices.yeelight.discover', return_value=[INFO]), \
                 patch('aiflame.devices.yeelight.YeelightClient') as client:
                service.start({'action': 'connect', 'hardware_model': YEELIGHT_BULB_MODEL,
                               'device_id': '', 'transport': 'yeelight', 'host': HOST})
                service.thread.join(2)
                self.assertEqual(service.job['state'], 'failed')
                self.assertIn('所选型号', service.job['message'])
                self.assertEqual(fleet.records, {})
                client.assert_not_called()

    def test_bulb_scene_fallback_and_off_never_use_background_commands(self):
        settings = {'brightness': 20, 'reduced': False}
        worker = YeelightTransport(Mock(settings=settings), threading.RLock(), {'hardware_model': YEELIGHT_BULB_MODEL})
        worker.command = Mock(return_value={'id': 1, 'result': ['ok']})
        desired = {'key': 'working', 'state': 'working', 'duration_ms': 30000}
        worker.support = {'set_scene'}
        worker.display(desired, settings)
        self.assertEqual(worker.command.call_args.args[0], 'set_scene')
        worker.command.reset_mock()
        worker.support = set()
        worker.display(desired, settings)
        self.assertEqual([call.args[0] for call in worker.command.call_args_list], ['set_power', 'start_cf'])
        worker.command.reset_mock()
        worker.display({'key': 'idle', 'state': 'idle'}, settings)
        self.assertEqual(worker.command.call_args.args, ('set_power', ['off', 'sudden', 0]))


if __name__ == '__main__':
    unittest.main()
