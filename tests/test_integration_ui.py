"""Shared Skill entry and source switches, without coding-tool configuration writes."""

import os
from importlib.util import find_spec
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication, QPushButton, QLineEdit, QComboBox, QLabel
    from aiflame.desktop.components import Disclosure
    from aiflame.desktop.localization import set_language
    from aiflame.desktop.widgets import Dashboard
    from aiflame.core.engine import Engine
    from aiflame.integrations.registry import SOURCES
    from aiflame.integrations.cli_tools.skill import NAMES, SKILL_URL, installation_prompt

    class OfflineAPI(QObject):
        error = Signal(str)

        def __init__(self, parent, port):
            super().__init__(parent)
            self.calls = []

        def request(self, path, callback=None, body=None, **kwargs):
            self.calls.append((path, {**kwargs, 'body': body}))

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
        set_language('zh-CN')
        self.addCleanup(lambda: set_language('zh-CN'))
        self.engine = Engine(Path(self.temp.name) / 'events.sqlite', sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        with patch('aiflame.desktop.widgets.LocalAPI', OfflineAPI):
            self.widget = Dashboard()
        self.addCleanup(self.widget.close)
        self.addCleanup(self.widget.shutdown)
        self.widget.ready = True
        self.widget.render({**self.engine.snapshot(), 'maintenance': False})
        self.widget.show()

    def test_all_six_have_only_status_and_switch_without_automatic_diagnostics(self):
        self.widget.api.calls.clear()
        self.widget.navigate(2)
        self.widget.source_category.setCurrentIndex(self.widget.source_category.findData('cli'))
        section = self.widget.source_sections['cli'][0]
        self.app.processEvents()
        buttons = [button for button in section.findChildren(QPushButton) if button.text() and button.isVisible()]
        self.assertEqual([button.text() for button in buttons], ['复制安装指令', '查看 Skill 仓库'])
        self.assertFalse(section.findChildren(Disclosure))
        self.assertFalse(section.findChildren(QLineEdit))
        self.assertFalse(section.findChildren(QComboBox))
        for source in NAMES:
            self.assertIn(source, self.widget.source_status)
            self.assertIn(source, self.widget.source_checks)
        self.assertFalse(any(path.startswith(('/api/integration-', '/api/cli-integrations'))
                             for path, _ in self.widget.api.calls))

    def test_shared_copy_is_short_and_does_not_install_or_change_sources(self):
        enabled = {source: self.widget.source_checks[source].isChecked() for source in NAMES}
        self.widget.api.calls.clear()
        self.widget.skill_copy_button.click()
        self.assertEqual(self.app.clipboard().text(), installation_prompt())
        self.assertEqual(len(self.app.clipboard().text().splitlines()), 2)
        self.assertFalse(self.widget.skill_notice.isHidden())
        self.assertFalse(self.widget.api.calls)
        self.assertEqual(enabled, {source: self.widget.source_checks[source].isChecked() for source in NAMES})

    def test_switch_saves_only_reminder_setting_and_preserves_skill_entry(self):
        self.widget.api.calls.clear()
        self.widget.source_checks['claude-code'].click()
        posts = [(path, kwargs['body']) for path, kwargs in self.widget.api.calls if kwargs.get('body')]
        self.assertEqual(posts, [('/api/settings', {'source_enabled': {
            **self.engine.settings['source_enabled'], 'claude-code': True}})])
        self.assertTrue(self.widget.skill_copy_button.isEnabled())

    def test_english_copy_and_repository_failure_offer_visible_feedback(self):
        set_language('en')
        self.widget.skill_copy_button.click()
        self.assertNotRegex(self.app.clipboard().text(), r'[\u4e00-\u9fff]')
        self.assertIn(SKILL_URL, self.app.clipboard().text())
        with patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=False) as opened:
            self.widget.open_coding_skill()
        self.assertEqual(opened.call_args.args[0].toString(), SKILL_URL)
        self.assertIn('browser', self.widget.skill_notice.text())
        self.assertFalse(self.widget.skill_notice.isHidden())

    def test_expanded_help_follows_category_without_other_types_or_repeated_statuses(self):
        self.widget.navigate(2)
        disclosure = self.widget.integration_help_disclosure
        disclosure.button.click()
        for category, phrase in [('cli', 'SeeEnd Skill'), ('web', 'Chrome'),
                                 ('desktop', 'macOS'), ('local', '本地 API')]:
            with self.subTest(category=category):
                self.widget.source_category.setCurrentIndex(self.widget.source_category.findData(category))
                self.app.processEvents()
                self.assertTrue(disclosure.button.isChecked())
                self.assertEqual([key for key, panel in self.widget.integration_help_panels.items()
                                  if panel.isVisible()], [category])
                panel = self.widget.integration_help_panels[category]
                text = '\n'.join(item.text() for item in panel.findChildren(QLabel) if item.isVisible())
                self.assertIn(phrase, text)
                self.assertNotIn('来源状态', text)
                self.assertNotIn('Yeelight', text)
                self.assertLess(len(text), 100)
        set_language('en')
        self.app.processEvents()
        for panel in self.widget.integration_help_panels.values():
            for label in panel.findChildren(QLabel):
                self.assertNotRegex(label.text(), r'[\u4e00-\u9fff]')

    def test_web_icons_titles_and_overview_open_matching_urls_without_changing_sources(self):
        from aiflame.integrations.chatgpt_web.host import WEB_URLS
        with patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=True) as open_url:
            for source, url in WEB_URLS.items():
                with self.subTest(source=source):
                    enabled = self.widget.source_checks[source].isChecked()
                    buttons = [button for button in self.widget.source_rows[source].findChildren(QPushButton)
                               if button.property('variant') in ('brand', 'webSourceTitle')]
                    self.assertEqual(len(buttons), 2)
                    for button in [*buttons, self.widget.home_source_icons[source]]:
                        button.click()
                        self.assertEqual(open_url.call_args.args[0].toString(), url)
                    self.assertEqual(self.widget.source_checks[source].isChecked(), enabled)
