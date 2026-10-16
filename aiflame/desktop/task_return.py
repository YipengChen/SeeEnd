"""Return actions keep the dashboard responsive while authorization is pending."""

from urllib.parse import urlencode
from PySide6.QtCore import QTimer, Qt
from .localization import t


def return_unavailable_message(event):
    if not event:
        return "当前没有可返回的任务。请从活动记录选择任务。"
    destination = event.get("return_to")
    if not destination:
        return "此记录没有返回位置。请更新接入后发起一条新任务；试亮和模拟任务不能返回。"
    if not destination.get("available", True):
        return "原终端已切换到其他会话，请在编程工具中恢复原会话。"
    return None


class TaskReturnActions:
    def return_failed_message(self, message, event_id=None):
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.tell(message, error=True, duration_ms=2000,
                  retry=(lambda: self.return_task(event_id)) if event_id else None)
        self.return_failed.emit()

    def init_task_returns(self):
        self.return_requests = {}
        self.return_timer = QTimer(self)
        self.return_timer.setInterval(400)
        self.return_timer.timeout.connect(self.poll_task_returns)

    def return_task(self, event_id):
        if not self.ready or not isinstance(event_id, str) or event_id in self.return_requests:
            return
        self.return_requests[event_id] = None
        self.refresh_return_buttons()

        def started(data):
            if event_id not in self.return_requests:
                return
            self.return_requests[event_id] = data["request_id"]
            self.return_timer.start()

        def failed():
            self.return_requests.pop(event_id, None)
            self.refresh_return_buttons()

        self.api.request("/api/task-return", started, body={"event_id": event_id},
                         failed=failed, error_callback=lambda message: self.return_failed_message(message, event_id))

    def return_selected_task(self):
        row = self.history.currentRow()
        if self.history.selectionModel().hasSelection() and 0 <= row < len(self.visible_events):
            self.return_task(self.visible_events[row]["id"])

    def return_displayed_task(self):
        if not self.ready:
            self.return_failed_message("本地服务未连接，请重新打开 SeeEnd。")
            return
        display = self.model.get("display", {}) if self.model else {}
        event = ({"return_to": None} if self.preview_phase or display.get("simulated")
                 else display.get("return_task"))
        if message := return_unavailable_message(event):
            self.return_failed_message(message)
        else:
            self.return_task(event["id"])

    def configure_return_button(self, button, event):
        event_id = event["id"]
        destination = event.get("return_to")
        pending = event_id in self.return_requests
        button.setProperty("returnEventId", event_id)
        button.setText(("正在定位…" if destination and destination.get("requires_resolution") else "正在返回…")
                       if pending else "返回任务")
        button.setEnabled(self.ready and bool(destination) and destination.get("available", True) and not pending)
        if pending:
            hint = "正在返回任务；终端首次使用时，请处理系统自动化授权弹窗。"
        elif destination and not destination.get("available", True):
            hint = "原终端已切换到其他会话，请在编程工具中恢复原会话。"
        elif destination:
            hint = ("按当前活终端的名称和目录唯一匹配；旧记录可能返回同名的新会话。" if destination.get("requires_resolution")
                    else "返回对应网页会话" if destination["kind"] == "web" else "返回对应终端标签页或分屏")
            if destination.get("project"):
                hint = t(hint) + " · " + destination["project"]
        else:
            hint = "此记录没有返回位置。请更新接入后发起一条新任务；试亮和模拟任务不能返回。"
        button.setToolTip(hint)
        button.setAccessibleName(t("返回任务") + " · " + t(self.event_values(event)[1]))

    def refresh_return_buttons(self):
        if not self.built:
            return
        events = {event["id"]: event for event in self.model.get("task_events", self.model.get("events", []))} if self.model else {}
        for button in [*self.recent_return_buttons, self.return_task_button]:
            event = events.get(button.property("returnEventId"))
            if event:
                self.configure_return_button(button, event)

    def poll_task_returns(self):
        if not self.return_requests:
            self.return_timer.stop()
            return
        for event_id, identifier in list(self.return_requests.items()):
            if not identifier:
                continue
            def received(data, event_id=event_id, identifier=identifier):
                if self.return_requests.get(event_id) != identifier or data["status"] == "pending":
                    return
                self.return_requests.pop(event_id, None)
                self.refresh_return_buttons()
                if data["status"] != "opened":
                    self.return_failed_message(data.get("message") or "无法返回任务，请稍后重试。", event_id)

            def failed(event_id=event_id):
                self.return_requests.pop(event_id, None)
                self.refresh_return_buttons()

            self.api.request("/api/task-return?" + urlencode({"request_id": identifier}), received,
                             failed=failed,
                             error_callback=lambda message, event_id=event_id: self.return_failed_message(message, event_id))
