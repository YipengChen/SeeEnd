"""Presentation state for reversible saves and native notification controls."""

from PySide6.QtCore import QEvent, QTimer, QSignalBlocker
from .components import restyle
from .localization import source_text, t


class DashboardInteraction:
    def eventFilter(self, watched, event):
        if self.built and event.type() == QEvent.Type.Resize:
            if watched is self.tabs.widget(0).viewport():
                self.reflow_layout()
            elif watched is self.tabs.widget(1).viewport():
                QTimer.singleShot(0, self, self.reflow_device_controls)
            elif watched is self.home_sources_row or watched in self.home_source_layouts:
                QTimer.singleShot(0, self, self.reflow_home_sources)
            elif watched is self.recent_list_body:
                self.recent_reflow_timer.start(0)
        return super().eventFilter(watched, event)

    def reflow_home_channels(self):
        if not self.built:
            return
        page = self.tabs.widget(0)
        page_margins = page.widget().layout().contentsMargins()
        margins = self.home_channels_layout.contentsMargins()
        available = (page.viewport().width() - page_margins.left() - page_margins.right()
                     - margins.left() - margins.right() - 2 * self.home_channels_frame.frameWidth())
        count = len(self.home_channel_items)
        spacing = self.home_channels_grid.horizontalSpacing()
        required = (self.home_channel_heading.sizeHint().width()
                    + sum(item.minimumSizeHint().width() for item in self.home_channel_items)
                    + (count - 1) * spacing
                    + self.home_channels_heading.horizontalSpacing())
        inline = available >= required
        item_width = max(item.minimumSizeHint().width() for item in self.home_channel_items)
        capacity = max(1, min(count, (available + spacing) // (item_width + spacing)))
        rows = (count + capacity - 1) // capacity
        columns = count if inline else (count + rows - 1) // rows
        if (inline, columns) == (self.home_channels_inline, self.home_channels_columns):
            return
        self.home_channels_inline = inline
        self.home_channels_columns = columns
        for item in self.home_channel_items:
            self.home_channels_grid.removeWidget(item)
        for index, item in enumerate(self.home_channel_items):
            self.home_channels_grid.addWidget(item, index // columns, index % columns)
        for column in range(count):
            self.home_channels_grid.setColumnStretch(column, int(column < columns))
        self.home_channels_heading.removeItem(self.home_channels_grid)
        if inline:
            self.home_channels_heading.addLayout(self.home_channels_grid, 0, 1)
        else:
            self.home_channels_heading.addLayout(self.home_channels_grid, 1, 0, 1, 2)

    def reflow_home_sources(self):
        if not self.built:
            return
        changed = False
        for group, sources in self.home_source_groups.values():
            buttons = [(source, self.home_source_icons[source]) for source in sources
                       if not self.home_source_icons[source].isHidden()]
            if not buttons:
                continue
            grid = self.home_source_layouts[group]
            spacing = grid.horizontalSpacing()
            width = max(button.width() for _, button in buttons)
            capacity = max(1, min(len(buttons), (group.width() + spacing) // (width + spacing)))
            rows = (len(buttons) + capacity - 1) // capacity
            columns = (len(buttons) + rows - 1) // rows
            state = columns, tuple(source for source, _ in buttons)
            if self.home_source_grid_state.get(group) == state:
                continue
            changed = True
            self.home_source_grid_state[group] = state
            for source in sources:
                grid.removeWidget(self.home_source_icons[source])
            for index, (_, button) in enumerate(buttons):
                grid.addWidget(button, index // columns, index % columns)
            for column in range(len(sources)):
                grid.setColumnStretch(column, 0)
            group.updateGeometry()
        if changed:
            # Icon wrapping can change during a resize. Refresh the enclosing
            # height-for-width cache after reflow so the scroll area fits it.
            self.home_sources_row.updateGeometry()
            self.home_sources_frame.updateGeometry()
            content = self.home_sources_frame.parentWidget()
            content.layout().invalidate()
            content.updateGeometry()

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
                if key in self.home_channel_checks and self.model and key in data.get("settings", {}):
                    self.model["settings"][key] = data["settings"][key]
                record["state"] = "saved"
                self.render_save_feedback(key)
                if key in self.home_channel_checks and self.model:
                    self.render({**self.model, **data})
            if callback:
                callback(data)

        def failed():
            if self.setting_saves.get(key) is record:
                record.update(state="failed", error=record.get("error", source_text(self.message)))
                self.render_save_feedback(key)

        self.act("/api/settings", body, saved, failed)

    def retry_setting(self, key):
        record = self.setting_saves.get(key)
        if record and record["state"] == "failed" and self.ready:
            if key == "desktop_notifications" and record["value"]:
                self.begin_notification_enable()
                return
            if key in ("desktop_notifications", "wechat_notifications") and record["value"] and not self.notification_channel_available(key):
                return
            self.save_setting(key, record["value"], callback=record.get("callback"))

    def render_save_feedback(self, key=None):
        keys = [key] if key else [*self.save_feedback, *self.home_channel_checks]
        for current in keys:
            if current in self.home_channel_checks:
                details = {"desktop_notifications": self.notification_permission_text,
                           "wechat_notifications": self.wechat_notice}.get(current, "")
                self.update_preference_help(current, details)
                continue
            if current not in self.save_feedback:
                continue
            frame, message, retry = self.save_feedback[current]
            record = self.setting_saves.get(current)
            visible = bool(record and record["state"] != "saved")
            frame.setVisible(visible)
            if not visible:
                continue
            state = record["state"]
            value = (t("开启") if record["value"] else t("关闭")) if isinstance(record["value"], bool) else str(record["value"])
            message.setText(t("未保存；待保存值：") + value + t("。请重试。") if state == "failed"
                            else "正在保存…")
            message.setToolTip(record.get("error", ""))
            restyle(message, "tone", "warning" if state == "failed" else "neutral")
            retry.setVisible(state == "failed")
            eligible = current not in ("desktop_notifications", "wechat_notifications") or not record["value"] or self.notification_channel_available(current)
            retry.setEnabled(self.ready and state == "failed" and not self.writing and eligible)

    def has_visible_save_failure(self):
        return any(key not in self.home_channel_checks and item["state"] == "failed"
                   for key, item in self.setting_saves.items())

    def channel_save_feedback(self, key, hint=""):
        record = self.setting_saves.get(key)
        if key in self.home_channel_checks and record and record["state"] == "failed":
            return "\n\n".join(text for text in (t(record.get("error", "")),
                t("未保存；当前仍使用原设置。请重新切换开关重试。"), hint) if text)
        return hint

    def source_category_changed(self, index):
        category = self.source_category.itemData(index)
        for key, (frame, _) in self.source_sections.items():
            frame.setVisible(key == category)
        self.integration_help.set_help(self.integration_help_texts.get(category, ""))

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
        page = self.tabs.widget(1)
        width = page.viewport().width()
        page_margins = page.widget().layout().contentsMargins()
        card_margins = self.device_selector.layout().contentsMargins()
        frame_margins = self.device_selector.contentsMargins()
        grid_width = (width - page_margins.left() - page_margins.right()
                      - card_margins.left() - card_margins.right()
                      - frame_margins.left() - frame_margins.right())
        minimum_card_width = max(button.minimumWidth() for button in self.device_model_buttons.values())
        columns = 2 if grid_width >= 2 * minimum_card_width + self.device_cards_layout.horizontalSpacing() else 1
        if columns != self.device_card_columns:
            self.device_card_columns = columns
            for button in self.device_model_buttons.values():
                self.device_cards_layout.removeWidget(button)
            for index, button in enumerate(self.device_model_buttons.values()):
                self.device_cards_layout.addWidget(button, index // columns, index % columns)
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
        self.home_task_summary.setVisible(bool(working or waiting))
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
        self.notification_status = "checking"
        self.notification_permission_text = "读取授权中…"
        self.update_preference_help("desktop_notifications", self.notification_permission_text)
        self.update_notification_controls()
        self.notification_status_requested.emit()

    def notification_channel_available(self, key):
        if key == "desktop_notifications":
            return self.notification_status in ("authorized", "available")
        return bool(self.wechat_data.get("available", self.wechat_data.get("ready", False)))

    def notification_toggle_value(self, key):
        pending = self.setting_saves.get(key)
        if pending and pending["state"] == "saving":
            return bool(pending["value"])
        return bool(self.model and self.model["settings"][key])

    def channel_toggle_editable(self, key):
        if not self.ready or self.model is None or self.reset_pending:
            return False
        pending = self.setting_saves.get(key)
        if pending and pending["state"] == "saving":
            return False
        if self.writing:
            action = self.active_action
            # Demo requests do not change channel settings; keep the switches
            # stable while the serialized request queue starts or stops a demo.
            return bool(action and (action[0] == "/api/preview" or
                        action[0] == "/api/settings" and action[1]
                        and set(action[1]).issubset(self.home_channel_checks)))
        return True

    def update_channel_controls(self):
        self.reflow_home_channels()
        connected = self.ready and self.model is not None and not self.reset_pending
        for key in ("desktop_flame_enabled", "hardware_notifications", "sound_notifications"):
            toggle = self.checks.get(key)
            if toggle is None:
                continue
            with QSignalBlocker(toggle):
                toggle.setChecked(connected and self.notification_toggle_value(key))
            toggle.setEnabled(self.channel_toggle_editable(key) and
                              (key != "hardware_notifications" or not self.job_busy and not self.model["maintenance"]))
            hint = "关闭主窗口后，仍在桌面显示任务状态。" if key == "desktop_flame_enabled" else ""
            toggle.setToolTip(self.channel_save_feedback(key, hint))
        for key, toggle in self.home_channel_checks.items():
            primary = self.checks.get(key)
            if primary is None:
                continue
            with QSignalBlocker(toggle):
                toggle.setChecked(primary.isChecked())
            toggle.setEnabled(primary.isEnabled())
            toggle.setToolTip(primary.toolTip())
            primary.setAccessibleDescription(primary.toolTip())
            toggle.setAccessibleDescription(primary.toolTip())

    def update_notification_controls(self):
        available = self.notification_channel_available("desktop_notifications")
        connected = self.ready and self.model is not None and not self.reset_pending
        editable = connected and not self.writing
        toggle = self.checks["desktop_notifications"]
        verifying = self.notification_enable_pending is not None
        with QSignalBlocker(toggle):
            toggle.setChecked(connected and available and not verifying and self.notification_toggle_value("desktop_notifications"))
        toggle.setEnabled(self.channel_toggle_editable("desktop_notifications") and
                          not verifying and not self.notification_authorizing)
        toggle.setToolTip(self.channel_save_feedback("desktop_notifications", "" if available else self.notification_permission_text))
        self.notification_test_button.setEnabled(editable and toggle.isChecked() and
                                                 not self.notification_authorizing and not self.notification_testing)
        self.update_channel_controls()

    def notification_action(self):
        self.test_notification()

    def begin_notification_enable(self):
        if not self.channel_toggle_editable("desktop_notifications") or self.notification_enable_pending is not None:
            self.update_notification_controls()
            return
        self.notification_enable_serial += 1
        request = self.notification_enable_serial
        self.notification_enable_pending = request
        self.notification_status = "checking"
        self.notification_permission_text = "读取授权中…"
        self.update_preference_help("desktop_notifications", self.notification_permission_text)
        self.update_notification_controls()
        self.notification_enable_timer.start()
        self.notification_enable_requested.emit(request)

    def notification_enable_phase(self, request, authorizing):
        if request != self.notification_enable_pending:
            return
        self.notification_authorizing = authorizing
        if authorizing:
            self.notification_enable_timer.stop()
            self.notification_permission_text = "请在系统授权弹窗中允许 SeeEnd 通知。"
        else:
            self.notification_enable_timer.start()
            self.notification_permission_text = "读取授权中…"
        self.update_preference_help("desktop_notifications", self.notification_permission_text)
        self.update_notification_controls()

    def cancel_notification_enable(self):
        self.notification_enable_pending = None
        self.notification_authorizing = False
        self.notification_enable_timer.stop()

    def notification_enable_timed_out(self):
        if self.notification_enable_pending is not None:
            self.notification_enable_verified(self.notification_enable_pending, "unknown")

    def notification_enable_verified(self, request, status):
        if request != self.notification_enable_pending:
            return
        self.cancel_notification_enable()
        if not self.built or not self.ready or not self.model or self.reset_pending:
            return
        self.update_notification_status(status)
        if self.notification_channel_available("desktop_notifications"):
            self.save_setting("desktop_notifications", True)
            self.update_notification_controls()

    def update_notification_status(self, status):
        if self.notification_enable_pending is not None:
            return
        if self.notification_enable_pending is None:
            self.notification_authorizing = False
        self.notification_status = status
        texts = {"authorized": "", "available": "",
                 "denied": "系统通知未开启。请在系统设置 → 通知 → SeeEnd 中开启「允许通知」，再打开此开关。",
                 "not_determined": "系统通知未开启。请在系统设置 → 通知 → SeeEnd 中开启「允许通知」，再打开此开关。",
                 "unavailable": "系统通知不可用，macOS 请使用打包应用。",
                 "unknown": "无法读取通知权限，请在系统通知设置中检查 SeeEnd 的授权。"}
        self.notification_permission_text = texts.get(status, texts["unknown"])
        self.update_preference_help("desktop_notifications", self.notification_permission_text)
        self.update_notification_controls()

    def test_notification(self):
        if self.notification_testing or not self.notification_channel_available("desktop_notifications") or not self.ready or self.notification_enable_pending is not None or not self.checks["desktop_notifications"].isChecked():
            return
        self.notification_testing = True
        self.notification_test_button.setEnabled(False)
        self.notification_test_feedback.setText("正在提交测试通知…")
        self.notification_test_feedback.show()
        self.notification_test_requested.emit()

    def notification_test_finished(self, status):
        self.notification_testing = False
        messages = {"denied": "系统未允许通知，请打开通知设置后重试。",
                    "unavailable": "系统通知不可用，macOS 请使用打包应用。",
                    "failed": "提交失败，请重新检查授权后重试。"}
        self.notification_test_feedback.setText("" if status == "submitted" else messages.get(status, messages["failed"]))
        self.notification_test_feedback.setVisible(status != "submitted")
        if status == "submitted":
            self.tell("测试通知已提交，请在系统通知中查看。")
        self.request_notification_status()
