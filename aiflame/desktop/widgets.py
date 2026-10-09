"""Native dashboard controller with bounded polling and stable controls."""

import csv
from html import escape
from collections import deque
from datetime import datetime
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QSignalBlocker, QTime, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QBoxLayout, QFileDialog, QToolTip, QMessageBox, QColorDialog
from aiflame.desktop.localization import QTableWidgetItem, QWidget

from .components import Disclosure, icon, restyle
from .network import LocalAPI
from .theme import STYLE
from .views import DashboardViews, PAGES
from .localization import set_language, language, source_text, t
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS, hardware_model
from aiflame.core.delivery import REASONS as DELIVERY_REASONS

NAMES = {
    "idle": "当前无任务",
    "working": "任务进行中",
    "done": "任务已完成",
    "error": "需关注",
    "cancelled": "已取消",
    "unknown": "状态未知",
    "needs_input": "等待输入",
    "timeout": "已超时",
}
RESULTS = {
    "screen": "APP 已呈现",
    "queued": "等待发送",
    "applied": "设备已确认",
    "suppressed": "已静默",
    "unmatched": "来源已停用",
    "expired": "事件已过期",
    "lease_expired": "工作状态超时",
    "dropped": "队列已淘汰",
    "ended_on_reconnect": "重连后不补播",
    "superseded": "已被后续状态替代",
    "interrupted": "进程重启，未重播",
}
VERIFICATION_STATUS = {
    "needs_input": "等待输入",
    "unknown": "状态未知",
    "waiting": "等待新任务",
    "working": "任务进行中",
    "verified": "已收到开始与完成",
    "cancelled": "已取消",
    "error": "异常",
    "timeout": "超时",
    "incomplete": "等待新任务",
    "disabled": "已关闭",
}

VERIFICATION_DETAILS = {
    "needs_input": "任务等待你的输入或授权，可在来源应用继续。",
    "unknown": "尚不能确定任务结果，请检查来源应用。",
    "waiting": "尚未收到真实任务，请在对应应用发起新任务。",
    "working": "已收到开始，等待同一任务完成。",
    "verified": "已收到同一任务的开始与完成。",
    "cancelled": "本轮已取消，请另发一条完整任务。",
    "error": "本轮报告异常，请检查对应应用后重试。",
    "timeout": "工作状态已超时，尚未确认完成。",
    "incomplete": "事件不完整，尚不能确认此用法。",
    "disabled": "来源已关闭，请先开启接入。",
}
CLIENT_STATUS = {
    "checking": "正在检查",
    "unsupported": "系统暂不支持",
    "not_installed": "未发现安装",
    "disabled": "请先开启来源",
    "paused": "触发已暂停",
    "permission_required": "需要授权",
    "input_unavailable": "中断监测不可用，请重启并检查权限",
    "ambiguous": "多个客户端进程，暂不跟踪",
    "waiting": "请打开客户端",
    "generating": "检测到生成中",
    "ready": "输入控件已识别，等待新任务",
    "controls_unavailable": "未识别完整对话控件，请打开对话窗口；此版本可能需要适配",
}


def is_test(event):
    return bool(event.get("simulated") or event["source"] in ("manual", "demo"))


def quiet_now(settings):
    if not settings["quiet_enabled"]:
        return False
    now = datetime.now().strftime("%H:%M")
    start, end = settings["quiet_start"], settings["quiet_end"]
    return start <= now < end if start < end else now >= start or now < end


