"""Presentation state for reversible saves and native notification controls."""

import sys
from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QDesktopServices
from .components import restyle
from .localization import source_text, t


class DashboardInteraction:
    def eventFilter(self, watched, event):
        if self.built and watched is self.tabs.widget(1).viewport() and event.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self, self.reflow_device_controls)
        return super().eventFilter(watched, event)

    @staticmethod
    def event_key(event):
        return (event["source"], event.get("task") or ("event", event["id"]),
                bool(event.get("simulated") or event["source"] in ("manual", "demo")))

    def save_setting(self, key, value, *, callback=None):
        source = key.removeprefix("source:") if key.startswith("source:") else None
        body = {"source_enabled": {source: value}} if source else {key: value}
        record = {"value": value, "state": "saving", "callback": callback}
        self.setting_saves[key] = record
        self.render_save_feedback(key)

        def saved(data):
            if self.setting_saves.get(key) is record:
                record["state"] = "saved"
                self.render_save_feedback(key)
            if callback:
                callback(data)

        def failed():
            if self.setting_saves.get(key) is record:
                record.update(state="failed", error=source_text(self.message))
                self.render_save_feedback(key)

        self.act("/api/settings", body, saved, failed)

    def retry_setting(self, key):
        record = self.setting_saves.get(key)
        if record and record["state"] == "failed" and self.ready:
            self.save_setting(key, record["value"], callback=record.get("callback"))

    def render_save_feedback(self, key=None):
        keys = [key] if key else list(self.save_feedback)
        for current in keys:
            if current not in self.save_feedback:
                continue
            frame, message, retry = self.save_feedback[current]
            record = self.setting_saves.get(current)
            frame.setVisible(bool(record))
            if not record:
                continue
            state = record["state"]
            value = (t("开启") if record["value"] else t("关闭")) if isinstance(record["value"], bool) else str(record["value"])
            message.setText(t("未保存；待保存值：") + value + t("。请重试。") if state == "failed"
                            else {"saving": "正在保存…", "saved": "已保存"}[state])
            message.setToolTip(record.get("error", ""))
            restyle(message, "tone", "warning" if state == "failed" else "neutral")
            retry.setVisible(state == "failed")
            retry.setEnabled(self.ready and state == "failed" and not self.writing)

    def source_category_changed(self, index):
        category = self.source_category.itemData(index)
        for key, (frame, _) in self.source_sections.items():
            frame.setVisible(key == category)
        for key, panel in getattr(self, "integration_help_panels", {}).items():
            panel.setVisible(key == category)

    def order_sources(self):
        # Move whole source blocks so their actions and diagnostics stay together.
        for _, layout in self.source_sections.values():
            blocks = [(sid, frame) for sid, (frame, owner) in self.source_blocks.items()
                      if owner is layout]
            wanted = sorted(blocks, key=lambda pair: not self.source_values.get(pair[0], False))
            current = [layout.itemAt(i).widget() for i in range(layout.count())
                       if layout.itemAt(i).widget() in [frame for _, frame in blocks]]
            if current != [frame for _, frame in wanted]:
                for _, frame in wanted:
                    layout.removeWidget(frame)
                for sid, frame in wanted:
                    layout.addWidget(frame)

    def reflow_device_controls(self):
        if not self.built:
            return
        width = self.tabs.widget(1).viewport().width()
        columns = 1 if width < 676 else 2
        if columns != self.device_card_columns:
            self.device_card_columns = columns
            for button in self.device_model_buttons.values():
                self.device_cards_layout.removeWidget(button)
            for index, button in enumerate(self.device_model_buttons.values()):
                self.device_cards_layout.addWidget(button, index // columns, index % columns)
        self.device_actions_layout.setDirection(
            self.device_actions_layout.Direction.TopToBottom if width < 820
            else self.device_actions_layout.Direction.LeftToRight)
        self.color_heading_layout.setDirection(
            self.color_heading_layout.Direction.TopToBottom if width < 740
            else self.color_heading_layout.Direction.LeftToRight)

    def render_task_context(self, names):
        tasks = self.model.get("tasks", [])
        enabled = self.model["settings"]["source_enabled"]
        active = [task for task in tasks if task["state"] in ("working", "needs_input")
                  and enabled.get(task["source"], False)]
        working = self.model.get("active_count", 0)
        waiting = sum(task["state"] == "needs_input" for task in active)
        self.home_task_summary.setText(f"进行中 {working} · 等待输入 {waiting}")
        text = "\n".join(names.get(task["source"], task["source"]) + " · " + task["task"]
                         for task in active)
        self.home_task_summary.setToolTip(text)
        self.home_task_context.setText(
            " · ".join(dict.fromkeys(names.get(task["source"], task["source"]) for task in active))
            if active else "接入应用后，新任务会自动出现在这里。")

    def displayed_task_state(self):
        display = self.model["display"]
        # The lamp uses the error pattern for input requests; keep the UI's
        # task meaning tied to the actual event rather than the light pattern.
        event = next((item for item in self.model.get("events", [])
                      if item["id"] == display.get("key")), None)
        return "needs_input" if event and event["state"] == "needs_input" else display["state"]

    def copy_task_id(self):
        row = self.history.currentRow()
        if self.history.selectionModel().hasSelection() and 0 <= row < len(self.visible_events):
            from PySide6.QtWidgets import QApplication
            task = self.visible_events[row].get("task")
            if task:
                QApplication.clipboard().setText(task)
                self.tell("任务标识已复制。")
            else:
                self.tell("此记录未提供任务标识。")

    def request_notification_status(self):
        self.notification_permission.setText("正在读取系统通知状态…")
        self.notification_status_requested.emit()

    def update_notification_status(self, status):
        self.notification_status = status
        texts = {"authorized": "系统已允许通知；显示仍取决于专注模式与系统通知设置。",
                 "available": "系统通知通道可用；显示仍取决于系统通知设置。",
                 "denied": "系统未允许通知，请在系统设置中开启 SeeEnd 通知。",
                 "not_determined": "尚未授权。点击「测试通知」发起授权并检查显示。",
                 "unavailable": "系统通知通道不可用。macOS 请使用打包应用。",
                 "unknown": "暂时无法读取系统通知状态，请重新检查。"}
        self.notification_permission.setText(texts.get(status, texts["unknown"]))
        self.notification_test_button.setEnabled(status != "unavailable" and not self.notification_testing)

    def test_notification(self):
        if self.notification_testing:
            return
        self.notification_testing = True
        self.notification_test_button.setEnabled(False)
        self.notification_test_feedback.setText("正在提交测试通知…")
        self.notification_test_requested.emit()

    def notification_test_finished(self, status):
        self.notification_testing = False
        messages = {"submitted": "测试通知已提交系统，请确认是否看到。未显示时请检查专注模式。",
                    "denied": "测试未提交：系统通知权限未允许。请打开系统设置后重试。",
                    "unavailable": "测试未提交：系统通知通道不可用。",
                    "failed": "测试通知提交失败，请重新检查后重试。"}
        self.notification_test_feedback.setText(messages.get(status, messages["failed"]))
        self.notification_test_button.setEnabled(self.notification_status != "unavailable")
        self.request_notification_status()

    def open_notification_settings(self):
        from PySide6.QtCore import QUrl
        url = "x-apple.systempreferences:com.apple.preference.notifications" if sys.platform == "darwin" else "ms-settings:notifications" if sys.platform == "win32" else None
        if not url or not QDesktopServices.openUrl(QUrl(url)):
            self.tell("请在系统设置的「通知」中找到 SeeEnd，允许通知。")
