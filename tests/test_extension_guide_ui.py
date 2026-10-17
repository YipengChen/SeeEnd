"""Regression checks for the two-action extension setup and failure recovery."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import subprocess
import unittest
from unittest.mock import patch, Mock
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QPushButton
from aiflame.desktop.extension_guide import ExtensionGuide
from aiflame.desktop.localization import set_language, QLabel as LocalizedLabel
from aiflame.desktop.theme import STYLE
from aiflame.desktop.browser_launch import open_in_chrome
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES
from aiflame.integrations.chatgpt_web.guide import STORE_URL


class MemoryAPI:
    def __init__(self, parent):
        self.parent = parent
        self.defer = False
        self.waiting = []
        self.failed = False

    def request(self, path, received, *args, failed=None):
        data = {'path': '/tmp/SeeEnd/BrowserExtension', 'prepared': self.parent.prepared} if path == '/api/browser-extension' else copy.deepcopy(self.parent.model)
        if self.defer:
            self.waiting.append((received, failed, data))
        elif self.failed:
            if failed:
                failed()
        else:
            received(data)


class MemoryParent(QWidget):
    def __init__(self, prepared=True):
        super().__init__()
        self.prepared = prepared
        self.model = {'sources': [{'id': source, 'enabled': source == 'chatgpt-web'} for source in WEB_SOURCES], 'settings': {}, 'display': {}, 'verification': {}}
        self.api = MemoryAPI(self)
        self.actions = []
        self.fail_action = False
        self.message_error = False
        self.message = LocalizedLabel('')

    def act(self, path, body, received, failed):
        self.actions.append((path, body))
        if self.fail_action:
            failed()
            return
        if path == '/api/browser-extension/prepare':
            self.prepared = True
            received({'prepared': True, 'path': '/tmp/SeeEnd/BrowserExtension'})
        elif path == '/api/settings':
            for row in self.model['sources']:
                if row['id'] in body['source_enabled']:
                    row['enabled'] = body['source_enabled'][row['id']]
            received(copy.deepcopy(self.model))
        else:
            received({'message': 'configured'})

    def navigate(self, page):
        self.page = page


class ExtensionGuideUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        set_language('zh-CN')
        self.parent = MemoryParent()
        self.guide = ExtensionGuide(self.parent)
        self.guide.show()
        self.flush()

    def tearDown(self):
        self.guide.close()
        self.parent.deleteLater()
        self.flush()
        set_language('zh-CN')

    def flush(self):
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_only_two_actions_and_no_site_selection_or_test(self):
        from PySide6.QtWidgets import QCheckBox, QComboBox
        self.assertEqual([title.text() for title in self.guide.titles], ['1. 安装扩展', '2. 允许连接'])
        visible = [button.text() for button in self.guide.findChildren(QPushButton) if button.isVisible()]
        self.assertEqual(visible, ['前往应用商店', '允许连接'])
        self.assertEqual(self.guide.findChildren(QCheckBox), [])
        self.assertEqual(self.guide.findChildren(QComboBox), [])
        self.assertFalse(self.guide.status.isVisible())
        self.assertFalse(self.guide.store_status.isVisible())

    def test_store_launch_does_not_claim_installation_or_prepare_local_files(self):
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True) as launch:
            self.guide.store_button.click()
        launch.assert_called_once_with(STORE_URL)
        self.assertFalse(self.guide.configured)
        self.assertEqual(self.parent.actions, [])
        self.assertFalse(self.guide.store_status.isVisible())
        self.assertEqual(self.guide.primary.text(), '允许连接')

    def test_source_entry_points_reuse_the_generic_guide(self):
        from aiflame.desktop.widgets import Dashboard
        self.parent.extension_guide = self.guide
        for source in WEB_SOURCES:
            self.guide.close()
            Dashboard.open_browser_extension_guide(self.parent, source)
            self.assertIs(self.parent.extension_guide, self.guide)
            self.assertTrue(self.guide.isVisible())
            self.assertEqual(self.guide.primary.text(), '允许连接')

    def test_store_failure_exposes_copy_link_and_retry(self):
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=False):
            self.guide.store_button.click()
        self.assertTrue(self.guide.copy_button.isVisible())
        self.assertIn('未能打开 Chrome', self.guide.store_status.text())
        self.guide.copy_button.click()
        self.assertEqual(QApplication.clipboard().text(), STORE_URL)
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True):
            self.guide.store_button.click()
        self.assertFalse(self.guide.copy_button.isVisible())
        self.assertFalse(self.guide.store_status.isVisible())

    def test_connection_is_generic_preserves_source_switches_and_needs_no_task(self):
        self.parent.model['sources'][0]['enabled'] = False
        sources = copy.deepcopy(self.parent.model['sources'])
        with patch.object(self.parent.api, 'request') as request:
            self.guide.primary.click()
            self.assertTrue(self.guide.configured)
            self.assertEqual(self.guide.primary.text(), '完成')
            self.assertEqual(self.guide.status.text(), '已允许扩展连接 SeeEnd。')
            request.assert_not_called()
        self.assertEqual(self.parent.actions, [('/api/integrations/install', {'source': 'chatgpt-web'})])
        self.assertEqual(self.parent.model['sources'], sources)
        self.guide.primary.click()
        self.assertFalse(self.guide.isVisible())
        self.assertEqual(len(self.parent.actions), 1)

    def test_connection_failure_shows_reason_and_can_retry(self):
        self.parent.fail_action = self.parent.message_error = True
        self.parent.message.setText('Permission denied')
        self.guide.primary.click()
        self.assertFalse(self.guide.configured)
        self.assertTrue(self.guide.primary.isEnabled())
        self.assertEqual(self.guide.primary.text(), '允许连接')
        self.assertIn('请重试', self.guide.status.text())
        self.assertIn('Permission denied', self.guide.status.text())
        self.parent.fail_action = self.parent.message_error = False
        self.guide.primary.click()
        self.assertTrue(self.guide.configured)
        self.assertNotIn('Permission denied', self.guide.status.text())

    def test_pending_connection_disables_controls_and_rejects_duplicate_requests(self):
        with patch.object(self.parent, 'act') as act:
            self.guide.primary.click()
            self.assertEqual(self.guide.primary.text(), '正在允许连接…')
            self.assertFalse(self.guide.primary.isEnabled())
            self.assertFalse(self.guide.store_button.isEnabled())
            self.guide.connect()
            act.assert_called_once()
            act.call_args.args[2]({'message': 'configured'})
        self.assertTrue(self.guide.primary.isEnabled())
        self.assertTrue(self.guide.store_button.isEnabled())
        self.assertTrue(self.guide.configured)

    def test_language_switch_and_minimum_size_keep_controls_visible(self):
        self.guide.resize(480, 340)
        for language in ('zh-CN', 'en'):
            set_language(language)
            self.flush()
            if language == 'en':
                self.assertEqual(self.guide.titles[1].text(), '2. Allow connection')
                self.assertNotRegex(self.guide.windowTitle(), '[\u4e00-\u9fff]')
                for label in self.guide.findChildren(QLabel):
                    if label.isVisible():
                        self.assertNotRegex(label.text(), '[\u4e00-\u9fff]')
            for control in (*self.guide.titles, self.guide.store_button, self.guide.primary):
                origin = control.mapTo(self.guide, QPoint(0, 0))
                self.assertGreaterEqual(origin.y(), 0)
                self.assertLessEqual(origin.y() + control.height(), self.guide.height())
                self.assertLessEqual(origin.x() + control.width(), self.guide.width())
        self.guide.primary.click()
        self.assertNotRegex(self.guide.status.text(), '[\u4e00-\u9fff]')
        self.assertEqual(self.guide.primary.text(), 'Done')

    def test_error_content_remains_visible_at_minimum_size(self):
        self.guide.resize(480, 340)
        set_language('en')
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=False):
            self.guide.store_button.click()
        self.parent.fail_action = True
        self.guide.primary.click()
        self.flush()
        for control in (self.guide.store_status, self.guide.copy_button, self.guide.primary, self.guide.status):
            self.assertTrue(control.isVisible())
            origin = control.mapTo(self.guide, QPoint(0, 0))
            self.assertLessEqual(origin.y() + control.height(), self.guide.height())
            self.assertLessEqual(origin.x() + control.width(), self.guide.width())


class ChromeLaunchTests(unittest.TestCase):
    def test_macos_explicit_browser_and_failure(self):
        with patch('aiflame.desktop.browser_launch.sys.platform', 'darwin'), patch('aiflame.desktop.browser_launch.subprocess.run', return_value=Mock(returncode=0)) as run:
            self.assertTrue(open_in_chrome('https://chatgpt.com/'))
            self.assertEqual(run.call_args.args[0], ['/usr/bin/open', '-b', 'com.google.Chrome', 'https://chatgpt.com/'])
            run.return_value.returncode = 1
            self.assertFalse(open_in_chrome('https://chatgpt.com/'))
            run.side_effect = subprocess.TimeoutExpired('open', 5)
            self.assertFalse(open_in_chrome('https://chatgpt.com/'))

    def test_missing_chrome_has_no_default_browser_fallback(self):
        with patch('aiflame.desktop.browser_launch.sys.platform', 'linux'), patch('aiflame.desktop.browser_launch.shutil.which', return_value=None), patch('aiflame.desktop.browser_launch.subprocess.run') as run:
            self.assertFalse(open_in_chrome('https://chatgpt.com/'))
            run.assert_not_called()
