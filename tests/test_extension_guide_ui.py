"""Regression checks for installation handoffs, reminder settings and recovery."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import subprocess
import unittest
from unittest.mock import patch, Mock
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QLabel, QLineEdit, QPushButton
from aiflame.desktop.extension_guide import ExtensionGuide
from aiflame.desktop.localization import set_language
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
        self.message = QLabel('')

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

    def final_stage(self):
        self.guide.checkpoint.setChecked(True)
        self.guide.go(1)
        self.flush()

    def evidence(self, status, started=101):
        self.parent.model['verification']['chatgpt-web'] = {'since': 100, 'status': status, 'started_at': started, 'last_seen': 110}
        self.guide.check_events()

    def test_store_launch_does_not_confirm_installation_or_prepare_local_files(self):
        self.guide.go(1)
        self.assertEqual(self.guide.step, 0)
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True) as launch:
            self.guide.primary.click()
        launch.assert_called_once_with(STORE_URL)
        self.assertFalse(self.guide.installed)
        self.assertFalse(self.guide.configured)
        self.assertEqual(self.parent.actions, [])
        self.guide.go(1)
        self.assertEqual(self.guide.step, 0)
        self.guide.checkpoint.setChecked(True)
        self.guide.primary.click()
        self.assertEqual(self.guide.step, 1)
        self.assertFalse(self.guide.configured)

    def test_store_launch_failure_has_copy_link_and_retry(self):
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=False):
            self.guide.primary.click()
        self.assertTrue(self.guide.primary.isEnabled())
        self.assertIn('未能打开 Chrome', self.guide.status.text())
        copy_link = next(button for button in self.guide.scroll.findChildren(QPushButton) if button.text() == '复制商店链接')
        copy_link.click()
        self.assertEqual(QApplication.clipboard().text(), STORE_URL)
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True):
            self.guide.primary.click()
        self.assertFalse(self.guide.store_error)
        self.assertFalse(self.guide.installed)
        self.assertIn('添加至 Chrome', self.guide.status.text())

    def test_site_selection_precedes_configuration_and_disabled_source_can_be_enabled(self):
        self.final_stage()
        self.guide.site_choice.setCurrentIndex(self.guide.site_choice.findData('gemini-web'))
        self.assertEqual(self.guide.primary.text(), '允许连接')
        self.guide.primary.click()
        self.assertEqual(self.guide.primary.text(), '开启此网站提醒')
        self.guide.primary.click()
        self.assertEqual(self.parent.actions[-1], ('/api/settings', {'source_enabled': {'gemini-web': True}}))
        self.assertTrue(next(row['enabled'] for row in self.parent.model['sources'] if row['id'] == 'chatgpt-web'))
        self.assertEqual(self.guide.primary.text(), '完成设置')

    def test_setup_completes_without_task_evidence_or_a_verification_request(self):
        self.final_stage()
        self.guide.connect()
        ready = self.guide.status.text()
        for status, started in [('waiting', 90), ('working', 101), ('verified', 101), ('unknown', 101)]:
            self.evidence(status, started)
            self.assertEqual(self.guide.status.text(), ready)
            self.assertEqual(self.guide.primary.text(), '完成设置')
            self.assertTrue(self.guide.primary.isEnabled())
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True) as launch:
            self.guide.open_site()
        launch.assert_called_once_with('https://chatgpt.com/')
        self.assertEqual(self.parent.actions, [('/api/integrations/install', {'source': 'chatgpt-web'})])
        self.guide.primary.click()
        self.assertFalse(self.guide.isVisible())

    def test_read_error_and_optional_chrome_launch_failure_have_recovery(self):
        self.final_stage()
        self.guide.connect()
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=False):
            self.guide.open_site()
        self.assertIn('未能打开 Chrome', self.guide.status.text())
        copy_link = next(button for button in self.guide.scroll.findChildren(QPushButton) if button.text() == '复制网站地址')
        copy_link.click()
        self.assertEqual(QApplication.clipboard().text(), 'https://chatgpt.com/')
        self.assertEqual(self.guide.primary.text(), '完成设置')
        self.parent.api.failed = True
        self.guide.check_events()
        self.assertTrue(self.guide.configured)
        self.assertEqual(self.guide.primary.text(), '重新读取状态')
        self.parent.api.failed = False
        self.guide.primary.click()
        self.assertEqual(self.guide.primary.text(), '完成设置')
        self.assertTrue(self.guide.primary.isEnabled())

    def test_site_switch_ignores_old_inflight_setting_response(self):
        self.final_stage()
        self.guide.configured = True
        self.parent.api.defer = True
        self.guide.check_events()
        old_received, _, old_data = self.parent.api.waiting.pop(0)
        self.guide.site_choice.setCurrentIndex(self.guide.site_choice.findData('gemini-web'))
        old_received(old_data)
        self.assertEqual(self.guide.selected_source, 'gemini-web')
        self.assertFalse(self.guide.source_enabled())
        self.assertTrue(self.guide.pending_status)
        self.parent.api.defer = False
        self.guide.connect()
        self.assertTrue(self.guide.configured)
        self.assertEqual(self.guide.primary.text(), '开启此网站提醒')

    def test_external_source_toggle_updates_reminder_action(self):
        self.final_stage()
        self.guide.configured = True
        self.parent.model['sources'][0]['enabled'] = False
        self.guide.check_events()
        self.assertTrue(self.guide.configured)
        self.assertEqual(self.guide.primary.text(), '开启此网站提醒')
        self.guide.primary.click()
        self.assertEqual(self.guide.primary.text(), '完成设置')

    def test_reopen_keeps_store_installation_without_reading_local_folder(self):
        self.final_stage()
        self.guide.close()
        with patch.object(self.parent.api, 'request', wraps=self.parent.api.request) as request:
            self.guide.show()
        self.assertEqual(self.guide.step, 1)
        self.assertTrue(self.guide.installed)
        self.assertNotIn('/api/browser-extension', [call.args[0] for call in request.call_args_list])
        self.guide.go(0)
        self.guide.checkpoint.setChecked(False)
        self.assertFalse(self.guide.installed)
        self.assertFalse(self.guide.configured)

    def test_language_switch_updates_title_progress_and_keeps_primary_visible_at_minimum(self):
        self.final_stage()
        self.guide.connect()
        self.guide.resize(620, 500)
        set_language('en')
        self.flush()
        self.assertEqual(self.guide.progress.text(), 'Step 2 of 2')
        self.assertNotRegex(self.guide.windowTitle(), '[\u4e00-\u9fff]')
        self.assertNotRegex(self.guide.title.text() + self.guide.status.text(), '[\u4e00-\u9fff]')
        for control in (self.guide.site_panel, self.guide.primary, self.guide.status):
            origin = control.mapTo(self.guide, QPoint(0, 0))
            self.assertGreaterEqual(origin.y(), 0)
            self.assertLessEqual(origin.y() + control.height(), self.guide.height())
            self.assertLessEqual(origin.x() + control.width(), self.guide.width())
        self.guide.close()
        self.assertFalse(self.guide.timer.isActive())
        self.guide.show()
        self.assertEqual(self.guide.step, 1)
        self.assertTrue(self.guide.timer.isActive())

    def test_store_step_is_translated_and_has_readonly_exact_url(self):
        self.guide.resize(620, 500)
        set_language('en')
        self.flush()
        self.assertEqual(self.guide.progress.text(), 'Step 1 of 2')
        for label in self.guide.findChildren(QLabel):
            if label.isVisible():
                self.assertNotRegex(label.text(), '[\u4e00-\u9fff]')
        self.assertEqual(self.guide.primary.text(), 'Install in Chrome')
        address = self.guide.scroll.findChild(QLineEdit)
        self.assertTrue(address.isReadOnly())
        self.assertEqual(address.text(), STORE_URL)
        for control in (self.guide.checkpoint, self.guide.primary):
            origin = control.mapTo(self.guide, QPoint(0, 0))
            self.assertLessEqual(origin.x() + control.width(), self.guide.width())
            self.assertLessEqual(origin.y() + control.height(), self.guide.height())


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
