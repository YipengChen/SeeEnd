"""Native dashboard controller with bounded polling and stable controls."""

import csv
from html import escape
from collections import deque
from datetime import datetime
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import QBoxLayout, QFileDialog, QToolTip, QMessageBox, QColorDialog, QWidget as QtWidget
from aiflame.desktop.localization import QTableWidgetItem, QWidget, QPushButton

from .components import DeviceInstanceRow, Disclosure, icon, restyle
from .network import LocalAPI
from .interaction import DashboardInteraction
from .mijia import MijiaConnection
from .wechat import WechatConnection, RECEIPTS as WECHAT_RECEIPTS
from .theme import STYLE
from .responsive import ScreenAdaptation
from .task_return import TaskReturnActions
from .views import DashboardViews, PAGES, RECENT_ROW_HEIGHT, RECENT_ROW_GAP
from .localization import set_language, language, source_text, t
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODEL, YEELIGHT_MODELS, SUPPORTED_MODELS, MIJIA_PLUG_MODELS, YEELIGHT_CUBE_MODEL, hardware_model
from aiflame.core.delivery import REASONS as DELIVERY_REASONS
from aiflame.outputs.flame import DEMO_DURATION_SECONDS, demo_display

NAMES = {
    "idle": "当前无任务",
    "working": "任务进行中",
    "done": "任务已完成",
    "cancelled": "已取消",
    "unknown": "状态未知",
    "needs_input": "等待输入",
    "timeout": "已超时",
}
RESULTS = {
    "screen": "本地状态已更新",
    "queued": "等待发送",
    "applied": "设备已确认",
    "suppressed": "未触发灯光",
    "unmatched": "来源已停用",
    "expired": "事件已过期",
    "lease_expired": "工作状态超时",
    "dropped": "队列已淘汰",
    "ended_on_reconnect": "重连后不补播",
    "superseded": "已被后续状态替代",
    "interrupted": "进程重启，未重播",
}
def is_test(event):
    return bool(event.get("simulated") or event["source"] in ("manual", "demo"))


