"""Recover abandoned CLI states without expiring legitimately long tasks."""
import threading
import unittest
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.integrations.registry import SOURCES


class OrphanTaskTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.engine = Engine(':memory:', clock=lambda: self.now, sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        self.origin = {'kind': 'terminal', 'app': 'Terminal', 'tty': '/dev/ttys003',
                       'pid': 12345, 'started': 'birth', 'instance_id': 'instance', 'session_id': 'session'}

    def start(self, task='one', origin=True):
        event = make_event('codex', task, 'working')
        if origin:
            event['return_target'] = dict(self.origin)
        self.engine.accept(event, 'codex')
        return event

    def state(self, task='one'):
        return self.engine.db.execute("SELECT state FROM tasks WHERE source='codex' AND task=?", (task,)).fetchone()[0]

    def check(self, records=None, error=None):
        self.now += 31
        with patch('aiflame.core.task_origins.sys.platform', 'darwin'), \
                patch('aiflame.core.task_origins.os.kill', side_effect=error), \
                patch('aiflame.integrations.cli_tools.origin.processes', return_value=records or {}):
            self.engine.tick()
            if self.engine.host_check_thread:
                self.engine.host_check_thread.join(1)
            self.engine.tick()

    def test_alive_long_running_and_input_tasks_have_no_arbitrary_timeout(self):
        self.start()
        self.engine.accept(make_event('codex', 'one', 'needs_input', 2), 'codex')
        self.now += 86400
        self.check({12345: {'started': 'birth'}})
        self.assertEqual(self.state(), 'needs_input')
        self.assertEqual(self.engine.unfinished_task_count(), 1)

    def test_dead_host_reclaims_only_its_task_and_never_reports_done(self):
        self.start()
        self.start('no-origin', origin=False)
        self.check(error=ProcessLookupError())
        self.assertEqual(self.state(), 'unknown')
        self.assertEqual(self.state('no-origin'), 'working')
        events = self.engine.snapshot()['events']
        recovered = next(row for row in events if row['reason'] == 'host_exited')
        self.assertEqual(recovered['state'], 'unknown')
        self.assertFalse(any(row['state'] == 'done' for row in events))
        self.assertNotIn(('codex', 'one'), self.engine.active)

    def test_pid_reuse_is_death_of_original_process(self):
        self.start()
        self.check({12345: {'started': 'another-birth'}})
        self.assertEqual(self.state(), 'unknown')

    def test_missing_record_and_permission_error_are_not_death_evidence(self):
        self.start()
        self.check({})
        self.assertEqual(self.state(), 'working')
        self.check(error=PermissionError())
        self.assertEqual(self.state(), 'working')

    def test_process_inspection_failure_does_not_expire_task(self):
        self.start()
        self.now += 31
        with patch('aiflame.core.task_origins.sys.platform', 'darwin'), \
                patch('aiflame.core.task_origins.os.kill'), \
                patch('aiflame.integrations.cli_tools.origin.processes', side_effect=OSError()):
            self.engine.tick()
            self.engine.host_check_thread.join(1)
            self.engine.tick()
        self.assertEqual(self.state(), 'working')

    def test_manual_recovery_without_origin_preserves_other_tasks_and_late_real_result(self):
        event = self.start(origin=False)
        self.start('other', origin=False)
        self.assertTrue(next(row for row in self.engine.snapshot()['events'] if row['id'] == event['event_id'])['can_mark_unknown'])
        result = self.engine.mark_task_unknown(event['event_id'])
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(self.state('other'), 'working')
        self.assertEqual(self.engine.unfinished_task_count(), 1)
        self.assertFalse(next(row for row in self.engine.snapshot()['events'] if row['id'] == event['event_id'])['can_mark_unknown'])
        self.engine.accept(make_event('codex', 'one', 'done', 2), 'codex')
        self.assertEqual(self.state(), 'done')

    def test_stale_selection_and_already_completed_tasks_reject_recovery(self):
        event = self.start()
        self.engine.accept(make_event('codex', 'one', 'needs_input', 2), 'codex')
        with self.assertRaises(EventError):
            self.engine.mark_task_unknown(event['event_id'])
        self.engine.accept(make_event('codex', 'one', 'done', 3), 'codex')
        with self.assertRaises(EventError):
            self.engine.mark_task_unknown(event['event_id'])
        self.assertEqual(self.state(), 'done')

    def test_recovery_clears_attention_prompt_and_pending_sound(self):
        self.engine.update_settings({'sound_notifications': True})
        self.start(origin=False)
        prompt = make_event('codex', 'one', 'needs_input', 2)
        self.engine.accept(prompt, 'codex')
        self.engine.desired()
        self.engine.mark_task_unknown(prompt['event_id'])
        self.assertEqual(self.engine.desired()['state'], 'idle')
        self.assertEqual(self.engine.sound.claim(), [])

    def test_auto_inspection_is_throttled_and_unsupported_platform_is_conservative(self):
        self.start()
        with patch.object(self.engine.origins, 'exited_hosts') as check:
            self.engine.tick()
            if self.engine.host_check_thread:
                self.engine.host_check_thread.join(1)
            self.engine.tick()
            check.assert_not_called()
        with patch('aiflame.core.task_origins.sys.platform', 'win32'), \
                patch('aiflame.core.task_origins.os.kill') as kill:
            self.now += 31
            self.engine.tick()
            self.engine.host_check_thread.join(1)
            self.engine.tick()
            kill.assert_not_called()
        self.assertEqual(self.state(), 'working')

    def test_process_scan_does_not_block_tick_or_overwrite_newer_task_state(self):
        import time
        self.start()
        gate = threading.Event()
        def inspect(candidates):
            gate.wait(2)
            return [('codex', 'one')]
        with patch.object(self.engine.origins, 'exited_hosts', side_effect=inspect):
            self.now += 31
            started = time.monotonic()
            self.engine.tick()
            self.assertLess(time.monotonic() - started, .2)
            self.engine.accept(make_event('codex', 'one', 'needs_input', 2), 'codex')
            gate.set()
            self.engine.host_check_thread.join(1)
            self.engine.tick()
        self.assertEqual(self.state(), 'needs_input')
