"""Installer safety regressions use isolated homes and never real tool settings."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiflame.integrations.codex.install import install as install_codex, hook_handler as codex_handler
from aiflame.integrations.cli_tools.install import install, hook_handler, MARKER, settings
from aiflame.integrations.cli_tools.opencode import install_plugin, wrapper


class IncrementalInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.environment = patch.dict(os.environ, {'AIFLAME_DATA_DIR': str(self.root / 'runtime')})
        self.environment.start()
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.environment.stop)

    def snapshot(self, root=None):
        return {str(path.relative_to(root or self.root)): path.read_bytes()
                for path in (root or self.root).rglob('*') if path.is_file()}

    def test_reading_installation_records_is_read_only(self):
        self.assertEqual(settings()[1], {})
        self.assertFalse((self.root / 'runtime').exists())

    def test_codex_merge_backup_idempotence_and_foreign_label(self):
        directory = self.root / 'codex'
        directory.mkdir()
        path = directory / 'hooks.json'
        third = {'type': 'command', 'command': 'my-command', 'statusMessage': codex_handler()['statusMessage']}
        original = {'permissions': {'ask': ['keep']}, 'hooks': {'Stop': [{'matcher': 'keep', 'hooks': [third]}], 'CustomEvent': [{'hooks': [{'type': 'prompt', 'prompt': 'keep'}]}]}}
        original_bytes = json.dumps(original, separators=(',', ':')).encode()
        path.write_bytes(original_bytes)
        self.assertEqual(install_codex(directory), path.resolve())
        backup = directory / 'hooks.json.seeend-backup-1'
        self.assertEqual(backup.read_bytes(), original_bytes)
        before, modified = self.snapshot(), path.stat().st_mtime_ns
        install_codex(directory)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(path.stat().st_mtime_ns, modified)
        install_codex(directory, uninstall=True)
        self.assertEqual(json.loads(path.read_text()), original)
        self.assertEqual(backup.read_bytes(), original_bytes)

    def test_all_json_cli_merges_preserve_label_collisions_and_unrelated_values(self):
        for source in ('claude-code', 'qoder', 'gemini-cli'):
            with self.subTest(source=source):
                directory = self.root / source
                directory.mkdir()
                path = directory / 'settings.json'
                event = 'AfterAgent' if source == 'gemini-cli' else 'Stop'
                third = {'type': 'command', 'command': 'external', 'name': 'aiflame-' + source,
                         'statusMessage': MARKER + ' · ' + source}
                original = {'env': {'TOKEN': 'keep'}, 'model': {'id': 'keep'}, 'hooks': {event: [{'matcher': '.*', 'custom': {'keep': True}, 'hooks': [third]}]}}
                path.write_text(json.dumps(original))
                install(source, config_dir=directory)
                before, modified = self.snapshot(), path.stat().st_mtime_ns
                install(source, config_dir=directory)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(path.stat().st_mtime_ns, modified)
                self.assertEqual(json.loads(path.read_text())['hooks'][event][0]['hooks'], [third])
                install(source, config_dir=directory, uninstall=True)
                self.assertEqual(json.loads(path.read_text()), original)

    def test_malformed_late_event_causes_zero_writes_including_runtime_and_backup(self):
        for source, filename, event in [('codex', 'hooks.json', 'Stop'), ('claude-code', 'settings.json', 'SessionEnd'), ('qoder', 'settings.json', 'SessionEnd'), ('gemini-cli', 'settings.json', 'SessionEnd')]:
            with self.subTest(source=source):
                directory = self.root / source
                directory.mkdir()
                path = directory / filename
                path.write_text(json.dumps({'env': {'keep': True}, 'hooks': {event: [{'hooks': [None]}]}}))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    install_codex(directory) if source == 'codex' else install(source, config_dir=directory)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / 'runtime').exists())

    def test_uninstall_absent_or_unconfigured_json_creates_nothing(self):
        install_codex(self.root / 'missing', uninstall=True)
        install('claude-code', config_dir=self.root / 'missing', uninstall=True)
        self.assertEqual(self.snapshot(), {})
        directory = self.root / 'unconfigured'
        directory.mkdir()
        path = directory / 'hooks.json'
        path.write_text('{"theme":"keep"}')
        install_codex(directory, uninstall=True)
        self.assertEqual(path.read_text(), '{"theme":"keep"}')
        self.assertFalse((self.root / 'runtime').exists())

    def test_cli_command_migration_requires_our_executable_and_same_source(self):
        from aiflame.integrations.cli_tools.install import owns_hook
        wanted = hook_handler('claude-code')['command']
        self.assertTrue(owns_hook({'type': 'command', 'command': "python -m aiflame --cli-hook claude-code"}, wanted, 'claude-code'))
        for command in ('other --cli-hook claude-code', 'python -m another --cli-hook claude-code', 'python -m aiflame --cli-hook qoder'):
            self.assertFalse(owns_hook({'type': 'command', 'command': command, 'name': 'aiflame-claude-code'}, wanted, 'claude-code'))

    def test_opencode_foreign_components_and_directory_collisions_are_zero_write(self):
        for relative in ('plugins/aiflame.js', 'aiflame/index.mjs', 'aiflame/bridge.json', 'aiflame', 'plugins'):
            with self.subTest(relative=relative):
                directory = self.root / relative.replace('/', '-')
                path = directory / relative
                path.parent.mkdir(parents=True)
                path.write_text('foreign original')
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    install('opencode', config_dir=directory)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / 'runtime').exists())

    def test_opencode_owned_bundle_idempotent_and_uninstall_preserves_later_edits(self):
        directory = self.root / 'opencode'
        install_plugin(directory)
        before = self.snapshot()
        install_plugin(directory)
        self.assertEqual(self.snapshot(), before)
        edited = directory / 'aiflame/index.mjs'
        edited.write_text('// custom user changes')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            install_plugin(directory)
        self.assertEqual(self.snapshot(), before)
        other = directory / 'plugins/other.js'
        other.write_text('// other')
        install_plugin(directory, uninstall=True)
        self.assertEqual(edited.read_text(), '// custom user changes')
        self.assertEqual(other.read_text(), '// other')
        self.assertFalse((directory / 'plugins/aiflame.js').exists())
        self.assertFalse((directory / 'aiflame/bridge.json').exists())

    def test_legacy_opencode_marker_does_not_authorize_overwriting_foreign_bundle(self):
        directory = self.root / 'legacy'
        (directory / 'plugins').mkdir(parents=True)
        (directory / 'aiflame').mkdir()
        (directory / 'plugins/aiflame.js').write_text(wrapper())
        (directory / 'aiflame/bridge.json').write_text('{"helperArgv":["foreign"]}')
        before = self.snapshot()
        with self.assertRaises(ValueError):
            install_plugin(directory)
        self.assertEqual(self.snapshot(), before)

    def test_deepseek_foreign_dependency_and_malformed_profile_are_zero_write(self):
        profile_root = self.root / 'dsh'
        directory = profile_root / 'profiles/default'
        directory.mkdir(parents=True)
        path = directory / 'package.json'
        with patch.dict(os.environ, {'DSH_HOME': str(profile_root)}), patch('aiflame.integrations.cli_tools.install.subprocess.run') as run:
            for package in ({'dependencies': {'aiflame-deepseek-harness': 'file:someone-else'}}, {'dsh': {'profile': {'bundles': 'invalid'}}}):
                path.write_text(json.dumps(package))
                before = self.snapshot()
                for uninstall in (False, True):
                    with self.assertRaises(ValueError):
                        install('deepseek-harness', profile='default', uninstall=uninstall)
                    self.assertEqual(self.snapshot(), before)
                run.assert_not_called()
                self.assertFalse((self.root / 'runtime').exists())

    def test_deepseek_workspace_lock_backup_and_relative_dependency_repair(self):
        profile_root = self.root / 'dsh'
        directory = profile_root / 'profiles/default'
        directory.mkdir(parents=True)
        manifest = directory / 'package.json'
        original = {'dependencies': {'keep': '1'}, 'dsh': {'profile': {'bundles': ['keep']}}}
        manifest.write_text(json.dumps(original))
        lock = directory / 'pnpm-lock.yaml'
        lock.write_text('original lock\n')
        (directory / 'pnpm-workspace.yaml').write_text('packages: []\n')
        original_bytes = manifest.read_bytes()
        with patch.dict(os.environ, {'DSH_HOME': str(profile_root)}), patch('aiflame.integrations.cli_tools.install.subprocess.run') as run:
            run.return_value.returncode = 0

            def added(arguments, **kwargs):
                package = json.loads(manifest.read_text())
                bundle = Path(arguments[-1].removeprefix('file:'))
                package['dependencies']['aiflame-deepseek-harness'] = 'file:' + os.path.relpath(bundle.resolve(), directory.resolve())
                package['dsh']['profile']['bundles'] = ['keep', 'aiflame-deepseek-harness']
                manifest.write_text(json.dumps(package))
                lock.write_text('updated lock\n')
                return run.return_value

            run.side_effect = added
            install('deepseek-harness', profile='default')
            self.assertIn('-w', run.call_args.args[0])
            self.assertEqual(manifest.with_name('package.json.seeend-backup-1').read_bytes(), original_bytes)
            self.assertEqual(lock.with_name('pnpm-lock.yaml.seeend-backup-1').read_text(), 'original lock\n')
            install('deepseek-harness', profile='default')
            self.assertEqual(json.loads(manifest.read_text())['dependencies']['keep'], '1')
            self.assertEqual(run.call_count, 2)

    def test_deepseek_manager_failure_reports_action_without_raw_output(self):
        profile_root = self.root / 'dsh'
        directory = profile_root / 'profiles/default'
        directory.mkdir(parents=True)
        (directory / 'package.json').write_text('{}')
        with patch.dict(os.environ, {'DSH_HOME': str(profile_root)}), patch('aiflame.integrations.cli_tools.install.subprocess.run') as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ''
            for failure, expected in [('EACCES', '权限'), ('ERR_PNPM_FETCH', '网络'), ('ERR_PNPM_ADDING_TO_ROOT', '-w')]:
                run.return_value.stderr = failure + ' secret-token'
                with self.assertRaises(RuntimeError) as caught:
                    install('deepseek-harness', profile='default')
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn('secret-token', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
