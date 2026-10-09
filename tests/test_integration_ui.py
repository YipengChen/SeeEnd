"""Native incremental setup guidance and check feedback without ports or config writes."""

import json
import os
from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.widgets import Dashboard
    from aiflame.integrations.cli_tools.guidance import integration_guide


if HAS_QT:
    class OfflineAPI(QObject):
        error = Signal(str)

        def __init__(self, parent, port):
            super().__init__(parent)
            self.calls = []
            self.reports = {}

        def request(self, path, callback=None, **kwargs):
            self.calls.append((path, kwargs))
            query = {key: value[0] for key, value in parse_qs(urlsplit(path).query).items()}
            if path.startswith('/api/integration-guide?'):
                callback(integration_guide(query['source'], query.get('config_dir'), query.get('profile')))
            elif path.startswith('/api/integration-check?'):
                report = self.reports.get(query['source'])
                if report:
                    callback(report)

        def close(self):
            pass


@unittest.skipUnless(HAS_QT, 'Install desktop dependencies')
class IntegrationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'AIFLAME_DATA_DIR': self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        with patch('aiflame.desktop.widgets.LocalAPI', OfflineAPI):
            self.widget = Dashboard()
        self.addCleanup(self.widget.close)
        self.addCleanup(self.widget.shutdown)

    def test_all_six_have_read_only_guides_and_actual_selected_paths(self):
        self.assertEqual(set(self.widget.integration_guides), {'codex', 'claude-code', 'qoder', 'gemini-cli', 'opencode', 'deepseek-harness'})
        directory = str(Path(self.temp.name) / 'custom codex')
        self.widget.cli_config_dirs['codex'].setText(directory)
        self.widget.load_integration_guide('codex')
        content = self.widget.integration_guides['codex'].text()
        self.assertIn(str(Path(directory).resolve() / 'hooks.json'), content)
        self.assertIn('不替换整个配置', content)
        self.assertIn('信任', content)
        snippet = self.widget.integration_snippets['codex'].toPlainText()
        self.assertIn('Stop', json.loads(snippet)['hooks'])
        self.assertTrue(self.widget.integration_copy_buttons['codex'].isEnabled())
        self.assertFalse(Path(directory).exists())

    def test_checks_display_concrete_error_or_verified_state_and_dont_replace_directory(self):
        self.widget.cli_config_dirs['claude-code'].setText('/tmp/selected cli')
        self.widget.api.reports['claude-code'] = {
            'source_id': 'claude-code', 'configuration_ready': False, 'status': 'configuration_error',
            'summary': '接入配置有问题：settings.json 缺少 Stop', 'next_action': '自动连接 / 修复',
            'checks': [{'status': 'error', 'message': 'Stop：缺少 SeeEnd Hook', 'action': '增量添加'}],
            'signals': {}, 'issues': ['缺少 Stop'],
        }
        self.widget.check_cli('claude-code')
        self.assertIn('缺少 Stop', self.widget.cli_messages['claude-code'].text())
        self.assertIn('增量添加', self.widget.cli_details['claude-code'].text())
        self.assertEqual(self.widget.cli_config_dirs['claude-code'].text(), '/tmp/selected cli')
        self.widget.api.reports['claude-code'].update(configuration_ready=True, status='connected',
                                                    summary='接入正常：已收到同一真实任务的开始与完成。', checks=[], issues=[])
        self.widget.check_cli('claude-code')
        self.assertIn('接入正常', self.widget.cli_messages['claude-code'].text())

    def test_codex_checks_always_have_visible_feedback_when_ready_but_not_verified(self):
        self.widget.api.reports['codex'] = {'configuration_ready': True, 'status': 'ready_unverified',
                                            'summary': '配置已就绪，但尚未验证真实任务接入。', 'checks': [], 'signals': {}}
        self.widget.check_codex()
        self.assertIn('尚未验证', self.widget.codex_message.text())
        self.assertFalse(self.widget.codex_message.isHidden())
        self.assertIn('尚未验证', self.widget.codex_connection_details.text())

    def test_compact_feedback_and_single_profile_need_no_extra_selection(self):
        self.widget.api.reports['deepseek-harness'] = {
            'source_id': 'deepseek-harness', 'configuration_ready': False, 'status': 'configuration_error',
            'summary': '详细的插件诊断', 'feedback': '请选择 Profile 后连接。', 'connect_label': '连接',
            'checks': [], 'signals': {}, 'profiles': ['default'],
        }
        self.widget.check_cli('deepseek-harness')
        self.assertEqual(self.widget.cli_profiles['deepseek-harness'].currentData(), 'default')
        self.assertEqual(self.widget.cli_messages['deepseek-harness'].text(), '请选择 Profile 后连接。')
        self.assertIn('详细的插件诊断', self.widget.cli_details['deepseek-harness'].text())
        self.assertTrue(all(not kwargs.get('body') for _, kwargs in self.widget.api.calls))

    def test_web_icons_titles_and_overview_open_matching_urls_without_changing_sources(self):
        from PySide6.QtWidgets import QPushButton
        from aiflame.integrations.chatgpt_web.host import WEB_URLS
        with patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=True) as open_url:
            for source, url in WEB_URLS.items():
                with self.subTest(source=source):
                    enabled = self.widget.source_checks[source].isChecked()
                    buttons = self.widget.source_rows[source].findChildren(QPushButton)
                    self.assertEqual(len(buttons), 2)
                    for button in [*buttons, self.widget.home_source_icons[source]]:
                        button.click()
                        self.assertEqual(open_url.call_args.args[0].toString(), url)
                    self.assertEqual(self.widget.source_checks[source].isChecked(), enabled)
