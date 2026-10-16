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
        self.assertEqual([self.widget.source_category.itemData(index)
                          for index in range(self.widget.source_category.count())], ['web', 'cli'])
        self.assertEqual(set(self.widget.integration_help_texts), {'web', 'cli'})
        self.assertTrue(all(self.widget.source_checks[source].isChecked() for source in NAMES))
        self.assertTrue({'local-script', 'manual', 'demo'}.isdisjoint(self.widget.source_checks))

    def test_all_six_have_names_and_switches_without_automatic_diagnostics(self):
        self.widget.api.calls.clear()
        self.widget.navigate(2)
        self.widget.source_category.setCurrentIndex(self.widget.source_category.findData('cli'))
        section = self.widget.source_sections['cli'][0]
        self.app.processEvents()
        buttons = [button for button in section.findChildren(QPushButton) if button.text() and button.isVisible()]
        self.assertEqual([button.text() for button in buttons], ['复制安装指令'])
        self.assertFalse(section.findChildren(Disclosure))
        self.assertFalse(section.findChildren(QLineEdit))
        self.assertFalse(section.findChildren(QComboBox))
        for source in NAMES:
            self.assertIn(source, self.widget.source_rows)
            self.assertIn(source, self.widget.source_checks)
        self.assertFalse(any(label.property('role') == 'sourceStatus'
                             for label in section.findChildren(QLabel)))
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

    def test_task_evidence_does_not_change_integration_ui_or_add_verification_labels(self):
        from copy import deepcopy
        original = self.engine.snapshot()
        self.widget.navigate(2)
        for status in ('waiting', 'working', 'verified', 'unknown'):
            snapshot = deepcopy(original)
            snapshot['verification'] = {source['id']: {'status': status} for source in snapshot['sources']}
            self.widget.render({**snapshot, 'maintenance': False})
            self.assertEqual(self.widget.browser_install_button.text(), '安装浏览器扩展')
            for category in ('web', 'cli'):
                self.widget.source_category.setCurrentIndex(self.widget.source_category.findData(category))
                self.app.processEvents()
                text = '\n'.join(label.text() for label in self.widget.source_sections[category][0].findChildren(QLabel)
                                 if label.isVisible())
                self.assertNotIn('验证', text)
                self.assertFalse(any(button.text() == '去验证'
                                     for button in self.widget.source_sections[category][0].findChildren(QPushButton)))

    def test_switch_saves_only_reminder_setting_and_preserves_skill_entry(self):
        self.widget.api.calls.clear()
        self.widget.source_checks['claude-code'].click()
        posts = [(path, kwargs['body']) for path, kwargs in self.widget.api.calls if kwargs.get('body')]
        self.assertEqual(posts, [('/api/settings', {'source_enabled': {
            **self.engine.settings['source_enabled'], 'claude-code': False}})])
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

    def test_hover_help_beside_title_follows_category_without_repeated_statuses(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent
        self.widget.navigate(2)
        help_button = self.widget.integration_help
        self.app.processEvents()
        title = next(label for label in self.widget.tabs.widget(2).findChildren(QLabel)
                     if label.text() == '接入应用')
        title_position = title.mapToGlobal(QPoint(0, 0))
        help_position = help_button.mapToGlobal(QPoint(0, 0))
        self.assertGreaterEqual(help_position.x(), title_position.x() + title.width())
        self.assertLessEqual(help_position.x() - title_position.x() - title.width(), 8)
        self.assertLess(abs(help_position.y() + help_button.height() / 2
                            - title_position.y() - title.height() / 2), 2)
        self.assertFalse(self.widget.tabs.widget(2).findChildren(Disclosure))
        for category, phrase in [('cli', 'SeeEnd Skill'), ('web', 'Chrome')]:
            with self.subTest(category=category):
                self.widget.source_category.setCurrentIndex(self.widget.source_category.findData(category))
                self.app.processEvents()
                text = help_button.toolTip()
                self.assertIn(phrase, text)
                self.assertEqual(help_button.accessibleDescription(), text)
                self.assertNotIn('来源状态', text)
                self.assertNotIn('Yeelight', text)
                self.assertLess(len(text), 100)
                event = QHelpEvent(QEvent.Type.ToolTip, QPoint(0, 0), help_position)
                with patch('aiflame.desktop.components.QToolTip.showText') as show:
                    self.app.sendEvent(help_button, event)
                self.assertIn(text, show.call_args.args[1])
        set_language('en')
        self.app.processEvents()
        for category in ('cli', 'web'):
            self.widget.source_category.setCurrentIndex(self.widget.source_category.findData(category))
            self.assertNotRegex(help_button.toolTip(), r'[\u4e00-\u9fff]')
            self.assertNotRegex(help_button.accessibleDescription(), r'[\u4e00-\u9fff]')

    def test_web_icons_titles_and_overview_open_matching_urls_without_changing_sources(self):
        from aiflame.integrations.chatgpt_web.host import WEB_URLS
        from aiflame.integrations.cli_tools.providers import REPOSITORY_URLS
        with patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=True) as open_url:
            for source, url in WEB_URLS.items():
                with self.subTest(source=source):
                    enabled = self.widget.source_checks[source].isChecked()
                    buttons = [button for button in self.widget.source_rows[source].findChildren(QPushButton)
                               if button.objectName() == 'sourceLink']
                    self.assertEqual(len(buttons), 1)
                    self.assertEqual(buttons[0].text(), '')
                    self.assertFalse(buttons[0].icon().isNull())
                    self.assertTrue(buttons[0].toolTip())
                    self.assertTrue(buttons[0].accessibleName())
                    for button in [*buttons, self.widget.home_source_icons[source]]:
                        button.click()
                        self.assertEqual(open_url.call_args.args[0].toString(), url)
                    self.assertEqual(self.widget.source_checks[source].isChecked(), enabled)
            for source, url in REPOSITORY_URLS.items():
                with self.subTest(source=source):
                    enabled = self.widget.source_checks[source].isChecked()
                    self.widget.home_source_icons[source].click()
                    self.assertEqual(open_url.call_args.args[0].toString(), url)
                    self.assertEqual(self.widget.tabs.currentIndex(), 0)
                    self.assertEqual(self.widget.source_checks[source].isChecked(), enabled)
