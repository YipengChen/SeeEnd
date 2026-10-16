import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiflame.devices.token_store import TokenStore

TOKEN = '0123456789abcdef0123456789abcdef'

class LocalDeviceTokensTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TokenStore(self.temp.name)
        self.platform = patch('aiflame.devices.token_store.sys.platform', 'darwin')
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def test_roundtrip_restart_permissions_and_no_keychain(self):
        with patch.object(TokenStore, '_call', side_effect=AssertionError('keychain accessed')):
            self.store.set('one', TOKEN)
            self.assertEqual(TokenStore(self.temp.name).get('one'), TOKEN)
            for path in Path(self.temp.name).rglob('*.json'):
                self.assertNotIn(TOKEN, path.read_text(encoding="utf-8"))
                if os.name != "nt":
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.store.delete('one')
            self.assertFalse(self.store._path('one').exists())

    def test_tamper_and_account_swap_rejected(self):
        self.store.set('one', TOKEN)
        original = self.store._path('one').read_text(encoding="utf-8")
        self.store._path('two').write_text(original, encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.store.get('two')
        saved = json.loads(original)
        saved['ciphertext'] = saved['ciphertext'][4:] + 'AAAA'
        self.store._path('one').write_text(json.dumps(saved), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.store.get('one')

    def test_missing_key_fails_without_regenerating_on_read(self):
        self.store.set('one', TOKEN)
        key = Path(self.temp.name) / 'device-tokens/key.json'
        key.unlink()
        with self.assertRaises(RuntimeError):
            self.store.get('one')
        self.assertFalse(key.exists())

    def test_legacy_migration_without_ui_then_local_only(self):
        with patch.object(self.store, '_call', side_effect=[TOKEN, None]) as legacy:
            self.assertEqual(self.store.get('one'), TOKEN)
            self.assertEqual(legacy.call_args_list[0].kwargs, {'noninteractive': True})
            self.assertEqual(legacy.call_args_list[1].args, ('delete', 'one'))
        with patch.object(self.store, '_call', side_effect=AssertionError()):
            self.assertEqual(self.store.get('one'), TOKEN)
            self.store.delete('one')

    def test_denied_legacy_migration_does_not_write(self):
        with patch.object(self.store, '_call', side_effect=RuntimeError('denied')):
            with self.assertRaisesRegex(RuntimeError, '重新扫码'):
                self.store.get('one')
        self.assertFalse(self.store._path('one').exists())

    def test_failed_legacy_cleanup_keeps_working_local_copy_and_retry(self):
        with patch.object(self.store, '_call', side_effect=[TOKEN, RuntimeError('denied')]):
            self.assertEqual(self.store.get('one'), TOKEN)
        self.assertTrue(json.loads(self.store._path('one').read_text(encoding="utf-8"))['legacy_cleanup'])
        with patch.object(self.store, '_call', return_value=None) as legacy:
            self.assertEqual(self.store.get('one'), TOKEN)
            legacy.assert_not_called()
            self.store.delete('one')
            legacy.assert_called_once_with('delete', 'one', noninteractive=True)
