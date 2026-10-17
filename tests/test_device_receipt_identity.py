"""Real transport loops must bind ACKs to the request that produced them."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.integrations.registry import SOURCES
from aiflame.transports.fleet import FleetTransportManager
from aiflame.transports.yeelight import YeelightTransport
from aiflame.transports.mijia import MijiaTransport


class Iterations:
    def __init__(self, count, after=None):
        self.count, self.after, self.stopped = count, after, False

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        if delay <= 0:
            return self.stopped
        self.count -= 1
        if self.after:
            self.after(self.count)
        self.stopped = self.count <= 0
        return self.stopped


class DeviceReceiptIdentityTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.engine = Engine(':memory:', clock=lambda: self.now, sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        self.engine.update_settings({'hardware_notifications': True})
        self.record = {'device_id': 'lamp', 'hardware_model': 'yeelight-yldp005'}
        self.fleet = FleetTransportManager(self.engine.flame, threading.RLock(), {'devices': {'lamp': self.record}})
        self.engine.flame.transport = self.fleet
        self.output = self.fleet.outputs['lamp']
        self.output.device = {'online': True, 'session_id': 'first', 'connected_at': 1}

    def emit(self, task='A', state='working', seq=1):
        event = make_event('codex', task, state, seq)
        self.engine.accept(event, 'codex')
        return event['event_id']

    def receipt(self, event_id):
        return self.engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output='flame:lamp'",
                                      (event_id,)).fetchone()[0]

    def appearance(self, color):
        before = self.record['light_settings']
        self.record['light_settings'] = {**before, 'colors': {**self.output.settings['colors'], 'working': color}}

    def run_lamp(self, display, *, count=1, after=None, query=None):
        worker = YeelightTransport(self.output, self.fleet.lock, self.record)
        worker.stopping = Iterations(count, after)
        worker.client = SimpleNamespace(stopping=None, close=lambda: None,
            command=lambda method, params: query(method) if query and method == 'get_prop'
            else {'id': 100, 'result': ['ok']})
        worker.display = display
        with patch('aiflame.transports.yeelight.time.monotonic', side_effect=lambda: self.now):
            worker.run()

    def test_inflight_working_ack_cannot_be_reattributed_to_replacement_task(self):
        self.emit()
        events = []
        def response(desired, settings):
            events.append(desired.event_ids)
            self.emit(state='unknown', seq=2)
            replacement = self.emit('B')
            events.append(replacement)
            return {'id': 1, 'result': ['ok']}
        self.run_lamp(response)
        self.assertEqual(len(events[0]), 1)
        self.assertEqual(self.receipt(events[1]), 'queued')
        self.assertFalse(self.fleet.confirmed('working'))

    def test_same_working_display_resends_for_new_event_before_heartbeat(self):
        first = self.emit()
        requests, second = [], []
        def response(desired, settings):
            requests.append(desired.event_ids)
            return {'id': len(requests), 'result': ['ok']}
        def after(remaining):
            if remaining == 1:
                second.append(self.emit('B'))
                self.assertEqual(self.receipt(second[0]), 'queued')
                self.assertFalse(self.fleet.confirmed('working'))
        self.run_lamp(response, count=2, after=after)
        self.assertEqual(requests, [(first,), tuple(sorted((first, second[0])))])
        self.assertEqual(self.receipt(second[0]), 'applied')
        self.assertTrue(self.fleet.confirmed('working'))

    def test_heartbeat_uses_original_request_even_when_new_task_arrives_during_query(self):
        self.emit()
        requests, second = [], []
        def response(desired, settings):
            requests.append(desired.event_ids)
            return {'id': 1, 'result': ['ok']}
        def query(method):
            second.append(self.emit('B'))
            return {'id': 2, 'result': ['on']}
        self.run_lamp(response, count=2, after=lambda _: setattr(self, 'now', 106.0), query=query)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(self.receipt(second[0]), 'queued')
        self.assertFalse(self.fleet.confirmed('working'))

    def test_inflight_ack_rejects_appearance_change_and_current_confirmation_expires(self):
        event_id = self.emit()
        def response(desired, settings):
            self.appearance('#0000FF')
            return {'id': 1, 'result': ['ok']}
        self.run_lamp(response)
        self.assertEqual(self.receipt(event_id), 'queued')
        self.assertFalse(self.fleet.confirmed('working'))
        current = self.output.desired()
        self.output.applied(current, {'result': 'applied'})
        self.assertTrue(self.fleet.confirmed('working'))
        self.appearance('#FF0000')
        self.assertFalse(self.fleet.confirmed('working'))
        self.assertEqual(self.receipt(event_id), 'applied')
        self.output.applied(current, {'result': 'applied'})
        self.assertFalse(self.fleet.confirmed('working'))

    def test_reconnect_and_appearance_roundtrip_reject_old_request(self):
        event_id = self.emit()
        old = self.output.desired().copy()
        original = self.output.settings['colors']['working']
        self.appearance('#0000FF')
        self.output.desired()
        self.appearance(original)
        self.output.applied(old, {'result': 'applied'})
        self.assertEqual(self.receipt(event_id), 'queued')
        current = self.output.desired()
        self.output.device.update(session_id='second', connected_at=2)
        self.output.applied(current, {'result': 'applied'})
        self.assertEqual(self.receipt(event_id), 'queued')
        self.output.applied(self.output.desired(), {'result': 'applied'})
        self.assertEqual(self.receipt(event_id), 'applied')

    def test_new_idle_event_requires_new_evidence_and_ack_does_not_change_revision(self):
        self.emit()
        first = self.emit(state='unknown', seq=2)
        old = self.output.desired()
        self.output.applied(old, {'result': 'applied'})
        self.assertTrue(self.fleet.confirmed('idle'))
        self.emit('B')
        second = self.emit('B', state='unknown', seq=2)
        self.output.applied(old, {'result': 'applied'})
        self.assertEqual(self.receipt(second), 'queued')
        self.assertFalse(self.fleet.confirmed('idle'))
        self.output.applied(self.output.desired(), {'result': 'applied'})
        self.assertEqual(self.receipt(first), 'applied')
        self.assertEqual(self.receipt(second), 'applied')
        self.assertTrue(self.fleet.confirmed('idle'))

    def test_idle_history_from_old_session_or_expired_event_cannot_gain_new_ack(self):
        import json
        self.emit()
        old = self.emit(state='unknown', seq=2)
        self.output.applied(self.output.desired(), {'result': 'sent'})
        self.output.device.update(session_id='second', connected_at=2)
        self.output.applied(self.output.desired(), {'result': 'applied'})
        self.assertEqual(self.receipt(old), 'sent')
        self.emit('B')
        expired = self.emit('B', state='unknown', seq=2)
        self.output.applied(self.output.desired(), {'result': 'sent'})
        pending = self.output.desired()
        saved = self.engine.db.execute("SELECT detail FROM deliveries WHERE event=? AND output='flame:lamp'", (expired,)).fetchone()[0]
        with patch.object(self.engine.flame, 'wall', return_value=json.loads(saved)['expires'] + 1):
            self.output.applied(pending, {'result': 'applied'})
            self.assertEqual(self.receipt(expired), 'sent')
            self.output.applied(self.output.desired(), {'result': 'applied'})
        self.assertEqual(self.receipt(expired), 'sent')

    def test_prompt_deadline_and_confirmed_history_do_not_regress(self):
        self.engine.update_settings({'hold_until_activity': False})
        self.emit()
        event_id = self.emit(state='done', seq=2)
        request = self.output.desired()
        self.output.applied(request, {'result': 'sent'})
        deadline = self.engine.flame.current['deadline']
        self.now += 1
        self.appearance('#0000FF')
        self.output.applied(request, {'result': 'applied'})
        self.assertEqual(self.receipt(event_id), 'sent')
        self.assertEqual(self.engine.flame.current['deadline'], deadline)
        current = self.output.desired()
        self.output.applied(current, {'result': 'applied'})
        self.output.applied(current, {'result': 'sent'})
        self.assertEqual(self.receipt(event_id), 'applied')
        self.assertEqual(self.engine.flame.current['deadline'], deadline)

    def test_mijia_rechecks_actual_power_for_new_working_event(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.record['hardware_model'] = MIJIA_PLUG_MODEL
        first = self.emit()
        second, reads = [], []
        def read():
            reads.append(1)
            return False
        def after(remaining):
            if remaining == 1:
                second.append(self.emit('B'))
        worker = MijiaTransport(self.output, self.fleet.lock, self.record)
        worker.client = SimpleNamespace(power=read, set_power=lambda _: None, close=lambda: None, sequence=1)
        worker.stopping = Iterations(2, after)
        worker.run()
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.receipt(first), 'applied')
        self.assertEqual(self.receipt(second[0]), 'applied')

    def test_cube_static_cache_cannot_reuse_previous_tasks_sent_packet(self):
        from aiflame.devices.models import YEELIGHT_CUBE_MODEL
        from aiflame.devices.yeelight import CubeClient
        from unittest.mock import Mock
        self.record['hardware_model'] = YEELIGHT_CUBE_MODEL
        self.record['light_settings'] = {'reminder_mode': 'rear'}
        self.emit()
        client = CubeClient('192.168.1.1')
        client.direct = True
        client.prepare_frame = Mock()
        client.udp_command = Mock(side_effect=lambda method, *a, **k:
                                  {'id': client.udp_command.call_count,
                                   'result': ['sent'] if method == 'update_leds' else ['ok']})
        worker = YeelightTransport(self.output, self.fleet.lock, self.record)
        worker.client = client
        second = []
        def after(remaining):
            if remaining == 1:
                second.append(self.emit('B'))
            else:
                self.assertEqual(client.udp_command.call_count, 2)
        worker.stopping = Iterations(2, after)
        def display(desired, settings):
            worker.next_command = self.now + .1
            return client.command('update_leds', ['same-static-frame'])
        worker.display = display
        with patch('aiflame.transports.yeelight.time.monotonic', side_effect=lambda: self.now), \
                patch('aiflame.devices.yeelight.time.monotonic', side_effect=lambda: self.now):
            worker.run()
        self.assertEqual(self.receipt(second[0]), 'sent')
        self.assertFalse(self.fleet.confirmed('working'))