class Dashboard(DashboardInteraction, DashboardViews, MijiaConnection, WechatConnection, TaskReturnActions, QWidget):
    state_changed = Signal(object)
    return_failed = Signal()
    notification_status_requested = Signal()
    notification_test_requested = Signal()
    notification_enable_requested = Signal(int)

    def __init__(self, parent=None, port=8765):
        super().__init__(parent)
        self.setObjectName("dashboard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(14)
        self.setFont(font)
        self.setStyleSheet(STYLE)
        self.built = False
        self.model = None
        self.ready = False
        self.source_checks, self.source_values = {}, {}
        self.confirmed_source_values = {}
        self.setting_saves = {}
        self.notification_status = "unknown"
        self.notification_testing = False
        self.notification_authorizing = False
        self.notification_enable_pending = None
        self.notification_enable_serial = 0
        self.last_status_at = None
        self.history_key = None
        self.visible_events = []
        self.actions = deque()
        self.init_task_returns()
        self.writing = False
        self.active_action = None
        self.reset_pending = False
        self.generation = 0
        self.job_busy = False
        self.maintenance_buttons = []
        self.preview_phase = None
        self.preview_started = 0
        self.hardware_preview_id = None
        self.hardware_preview_starting = False
        self.notice_kind = None
        self.api = LocalAPI(self, port)
        self.api.error.connect(self.on_error)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(1500)
        self.poll_timer.timeout.connect(self.poll)
        self.pending_device_brightness = None
        self.brightness_timer = QTimer(self)
        self.brightness_timer.setSingleShot(True)
        self.brightness_timer.setInterval(300)
        self.brightness_timer.timeout.connect(self.flush_device_brightness)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(1000)
        self.preview_timer.timeout.connect(self.advance_preview)
        self.device_test_deadlines = {}
        self.device_test_timer = QTimer(self)
        self.device_test_timer.setInterval(250)
        self.device_test_timer.timeout.connect(self.update_device_test_caption)
        self.device_test_timer.start()
        self.message_timer = QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.setInterval(5000)
        self.message_timer.timeout.connect(lambda: self.statusbar.hide())
        self.notification_enable_timer = QTimer(self)
        self.notification_enable_timer.setSingleShot(True)
        self.notification_enable_timer.setInterval(12000)
        self.notification_enable_timer.timeout.connect(self.notification_enable_timed_out)
        self.recent_reflow_timer = QTimer(self)
        self.recent_reflow_timer.setSingleShot(True)
        self.recent_reflow_timer.timeout.connect(self.render_recent)
        self.message_error = False
        self.build_views()
        self.built = True
        self.adaptation = ScreenAdaptation(self)
        self.tabs.currentChanged.connect(self.tab_changed)
        self.update_busy()
        self.api.request("/api/session", self.connected)

    def tell(self, text, *, error=False, duration_ms=5000, retry=None):
        if not error and self.has_visible_save_failure():
            text, error = "部分设置未保存，请查看标记项并重试。", True
        self.retry_button.hide()
        self.message_retry = retry
        self.retry_button.setText("重新定位" if retry is not None else "重新连接")
        self.retry_button.setVisible(retry is not None)
        self.message_timer.stop()
        self.message_error = error
        self.message.setStyleSheet("color: #a0472c;" if error else "")
        self.message.setText(str(text))
        self.position_message()
        self.statusbar.show()
        self.statusbar.raise_()
        self.message_timer.start(duration_ms)

    def retry_message(self):
        retry = getattr(self, "message_retry", None)
        self.statusbar.hide()
        self.message_timer.stop()
        if retry is not None:
            retry()
        else:
            self.poll(force=True)

    def position_message(self):
        self.statusbar.setFixedWidth(max(240, min(640, self.main_surface.width() - 48)))
        self.statusbar.adjustSize()
        self.statusbar.move(max(16, self.main_surface.width() - self.statusbar.width() - 24),
                            max(16, self.main_surface.height() - self.statusbar.height() - 24))

    def on_error(self, text):
        self.tell(text, error=True)
        if "本地服务未连接" in str(text) or text == "需要有效凭据":
            self.cancel_notification_enable()
            self.ready = False
            self.device_badge.setText("本地服务未连接")
            self.device_connection_icon.hide()
            self.device_summary_icon.hide()
            self.device_status.setToolTip("")
            for mark in (self.device_connection_icon, self.device_summary_icon):
                mark.setProperty("connection", None)
                mark.setToolTip("")
            restyle(self.device_status, "tone", "warning")
            restyle(self.device_badge, "tone", "warning")
            self.retry_button.show()
            self.message_timer.stop()
            self.position_message()
            self.notice_kind = "service"
            self.notice_box.hide()
            self.render_scene()
        self.update_busy()

    def connected(self, _):
        self.ready = True
        self.retry_button.hide()
        if self.notice_kind == "service":
            self.notice_box.hide()
            self.notice_kind = None
            self.message_error = False
            self.statusbar.hide()
        self.poll(force=True)

    def act(self, path, body, callback=None, failed=None):
        self.message_timer.stop()
        if not self.has_visible_save_failure():
            self.message_error = False
            self.statusbar.hide()
        self.generation += 1
        self.actions.append((path, body, callback, failed))
        if not self.writing:
            self.next_action()
        else:
            self.update_busy()

    def next_action(self):
        if not self.actions:
            self.writing = False
            self.active_action = None
            self.update_busy()
            self.poll(force=True)
            return
        path, body, callback, failed = self.actions.popleft()
        self.active_action = (path, body)
        self.writing = True
        self.update_busy()
        outgoing = body
        channel_save = path == "/api/settings" and body and set(body).issubset(self.home_channel_checks)
        if path == "/api/settings" and "source_enabled" in body:
            outgoing = {**body, "source_enabled": {**self.confirmed_source_values, **body["source_enabled"]}}

        def received(data):
            if path == "/api/settings" and "source_enabled" in body:
                self.confirmed_source_values = dict(data.get("settings", {}).get("source_enabled", outgoing["source_enabled"]))
            if path in ("/api/settings", "/api/device/light-settings"):
                if not channel_save and self.has_visible_save_failure():
                    self.tell("部分设置未保存，请查看标记项并重试。", error=True)
            elif path not in ("/api/preview", "/api/device/test-light"):
                self.tell(data.get("message", "操作已接收，正在同步状态。"))
            if callback:
                callback(data)
            if (path == "/api/device/light-settings" and body.get("device_id") == self.selected_device_id
                    and getattr(self, "state_preview_device", None) == self.selected_device_id):
                panel = self.plug_previews if self.selected_device_model in MIJIA_PLUG_MODELS else self.light_previews
                self.preview_state_on_device(panel.selected_state)
            self.next_action()

        def rejected():
            if failed:
                failed()
            self.next_action()

        options = {}
        if channel_save:
            def channel_error(text):
                for key in body:
                    record = self.setting_saves.get(key)
                    if record and record["state"] == "saving":
                        record["error"] = str(text)
                if "本地服务未连接" in str(text) or text == "需要有效凭据":
                    self.on_error(text)
            options["error_callback"] = channel_error
        self.api.request(path, received, outgoing, failed=rejected, **options)

    def reset_app(self):
        if self.reset_pending or not self.reset_button.isEnabled():
            return
        self.reset_pending = True
        self.cancel_notification_enable()
        self.update_busy()
        dialog = QMessageBox(QMessageBox.Icon.Warning, t("确定重置 APP 吗？"),
                             t("确定重置 APP 吗？"),
                             QMessageBox.StandardButton.NoButton, self)
        dialog.setObjectName("confirmation")
        dialog.setInformativeText(t("将清除 SeeEnd 的活动记录、设备配对与凭据、微信绑定、提醒偏好和本机接入数据，且无法撤销。\n已安装在其他应用中的 Hook、插件和浏览器扩展保留，不会修改设备固件。重置后请通过接入引导或 SeeEnd Skill 重新配置。"))
        reset = dialog.addButton(t("重置 APP"), QMessageBox.ButtonRole.DestructiveRole)
        reset.setProperty("variant", "danger")
        cancel = dialog.addButton(t("取消"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        dialog.exec()
        if dialog.clickedButton() is not reset:
            self.reset_pending = False
            self.update_busy()
            return

        def failed():
            self.reset_pending = False
            self.update_busy()

        self.act("/api/reset", {"confirm": True}, self.reset_finished, failed)

    def reset_finished(self, data):
        self.reset_mijia_draft()
        # The response has already refreshed the worker's authenticated cookie.
        # Invalidate old dashboard snapshots and discard local drafts. Keep the
        # shared API consumers' callbacks so their polling state can complete.
        self.generation += 1
        self.actions.clear()
        self.brightness_timer.stop()
        self.pending_device_brightness = None
        self.hardware_preview_id = None
        self.stop_preview()
        self.job_busy = False
        self.reset_pending = False
        self.device_test_deadlines.clear()
        self.selected_device_id = None
        self.selected_device_model = DEFAULT_MODEL
        self.selected_device_state = {"online": False}
        self.selected_device_brightness = 50
        self.device_selection_loading = self.port_restore_pending = False
        self.device_operation_id = self.rendered_yeelight_discovery = None
        self.connection_paused = False
        self.source_values.clear()
        self.setting_saves.clear()
        self.source_category.setCurrentIndex(self.source_category.findData("web"))
        for edit in [self.yeelight_host, self.history_search]:
            with QSignalBlocker(edit):
                edit.clear()
        for choices in [self.yeelight_choices, self.device_instances]:
            with QSignalBlocker(choices):
                choices.clear()
        with QSignalBlocker(self.device_instances):
            self.device_instances.addItem("添加此型号设备…", "")
        self.device_test_caption.hide()
        self.device_version.setText("连接后读取固件版本")
        with QSignalBlocker(self.history_filter):
            self.history_filter.setCurrentIndex(0)
        for message in [self.skill_notice, self.browser_message,
                        self.notification_test_feedback, self.job_message]:
            message.clear()
            message.hide()
        self.progress.hide()
        for disclosure in self.findChildren(Disclosure):
            disclosure.button.setChecked(False)
        self.history_key = None
        self.visible_events = []
        self.ready = True
        self.return_requests.clear()
        self.return_timer.stop()
        self.open_device_details(DEFAULT_MODEL)
        self.show_device_selector()
        self.render(data)
        self.navigate(0)
        self.tell(data.get("message", "APP 已重置，请重新配置应用接入。"), error=bool(data.get("token_cleanup_pending")))

    def setting(self, key, value):
        if key == "desktop_notifications":
            if value:
                self.begin_notification_enable()
                return
            self.cancel_notification_enable()
        if key == "wechat_notifications" and value and (not self.ready or not self.notification_channel_available(key)):
            self.update_notification_controls()
            self.update_wechat_controls()
            return
        if self.model:
            if key == "hardware_notifications":
                self.stop_preview()
            self.save_setting(key, value)

    def device_reminders_enabled(self):
        return bool(self.model and self.model["settings"].get("hardware_notifications"))

    def flush_device_brightness(self):
        self.brightness_timer.stop()
        pending, self.pending_device_brightness = self.pending_device_brightness, None
        if pending:
            self.act("/api/device/light-settings", pending)

    def yeelight_brightness_changed(self, state, value):
        with QSignalBlocker(self.yeelight_brightness_labels[state]):
            self.yeelight_brightness_labels[state].setValue(value)
        field = "front_state_brightness" if self.yeelight_front_mode() else "state_brightness"
        self.queue_device_light_value(field, state, value)

    def yeelight_front_mode(self):
        return self.selected_device_model in YEELIGHT_MODELS and self.yeelight_reminder_mode.currentData() == "front"

    def yeelight_temperature_changed(self, state, value):
        value = ((value + 50) // 100) * 100
        control = self.yeelight_temperature_controls[state]
        if control.value() != value:
            with QSignalBlocker(control):
                control.setValue(value)
        self.render_yeelight_temperature(state, value)
        if self.yeelight_front_mode():
            self.queue_device_light_value("front_state_temperature", state, value)

    def render_yeelight_temperature(self, state, value):
        with QSignalBlocker(self.yeelight_temperature_labels[state]):
            self.yeelight_temperature_labels[state].setValue(value)
        self.yeelight_temperature_swatches[state].set_temperature(value)

    def queue_device_light_value(self, field, state, value):
        self.render_light_previews()
        if self.ready and self.model and self.selected_device_id and not self.device_selection_loading:
            pending = self.pending_device_brightness or {}
            settings = dict(pending.get("settings", {}))
            values = dict(settings.get(field, {}))
            values[state] = value
            settings[field] = values
            self.pending_device_brightness = {"hardware_model": self.selected_device_model,
                                             "device_id": self.selected_device_id,
                                             "settings": settings}
            self.brightness_timer.start()

    def yeelight_mode_changed(self):
        self.render_yeelight_mode()
        self.device_light_setting("reminder_mode", self.yeelight_reminder_mode.currentData())

    def yeelight_flash_changed(self, field, value):
        if field != "enabled":
            from aiflame.devices.light_settings import FLASH_MIN_CYCLE_MS
            value = ((value + 50) // 100) * 100
            other = "off_ms" if field == "on_ms" else "on_ms"
            value = max(value, FLASH_MIN_CYCLE_MS - self.yeelight_flash_controls[other].value())
            control = self.yeelight_flash_controls[field]
            if control.value() != value:
                with QSignalBlocker(control):
                    control.setValue(value)
            with QSignalBlocker(self.yeelight_flash_labels[field]):
                self.yeelight_flash_labels[field].setValue(value / 1000)
        self.render_yeelight_flash_cycle()
        self.queue_device_light_value("front_flash", field, value)
        self.update_busy()

    def render_yeelight_flash_cycle(self):
        cycle = sum(control.value() for control in self.yeelight_flash_controls.values())
        on, off = (self.yeelight_flash_controls[key].value() / 1000 for key in ("on_ms", "off_ms"))
        self.yeelight_flash_cycle_label.setText(t("亮 {on:.1f} 秒，灭 {off:.1f} 秒，每 {cycle:.1f} 秒循环。").format(on=on, off=off, cycle=cycle / 1000))

    def render_yeelight_mode(self):
        if self.selected_device_model in MIJIA_PLUG_MODELS:
            self.render_light_previews()
            return
        from aiflame.devices.cube_effects import CUBE_EFFECTS
        cube = self.selected_device_model == YEELIGHT_CUBE_MODEL
        expected = 2 + len(CUBE_EFFECTS) if cube else 2
        if self.yeelight_reminder_mode.count() != expected:
            selected = self.yeelight_reminder_mode.currentData()
            with QSignalBlocker(self.yeelight_reminder_mode):
                self.yeelight_reminder_mode.clear()
                self.yeelight_reminder_mode.columns = 3 if cube else 2
                for title, value in (("彩灯", "back"), ("白灯", "front")):
                    self.yeelight_reminder_mode.addItem(title, value)
                if cube:
                    for value, title, _ in CUBE_EFFECTS:
                        self.yeelight_reminder_mode.addItem(title, value)
                self.yeelight_reminder_mode.setCurrentIndex(max(0, self.yeelight_reminder_mode.findData(selected)))
        front = self.yeelight_front_mode()
        effect = next((item for item in CUBE_EFFECTS if item[0] == self.yeelight_reminder_mode.currentData()), None)
        self.cube_effect_hint.setVisible(cube and effect is not None)
        self.cube_effect_hint.setText(effect[2] if cube and effect else "")
        self.yeelight_mode_panel.setVisible(hardware_model(self.selected_device_model).get("white_alerts", True))
        self.yeelight_flash_panel.setVisible(front and self.light_previews.selected_state == "needs_input")
        lamp = self.selected_device_model == YEELIGHT_MODEL
        with QSignalBlocker(self.yeelight_reminder_mode):
            self.yeelight_reminder_mode.setItemText(0, "背部彩灯" if lamp else "彩灯")
            self.yeelight_reminder_mode.setItemText(1, "前部白灯" if lamp else "白灯")
        self.yeelight_reminder_mode.setAccessibleName(hardware_model(self.selected_device_model)["card_description"].split(" · ")[0] + " 提醒灯模式")
        self.yeelight_light_title.setText("灯效设置")
        self.yeelight_light_description.setText("选择灯效，再点击状态预览调整对应参数。修改自动保存。")
        for state, title in (("working", "任务进行中"), ("done", "任务已完成"), ("needs_input", "等待输入")):
            with QSignalBlocker(self.yeelight_temperature_controls[state]):
                self.yeelight_temperature_controls[state].setMinimum(hardware_model(self.selected_device_model)["white_temperature_min"])
            with QSignalBlocker(self.yeelight_temperature_labels[state]):
                self.yeelight_temperature_labels[state].setMinimum(hardware_model(self.selected_device_model)["white_temperature_min"])
            self.yeelight_temperature_controls[state].setToolTip(
                f"{hardware_model(self.selected_device_model)['white_temperature_min']}K 暖光 · 6500K 冷光")
            self.yeelight_state_titles[state].setText(title)
            for item in (self.yeelight_color_labels[state], self.yeelight_color_choices[state], self.yeelight_color_buttons[state]):
                item.setVisible(not front)
            for item in (self.yeelight_temperature_titles[state], self.yeelight_temperature_fields[state],
                         self.yeelight_temperature_controls[state], self.yeelight_temperature_labels[state]):
                item.setVisible(front)

        self.render_light_previews()

    def show_light_state_parameters(self, _state=None):
        for state, panel in self.yeelight_state_sections.items():
            panel.setVisible(state == self.light_previews.selected_state)
        for state, panel in self.plug_state_sections.items():
            panel.setVisible(state == self.plug_previews.selected_state)
        self.yeelight_flash_panel.setVisible(
            self.yeelight_front_mode() and self.light_previews.selected_state == "needs_input")
        self.preview_state_on_device(_state)

    def stop_state_device_preview(self):
        device_id = getattr(self, "state_preview_device", None)
        self.state_preview_device = None
        if device_id and self.ready:
            self.actions = deque(action for action in self.actions if not (
                action[0] == "/api/device/test-light" and action[1].get("device_id") == device_id
                and action[1].get("state") in ("working", "needs_input", "done")))
            self.act("/api/device/test-light", {"device_id": device_id, "state": "stop"},
                     lambda _: self.device_test_deadlines.pop(device_id, None))
            self.device_test_deadlines.pop(device_id, None)

    def preview_state_on_device(self, state):
        if not state:
            self.stop_state_device_preview()
            return
        if not self.connected_light_settings():
            return
        if self.has_unfinished_tasks():
            self.tell("有任务尚未结束，暂时无法演示灯效。", error=True)
            return
        self.state_preview_device = self.selected_device_id
        # Keep only the latest queued choice; physical animation uses the existing worker.
        self.actions = deque(action for action in self.actions if not (
            action[0] == "/api/device/test-light" and action[1].get("state") in ("working", "needs_input", "done")))
        self.act("/api/device/test-light", {"device_id": self.selected_device_id, "state": state},
                 self.device_test_started)

    def render_light_previews(self):
        if not hasattr(self, "light_previews") or not hasattr(self, "yeelight_flash_controls"):
            return
        from aiflame.devices.light_settings import light_settings
        model = self.selected_device_model
        identity = (self.selected_device_id, model)
        if getattr(self, "light_parameter_device", None) != identity:
            self.light_parameter_device = identity
            self.light_previews.select_state(None)
            self.plug_previews.select_state(None)
        if model not in SUPPORTED_MODELS:
            return
        saved = getattr(self, "preview_saved_settings", (None, None, {}))
        settings = light_settings({"hardware_model": model, "light_settings":
            saved[2] if saved[:2] == (self.selected_device_id, model) else {}})
        if model in MIJIA_PLUG_MODELS:
            settings["state_power"] = {state: choice.currentData() for state, choice in self.plug_power_choices.items()}
            self.plug_previews.configure(model, settings)
        else:
            mode = self.yeelight_reminder_mode.currentData()
            settings["reminder_mode"] = mode
            settings["colors"] = {state: choice.currentData() for state, choice in self.yeelight_color_choices.items()}
            field = "front_state_brightness" if mode == "front" else "state_brightness"
            settings[field] = {state: control.value() for state, control in self.yeelight_brightness_controls.items()}
            settings["front_state_temperature"] = {state: control.value() for state, control in self.yeelight_temperature_controls.items()}
            settings["front_flash"] = {"enabled": self.yeelight_flash_enabled.isChecked(),
                                       **{key: control.value() for key, control in self.yeelight_flash_controls.items()}}
            self.light_previews.configure(model, settings)

    def device_light_setting(self, key, value):
        self.device_light_settings({key: value})

    def device_light_settings(self, settings):
        self.render_light_previews()
        if self.ready and self.model and self.selected_device_id and not self.device_selection_loading:
            self.flush_device_brightness()
            self.act("/api/device/light-settings", {"hardware_model": self.selected_device_model,
                     "device_id": self.selected_device_id, "settings": settings})

    def yeelight_color_changed(self, state):
        choice = self.yeelight_color_choices[state]
        if choice.currentData():
            self.device_light_setting("colors", {state: choice.currentData()})

    def choose_yeelight_color(self, state):
        choice = self.yeelight_color_choices[state]
        device_id, model_id = self.selected_device_id, self.selected_device_model
        color = QColorDialog.getColor(QColor(choice.currentData()), self, t("选择提醒颜色"))
        if color.isValid() and (device_id, model_id) == (self.selected_device_id, self.selected_device_model):
            value = color.name().upper()
            index = choice.findData(value)
            if index < 0:
                choice.addItem("自定义 " + value, value)
                index = choice.findData(value)
            choice.setCurrentIndex(index)

    def reset_light_state(self, state):
        from aiflame.devices.light_settings import (COLOR_DEFAULTS, STATE_BRIGHTNESS_DEFAULTS,
            front_brightness_defaults, FRONT_TEMPERATURE_DEFAULTS, FRONT_FLASH_DEFAULTS, PLUG_DEFAULTS)
        if self.selected_device_model in MIJIA_PLUG_MODELS:
            settings = {"state_power": {state: PLUG_DEFAULTS["state_power"][state]}}
        elif self.yeelight_front_mode():
            settings = {"front_state_temperature": {state: FRONT_TEMPERATURE_DEFAULTS[state]},
                        "front_state_brightness": {state: front_brightness_defaults(self.selected_device_model)[state]}}
            if state == "needs_input":
                settings["front_flash"] = dict(FRONT_FLASH_DEFAULTS)
        else:
            settings = {"colors": {state: COLOR_DEFAULTS[state]},
                        "state_brightness": {state: STATE_BRIGHTNESS_DEFAULTS[state]}}
        self.device_light_settings(settings)

    def brightness_off(self):
        return bool(self.model and self.model.get("brightness_off", self.model["settings"]["brightness"] == 0))

    def source_changed(self, source, value):
        self.source_values[source] = value
        if self.model:
            self.save_setting("source:" + source, value)

    def adjust_duration(self, step):
        self.duration.stepBy(step)
        self.duration.editingFinished.emit()

    def update_duration_steps(self):
        self.duration_decrease.setEnabled(self.duration.value() > self.duration.minimum())
        self.duration_increase.setEnabled(self.duration.value() < self.duration.maximum())
        if "duration" in getattr(self, "preference_help", {}):
            duration = self.duration.value()
            hold = bool(self.model and self.model["settings"]["hold_until_activity"])
            description = t("检测到键鼠操作后，完成提醒再持续 {duration} 秒，随后恢复当前任务状态。" if hold
                            else "完成或等待输入提醒持续 {duration} 秒，随后恢复当前任务状态。").format(duration=duration)
            self.update_preference_help("duration", description, "等待输入提醒立即计时。" if hold else "")

    def confirm_delete_current_device(self):
        if self.device_selection_loading or not self.selected_device_id:
            return
        device_id, model_id = self.selected_device_id, self.selected_device_model
        name = self.detail_device().get("name", hardware_model(model_id)["name"])
        dialog = QMessageBox(QMessageBox.Icon.Warning, t("删除此设备？"), t("删除此设备？"),
                             QMessageBox.StandardButton.NoButton, self)
        dialog.setObjectName("confirmation")
        dialog.setInformativeText(t("将移除设备配对和此设备的提醒设置。需要再次使用时，可重新添加。") + "\n" + name)
        remove = dialog.addButton(t("删除此设备"), QMessageBox.ButtonRole.DestructiveRole)
        remove.setProperty("variant", "danger")
        cancel = dialog.addButton(t("取消"), QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        def finished(_):
            if dialog.clickedButton() is remove:
                self.delete_current_device(device_id=device_id, model_id=model_id)
            dialog.deleteLater()
        dialog.finished.connect(finished)
        self.delete_confirmation = dialog
        dialog.open()

    def delete_current_device(self, *, device_id=None, model_id=None):
        device_id, model_id = device_id or self.selected_device_id, model_id or self.selected_device_model
        if not device_id or model_id not in SUPPORTED_MODELS or not self.ready:
            return
        self.flush_device_brightness()

        def received(data):
            self.device_test_deadlines.pop(device_id, None)
            if (self.selected_device_id, self.selected_device_model) == (device_id, model_id):
                self.generation += 1
                self.device_operation_id = None
                self.port_restore_pending = False
                self.yeelight_host.clear()
                self.render_device(data)
            self.tell(data["message"])
            self.poll(force=True)

        self.act("/api/device/delete", {"hardware_model": model_id, "device_id": device_id}, received)

    def test_current_device(self):
        if self.device_selection_loading or not self.selected_device_id or not self.detail_device().get("online"):
            self.tell("请先连接当前设备", error=True)
            return
        reason = ("有任务尚未结束，暂时无法演示灯效。" if self.has_unfinished_tasks() else
                  "此设备亮度为 0%，请先提高灯光亮度。" if self.selected_device_brightness == 0 else self.light_block_reason())
        if reason:
            self.tell(reason, error=True)
            return
        self.act("/api/device/test-light", {"device_id": self.selected_device_id},
                 self.device_test_started)

    def device_test_started(self, data):
        self.device_test_deadlines[data["device_id"]] = (None if data.get("continuous") else
            time.monotonic() + data.get("remaining_ms", DEMO_DURATION_SECONDS * 1000) / 1000)
        self.update_device_test_caption()

    def update_device_test_caption(self):
        deadline = self.device_test_deadlines.get(self.selected_device_id, 0)
        continuous = deadline is None
        remaining = (deadline or 0) - time.monotonic()
        visible = (continuous or remaining > 0) and not self.device_selection_loading
        self.device_test_caption.setVisible(visible)
        caption = ""
        if visible:
            if continuous:
                caption = "正在预览所选状态"
            else:
                frame = demo_display("device-caption", max(0, DEMO_DURATION_SECONDS - remaining))
                state = {"working": "工作中", "needs_input": "待输入", "done": "已完成"}[frame["state"]]
                caption = f"试亮剩余 {max(1, int(remaining + 0.999))} 秒，当前状态为：{state}"
        self.device_test_caption.setText(caption)

    def navigate(self, index):
        if index != self.tabs.currentIndex() and self.retry_button.isHidden():
            self.statusbar.hide()
            self.message_timer.stop()
        if index != 1:
            self.stop_state_device_preview()
            self.cancel_mijia_login()
        self.device_view_epoch += 1
        if index == 1:
            self.show_device_selector()
        self.tabs.setCurrentIndex(index)

    def show_device_selector(self):
        self.stop_state_device_preview()
        self.cancel_mijia_login()
        self.device_view_epoch += 1
        self.device_details.hide()
        self.device_selector.show()
        self.adding_device = False
        self.render_device_list()
        self.breadcrumb.setText("我的设备")

    def show_device_catalog(self):
        self.adding_device = True
        self.device_list_panel.hide()
        self.device_catalog_panel.show()
        self.device_catalog_back.setVisible(bool(self.model and self.model.get("devices")))

    def render_device_list(self):
        devices = [item for item in (self.model or {}).get("devices", []) if item.get("hardware_model") in SUPPORTED_MODELS]
        identities = {item["device_id"] for item in devices}
        for identity in list(self.device_rows):
            if identity not in identities:
                button = self.device_rows.pop(identity)
                self.device_list_layout.removeWidget(button)
                button.hide()
                button.deleteLater()
        for device in devices:
            identity = device["device_id"]
            if identity not in self.device_rows:
                button = DeviceInstanceRow(hardware_model(device["hardware_model"]))
                button.clicked.connect(lambda checked=False, i=identity: self.choose_registered_device(i))
                self.device_list_layout.addWidget(button)
                self.device_rows[identity] = button
            self.device_rows[identity].update_device(device)
        self.device_list_panel.setVisible(bool(devices) and not self.adding_device)
        self.device_catalog_panel.setVisible(not devices or self.adding_device)
        self.device_catalog_back.setVisible(bool(devices))

    def choose_registered_device(self, device_id):
        device = next((item for item in (self.model or {}).get("devices", []) if item["device_id"] == device_id), None)
        if not device or not self.ready:
            return
        self.flush_device_brightness()
        self.reset_mijia_draft()
        self.generation += 1
        self.selected_device_id = device_id
        self.selected_device_state = device
        self.device_selection_loading = self.port_restore_pending = True
        self.device_operation_id = None
        self.yeelight_host.clear()
        self.open_device_details(device["hardware_model"])
        with QSignalBlocker(self.device_instances):
            self.device_instances.clear()
            self.device_instances.addItem("正在读取设备…", device_id)
        self.render_selected_device_status()
        self.update_busy()
        self.poll(force=True)

    def open_device_details(self, model_id):
        model = hardware_model(model_id)
        self.selected_device_model = model_id
        self.device_model_title.setText(model["name"])
        self.device_model_description.setText(model["card_description"])
        self.yeelight_card.show()
        plug = model_id in MIJIA_PLUG_MODELS
        self.yeelight_history_heading.setVisible(not plug)
        self.device_instance_panel.setVisible(not plug or self.device_instances.count() > 1 or bool(self.selected_device_id))
        self.yeelight_colors_panel.setVisible(not plug)
        self.plug_settings_panel.setVisible(plug)
        self.mijia_panel.setVisible(plug and not self.selected_device_id)
        self.device_light_card.setVisible(self.connected_light_settings())
        self.mijia_connected_identity.setVisible(plug and bool(self.selected_device_id))
        self.connection_title.setText("插座使用说明" if plug else "连接准备")
        identity = (model_id, self.selected_device_id)
        if getattr(self, "connection_prepare_identity", None) != identity:
            self.connection_prepare_disclosure.button.setChecked(not self.selected_device_id)
            self.device_information.button.setChecked(False)
            self.connection_prepare_identity = identity
        self.connection_prepare_hint.setText("此电脑与插座需在同一局域网。灯具应保持自身开关开启，并支持通电自动亮起。" if plug else
            "在 Yeelight APP 中配置灯具，接入2.4 GHz WiFi，开启「局域网控制」。此电脑需与灯具在同一局域网内。")
        self.yeelight_connect_button.setVisible(not plug)
        self.yeelight_host.setPlaceholderText("插座 IP（可留空自动查找）" if plug else "灯具 IP，例如 192.168.1.100")
        self.yeelight_host.setAccessibleName("米家插座 IP 地址" if plug else "Yeelight 灯具 IP 地址")
        self.yeelight_choices.setAccessibleName("扫描到的米家设备" if plug else "搜索到的 Yeelight 灯具")
        self.discovery_hint.setText("扫描可发现设备 IP；Token 需要下方扫码获取。" if plug else
                                    "先搜索局域网中的灯具；未找到时，可以手动输入 IP 地址。")
        self.device_status_card.setVisible(bool(self.selected_device_id))
        self.yeelight_add_panel.setVisible(not self.selected_device_id and not plug)
        self.yeelight_reconnect_button.setVisible(bool(self.selected_device_id))
        self.yeelight_delete_button.setVisible(bool(self.selected_device_id))
        self.yeelight_discover_button.setText("搜索 " + model["name"].removeprefix("Yeelight "))
        self.render_yeelight_mode()
        if self.tabs.currentIndex() == 1:
            self.breadcrumb.setText("我的设备")
        self.device_selector.hide()
        self.device_details.show()
        self.render_selected_device_status()

    def choose_device_model(self, model_id):
        if not self.ready or not self.model:
            return
        self.flush_device_brightness()
        self.reset_mijia_draft()
        self.generation += 1
        self.selected_device_id = "" if self.adding_device else None
        self.port_restore_pending = True
        self.device_selection_loading = True
        self.device_operation_id = None
        self.connection_paused = False
        self.update_busy()
        self.selected_device_state = {"online": False}
        self.selected_device_brightness = 50
        self.open_device_details(model_id)
        with QSignalBlocker(self.device_instances):
            self.device_instances.clear()
            self.device_instances.addItem("正在读取设备…", "")
        self.yeelight_host.clear()
        self.yeelight_choices.clear()
        self.yeelight_choices.addItem("扫描后选择设备…" if model_id in MIJIA_PLUG_MODELS else "搜索后选择灯具…", "")
        self.rendered_yeelight_discovery = None
        self.poll(force=True)

    def choose_device_instance(self, _index):
        self.reset_mijia_draft()
        self.flush_device_brightness()
        self.generation += 1
        self.selected_device_id = self.device_instances.currentData() or ""
        self.port_restore_pending = True
        self.device_selection_loading = True
        self.device_operation_id = None
        self.connection_paused = False
        self.update_busy()
        self.selected_device_state = {"online": False}
        self.render_selected_device_status()
        self.selected_device_brightness = 50
        self.yeelight_host.clear()
        self.yeelight_choices.clear()
        self.yeelight_choices.addItem("搜索后选择灯具…", "")
        self.rendered_yeelight_discovery = None
        self.poll(force=True)

    def render_selected_device_status(self):
        selected = self.detail_device()
        loading = self.device_selection_loading
        online = bool(selected.get("online")) and not loading
        title = ("正在读取设备…" if loading else "设备已连接" if online else
                 "正在连接设备…" if selected.get("connecting") else
                 "设备已断开" if selected.get("connection_paused") else
                 "设备离线" if self.selected_device_id else "尚未添加设备")
        self.device_summary.setText(title)
        restyle(self.device_summary, "tone", "success" if online else "neutral")
        self.device_summary_icon.setVisible(online)
        if online:
            self.device_summary_icon.setPixmap(icon("wifi", "#a34235", 18).pixmap(18, 18))
        self.device_connection_hint.setText("" if loading or online else selected.get("last_error", ""))
        self.device_connection_hint.setVisible(bool(self.device_connection_hint.text()))
        self.device_version.setText("固件：" + str(selected.get("firmware_version") or "未上报")
                                    + " · 协议：" + str(selected.get("protocol_version") or "未上报"))
        self.device_identity.setText("设备 ID：" + (self.selected_device_id or "未提供")
                                    + "\nIP：" + (selected.get("port") or self.yeelight_host.text() or "未提供"))
        information_available = bool(self.selected_device_id) and not loading
        self.device_information.button.setVisible(information_available)
        self.device_information.setVisible(information_available and self.device_information.button.isChecked())

    def clear_history_filters(self):
        with QSignalBlocker(self.history_search), QSignalBlocker(self.history_filter):
            self.history_search.clear()
            self.history_filter.setCurrentIndex(self.history_filter.findData("all"))
        self.render_history()

    def device_query(self):
        values = {"model": self.selected_device_model}
        if self.selected_device_id is not None:
            values["device_id"] = self.selected_device_id
        return "/api/device?" + urlencode(values)

    def detail_device(self):
        if self.selected_device_id == "":
            return self.selected_device_state
        if self.model and self.model.get("devices"):
            return next((item for item in self.model["devices"] if item["device_id"] == self.selected_device_id), self.selected_device_state)
        if self.selected_device_id and self.selected_device_state.get("device_id") == self.selected_device_id:
            return self.selected_device_state
        return self.model["device"] if self.model else {}

    def home_action(self):
        self.navigate(2)

    def reflow_layout(self):
        if not self.built:
            return
        self.sidebar.setFixedWidth(250 if language() == "en" else 198)
        page = self.tabs.widget(0)
        margins = page.widget().layout().contentsMargins()
        available = page.viewport().width() - margins.left() - margins.right()
        compact = available < 780
        self.scene_frame.setMaximumHeight(500 if compact else 16777215)
        self.home_body_frame.setMaximumHeight(16777215 if compact else 820)
        self.home_top.setDirection(QBoxLayout.Direction.TopToBottom if compact
                                  else QBoxLayout.Direction.LeftToRight)
        self.position_message()
        self.reflow_device_controls()
        self.reflow_home_channels()
        QTimer.singleShot(0, self, self.reflow_home_sources)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.built:
            self.reflow_layout()

    def light_block_reason(self, source="manual"):
        if not self.ready or not self.model:
            return "本地服务尚未连接，请稍后重试。"
        if self.job_busy or self.model["maintenance"]:
            return "设备正在维护，请等待完成。"
        settings = self.model["settings"]
        if self.brightness_off():
            return "亮度为 0%，请先提高灯光亮度。"
        if source is not None and not settings["source_enabled"].get(source, False):
            return "该来源已关闭，请在「应用接入」中开启后重试。"
        return ""

    def start_preview(self):
        QToolTip.hideText()
        if self.preview_phase or self.hardware_preview_id:
            self.stop_preview()
            return
        if self.preview_block_reason():
            self.update_busy()
            return
        if self.preview_uses_devices():
            identifier = str(uuid.uuid4())
            self.hardware_preview_id = identifier
            self.hardware_preview_starting = True
            self.preview_button.setText("退出演示")
            self.preview_caption.setText("正在同步设备…")
            self.preview_caption.show()

            def received(data):
                if self.hardware_preview_id == identifier:
                    self.hardware_preview_starting = False
                    self.render({**self.model, **data})

            def failed():
                if self.hardware_preview_id == identifier:
                    self.hardware_preview_id = None
                    self.hardware_preview_starting = False
                    self.render_scene()

            self.act("/api/preview", {"action": "start", "id": identifier}, received, failed)
            return
        self.preview_started = time.monotonic()
        self.preview_phase = "working"
        self.preview_button.setText("退出演示")
        self.preview_timer.start()
        self.advance_preview()

    def advance_preview(self):
        elapsed = time.monotonic() - self.preview_started
        display = demo_display("screen-preview", elapsed)
        if display is None:
            self.stop_preview()
            return
        self.preview_phase = display["state"]
        self.preview_caption.show()
        self.preview_caption.setText(
            f"{max(1, (display['remaining_ms'] + 999) // 1000)} 秒后结束"
        )
        self.render_scene()

    def stop_preview(self):
        self.preview_timer.stop()
        self.preview_phase = None
        identifier = self.hardware_preview_id
        self.hardware_preview_id = None
        self.hardware_preview_starting = False
        self.poll_timer.setInterval(1500)
        self.preview_button.setText("灯效演示")
        self.preview_caption.clear()
        self.preview_caption.hide()
        if identifier:
            self.act(
                "/api/preview", {"action": "stop", "id": identifier},
                lambda data: self.render({**self.model, **data}),
            )
        self.render_scene()

    def render_scene(self):
        reduced = bool(self.model and self.model["settings"]["reduced"])
        self.display_hint.clear()
        self.display_hint.hide()
        if self.preview_phase:
            self.flame.set_state(self.preview_phase, True, reduced=reduced)
            self.display_title.setText(NAMES[self.preview_phase])
            return
        if not self.model or not self.ready:
            self.poll_timer.setInterval(1500)
            self.preview_button.setText("灯效演示")
            self.preview_caption.hide()
            self.flame.set_state("idle", reduced=reduced)
            self.display_title.setText("任务状态暂不可用" if not self.ready else "正在读取任务状态…")
            if self.model and self.last_status_at:
                self.display_hint.setText("最近更新时间：" + self.last_status_at.strftime("%H:%M:%S") + " · " + NAMES.get(self.displayed_task_state(), "状态未知"))
                self.display_hint.show()
            return
        data = self.model
        state = data["display"]["state"]
        preview = data["display"].get("preview")
        if preview and self.hardware_preview_id == preview:
            self.poll_timer.setInterval(150)
            self.preview_button.setText("退出演示")
            self.preview_caption.setText(
                f"{max(1, (data['display']['remaining_ms'] + 999) // 1000)} 秒后结束"
            )
            self.preview_caption.show()
            self.flame.set_state(state, True, reduced=reduced)
            self.display_title.setText(NAMES[state])
            return
        if not self.hardware_preview_starting:
            self.hardware_preview_id = None
            self.poll_timer.setInterval(150 if data["device"].get("connecting") else 1500)
            self.preview_button.setText("灯效演示")
            self.preview_caption.hide()
        self.flame.set_state(state, reduced=reduced, live=True)
        if self.brightness_off():
            title = "已熄灭"
        else:
            title = NAMES.get(self.displayed_task_state(), "当前无任务")
        self.display_title.setText(title)

    def render_notice(self):
        data = self.model
        if data["maintenance"]:
            kind, text, action = (
                "maintenance",
                "设备连接操作进行中，请稍候。",
                "查看进度",
            )
        elif self.brightness_off():
            kind, text, action = (
                "brightness",
                "灯光亮度为 0%，请在「我的设备」中调整；事件仍会记录。",
                "调节亮度",
            )
        else:
            self.notice_kind = None
            self.notice_box.hide()
            return
        self.notice_kind = kind
        self.notice_text.setText(text)
        self.notice_action.setText(action)
        self.notice_box.show()

    def resolve_notice(self):
        if self.notice_kind == "brightness":
            if self.device_reminders_enabled():
                self.navigate(1)
            else:
                self.setting("brightness", 20)
        elif self.notice_kind == "task":
            self.navigate(4)
        elif self.notice_kind == "service":
            self.poll(force=True)
        else:
            self.navigate(1)

    def render(self, data):
        self.last_status_at = datetime.now()
        chosen_language = data["settings"].get("language", "zh-CN")
        if chosen_language != language():
            set_language(chosen_language)
            self.history_key = None
        self.reflow_layout()
        with QSignalBlocker(self.language_choice):
            self.language_choice.setCurrentIndex(self.language_choice.findData(chosen_language))
        for item in data.get("devices", []):
            self.device_test_deadlines[item["device_id"]] = (None if item.get("test_continuous") else
                time.monotonic() + item.get("test_remaining_ms", 0) / 1000)
        self.update_device_test_caption()
        was_hardware = self.device_reminders_enabled()
        self.model = data
        self.render_device_list()
        self.render_wechat(data.get("wechat", {}))
        if self.device_reminders_enabled() != was_hardware or (
                self.has_unfinished_tasks() and (self.preview_phase or self.hardware_preview_id)):
            self.stop_preview()
        device, settings = (
            data["device"],
            data["settings"],
        )
        if not self.writing:
            self.confirmed_source_values = dict(settings["source_enabled"])
        self.duration_title.setText("提醒持续时间")
        hardware = self.device_reminders_enabled()
        online = hardware and device["online"]
        connecting = hardware and not online and device.get("connecting", False) and not data["maintenance"]
        port = device.get("port", "")
        connection = "wifi" if port else None
        connection_name = "Wi-Fi" if connection else "设备"
        count = device.get("connected_count", int(bool(online)))
        connected_title = f"已连接 {count} 台设备"
        for model_id, button in self.device_model_buttons.items():
            devices = [item for item in data.get("devices", []) if item.get("hardware_model") == model_id]
            online_count = sum(bool(item.get("online")) for item in devices)
            button.set_connection_count(online_count, len(devices) - online_count)
        self.scene_title.setText("任务状态")
        self.hardware_notice.setVisible(hardware and not online and not connecting)
        self.hardware_notice_text.setText(
            "设备维护中，硬件灯光暂不可用。" if data["maintenance"]
            else "尚未连接设备，硬件灯光暂不可用。"
        )
        self.hardware_notice_action.setText("查看进度" if data["maintenance"] else "连接设备")
        self.device_disabled_card.setVisible(not hardware)
        self.hardware_controls.show()
        self.device_badge.setText(
            "设备提醒已关闭"
            if not hardware
            else
            "设备维护中"
            if data["maintenance"] and not online
            else connected_title
            if online
            else "正在连接设备" if connecting else "尚未连接设备"
        )
        restyle(
            self.device_status,
            "tone",
            "success" if online and not data["maintenance"] else "warning" if hardware and not connecting else "neutral",
        )
        restyle(self.device_badge, "tone", self.device_status.property("tone"))
        show_connection = bool(online and connection and count == 1)
        for mark, size in ((self.device_connection_icon, 16), (self.device_summary_icon, 18)):
            mark.setVisible(show_connection)
            mark.setProperty("connection", connection if show_connection else None)
            if show_connection:
                mark.setPixmap(icon(connection, "#38735b", size).pixmap(size, size))
                mark.setAccessibleName(connection_name + " 连接")
        self.device_status.setToolTip(connection_name + " 连接" if show_connection else "")
        self.device_summary_icon.setToolTip(connection_name + " 连接" if show_connection else "")
        self.render_selected_device_status()
        self.render_scene()
        self.render_notice()
        if not self.writing and not self.brightness_timer.isActive():
            self.source_values = dict(settings["source_enabled"])
            if not self.duration.hasFocus():
                with QSignalBlocker(self.duration):
                    self.duration.setValue(settings["duration"])
                self.update_duration_steps()
            for key, check in self.checks.items():
                with QSignalBlocker(check):
                    check.setChecked(settings[key])
        self.order_sources()
        self.render_task_context({s["id"]: s["name"].replace("（实验）", "") for s in data["sources"]})
        enabled_sources = {source["id"]: source["name"].replace("（实验）", "")
                           for source in data["sources"] if source["enabled"]}
        for source, button in self.home_source_icons.items():
            button.setVisible(source in enabled_sources)
            name = t(WEB_SOURCES.get(source, enabled_sources.get(source, source)))
            button.set_name(name)
        for group, sources in self.home_source_groups.values():
            group.setVisible(any(source in enabled_sources for source in sources))
        visible = any(source in enabled_sources for source in self.home_source_icons)
        self.home_sources_row.setVisible(visible)
        self.home_sources_empty.setVisible(not visible)
        self.reflow_home_sources()
        for source in data["sources"]:
            sid = source["id"]
            if sid not in self.source_checks:
                continue
            if not self.writing:
                with QSignalBlocker(self.source_checks[sid]):
                    self.source_checks[sid].setChecked(source["enabled"])
        key = (
            [(e["id"], e["status"], e["reason"], e.get("delivery_summary")) for e in data["events"]],
            [(e["id"], e["status"], e["reason"], e.get("delivery_summary")) for e in data.get("task_events", [])],
            [(r["event"], r["output"], r["status"], r.get("detail")) for r in data.get("deliveries", [])],
        )
        if key != self.history_key:
            self.history_key = key
            self.render_history()
            self.render_recent()
        self.update_busy()
        self.state_changed.emit(data)

    def event_values(self, event):
        names = {
            s["id"]: s["name"].replace("（实验）", "") for s in self.model["sources"]
        }
        return (
            datetime.fromtimestamp(event["created"]).strftime("%m/%d %H:%M:%S"),
            names.get(event["source"], event["source"])
            + (" · 测试" if is_test(event) else ""),
            "已取消"
            if event["reason"] == "cancelled"
            else NAMES.get(event["state"], event["state"]),
            self.event_delivery_result(event),
        )

    @staticmethod
    def delivery_device_name(device):
        return device["name"] + "（" + device["device_id"] + "）"

    def event_delivery_result(self, event):
        result = RESULTS.get(event["status"], event["status"])
        devices = (event.get("delivery_summary") or {}).get("devices", [])
        if not devices:
            return result
        confirmed = sum(device["status"] == "applied" for device in devices)
        if confirmed == len(devices):
            return f"全部确认 · {confirmed}/{len(devices)} 台设备"
        if confirmed:
            return f"部分确认 · {confirmed}/{len(devices)} 台设备"
        return result + f" · 0/{len(devices)} 台设备确认"

    @staticmethod
    def delivery_color(event):
        devices = (event.get("delivery_summary") or {}).get("devices", [])
        confirmed = sum(device["status"] == "applied" for device in devices)
        if devices and 0 < confirmed < len(devices):
            return "#95630b"
        if event["status"] == "applied" and (not devices or confirmed == len(devices)):
            return "#38735b"
        return "#625f57"

    def event_search_text(self, event):
        values = [*self.event_values(event), (event.get("task") or t("未提供"))]
        for device in (event.get("delivery_summary") or {}).get("devices", []):
            values.extend((self.delivery_device_name(device), device["reason"]))
        return " ".join(values + [t(value) for value in values]).casefold()

    def render_recent(self):
        if not self.built or not self.model:
            return
        seen, events = set(), []
        for event in self.model.get("task_events", self.model["events"]):
            identity = self.event_key(event)
            if identity not in seen:
                seen.add(identity)
                events.append(event)
        self.recent_empty.setVisible(not events)
        capacity = max(0, (self.recent_list_body.contentsRect().height() + RECENT_ROW_GAP)
                       // (RECENT_ROW_HEIGHT + RECENT_ROW_GAP))
        events = events[:capacity]
        while len(self.recent_rows) < len(events):
            self.add_recent_row()
        for index, (gap_index, gap) in enumerate(self.recent_gaps):
            gap.changeSize(0, RECENT_ROW_GAP if index < len(events) - 1 else 0)
            self.recent_list.setStretch(gap_index, 0)
        self.recent_list.setStretch(self.recent_list.count() - 1, 1)
        self.recent_list.invalidate()
        for index, labels in enumerate(self.recent_rows):
            self.recent_row_frames[index].setVisible(index < len(events))
            if index < len(events):
                restyle(self.recent_row_frames[index], "last", index == len(events) - 1)
                event = events[index]
                values = list(self.event_values(event))
                values[0] = values[0][-8:]
                detail = values[3]
                if event["status"] != "applied":
                    values[3] = RESULTS.get(event["status"], event["status"])
                for item, value in zip(labels, values):
                    item.setText(value)
                    item.setToolTip(value)
                labels[1].setToolTip(t(values[1]) + " · " + (event.get("task") or t("未提供")))
                labels[3].setToolTip(detail)
                labels[3].setStyleSheet("color: " + self.delivery_color(event) + ";")
                restyle(labels[2], "recentTone", event["state"])
                button = self.recent_return_buttons[index]
                button.setVisible(bool(event.get("return_to")))
                self.configure_return_button(button, event)
        if events:
            labels = self.recent_rows[:len(events)]
            buttons = self.recent_return_buttons[:len(events)]
            for button in buttons:
                button.ensurePolished()
            action_width = max(buttons[0].fontMetrics().horizontalAdvance(t(text))
                               for text in ("返回任务", "正在返回…")) + 12
            state_width = max(row[2].fontMetrics().horizontalAdvance(row[2].text()) for row in labels)
            result_width = max(row[3].fontMetrics().horizontalAdvance(row[3].text()) for row in labels) + 2
            # Shared columns preserve alignment without squeezing the task state
            # when English labels expand in the narrower overview card.
            result_width = min(result_width, max(0, self.recent_list_body.width()
                                                - state_width - action_width - 36))
            for row, button in zip(labels, buttons):
                row[3].setFixedWidth(result_width)
                button.setFixedWidth(action_width)

    def render_history(self):
        if not self.model:
            return
        self.history.horizontalHeaderItem(3).setToolTip(t("此列显示灯光处理结果。系统和微信通知请查看详情中的「通知回执」。"))
        query = self.history_search.text().strip().casefold()
        kind = self.history_filter.currentData()
        grouped = self.history_view.currentData() == "tasks"
        history_events = self.model.get("task_events", self.model["events"]) if grouped else self.model["events"]
        events = [
            e
            for e in history_events
            if (kind == "all" or is_test(e) == (kind == "test"))
            and (not query or query in self.event_search_text(e))
        ]
        self.event_groups = {}
        for event in events:
            self.event_groups.setdefault(self.event_key(event), []).append(event)
        if grouped:
            events = [items[0] for items in self.event_groups.values()]
        selection = self.history.currentRow()
        selected_id = (
            self.visible_events[selection]["id"]
            if self.history.selectionModel().hasSelection()
            and 0 <= selection < len(self.visible_events)
            else None
        )
        selected_identity = (self.event_key(self.visible_events[selection])
                             if selected_id is not None and grouped else None)
        self.visible_events = events
        with QSignalBlocker(self.history):
            self.history.setRowCount(len(events))
            selected_row = -1
            for row, event in enumerate(events):
                if event["id"] == selected_id or self.event_key(event) == selected_identity:
                    selected_row = row
                for column, value in enumerate((*self.event_values(event), (event.get("task") or t("未提供")))):
                    item = self.history.item(row, column)
                    if item is None:
                        item = QTableWidgetItem(value, literal=column == 4)
                        self.history.setItem(row, column, item)
                    elif item.data(Qt.ItemDataRole.UserRole + 99) != value:
                        item.setText(value)
                    item.setToolTip(value if column == 4 else t(value))
                    color = self.delivery_color(event) if column == 3 else "#95630b" if column == 2 and event["state"] == "needs_input" else "#292824"
                    item.setForeground(QColor(color))
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                button = self.history.cellWidget(row, 5)
                if button is None:
                    button = QPushButton("查看")
                    button.setProperty("variant", "quiet")
                    button.clicked.connect(lambda checked=False, b=button: self.select_event_detail(b.property("eventId")))
                    self.history.setCellWidget(row, 5, button)
                button.setProperty("eventId", event["id"])
                button.setAccessibleName(t("查看记录详情") + " · " + t(self.event_values(event)[1]))
            if selected_row >= 0:
                self.history.selectRow(selected_row)
            else:
                self.history.clearSelection()
                self.history.setCurrentCell(-1, -1)
        self.history_stack.setCurrentIndex(0 if events else 1)
        self.history_clear_button.setVisible(bool(query or kind != "all"))
        self.history_selection_hint.setVisible(bool(events))
        self.history_empty.setText(
            "没有符合筛选条件的记录，试试其他关键词或事件类型。"
            if history_events
            else "还没有记录。接入应用后，新任务会自动出现在这里。"
        )
        self.history_count.setText(
            (f"显示 {len(events)} 项任务" if query or kind != "all" else f"最近 {len(events)} 项任务") if grouped
            else (f"显示 {len(events)} 条事件" if query or kind != "all" else f"最近 {len(events)} 条事件")
        )
        self.show_event_detail()

    def select_event_detail(self, event_id):
        row = next((index for index, event in enumerate(self.visible_events) if event["id"] == event_id), None)
        if row is not None:
            self.history.selectRow(row)
            self.show_event_detail()
            self.tabs.widget(4).ensureWidgetVisible(self.event_detail_box)

    def close_event_detail(self):
        # Keep hiding the focused close button from selecting a table row again.
        self.history.setFocus(Qt.FocusReason.MouseFocusReason)
        with QSignalBlocker(self.history):
            self.history.clearSelection()
            self.history.setCurrentCell(-1, -1)
        self.show_event_detail()

    def show_event_detail(self):
        row = self.history.currentRow()
        if not self.history.selectionModel().hasSelection() or not 0 <= row < len(self.visible_events):
            self.event_detail_box.hide()
            self.event_detail.clear()
            return
        self.event_detail_box.show()
        event = self.visible_events[row]
        self.configure_return_button(self.return_task_button, event)
        def paragraph(text, style, *, literal=False):
            return '<p style="' + style + '">' + escape(text if literal else t(text)) + '</p>'

        secondary = "font-size:12px; color:#625f57; font-weight:400;"
        metadata = secondary + " margin-top:0px; margin-bottom:4px;"
        heading = "font-size:14px; font-weight:600; color:#292824; margin-top:16px; margin-bottom:6px;"
        values = self.event_values(event)
        task_id = event.get("task") or t("未提供")
        # Keep diagnostic identifiers subordinate to the task and delivery.
        # The header action exposes and copies the original, unmodified value.
        display_id = task_id if len(task_id) <= 48 else task_id[:24] + "…" + task_id[-16:]
        QtWidget.setToolTip(self.copy_task_button, "<p>" + escape(task_id) + "</p>")
        QtWidget.setAccessibleDescription(self.copy_task_button, task_id)
        blocks = [paragraph(" · ".join(values[1:3]),
                            "font-size:14px; font-weight:600; color:#292824; margin-top:0px; margin-bottom:4px;")]
        blocks.append(paragraph(values[0], metadata))
        blocks.append(paragraph(t("任务标识：") + display_id, metadata, literal=True))
        blocks.append(paragraph("灯光处理结果：", heading))
        blocks.append(paragraph(self.event_delivery_result(event),
                                "font-size:16px; font-weight:500; color:" + self.delivery_color(event) + "; margin-top:0px; margin-bottom:0px;"))
        summary = event.get("delivery_summary") or {}
        if event["status"] != "applied":
            reason = summary.get("reason") or DELIVERY_REASONS.get(event["status"])
            if reason:
                blocks.append(paragraph(reason, secondary + " margin-top:6px; margin-bottom:0px;"))
        devices = summary.get("devices", [])
        if devices:
            blocks.append(paragraph("设备回执", heading))
            for device in devices:
                color = "#38735b" if device["status"] == "applied" else "#625f57"
                blocks.append('<p style="font-size:14px; font-weight:400; color:#292824; margin-top:0px; margin-bottom:6px; line-height:130%;">'
                              + escape(t(device["name"]) if device["name"] in ("Yeelight 屏幕挂灯 Pro", "Yeelight W3 彩色灯泡", "Yeelight Beam 塔灯") else device["name"]) + '<span style="' + secondary + '">（'
                              + escape(device["device_id"]) + '）</span><br>'
                              + '<span style="font-size:12px; font-weight:400; color:' + color + ';">'
                              + escape(t(device["reason"])) + '</span></p>')
        statuses = {"pending": "等待桌面应用领取", "claimed": "桌面应用处理中", "submitted": "已提交系统（显示取决于系统通知设置）",
                    "denied": "系统通知权限未允许", "unavailable": "系统通知不可用", "failed": "投递失败", "disabled": "已关闭",
                    "not_requested": "本事件无需弹窗", "interrupted": "进程重启，未重播", "unconfirmed": "提交结果未确认", "superseded": "任务状态已变化"}
        notifications = [statuses.get(receipt["status"], RESULTS.get(receipt["status"], receipt["status"]))
                         for receipt in self.model.get("deliveries", [])
                         if receipt["event"] == event["id"] and receipt["output"] == "desktop"]
        wechat = [WECHAT_RECEIPTS.get(receipt["status"], receipt["status"])
                  for receipt in self.model.get("deliveries", [])
                  if receipt["event"] == event["id"] and receipt["output"] == "wechat"]
        if notifications or wechat:
            blocks.append(paragraph("通知回执", heading))
            blocks.append('<table width="100%" cellspacing="0" cellpadding="0">')
            channel_width = 156 if language() == "en" else 120
            for channel, receipts in (("系统通知", notifications), ("微信通知", wechat)):
                if not receipts:
                    continue
                blocks.append(f'<tr><td width="{channel_width}" valign="top" style="font-size:12px; font-weight:400; color:#292824; padding-right:16px; padding-bottom:6px;">'
                              + escape(t(channel)) + '</td><td valign="top" style="' + secondary + ' padding-bottom:6px;">'
                              + '<br>'.join(escape(t(receipt)) for receipt in receipts) + '</td></tr>')
            blocks.append('</table>')
        related = self.event_groups.get(self.event_key(event), [])
        if len(related) > 1:
            blocks.append('<p style="' + heading + '">' + escape(t("状态事件"))
                          + '<span style="' + secondary + '">（' + escape(t("当前筛选范围")) + '）</span></p>')
            cell = "border:1px solid #dedcd4; padding:8px;"
            blocks.append('<table width="100%" border="1" cellspacing="0" cellpadding="8" '
                          'style="border-collapse:collapse; border:1px solid #dedcd4;"><thead><tr>')
            for title, width in (("时间", 110), ("任务状态", 120), ("灯光结果", None)):
                blocks.append('<th' + (f' width="{width}"' if width else '')
                              + ' align="left" valign="middle" bgcolor="#f4f2ed" style="' + cell
                              + ' font-size:12px; font-weight:600; color:#292824;">'
                              + escape(t(title)) + '</th>')
            blocks.append('</tr></thead><tbody>')
            for index, item in enumerate(reversed(related)):
                when, _, state, result = self.event_values(item)
                background = "#ffffff" if index % 2 == 0 else "#faf9f6"
                blocks.append('<tr bgcolor="' + background + '"><td width="110" valign="middle" style="' + cell + secondary + '">'
                              + escape(when)
                              + '</td><td width="120" valign="middle" style="' + cell + ' font-size:12px; font-weight:400; color:#292824; padding:8px;">'
                              + escape(t(state)) + '</td><td valign="middle" style="' + cell + secondary + '">'
                              + escape(t(result))
                              + '</td></tr>')
            blocks.append('</tbody></table>')
        self.event_detail.setText("".join(blocks))

    def export_history(self):
        if not self.visible_events:
            self.tell("当前筛选下没有可导出的记录。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, t("导出当前筛选的记录"), "SeeEnd-events.csv", t("CSV 文件 (*.csv)")
        )
        if not path:
            return
        try:
            with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(t(value) for value in ("时间", "应用 / 来源", "任务状态", "灯光结果", "任务标识"))
                writer.writerows([*[t(value) for value in self.event_values(e)], e.get("task", "")] for e in self.visible_events)
            self.tell(f"已导出 {len(self.visible_events)} 条记录。")
        except OSError:
            self.on_error("保存失败，请检查所选目录的权限。")

    def poll(self, force=False):
        if not force and not self.isVisible():
            return
        if not self.ready:
            self.api.request("/api/session", self.connected)
            return
        generation = self.generation
        # Hardware preview polls every 150 ms. Both preview and device-page
        # updates must avoid rebuilding history under the animation's lock.
        page = self.tabs.currentIndex()
        lightweight = bool(self.model) and (
            (page == 1 and not force) or (page == 0 and bool(self.hardware_preview_id)))

        def received(data):
            if lightweight and self.tabs.currentIndex() != page:
                self.poll(force=True)
            elif generation == self.generation:
                self.render({**self.model, **data} if lightweight else data)
            else:
                self.poll(force=True)

        self.api.request("/api/status/live" if lightweight else "/api/status", received)
        if (self.tabs.currentIndex() == 1 and not self.device_details.isHidden()) or self.job_busy:
            self.api.request(self.device_query(), lambda data: self.render_device(data) if generation == self.generation else None)

    def showEvent(self, event):
        super().showEvent(event)
        self.poll_timer.start()
        self.poll(force=True)

    def hideEvent(self, event):
        self.stop_state_device_preview()
        self.light_previews.select_state(None)
        self.plug_previews.select_state(None)
        self.poll_timer.stop()
        self.stop_preview()
        self.flush_device_brightness()
        super().hideEvent(event)

    def tab_changed(self, index):
        self.nav_buttons[index].setChecked(True)
        self.breadcrumb.setText(PAGES[index][0])
        if index != 0:
            self.stop_preview()
        if index == 1:
            self.show_device_selector()
        if index == 3:
            self.request_notification_status()
        self.poll(force=True)


    def device_action(self, action, callback=None, **values):
        if self.device_selection_loading:
            self.tell("正在读取此设备，请稍候。", error=True)
            return
        if not self.device_reminders_enabled():
            self.tell("请先开启设备提醒。", error=True)
            return
        self.job_message.setText("正在提交设备操作…")
        self.job_message.setStyleSheet("")
        self.job_message.show()
        self.progress.setValue(0)
        self.progress.show()

        def received(job):
            self.device_operation_id = job.get("id")
            self.render_device({"job": job})
            if callback:
                callback(job)

        def failed():
            self.progress.hide()
            self.job_message.setText(source_text(self.message))
            self.job_message.setStyleSheet("color: #a0472c;")
            plug_scan = self.selected_device_model in MIJIA_PLUG_MODELS and action == "discover"
            self.job_message.setVisible(not plug_scan)
            if plug_scan:
                self.mijia_scan_status.setText(source_text(self.message))

        self.act(
            "/api/device",
            {"action": action, "hardware_model": self.selected_device_model, "device_id": self.selected_device_id or "", **values},
            received,
            failed,
        )

    def reconnect_current_device(self):
        self.device_action("connect", transport="miio" if self.selected_device_model in MIJIA_PLUG_MODELS else "yeelight",
                           host=self.yeelight_host.text().strip())

    def choose_yeelight_address(self, _index):
        host = self.yeelight_choices.currentData()
        if host:
            self.yeelight_host.setText(host)

    def render_device(self, data):
        if "mijia_login" in data:
            self.render_mijia_login(data["mijia_login"])
        job = data.get("job")
        if (job and job.get("id") == self.device_operation_id and job["state"] == "succeeded"
                and job.get("hardware_model") == self.selected_device_model and not self.selected_device_id
                and job.get("result", {}).get("device_id")):
            self.selected_device_id = job["result"]["device_id"]
            data = {**data, "selected_device_id": self.selected_device_id}
            self.device_operation_id = None
        if "connection_paused" in data:
            self.connection_paused = data["connection_paused"]
        if "selected_device_id" in data:
            self.device_selection_loading = False
            self.selected_device_id = data["selected_device_id"]
            self.selected_device_state = data.get("device", {"online": False})
            if self.model and self.selected_device_id:
                self.model["devices"] = [{**item, **self.selected_device_state} if item["device_id"] == self.selected_device_id else item
                                         for item in self.model.get("devices", [])]
            self.selected_device_brightness = data.get("light_settings", {}).get("brightness", 50)
            self.mijia_connected_identity.setText("IP：" + (data.get("miio_host") or "未提供") + "   ·   设备 ID：" + (data.get("miio_did") or "未提供"))
            self.device_light_hint.setVisible(not self.selected_device_id)
            from aiflame.devices.light_settings import COLOR_DEFAULTS, STATE_BRIGHTNESS_DEFAULTS, front_brightness_defaults, FRONT_TEMPERATURE_DEFAULTS, FRONT_FLASH_DEFAULTS
            light_settings = data.get("light_settings", {})
            self.preview_saved_settings = (self.selected_device_id, self.selected_device_model, light_settings)
            if self.selected_device_model in MIJIA_PLUG_MODELS and not self.writing:
                for state, choice in self.plug_power_choices.items():
                    with QSignalBlocker(choice):
                        choice.setCurrentIndex(choice.findData(light_settings.get("state_power", {}).get(state, state != "working")))
            color_values = light_settings.get("colors", COLOR_DEFAULTS)
            if not self.writing and not self.brightness_timer.isActive():
                with QSignalBlocker(self.yeelight_reminder_mode):
                    self.yeelight_reminder_mode.setCurrentIndex(self.yeelight_reminder_mode.findData(light_settings.get("reminder_mode", "back")))
                self.render_yeelight_mode()
                flash = {**FRONT_FLASH_DEFAULTS, **light_settings.get("front_flash", {})}
                with QSignalBlocker(self.yeelight_flash_enabled):
                    self.yeelight_flash_enabled.setChecked(flash["enabled"])
                for key, control in self.yeelight_flash_controls.items():
                    with QSignalBlocker(control):
                        control.setValue(flash[key])
                    with QSignalBlocker(self.yeelight_flash_labels[key]):
                        self.yeelight_flash_labels[key].setValue(flash[key] / 1000)
                self.render_yeelight_flash_cycle()
                defaults = front_brightness_defaults(self.selected_device_model) if self.yeelight_front_mode() else STATE_BRIGHTNESS_DEFAULTS
                field = "front_state_brightness" if self.yeelight_front_mode() else "state_brightness"
                state_brightness = light_settings.get(field, defaults)
                for state, control in self.yeelight_brightness_controls.items():
                    if not control.isSliderDown():
                        value = state_brightness.get(state, defaults[state])
                        with QSignalBlocker(control):
                            control.setValue(value)
                        with QSignalBlocker(self.yeelight_brightness_labels[state]):
                            self.yeelight_brightness_labels[state].setValue(value)
                temperatures = light_settings.get("front_state_temperature", FRONT_TEMPERATURE_DEFAULTS)
                for state, control in self.yeelight_temperature_controls.items():
                    if not control.isSliderDown():
                        value = temperatures.get(state, FRONT_TEMPERATURE_DEFAULTS[state])
                        with QSignalBlocker(control):
                            control.setValue(value)
                        self.render_yeelight_temperature(state, value)
            if not self.writing:
                for state, choice in self.yeelight_color_choices.items():
                    value = color_values.get(state, COLOR_DEFAULTS[state])
                    with QSignalBlocker(choice):
                        index = choice.findData(value)
                        if index < 0:
                            choice.addItem("自定义 " + value, value)
                            index = choice.findData(value)
                        choice.setCurrentIndex(index)
            has_history = bool(data.get("devices"))
            yeelight = self.selected_device_model in YEELIGHT_MODELS
            self.yeelight_history_heading.setVisible(yeelight)
            self.device_instance_panel.setVisible(yeelight or has_history)
            with QSignalBlocker(self.device_instances):
                self.device_instances.clear()
                self.device_instances.addItem("尚未添加设备" if yeelight and not has_history else "添加此型号设备…", "")
                for item in data.get("devices", []):
                    state = "已连接" if item.get("online") else "已断开" if item.get("connection_paused") else "离线"
                    self.device_instances.addItem(item["name"] + " · " + state, item["device_id"])
                    self.device_instances.setItemData(self.device_instances.count() - 1, item["device_id"], Qt.ToolTipRole)
                self.device_instances.setCurrentIndex(max(0, self.device_instances.findData(self.selected_device_id)))
            if self.port_restore_pending and self.selected_device_model in SUPPORTED_MODELS:
                self.yeelight_host.setText(data.get("miio_host" if self.selected_device_model in MIJIA_PLUG_MODELS else "yeelight_host", ""))
                self.port_restore_pending = False
        job = data.get("job")
        if "hardware_model" in data and not (job and job["state"] == "running"):
            if not self.device_details.isHidden():
                self.open_device_details(data["hardware_model"])
        job = data.get("job")
        if job:
            if job.get("action") == "discover" and job["state"] == "succeeded" and self.selected_device_model in SUPPORTED_MODELS and job.get("hardware_model", self.selected_device_model) == self.selected_device_model and self.rendered_yeelight_discovery != job.get("id"):
                self.rendered_yeelight_discovery = job.get("id")
                rows = job.get("result", {}).get("devices", [])
                if self.selected_device_model in MIJIA_PLUG_MODELS:
                    self.render_mijia_discovery(rows)
                else:
                    with QSignalBlocker(self.yeelight_choices):
                        self.yeelight_choices.clear()
                        self.yeelight_choices.addItem("选择灯具…" if rows else "未发现，可手动填写 IP", "")
                        for item in rows:
                            self.yeelight_choices.addItem(item["name"] + " · " + item["host"], item["host"])
            self.job_busy = job["state"] == "running"
            self.progress.setVisible(self.job_busy)
            plug_scan = self.selected_device_model in MIJIA_PLUG_MODELS and job.get("action") == "discover"
            self.job_message.setVisible(not plug_scan)
            if plug_scan and job["state"] == "failed":
                self.mijia_scan_status.setText(job["message"])
            self.progress.setValue(job["progress"])
            self.job_message.setText(job["message"])
            self.job_message.setStyleSheet(
                "color: #a0472c;" if job["state"] == "failed" else ""
            )
        self.render_light_previews()
        self.render_selected_device_status()
        self.update_busy()

    def connected_light_settings(self):
        device = self.detail_device()
        return bool(self.ready and self.model is not None and self.selected_device_id
                    and not self.device_selection_loading and device.get("online")
                    and not device.get("connecting"))

    def update_busy(self):
        if not self.built:
            return
        self.render_save_feedback()
        connected = self.ready and self.model is not None
        busy = (
            self.reset_pending
            or self.job_busy
            or self.writing
            or bool(self.model and self.model["maintenance"])
        )
        self.reset_button.setEnabled(connected and not busy)
        hardware = connected and self.device_reminders_enabled() and not self.device_selection_loading
        for button in self.device_model_buttons.values():
            button.setEnabled(connected and not self.reset_pending)
        self.device_add_button.setEnabled(connected and not self.reset_pending)
        for button in self.device_rows.values():
            button.setEnabled(connected and not self.reset_pending)
        for button in self.maintenance_buttons:
            button.setEnabled(hardware and not busy)
        device = self.detail_device()
        online = hardware and device.get("online", False)
        connecting = device.get("connecting", False)
        available = hardware and not busy and not connecting
        self.yeelight_discover_button.setEnabled(hardware and not busy)
        self.yeelight_connect_button.setEnabled(available and not self.selected_device_id and bool(self.yeelight_host.text().strip()))
        self.yeelight_reconnect_button.setEnabled(available and not online and bool(self.selected_device_id) and bool(self.yeelight_host.text().strip()))
        self.yeelight_reconnect_button.setText("连接此设备")
        plug = self.selected_device_model in MIJIA_PLUG_MODELS
        self.update_mijia_controls(hardware, busy)
        for control in [*self.plug_power_choices.values(), *self.plug_state_resets.values()]:
            control.setEnabled(connected and not busy and not self.device_selection_loading and plug and bool(self.selected_device_id))
        self.device_instances.setEnabled(connected and not busy and not self.device_selection_loading)
        color_enabled = connected and not self.writing and not self.device_selection_loading and bool(self.selected_device_id) and self.selected_device_model in YEELIGHT_MODELS
        for control in [*self.yeelight_color_choices.values(), *self.yeelight_color_buttons.values(),
                        *self.yeelight_brightness_controls.values(), *self.yeelight_temperature_controls.values(),
                        *self.yeelight_brightness_labels.values(), *self.yeelight_temperature_labels.values(),
                        self.yeelight_reminder_mode, *self.yeelight_state_resets.values(), self.yeelight_flash_enabled]:
            control.setEnabled(color_enabled)
        for control in [*self.yeelight_flash_controls.values(), *self.yeelight_flash_labels.values()]:
            control.setEnabled(color_enabled and self.yeelight_flash_enabled.isChecked())
        if self.selected_device_model in SUPPORTED_MODELS:
            self.yeelight_add_panel.setVisible(not self.selected_device_id and not plug)
            self.device_light_card.setVisible(self.connected_light_settings())
            self.mijia_connected_identity.setVisible(plug and bool(self.selected_device_id))
            self.device_status_card.setVisible(bool(self.selected_device_id))
            self.yeelight_reconnect_button.setVisible(bool(self.selected_device_id) and not online)
            self.try_light_button.setVisible(bool(self.selected_device_id) and online)
        self.disconnect_button.setEnabled(hardware and not busy and not self.connection_paused and (online or connecting or bool(self.selected_device_id)))
        self.yeelight_delete_button.setEnabled(connected and not busy and not self.device_selection_loading
                                             and bool(self.selected_device_id) and self.selected_device_model in SUPPORTED_MODELS)
        reason = self.light_block_reason()
        if self.has_unfinished_tasks():
            reason = "有任务尚未结束，暂时无法演示灯效。"
        for button in self.manual_buttons:
            button.setEnabled(not self.writing and not reason)
            button.setToolTip(reason)
        self.try_light_button.setEnabled(
            hardware and not self.writing
            and not reason
            and online and bool(self.selected_device_id)
        )
        if self.has_unfinished_tasks():
            self.try_light_button.setToolTip(reason)
        elif not online or not self.selected_device_id:
            self.try_light_button.setToolTip("请先连接当前设备；也可以在屏幕预览灯效。")
        preview_reason = self.preview_block_reason()
        preview_available = bool(self.preview_phase or self.hardware_preview_id) or not preview_reason
        self.preview_button.setEnabled(preview_available)
        self.preview_button.setToolTip("" if preview_available else preview_reason)
        self.preview_button.setAccessibleDescription("" if preview_available else preview_reason)
        self.preview_button.setCursor(Qt.CursorShape.PointingHandCursor if preview_available else Qt.CursorShape.ArrowCursor)
        for check in self.source_checks.values():
            check.setEnabled(connected and not self.reset_pending)
        for control in [
            self.duration_control,
            *(check for key, check in self.checks.items() if key not in self.home_channel_checks),
        ]:
            control.setEnabled(connected and not self.reset_pending)
        self.update_notification_controls()
        self.update_wechat_controls()
        self.update_channel_controls()


    def has_unfinished_tasks(self):
        return bool(self.model and (self.model.get("unfinished_count", 0) or self.model.get("active_count", 0) or
                    any(task["state"] in ("working", "needs_input") and
                        self.model["settings"]["source_enabled"].get(task["source"])
                        for task in self.model.get("tasks", []))))

    def preview_uses_devices(self):
        return bool(self.device_reminders_enabled() and self.model["device"].get("online"))

    def preview_block_reason(self):
        if not self.ready or not self.model:
            return "正在读取任务状态…"
        if self.has_unfinished_tasks():
            return "有任务尚未结束，暂时无法演示灯效。"
        if not self.preview_uses_devices():
            return ""
        reason = self.light_block_reason(source=None)
        if reason:
            return reason
        return ""

    def open_browser_extension_guide(self, source=None):
        from aiflame.desktop.extension_guide import ExtensionGuide
        dialog = getattr(self, "extension_guide", None)
        if dialog is None:
            dialog = self.extension_guide = ExtensionGuide(self)
        if source in WEB_SOURCES:
            dialog.site_choice.setCurrentIndex(dialog.site_choice.findData(source))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def copy_coding_skill(self):
        from PySide6.QtWidgets import QApplication
        from aiflame.integrations.cli_tools.skill import installation_prompt
        QApplication.clipboard().setText(installation_prompt(translate=t))
        self.skill_notice.setText("安装指令已复制。请到编程工具粘贴并发送。")
        self.skill_notice.show()
        QTimer.singleShot(8000, self.skill_notice.hide)

    def open_coding_skill(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from aiflame.integrations.cli_tools.skill import SKILL_URL
        if not QDesktopServices.openUrl(QUrl(SKILL_URL)):
            self.skill_notice.setText("无法打开浏览器。安装指令中包含 Skill 仓库地址，可复制后打开。")
            self.skill_notice.show()

    def open_web_app(self, source):
        from aiflame.integrations.chatgpt_web.host import WEB_URLS
        self.open_app_link(WEB_URLS[source])

    def open_app_link(self, link):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if not QDesktopServices.openUrl(QUrl(link)):
            self.tell(t("无法打开浏览器，请手动打开：") + link, error=True)


    def shutdown(self):
        self.light_previews.timer.stop()
        self.plug_previews.timer.stop()
        self.return_timer.stop()
        self.return_requests.clear()
        self.cancel_notification_enable()
        self.recent_reflow_timer.stop()
        self.adaptation.close()
        self.cancel_mijia_login()
        self.built = False
        self.device_test_timer.stop()
        self.poll_timer.stop()
        self.brightness_timer.stop()
        self.preview_timer.stop()
        self.message_timer.stop()
        self.flame.animation_timer.stop()
        self.actions.clear()
        self.api.close()
