"""Cube GUI restarts reuse a confirmed endpoint instead of accumulating sessions."""
import errno
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from aiflame.core.paths import write_private
from aiflame.devices.yeelight import CubeClient


HOST = '192.168.1.44'


class SessionSocket:
    def __init__(self, *, rejected_token=None, bind_error=None, send_error=None, silent_token=None):
        self.rejected_token = rejected_token
        self.bind_error = bind_error
        self.send_error = send_error
        self.silent_token = silent_token
        self.clock = [100.0]
        self.port = 54001
        self.bound = None
        self.sent = []
        self.incoming = []
        self.closed = False
        self.before_send = None

    def bind(self, address):
        if self.bind_error:
            raise self.bind_error
        self.bound = address
        self.port = address[1]

    def connect(self, address):
        self.address = address

    def getsockname(self):
        return ('192.168.1.10', self.port)

    def settimeout(self, seconds):
        pass

    def send(self, wire):
        if self.send_error:
            raise self.send_error
        packet = json.loads(wire)
        if self.before_send:
            self.before_send(packet)
        self.sent.append(packet)
        if self.silent_token is not None and packet.get('token') == self.silent_token:
            return
        if packet['method'] == 'update_leds':
            return
        if packet.get('token') == self.rejected_token and self.rejected_token is not None:
            response = {'id': packet['id'], 'error': {'code': -1, 'message': 'invalid parameter'}}
        elif packet['method'] == 'udp_sess_new':
            response = {'id': packet['id'], 'params': {'token': 'replacement-session'}}
        else:
            response = {'id': packet['id'], 'result': ['ok']}
        self.incoming.append(json.dumps(response).encode())

    def recv(self, size):
        if not self.incoming:
            self.clock[0] += .05
            raise socket.timeout()
        return self.incoming.pop(0)

    def close(self):
        self.closed = True


class CubeSessionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'sessions' / 'physical-lamp.json'

    def save(self, **changes):
        saved = {'format_version': 1, 'host': HOST, 'local_port': 54999,
                 'token': 'previous-session', 'next_sequence': 65536}
        write_private(self.path, {**saved, **changes})

    def open(self, sock):
        client = CubeClient(HOST, session_path=self.path)
        with patch('aiflame.devices.yeelight.socket.socket', return_value=sock):
            client.open()
        self.addCleanup(client.close)
        return client

    def test_first_connection_saves_private_endpoint_and_reserved_ids(self):
        sock = SessionSocket()
        client = self.open(sock)
        self.assertFalse(client.session_reused)
        self.assertEqual([p['method'] for p in sock.sent], ['udp_sess_new', 'udp_sess_keep_alive'])
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['local_port'], sock.port)
        self.assertEqual(saved['token'], 'replacement-session')
        self.assertGreater(saved['next_sequence'], sock.sent[-1]['id'])
        if os.name != 'nt':
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_restart_reuses_endpoint_token_and_skips_reserved_request_ids(self):
        self.save()
        first = SessionSocket()
        client = self.open(first)
        client.close()
        next_range = json.loads(self.path.read_text())['next_sequence']
        second = SessionSocket()
        self.assertTrue(self.open(second).session_reused)
        for sock in (first, second):
            self.assertEqual(sock.bound, ('', 54999))
            self.assertEqual([p['method'] for p in sock.sent], ['udp_sess_keep_alive'])
            self.assertEqual(sock.sent[0]['token'], 'previous-session')
        self.assertGreater(second.sent[0]['id'], next_range)
        self.assertGreater(next_range, first.sent[0]['id'])

    def test_evicted_token_performs_one_handshake_on_same_endpoint(self):
        self.save()
        sock = SessionSocket(rejected_token='previous-session')
        self.assertFalse(self.open(sock).session_reused)
        self.assertEqual(sock.bound, ('', 54999))
        self.assertEqual([p['method'] for p in sock.sent],
                         ['udp_sess_keep_alive', 'udp_sess_new', 'udp_sess_keep_alive'])
        self.assertNotIn('token', sock.sent[1])
        self.assertEqual(json.loads(self.path.read_text())['token'], 'replacement-session')

    def test_occupied_endpoint_never_creates_a_parallel_session(self):
        self.save()
        before = self.path.read_bytes()
        sock = SessionSocket(bind_error=OSError(errno.EADDRINUSE, 'occupied endpoint'))
        with self.assertRaises(OSError):
            self.open(sock)
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)
        self.assertEqual(self.path.read_bytes(), before)

    def test_silent_old_token_retries_same_id_then_handshakes_once(self):
        self.save()
        sock = SessionSocket(silent_token='previous-session')
        with patch('aiflame.devices.yeelight.time.monotonic', side_effect=lambda: sock.clock[0]):
            self.open(sock)
        self.assertEqual(sock.bound, ('', 54999))
        self.assertEqual([p['method'] for p in sock.sent],
                         ['udp_sess_keep_alive'] * 3 + ['udp_sess_new', 'udp_sess_keep_alive'])
        self.assertEqual(sock.sent[:3], [sock.sent[0]] * 3)
        self.assertEqual(json.loads(self.path.read_text())['token'], 'replacement-session')

    def test_send_failure_does_not_rotate_a_valid_session(self):
        self.save()
        sock = SessionSocket(send_error=OSError(errno.EHOSTUNREACH, 'no route'))
        with self.assertRaises(OSError):
            self.open(sock)
        self.assertEqual(sock.sent, [])
        self.assertTrue(sock.closed)
        self.assertEqual(json.loads(self.path.read_text())['token'], 'previous-session')

    def test_request_range_is_persisted_before_first_send_in_new_range(self):
        self.save()
        sock = SessionSocket()
        client = self.open(sock)
        client.seq = client.reserved_sequence - 1
        observed = []
        def before_send(packet):
            saved = json.loads(self.path.read_text())
            observed.append((saved['next_sequence'], packet['id']))
        sock.before_send = before_send
        client.udp_command('update_leds', ['frame'], acknowledge=False)
        self.assertEqual(len(observed), 1)
        self.assertGreater(observed[0][0], observed[0][1])

    def test_dhcp_change_keeps_the_physical_lamps_original_endpoint(self):
        self.save(host='192.168.1.45')
        sock = SessionSocket()
        self.open(sock)
        self.assertEqual(sock.address, (HOST, 55444))
        self.assertEqual(sock.bound, ('', 54999))
        self.assertEqual([p['method'] for p in sock.sent], ['udp_sess_keep_alive'])
        self.assertEqual(json.loads(self.path.read_text())['host'], HOST)

    def test_bad_cache_cannot_reuse_an_unvalidated_endpoint(self):
        for changes in ({'host': None}, {'local_port': True}, {'token': ''},
                        {'next_sequence': False}, {'format_version': 2}):
            with self.subTest(changes=changes):
                self.save(**changes)
                sock = SessionSocket()
                self.open(sock)
                self.assertIsNone(sock.bound)
                self.assertEqual(sock.sent[0]['method'], 'udp_sess_new')
        self.path.write_text('{broken')
        sock = SessionSocket()
        self.open(sock)
        self.assertEqual(sock.sent[0]['method'], 'udp_sess_new')

    def test_stopped_client_does_not_send_or_create_a_session(self):
        stop = threading.Event()
        stop.set()
        client = CubeClient(HOST, stop, session_path=self.path)
        with patch('aiflame.devices.yeelight.socket.socket') as create:
            with self.assertRaises(OSError):
                client.open()
        create.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_malformed_session_reply_is_recoverable_and_closes_socket(self):
        for params in (None, [], 'bad', {}):
            with self.subTest(params=params):
                sock = SessionSocket()
                client = CubeClient(HOST, session_path=self.path)
                with patch('aiflame.devices.yeelight.socket.socket', return_value=sock), \
                     patch.object(client, 'udp_command', return_value={'id': 1, 'params': params}):
                    with self.assertRaises(ValueError):
                        client.open()
                self.assertTrue(sock.closed)
                self.assertFalse(self.path.exists())

    def test_rejected_startup_power_off_cannot_raise_brightness_or_start_display(self):
        sock = SessionSocket()
        client = self.open(sock)
        def reject_off(packet):
            if packet['method'] == 'set_power':
                sock.incoming.append(json.dumps({'id': packet['id'], 'error': {'code': 1}}).encode())
        sock.before_send = reject_off
        with patch('select.select', return_value=([], [], [])), self.assertRaises(ValueError):
            client.prepare_frame()
        methods = [p['method'] for p in sock.sent]
        self.assertNotIn('set_bright', methods)
        self.assertNotIn('activate_fx_mode', methods)
        self.assertNotIn('update_leds', methods)
        self.assertFalse(client.direct)

    def test_stop_during_startup_off_never_continues_to_brightness_or_power_on(self):
        sock = SessionSocket()
        client = self.open(sock)
        stop = threading.Event()
        client.stopping = stop
        def stop_at_off(packet):
            if packet['method'] == 'set_power':
                stop.set()
        sock.before_send = stop_at_off
        with patch('select.select', return_value=([], [], [])), self.assertRaises(OSError):
            client.prepare_frame()
        self.assertEqual([p['method'] for p in sock.sent],
                         ['udp_sess_new', 'udp_sess_keep_alive', 'set_power'])
        self.assertEqual(sock.sent[-1]['params'], ['off', 'sudden', 0])
        self.assertFalse(client.direct)

    def deletion_fixture(self):
        from aiflame.core.engine import Engine
        from aiflame.devices.fleet import FleetDeviceService
        from aiflame.devices.models import YEELIGHT_CUBE_MODEL
        from aiflame.devices.yeelight import cube_session_path
        from aiflame.transports.fleet import FleetTransportManager
        engine = Engine(':memory:')
        self.addCleanup(engine.db.close)
        runtime = self.path.parent.parent
        config = {'devices': {key: {'device_id': key, 'hardware_model': YEELIGHT_CUBE_MODEL,
                                   'connection_paused': True} for key in ('cube-one', 'cube-two')}}
        lock = threading.RLock()
        fleet = FleetTransportManager(engine.flame, lock, config)
        self.addCleanup(fleet.stop)
        service = FleetDeviceService(config, runtime, fleet, lock)
        paths = [cube_session_path(runtime, key) for key in config['devices']]
        for path in paths:
            write_private(path, {'owned_test_cache': True})
        return service, config, paths, runtime

    def test_delete_removes_only_selected_physical_devices_session_cache(self):
        from aiflame.devices.models import YEELIGHT_CUBE_MODEL
        service, config, paths, _ = self.deletion_fixture()
        service.delete({'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube-one'})
        self.assertFalse(paths[0].exists())
        self.assertTrue(paths[1].exists())
        self.assertNotIn('cube-one', config['devices'])
        self.assertIn('cube-two', config['devices'])

    def test_cache_cleanup_failure_preserves_device_for_retry(self):
        from aiflame.devices.models import YEELIGHT_CUBE_MODEL
        service, config, paths, runtime = self.deletion_fixture()
        denied = Mock()
        denied.unlink.side_effect = PermissionError('denied')
        with patch('aiflame.devices.yeelight.cube_session_path', return_value=denied), self.assertRaises(OSError):
            service.delete({'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube-one'})
        self.assertIn('cube-one', config['devices'])
        self.assertIn('cube-one', json.loads((runtime / 'credentials.json').read_text())['devices'])
        self.assertTrue(paths[0].exists())


if __name__ == '__main__':
    unittest.main()
