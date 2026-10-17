"""Regression coverage for failed saves, task identity and localized native UI."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import csv
import tempfile
import unittest
from importlib.util import find_spec
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.integrations.registry import SOURCES
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.widgets import Dashboard
    from aiflame.desktop.localization import set_language
    from aiflame.desktop.notifications import NotificationConsumer
else:
    QObject = object
    def Signal(*args):
        return None


class ControlledAPI(QObject):
    error = Signal(str)

    def __init__(self, parent, port):
        super().__init__(parent)
        self.posts = []
        self.error_callbacks = {}

    def request(self, path, callback=None, body=None, **kwargs):
        if body is not None:
            self.posts.append((path, callback, body, kwargs.get("failed")))
            self.error_callbacks[id(callback)] = kwargs.get("error_callback")

    def close(self):
        pass


@unittest.skipUnless(HAS_QT, "Install desktop dependencies")
class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        set_language("zh-CN")
        self.work = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.work.name) / "events.sqlite", sources=SOURCES)
        self.wechat = {}
        with patch("aiflame.desktop.widgets.LocalAPI", ControlledAPI):
            self.widget = Dashboard()
        self.widget.ready = True
        self.widget.resize(1180, 850)
        self.widget.show()
        self.render()

    def tearDown(self):
        self.widget.shutdown()
        self.widget.close()
        self.engine.db.close()
        self.work.cleanup()
        set_language("zh-CN")

    def settle(self):
        for _ in range(5):
            self.app.processEvents()

    def test_home_heading_opens_integrations_and_native_tabs_accept_arrow_keys(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        self.widget.home_sources_button.click()
        self.assertEqual(self.widget.tabs.currentIndex(), 2)
        self.widget.source_category.setCurrentIndex(self.widget.source_category.findData("cli"))
        self.assertEqual(self.widget.source_category.currentData(), "cli")
        self.widget.source_category.setFocus()
        QTest.keyClick(self.widget.source_category, Qt.Key.Key_Left)
        self.assertEqual(self.widget.source_category.currentData(), "web")
        self.widget.history_search.setText("abcd")
        self.widget.history_search.setCursorPosition(4)
        QTest.keyClick(self.widget.history_search, Qt.Key.Key_Left)
        self.assertEqual(self.widget.history_search.cursorPosition(), 3)

    def test_device_loading_and_delete_confirmation_are_safe(self):
        from PySide6.QtWidgets import QMessageBox
        device = {"device_id": "fixture", "hardware_model": "yeelight-yltd003", "name": "Desk lamp", "online": True}
        self.widget.render({**self.engine.snapshot(), "maintenance": False, "devices": [device]})
        self.widget.navigate(1)
        self.assertTrue(self.widget.device_list_panel.isVisible())
        self.settle()
        self.assertGreaterEqual(self.widget.device_rows["fixture"].height(), 88)
        self.assertLess(self.widget.device_rows["fixture"].status.height(), 40)
        self.widget.choose_registered_device("fixture")
        self.assertEqual(self.widget.device_summary.text(), "正在读取设备…")
        self.assertFalse(self.widget.try_light_button.isEnabled())
        self.widget.render_device({"selected_device_id": "fixture", "hardware_model": "yeelight-yltd003", "device": device, "devices": [device]})
        self.assertEqual(self.widget.device_summary.text(), "设备已连接")
        self.assertFalse(self.widget.connection_prepare_disclosure.body.isVisible())
        self.assertFalse(self.widget.connection_prepare_disclosure.button.isChecked())
        self.widget.confirm_delete_current_device()
        dialog = self.widget.delete_confirmation
        self.assertEqual(dialog.defaultButton().text(), "取消")
        dialog.reject()
        self.settle()
        self.assertFalse(any(post[0] == "/api/device/delete" for post in self.widget.api.posts))
        self.widget.confirm_delete_current_device()
        dialog = self.widget.delete_confirmation
        next(button for button in dialog.buttons() if dialog.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole).click()
        self.assertEqual(self.widget.api.posts[-1][2], {"hardware_model": "yeelight-yltd003", "device_id": "fixture"})

    def test_activity_view_button_preserves_cells_and_can_clear_empty_filter(self):
        self.engine.accept(make_event("codex", "review", "working", 1), "codex")
        self.engine.accept(make_event("codex", "review", "done", 2), "codex")
        self.render()
        self.widget.navigate(4)
        item = self.widget.history.item(0, 0)
        button = self.widget.history.cellWidget(0, 5)
        button.click()
        self.assertTrue(self.widget.event_detail_box.isVisible())
        self.widget.render_history()
        self.assertIs(self.widget.history.item(0, 0), item)
        self.assertIs(self.widget.history.cellWidget(0, 5), button)
        self.assertTrue(self.widget.history.selectionModel().hasSelection())
        self.widget.history_search.setText("unmatched-filter")
        self.assertEqual(self.widget.history_stack.currentIndex(), 1)
        self.widget.history_clear_button.click()
        self.assertEqual(self.widget.history_stack.currentIndex(), 0)

    def test_activity_detail_closes_and_stays_closed_until_selected(self):
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QPushButton
        self.engine.accept(make_event("codex", "review", "working", 1), "codex")
        self.engine.accept(make_event("codex", "review", "done", 2), "codex")
        self.render()
        self.widget.navigate(4)
        close = next(button for button in self.widget.event_detail_box.findChildren(QPushButton)
                     if button.text() == "关闭详情")
        for mode in ("tasks", "events"):
            with self.subTest(mode=mode):
                self.widget.history_view.setCurrentIndex(self.widget.history_view.findData(mode))
                self.widget.history.cellWidget(0, 5).click()
                self.settle()
                self.assertTrue(self.widget.event_detail_box.isVisible())
                close.setFocus()
                if mode == "tasks":
                    QTest.keyClick(close, Qt.Key.Key_Space)
                else:
                    position = QPointF(close.rect().center())
                    global_position = QPointF(close.mapToGlobal(close.rect().center()))
                    for kind, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                          (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
                        self.app.sendEvent(close, QMouseEvent(kind, position, global_position,
                                           Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier))
                self.settle()
                self.assertTrue(self.widget.event_detail_box.isHidden())
                self.assertEqual(self.widget.event_detail.text(), "")
                self.assertFalse(self.widget.history.selectionModel().hasSelection())
                self.assertEqual(self.widget.history.currentRow(), -1)
                self.render()
                self.widget.history.setFocus()
                self.settle()
                self.assertTrue(self.widget.event_detail_box.isHidden())
                self.widget.history.cellWidget(0, 5).click()
                self.assertTrue(self.widget.event_detail_box.isVisible())
                self.assertTrue(self.widget.event_detail.text())

    def test_toast_does_not_move_page_and_dismisses_across_pages(self):
        before = self.widget.tabs.geometry()
        self.widget.tell("测试提示", error=True)
        self.settle()
        self.assertEqual(self.widget.tabs.geometry(), before)
        self.assertTrue(self.widget.message_timer.isActive())
        self.widget.message_close.click()
        self.assertFalse(self.widget.statusbar.isVisible())
        self.widget.tell("测试提示", error=True)
        self.widget.navigate(2)
        self.assertFalse(self.widget.statusbar.isVisible())

    def test_active_hardware_task_disables_demo_with_hover_reason(self):
        self.engine.update_settings({"hardware_notifications": True})
        self.engine.accept(make_event("codex", "active", "working", 1), "codex")
        self.widget.render({**self.engine.snapshot(), "maintenance": False,
                            "device": {**self.engine.device, "online": True}})
        self.assertFalse(self.widget.preview_button.isEnabled())
        self.assertIn("有任务尚未结束", self.widget.preview_button.toolTip())
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QToolTip
        button = self.widget.preview_button
        self.settle()
        position = button.rect().center()
        self.app.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, position, button.mapToGlobal(position)))
        self.settle()
        self.assertTrue(QToolTip.isVisible())
        self.assertIn("有任务尚未结束", QToolTip.text())
        QToolTip.hideText()
        before = len(self.widget.api.posts)
        self.widget.start_preview()
        self.assertEqual(len(self.widget.api.posts), before)

    def test_all_demo_entries_block_working_and_waiting_tasks_in_both_device_settings(self):
        for hardware in (False, True):
            for state in ("working", "needs_input"):
                with self.subTest(hardware=hardware, state=state):
                    task = f"busy-{hardware}-{state}"
                    self.engine.update_settings({"hardware_notifications": hardware})
                    self.engine.accept(make_event("local-script", task, state), "local-script")
                    self.widget.render({**self.engine.snapshot(), "maintenance": False,
                                        "device": {**self.engine.device, "online": True}})
                    self.widget.selected_device_id = "fixture"
                    with patch.object(self.widget, "detail_device", return_value={"online": True}):
                        self.widget.update_busy()
                        self.assertFalse(self.widget.preview_button.isEnabled())
                        self.assertFalse(self.widget.try_light_button.isEnabled())
                        for button in (self.widget.preview_button, self.widget.try_light_button):
                            self.assertIn("有任务尚未结束", button.toolTip())
                        before = len(self.widget.api.posts)
                        self.widget.start_preview()
                        self.widget.test_current_device()
                        self.assertEqual(len(self.widget.api.posts), before)
                        self.assertIsNone(self.widget.preview_phase)
                    self.engine.accept(make_event("local-script", task, "cancelled", 2), "local-script")
                    self.widget.render({**self.engine.snapshot(), "maintenance": False,
                                        "device": {**self.engine.device, "online": True}})
                    self.assertTrue(self.widget.preview_button.isEnabled())

    def test_task_arrival_ends_screen_demo_and_history_limit_does_not_hide_waiting_task(self):
        self.widget.start_preview()
        self.assertIsNotNone(self.widget.preview_phase)
        self.engine.accept(make_event("local-script", "waiting", "needs_input"), "local-script")
        self.render()
        self.assertIsNone(self.widget.preview_phase)
        self.assertFalse(self.widget.preview_timer.isActive())
        self.assertFalse(self.widget.preview_button.isEnabled())
        self.assertTrue(self.widget.preview_caption.isHidden())
        for index in range(31):
            self.engine.accept(make_event("local-script", f"recent-{index}", "done"), "local-script")
        self.render()
        self.assertNotIn("waiting", {task["task"] for task in self.widget.model["tasks"]})
        self.assertFalse(self.widget.preview_button.isEnabled())
        self.assertIn("有任务尚未结束", self.widget.preview_button.toolTip())

    def test_enabled_device_channel_without_online_devices_can_play_full_screen_demo(self):
        for restrictions in ({}, {"brightness": 0}):
            with self.subTest(restrictions=restrictions):
                self.engine.update_settings({"hardware_notifications": True, "brightness": 20, **restrictions})
                self.render()
                before = self.engine.snapshot()
                writes = len(self.widget.api.posts)
                self.assertTrue(self.widget.preview_button.isEnabled())
                self.widget.preview_button.click()
                self.assertEqual(self.widget.preview_phase, "working")
                self.assertEqual(self.widget.preview_button.text(), "退出演示")
                self.assertIsNone(self.widget.hardware_preview_id)
                self.assertTrue(self.widget.preview_timer.isActive())
                self.widget.preview_started -= 5.5
                self.widget.advance_preview()
                self.assertEqual(self.widget.preview_phase, "needs_input")
                self.widget.preview_started -= 5
                self.widget.advance_preview()
                self.assertEqual(self.widget.preview_phase, "done")
                self.widget.preview_started -= 5
                self.widget.advance_preview()
                self.assertIsNone(self.widget.preview_phase)
                self.assertFalse(self.widget.preview_timer.isActive())
                self.assertEqual(self.widget.preview_button.text(), "灯效演示")
                self.assertEqual(len(self.widget.api.posts), writes)
                after = self.engine.snapshot()
                self.assertEqual(after["settings"], before["settings"])
                self.assertEqual(after["events"], before["events"])

    def test_unfinished_task_still_blocks_and_preempts_screen_demo_with_devices_offline(self):
        self.engine.update_settings({"hardware_notifications": True})
        self.render()
        self.widget.start_preview()
        self.assertEqual(self.widget.preview_phase, "working")
        self.engine.accept(make_event("local-script", "offline-task", "working"), "local-script")
        self.render()
        self.assertIsNone(self.widget.preview_phase)
        self.assertFalse(self.widget.preview_timer.isActive())
        for state, seq in (("working", 1), ("needs_input", 2)):
            if seq > 1:
                self.engine.accept(make_event("local-script", "offline-task", state, seq), "local-script")
                self.render()
            self.assertFalse(self.widget.preview_button.isEnabled())
            self.assertIn("有任务尚未结束", self.widget.preview_button.toolTip())
            self.widget.start_preview()
            self.assertIsNone(self.widget.preview_phase)
        self.engine.accept(make_event("local-script", "offline-task", "cancelled", 3), "local-script")
        self.render()
        self.assertTrue(self.widget.preview_button.isEnabled())
        self.widget.start_preview()
        self.assertEqual(self.widget.preview_phase, "working")

    def test_numeric_light_entry_and_fresh_device_state(self):
        device = {"device_id": "fixture", "hardware_model": "yeelight-yltd003", "name": "Lamp", "online": False}
        self.widget.render({**self.engine.snapshot(), "maintenance": False, "devices": [device]})
        self.widget.choose_registered_device("fixture")
        self.widget.render_device({"selected_device_id": "fixture", "hardware_model": "yeelight-yltd003", "device": {**device, "online": True}, "devices": [device]})
        self.assertEqual(self.widget.device_summary.text(), "设备已连接")
        self.widget.yeelight_brightness_labels["working"].setValue(37)
        self.assertEqual(self.widget.yeelight_brightness_controls["working"].value(), 37)
        self.widget.flush_device_brightness()
        self.assertEqual(self.widget.api.posts[-1][2]["settings"]["state_brightness"], {"working": 37})
        self.widget.show_device_catalog()
        self.widget.choose_device_model("yeelight-yldp005")
        self.assertEqual(self.widget.selected_device_id, "")
        self.assertEqual(self.widget.device_summary.text(), "正在读取设备…")
        self.widget.render_device({"selected_device_id": "", "hardware_model": "yeelight-yldp005", "device": {"online": False}, "devices": []})
        self.assertEqual(self.widget.device_summary.text(), "尚未添加设备")

    def test_icon_home_shortcuts_keep_names_and_correct_links_on_hover(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from aiflame.integrations.chatgpt_web.host import WEB_SOURCES, WEB_URLS
        from aiflame.integrations.cli_tools.providers import CLI_SOURCES, REPOSITORY_URLS
        from aiflame.desktop.localization import t
        self.widget.navigate(0)
        self.settle()
        names = {**WEB_SOURCES, "codex": "Codex", **CLI_SOURCES}
        urls = {**WEB_URLS, **REPOSITORY_URLS}
        for language in ("zh-CN", "en"):
            self.engine.update_settings({"language": language})
            self.render()
            for source, button in self.widget.home_source_icons.items():
                with self.subTest(language=language, source=source):
                    self.assertEqual(button.text(), "")
                    self.assertFalse(button.icon().isNull())
                    self.assertGreaterEqual(button.height(), 40)
                    self.assertEqual(button.accessibleName(), t(names[source]))
                    self.assertIn(t(names[source]), button.toolTip())
                    self.assertIn(urls[source], button.toolTip())
                    self.assertEqual(button.accessibleDescription(), urls[source])
            button = self.widget.home_source_icons["codex"]
            button.setFocus()
            with patch('PySide6.QtGui.QDesktopServices.openUrl', return_value=True) as opened:
                QTest.keyClick(button, Qt.Key.Key_Space)
            self.assertEqual(opened.call_args.args[0].toString(), REPOSITORY_URLS['codex'])
            self.assertEqual(self.widget.tabs.currentIndex(), 0)

    def test_wechat_binding_and_test_controls_follow_authoritative_state(self):
        self.assertFalse(self.widget.checks["wechat_notifications"].isChecked())
        self.assertFalse(self.widget.wechat_test_button.isEnabled())
        self.widget.render_wechat({"bound": True, "enabled": False})
        self.assertFalse(self.widget.checks["wechat_notifications"].isEnabled())
        self.assertFalse(self.widget.wechat_test_button.isEnabled())
        self.widget.render_wechat({"bound": True, "enabled": True, "needs_message": True})
        self.assertIn("发一条消息", self.widget.preference_help["wechat_notifications"].toolTip())
        self.assertFalse(self.widget.wechat_test_button.isEnabled())
        self.widget.render_wechat({"bound": True, "enabled": True, "ready": True})
        self.assertTrue(self.widget.wechat_test_button.isEnabled())
        self.widget.wechat_test_button.click()
        path, callback, body, failed = self.widget.api.posts.pop()
        self.assertEqual((path, body), ("/api/wechat", {"action": "test"}))
        callback({"bound": True, "enabled": True, "ready": True, "test_status": "submitted"})
        self.assertTrue(self.widget.wechat_feedback.isHidden())
        self.assertIn("微信中查看", self.widget.message.text())
        self.assertEqual(self.widget.wechat_notice, "")

    def test_wechat_qr_clear_and_verification_privacy(self):
        from aiflame.integrations.wechat.api import qr_image
        self.widget.render_wechat({"login": {"state": "need_verifycode", "image": qr_image("test QR content")}})
        self.assertFalse(self.widget.wechat_verify_box.isHidden())
        self.assertFalse(self.widget.wechat_qr.pixmap().isNull())
        self.widget.wechat_verify.setText("123456")
        self.widget.verify_wechat()
        self.assertEqual(self.widget.wechat_verify.text(), "")
        self.assertEqual(self.widget.api.posts[-1][2], {"action": "verify", "code": "123456"})
        self.widget.render_wechat({"login": {"state": "idle", "image": ""}})
        self.assertTrue(self.widget.wechat_qr.pixmap().isNull())
        self.assertTrue(self.widget.wechat_login_box.isHidden())

    def test_wechat_english_and_offline_controls(self):
        set_language("en")
        self.widget.render_wechat({"bound": True, "enabled": True, "needs_message": True})
        self.assertIn("message", self.widget.preference_help["wechat_notifications"].toolTip())
        self.widget.ready = False
        self.widget.update_busy()
        self.assertFalse(self.widget.wechat_disconnect_button.isEnabled())

    def test_preference_help_retains_dynamic_reasons_duration_and_native_activation(self):
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QHelpEvent, QTextDocument
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QLabel
        from aiflame.desktop.localization import t

        for language in ("zh-CN", "en"):
            self.engine.update_settings({"language": language, "duration": 17, "hold_until_activity": True})
            error = "fixture <b>failed</b> & retry"
            self.wechat = {"bound": True, "available": False, "error": error}
            self.render()
            self.widget.navigate(3)
            self.widget.update_notification_status("denied")
            self.settle()
            page = self.widget.tabs.widget(3)
            self.assertFalse(any(label.isVisible() and label.property("role") in ("preferenceHint", "preferenceStatus")
                                 for label in page.findChildren(QLabel)))
            self.assertIn(t("系统通知未开启。请在系统设置 → 通知 → SeeEnd 中开启「允许通知」，再打开此开关。"),
                          self.widget.preference_help["desktop_notifications"].toolTip())
            self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
            self.assertFalse(self.widget.checks["wechat_notifications"].isEnabled())
            self.assertIn("17", self.widget.preference_help["duration"].toolTip())
            self.assertIn(t("等待输入提醒立即计时。"), self.widget.preference_help["duration"].toolTip())
            for key, button in self.widget.preference_help.items():
                with self.subTest(language=language, setting=key):
                    self.assertTrue(button.toolTip())
                    self.assertEqual(button.accessibleDescription(), button.toolTip())
                    if language == "en":
                        self.assertNotRegex(button.toolTip(), r"[\u4e00-\u9fff]")
                    with patch("aiflame.desktop.components.QToolTip.showText") as show:
                        position = button.rect().center()
                        self.app.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, position, button.mapToGlobal(position)))
                        self.assertEqual(show.call_count, 1)
                        button.setFocus()
                        QTest.keyClick(button, Qt.Key.Key_Space)
                        self.assertEqual(show.call_count, 2)
                    document = QTextDocument()
                    document.setHtml(show.call_args.args[1])
                    self.assertEqual(document.toPlainText().strip("\n"), button.toolTip())
            self.assertIn(error, self.widget.preference_help["wechat_notifications"].toolTip())
            self.widget.update_notification_status("authorized")
            self.widget.render_wechat({"bound": True, "available": True})
            self.assertNotIn(error, self.widget.preference_help["wechat_notifications"].toolTip())
            self.assertEqual(self.widget.notification_permission_text, "")
            self.assertTrue(self.widget.checks["desktop_notifications"].isEnabled())
            self.assertTrue(self.widget.checks["wechat_notifications"].isEnabled())

    def render(self):
        self.widget.render({**self.engine.snapshot(), "maintenance": False,
                            "wechat": {**self.wechat, "enabled": self.engine.settings["wechat_notifications"]}})
        self.settle()

    def complete(self, post):
        _, callback, body, _ = post
        settings = self.engine.update_settings(body)
        callback({"settings": settings})
        self.render()

    def fail_post(self, post):
        handler = self.widget.api.error_callbacks.get(id(post[1])) or self.widget.api.error.emit
        handler("保存失败，请重试。")
        post[3]()

    def test_later_success_keeps_failed_field_and_retry_restores_requested_value(self):
        self.wechat = {"bound": True, "available": True}
        self.render()
        self.widget.navigate(3)
        self.widget.checks["wechat_notifications"].click()
        self.widget.checks["hold_until_activity"].click()
        first = self.widget.api.posts[0]
        self.fail_post(first)
        self.complete(self.widget.api.posts[1])
        self.assertFalse(self.widget.checks["wechat_notifications"].isChecked())
        self.assertFalse(self.widget.checks["hold_until_activity"].isChecked())
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertNotIn("wechat_notifications", self.widget.save_feedback)
        self.assertIn("未保存", self.widget.preference_help["wechat_notifications"].toolTip())
        self.assertIn("重新切换", self.widget.checks["wechat_notifications"].toolTip())
        self.widget.checks["wechat_notifications"].click()
        self.assertEqual(self.widget.api.posts[-1][2], {"wechat_notifications": True})
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.widget.checks["wechat_notifications"].isChecked())
        self.assertEqual(self.widget.setting_saves["wechat_notifications"]["state"], "saved")
        self.assertNotIn("未保存", self.widget.preference_help["wechat_notifications"].toolTip())
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_reminder_saves_and_failures_keep_preferences_geometry_unchanged(self):
        from PySide6.QtCore import QPoint
        keys = tuple(self.widget.home_channel_checks)
        self.wechat = {"bound": True, "available": True}
        self.widget.navigate(3)
        root = self.widget.tabs.widget(3).widget()
        controls = [*(self.widget.checks[key] for key in keys),
                    *(self.widget.preference_help[key] for key in keys),
                    self.widget.notification_test_button, self.widget.wechat_disconnect_button,
                    self.widget.duration_control]

        def geometry():
            return [control.rect().translated(control.mapTo(root, QPoint())) for control in controls] + [root.size()]

        for language in ("zh-CN", "en"):
            for width in (920, 1512):
                self.widget.resize(width, 850)
                self.engine.update_settings({"language": language, **dict.fromkeys(keys, True)})
                self.render()
                self.widget.update_notification_status("authorized")
                self.settle()
                for key in keys:
                    with self.subTest(language=language, width=width, key=key):
                        before = geometry()
                        self.assertNotIn(key, self.widget.save_feedback)
                        self.widget.checks[key].click()
                        self.settle()
                        self.assertEqual(geometry(), before)
                        self.fail_post(self.widget.api.posts[-1])
                        self.settle()
                        self.assertTrue(self.widget.checks[key].isChecked())
                        self.assertIn("未保存" if language == "zh-CN" else "Not saved", self.widget.preference_help[key].toolTip())
                        self.assertTrue(self.widget.statusbar.isHidden())
                        self.assertEqual(geometry(), before)
                        self.widget.checks[key].click()
                        self.complete(self.widget.api.posts[-1])
                        self.assertFalse(self.widget.checks[key].isChecked())
                        self.assertTrue(self.widget.statusbar.isHidden())
                        self.assertEqual(geometry(), before)
                        self.assertNotIn("未保存" if language == "zh-CN" else "Not saved", self.widget.preference_help[key].toolTip())

    def test_permission_check_and_rejection_keep_preferences_geometry_unchanged(self):
        from PySide6.QtCore import QPoint
        callbacks = []
        consumer = NotificationConsumer(self.widget, SimpleNamespace(status=callbacks.append))
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        callbacks.pop()("authorized")
        self.widget.navigate(3)
        self.settle()
        root = self.widget.tabs.widget(3).widget()
        controls = [*self.widget.checks.values(), *self.widget.preference_help.values(),
                    self.widget.notification_test_button, self.widget.duration_control]
        def geometry():
            return [control.rect().translated(control.mapTo(root, QPoint())) for control in controls] + [root.size()]
        for status in ("denied", "unknown", "unavailable"):
            with self.subTest(status=status):
                before = geometry()
                self.widget.checks["desktop_notifications"].click()
                self.settle()
                self.assertEqual(geometry(), before)
                callbacks.pop()(status)
                self.settle()
                self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
                self.assertFalse(self.widget.notification_test_button.isEnabled())
                self.assertTrue(self.widget.statusbar.isHidden())
                self.assertEqual(geometry(), before)

    def test_source_queue_does_not_resubmit_another_sources_failed_change(self):
        self.engine.update_settings({'source_enabled': {**self.engine.settings['source_enabled'],
                                                       'claude-code': False, 'qoder': False}})
        self.render()
        self.widget.source_changed("claude-code", True)
        self.widget.source_changed("qoder", True)
        self.fail_post(self.widget.api.posts[0])
        second = self.widget.api.posts[1]
        self.assertFalse(second[2]["source_enabled"]["claude-code"])
        self.assertTrue(second[2]["source_enabled"]["qoder"])
        self.complete(second)
        self.widget.retry_setting("source:claude-code")
        self.assertTrue(self.widget.api.posts[-1][2]["source_enabled"]["qoder"])
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.engine.settings["source_enabled"]["claude-code"])
        self.assertTrue(self.widget.save_feedback["source:claude-code"][0].isHidden())
        self.assertTrue(self.widget.save_feedback["source:qoder"][0].isHidden())
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_task_groups_keep_source_identity_and_literal_ids_in_english(self):
        for source in ("codex", "local-script"):
            for seq, state in ((1, "working"), (2, "done")):
                self.engine.accept(make_event(source, "我的设备", state, seq), source)
        self.engine.update_settings({"language": "en"})
        self.render()
        self.widget.navigate(4)
        self.assertEqual(self.widget.history.rowCount(), 2)
        self.assertEqual(self.widget.history.item(0, 4).text(), "我的设备")
        self.widget.history.selectRow(0)
        self.assertIn("我的设备", self.widget.event_detail.text())
        self.assertIn("State events", self.widget.event_detail.text())
        self.widget.history_view.setCurrentIndex(1)
        self.assertEqual(self.widget.history.rowCount(), 4)
        path = Path(self.work.name) / "activity.csv"
        with patch("aiflame.desktop.widgets.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            self.widget.export_history()
        with path.open(encoding="utf-8-sig") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows[0][-1], "Task ID")
        self.assertTrue(all(row[-1] == "我的设备" for row in rows[1:]))

    def test_overview_distinguishes_tasks_with_the_same_prefix(self):
        enabled = {**self.engine.settings["source_enabled"], "codex": True}
        self.engine.update_settings({"source_enabled": enabled})
        for task in ("review-session-common-task-one", "review-session-common-task-two"):
            self.engine.accept(make_event("codex", task, "working", 1), "codex")
        self.engine.accept(make_event("codex", "review-session-common-task-two", "needs_input", 2), "codex")
        self.render()
        self.assertIn("进行中 1", self.widget.home_task_summary.text())
        self.assertIn("等待输入 1", self.widget.home_task_summary.text())
        self.assertEqual(self.widget.display_title.text(), "等待输入")
        self.assertEqual(self.widget.displayed_task_state(), "needs_input")
        texts = [labels[1].toolTip() for labels in self.widget.recent_rows[:2]]
        self.assertTrue(any("one" in text for text in texts))
        self.assertTrue(any("two" in text for text in texts))

    def test_disconnection_reports_unavailable_and_keeps_last_update(self):
        self.widget.navigate(0)
        self.widget.on_error("本地服务未连接，请重新打开 SeeEnd 后重试。")
        self.assertEqual(self.widget.display_title.text(), "任务状态暂不可用")
        self.assertIn("最近更新时间", self.widget.display_hint.text())
        self.assertTrue(self.widget.retry_button.isVisible())

    def test_notifications_gate_switch_and_show_only_failures_inline(self):
        self.assertTrue(self.widget.notification_test_feedback.isHidden())
        submitted = []
        backend = SimpleNamespace(status=lambda done: done("denied"),
                                  submit=lambda message, done: (submitted.append(message), done("submitted")))
        consumer = NotificationConsumer(self.widget, backend)
        self.addCleanup(consumer.shutdown)
        self.settle()
        self.assertTrue(self.widget.checks["desktop_notifications"].isEnabled())
        self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
        self.widget.test_notification()
        self.settle()
        self.assertEqual(submitted, [])
        backend.status = lambda done: done("authorized")
        consumer.check_status()
        self.settle()
        self.assertEqual(self.widget.notification_permission_text, "")
        self.assertTrue(self.widget.checks["desktop_notifications"].isEnabled())
        self.assertFalse(self.widget.notification_test_button.isEnabled())
        self.widget.checks["desktop_notifications"].click()
        self.settle()
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.widget.notification_test_button.isEnabled())
        self.widget.test_notification()
        self.settle()
        self.assertEqual(len(submitted), 1)
        self.assertTrue(self.widget.notification_test_feedback.isHidden())
        self.assertIn("请在系统通知中查看", self.widget.message.text())
        self.assertFalse(self.widget.notification_testing)

    def test_unavailable_notification_channels_cannot_be_enabled_or_retried(self):
        for key in ("desktop_notifications", "wechat_notifications"):
            self.widget.api.posts.clear()
            self.widget.checks[key].click()
            self.widget.setting(key, True)
            self.assertFalse(self.widget.checks[key].isChecked())
            self.assertEqual(self.widget.api.posts, [])
            self.widget.setting_saves[key] = {"value": True, "state": "failed"}
            self.widget.retry_setting(key)
            self.assertEqual(self.widget.api.posts, [])

    def test_parallel_home_switches_save_independently_and_match_preferences(self):
        consumer = NotificationConsumer(self.widget, SimpleNamespace(status=lambda done: done("authorized")))
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        self.settle()
        keys = ("desktop_notifications", "sound_notifications", "wechat_notifications", "desktop_flame_enabled", "hardware_notifications")
        self.assertEqual(set(self.widget.home_channel_checks), set(keys))
        self.wechat = {"bound": True, "available": True}
        self.render()
        self.widget.update_notification_status("authorized")
        expected = {key: self.engine.settings[key] for key in keys}
        for key in keys:
            toggle = self.widget.home_channel_checks[key]
            expected[key] = not expected[key]
            toggle.click()
            self.settle()
            self.assertEqual(self.widget.api.posts[-1][2], {key: expected[key]})
            self.assertEqual(self.widget.checks[key].isChecked(), expected[key])
            self.complete(self.widget.api.posts[-1])
            self.assertEqual({k: self.engine.settings[k] for k in keys}, expected)
            self.assertEqual({k: self.widget.home_channel_checks[k].isChecked() for k in keys}, expected)
        self.widget.navigate(3)
        self.widget.checks["desktop_flame_enabled"].click()
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.widget.home_channel_checks["desktop_flame_enabled"].isChecked())
        self.assertTrue(self.widget.home_channel_checks["hardware_notifications"].isChecked())

    def test_channel_save_never_dims_or_changes_other_switches(self):
        from PySide6.QtCore import QEvent

        consumer = NotificationConsumer(self.widget, SimpleNamespace(status=lambda done: done("authorized")))
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        self.wechat = {"bound": True, "available": True}
        keys = tuple(self.widget.home_channel_checks)

        class EnabledChanges(QObject):
            def __init__(self):
                super().__init__()
                self.events = []
                self.keys = {}

            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.EnabledChange:
                    self.events.append(self.keys[watched])
                return False

        changes = EnabledChanges()
        for key in keys:
            for toggle in (self.widget.checks[key], self.widget.home_channel_checks[key]):
                changes.keys[toggle] = key
                toggle.installEventFilter(changes)
        for fail in (False, True):
            for key in keys:
                with self.subTest(key=key, fail=fail):
                    self.engine.update_settings(dict.fromkeys(keys, True))
                    self.render()
                    self.widget.update_notification_status("authorized")
                    self.widget.navigate(0)
                    self.settle()
                    changes.events.clear()
                    images = {other: self.widget.home_channel_checks[other].grab().toImage()
                              for other in keys if other != key}
                    self.widget.home_channel_checks[key].click()
                    self.settle()
                    self.assertFalse(self.widget.home_channel_checks[key].isEnabled())
                    for other, image in images.items():
                        self.assertTrue(self.widget.home_channel_checks[other].isEnabled())
                        self.assertTrue(self.widget.home_channel_checks[other].isChecked())
                        self.assertEqual(self.widget.home_channel_checks[other].grab().toImage(), image)
                    post = self.widget.api.posts[-1]
                    self.fail_post(post) if fail else self.complete(post)
                    self.settle()
                    self.assertTrue(self.widget.home_channel_checks[key].isEnabled())
                    self.assertEqual(self.widget.home_channel_checks[key].isChecked(), fail)
                    self.assertFalse(any(other != key for other in changes.events), changes.events)
                    for other, image in images.items():
                        self.assertTrue(self.widget.checks[other].isEnabled())
                        self.assertTrue(self.widget.checks[other].isChecked())
                        self.assertEqual(self.widget.home_channel_checks[other].grab().toImage(), image)
        for toggle in changes.keys:
            toggle.removeEventFilter(changes)

    def test_different_channel_changes_queue_without_losing_pending_values(self):
        permission_reads = []
        consumer = NotificationConsumer(self.widget, SimpleNamespace(status=permission_reads.append))
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        permission_reads.pop()("authorized")
        self.wechat = {"bound": True, "available": True}
        self.render()
        self.widget.navigate(0)
        expected = {"desktop_flame_enabled": False, "wechat_notifications": True,
                    "desktop_notifications": True, "hardware_notifications": True}
        for key, value in expected.items():
            self.assertTrue(self.widget.home_channel_checks[key].isEnabled())
            self.widget.home_channel_checks[key].click()
            if key == "desktop_notifications":
                self.assertFalse(self.widget.home_channel_checks[key].isChecked())
                permission_reads.pop()("authorized")
            self.settle()
            self.assertEqual(self.widget.home_channel_checks[key].isChecked(), value)
            self.assertFalse(self.widget.home_channel_checks[key].isEnabled())
        self.assertEqual(len(self.widget.api.posts), 1)
        self.assertEqual(len(self.widget.actions), 3)
        self.fail_post(self.widget.api.posts[0])
        self.settle()
        self.assertTrue(self.widget.home_channel_checks["desktop_flame_enabled"].isChecked())
        for index, key in enumerate(tuple(expected)[1:], 1):
            self.assertEqual(self.widget.api.posts[index][2], {key: True})
            self.complete(self.widget.api.posts[index])
            for other in tuple(expected)[index + 1:]:
                self.assertTrue(self.widget.home_channel_checks[other].isChecked())
                self.assertFalse(self.widget.home_channel_checks[other].isEnabled())
        self.assertFalse(self.widget.writing)
        self.assertIsNone(self.widget.active_action)
        self.assertTrue(all(toggle.isEnabled() for toggle in self.widget.home_channel_checks.values()))
        self.widget.retry_setting("desktop_flame_enabled")
        self.complete(self.widget.api.posts[-1])
        self.assertEqual({key: self.engine.settings[key] for key in expected}, expected)
        self.assertEqual({key: self.widget.home_channel_checks[key].isChecked() for key in expected}, expected)

    def test_unrelated_actions_and_reset_still_lock_reminder_switches(self):
        self.wechat = {"bound": True, "available": True}
        self.render()
        self.widget.update_notification_status("authorized")
        self.widget.act("/api/test", {"state": "done"})
        self.assertTrue(all(not toggle.isEnabled() for toggle in self.widget.home_channel_checks.values()))
        self.fail_post(self.widget.api.posts[-1])
        self.assertTrue(all(toggle.isEnabled() for toggle in self.widget.home_channel_checks.values()))
        self.widget.reset_pending = True
        self.widget.update_busy()
        self.assertTrue(all(not toggle.isEnabled() for toggle in self.widget.home_channel_checks.values()))
        self.widget.reset_pending = False
        self.widget.update_busy()
        self.assertTrue(all(toggle.isEnabled() for toggle in self.widget.home_channel_checks.values()))

    def test_wechat_capability_is_independent_of_switch_and_loss_turns_it_off(self):
        self.wechat = {"bound": True, "available": True}
        self.render()
        toggle = self.widget.checks["wechat_notifications"]
        self.assertTrue(toggle.isEnabled())
        self.assertFalse(toggle.isChecked())
        self.assertEqual(self.widget.wechat_notice, "")
        toggle.click()
        self.assertEqual(self.widget.api.posts[-1][2], {"wechat_notifications": True})
        self.assertTrue(toggle.isChecked())
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(toggle.isChecked())
        toggle.click()
        self.assertFalse(toggle.isChecked())
        self.complete(self.widget.api.posts[-1])
        self.assertFalse(toggle.isChecked())
        toggle.click()
        self.complete(self.widget.api.posts[-1])
        self.wechat = {"bound": True, "available": False, "error": "连接失败"}
        self.render()
        self.assertFalse(toggle.isChecked())
        self.assertFalse(toggle.isEnabled())
        self.assertIn("连接失败", self.widget.preference_help["wechat_notifications"].toolTip())

    def test_system_permission_request_enables_control_without_test_submission(self):
        requests, submitted = [], []
        backend = SimpleNamespace(status=lambda done: done("not_determined"),
                                  authorize=requests.append,
                                  submit=lambda message, done: submitted.append(message))
        consumer = NotificationConsumer(self.widget, backend)
        self.addCleanup(consumer.shutdown)
        self.widget.navigate(3)
        self.settle()
        self.assertTrue(self.widget.checks["desktop_notifications"].isEnabled())
        self.assertEqual(self.widget.notification_test_button.text(), "测试通知")
        self.assertFalse(self.widget.notification_test_button.isEnabled())
        self.widget.checks["desktop_notifications"].click()
        self.assertEqual(len(requests), 1)
        self.assertEqual(submitted, [])
        self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
        backend.status = lambda done: done("authorized")
        requests[0]("authorized")
        self.settle()
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.widget.checks["desktop_notifications"].isEnabled())
        self.assertEqual(self.widget.notification_permission_text, "")
        self.widget.update_notification_status("denied")
        self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
        self.assertTrue(self.widget.checks["desktop_notifications"].isEnabled())
        self.assertFalse(self.widget.notification_test_button.isEnabled())

    def test_system_enable_checks_fresh_permission_and_test_requires_enabled_switch(self):
        from PySide6.QtWidgets import QPushButton
        submitted, callbacks = [], []
        backend = SimpleNamespace(status=lambda done: done("authorized"),
                                  submit=lambda message, done: (submitted.append(message), done("submitted")))
        consumer = NotificationConsumer(self.widget, backend)
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        self.widget.navigate(3)
        self.settle()
        self.assertFalse(any(button.text() == "系统通知设置" for button in self.widget.tabs.widget(3).findChildren(QPushButton)))
        backend.status = callbacks.append
        self.widget.checks["desktop_notifications"].click()
        self.assertEqual(len(callbacks), 1)
        self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
        self.assertFalse(self.widget.home_channel_checks["desktop_notifications"].isChecked())
        self.assertFalse(self.widget.notification_test_button.isEnabled())
        self.widget.update_notification_status("authorized")  # Older queued status cannot bypass the fresh check.
        callbacks.pop()("denied")
        self.settle()
        self.assertEqual(self.widget.api.posts, [])
        self.assertIn("系统设置 → 通知 → SeeEnd", self.widget.preference_help["desktop_notifications"].toolTip())
        self.assertIn("系统设置 → 通知 → SeeEnd", self.widget.home_channel_checks["desktop_notifications"].toolTip())
        self.assertTrue(self.widget.statusbar.isHidden())
        self.widget.test_notification()
        self.assertEqual(submitted, [])
        self.widget.home_channel_checks["desktop_notifications"].click()
        self.assertEqual(len(callbacks), 1)
        callbacks.pop()("authorized")
        self.settle()
        self.assertEqual(self.widget.api.posts[-1][2], {"desktop_notifications": True})
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.widget.notification_test_button.isEnabled())
        self.widget.notification_test_button.click()
        self.settle()
        self.assertEqual(len(submitted), 1)
        self.widget.checks["desktop_notifications"].click()
        self.complete(self.widget.api.posts[-1])
        self.assertFalse(self.widget.notification_test_button.isEnabled())
        self.widget.notification_test_button.click()
        self.widget.test_notification()
        self.assertEqual(len(submitted), 1)

    def test_system_enable_ignores_expired_canceled_and_disconnected_permission_results(self):
        callbacks = []
        backend = SimpleNamespace(status=lambda done: done("authorized"))
        consumer = NotificationConsumer(self.widget, backend)
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        self.settle()
        backend.status = callbacks.append
        self.widget.checks["desktop_notifications"].click()
        old = callbacks.pop()
        self.widget.notification_enable_timed_out()
        old("authorized")
        self.settle()
        self.assertEqual(self.widget.api.posts, [])
        self.assertIsNone(self.widget.notification_enable_pending)
        self.widget.checks["desktop_notifications"].click()
        old = callbacks.pop()
        self.widget.setting("desktop_notifications", False)
        self.complete(self.widget.api.posts[-1])
        old("authorized")
        self.settle()
        self.assertFalse(any(post[2].get("desktop_notifications") for post in self.widget.api.posts))
        self.widget.checks["desktop_notifications"].click()
        old = callbacks.pop()
        self.widget.on_error("本地服务未连接，请重新打开 SeeEnd 后重试。")
        self.widget.ready = True
        self.render()
        old("authorized")
        self.settle()
        self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
        self.assertFalse(any(post[2].get("desktop_notifications") for post in self.widget.api.posts))

    def test_system_permission_grant_rechecks_settings_before_saving(self):
        requests = []
        backend = SimpleNamespace(status=lambda done: done("not_determined"), authorize=requests.append)
        consumer = NotificationConsumer(self.widget, backend)
        self.addCleanup(consumer.shutdown)
        consumer.timer.stop()
        self.settle()
        self.widget.checks["desktop_notifications"].click()
        self.settle()
        self.assertEqual(len(requests), 1)
        self.assertFalse(self.widget.notification_enable_timer.isActive())
        backend.status = lambda done: done("denied")
        requests.pop()("authorized")
        self.settle()
        self.assertEqual(self.widget.api.posts, [])
        self.assertFalse(self.widget.checks["desktop_notifications"].isChecked())
        self.assertFalse(self.widget.notification_test_button.isEnabled())
        self.assertIn("允许通知", self.widget.preference_help["desktop_notifications"].toolTip())
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_disconnected_service_turns_off_notification_switches(self):
        self.wechat = {"bound": True, "available": True}
        self.engine.update_settings({"desktop_notifications": True, "wechat_notifications": True})
        self.render()
        self.widget.update_notification_status("authorized")
        self.assertTrue(self.widget.checks["desktop_notifications"].isChecked())
        self.assertTrue(self.widget.checks["wechat_notifications"].isChecked())
        self.widget.on_error("本地服务未连接，请重新打开 SeeEnd 后重试。")
        for key in ("desktop_notifications", "wechat_notifications"):
            self.assertFalse(self.widget.checks[key].isChecked())
            self.assertFalse(self.widget.checks[key].isEnabled())

    def test_minimum_english_device_detail_has_no_horizontal_overflow(self):
        self.engine.update_settings({"language": "en", "hardware_notifications": True})
        self.render()
        self.widget.navigate(1)
        self.widget.selected_device_id = "fixture"
        device = {"device_id": "fixture", "hardware_model": "yeelight-yltd003", "name": "Fixture lamp",
                  "online": True, "firmware_version": "38"}
        self.widget.render({**self.engine.snapshot(), "maintenance": False, "devices": [device],
                            "device": {**self.engine.device, "online": True, "connected_count": 1}})
        self.widget.open_device_details("yeelight-yltd003")
        self.widget.render_device({"selected_device_id": "fixture", "hardware_model": "yeelight-yltd003",
                                   "device": device, "devices": [device], "yeelight_host": "192.0.2.1"})
        self.widget.resize(920, 680)
        self.settle()
        self.assertTrue(self.widget.device_details.isVisible())
        self.assertEqual(self.widget.tabs.widget(1).horizontalScrollBar().maximum(), 0)
        viewport = self.widget.tabs.widget(1).viewport()
        for button in (self.widget.yeelight_delete_button, *self.widget.yeelight_state_resets.values(),
                       *self.widget.yeelight_color_buttons.values()):
            if not button.isVisible():
                continue
            right = button.mapTo(viewport, button.rect().topRight()).x()
            self.assertLess(right, viewport.width())
        if os.environ.get("SEEEND_UI_CAPTURE_DIR"):
            out = Path(os.environ["SEEEND_UI_CAPTURE_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            self.widget.grab().save(str(out / "english-device-minimum.png"))

    def test_source_switches_keep_category_and_enabled_sources_come_first(self):
        self.engine.update_settings({'source_enabled': {**self.engine.settings['source_enabled'],
                                                       'claude-code': False, 'qoder': False}})
        self.render()
        # A category chosen while the first status request is pending must survive it.
        with patch("aiflame.desktop.widgets.LocalAPI", ControlledAPI):
            pending = Dashboard()
        try:
            self.assertEqual(pending.source_category.currentData(), "web")
            pending.source_category.setCurrentIndex(pending.source_category.findData("cli"))
            pending.render({**self.engine.snapshot(), "maintenance": False})
            self.assertEqual(pending.source_category.currentData(), "cli")
        finally:
            pending.shutdown()
            pending.close()
        self.assertTrue(self.engine.settings["source_enabled"]["codex"])
        self.assertEqual(self.widget.source_category.currentData(), "web")
        self.assertFalse(self.widget.source_checks["claude-code"].isChecked())
        enabled = {**self.engine.settings["source_enabled"], "claude-code": True}
        self.engine.update_settings({"source_enabled": enabled})
        self.render()
        self.assertEqual(self.widget.source_category.currentData(), "web")
        self.assertTrue(self.widget.source_checks["claude-code"].isChecked())
        self.widget.navigate(2)
        self.widget.source_category.setCurrentIndex(self.widget.source_category.findData("cli"))
        self.render()
        self.assertEqual(self.widget.source_category.currentData(), "cli")
        enabled_frame = self.widget.source_blocks["claude-code"][0]
        disabled_frame = self.widget.source_blocks["qoder"][0]
        self.assertLess(enabled_frame.y(), disabled_frame.y())
        self.assertFalse(self.widget.source_sections["web"][0].isVisible())

    def test_activity_english_filters_fit_minimum_width_and_results_keep_channel_scope(self):
        self.engine.update_settings({"language": "en"})
        self.engine.accept(make_event("local-script", "activity-width", "done"), "local-script")
        self.render()
        self.widget.resize(920, 680)
        self.widget.navigate(4)
        self.settle()
        self.assertEqual(self.widget.tabs.widget(4).horizontalScrollBar().maximum(), 0)
        self.assertEqual(self.widget.history.horizontalHeaderItem(3).text(), "Light result")
        self.assertNotRegex(self.widget.history.horizontalHeaderItem(3).toolTip(), r"[\u4e00-\u9fff]")
        self.assertEqual(self.widget.history.rowCount(), 1)
        self.widget.history_view.setCurrentIndex(self.widget.history_view.findData("events"))
        self.settle()
        self.assertEqual(self.widget.history.rowCount(), 1)

    def test_current_browser_setup_sentence_is_fully_english(self):
        self.engine.update_settings({"language": "en"})
        self.render()
        text = self.widget.browser_install_hint.text()
        self.assertIn("Install and connect", text)
        self.assertNotIn("首次", text)
        self.widget.resize(920, 680)
        self.widget.navigate(2)
        self.widget.source_category.setCurrentIndex(1)
        self.settle()
        tabs = self.widget.source_category
        for index in range(tabs.count()):
            self.assertTrue(tabs.rect().contains(tabs.tabRect(index)))