class Dashboard(DashboardViews, QWidget):
    state_changed = Signal(object)

    def __init__(self, parent=None, port=8765):
        super().__init__(parent)
        self.setObjectName("dashboard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.built = False
        self.model = None
        self.ready = False
        self.source_checks, self.source_values = {}, {}
        self.chatgpt_permission_pending = False
        self.codex_action_notice = ""
        self.history_key = None
        self.visible_events = []
        self.manual_task, self.manual_seq = None, 0
        self.actions = deque()
        self.writing = False
        self.reset_pending = False
        self.mode_pending = None
        self.generation = 0
        self.job_busy = self.test_busy = False
        self.maintenance_buttons, self.trigger_buttons = [], []
        self.source_action_buttons = []
        self.preview_phase = None
        self.preview_started = 0
        self.hardware_preview_id = None
        self.hardware_preview_starting = False
        self.notice_kind = None
        self.clients_supported = True
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
        self.message_timer.setInterval(3000)
        self.message_timer.timeout.connect(lambda: self.statusbar.hide())
        self.message_error = False
        self.build_views()
        self.built = True
        self.tabs.currentChanged.connect(self.tab_changed)
        self.update_busy()
        self.api.request("/", self.connected, raw=True)

    def tell(self, text, *, error=False):
        self.retry_button.hide()
        self.message_timer.stop()
        self.message_error = error
        self.message.setStyleSheet("color: #a0472c;" if error else "")
        self.message.setText(str(text))
        self.statusbar.show()
        if not error:
            self.message_timer.start()

    def on_error(self, text):
        self.tell(text, error=True)
        if "Hub 未连接" in str(text) or text == "需要有效凭据":
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
        self.message_error = False
        self.statusbar.hide()
        self.generation += 1
        self.actions.append((path, body, callback, failed))
        if not self.writing:
            self.next_action()

    def next_action(self):
        if not self.actions:
            self.writing = False
            self.update_busy()
            self.poll(force=True)
            return
        self.writing = True
        self.update_busy()
        path, body, callback, failed = self.actions.popleft()

        def received(data):
            if path != "/api/preview" and not (path == "/api/settings" and set(body) == {"mode"}):
                self.tell(
                    data.get(
                        "message",
                        "偏好已保存"
                        if path == "/api/settings"
                        else "操作已接收，正在同步状态。",
                    )
                )
            if callback:
                callback(data)
            self.next_action()

        def rejected():
            if failed:
                failed()
            self.next_action()

        self.api.request(path, received, body, failed=rejected)

    def reset_app(self):
        if self.reset_pending or not self.reset_button.isEnabled():
            return
        self.reset_pending = True
        self.update_busy()
        dialog = QMessageBox(QMessageBox.Icon.Warning, t("确定重置 APP 吗？"),
                             t("清除本机缓存、活动记录、设备配对、提醒偏好和应用接入配置，恢复初始状态。不会修改其他应用的文件或设备固件；应用接入需要重新配置。"),
                             QMessageBox.StandardButton.NoButton, self)
        reset = dialog.addButton(t("重置 APP"), QMessageBox.ButtonRole.DestructiveRole)
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
        # The response has already refreshed the worker's authenticated cookie.
        # Invalidate old dashboard snapshots and discard local drafts. Keep the
        # shared API consumers' callbacks so their polling state can complete.
        self.generation += 1
        self.actions.clear()
        self.brightness_timer.stop()
        self.pending_device_brightness = None
        self.hardware_preview_id = None
        self.stop_preview()
        self.manual_task, self.manual_seq = None, 0
        self.mode_pending = None
        self.job_busy = self.test_busy = False
        self.reset_pending = False
        self.chatgpt_permission_pending = False
        self.codex_action_notice = ""
        self.clients_supported = True
        self.device_test_deadlines.clear()
        self.selected_device_id = None
        self.selected_device_model = DEFAULT_MODEL
        self.selected_device_state = {"online": False}
        self.selected_device_brightness = 50
        self.device_selection_loading = self.port_restore_pending = False
        self.device_operation_id = self.rendered_yeelight_discovery = None
        self.connection_paused = False
        self.source_values.clear()
        for edit in [self.yeelight_host, *self.cli_config_dirs.values(), self.history_search]:
            with QSignalBlocker(edit):
                edit.clear()
        for choices in [self.yeelight_choices, self.device_instances, *self.cli_profiles.values()]:
            with QSignalBlocker(choices):
                choices.clear()
        with QSignalBlocker(self.device_instances):
            self.device_instances.addItem("添加此型号设备…", "")
        for choices in self.cli_profiles.values():
            with QSignalBlocker(choices):
                choices.addItem("正在查找已有 Profile…", "")
        self.device_test_caption.hide()
        self.device_version.setText("连接后读取固件版本")
        with QSignalBlocker(self.history_filter):
            self.history_filter.setCurrentIndex(0)
        self.integration_plans.clear()
        for source, guide in self.integration_guides.items():
            guide.setText("展开后显示实际配置文件、增量修改方式和验证步骤。")
            self.integration_manual_guides[source].clear()
            self.integration_snippets[source].clear()
            self.integration_copy_buttons[source].setEnabled(False)
            self.integration_copy_buttons[source].setText("复制增量配置 / 命令")
        for message in [*self.cli_messages.values(), *self.cli_details.values(), self.codex_connection_details]:
            message.setText("尚未检查。")
        for message in [self.browser_message, self.client_message, self.chatgpt_permission_hint,
                        self.test_message, self.test_details, self.job_message]:
            message.clear()
            message.hide()
        self.client_details.clear()
        self.test_details.setText("尚未运行诊断。")
        self.progress.hide()
        for disclosure in self.findChildren(Disclosure):
            disclosure.button.setChecked(False)
        self.history_key = None
        self.visible_events = []
        self.ready = True
        self.open_device_details(DEFAULT_MODEL)
        self.show_device_selector()
        self.render(data)
        self.navigate(0)
        self.tell("APP 已重置，请重新配置应用接入。")

    def setting(self, key, value):
        if self.model:
            if key == "quiet_enabled":
                self.quiet_times_box.setVisible(value)
                for edit in self.times.values():
                    edit.setEnabled(value)
            self.act("/api/settings", {key: value})

    def hardware_mode(self):
        return bool(self.model and self.model["settings"].get("mode") == "hardware")

    def choose_mode(self, mode):
        if not self.ready or not self.model or self.writing:
            return
        if self.model["settings"]["mode"] == mode:
            return
        self.mode_pending = mode
        self.render_mode_choice()
        self.stop_preview()

        def saved(data):
            self.mode_pending = None
            self.render({**self.model, **data})

        def failed():
            self.mode_pending = None
            self.render_mode_choice()

        self.act("/api/settings", {"mode": mode}, saved, failed)

    def render_mode_choice(self):
        selected = self.mode_pending or self.model["settings"]["mode"]
        for mode, button in self.mode_buttons.items():
            button.setChecked(mode == selected)
        self.mode_hint.setText(
            ("正在切换至虚拟模式…" if selected == "virtual" else "正在切换至硬件模式…")
            if self.mode_pending else
            "连接设备接收任务提醒，屏幕也会同步设备状态。" if self.hardware_mode()
            else "屏幕烛火随任务变化，不查找或连接设备。"
        )


    def flush_device_brightness(self):
        self.brightness_timer.stop()
        pending, self.pending_device_brightness = self.pending_device_brightness, None
        if pending:
            self.act("/api/device/light-settings", pending)

    def yeelight_brightness_changed(self, state, value):
        self.yeelight_brightness_labels[state].setText(f"{value}%")
        if self.ready and self.model and self.selected_device_id and not self.device_selection_loading:
            pending = self.pending_device_brightness or {}
            values = dict(pending.get("settings", {}).get("state_brightness", {}))
            values[state] = value
            self.pending_device_brightness = {"hardware_model": self.selected_device_model,
                                             "device_id": self.selected_device_id,
                                             "settings": {"state_brightness": values}}
            self.brightness_timer.start()

    def device_light_setting(self, key, value):
        self.device_light_settings({key: value})

    def device_light_settings(self, settings):
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

    def reset_yeelight_colors(self):
        from aiflame.devices.light_settings import COLOR_DEFAULTS, STATE_BRIGHTNESS_DEFAULTS
        self.device_light_settings({"colors": dict(COLOR_DEFAULTS),
                                    "state_brightness": dict(STATE_BRIGHTNESS_DEFAULTS)})

    def brightness_off(self):
        return bool(self.model and self.model.get("brightness_off", self.model["settings"]["brightness"] == 0))

    def source_changed(self, source, value):
        self.source_values[source] = value
        if source == "chatgpt-desktop":
            self.chatgpt_permission_hint.setVisible(value)
            if value:
                self.chatgpt_permission_hint.setText("正在检查 ChatGPT 桌面接入权限…")
        if self.model:

            def save_failed():
                if source == "chatgpt-desktop" and value:
                    self.chatgpt_permission_hint.setText(
                        "来源设置未保存，权限检查未开始。请重新勾选后重试。"
                    )

            self.act(
                "/api/settings",
                {"source_enabled": dict(self.source_values)},
                lambda _: (
                    self.check_chatgpt_permissions()
                    if source == "chatgpt-desktop" and value
                    else None
                ),
                save_failed,
            )

    def request_client_permissions(self):
        def received(data):
            self.chatgpt_permission_hint.setText(data["message"])
            self.chatgpt_permission_hint.show()
            self.check_clients()

        self.act("/api/desktop-clients/permissions", {}, received)

    def check_chatgpt_permissions(self):
        if self.chatgpt_permission_pending or not self.source_values.get(
            "chatgpt-desktop"
        ):
            return
        self.chatgpt_permission_pending = True

        def received(data):
            self.chatgpt_permission_pending = False
            if self.source_values.get("chatgpt-desktop"):
                self.chatgpt_permission_hint.setText(data["message"])
                self.chatgpt_permission_hint.show()
            self.check_clients()

        def failed():
            self.chatgpt_permission_pending = False
            if self.source_values.get("chatgpt-desktop"):
                self.chatgpt_permission_hint.setText(
                    "权限检查未完成，请点击「授权接入」重试。"
                )

        self.act("/api/desktop-clients/permissions", {}, received, failed)

    def delete_current_device(self):
        if (self.device_selection_loading or not self.selected_device_id
                or self.selected_device_model not in YEELIGHT_MODELS):
            return
        device_id, model_id = self.selected_device_id, self.selected_device_model
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
        reason = ("此设备亮度为 0%，请先提高灯光亮度。" if self.selected_device_brightness == 0 else self.light_block_reason())
        if reason:
            self.tell(reason, error=True)
            return
        self.act("/api/device/test-light", {"device_id": self.selected_device_id},
                 self.device_test_started)

    def device_test_started(self, data):
        self.device_test_deadlines[data["device_id"]] = time.monotonic() + data.get("remaining_ms", 10000) / 1000
        self.update_device_test_caption()

    def update_device_test_caption(self):
        remaining = self.device_test_deadlines.get(self.selected_device_id, 0) - time.monotonic()
        visible = remaining > 0 and not self.device_selection_loading
        self.device_test_caption.setVisible(visible)
        self.device_test_caption.setText(f"试亮剩余 {max(1, int(remaining + 0.999))} 秒" if visible else "")

    def manual(self, state):
        reason = self.light_block_reason()
        if reason:
            self.tell(reason, error=True)
            return
        self.stop_preview()
        self.manual_seq = self.manual_seq + 1 if self.manual_task else 1
        body = {"state": state, "seq": self.manual_seq}
        if self.manual_task:
            body["task_id"] = self.manual_task
        if state == "idle":
            body["reason"] = "cancelled"

        def received(data):
            self.manual_task = data.get("task_id") if state == "working" else None
            self.tell("测试已发送")

        self.act("/api/test", body, received)

    def navigate(self, index):
        self.device_view_epoch += 1
        if index == 1:
            self.show_device_selector()
        self.tabs.setCurrentIndex(index)

    def show_device_selector(self):
        self.device_view_epoch += 1
        self.device_details.hide()
        self.device_selector.show()
        self.breadcrumb.setText("我的设备")

    def open_device_details(self, model_id):
        model = hardware_model(model_id)
        self.selected_device_model = model_id
        self.device_model_title.setText(model["name"])
        self.device_model_description.setText(model["card_description"])
        self.yeelight_card.show()
        self.yeelight_history_heading.show()
        self.device_instance_panel.show()
        self.yeelight_colors_panel.show()
        self.device_status_card.setVisible(bool(self.selected_device_id))
        self.yeelight_add_panel.setVisible(not self.selected_device_id)
        self.yeelight_reconnect_button.setVisible(bool(self.selected_device_id))
        self.yeelight_delete_button.setVisible(bool(self.selected_device_id))
        self.yeelight_discover_button.setText("搜索 " + model["name"].removeprefix("Yeelight "))
        self.yeelight_light_hint.setText(model["light_description"])
        if self.tabs.currentIndex() == 1:
            self.breadcrumb.setText("我的设备 / " + model["name"])
        self.device_selector.hide()
        self.device_details.show()

    def choose_device_model(self, model_id):
        if not self.ready or not self.model:
            return
        self.flush_device_brightness()
        self.generation += 1
        self.selected_device_id = None
        self.port_restore_pending = True
        self.device_selection_loading = True
        self.device_operation_id = None
        self.connection_paused = False
        self.update_busy()
        self.selected_device_state = {"online": False}
        self.selected_device_brightness = 50
        self.open_device_details(model_id)
        self.yeelight_host.clear()
        self.yeelight_choices.clear()
        self.yeelight_choices.addItem("搜索后选择灯具…", "")
        self.rendered_yeelight_discovery = None
        self.poll(force=True)

    def choose_device_instance(self, _index):
        self.flush_device_brightness()
        self.generation += 1
        self.selected_device_id = self.device_instances.currentData() or ""
        self.port_restore_pending = True
        self.device_selection_loading = True
        self.device_operation_id = None
        self.connection_paused = False
        self.update_busy()
        self.selected_device_state = {"online": False}
        self.selected_device_brightness = 50
        self.yeelight_host.clear()
        self.yeelight_choices.clear()
        self.yeelight_choices.addItem("搜索后选择灯具…", "")
        self.rendered_yeelight_discovery = None
        self.poll(force=True)

    def device_query(self):
        values = {"model": self.selected_device_model}
        if self.selected_device_id is not None:
            values["device_id"] = self.selected_device_id
        return "/api/device?" + urlencode(values)

    def detail_device(self):
        if self.model and self.model.get("devices"):
            return next((item for item in self.model["devices"] if item["device_id"] == self.selected_device_id), self.selected_device_state)
        return self.model["device"] if self.model else {}

    def home_action(self):
        self.navigate(2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.built:
            columns = 1 if self.width() < 850 else 2
            if columns != self.device_card_columns:
                self.device_card_columns = columns
                for button in self.device_model_buttons.values():
                    self.device_cards_layout.removeWidget(button)
                for index, button in enumerate(self.device_model_buttons.values()):
                    self.device_cards_layout.addWidget(button, index // columns, index % columns)
            compact = self.width() < 1050
            self.scene_frame.setMaximumHeight(500 if compact else 16777215)
            self.home_top.setDirection(
                QBoxLayout.Direction.TopToBottom
                if compact
                else QBoxLayout.Direction.LeftToRight
            )

    def light_block_reason(self, source="manual"):
        if not self.ready or not self.model:
            return "本地服务尚未连接，请稍后重试。"
        if self.job_busy or self.model["maintenance"]:
            return "设备正在维护，请等待完成。"
        if self.test_busy:
            return "诊断正在进行，请等待本次测试完成。"
        settings = self.model["settings"]
        if settings["paused"]:
            return "所有触发已暂停，请在提醒偏好中恢复。"
        if self.brightness_off():
            return "亮度为 0%，请先提高灯光亮度。"
        if quiet_now(settings):
            return "当前处于静默时段，请先在提醒偏好中关闭定时静默。"
        if source is not None and not settings["source_enabled"].get(source, False):
            return "该来源已关闭，请在「应用接入」中开启后重试。"
        return ""

    def start_preview(self):
        QToolTip.hideText()
        if self.preview_phase or self.hardware_preview_id:
            self.stop_preview()
            return
        if self.hardware_mode():
            reason = self.light_block_reason(source=None)
            if not reason and not self.model["device"]["online"]:
                reason = "请先连接设备，再开始同步灯效演示。"
            if reason:
                QToolTip.showText(
                    self.preview_button.mapToGlobal(self.preview_button.rect().topLeft()),
                    t(reason), self.preview_button, self.preview_button.rect(), 5000,
                )
                return
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
        if elapsed >= 10:
            self.stop_preview()
            return
        self.preview_phase = "working" if elapsed < 5 else "done"
        self.preview_caption.show()
        self.preview_caption.setText(
            f"{max(1, 10 - int(elapsed))} 秒后结束"
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
            self.display_title.setText("当前无任务")
            return
        data = self.model
        settings, state = data["settings"], data["display"]["state"]
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
        elif settings["paused"]:
            title = "已暂停"
        elif quiet_now(settings):
            title = "静默中"
        else:
            title = NAMES.get(state, "当前无任务")
        self.display_title.setText(title)

    def render_notice(self):
        data, settings = self.model, self.model["settings"]
        if data["maintenance"]:
            kind, text, action = (
                "maintenance",
                "设备连接操作进行中，请稍候。",
                "查看进度",
            )
        elif settings["paused"]:
            kind, text, action = (
                "paused",
                "已暂停灯光触发，活动记录仍会更新。",
                "恢复提醒",
            )
        elif self.brightness_off():
            kind, text, action = (
                "brightness",
                "灯光亮度为 0%，请在「我的设备」中调整；事件仍会记录。",
                "调节亮度",
            )
        elif quiet_now(settings):
            kind, text, action = (
                "quiet",
                "全天静默已开启，屏幕与实体灯提醒已关闭。"
                if settings["quiet_start"] == settings["quiet_end"]
                else f"正处于静默时段，{settings['quiet_end']} 后恢复提醒。",
                "调整时段",
            )
        elif data["display"]["state"] == "error":
            kind, text, action = ("task", "任务报告异常，请查看对应应用。", "查看记录")
        else:
            self.notice_kind = None
            self.notice_box.hide()
            return
        self.notice_kind = kind
        self.notice_text.setText(text)
        self.notice_action.setText(action)
        self.notice_box.show()

    def resolve_notice(self):
        if self.notice_kind == "paused":
            self.setting("paused", False)
        elif self.notice_kind == "brightness":
            if self.hardware_mode():
                self.navigate(1)
            else:
                self.setting("brightness", 20)
        elif self.notice_kind == "quiet":
            self.navigate(3)
        elif self.notice_kind == "task":
            self.navigate(4)
        elif self.notice_kind == "service":
            self.poll(force=True)
        else:
            self.navigate(1)

    def render(self, data):
        chosen_language = data["settings"].get("language", "zh-CN")
        if chosen_language != language():
            set_language(chosen_language)
            self.history_key = None
        self.sidebar.setFixedWidth(250 if chosen_language == "en" else 198)
        self.duration.setFixedWidth(140 if chosen_language == "en" else 105)
        with QSignalBlocker(self.language_choice):
            self.language_choice.setCurrentIndex(self.language_choice.findData(chosen_language))
        for item in data.get("devices", []):
            self.device_test_deadlines[item["device_id"]] = time.monotonic() + item.get("test_remaining_ms", 0) / 1000
        self.update_device_test_caption()
        was_hardware = self.hardware_mode()
        self.model = data
        if self.hardware_mode() != was_hardware:
            self.stop_preview()
        device, settings = (
            data["device"],
            data["settings"],
        )
        hardware = self.hardware_mode()
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
        self.render_mode_choice()
        self.scene_title.setText("灯光状态" if hardware else "虚拟烛火")
        self.hardware_notice.setVisible(hardware and not online and not connecting)
        self.hardware_notice_text.setText(
            "设备维护中，硬件灯光暂不可用。" if data["maintenance"]
            else "尚未连接设备，硬件灯光暂不可用。"
        )
        self.hardware_notice_action.setText("查看进度" if data["maintenance"] else "连接设备")
        self.virtual_device_card.setVisible(not hardware)
        self.hardware_controls.setVisible(hardware)
        self.device_badge.setText(
            "虚拟模式 · 屏幕提醒"
            if not hardware
            else
            "设备维护中"
            if data["maintenance"] and not online
            else connected_title
            if online
            else "正在连接设备" if connecting else "○  硬件未连接"
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
        selected = self.detail_device()
        selected_online = hardware and selected.get("online", False)
        self.device_summary.setText("设备已连接" if selected_online else selected.get("status", "设备尚未连接"))
        self.device_version.setText(
            "灯光固件 " + str(selected.get("firmware_version") or "未知（设备未上报）")
            + " · 协议 " + str(selected.get("protocol_version") or "未知")
            if selected_online else selected.get("last_error") or "连接后读取固件版本"
        )
        self.device_summary_icon.setVisible(selected_online)
        if selected_online:
            selected_transport = "wifi"
            self.device_summary_icon.setPixmap(icon(selected_transport, "#38735b", 18).pixmap(18, 18))
        self.render_scene()
        self.render_notice()
        if not self.writing and not self.brightness_timer.isActive():
            self.source_values = dict(settings["source_enabled"])
            if not self.duration.hasFocus():
                with QSignalBlocker(self.duration):
                    self.duration.setValue(settings["duration"])
            for key, check in self.checks.items():
                with QSignalBlocker(check):
                    check.setChecked(settings[key])
            for key, edit in self.times.items():
                if not edit.hasFocus():
                    with QSignalBlocker(edit):
                        edit.setTime(QTime.fromString(settings[key], "HH:mm"))
                edit.setEnabled(settings["quiet_enabled"])
            self.quiet_times_box.setVisible(settings["quiet_enabled"])
            self.quiet_note.setText(
                "开始与结束相同表示全天静默。"
                if settings["quiet_start"] == settings["quiet_end"]
                else ""
            )
        enabled_sources = {source["id"]: source["name"].replace("（实验）", "")
                           for source in data["sources"] if source["enabled"]}
        for source, button in self.home_source_icons.items():
            button.setVisible(source in enabled_sources)
            button.setToolTip("已开启：" + enabled_sources.get(source, ""))
        for group, sources in self.home_source_groups.values():
            group.setVisible(any(source in enabled_sources for source in sources))
        visible = any(source in enabled_sources for source in self.home_source_icons)
        self.home_sources_row.setVisible(visible)
        self.home_sources_empty.setVisible(not visible)
        for source in data["sources"]:
            sid = source["id"]
            if sid not in self.source_checks:
                continue
            if not self.writing:
                with QSignalBlocker(self.source_checks[sid]):
                    self.source_checks[sid].setChecked(source["enabled"])
            text = "等待新任务" if source["enabled"] else "已关闭"
            evidence = data.get("verification", {}).get(sid)
            if evidence and source["enabled"]:
                text = VERIFICATION_STATUS.get(evidence["status"], "等待新任务")
            self.source_status[sid].setText(text)
            detail_status = (
                "disabled" if not source["enabled"]
                else evidence["status"] if evidence else "waiting"
            )
            self.source_details[sid].setText(
                source["name"] + "：" + VERIFICATION_DETAILS.get(detail_status, text)
            )
        key = (
            [(e["id"], e["status"], e["reason"], e.get("delivery_summary")) for e in data["events"]],
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
        count = f"已确认 {confirmed}/{len(devices)} 台设备"
        return count if event["status"] == "applied" else result + " · " + count

    def event_search_text(self, event):
        values = list(self.event_values(event))
        for device in (event.get("delivery_summary") or {}).get("devices", []):
            values.extend((self.delivery_device_name(device), device["reason"]))
        return " ".join(values + [t(value) for value in values]).casefold()

    def render_recent(self):
        events = self.model["events"][:len(self.recent_rows)]
        self.recent_empty.setVisible(not events)
        full = len(events) == len(self.recent_rows)
        for index, (gap_index, gap) in enumerate(self.recent_gaps):
            gap.changeSize(0, 8 if index < len(events) - 1 else 0)
            self.recent_list.setStretch(gap_index, 1 if full else 0)
        self.recent_list.setStretch(self.recent_list.count() - 1, 0 if full else 1)
        self.recent_list.invalidate()
        for index, labels in enumerate(self.recent_rows):
            self.recent_row_frames[index].setVisible(index < len(events))
            if index < len(events):
                event = events[index]
                values = list(self.event_values(event))
                values[0] = values[0][-8:]
                detail = values[3]
                if event["status"] != "applied":
                    values[3] = RESULTS.get(event["status"], event["status"])
                for item, value in zip(labels, values):
                    item.setText(value)
                labels[3].setToolTip(detail)
                restyle(labels[2], "recentTone", event["state"])

    def render_history(self):
        if not self.model:
            return
        query = self.history_search.text().strip().casefold()
        kind = self.history_filter.currentData()
        events = [
            e
            for e in self.model["events"]
            if (kind == "all" or is_test(e) == (kind == "test"))
            and (not query or query in self.event_search_text(e))
        ]
        selection = self.history.currentRow()
        selected_id = (
            self.visible_events[selection]["id"]
            if self.history.selectionModel().hasSelection()
            and 0 <= selection < len(self.visible_events)
            else None
        )
        self.visible_events = events
        with QSignalBlocker(self.history):
            self.history.setRowCount(len(events))
            selected_row = -1
            for row, event in enumerate(events):
                if event["id"] == selected_id:
                    selected_row = row
                for column, value in enumerate(self.event_values(event)):
                    item = QTableWidgetItem(value)
                    if column == 3:
                        item.setToolTip(t(value))
                    if column == 3 and event["status"] == "applied":
                        item.setForeground(QColor("#38735b"))
                    elif column == 2 and event["state"] == "error":
                        item.setForeground(QColor("#a94c2b"))
                    self.history.setItem(row, column, item)
            if selected_row >= 0:
                self.history.selectRow(selected_row)
            else:
                self.history.clearSelection()
                self.history.setCurrentCell(-1, -1)
        self.history_empty.setVisible(not events)
        self.history_empty.setText(
            "没有符合筛选条件的记录，试试其他关键词或事件类型。"
            if self.model["events"]
            else "还没有记录。接入应用后，新任务会自动出现在这里。"
        )
        self.history_count.setText(
            f"显示 {len(events)} 条 / 最近 {len(self.model['events'])} 条事件"
        )
        self.show_event_detail()

    def show_event_detail(self):
        row = self.history.currentRow()
        if not self.history.selectionModel().hasSelection() or not 0 <= row < len(self.visible_events):
            self.event_detail_box.hide()
            self.event_detail.clear()
            return
        self.event_detail_box.show()
        event = self.visible_events[row]
        def paragraph(text, style):
            return '<p style="' + style + '">' + escape(text) + '</p>'

        secondary = "font-size:12px; color:#75736b; font-weight:400;"
        heading = "font-size:14px; font-weight:600; color:#292824; margin-top:18px; margin-bottom:8px;"
        blocks = [paragraph(" · ".join(self.event_values(event)[:3]), secondary)]
        blocks.append(paragraph("处理结果：", "font-size:11px; color:#75736b; margin-bottom:4px;"))
        blocks.append(paragraph(self.event_delivery_result(event),
                                "font-size:20px; font-weight:600; color:#292824; margin-top:0px; margin-bottom:8px;"))
        summary = event.get("delivery_summary") or {}
        if event["status"] != "applied":
            reason = summary.get("reason") or DELIVERY_REASONS.get(event["status"])
            if reason:
                blocks.append(paragraph(reason, secondary))
        devices = summary.get("devices", [])
        if devices:
            blocks.append(paragraph("设备回执", heading))
            for device in devices:
                color = "#38735b" if device["status"] == "applied" else "#75736b"
                blocks.append('<p style="font-size:13px; font-weight:600; color:#292824; margin-top:0px; margin-bottom:10px;">'
                              + escape(device["name"]) + '<span style="' + secondary + '">（'
                              + escape(device["device_id"]) + '）</span><br>'
                              + '<span style="font-size:12px; font-weight:400; color:' + color + ';">'
                              + escape(device["reason"]) + '</span></p>')
        statuses = {"pending": "等待桌面应用领取", "claimed": "桌面应用处理中", "submitted": "已提交系统（显示取决于系统通知设置）",
                    "denied": "系统通知权限未允许", "unavailable": "系统通知不可用", "failed": "投递失败", "disabled": "已关闭",
                    "not_requested": "本事件无需弹窗", "interrupted": "进程重启，未重播", "unconfirmed": "提交结果未确认", "superseded": "任务状态已变化"}
        notifications = [statuses.get(receipt["status"], RESULTS.get(receipt["status"], receipt["status"]))
                         for receipt in self.model.get("deliveries", [])
                         if receipt["event"] == event["id"] and receipt["output"] == "desktop"]
        if notifications:
            blocks.append(paragraph("桌面通知", heading))
            blocks.extend(paragraph(notification, secondary) for notification in notifications)
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
                writer.writerow(t(value) for value in ("时间", "应用 / 来源", "任务状态", "处理结果"))
                writer.writerows([t(value) for value in self.event_values(e)] for e in self.visible_events)
            self.tell(f"已导出 {len(self.visible_events)} 条记录。")
        except OSError:
            self.on_error("保存失败，请检查所选目录的权限。")

    def poll(self, force=False):
        if not force and not self.isVisible():
            return
        if not self.ready:
            self.api.request("/", self.connected, raw=True)
            return
        generation = self.generation

        def received(data):
            if generation == self.generation:
                self.render(data)
            else:
                self.poll(force=True)

        self.api.request("/api/status", received)
        if (self.tabs.currentIndex() == 1 and not self.device_details.isHidden()) or self.job_busy:
            self.api.request(self.device_query(), lambda data: self.render_device(data) if generation == self.generation else None)
        if self.tabs.currentIndex() == 5 or self.test_busy:
            self.api.request("/api/trigger-test", self.render_test)
        if self.tabs.currentIndex() == 2:
            self.check_clients()

    def showEvent(self, event):
        super().showEvent(event)
        self.poll_timer.start()
        self.poll(force=True)

    def hideEvent(self, event):
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
        if index == 2:
            self.check_cli()
            self.check_codex()
        self.poll(force=True)


    def device_action(self, action, callback=None, **values):
        if self.device_selection_loading:
            self.tell("正在读取此设备，请稍候。", error=True)
            return
        if not self.hardware_mode():
            self.tell("请先在总览切换至硬件模式。", error=True)
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
            self.job_message.show()

        self.act(
            "/api/device",
            {"action": action, "hardware_model": self.selected_device_model, "device_id": self.selected_device_id or "", **values},
            received,
            failed,
        )






    def choose_yeelight_address(self, _index):
        host = self.yeelight_choices.currentData()
        if host:
            self.yeelight_host.setText(host)

    def render_device(self, data):
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
            self.selected_device_brightness = data.get("light_settings", {}).get("brightness", 50)
            self.device_light_hint.setVisible(not self.selected_device_id)
            from aiflame.devices.light_settings import COLOR_DEFAULTS
            color_values = data.get("light_settings", {}).get("colors", COLOR_DEFAULTS)
            from aiflame.devices.light_settings import STATE_BRIGHTNESS_DEFAULTS
            state_brightness = data.get("light_settings", {}).get("state_brightness", STATE_BRIGHTNESS_DEFAULTS)
            if not self.writing and not self.brightness_timer.isActive():
                for state, control in self.yeelight_brightness_controls.items():
                    if not control.isSliderDown():
                        value = state_brightness.get(state, STATE_BRIGHTNESS_DEFAULTS[state])
                        with QSignalBlocker(control):
                            control.setValue(value)
                        self.yeelight_brightness_labels[state].setText(f"{value}%")
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
            self.device_instance_panel.show()
            with QSignalBlocker(self.device_instances):
                self.device_instances.clear()
                self.device_instances.addItem("尚未添加设备" if yeelight and not has_history else "添加此型号设备…", "")
                for item in data.get("devices", []):
                    state = "已连接" if item.get("online") else "已断开" if item.get("connection_paused") else "离线"
                    self.device_instances.addItem(item["name"] + " · " + item["device_id"] + " · " + state, item["device_id"])
                self.device_instances.setCurrentIndex(max(0, self.device_instances.findData(self.selected_device_id)))
            if self.port_restore_pending and self.selected_device_model in YEELIGHT_MODELS:
                self.yeelight_host.setText(data.get("yeelight_host", ""))
                self.port_restore_pending = False
        job = data.get("job")
        if "hardware_model" in data and not (job and job["state"] == "running"):
            if not self.device_details.isHidden():
                self.open_device_details(data["hardware_model"])
        job = data.get("job")
        if job:
            if job.get("action") == "discover" and job["state"] == "succeeded" and self.selected_device_model in YEELIGHT_MODELS and job.get("hardware_model", self.selected_device_model) == self.selected_device_model and self.rendered_yeelight_discovery != job.get("id"):
                self.rendered_yeelight_discovery = job.get("id")
                with QSignalBlocker(self.yeelight_choices):
                    self.yeelight_choices.clear()
                    rows = job.get("result", {}).get("devices", [])
                    self.yeelight_choices.addItem("选择灯具…" if rows else "未发现，可手动填写 IP", "")
                    for item in rows:
                        self.yeelight_choices.addItem(item["name"] + " · " + item["host"], item["host"])
            self.job_busy = job["state"] == "running"
            self.progress.setVisible(self.job_busy)
            self.job_message.show()
            self.progress.setValue(job["progress"])
            self.job_message.setText(job["message"])
            self.job_message.setStyleSheet(
                "color: #a0472c;" if job["state"] == "failed" else ""
            )
        self.update_busy()

    def trigger(self, source):
        reason = self.light_block_reason(source)
        if reason:
            self.test_message.setText(reason)
            self.tell(reason, error=True)
            return

        def received(_):
            self.test_busy = True
            self.test_message.setText("正在测试…")
            self.test_details.clear()
            self.update_busy()

        self.act(
            "/api/trigger-test",
            {"source": source},
            received,
            lambda: self.test_message.setText(source_text(self.message)),
        )

    def render_test(self, data):
        job = data.get("job")
        if job:
            self.test_busy = job["state"] == "running"
            text = [job["message"]]
            text += [
                NAMES.get(e["state"], e["state"])
                + " · "
                + RESULTS.get(e["status"], e["status"])
                for e in job.get("delivery", [])
            ]
            self.test_details.setText("\n".join(text))
            self.test_message.setText(
                "正在测试…" if self.test_busy
                else job["message"] if job["state"] == "failed"
                else "测试已完成"
            )
        self.update_busy()

    def update_busy(self):
        if not self.built:
            return
        connected = self.ready and self.model is not None
        busy = (
            self.reset_pending
            or self.job_busy
            or self.test_busy
            or self.writing
            or bool(self.model and self.model["maintenance"])
        )
        self.reset_button.setEnabled(connected and not busy)
        hardware = connected and self.hardware_mode() and not self.device_selection_loading
        for button in self.device_model_buttons.values():
            button.setEnabled(connected and not self.reset_pending)
        for button in self.mode_buttons.values():
            button.setEnabled(connected and not busy)
        for button in self.trigger_buttons:
            button.setEnabled(connected and not busy)
        for button in self.maintenance_buttons:
            button.setEnabled(hardware and not busy)
        device = self.detail_device()
        online = hardware and device.get("online", False)
        connecting = device.get("connecting", False)
        available = hardware and not busy and not connecting
        self.yeelight_discover_button.setEnabled(hardware and not busy)
        self.yeelight_connect_button.setEnabled(available and not self.selected_device_id and bool(self.yeelight_host.text().strip()))
        self.yeelight_reconnect_button.setEnabled(available and not online and bool(self.selected_device_id) and bool(self.yeelight_host.text().strip()))
        self.yeelight_reconnect_button.setText("设备已连接" if online else "连接此设备")
        self.device_instances.setEnabled(connected and not busy)
        color_enabled = connected and not self.writing and not self.device_selection_loading and bool(self.selected_device_id) and self.selected_device_model in YEELIGHT_MODELS
        for control in [*self.yeelight_color_choices.values(), *self.yeelight_color_buttons.values(),
                        *self.yeelight_brightness_controls.values(), self.yeelight_color_reset]:
            control.setEnabled(color_enabled)
        if self.selected_device_model in YEELIGHT_MODELS:
            self.yeelight_add_panel.setVisible(not self.selected_device_id)
            self.device_status_card.setVisible(bool(self.selected_device_id))
            self.yeelight_reconnect_button.setVisible(bool(self.selected_device_id))
        self.disconnect_button.setEnabled(hardware and not busy and not self.connection_paused and (online or connecting or bool(self.selected_device_id)))
        self.yeelight_delete_button.setEnabled(connected and not busy and not self.device_selection_loading
                                             and bool(self.selected_device_id) and self.selected_device_model in YEELIGHT_MODELS)
        reason = self.light_block_reason()
        for button in self.manual_buttons:
            button.setEnabled(not self.writing and not reason)
            button.setToolTip(reason)
        self.try_light_button.setEnabled(
            hardware and not self.writing
            and not reason
            and online and bool(self.selected_device_id)
        )
        if not online or not self.selected_device_id:
            self.try_light_button.setToolTip("请先连接当前设备；也可以在屏幕预览灯效。")
        self.manual_hint.setText(reason)
        self.manual_hint.setVisible(bool(reason))
        for button in self.source_action_buttons:
            button.setEnabled(connected and not self.writing)
        self.permission_button.setEnabled(
            connected and not self.writing and self.clients_supported
        )
        for check in self.source_checks.values():
            check.setEnabled(connected and not self.reset_pending)
        for control in [
            self.duration,
            *self.checks.values(),
        ]:
            control.setEnabled(connected and not self.reset_pending)
        for edit in self.times.values():
            edit.setEnabled(connected and self.checks["quiet_enabled"].isChecked())

    def integration_parameters(self, source):
        body = {"source": source}
        if source in self.cli_config_dirs:
            directory = self.cli_config_dirs[source].text().strip()
            if directory:
                body["config_dir"] = directory
        if source in self.cli_profiles:
            profile = self.cli_profiles[source].currentData()
            if profile:
                body["profile"] = profile
        return body

    def load_integration_guide(self, source):
        if source not in self.integration_guides:
            return
        query = urlencode(self.integration_parameters(source))
        self.integration_copy_buttons[source].setEnabled(False)

        def received(data):
            if query != urlencode(self.integration_parameters(source)):
                return
            from aiflame.integrations.cli_tools.guidance import guide_text
            self.integration_plans[source] = data
            self.integration_guides[source].setText(guide_text(data))
            self.integration_manual_guides[source].setText(data["manual"] + "\n\n" + "\n".join(data["preparation"]))
            self.integration_snippets[source].setPlainText(data["snippet"])
            self.integration_copy_buttons[source].setText(data["copy_label"])
            self.integration_copy_buttons[source].setEnabled(True)

        self.api.request("/api/integration-guide?" + query, received,
                         failed=lambda: self.integration_guides[source].setText("无法加载接入步骤，请检查 SeeEnd 服务后重新展开。"))

    def copy_integration_config(self, source):
        from PySide6.QtWidgets import QApplication
        plan = self.integration_plans.get(source)
        if plan and self.integration_copy_buttons[source].isEnabled():
            QApplication.clipboard().setText(plan["snippet"])
            self.integration_copy_buttons[source].setText("已复制")

    def open_web_app(self, source):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from aiflame.integrations.chatgpt_web.host import WEB_URLS
        QDesktopServices.openUrl(QUrl(WEB_URLS[source]))

    def open_integration_docs(self, source):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from aiflame.integrations.cli_tools.guidance import DOCS
        QDesktopServices.openUrl(QUrl(DOCS[source]))

    @staticmethod
    def integration_report_text(data):
        lines = [data.get("summary", "配置已就绪，尚需真实任务验证。" if data.get("configuration_ready") else "接入配置有问题。")]
        if data.get("next_action"):
            lines.append("下一步：" + data["next_action"])
        for check in data.get("checks", []):
            mark = {"ok": "✓", "error": "!", "warning": "!", "pending": "○"}.get(check["status"], "○")
            lines.append(mark + " " + check["message"] + ("\n  " + check["action"] if check.get("action") else ""))
        if not data.get("checks"):
            lines.extend(data.get("issues", []))
        lines.extend(s["name"] + f"：收到 {s['received']} / 设备确认 {s['applied']}" for s in data.get("signals", {}).values())
        limitations = data.get("limitations", [])
        lines.extend([limitations] if isinstance(limitations, str) else limitations)
        if data.get("evidence_scope"):
            lines.append(data["evidence_scope"])
        return "\n".join(lines)

    def install_integration(self, source):
        target = self.codex_message if source == "codex" else self.browser_message
        target.setText("正在增量配置连接，请稍候…")
        target.show()

        def received(data):
            message = data.get("message", "连接配置已更新，请发起新任务。")
            if source == "codex":
                self.codex_action_notice = message
                target.setText(message)
                self.codex_details.setText(message)
                self.check_codex()
            else:
                self.browser_details.setText(message)
                target.setText("请加载或重新加载扩展，并刷新 AI 网页。")

        self.act("/api/integrations/install",
                 self.integration_parameters(source) if source == "codex" else {"source": source},
                 received, lambda: target.setText(source_text(self.message)))

    def cli_action(self, source, action):
        body = self.integration_parameters(source)
        if source in self.cli_profiles and "profile" not in body:
            self.cli_messages[source].setText("请选择实际使用的已有 Profile。")
            return
        target = self.cli_messages[source]
        target.setText("正在增量配置…" if action == "install" else "正在移除本应用接入…")
        self.act("/api/integrations/" + action, body,
                 lambda data: (target.setText(data.get("message", "接入已更新。")), self.check_cli(source)),
                 lambda: target.setText(source_text(self.message)))

    def check_cli(self, source=None):
        query = urlencode(self.integration_parameters(source)) if source else None
        if source:
            self.cli_messages[source].setText("正在检查配置和真实任务接收记录…")

        def received(data):
            if source and query != urlencode(self.integration_parameters(source)):
                return
            for item in data.get("integrations", [data] if source else []):
                current = item["source_id"]
                if current not in self.cli_messages or (source and source != current):
                    continue
                notes = []
                if item.get("config_dir"):
                    notes.append("配置目录：" + item["config_dir"])
                if item.get("installed_profiles"):
                    notes.append("已连接 Profile：" + "、".join(item["installed_profiles"]))
                self.cli_details[current].setText(self.integration_report_text(item) + "\n" + "\n".join(notes))
                self.cli_messages[current].setText(item.get("feedback") or item.get("summary", "配置已就绪，尚需真实任务验证。" if item["configuration_ready"] else "接入配置有问题：" + "；".join(item.get("issues", [])[:1])))
                self.integration_connect_buttons[current].setText(item.get("connect_label", "连接"))
                self.cli_messages[current].setStyleSheet("color: #397052;" if item.get("status") == "connected" else "color: #a0472c;" if item.get("status") in ("configuration_error", "disabled") else "")
                if current in self.cli_profiles:
                    selector = self.cli_profiles[current]
                    selected = selector.currentData() or item.get("profile")
                    blocker = QSignalBlocker(selector)
                    selector.clear()
                    selector.addItem("请选择已有 Profile", "")
                    for profile in item.get("profiles", []):
                        selector.addItem(profile, profile)
                    if not selected and len(item.get("profiles", [])) == 1:
                        selected = item["profiles"][0]
                    index = selector.findData(selected)
                    if index >= 0:
                        selector.setCurrentIndex(index)
                    del blocker
                    self.load_integration_guide(current)

        def failed():
            for current, message in self.cli_messages.items():
                if source is None or source == current:
                    message.setText("检查未完成：" + source_text(self.message))
        self.api.request("/api/integration-check?" + query if source else "/api/cli-integrations", received, failed=failed)

    def check_codex(self):
        query = urlencode(self.integration_parameters("codex"))
        self.codex_message.setText("正在检查 Codex 配置和真实任务接收记录…")
        self.codex_message.show()

        def received(data):
            if query != urlencode(self.integration_parameters("codex")):
                return
            self.codex_message.setText(data.get("feedback") or data.get("summary", "配置已就绪，尚需真实任务验证。" if data.get("configuration_ready") else "接入配置有问题。"))
            self.integration_connect_buttons["codex"].setText(data.get("connect_label", "连接"))
            self.codex_message.setStyleSheet("color: #397052;" if data.get("status") == "connected" else "color: #a0472c;" if data.get("status") in ("configuration_error", "disabled") else "")
            details = self.integration_report_text(data)
            self.codex_details.setText(details)
            self.codex_connection_details.setText(details)
            self.load_integration_guide("codex")

        self.api.request("/api/integration-check?" + query, received,
                         failed=lambda: self.codex_message.setText("检查未完成：" + source_text(self.message)))

    def check_clients(self):
        def received(data):
            self.clients_supported = data["supported"]
            self.permission_button.setEnabled(data["supported"] and not self.writing)
            lines = [
                "辅助功能："
                + ("已授权" if data["permissions"]["accessibility"] else "未授权")
                + " · 输入监控："
                + ("已授权" if data["permissions"]["input_monitoring"] else "未授权")
                if data["supported"]
                else "当前系统暂不支持桌面客户端接入。"
            ]
            lines += [
                c["name"]
                + (" " + " / ".join(c.get("versions", [])) if c.get("versions") else "")
                + " · "
                + CLIENT_STATUS.get(c["status"], "等待检查")
                + (" · 正在跟踪" if c.get("tracking") else "")
                for c in data["clients"]
            ]
            self.client_details.setText("\n".join(lines))
            missing = []
            if not data["permissions"]["accessibility"]:
                missing.append("辅助功能")
            if not data["permissions"]["input_monitoring"]:
                missing.append("输入监控")
            summary = (
                "当前系统暂不支持桌面客户端接入。" if not data["supported"]
                else "请授权：" + "、".join(missing) if missing else ""
            )
            actionable = {
                "input_unavailable": "请重启 SeeEnd 并检查权限",
                "controls_unavailable": "请打开对话窗口；仍无响应时检查版本兼容性",
                "ambiguous": "请关闭多余的客户端进程",
                "not_installed": "未找到应用，请先安装",
                "waiting": "请打开应用",
                "unsupported": "当前系统暂不支持",
            }
            if data["supported"] and not missing:
                summary = "\n".join(
                    client["name"] + "：" + actionable[client["status"]]
                    for client in data["clients"]
                    if self.source_values.get(client["source_id"])
                    and client["status"] in actionable
                )
            self.client_message.setText(summary)
            self.client_message.setVisible(bool(summary))

        self.api.request("/api/desktop-clients", received)

    def download(self, endpoint, filename):
        path, _ = QFileDialog.getSaveFileName(
            self, t("保存下载包"), filename, t("ZIP 文件 (*.zip)")
        )
        if not path:
            return

        def received(data):
            try:
                Path(path).write_bytes(data)
                self.tell("文件已保存。")
            except OSError:
                self.on_error("保存失败，请检查所选目录的权限。")

        self.api.request(endpoint, received, raw=True)


    def shutdown(self):
        self.device_test_timer.stop()
        self.poll_timer.stop()
        self.brightness_timer.stop()
        self.preview_timer.stop()
        self.message_timer.stop()
        self.flame.animation_timer.stop()
        self.actions.clear()
        self.api.close()
