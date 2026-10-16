"""Supported models and safe migration away from retired hardware."""
import unittest
from aiflame.core.events import EventError
from aiflame.devices.models import MODELS, DEFAULT_MODEL, YEELIGHT_MODELS, SUPPORTED_MODELS, hardware_model
from aiflame.devices.registry import migrate_devices

class DeviceModelTests(unittest.TestCase):
    def test_supported_models_are_registered(self):
        self.assertEqual(tuple(model['id'] for model in MODELS), SUPPORTED_MODELS)
        self.assertIn(DEFAULT_MODEL, YEELIGHT_MODELS)
        for retired in ('ai-flame-v01', 'ai-flame-v02'):
            with self.assertRaises(EventError):
                hardware_model(retired)

    def test_upgrade_ignores_matrix_records_and_preserves_lamps(self):
        lamp = {'device_id': 'lamp', 'hardware_model': YEELIGHT_MODELS[1], 'connection_paused': True,
                'light_settings': {'state_brightness': {'working': 100}}}
        config = {'hardware_model': 'ai-flame-v01', 'device_id': 'board', 'port': 'COM1',
                  'devices': {'board': {'hardware_model': 'ai-flame-v01'}, 'lamp': lamp}}
        self.assertEqual(migrate_devices(config), {'lamp': lamp})
        self.assertNotIn('device_id', config)
        self.assertEqual(config['hardware_model'], DEFAULT_MODEL)
        self.assertEqual(migrate_devices(config), {'lamp': lamp})

    def test_legacy_profiles_keep_supported_identity_and_explicit_pause(self):
        config = {'hardware_model': 'ai-flame-v02', 'device_id': 'board',
                  'hardware_profiles': {'ai-flame-v02': {'device_id': 'board'},
                    YEELIGHT_MODELS[0]: {'device_id': 'lamp', 'connection_paused': True}}}
        self.assertEqual(set(migrate_devices(config)), {'lamp'})
        self.assertTrue(config['devices']['lamp']['connection_paused'])
