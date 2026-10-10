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
    Signal = lambda *args: None


class ControlledAPI(QObject):
    error = Signal(str)

    def __init__(self, parent, port):
        super().__init__(parent)
        self.posts = []

    def request(self, path, callback=None, body=None, **kwargs):
        if body is not None:
            self.posts.append((path, callback, body, kwargs.get("failed")))

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

    def render(self):
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.settle()

    def complete(self, post):
        _, callback, body, _ = post
        settings = self.engine.update_settings(body)
        callback({"settings": settings})
        self.render()

    def fail_post(self, post):
        self.widget.api.error.emit("保存失败，请重试。")
        post[3]()

    def test_later_success_keeps_failed_field_and_retry_restores_requested_value(self):
        self.widget.navigate(3)
        self.widget.checks["quiet_enabled"].click()
        self.widget.checks["paused"].click()
        first = self.widget.api.posts[0]
        self.fail_post(first)
        self.complete(self.widget.api.posts[1])
        self.assertFalse(self.widget.checks["quiet_enabled"].isChecked())
        self.assertTrue(self.widget.checks["paused"].isChecked())
        self.assertIn("未保存", self.widget.message.text())
        self.assertFalse(self.widget.message_timer.isActive())
        frame, message, retry = self.widget.save_feedback["quiet_enabled"]
        self.assertTrue(frame.isVisible())
        self.assertIn("开启", message.text())
        self.assertTrue(retry.isEnabled())
        retry.click()
        self.assertEqual(self.widget.api.posts[-1][2], {"quiet_enabled": True})
        self.complete(self.widget.api.posts[-1])
        self.assertTrue(self.widget.checks["quiet_enabled"].isChecked())
        self.assertEqual(self.widget.setting_saves["quiet_enabled"]["state"], "saved")

    def test_source_queue_does_not_resubmit_another_sources_failed_change(self):
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
        self.assertNotRegex(self.widget.history_count.text(), r"[\u4e00-\u9fff]")
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
        self.assertIn("等待输入或授权", self.widget.notice_text.text())
        texts = [labels[1].text() for labels in self.widget.recent_rows[:2]]
        self.assertTrue(any("one" in text for text in texts))
        self.assertTrue(any("two" in text for text in texts))

    def test_disconnection_reports_unavailable_and_keeps_last_update(self):
        self.widget.navigate(0)
        self.widget.on_error("Hub 未连接，请检查 SeeEnd 服务")
        self.assertEqual(self.widget.display_title.text(), "任务状态暂不可用")
        self.assertIn("最近更新时间", self.widget.display_hint.text())
        self.assertTrue(self.widget.retry_button.isVisible())

    def test_notifications_show_permission_and_submission_without_claiming_display(self):
        submitted = []
        backend = SimpleNamespace(status=lambda done: done("denied"),
                                  submit=lambda message, done: (submitted.append(message), done("submitted")))
        consumer = NotificationConsumer(self.widget, backend)
        self.addCleanup(consumer.shutdown)
        self.settle()
        self.assertIn("未允许", self.widget.notification_permission.text())
        self.widget.test_notification()
        self.settle()
        self.assertEqual(len(submitted), 1)
        self.assertIn("已提交系统", self.widget.notification_test_feedback.text())
        self.assertNotIn("已显示", self.widget.notification_test_feedback.text())
        self.assertFalse(self.widget.notification_testing)

    def test_minimum_english_device_detail_has_no_horizontal_overflow(self):
        self.engine.update_settings({"language": "en", "mode": "hardware"})
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
        for button in (self.widget.yeelight_delete_button, self.widget.yeelight_color_reset,
                       *self.widget.yeelight_color_buttons.values()):
            right = button.mapTo(viewport, button.rect().topRight()).x()
            self.assertLess(right, viewport.width())
        if os.environ.get("SEEEND_UI_CAPTURE_DIR"):
            out = Path(os.environ["SEEEND_UI_CAPTURE_DIR"])
            out.mkdir(parents=True, exist_ok=True)
            self.widget.grab().save(str(out / "english-device-minimum.png"))

    def test_disabled_sources_are_neutral_and_enabled_sources_come_first(self):
        self.assertEqual(self.widget.source_status["claude-code"].text(), "已关闭")
        self.assertEqual(self.widget.source_status["claude-code"].styleSheet(), "")
        enabled = {**self.engine.settings["source_enabled"], "claude-code": True}
        self.engine.update_settings({"source_enabled": enabled})
        self.render()
        self.assertEqual(self.widget.source_status["claude-code"].text(), "等待新任务")
        self.widget.navigate(2)
        self.widget.source_category.setCurrentIndex(self.widget.source_category.findData("cli"))
        self.settle()
        enabled_frame = self.widget.source_blocks["claude-code"][0]
        disabled_frame = self.widget.source_blocks["qoder"][0]
        self.assertLess(enabled_frame.y(), disabled_frame.y())
        self.assertFalse(self.widget.source_sections["web"][0].isVisible())

    def test_current_browser_setup_sentence_is_fully_english(self):
        self.engine.update_settings({"language": "en"})
        self.render()
        text = self.widget.browser_install_hint.text()
        self.assertIn("install it once", text)
        self.assertNotIn("首次", text)
