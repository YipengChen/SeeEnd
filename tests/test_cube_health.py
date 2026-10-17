"""Cube executor health must be distinct from a live UDP session."""
import base64
import json
import threading
import unittest
from unittest.mock import Mock, patch

from aiflame.devices import yeelight
from aiflame.devices.light_settings import light_settings
from aiflame.devices.models import YEELIGHT_CUBE_MODEL
from aiflame.transports.yeelight import YeelightTransport


class Datagram:
    def __init__(self, clock):
        self.clock = clock
        self.sent = []
        self.incoming = []
        self.received = []
        self.auto_ack = False
        self.fail_frames = False

    def send(self, wire):
        packet = json.loads(wire)
        self.sent.append((self.clock[0], packet))
        if packet['method'] == 'update_leds' and self.fail_frames:
            raise OSError('black frame send failed')
        if self.auto_ack and packet['method'] != 'update_leds':
            self.ack(packet)
        return len(wire)

    def recv(self, size):
        packet = self.incoming.pop(0)
        self.received.append(packet)
        return json.dumps(packet).encode()

    def ready(self, *_):
        return ([self] if self.incoming else [], [], [])

    def ack(self, packet):
        self.incoming.append({'id': packet['id'], 'result': ['ok']})

    def close(self):
        self.closed = True

    def packets(self, method):
        return [packet for _, packet in self.sent if packet['method'] == method]


class CubeHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.udp = Datagram(self.now)
        self.client = yeelight.CubeClient('192.168.1.44')
        self.client.udp = self.udp
        self.client.token = 'session'
        self.client.direct = True
        self.client.last_keep_alive = 100.0
        self.client.last_control_ack = 100.0
        self.clock = patch('aiflame.devices.yeelight.time.monotonic', side_effect=lambda: self.now[0])
        self.ready = patch('select.select', side_effect=self.udp.ready)
        self.clock.start()
        self.ready.start()
        self.addCleanup(self.clock.stop)
        self.addCleanup(self.ready.stop)

    def start_probe(self):
        self.now[0] = 160.0
        self.client.check_connection()
        probe = self.udp.packets('set_bright')
        self.assertEqual(len(probe), 1)
        return probe[0]

    def test_synchronous_controls_reject_malformed_ack_without_confirming_health(self):
        for method in ('set_bright', 'set_power', 'activate_fx_mode', 'udp_sess_keep_alive'):
            for payload in ({}, {'result': None}, {'result': []}, {'result': ['sent']},
                            {'result': ['unknown']}, {'result': 'ok'}):
                with self.subTest(method=method, payload=payload):
                    self.udp.incoming.append({'id': self.client.seq + 1, **payload})
                    with self.assertRaises(ValueError):
                        self.client.udp_command(method, [])
                    self.assertEqual(self.client.last_control_ack, 100.0)
                    self.assertEqual(self.client.last_keep_alive, 100.0)

    def test_synchronous_control_ignores_props_and_unrelated_replies(self):
        self.udp.incoming.extend([{'method': 'props', 'params': {'bright': '50'}},
                                  {'id': 9999, 'params': {}}, {'id': 1, 'result': ['ok']}])
        self.assertEqual(self.client.command('set_bright', [100]), {'id': 1, 'result': ['ok']})
        self.assertEqual(self.udp.incoming, [])

    def test_periodic_probe_is_asynchronous_and_requires_its_own_ack(self):
        self.now[0] = 159.99
        self.client.check_connection()
        self.assertEqual(self.udp.packets('set_bright'), [])
        # Complete the preceding session heartbeat before the control probe.
        for beat in self.udp.packets('udp_sess_keep_alive'):
            self.udp.ack(beat)
        probe = self.start_probe()
        self.assertEqual(probe['params'], [100, 'sudden', 0])
        self.assertEqual(probe['token'], 'session')
        self.assertIsNotNone(self.client.pending_control)
        self.assertEqual(self.client.last_control_ack, 100.0)
        self.now[0] = 160.1
        self.udp.ack(probe)
        self.client.check_connection()
        self.assertIsNone(self.client.pending_control)
        self.assertEqual(self.client.last_control_ack, 160.1)
        self.now[0] = 219.9
        self.client.last_keep_alive = self.now[0]
        self.client.check_connection()
        self.assertEqual(len(self.udp.packets('set_bright')), 1)

    def test_heartbeat_and_control_ack_can_arrive_in_either_order(self):
        for control_first in (False, True):
            with self.subTest(control_first=control_first):
                self.client.seq = 0
                self.client.last_keep_alive = self.client.last_control_ack = 100.0
                self.client.pending_heartbeat = self.client.pending_control = None
                self.udp.sent.clear()
                probe = self.start_probe()
                beat = self.udp.packets('udp_sess_keep_alive')[0]
                self.now[0] = 160.2
                for packet in ([probe, beat] if control_first else [beat, probe]):
                    self.udp.ack(packet)
                self.client.check_connection()
                self.assertIsNone(self.client.pending_heartbeat)
                self.assertIsNone(self.client.pending_control)
                self.assertEqual(self.client.last_keep_alive, 160.2)
                self.assertEqual(self.client.last_control_ack, 160.2)

    def test_synchronous_command_preserves_pending_health_acknowledgements(self):
        probe = self.start_probe()
        beat = self.udp.packets('udp_sess_keep_alive')[0]
        self.now[0] = 160.1
        self.udp.ack(beat)
        self.udp.ack(probe)
        self.udp.incoming.append({'id': self.client.seq + 1, 'result': ['ok']})
        response = self.client.command('set_bright', [100])
        self.assertEqual(response['result'], ['ok'])
        self.assertIsNone(self.client.pending_heartbeat)
        self.assertIsNone(self.client.pending_control)
        self.assertEqual(self.client.last_keep_alive, 160.1)
        self.assertEqual(self.client.last_control_ack, 160.1)

    def test_live_session_cannot_hide_lost_executor_ack_and_retries_keep_id(self):
        probe = self.start_probe()
        self.udp.ack(self.udp.packets('udp_sess_keep_alive')[0])
        for when in (160.3, 160.6, 161.0):
            self.now[0] = when
            self.client.check_connection()
        probes = self.udp.packets('set_bright')
        self.assertEqual(probes, [probe, probe, probe])
        self.assertIsNone(self.client.pending_heartbeat)
        self.assertEqual(self.client.last_keep_alive, 160.3)
        self.assertEqual(self.client.last_control_ack, 100.0)
        self.now[0] = 162.0
        with self.assertRaises(yeelight.CubeControlError) as caught:
            self.client.check_connection()
        self.assertIsInstance(caught.exception, TimeoutError)

    def test_wrong_id_and_rejected_control_do_not_confirm_executor(self):
        probe = self.start_probe()
        self.udp.incoming.append({'id': 999, 'result': ['ok']})
        self.now[0] = 160.1
        self.client.check_connection()
        self.assertEqual(self.client.last_control_ack, 100.0)
        self.assertIsNotNone(self.client.pending_control)
        self.udp.incoming.append({'id': probe['id'], 'error': {'code': 1, 'message': 'rejected'}})
        with self.assertRaises(ValueError):
            self.client.check_connection()
        self.assertEqual(self.client.last_control_ack, 100.0)

    def test_static_frames_refresh_each_second_while_health_is_polled(self):
        self.client.command('update_leds', ['static'])
        for when in (100.1, 100.3, 100.9):
            self.now[0] = when
            self.client.command('update_leds', ['static'])
        self.assertEqual(len(self.udp.packets('update_leds')), 1)
        self.now[0] = 101.0
        self.client.command('update_leds', ['static'])
        self.assertEqual(len(self.udp.packets('update_leds')), 2)

    def test_waiting_for_probe_does_not_stop_ten_fps_animation(self):
        self.client.last_control_ack = 40.0
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        desired = {'key': 'task', 'state': 'working'}
        engine = Mock(settings=settings)
        engine.desired.return_value = desired
        worker = YeelightTransport(engine, threading.RLock(), {'hardware_model': YEELIGHT_CUBE_MODEL})
        worker.client = self.client
        worker.stopping = Mock()
        worker.stopping.is_set.return_value = False
        def wait(delay):
            self.now[0] += delay
            return False
        worker.stopping.wait.side_effect = wait
        for frame in range(10):
            worker.command('update_leds', None, desired=desired, settings=settings,
                           params_factory=lambda frame=frame: [str(frame)])
        frames = [(when, packet) for when, packet in self.udp.sent if packet['method'] == 'update_leds']
        self.assertEqual([packet['params'] for _, packet in frames], [[str(i)] for i in range(10)])
        self.assertAlmostEqual(frames[-1][0] - frames[0][0], .9)
        self.assertIsNotNone(self.client.pending_control)

    def test_control_ack_repairs_current_screen_without_replaying_old_frame(self):
        self.client.command('update_leds', ['old-task'])
        self.now[0] = 160.0
        self.client.command('update_leds', ['current-task'])
        probe = self.udp.packets('set_bright')[0]
        self.udp.ack(probe)
        self.udp.ack(self.udp.packets('udp_sess_keep_alive')[0])
        self.now[0] = 160.1
        # The identical current frame is inside its normal resend interval,
        # but a delayed setter ACK requires a fresh repair of that screen.
        self.client.command('update_leds', ['current-task'])
        self.assertEqual([packet['params'] for packet in self.udp.packets('update_leds')],
                         [['old-task'], ['current-task'], ['current-task']])

    def test_retried_setter_repairs_static_screen_before_resend_interval(self):
        self.now[0] = 160.0
        self.client.command('update_leds', ['current-task'])
        self.udp.ack(self.udp.packets('udp_sess_keep_alive')[0])
        for when in (160.3, 160.6):
            self.now[0] = when
            self.client.command('update_leds', ['current-task'])
        self.assertEqual(len(self.udp.packets('set_bright')), 3)
        self.assertEqual([packet['params'] for packet in self.udp.packets('update_leds')],
                         [['current-task']] * 3)

    def test_executor_timeout_stops_frames_and_worker_backs_off_65_seconds(self):
        self.start_probe()
        self.udp.ack(self.udp.packets('udp_sess_keep_alive')[0])
        self.now[0] = 160.1
        self.client.check_connection()
        self.now[0] = 162.0
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        engine = Mock(settings=settings, device={})
        engine.desired.return_value = {'key': 'task', 'state': 'working'}
        worker = YeelightTransport(engine, threading.RLock(),
                                  {'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube'})
        worker.client = self.client
        worker.connect = Mock()
        stopped = [False]
        waits = []
        worker.stopping = Mock()
        worker.stopping.is_set.side_effect = lambda: stopped[0]
        def wait(delay):
            waits.append(delay)
            if delay == 65:
                stopped[0] = True
            return stopped[0]
        worker.stopping.wait.side_effect = wait
        worker.run()
        self.assertEqual(self.udp.packets('update_leds'), [])
        self.assertEqual(waits.count(65), 1)
        self.assertNotIn(3, waits)
        self.assertIsNone(worker.client)
        self.assertFalse(engine.device['online'])
        self.assertIn('65', engine.device['last_error'])
        worker.connect.assert_not_called()

    def test_worker_reports_ack_time_instead_of_frame_or_poll_time(self):
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        engine = Mock(settings=settings, device={}, last_command={})
        engine.desired.return_value = {'key': 'task', 'state': 'working'}
        worker = YeelightTransport(engine, threading.RLock(),
                                  {'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube'})
        client = Mock(last_keep_alive=99.0, last_control_ack=98.0)
        worker.client = client
        stopped = [False]
        before_first_poll = []
        worker.stopping = Mock()
        worker.stopping.is_set.side_effect = lambda: stopped[0]
        def wait(delay):
            if self.now[0] < 105:
                before_first_poll.append(dict(engine.device))
            self.now[0] += delay
            stopped[0] = self.now[0] >= 105.2
            return stopped[0]
        worker.stopping.wait.side_effect = wait
        def display(*_):
            worker.next_command = self.now[0] + .1
            return {'id': 1, 'result': ['sent']}
        worker.display = Mock(side_effect=display)
        worker.command = Mock(return_value={'id': 2, 'result': ['sent']})
        with patch('aiflame.transports.yeelight.time.time', side_effect=lambda: 1000 + self.now[0]):
            worker.run()
        self.assertGreater(worker.display.call_count, 40)
        self.assertTrue(before_first_poll)
        self.assertTrue(all('last_control_ack' not in row and 'last_heartbeat' not in row
                            for row in before_first_poll))
        client.check_connection.assert_called_once()
        self.assertAlmostEqual(engine.device['last_heartbeat'], 1099.0)
        self.assertAlmostEqual(engine.device['last_control_ack'], 1098.0)

    def stopped_before_first_frame(self):
        """Connect succeeds, but an exit arrives before the first display."""
        settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        engine = Mock(settings=settings, device={}, last_command={})
        engine.desired.return_value = {'key': 'task', 'state': 'working'}
        worker = YeelightTransport(engine, threading.RLock(),
                                  {'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube'})
        self.client.stopping = worker.stopping
        self.udp.auto_ack = True
        def connect():
            worker.client = self.client
            worker.stopping.set()
        worker.connect = Mock(side_effect=connect)
        worker.run()
        worker.connect.assert_called_once()
        return worker

    def assert_shutdown_power_off_was_acknowledged(self):
        off = self.udp.packets('set_power')
        self.assertEqual(len(off), 1)
        self.assertEqual(off[0]['params'], ['off', 'sudden', 0])
        self.assertIn({'id': off[0]['id'], 'result': ['ok']}, self.udp.received)
        self.assertTrue(self.udp.closed)
        self.assertIsNone(self.client.udp)

    def test_shutdown_before_direct_does_not_initialize_or_raise_brightness(self):
        self.client.direct = False
        self.stopped_before_first_frame()
        self.assertEqual([packet['method'] for _, packet in self.udp.sent], ['set_power'])
        self.assert_shutdown_power_off_was_acknowledged()

    def test_direct_shutdown_clears_screen_then_turns_off_even_if_clear_fails(self):
        for fail_frames in (False, True):
            with self.subTest(fail_frames=fail_frames):
                self.udp.sent.clear()
                self.udp.incoming.clear()
                self.udp.received.clear()
                self.udp.fail_frames = fail_frames
                self.client.udp = self.udp
                self.client.token = 'session'
                self.client.direct = True
                self.client.last_keep_alive = self.client.last_control_ack = self.now[0]
                self.stopped_before_first_frame()
                self.assertEqual([packet['method'] for _, packet in self.udp.sent],
                                 ['update_leds', 'set_power'])
                frame = self.udp.packets('update_leds')[0]
                self.assertEqual(base64.b64decode(frame['params'][0]), bytes(300))
                self.assert_shutdown_power_off_was_acknowledged()


if __name__ == '__main__':
    unittest.main()
