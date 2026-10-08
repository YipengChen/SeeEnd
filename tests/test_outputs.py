"""Independent output delivery and compatible lifecycle semantics."""
import datetime as dt
import unittest
from aiflame.core.engine import Engine
from aiflame.core.events import make_event, EventError, SourcePolicy


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.wall = 1770000000
        self.engine = Engine(':memory:', lambda: self.now, lambda: self.wall,
                             sources=[SourcePolicy('test', 'Test', 'test')])
        self.addCleanup(self.engine.db.close)

    def event(self, state, seq=1, task='task', **extra):
        event = make_event('test', task, state, seq)
        event['occurred_at'] = dt.datetime.fromtimestamp(self.wall, dt.timezone.utc).isoformat()
        return {**event, **extra}

    def send(self, state, seq=1, **extra):
        return self.engine.accept(self.event(state, seq, **extra), 'test')[0]

    def test_zero_brightness_and_disconnected_lamp_do_not_suppress_desktop(self):
        self.engine.update_settings({'brightness': 0})
        result = self.send('done')
        self.assertEqual(result['deliveries'], {'flame': 'suppressed', 'desktop': 'pending'})
        self.assertFalse(self.engine.device['online'])
        message, = self.engine.desktop.claim()
        self.assertEqual(message['title'], 'SeeEnd · 任务已完成')
        receipt = self.engine.desktop.acknowledge({k: message[k] for k in ('event_id', 'claim')} | {'status': 'submitted'})
        self.assertEqual(receipt['status'], 'submitted')
        self.assertEqual(self.engine.db.execute("SELECT status FROM deliveries WHERE output='flame'").fetchone()[0], 'suppressed')

    def test_output_failure_does_not_stop_other_outputs(self):
        def fail(event): raise OSError('offline')
        self.engine.flame.accept = fail
        self.assertEqual(self.send('done')['deliveries'], {'flame': 'failed', 'desktop': 'pending'})

    def test_duplicate_does_not_notify_twice(self):
        event = self.event('done')
        self.engine.accept(event, 'test')
        self.assertTrue(self.engine.accept(event, 'test')[0]['duplicate'])
        self.assertEqual(len(self.engine.desktop.claim()), 1)
        self.assertEqual(self.engine.desktop.claim(), [])

    def test_start_renewal_input_resume_and_completion(self):
        self.assertEqual(self.send('working')['phase'], 'started')
        self.assertEqual(self.send('working', 2)['phase'], 'renewed')
        self.assertEqual(self.engine.desktop.claim(), [])
        self.send('needs_input', 3)
        self.assertEqual(self.engine.desired()['state'], 'error')
        self.assertEqual(self.engine.snapshot()['tasks'][0]['state'], 'needs_input')
        self.assertEqual(self.send('working', 4)['phase'], 'resumed')
        self.assertEqual(self.engine.desired()['state'], 'working')
        self.send('done', 5)
        self.assertEqual(self.engine.desired()['state'], 'done')

    def test_completion_replaces_input_light(self):
        self.send('needs_input')
        self.engine.desired()
        self.send('done', 2)
        self.assertEqual(self.engine.desired()['state'], 'done')
        self.assertEqual(len(self.engine.queue), 0)

    def test_timeout_cancelled_unknown_preserved(self):
        for state in ('timeout', 'cancelled', 'unknown'):
            self.send(state, task=state)
        self.assertEqual({r['state'] for r in self.engine.snapshot()['tasks']}, {'timeout', 'cancelled', 'unknown'})
        with self.assertRaises(EventError): self.send('working', 2, task='timeout')
        self.assertEqual(self.send('working', 2, task='unknown')['phase'], 'resumed')

    def test_lease_expiry_is_core_lifecycle_event_without_light_poll(self):
        self.send('working')
        self.now += 121
        self.engine.tick()
        self.assertEqual(self.engine.snapshot()['tasks'][0]['state'], 'unknown')
        message, = self.engine.desktop.claim()
        self.assertIn('未知', message['title'])
        self.assertEqual(self.send('working', 2)['phase'], 'resumed')

    def test_disabled_desktop_does_not_suppress_flame(self):
        self.engine.update_settings({'desktop_notifications': False})
        self.assertEqual(self.send('done')['deliveries'], {'flame': 'queued', 'desktop': 'disabled'})
        self.assertEqual(self.engine.desktop.claim(), [])

    def test_claim_token_and_expiry(self):
        result = self.send('done')
        with self.assertRaises(EventError):
            self.engine.desktop.acknowledge({'event_id': result['event_id'], 'claim': 'fake', 'status': 'submitted'})
        self.wall += 61
        self.assertEqual(self.engine.desktop.claim(), [])
        self.assertEqual(self.engine.db.execute("SELECT status FROM deliveries WHERE output='desktop'").fetchone()[0], 'expired')

    def test_hardware_ack_only_changes_flame_receipt(self):
        self.engine.device['online'] = True
        self.send('done')
        self.engine.applied(self.engine.desired(), {'result': 'applied'})
        self.assertEqual({r['output']: r['status'] for r in self.engine.router.snapshot()}, {'flame': 'applied', 'desktop': 'pending'})

    def test_disabled_source_suppresses_all_outputs(self):
        self.engine.update_settings({'source_enabled': {'test': False}})
        self.assertEqual(self.send('done')['deliveries'], {'flame': 'unmatched', 'desktop': 'unmatched'})

    def test_offline_and_idle_receipts_track_legacy_status(self):
        for state in ('working', 'idle', 'cancelled', 'timeout', 'unknown'):
            result = self.send(state, task=state)
            self.engine.desired()
            row = self.engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output='flame'", (result['event_id'],)).fetchone()
            self.assertEqual(row[0], 'screen')

    def test_queue_victim_receipt_is_not_overwritten(self):
        for i in range(20): self.send('error', task=str(i))
        result = self.send('done', task='overflow')
        self.assertEqual(result['deliveries']['flame'], 'dropped')
        self.assertEqual(result['deliveries']['desktop'], 'pending')

    def test_future_output_receives_unmodified_lifecycle_independent_of_lamp(self):
        class Observer:
            name = 'watch'
            def __init__(self): self.events = []
            def accept(self, event):
                self.events.append(event)
                return 'submitted'
        observer = Observer()
        self.engine.router.register(observer)
        self.engine.update_settings({'brightness': 0, 'desktop_notifications': False})
        self.send('working')
        self.send('working', 2)
        self.send('needs_input', 3)
        self.assertEqual([e['phase'] for e in observer.events], ['started', 'renewed', 'needs_input'])
        self.assertEqual(observer.events[-1]['state'], 'needs_input')
        self.assertEqual(self.engine.db.execute("SELECT COUNT(*) FROM deliveries WHERE output='watch' AND status='submitted'").fetchone()[0], 3)
