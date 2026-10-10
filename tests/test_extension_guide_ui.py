"""Regression checks for handoffs, current-session evidence and async recovery."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import copy
import subprocess
import unittest
from unittest.mock import patch, Mock
from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtWidgets import QApplication, QWidget, QLabel
from aiflame.desktop.extension_guide import ExtensionGuide
from aiflame.desktop.localization import set_language
from aiflame.desktop.theme import STYLE
from aiflame.desktop.browser_launch import open_in_chrome
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES


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
        elif path == '/api/verification':
            self.model['verification'][body['source']] = {'since': 100, 'status': 'waiting'}
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
        self.guide.confirmed = {1: True, 2: True}
        self.guide.go(1)
        self.guide.go(2)
        self.guide.go(3)
        self.flush()

    def evidence(self, status, started=101):
        self.parent.model['verification']['chatgpt-web'] = {'since': 100, 'status': status, 'started_at': started, 'last_seen': 110}
        self.guide.check_events()

    def test_manual_handoff_requires_open_picker_then_loaded_card(self):
        self.guide.go(1)
        self.guide.go(2)
        self.assertEqual(self.guide.step, 1)
        self.assertIn('文件夹选择窗口已打开', self.guide.checkpoint.text())
        self.guide.primary.click()
        self.assertEqual(QApplication.clipboard().text(), 'chrome://extensions/')
        self.assertIn('已复制', self.guide.status.text())
        self.guide.checkpoint.setChecked(True)
        self.assertEqual(self.guide.primary.text(), '继续')
        self.guide.primary.click()
        self.assertEqual(self.guide.step, 2)
        self.guide.go(3)
        self.assertEqual(self.guide.step, 2)
        self.guide.checkpoint.setChecked(True)
        self.guide.primary.click()
        self.assertEqual(self.guide.step, 3)

    def test_preparing_failure_allows_retry_and_resets_handoffs(self):
        self.parent.fail_action = True
        self.guide.prepare()
        self.assertFalse(self.guide.busy)
        self.assertTrue(self.guide.primary.isEnabled())
        self.assertIn('未能准备', self.guide.status.text())
        self.guide.go(1)
        self.assertEqual(self.guide.step, 0)
        self.parent.fail_action = False
        self.guide.primary.click()
        self.assertTrue(self.guide.prepared)
        self.assertEqual(self.guide.primary.text(), '继续')

    def test_site_selection_precedes_configuration_and_disabled_source_can_be_enabled(self):
        self.final_stage()
        self.guide.site_choice.setCurrentIndex(self.guide.site_choice.findData('gemini-web'))
        self.assertEqual(self.guide.primary.text(), '允许连接')
        self.guide.primary.click()
        self.assertEqual(self.guide.primary.text(), '开启此网站提醒')
        self.guide.primary.click()
        self.assertEqual(self.parent.actions[-1], ('/api/settings', {'source_enabled': {'gemini-web': True}}))
        self.assertTrue(next(row['enabled'] for row in self.parent.model['sources'] if row['id'] == 'chatgpt-web'))
        self.assertEqual(self.guide.primary.text(), '在 Chrome 打开')

    def test_configuration_does_not_verify_old_tasks_and_start_resets_server_window(self):
        self.final_stage()
        self.evidence('verified', started=90)
        self.assertFalse(self.guide.verified)
        self.guide.connect()
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=True) as launch:
            self.guide.primary.click()
        launch.assert_called_once_with('https://chatgpt.com/')
        self.assertEqual(self.parent.actions[-1], ('/api/verification', {'source': 'chatgpt-web'}))
        self.evidence('verified', started=90)
        self.assertFalse(self.guide.verified)
        self.evidence('working')
        self.assertIn('已收到任务开始', self.guide.status.text())
        self.assertFalse(self.guide.primary.isEnabled())
        self.evidence('verified')
        self.assertTrue(self.guide.verified)
        self.assertEqual(self.guide.primary.text(), '完成设置')

    def test_failed_task_read_error_and_chrome_failure_have_recovery(self):
        self.final_stage()
        self.guide.connect()
        with patch('aiflame.desktop.extension_guide.open_in_chrome', return_value=False):
            self.guide.primary.click()
        self.assertEqual(self.guide.primary.text(), '复制网站地址')
        self.guide.primary.click()
        self.assertEqual(QApplication.clipboard().text(), 'https://chatgpt.com/')
        self.evidence('working')
        self.assertFalse(self.guide.chrome_error)
        self.assertIn('已收到任务开始', self.guide.status.text())
        self.evidence('cancelled')
        self.assertIn('没有正常完成', self.guide.status.text())
        self.parent.api.failed = True
        self.guide.check_events()
        self.assertFalse(self.guide.verified)
        self.assertEqual(self.guide.primary.text(), '重新读取状态')
        self.parent.api.failed = False
        self.evidence('verified')
        self.parent.api.failed = True
        self.guide.check_events()
        self.assertTrue(self.guide.verified)
        self.assertIn('已保留本次验证成功', self.guide.status.text())

    def test_switch_reconnect_and_old_inflight_response_cannot_keep_success(self):
        self.final_stage()
        self.guide.configured = True
        self.guide.baseline = {'chatgpt-web': 100}
        self.evidence('verified')
        self.parent.api.defer = True
        self.guide.check_events()
        old_received, _, old_data = self.parent.api.waiting.pop(0)
        self.guide.site_choice.setCurrentIndex(self.guide.site_choice.findData('gemini-web'))
        old_received(old_data)
        self.assertFalse(self.guide.verified)
        self.assertIsNone(self.guide.baseline)
        self.parent.api.defer = False
        self.guide.connect()
        self.assertFalse(self.guide.verified)
        self.assertIsNone(self.guide.baseline)
        self.assertTrue(self.guide.configured)

    def test_external_source_toggle_invalidates_current_session(self):
        self.final_stage()
        self.guide.configured = True
        self.guide.baseline = {'chatgpt-web': 100}
        self.evidence('verified')
        self.parent.model['sources'][0]['enabled'] = False
        self.guide.check_events()
        self.assertFalse(self.guide.verified)
        self.assertIsNone(self.guide.baseline)

    def test_stale_folder_read_cannot_overwrite_a_new_prepare_and_missing_files_restart_flow(self):
        self.guide.close()
        self.parent.api.defer = True
        self.guide.show()
        old_received, _, _ = self.parent.api.waiting.pop(0)
        self.guide.prepare()
        old_received({'prepared': False, 'path': '/tmp/stale'})
        self.assertTrue(self.guide.prepared)
        self.assertEqual(self.guide.path, '/tmp/SeeEnd/BrowserExtension')
        self.parent.api.defer = False
        self.final_stage()
        self.guide.close()
        self.parent.prepared = False
        self.guide.show()
        self.assertEqual(self.guide.step, 0)
        self.assertFalse(self.guide.configured)
        self.assertIsNone(self.guide.baseline)

    def test_language_switch_updates_title_progress_and_keeps_primary_visible_at_minimum(self):
        self.final_stage()
        self.guide.connect()
        self.guide.resize(620, 500)
        set_language('en')
        self.flush()
        self.assertEqual(self.guide.progress.text(), 'Step 4 of 4')
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
        self.assertEqual(self.guide.step, 3)
        self.assertTrue(self.guide.timer.isActive())

    def test_zoom_uses_the_same_source_crop_with_explicit_zoom_choices(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog, QComboBox
        observed = []
        def inspect():
            dialog = next(widget for widget in QApplication.topLevelWidgets() if isinstance(widget, QDialog) and widget is not self.guide and widget.isVisible())
            image = next(label for label in dialog.findChildren(QLabel) if label.pixmap() and not label.pixmap().isNull())
            zoom = dialog.findChild(QComboBox)
            zoom.setCurrentIndex(1)
            observed.append((image.pixmap().width(), image.pixmap().height(), zoom.count()))
            dialog.accept()
        QTimer.singleShot(0, inspect)
        self.guide.enlarge('chrome-extensions.jpg', (30, 120, 355, 95), '加载按钮')
        self.assertEqual(observed, [(355, 95, 3)])

    def test_retina_screens_keep_the_original_crop_pixels(self):
        self.guide.go(1)
        for label, original in self.guide.pictures:
            # An offscreen 1x QLabel may normalize its getter back to 1x.
            # Inspect the actual pixmap supplied to the native control instead.
            with patch.object(self.guide, 'devicePixelRatioF', return_value=2), patch.object(label, 'setPixmap') as rendered:
                self.guide.fit_images()
            display = rendered.call_args.args[0]
            self.assertEqual(display.devicePixelRatio(), 2)
            self.assertGreaterEqual(display.width(), original.width() - 1)
            self.assertEqual(label.height(), round(display.height()/2))


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
