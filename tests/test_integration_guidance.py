"""Setup plans, actionable diagnostics and authenticated handlers without sockets."""

import io
import json
import os
from pathlib import Path
from email.message import Message
from types import SimpleNamespace
import tempfile
import unittest
import uuid
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from aiflame.core.events import make_event
from aiflame.core.verification import verification_summary, begin_verification
from aiflame.hub.server import Handler, Hub
from aiflame.integrations.cli_tools.guidance import DOCS, VERIFICATION_PROMPT, integration_guide
from aiflame.integrations.cli_tools.install import install
from aiflame.integrations.cli_tools.diagnostics import diagnostics
from aiflame.integrations.codex.install import install as install_codex
from aiflame.integrations.codex.diagnostics import diagnostics as codex_diagnostics


class IntegrationGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'AIFLAME_DATA_DIR': str(self.root / 'runtime'),
                                         'CODEX_HOME': str(self.root / 'codex'),
                                         'DSH_HOME': str(self.root / 'dsh')})
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.root / 'hub').mkdir()
        self.hub = Hub(port=8765, runtime=self.root / 'hub', config={'dashboard': 'fixture', 'tokens': {'claude-code': 'fixture-token'}})
        self.addCleanup(self.hub.engine.db.close)
        self.engine = self.hub.engine

    def request(self, path, body=None, identity='dashboard'):
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(context=self.hub, server_address=('127.0.0.1', 8765))
        handler.path = path
        handler.headers = Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        if identity == 'dashboard':
            handler.headers['Cookie'] = 'aiflame=fixture'
        elif identity:
            handler.headers['Authorization'] = 'Bearer fixture-token'
        handler.reply = Mock()
        if body is None:
            handler.do_GET()
        else:
            encoded = json.dumps(body).encode()
            handler.headers['Content-Type'] = 'application/json'
            handler.headers['Content-Length'] = str(len(encoded))
            handler.rfile = io.BytesIO(encoded)
            handler.do_POST()
        return handler.reply.call_args.args[:2]

    def test_guides_are_read_only_and_list_all_six_tools(self):
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        for source in DOCS:
            guide = integration_guide(source, str(self.root / 'custom') if source != 'deepseek-harness' else None)
            self.assertTrue(guide['files'])
            self.assertIn('增量', guide['preservation'])
            self.assertTrue(guide['preparation'] and guide['next_steps'])
            self.assertTrue(guide['snippet'])
            first_use = guide['onboarding']
            self.assertTrue(first_use['intro'])
            self.assertTrue(first_use['preparation'] and first_use['reload_steps'] and first_use['limitations'])
            self.assertEqual(first_use['verification_prompt'], VERIFICATION_PROMPT)
            self.assertIn('SeeEnd Skill', guide['automatic'])
            self.assertEqual(guide['skill_url'], 'https://github.com/YipengChen/SeeEnd-skill')
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertFalse((self.root / 'runtime').exists())

    def test_public_contract_and_evidence_are_authenticated_read_only_metadata(self):
        from aiflame.integrations.registry import integration_contract
        for path in ('/api/integration-contract', '/api/verification-evidence?source=codex'):
            self.assertEqual(self.request(path, identity=None)[0], 401)
            self.assertEqual(self.request(path, identity='source')[0], 403)
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*'))
        code, contract = self.request('/api/integration-contract')
        self.assertEqual(code, 200)
        self.assertEqual(contract, integration_contract())
        self.assertEqual(set(contract['capabilities']['native_sources']), set(DOCS))
        self.assertEqual(contract['optional_fields'], ['reason', 'device_id', 'return_target', 'return_context'])
        self.assertTrue(contract['capabilities']['device_targeting'])
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob('*')))
        for path in ('/api/verification-evidence', '/api/verification-evidence?source=codex&source=qoder',
                     '/api/verification-evidence?source=codex&extra=x', '/api/verification-evidence?source=manual'):
            self.assertEqual(self.request(path)[0], 400)
        self.engine.accept(make_event('codex', 'before-window', 'working'), 'codex')
        begin_verification(self.engine, 'codex')
        for task, simulated in (('simulation', True), ('current-real-task', False)):
            for seq, state in ((1, 'working'), (2, 'done')):
                event = make_event('codex', task, state, seq)
                if simulated:
                    self.engine.db.execute('INSERT INTO self_tests VALUES (?,?)', (event['event_id'], self.engine.wall()))
                    self.engine.db.commit()
                self.engine.accept(event, 'codex')
        code, evidence = self.request('/api/verification-evidence?source=codex')
        self.assertEqual(code, 200)
        self.assertEqual(evidence['summary']['status'], 'verified')
        self.assertEqual({event['task_id'] for event in evidence['events']}, {'simulation', 'current-real-task'})
        self.assertEqual(sum(event['simulated'] for event in evidence['events']), 2)
        self.assertEqual(set(evidence['events'][0]), {'event_id', 'task_id', 'seq', 'state', 'reason', 'created', 'status', 'simulated'})
        self.assertNotIn('fixture-token', json.dumps(evidence))

    def test_repair_keeps_disabled_source_and_partial_uninstall_keeps_other_config(self):
        for source in ('codex', 'claude-code', 'opencode'):
            with self.subTest(source=source):
                first, second = self.root / (source + '-one'), self.root / (source + '-two')
                for directory in (first, second):
                    self.assertEqual(self.request('/api/integrations/install', {'source': source, 'config_dir': str(directory)})[0], 200)
                self.engine.update_settings({'source_enabled': {**self.engine.settings['source_enabled'], source: False}})
                self.assertEqual(self.request('/api/integrations/install', {'source': source, 'config_dir': str(first)})[0], 200)
                self.assertFalse(self.engine.settings['source_enabled'][source])
                self.engine.update_settings({'source_enabled': {**self.engine.settings['source_enabled'], source: True}})
                self.assertEqual(self.request('/api/integrations/uninstall', {'source': source, 'config_dir': str(first)})[0], 200)
                self.assertTrue(self.engine.settings['source_enabled'][source])
                self.assertEqual(self.request('/api/integrations/uninstall', {'source': source, 'config_dir': str(second)})[0], 200)
                self.assertFalse(self.engine.settings['source_enabled'][source])

    def test_opencode_owner_record_is_part_of_configuration_readiness(self):
        directory = self.root / 'opencode-owner'
        install('opencode', config_dir=directory)
        manifest = directory / 'aiflame/.seeend-owned.json'
        original = manifest.read_bytes()
        manifest.write_text(json.dumps({'owner': 'another-owner', 'files': {}}))
        result = diagnostics(self.engine.db, source='opencode')['integrations'][0]
        self.assertFalse(result['configuration_ready'])
        self.assertTrue(any('归属' in issue for issue in result['issues']))
        manifest.write_bytes(original)
        self.assertTrue(diagnostics(self.engine.db, source='opencode')['integrations'][0]['configuration_ready'])

    def test_deepseek_directory_rejection_has_stable_error_code(self):
        code, result = self.request('/api/integrations/install', {'source': 'deepseek-harness', 'config_dir': str(self.root / 'custom'), 'profile': 'default'})
        self.assertEqual(code, 409)
        self.assertEqual(result['error_code'], 'config_invalid')
        self.assertFalse((self.root / 'custom').exists())
        for route in ('integration-guide', 'integration-check'):
            code, result = self.request('/api/' + route + '?' + urlencode({'source': 'deepseek-harness', 'config_dir': str(self.root / 'custom')}))
            self.assertEqual(code, 400)
            self.assertEqual(result['error_code'], 'config_invalid')

    def test_adapter_self_tests_route_to_configured_port_without_a_live_server(self):
        from aiflame.integrations.codex.events import from_hook
        from aiflame.integrations.chatgpt_web.host import read_message, write_message
        self.hub.port = 19321

        def helper(mode, *args, input=None, **kwargs):
            # This isolated receiver refuses traffic that would go to default 8765.
            port_index = args.index('--port')
            self.assertEqual(int(args[port_index + 1]), self.hub.port)
            if mode == '--codex-hook':
                event = from_hook(json.loads(input), self.root / 'runtime')
            else:
                self.assertEqual(mode, '--browser-host')
                event = read_message(io.BytesIO(input))
            self.engine.accept(event, event['source_id'])
            output = io.BytesIO()
            write_message(output, {'ok': True})
            return SimpleNamespace(returncode=0, stdout=output.getvalue())

        with patch('aiflame.integrations.testing.run', side_effect=helper), patch.object(self.hub.stopping, 'wait', return_value=False):
            for source in ('codex', 'chatgpt-web'):
                self.hub.tests.job = {'state': 'running', 'events': []}
                self.hub.tests._run(source)
                self.assertEqual(self.hub.tests.job['state'], 'succeeded')
                self.assertEqual(len(self.hub.tests.job['events']), 2)
                self.assertEqual(verification_summary(self.engine)[source]['complete_tasks'], 0)

    def test_evidence_reports_when_latest_records_are_truncated(self):
        now = self.engine.wall()
        records = [(str(uuid.uuid4()), 'codex', 'task-' + str(index), 1, 'idle', None, now + index / 10000, 'screen')
                   for index in range(1001)]
        self.engine.db.executemany('INSERT INTO events VALUES (?,?,?,?,?,?,?,?)', records)
        self.engine.db.commit()
        code, evidence = self.request('/api/verification-evidence?source=codex')
        self.assertEqual(code, 200)
        self.assertEqual(evidence['total'], 1001)
        self.assertEqual(len(evidence['events']), evidence['limit'])
        self.assertTrue(evidence['truncated'])
        self.assertEqual(evidence['events'][0]['task_id'], 'task-1')
        self.assertEqual(evidence['events'][-1]['task_id'], 'task-1000')

    def test_reload_instructions_match_the_tool_instead_of_generic_restart(self):
        plans = {source: integration_guide(source)['onboarding'] for source in DOCS}
        codex = '\n'.join(plans['codex']['reload_steps'])
        self.assertIn('/hooks', codex)
        self.assertIn('信任当前定义', codex)
        self.assertIn('App 或 IDE 版本', codex)
        self.assertIn('自动读取配置变更', '\n'.join(plans['claude-code']['reload_steps']))
        self.assertIn('QoderWork', '\n'.join(plans['qoder']['reload_steps']))
        gemini = '\n'.join(plans['gemini-cli']['reload_steps'])
        self.assertIn('/hooks panel', gemini)
        self.assertNotIn('/hooks list', gemini)
        opencode = '\n'.join(plans['opencode']['reload_steps'])
        self.assertIn('服务进程', opencode)
        self.assertIn('只刷新客户端界面不足', opencode)

    def test_profile_guidance_uses_the_selected_profile_and_explains_its_scope(self):
        guide = integration_guide('deepseek-harness', profile='daily work')
        plan = guide['onboarding']
        self.assertIn('daily work', '\n'.join(plan['reload_steps']))
        self.assertIn(str(self.root / 'dsh/profiles/daily work/package.json'), guide['files'])
        preparation = '\n'.join(plan['preparation'])
        self.assertIn('一套配置', preparation)
        self.assertIn('其他 Profile', preparation)
        self.assertIn('dsh 和 pnpm', preparation)
        self.assertTrue(any('WSL' in item and '不会自动读取' in item for item in plan['limitations']))
        self.assertFalse((self.root / 'dsh').exists())

    def test_json_snippet_matches_installed_entries_without_replacing_original(self):
        for source in ('codex', 'claude-code', 'qoder', 'gemini-cli'):
            directory = self.root / source
            directory.mkdir(exist_ok=True)
            path = directory / ('hooks.json' if source == 'codex' else 'settings.json')
            original = {'model': {'keep': True}, 'hooks': {'OtherEvent': [{'hooks': [{'command': 'keep'}]}]}}
            path.write_text(json.dumps(original))
            guide = integration_guide(source, str(directory))
            install_codex(directory) if source == 'codex' else install(source, config_dir=directory)
            data = json.loads(path.read_text())
            for event, groups in json.loads(guide['snippet'])['hooks'].items():
                self.assertEqual(data['hooks'][event], groups)
            self.assertEqual(data['model'], original['model'])
            self.assertEqual(data['hooks']['OtherEvent'], original['hooks']['OtherEvent'])
            self.assertEqual(guide['files'], [str(path.resolve())])

    def test_missing_file_and_invalid_json_report_exact_location_and_action(self):
        directory = self.root / 'missing'
        report = diagnostics(self.engine.db, source='qoder', config_dirs={'qoder': directory})['integrations'][0]
        self.assertEqual(report['status'], 'configuration_error')
        self.assertIn(str(directory / 'settings.json'), report['summary'])
        self.assertIn('SeeEnd Skill', report['summary'])
        self.assertEqual(report['feedback'], '尚未接入。使用 SeeEnd Skill 安装并验证接入。')
        self.assertEqual(report['connect_label'], '使用 SeeEnd Skill')
        self.assertIn('SeeEnd Skill', report['next_action'])
        directory.mkdir()
        path = directory / 'settings.json'
        path.write_text('{\n BAD PRIVATE-TOKEN')
        report = diagnostics(self.engine.db, source='qoder', config_dirs={'qoder': directory})['integrations'][0]
        self.assertIn('第 2 行', report['summary'])
        self.assertNotIn('PRIVATE-TOKEN', str(report))
        self.assertEqual(path.read_text(), '{\n BAD PRIVATE-TOKEN')

    def test_disabled_hooks_and_stale_helper_are_explicit(self):
        directory = self.root / 'gemini'
        install('gemini-cli', config_dir=directory)
        path = directory / 'settings.json'
        data = json.loads(path.read_text())
        data['hooksConfig'] = {'enabled': False}
        path.write_text(json.dumps(data))
        report = diagnostics(self.engine.db, source='gemini-cli')['integrations'][0]
        self.assertFalse(report['configuration_ready'])
        self.assertIn('hooksConfig.enabled=false', str(report['issues']))
        self.assertIn('在 Gemini CLI 中', str(report['issues']))
        self.assertIn('Gemini CLI', report['next_action'])
        self.assertFalse(json.loads(path.read_text())['hooksConfig']['enabled'])
        data.pop('hooksConfig')
        data['hooks']['AfterAgent'][0]['hooks'][0]['command'] = '/Applications/Old/SeeEnd.app/Contents/MacOS/SeeEnd --cli-hook gemini-cli'
        path.write_text(json.dumps(data))
        report = diagnostics(self.engine.db, source='gemini-cli')['integrations'][0]
        self.assertTrue(any('旧版' in issue and 'AfterAgent' in issue for issue in report['issues']))

    def test_ready_does_not_mean_connected_and_simulation_never_qualifies(self):
        install('claude-code', config_dir=self.root / 'claude')
        self.engine.update_settings({'source_enabled': {**self.engine.settings['source_enabled'], 'claude-code': True}})
        def report():
            return diagnostics(self.engine.db, source='claude-code', source_enabled=self.engine.settings['source_enabled'], verification=verification_summary(self.engine))['integrations'][0]
        self.assertEqual(report()['status'], 'ready_unverified')
        self.assertIn('SeeEnd Skill', report()['feedback'])
        self.assertIn('加载与授权', report()['next_action'])
        for seq, state in ((1, 'working'), (2, 'done')):
            event = make_event('claude-code', 'simulation', state, seq)
            self.engine.db.execute('INSERT INTO self_tests VALUES (?,?)', (event['event_id'], 0))
            self.engine.db.commit()
            self.engine.accept(event, 'claude-code')
        self.assertEqual(report()['status'], 'ready_unverified')
        self.engine.accept(make_event('claude-code', 'real', 'working', 1), 'claude-code')
        self.assertEqual(report()['status'], 'receiving')
        self.engine.accept(make_event('claude-code', 'real', 'done', 2), 'claude-code')
        self.assertEqual(report()['status'], 'connected')
        self.assertIn('同一真实任务', report()['summary'])
        self.engine.update_settings({'source_enabled': {**self.engine.settings['source_enabled'], 'claude-code': False}})
        self.assertEqual(report()['status'], 'disabled')
        self.assertIn('开启此工具的来源开关', report()['feedback'])
        self.assertIn('SeeEnd Skill', report()['next_action'])

    def test_unrelated_start_and_finish_cannot_claim_connected(self):
        install_codex(self.root / 'codex')
        self.engine.accept(make_event('codex', 'start-only', 'working', 1), 'codex')
        self.engine.accept(make_event('codex', 'finish-only', 'done', 2), 'codex')
        result = codex_diagnostics(self.engine.db, enabled=True, verification=verification_summary(self.engine)['codex'])
        self.assertNotEqual(result['status'], 'connected')

    def test_open_code_reports_the_missing_component(self):
        directory = self.root / 'opencode'
        install('opencode', config_dir=directory)
        (directory / 'aiflame/index.mjs').unlink()
        report = diagnostics(self.engine.db, source='opencode')['integrations'][0]
        self.assertIn(str(directory / 'aiflame/index.mjs'), report['summary'])
        self.assertFalse(report['configuration_ready'])

    def test_deepseek_reports_selected_profile_instead_of_other_profile(self):
        profile_root = self.root / 'dsh/profiles'
        for name in ('one', 'two'):
            (profile_root / name).mkdir(parents=True)
            (profile_root / name / 'package.json').write_text('{}')
        report = diagnostics(self.engine.db, source='deepseek-harness', profiles={'deepseek-harness': 'two'})['integrations'][0]
        self.assertIn(str(profile_root / 'two/package.json'), report['summary'])
        self.assertIn('所选 Profile', report['summary'])

    def test_guide_and_check_routes_require_dashboard_auth_and_validate_query(self):
        for route in ('integration-guide', 'integration-check'):
            path = '/api/' + route + '?source=claude-code'
            self.assertEqual(self.request(path, identity=None)[0], 401)
            self.assertEqual(self.request(path, identity='source')[0], 403)
            self.assertEqual(self.request(path + '&source=qoder')[0], 400)
            self.assertEqual(self.request(path + '&unknown=x')[0], 400)
        self.assertEqual(self.request('/api/integration-guide?source=unknown')[0], 400)
        path = '/api/integration-check?' + urlencode({'source': 'qoder', 'config_dir': str(self.root / 'target')})
        code, data = self.request(path)
        self.assertEqual(code, 200)
        self.assertIn(str(self.root / 'target/settings.json'), data['summary'])
        self.assertFalse((self.root / 'target').exists())

    def test_auto_connect_codex_uses_selected_directory_and_restarts_evidence_window(self):
        directory = self.root / 'selected-codex'
        self.engine.accept(make_event('codex', 'previous', 'working', 1), 'codex')
        self.engine.accept(make_event('codex', 'previous', 'done', 2), 'codex')
        code, _ = self.request('/api/integrations/install', {'source': 'codex', 'config_dir': str(directory)})
        self.assertEqual(code, 200)
        self.assertTrue((directory / 'hooks.json').exists())
        self.assertFalse((self.root / 'codex').exists())
        code, result = self.request('/api/codex')
        self.assertEqual(code, 200)
        self.assertEqual(result['status'], 'ready_unverified')
        self.assertEqual(result['config_file'], str(directory / 'hooks.json'))

    def test_install_conflict_returns_precise_reason_without_mutation(self):
        directory = self.root / 'opencode'
        path = directory / 'aiflame/index.mjs'
        path.parent.mkdir(parents=True)
        path.write_text('third-party-plugin')
        code, result = self.request('/api/integrations/install', {'source': 'opencode', 'config_dir': str(directory)})
        self.assertEqual(code, 409)
        self.assertIn('同名', result['error'])
        self.assertIn(str(path), result['error'])
        self.assertEqual(path.read_text(), 'third-party-plugin')
        self.assertFalse((directory / 'plugins').exists())
