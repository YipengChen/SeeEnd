"""History limits must preserve unfinished task identity and current returns."""
import time
import unittest
import uuid

from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.integrations.registry import SOURCES


class RetentionReturnTests(unittest.TestCase):
    def test_age_and_capacity_keep_current_event_and_completion_destination(self):
        for cleanup in ('age', 'capacity'):
            for state in ('working', 'needs_input'):
                with self.subTest(cleanup=cleanup, state=state):
                    engine = Engine(':memory:', sources=SOURCES)
                    try:
                        context = {'session_id': str(uuid.uuid4()), 'turn_id': 'turn', 'cwd': '/work/project'}
                        origin = {'kind': 'terminal', 'app': 'Terminal', 'tty': '/dev/ttys003',
                                  'pid': 123, 'started': 'birth', 'instance_id': 'instance', 'session_id': 'session'}
                        start = {**make_event('codex', 'live', 'working'),
                                 'return_target': origin, 'return_context': context}
                        engine.accept(start, 'codex')
                        current = start
                        if state == 'needs_input':
                            current = make_event('codex', 'live', state, 2)
                            engine.accept(current, 'codex')
                        engine.db.execute('UPDATE events SET created=0')
                        engine.db.execute('UPDATE tasks SET updated=0')
                        if cleanup == 'age':
                            engine.cleanup(force=True)
                        else:
                            engine.db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)',
                                ((f'history-{i}', 'manual', f'history-{i}', 1, 'done', None, time.time(), 'screen')
                                 for i in range(10001)))
                            engine.accept(make_event('manual', 'trigger', 'done'), 'manual')
                            self.assertLessEqual(engine.db.execute('SELECT count(*) FROM events').fetchone()[0], 10001)
                        self.assertEqual(engine.origins.context('codex', 'live'), context)
                        self.assertEqual(engine.return_target(current['event_id'])[0], origin)
                        if state == 'working':
                            engine.clear_prompts('expired')
                            self.assertEqual(engine.display_status()['return_task']['id'], current['event_id'])
                        done = make_event('codex', 'live', 'done', 3)
                        engine.accept(done, 'codex')
                        self.assertEqual(engine.return_target(done['event_id'])[0], origin)
                        # Reclaim identity only once both its task and events expire.
                        engine.db.execute('UPDATE events SET created=0')
                        engine.db.execute('UPDATE tasks SET updated=0')
                        engine.cleanup(force=True)
                        self.assertIsNone(engine.origins.get('codex', 'live'))
                        self.assertIsNone(engine.origins.context('codex', 'live'))
                        self.assertEqual(engine.db.execute('SELECT count(*) FROM terminal_instances').fetchone()[0], 0)
                    finally:
                        engine.db.close()

    def test_task_identity_survives_missing_events_without_borrowing_another_source(self):
        engine = Engine(':memory:', sources=SOURCES)
        try:
            context = {'session_id': str(uuid.uuid4()), 'turn_id': 'turn', 'cwd': '/work'}
            engine.accept({**make_event('codex', 'shared', 'working'), 'return_context': context}, 'codex')
            engine.db.execute('DELETE FROM events')
            engine.origins.cleanup()
            self.assertEqual(engine.origins.context('codex', 'shared'), context)
            engine.db.execute("UPDATE tasks SET source='manual'")
            engine.origins.cleanup()
            self.assertIsNone(engine.origins.context('codex', 'shared'))
        finally:
            engine.db.close()
