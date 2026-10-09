"""Yeelight wire framing, native effects, physical identity and fleet isolation."""
import json
import socket
from contextlib import nullcontext
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.integrations.registry import SOURCES
from aiflame.core.events import EventError, make_event
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


class DiscoverySocket:
    """Virtual-time UDP socket: timeout advances the clock without sleeping."""
    def __init__(self, on_send=None, stopping=None):
        self.now = 0.0
        self.timeout = 0.0
        self.events = []
        self.sent = []
        self.on_send = on_send
        self.stopping = stopping
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def setsockopt(self, *args):
        pass

    def bind(self, address):
        self.bound = address

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, packet, address):
        self.sent.append((self.now, packet, address))
        if self.on_send:
            self.on_send(self, len(self.sent))

    def recvfrom(self, size):
        deadline = self.now + self.timeout
        self.events.sort(key=lambda item: item[0])
        if self.events and self.events[0][0] <= deadline:
            self.now, packet, host = self.events.pop(0)
            return packet, (host, 1982)
        self.now = deadline
        if self.stopping:
            self.stopping.set()
        raise socket.timeout()


class DiscoveryRetryTests(unittest.TestCase):
    def search(self, sock, *args, **kwargs):
        with patch('aiflame.devices.yeelight.socket.socket', return_value=sock) as create, \
             patch('aiflame.devices.yeelight.time.monotonic', side_effect=lambda: sock.now):
            rows = discover(*args, **kwargs)
        create.assert_called_once()
        self.assertTrue(sock.closed)
        return rows

    def test_first_two_queries_can_be_lost_without_repeating_ui_search(self):
        def respond(sock, attempt):
            if attempt == 3:
                sock.events.append((sock.now + .1, REPLY, HOST))
        sock = DiscoverySocket(respond)
        self.assertEqual(self.search(sock)[0]['device_id'], IDENTITY)
        self.assertEqual([round(item[0], 2) for item in sock.sent], [0, 1, 2])
        self.assertEqual(sock.now, 4.0)

    def test_late_reply_to_first_query_is_collected_on_original_socket(self):
        def respond(sock, attempt):
            if attempt == 1:
                sock.events.append((2.8, REPLY, HOST))
        sock = DiscoverySocket(respond)
        self.assertEqual(self.search(sock)[0]['device_id'], IDENTITY)
        self.assertEqual(len(sock.sent), 3)

    def test_lan_search_merges_duplicates_and_keeps_waiting_for_other_lamps(self):
        def respond(sock, attempt):
            sock.events.append((sock.now + .1, REPLY, HOST))
            if attempt == 3:
                sock.events.append((3.8, BULB_REPLY, HOST))
        sock = DiscoverySocket(respond)
        self.assertEqual({row['device_id'] for row in self.search(sock)}, {IDENTITY, BULB_ID})
        self.assertEqual(sock.now, 4.0)

    def test_targeted_search_retries_filters_invalid_replies_and_returns_promptly(self):
        def respond(sock, attempt):
            if attempt == 1:
                sock.events.extend([(.1, b'\xff', HOST), (.2, REPLY, '192.168.1.45'),
                                    (.3, BULB_REPLY, HOST)])
            if attempt == 2:
                sock.events.append((1.1, REPLY, HOST))
        sock = DiscoverySocket(respond)
        rows = self.search(sock, HOST, model_id=YEELIGHT_MODEL)
        self.assertEqual([row['device_id'] for row in rows], [IDENTITY])
        self.assertEqual(len(sock.sent), 2)
        self.assertAlmostEqual(sock.now, 1.1)
        self.assertTrue(all(item[2] == (HOST, 1982) for item in sock.sent))

    def test_empty_search_keeps_deadline_bounded_even_with_short_custom_timeout(self):
        for timeout in (4, .6):
            with self.subTest(timeout=timeout):
                sock = DiscoverySocket()
                self.assertEqual(self.search(sock, timeout=timeout), [])
                self.assertEqual(len(sock.sent), 3)
                self.assertAlmostEqual(sock.now, timeout)

    def test_reconnect_discovery_stops_without_waiting_for_full_search(self):
        stopping = threading.Event()
        sock = DiscoverySocket(stopping=stopping)
        self.assertEqual(self.search(sock, stopping=stopping), [])
        self.assertAlmostEqual(sock.now, .2)
        self.assertEqual(len(sock.sent), 1)
        with patch('aiflame.devices.yeelight.socket.socket') as create:
            self.assertEqual(discover(stopping=stopping), [])
        create.assert_not_called()


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

    def test_connection_errors_explain_control_port_failure(self):
        for error, message in ((TimeoutError(), '连接超时'), (ConnectionRefusedError(), '拒绝连接'),
                               (OSError('No route to host'), 'No route to host')):
            with self.subTest(error=type(error).__name__), \
                 patch('aiflame.devices.yeelight.socket.create_connection', side_effect=error):
                with self.assertRaisesRegex(OSError, message) as raised:
                    YeelightClient(HOST).open()
                self.assertIn(HOST + ':55443', str(raised.exception))

    def test_first_query_waits_for_lamp_command_handler(self):
        for stopping in (None, threading.Event()):
            with self.subTest(stoppable=stopping is not None):
                ready = threading.Event()
                sock = Mock()
                sock.sendall.side_effect = lambda packet: self.assertTrue(ready.is_set(), 'first command reached an unready lamp')
                sock.recv.return_value = b'{"id":1,"result":["on","100","917331"]}\r\n'
                def settled(delay):
                    self.assertGreaterEqual(delay, .5)
                    ready.set()
                    return False
                wait = patch.object(stopping, 'wait', side_effect=settled) if stopping else nullcontext()
                with patch('aiflame.devices.yeelight.socket.create_connection', return_value=sock), \
                     patch('aiflame.devices.yeelight.time.sleep', side_effect=settled), \
                     wait:
                    client = YeelightClient(HOST, stopping).open()
                    self.assertEqual(client.command('get_prop', ['power', 'bright', 'rgb'])['result'], ['on', '100', '917331'])
                    client.close()

    def test_shutdown_interrupts_connection_settle_and_closes_socket(self):
        stopping = threading.Event()
        stopping.set()
        sock = Mock()
        with patch('aiflame.devices.yeelight.socket.create_connection', return_value=sock):
            client = YeelightClient(HOST, stopping)
            with self.assertRaisesRegex(OSError, '服务已停止'):
                client.open()
        sock.close.assert_called_once()
        sock.sendall.assert_not_called()
        self.assertIsNone(client.sock)

    def test_silent_control_port_explains_missing_command_reply(self):
        client = YeelightClient(HOST)
        client.sock = Mock()
        client.sock.recv.side_effect = TimeoutError()
        with patch('aiflame.devices.yeelight.time.monotonic', side_effect=[0, 0, 3]):
            with self.assertRaisesRegex(TimeoutError, 'get_prop.*未收到回执') as raised:
                client.command('get_prop', ['power', 'bright', 'rgb'])
        self.assertIn(HOST + ':55443', str(raised.exception))
        self.assertIn('关闭再开启局域网控制', str(raised.exception))

    def test_native_flows_end_off_respect_brightness_reduced_and_hold(self):
        settings = {'brightness': 20, 'reduced': False}
        for state in ('working', 'done', 'error'):
            desired = {'key': state, 'state': state, 'duration_ms': 10000}
            count, action, expression = light_flow(desired, settings)
            self.assertEqual(action, 2)  # Official action 2 turns off; 0 restores previous state.
            values = list(map(int, expression.split(',')))
            tuples = [values[index:index + 4] for index in range(0, len(values), 4)]
            self.assertGreater(count, 0)
            self.assertEqual(tuples, [[50, 1, COLORS[state], 20],
                                      [(30000 if state == 'working' else 10000) - 50, 7, 0, 0]])
            total = sum(tuples[index % len(tuples)][0] for index in range(count))
            self.assertLessEqual(total, 30000 if state == 'working' else 10000)
            reduced = light_flow(desired, {**settings, 'reduced': True})
            self.assertEqual(reduced, [count, action, expression])
            self.assertEqual(reduced[0], 2)
            self.assertEqual(len(reduced[2].split(',')), 8)
        held = light_flow({'state': 'done', 'duration_ms': 0, 'holding': True}, {**settings, 'reduced': True})
        self.assertEqual(held, [2, 2, f'50,1,{COLORS["done"]},20,29950,7,0,0'])
        self.assertIsNone(light_flow({'state': 'idle'}, settings))
        self.assertIsNone(light_flow({'state': 'done'}, {**settings, 'brightness': 0}))
        preview = light_flow({'state': 'working', 'preview': 'preview', 'duration_ms': 5000}, {**settings, 'reduced': True})
        self.assertEqual(preview[2].split(',')[4], '4950')

    def test_brightness_reaches_full_range_on_both_models(self):
        from aiflame.devices.light_settings import light_settings
        for model in (YEELIGHT_MODEL, YEELIGHT_BULB_MODEL):
            self.assertEqual(light_settings({'hardware_model': model})['brightness'], 50)
            self.assertFalse(light_settings({'hardware_model': model, 'light_settings': {'reduced': True}})['reduced'])
            for state in ('working', 'done', 'error'):
                for brightness in (50, 100):
                    settings = {**light_settings({'hardware_model': model}), 'state_brightness': {state: brightness}}
                    flow = light_flow({'state': state, 'duration_ms': 10000}, settings)
                    self.assertEqual(int(flow[2].split(',')[3]), brightness)

    def test_custom_rgb_colors_reach_native_flow_for_every_state(self):
        for state, color in (("working", "#FF72B6"), ("done", "#22D3EE"), ("error", "#A78BFA")):
            for reduced in (False, True):
                with self.subTest(state=state, reduced=reduced):
                    flow = light_flow({"state": state, "duration_ms": 10000},
                                      {"brightness": 12, "reduced": reduced, "colors": {state: color}})
                    values = list(map(int, flow[2].split(',')))
                    rgb_steps = [values[i + 2] for i in range(0, len(values), 4) if values[i + 1] == 1]
                    self.assertTrue(rgb_steps)
                    self.assertEqual(values[:4], [50, 1, int(color[1:], 16), 12])
                    self.assertEqual(values[4:], [9950 if state != 'working' else 29950, 7, 0, 0])
                    self.assertEqual(set(rgb_steps), {int(color[1:], 16)})
                    self.assertEqual(flow[1], 2)

    def test_color_patch_is_independent_persistent_and_validated(self):
        from aiflame.devices.light_settings import COLOR_DEFAULTS, light_settings
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            config = {'devices': {
                IDENTITY: {'device_id': IDENTITY, 'hardware_model': YEELIGHT_MODEL, 'connection_paused': True},
                BULB_ID: {'device_id': BULB_ID, 'hardware_model': YEELIGHT_BULB_MODEL, 'connection_paused': True},
                'matrix': {'device_id': 'matrix', 'hardware_model': 'yeelight-yltd003', 'connection_paused': True}}}
            fleet = FleetTransportManager(engine.flame, threading.RLock(), config)
            service = FleetDeviceService(config, directory, fleet, fleet.lock)
            body = {'hardware_model': YEELIGHT_MODEL, 'device_id': IDENTITY}
            for model, identity in ((YEELIGHT_MODEL, IDENTITY), (YEELIGHT_BULB_MODEL, BULB_ID)):
                self.assertEqual(service.status(model, '')['light_settings']['brightness'], 50)
                self.assertEqual(service.status(model, identity)['light_settings']['brightness'], 50)
                service.update_light_settings({'hardware_model': model, 'device_id': identity,
                                               'settings': {'brightness': 100}})
                for value in (-1, 101, True):
                    with self.subTest(model=model, value=value), self.assertRaises(EventError):
                        service.update_light_settings({'hardware_model': model, 'device_id': identity,
                                                       'settings': {'brightness': value}})
            service.update_light_settings({**body, 'settings': {'colors': {'working': '#ff72b6'}}})
            service.update_light_settings({**body, 'settings': {'colors': {'done': '#22D3EE'}}})
            expected = {**COLOR_DEFAULTS, 'working': '#FF72B6', 'done': '#22D3EE'}
            self.assertEqual(light_settings(fleet.records[IDENTITY])['colors'], expected)
            self.assertEqual(light_settings(fleet.records[BULB_ID])['colors'], COLOR_DEFAULTS)
            saved = json.loads((Path(directory) / 'credentials.json').read_text())
            restarted = FleetTransportManager(engine.flame, fleet.lock, saved)
            self.assertEqual(restarted.outputs[IDENTITY].settings['colors'], expected)
            self.assertEqual(restarted.outputs[IDENTITY].settings['brightness'], 100)
            for colors in ({}, {'idle': '#112233'}, {'working': '#fff'}, {'done': 12}):
                with self.subTest(colors=colors), self.assertRaises(EventError):
                    service.update_light_settings({**body, 'settings': {'colors': colors}})
            with self.assertRaises(EventError):
                service.update_light_settings({'hardware_model': 'ai-flame-v01', 'device_id': 'matrix',
                                               'settings': {'colors': {'done': '#22D3EE'}}})

    def test_state_brightness_defaults_independence_persistence_and_validation(self):
        from aiflame.devices.light_settings import STATE_BRIGHTNESS_DEFAULTS, light_settings
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            config = {'devices': {
                IDENTITY: {'device_id': IDENTITY, 'hardware_model': YEELIGHT_MODEL, 'connection_paused': True},
                BULB_ID: {'device_id': BULB_ID, 'hardware_model': YEELIGHT_BULB_MODEL, 'connection_paused': True}}}
            fleet = FleetTransportManager(engine.flame, threading.RLock(), config)
            service = FleetDeviceService(config, directory, fleet, fleet.lock)
            for model, identity in ((YEELIGHT_MODEL, IDENTITY), (YEELIGHT_BULB_MODEL, BULB_ID)):
                body = {'hardware_model': model, 'device_id': identity}
                self.assertEqual(service.status(model, identity)['light_settings']['state_brightness'], STATE_BRIGHTNESS_DEFAULTS)
                service.update_light_settings({**body, 'settings': {'state_brightness': {'working': 35}}})
                service.update_light_settings({**body, 'settings': {'state_brightness': {'done': 80}}})
                values = light_settings(fleet.records[identity])
                self.assertEqual(values['state_brightness'], {'working': 35, 'done': 80, 'error': 50})
                for state, brightness in values['state_brightness'].items():
                    flow = light_flow({'state': state, 'duration_ms': 5000, 'device_test': True}, values)
                    self.assertEqual(int(flow[2].split(',')[3]), brightness)
                for patch_values in ({'working': -1}, {'done': 101}, {'error': True}, {'idle': 50}, {}):
                    with self.subTest(values=patch_values), self.assertRaises(EventError):
                        service.update_light_settings({**body, 'settings': {'state_brightness': patch_values}})
            saved = json.loads((Path(directory) / 'credentials.json').read_text())
            restarted = FleetTransportManager(engine.flame, fleet.lock, saved)
            self.assertEqual(restarted.outputs[IDENTITY].settings['state_brightness'], {'working': 35, 'done': 80, 'error': 50})
            service.update_light_settings({'hardware_model': YEELIGHT_MODEL, 'device_id': IDENTITY,
                                          'settings': {'state_brightness': {'working': 0}}})
            values = fleet.outputs[IDENTITY].settings
            self.assertIsNone(light_flow({'state': 'working', 'duration_ms': 5000}, values))
            self.assertIsNotNone(light_flow({'state': 'done', 'duration_ms': 5000}, values))
            self.assertEqual(fleet.outputs[BULB_ID].settings['state_brightness']['working'], 35)

    def test_display_only_controls_rear_channel_and_falls_back_when_scene_absent(self):
        engine = Mock(settings={'brightness': 20, 'reduced': False})
        worker = YeelightTransport(engine, threading.RLock(), {})
        desired = {'state': 'working', 'key': 'working', 'duration_ms': 30000}
        worker.command = Mock(return_value={'id': 1, 'result': ['ok']})
        worker.support = {'bg_set_scene'}
        worker.display(desired, engine.settings)
        self.assertEqual(worker.command.call_args.args[0], 'bg_set_scene')
        self.assertEqual(worker.command.call_args.args[1][0:3], ['cf', 2, 2])
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
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            engine.update_settings({'mode': 'hardware'})
            fleet = FleetTransportManager(engine.flame, threading.RLock(), {'devices': {}})
            service = FleetDeviceService(fleet.config, directory, fleet, fleet.lock)
            for action in ('install', 'flash_base', 'provision', 'probe'):
                with self.subTest(action=action), self.assertRaisesRegex(EventError, '不支持'):
                    service.start({'hardware_model': YEELIGHT_MODEL, 'action': action, 'device_id': ''})

    def _fleet_connect(self, model_id, info):
        identity = info["device_id"]
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(':memory:')
            self.addCleanup(engine.db.close)
            engine.update_settings({'mode': 'hardware'})
            config = {'devices': {'matrix': {'device_id': 'matrix', 'hardware_model': 'yeelight-yltd003', 'connection_paused': True}}}
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
                self.assertEqual(fleet.records['matrix']['hardware_model'], 'yeelight-yltd003')
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
