"""Removed task errors remain compatible without becoming input/completion alerts."""
import tempfile
import unittest
from pathlib import Path
from aiflame.core.engine import Engine
from aiflame.core.events import STATES, EventError, make_event
from aiflame.integrations.registry import SOURCES
from aiflame.devices.models import SUPPORTED_MODELS
from aiflame.devices.light_settings import light_settings, migrate_light_settings, validate_patch
from aiflame.devices.cube_effects import CUBE_MODES, frame
from aiflame.desktop.light_controls import LightPreviewPanel


class RetiredTaskErrorTests(unittest.TestCase):
    def test_legacy_sender_is_recorded_without_false_alert_or_completion(self):
        engine = Engine(':memory:', sources=SOURCES)
        self.addCleanup(engine.db.close)
        engine.update_settings({'desktop_notifications': True, 'hardware_notifications': True})
        engine.accept(make_event('manual', 'legacy', 'working', 1), 'manual')
        event = make_event('manual', 'legacy', 'error', 2)
        response, _ = engine.accept(event, 'manual')
        self.assertNotIn('error', STATES)
        self.assertEqual(response['deliveries']['desktop'], 'not_requested')
        self.assertEqual(engine.desired()['state'], 'idle')
        self.assertEqual(engine.active, {})
        row = engine.db.execute('SELECT state, reason FROM events WHERE id=?', (event['event_id'],)).fetchone()
        self.assertEqual(tuple(row), ('unknown', 'failed'))
        self.assertEqual(engine.db.execute('SELECT COUNT(*) FROM notification_outbox').fetchone()[0], 0)
        self.assertEqual(event['state'], 'error')  # do not mutate the caller
        engine.accept(make_event('manual', 'input', 'needs_input', 1), 'manual')
        self.assertEqual(engine.desired()['state'], 'needs_input')

    def test_old_history_migrates_without_deleting_events(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.sqlite'
            engine = Engine(path, sources=SOURCES)
            event = make_event('manual', 'history', 'working')
            engine.accept(event, 'manual')
            engine.db.execute("UPDATE events SET state='error' WHERE id=?", (event['event_id'],))
            engine.db.execute("UPDATE tasks SET state='error'")
            engine.db.commit()
            engine.db.close()
            engine = Engine(path, sources=SOURCES)
            try:
                self.assertEqual(tuple(engine.db.execute('SELECT state, reason FROM events').fetchone()), ('unknown', 'failed'))
                self.assertEqual(engine.db.execute('SELECT state FROM tasks').fetchone()[0], 'unknown')
                self.assertEqual(engine.desired()['state'], 'idle')
            finally:
                engine.db.close()

    def test_legacy_attention_preferences_migrate_and_new_input_value_wins(self):
        fields = {'colors': '#123456', 'state_brightness': 37, 'front_state_brightness': 29,
                  'front_state_temperature': 4200, 'state_power': False}
        for model in SUPPORTED_MODELS:
            record = {'hardware_model': model, 'light_settings': {field: {'error': value} for field, value in fields.items()}}
            settings = light_settings(record)
            for field, value in fields.items():
                if field in settings:
                    self.assertNotIn('error', settings[field])
                    self.assertEqual(settings[field]['needs_input'], value)
            record['light_settings']['colors']['needs_input'] = '#654321'
            migrate_light_settings(record)
            self.assertEqual(record['light_settings']['colors'], {'needs_input': '#654321'})
            for value in record['light_settings'].values():
                if isinstance(value, dict):
                    self.assertNotIn('error', value)
        with self.assertRaises(EventError):
            validate_patch({'colors': {'error': '#123456'}}, 'yeelight-cube-lite')

    def test_no_retired_status_in_preview_or_pixel_renderer(self):
        self.assertEqual([state for state, _ in LightPreviewPanel.STATES], ['working', 'needs_input', 'done'])
        for mode in CUBE_MODES:
            self.assertEqual(frame({'key':'legacy','state':'error'}, {'reminder_mode':mode}, 0), bytes(300))
