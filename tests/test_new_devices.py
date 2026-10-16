"""New models: discovery, safe cloud routing, persistence and Cube UDP frames."""
import base64
import json
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from aiflame.core.events import EventError
from aiflame.core.engine import Engine
from aiflame.devices.models import MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL, YEELIGHT_CUBE_MODEL, hardware_model
from aiflame.devices.light_settings import light_settings, validate_patch, migrate_light_settings
from aiflame.devices.mijia_cloud import QRCloud
from aiflame.devices.fleet import FleetDeviceService
from aiflame.devices.yeelight import CubeClient, parse_discovery, verify_control, YeelightQuotaError, raise_command_error
from aiflame.transports.fleet import FleetTransportManager
from aiflame.transports.yeelight import YeelightTransport
from aiflame.transports.mijia import desired_power


class NewDeviceTests(unittest.TestCase):
    def test_both_plugs_migrate_disabled_alerts_and_keep_state_rules(self):
        for model in (MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL):
            with self.subTest(model=model):
                record = {'hardware_model': model, 'light_settings': {
                    'enabled': False, 'state_power': {'working': True, 'done': False}}}
                self.assertEqual(light_settings(record)['brightness'], 100)
                migrate_light_settings(record)
                self.assertNotIn('enabled', record['light_settings'])
                settings = light_settings(record)
                self.assertTrue(desired_power({'state': 'working'}, settings))
                self.assertFalse(desired_power({'state': 'done'}, settings))
                self.assertTrue(desired_power({'state': 'needs_input'}, settings))


    def test_cube_drops_frame_if_state_changes_during_initialization(self):
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        old = {'key': 'old', 'state': 'working'}
        engine = Mock(settings=settings)
        engine.desired.return_value = old
        worker = YeelightTransport(engine, threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.client = Mock()
        def initialize():
            engine.desired.return_value = {'key': 'new', 'state': 'done'}
        worker.client.prepare_frame.side_effect = initialize
        render = Mock(return_value=['old-frame'])
        self.assertIsNone(worker.command('update_leds', None, desired=old, settings=settings,
                                         params_factory=render))
        render.assert_not_called()
        worker.client.command.assert_not_called()

    def test_cube_renders_after_rate_wait_and_setup(self):
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        desired = {'key': 'current', 'state': 'working'}
        engine = Mock(settings=settings)
        engine.desired.return_value = desired
        worker = YeelightTransport(engine, threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.client = Mock()
        worker.client.command.return_value = {'id': 1, 'result': ['sent']}
        order = []
        worker.stopping = Mock()
        worker.stopping.is_set.return_value = False
        worker.stopping.wait.side_effect = lambda delay: order.append('wait')
        worker.client.prepare_frame.side_effect = lambda: order.append('setup')
        def render():
            order.append('render')
            return ['fresh-frame']
        worker.command('update_leds', None, desired=desired, settings=settings, params_factory=render)
        self.assertEqual(order, ['wait', 'setup', 'render'])
        worker.client.command.assert_called_once_with('update_leds', ['fresh-frame'])

    def test_cube_lost_heartbeat_does_not_pause_or_replay_frames(self):
        client = CubeClient('192.168.1.44')
        client.udp = Mock()
        client.token = 'session'
        client.direct = True
        with patch('select.select', return_value=([], [], [])) as ready, \
             patch('aiflame.devices.yeelight.time.monotonic', return_value=100) as clock:
            for i in range(10):
                clock.return_value = 100 + i / 10
                client.command('update_leds', [str(i)])
            packets = [json.loads(call.args[0]) for call in client.udp.send.call_args_list]
            self.assertEqual([p['params'] for p in packets if p['method'] == 'update_leds'],
                             [[str(i)] for i in range(10)])
            beats = [p for p in packets if p['method'] == 'udp_sess_keep_alive']
            self.assertEqual(len(beats), 3)
            self.assertEqual(beats, [beats[0]] * 3)
            client.udp.recv.assert_not_called()
            # Frame IDs have advanced; the delayed heartbeat still matches its
            # own stored ID and unrelated ACKs cannot confirm it.
            client.udp.recv.side_effect = [json.dumps({'id': 999, 'result': ['ok']}).encode(),
                                          json.dumps({'id': beats[0]['id'], 'result': ['ok']}).encode()]
            ready.side_effect = [([client.udp], [], []), ([], [], []),
                                 ([client.udp], [], []), ([], [], [])]
            client.check_connection()
            self.assertIsNotNone(client.pending_heartbeat)
            client.check_connection()
            self.assertIsNone(client.pending_heartbeat)
            self.assertEqual(client.last_keep_alive, 100.9)

    def test_cube_frame_clock_does_not_accumulate_render_cost_or_catch_up(self):
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        desired = {'key': 'current', 'state': 'working'}
        engine = Mock(settings=settings)
        engine.desired.return_value = desired
        worker = YeelightTransport(engine, threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.client = Mock()
        worker.client.command.return_value = {'id': 1, 'result': ['sent']}
        now = [100.0]
        starts = []
        worker.stopping = Mock()
        worker.stopping.is_set.return_value = False
        def wait(delay):
            now[0] += delay
            return False
        worker.stopping.wait.side_effect = wait
        cost = [.025]
        def render():
            starts.append(now[0])
            now[0] += cost[0]
            return ['frame']
        worker.next_command = now[0]
        with patch('aiflame.transports.yeelight.time.monotonic', side_effect=lambda: now[0]):
            for _ in range(20):
                worker.command('update_leds', None, desired=desired, settings=settings, params_factory=render)
            self.assertAlmostEqual(starts[-1] - starts[0], 1.9)
            cost[0] = .25
            worker.command('update_leds', None, desired=desired, settings=settings, params_factory=render)
            self.assertAlmostEqual(worker.next_command - now[0], .1)

    def test_cube_retries_lost_heartbeat_with_same_request_id(self):
        client = CubeClient('192.168.1.44')
        client.udp = Mock()
        client.token = 'session'
        now = [0]
        def receive(size):
            if now[0] == 0:
                now[0] = .3
                raise socket.timeout()
            return b'{"id":1,"result":["ok"]}'
        client.udp.recv.side_effect = receive
        with patch('aiflame.devices.yeelight.time.monotonic', side_effect=lambda: now[0]):
            client.keep_alive()
        sent = [call.args[0] for call in client.udp.send.call_args_list]
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0], sent[1])

    def test_cube_frames_are_rate_limited_and_stale_frames_are_dropped(self):
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        engine = Mock(settings=settings)
        worker = YeelightTransport(engine, threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.client = Mock()
        worker.client.command.return_value = {'id': 1, 'result': ['sent']}
        worker.stopping = Mock()
        worker.stopping.wait.return_value = False
        with patch('aiflame.transports.yeelight.time.monotonic', return_value=100):
            worker.command('update_leds', ['one'])
            worker.command('update_leds', ['two'])
        self.assertAlmostEqual(worker.stopping.wait.call_args.args[0], .1)
        engine.desired.return_value = {'key': 'new', 'state': 'idle'}
        worker.client.command.reset_mock()
        self.assertIsNone(worker.command('update_leds', ['stale'],
                          desired={'key': 'old', 'state': 'working'}, settings=settings))
        worker.client.command.assert_not_called()

    def test_cube_duplicate_frames_keep_health_checks_and_periodic_refresh(self):
        client = CubeClient('192.168.1.44')
        client.direct = True
        with patch.object(client, 'check_connection') as check, \
             patch.object(client, 'udp_command', return_value={'id': 1, 'result': ['sent']}) as send, \
             patch('aiflame.devices.yeelight.time.monotonic', return_value=100) as clock:
            client.command('update_leds', ['same'])
            client.command('update_leds', ['same'])
            send.assert_called_once()
            self.assertEqual(check.call_count, 2)
            clock.return_value = 103
            client.command('update_leds', ['same'])
            self.assertEqual(send.call_count, 2)
            check.side_effect = YeelightQuotaError('quota')
            with self.assertRaises(YeelightQuotaError):
                client.command('update_leds', ['same'])
        client.close()
        self.assertIsNone(client.last_frame)

    def test_quota_error_cools_down_instead_of_reconnecting_every_three_seconds(self):
        with self.assertRaises(YeelightQuotaError):
            raise_command_error({'code': -1, 'message': 'client quota exceeded'})
        engine = Mock(device={})
        worker = YeelightTransport(engine, threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.connect = Mock(side_effect=YeelightQuotaError('quota'))
        worker.stopping = Mock()
        worker.stopping.is_set.side_effect = [False, False, True]
        worker.run()
        worker.stopping.wait.assert_called_once_with(65)
        worker.connect.assert_called_once()

    def test_cube_control_requires_real_setter_ack_not_state_query(self):
        client = Mock()
        client.command.return_value = {'result': ['ok']}
        verify_control(client, YEELIGHT_CUBE_MODEL)
        client.command.assert_called_once_with('set_bright', [100])
        for response in ({'result': ['sent']}, {'result': []}, {'error': {'code': -1}}):
            client.command.return_value = response
            with self.assertRaises(ValueError):
                verify_control(client, YEELIGHT_CUBE_MODEL)
        client.command.side_effect = TimeoutError('no reply')
        with self.assertRaises(TimeoutError):
            verify_control(client, YEELIGHT_CUBE_MODEL)

    def test_cube_add_and_reconnect_without_get_prop(self):
        info = {'device_id': 'yeelight:cube', 'host': '192.168.1.44', 'port': 55443,
                'model': 'CubeLite', 'hardware_model': YEELIGHT_CUBE_MODEL,
                'firmware_version': '1.0.0', 'support': []}
        client = Mock()
        client.open.return_value = client
        def command(method, params):
            if method == 'get_prop':
                raise TimeoutError('Cube firmware does not reply')
            return {'id': 1, 'result': ['ok']}
        client.command.side_effect = command
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            engine.update_settings({'hardware_notifications': True})
            config = {'devices': {}}
            lock = threading.RLock()
            fleet = FleetTransportManager(engine.flame, lock, config)
            service = FleetDeviceService(config, directory, fleet, lock)
            with patch('aiflame.devices.yeelight.discover', return_value=[info]), \
                 patch('aiflame.devices.yeelight.CubeClient', return_value=client), \
                 patch('aiflame.transports.manager.YeelightTransport'):
                service.start({'action': 'connect', 'hardware_model': YEELIGHT_CUBE_MODEL,
                               'device_id': '', 'transport': 'yeelight', 'host': info['host']})
                service.thread.join(2)
                self.assertEqual(service.job['state'], 'succeeded', service.job)
                saved = json.loads((Path(directory) / 'credentials.json').read_text())
                self.assertIn(info['device_id'], saved['devices'])
            output = Mock(settings=light_settings({'hardware_model': YEELIGHT_CUBE_MODEL}))
            worker = YeelightTransport(output, lock, saved['devices'][info['device_id']])
            with patch('aiflame.transports.yeelight.discover', return_value=[info]), \
                 patch('aiflame.transports.yeelight.CubeClient', return_value=client):
                worker.connect()
                worker.next_command = 0
                worker.connect()
            self.assertTrue(output.device['online'])
            self.assertTrue(all(call.args[0] == 'set_bright' for call in client.command.call_args_list))

    def test_cube_empty_capabilities_broadcast_is_discoverable(self):
        reply = (b'HTTP/1.1 200 OK\r\nLocation: yeelight://192.168.5.59:55443\r\n'
                 b'id: 8b366b4e6f21\r\nmodel: CubeLite\r\nfw_ver: 1.0.0\r\nsupport: \r\n')
        row = parse_discovery(reply, '192.168.5.59', YEELIGHT_CUBE_MODEL)
        self.assertEqual(row['host'], '192.168.5.59')
        self.assertEqual(row['support'], [])
        self.assertIsNone(parse_discovery(reply, '192.168.5.60'))
        self.assertIsNone(parse_discovery(reply.replace(b'CubeLite', b'lamp15'), '192.168.5.59'))
        self.assertIsNone(parse_discovery(reply.replace(b':55443', b':1234'), '192.168.5.59'))
        self.assertIsNone(parse_discovery(reply.replace(b'id: 8b366b4e6f21', b'id: '), '192.168.5.59'))

    def test_cloud_keeps_both_plug_models_and_import_routes_selected_identity(self):
        cloud = QRCloud()
        cloud.user_id = '42'
        token = '1234567890abcdef' * 2
        devices = [{'did': str(i), 'model': model, 'token': token} for i, model in
                   enumerate(('cuco.plug.v3', 'xiaomi.plug.04', 'other.plug.4'), 1)]
        with patch.object(cloud, 'api', side_effect=[{'homelist': [{'id': 1}]}, {'device_info': devices}]):
            rows = cloud.plugs('cn', threading.Event())
        self.assertEqual(set(rows), {'1', '2'})
        self.assertEqual(rows['1']['hardware_model'], MIJIA_PLUG_MODEL)
        self.assertEqual(rows['2']['hardware_model'], MIJIA_PLUG4_MODEL)
        service = FleetDeviceService.__new__(FleetDeviceService)
        service.transport = Mock()
        service.transport.engine.settings = {'hardware_notifications': True}
        service.mijia = Mock()
        service.mijia.take.return_value = rows['2']
        service.busy = lambda: False
        service.start = Mock(return_value={})
        service.mijia_action({'action': 'import', 'id': 'session', 'did': '2', 'host': ''})
        self.assertEqual(service.start.call_args.args[0]['hardware_model'], MIJIA_PLUG4_MODEL)
        self.assertEqual(service.start.call_args.args[0]['did'], '2')
        self.assertEqual(rows['2'], {})  # Consumed credential is cleared.

    def test_new_settings_persist_independently_and_plug4_uses_switch_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            config = {'devices': {key: {'device_id': key, 'hardware_model': model, 'connection_paused': True}
                                 for key, model in [('cube', YEELIGHT_CUBE_MODEL), ('plug', MIJIA_PLUG4_MODEL)]}}
            fleet = FleetTransportManager(engine.flame, threading.RLock(), config)
            service = FleetDeviceService(config, directory, fleet, fleet.lock)
            for values in ({'colors': {'working': '#123456'}}, {'reminder_mode': 'front',
                           'front_state_temperature': {'working': 4200}}, {'reminder_mode': 'back'}):
                service.update_light_settings({'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube', 'settings': values})
            service.update_light_settings({'hardware_model': MIJIA_PLUG4_MODEL, 'device_id': 'plug',
                                           'settings': {'state_power': {'working': False, 'done': True}}})
            saved = json.loads((Path(directory) / 'credentials.json').read_text())
            settings = light_settings(saved['devices']['cube'])
            self.assertEqual(settings['colors']['working'], '#123456')
            self.assertEqual(settings['front_state_temperature']['working'], 4200)
            plug = light_settings(saved['devices']['plug'])
            self.assertFalse(desired_power({'state': 'working'}, plug))
            self.assertTrue(desired_power({'state': 'done'}, plug))
            self.assertFalse(desired_power({'state': 'idle'}, plug))
            with self.assertRaises(EventError):
                validate_patch({'enabled': False}, MIJIA_PLUG4_MODEL)

    def test_cube_discovery_is_strict_and_frames_have_exactly_100_pixels(self):
        model = hardware_model(YEELIGHT_CUBE_MODEL)
        reply = ('HTTP/1.1 200 OK\r\nLocation: yeelight://192.168.1.44:55443\r\n'
                 'id: cube123\r\nmodel: cubelite\r\nsupport: ' + ' '.join(model['required_methods']) + '\r\n').encode()
        self.assertEqual(parse_discovery(reply, '192.168.1.44')['hardware_model'], YEELIGHT_CUBE_MODEL)
        self.assertIsNone(parse_discovery(reply.replace(b'cubelite', b'unknown'), '192.168.1.44'))
        self.assertIsNone(parse_discovery(reply.replace(b'update_leds', b''), '192.168.1.44'))
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        worker = YeelightTransport(Mock(settings=settings), threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.command = Mock(return_value={'id': 1, 'result': ['sent']})
        for mode in ('front', 'back'):
            settings['reminder_mode'] = mode
            for state in ('working', 'done', 'needs_input', 'idle'):
                worker.display({'key': state, 'state': state, 'duration_ms': 3000}, settings)
                payload = base64.b64decode(worker.command.call_args.kwargs["params_factory"]()[0])
                self.assertEqual(len(payload), 300)
                self.assertEqual(payload, payload[:3] * 100)
                self.assertEqual(payload == bytes(300), state == 'idle')

    def test_cube_udp_session_acknowledges_setup_and_sends_100_pixel_frames(self):
        class FakeUDP:
            def __init__(self):
                self.sent, self.pending = [], []
                self.closed = False
            def connect(self, address): self.address = address
            def settimeout(self, timeout): pass
            def close(self): self.closed = True
            def send(self, data):
                packet = json.loads(data)
                self.sent.append(packet)
                if packet['method'] == 'udp_sess_new':
                    self.pending.append({'id': packet['id'], 'params': {'token': 'session'}})
                elif packet['method'] != 'update_leds':
                    self.pending.append({'id': packet['id'], 'result': ['ok']})
            def recv(self, size): return json.dumps(self.pending.pop(0)).encode()
        udp = FakeUDP()
        client = CubeClient('192.168.1.44')
        with patch('aiflame.devices.yeelight.socket.socket', return_value=udp), \
             patch('aiflame.devices.yeelight.socket.create_connection') as tcp, \
             patch('select.select', return_value=([], [], [])):
            client.open()
            result = client.command('update_leds', [base64.b64encode(bytes(300)).decode()])
            self.assertEqual(udp.address, ('192.168.1.44', 55444))
            self.assertEqual([p['method'] for p in udp.sent],
                             ['udp_sess_new', 'udp_sess_keep_alive', 'activate_fx_mode',
                              'set_bright', 'set_power', 'update_leds'])
            self.assertTrue(all(p['token'] == 'session' for p in udp.sent[1:]))
            self.assertEqual(result['result'], ['sent'])
            tcp.assert_not_called()
            client.last_keep_alive = 0
            client.check_connection()
            self.assertEqual(udp.sent[-1]['method'], 'udp_sess_keep_alive')
        client.close()
        self.assertTrue(udp.closed)
        self.assertIsNone(client.token)
        self.assertFalse(client.direct)

    def test_cube_async_error_is_not_hidden_by_heartbeat_id(self):
        client = CubeClient('192.168.1.44')
        client.udp = Mock()
        client.udp.recv.return_value = b'{"id":999,"error":{"code":-1,"message":"client quota exceeded"}}'
        with self.assertRaises(YeelightQuotaError):
            client.keep_alive()

    def test_cube_udp_errors_and_missing_heartbeat_fail_connection(self):
        client = CubeClient('192.168.1.44')
        client.udp = Mock()
        client.udp.recv.return_value = b'{"error":{"code":6}}'
        with patch('select.select', return_value=([client.udp], [], [])), self.assertRaises(ValueError):
            client.check_connection()
        client.last_keep_alive = 0
        with patch('select.select', return_value=([], [], [])), \
             patch('aiflame.devices.yeelight.time.monotonic', return_value=100) as clock:
            client.check_connection()
            client.udp.recv.assert_called_once()  # Only the preceding error packet.
            clock.return_value = 102
            with self.assertRaises(TimeoutError):
                client.check_connection()
