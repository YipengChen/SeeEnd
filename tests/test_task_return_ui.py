"""Direct task actions, loading states and old-record explanations."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.integrations.registry import SOURCES
from aiflame.desktop.widgets import Dashboard
from aiflame.desktop.localization import set_language


class API(QObject):
    error = Signal(str)
    def __init__(self, *args):
        super().__init__()
        self.calls = []
    def request(self, path, callback=None, body=None, **kwargs):
        if body is not None:
            kwargs['body'] = body
        self.calls.append((path, callback, kwargs))
    def close(self):
        pass


class ReturnUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "events.sqlite", sources=SOURCES)
        with patch("aiflame.desktop.widgets.LocalAPI", API):
            self.widget = Dashboard()
        self.widget.ready = True
        self.widget.resize(1180, 850)
        self.widget.show()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.widget.shutdown()
        self.widget.hide()
        self.engine.db.close()
        self.temp.cleanup()
        set_language("zh-CN")

    def render(self):
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        for _ in range(4):
            self.app.processEvents()
        self.widget.render_recent()

    def test_single_task_recovery_is_bound_to_current_cli_event_and_translates(self):
        event = make_event('codex', 'residual', 'working')
        self.engine.accept(event, 'codex')
        self.render()
        self.widget.navigate(4)
        self.widget.history.selectRow(0)
        self.widget.show_event_detail()
        self.assertTrue(self.widget.mark_unknown_button.isVisible())
        set_language('en')
        self.assertEqual(self.widget.mark_unknown_button.text(), 'Mark as unknown')
        self.widget.mark_unknown_button.click()
        path, callback, options = self.widget.api.calls[-1]
        self.assertEqual(path, '/api/tasks/mark-unknown')
        self.assertEqual(options['body'], {'event_id': event['event_id']})
        self.assertFalse(self.widget.mark_unknown_button.isEnabled())
        callback(self.engine.mark_task_unknown(event['event_id']))
        self.render()
        self.widget.history.selectRow(0)
        self.widget.show_event_detail()
        self.assertFalse(self.widget.mark_unknown_button.isVisible())

    def test_recent_action_binds_exact_task_disables_double_click_and_completes(self):
        import uuid
        events = []
        for task in ("one", "two"):
            event = {**make_event("chatgpt-web", task, "working"), "return_target":{
                "kind":"web", "client_id":str(uuid.uuid4()), "locator_id":str(uuid.uuid4())}}
            self.engine.accept(event, "chatgpt-web")
            events.append(event)
        self.render()
        button = self.widget.recent_return_buttons[0]
        button.click()
        path, callback, options = self.widget.api.calls[-1]
        self.assertEqual(path, "/api/task-return")
        self.assertEqual(options["body"]["event_id"], events[-1]["event_id"])
        self.assertFalse(button.isEnabled())
        before = len(self.widget.api.calls)
        button.click()
        self.assertEqual(len(self.widget.api.calls), before)
        callback({"request_id":"request-one"})
        self.widget.poll_task_returns()
        self.widget.api.calls[-1][1]({"status":"opened"})
        self.assertTrue(button.isEnabled())

    def test_legacy_detail_explains_missing_location_and_failure_is_retryable(self):
        event = make_event("codex", "legacy", "working")
        self.engine.accept(event, "codex")
        self.render()
        self.widget.navigate(4)
        self.widget.history.selectRow(0)
        self.widget.show_event_detail()
        self.assertFalse(self.widget.return_task_button.isEnabled())
        self.assertIn("新任务", self.widget.return_task_button.toolTip())
        self.widget.return_task(event["event_id"])
        options = self.widget.api.calls[-1][2]
        options["error_callback"]("无法返回任务，请稍后重试。")
        options["failed"]()
        self.assertNotIn(event["event_id"], self.widget.return_requests)

    def test_overview_flame_double_click_returns_displayed_task(self):
        event = {**make_event("chatgpt-web", "one", "working"), "return_target": {
            "kind": "web", "client_id": str(uuid.uuid4()), "locator_id": str(uuid.uuid4()),
        }}
        self.engine.accept(event, "chatgpt-web")
        self.render()
        QTest.mouseDClick(self.widget.flame, Qt.MouseButton.LeftButton)
        self.assertEqual(self.widget.api.calls[-1][2]["body"], {"event_id": event["event_id"]})

    def test_codex_context_enables_return_and_pending_location_feedback(self):
        event = {**make_event("codex", "normal-cli", "working"), "return_context": {
            "session_id": str(uuid.uuid4()), "turn_id": str(uuid.uuid4()), "cwd": "/Users/test/project"}}
        self.engine.accept(event, "codex")
        self.render()
        button = self.widget.recent_return_buttons[0]
        self.assertTrue(button.isEnabled())
        self.assertIn("唯一匹配", button.toolTip())
        button.click()
        self.assertEqual(button.text(), "正在定位…")
        self.assertFalse(button.isEnabled())
        self.assertEqual(self.widget.api.calls[-1][2]["body"], {"event_id": event["event_id"]})

    def test_return_failure_disappears_after_two_seconds_without_moving_the_cards(self):
        self.render()
        before = (self.widget.recent_frame.geometry(), self.widget.flame.geometry())
        self.widget.return_failed_message("会话「回复123」对应 2 个终端位置，无法唯一定位。")
        self.app.processEvents()
        self.assertTrue(self.widget.statusbar.isVisible())
        self.assertEqual(self.widget.message_timer.interval(), 2000)
        self.assertEqual(before, (self.widget.recent_frame.geometry(), self.widget.flame.geometry()))
        self.assertEqual(self.widget.message.textFormat(), Qt.TextFormat.PlainText)
        QTest.qWait(2200)
        self.assertFalse(self.widget.statusbar.isVisible())
        self.widget.tell("普通提示")
        self.assertEqual(self.widget.message_timer.interval(), 5000)

    def test_return_failure_retry_reuses_the_original_event(self):
        self.render()
        self.widget.return_failed_message("无法唯一定位。", "exact-event")
        self.assertEqual(self.widget.retry_button.text(), "重新定位")
        self.widget.retry_button.click()
        self.assertEqual(self.widget.api.calls[-1][2]["body"], {"event_id": "exact-event"})

    def test_recent_mixed_return_rows_align_in_both_languages_and_window_sizes(self):
        self.engine.accept(make_event("codex", "legacy", "working"), "codex")
        for task in ("web-one", "web-two"):
            event = {**make_event("chatgpt-web", task, "working"), "return_target": {
                "kind": "web", "client_id": str(uuid.uuid4()), "locator_id": str(uuid.uuid4()),
            }}
            self.engine.accept(event, "chatgpt-web")
            if task == "web-one":
                self.widget.return_requests[event["event_id"]] = None
        for lang in ("zh-CN", "en"):
            self.engine.update_settings({"language": lang})
            for width, height in ((1180, 850), (920, 680)):
                with self.subTest(language=lang, width=width):
                    self.widget.resize(width, height)
                    self.render()
                    times, outcomes = [], []
                    for index, labels in enumerate(self.widget.recent_rows):
                        frame = self.widget.recent_row_frames[index]
                        if frame.isHidden():
                            continue
                        when, source, state, result = labels
                        button = self.widget.recent_return_buttons[index]
                        times.append(when.geometry().right())
                        outcomes.append(result.geometry().right())
                        self.assertLessEqual(abs(when.geometry().center().y() - source.geometry().center().y()), 1)
                        self.assertLessEqual(abs(state.geometry().center().y() - result.geometry().center().y()), 1)
                        self.assertEqual(state.fontMetrics().elidedText(state.text(), Qt.TextElideMode.ElideRight,
                                                                      state.contentsRect().width()), state.text())
                        for control in (*labels, button):
                            self.assertTrue(frame.rect().contains(control.geometry()))
                        if not button.isHidden():
                            self.assertGreater(button.geometry().top(), source.geometry().bottom())
                            self.assertLess(button.geometry().right(), result.geometry().left())
                    self.assertEqual(len(set(times)), 1)
                    self.assertEqual(len(set(outcomes)), 1)

    def test_flame_unavailable_states_explain_reason_without_return_requests(self):
        from aiflame.desktop.floating_flame import DesktopFlame
        pet = DesktopFlame(Path(self.temp.name) / "desktop-flame.json")
        self.addCleanup(pet.shutdown)
        pet.return_requested.connect(self.widget.return_task)
        self.render()
        base = self.widget.model
        cases = [(None, "本地服务未连接"),
                 ({**base, "latest_task": None}, "当前没有可返回"),
                 ({**base, "latest_task": {"id": "legacy", "return_to": None}}, "新任务"),
                 ({**base, "latest_task": {"id": "old", "return_to": {
                     "kind": "terminal", "available": False}}}, "其他会话")]
        for data, hint in cases:
            with self.subTest(hint=hint), patch("PySide6.QtWidgets.QToolTip.showText") as tooltip:
                before = len(self.widget.api.calls)
                pet.update_status(data)
                QTest.mouseDClick(pet, Qt.MouseButton.LeftButton)
                QTest.mouseRelease(pet, Qt.MouseButton.LeftButton)
                self.assertEqual(len(self.widget.api.calls), before)
                self.assertIn(hint, tooltip.call_args.args[1])



if __name__ == "__main__":
    unittest.main()
