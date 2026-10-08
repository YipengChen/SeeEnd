"""Native dashboard controller with bounded polling and stable controls."""

import csv
from collections import deque
from datetime import datetime
from pathlib import Path
import time
import uuid
from urllib.parse import urlencode

from PySide6.QtCore import Qt, QSignalBlocker, QTime, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QBoxLayout, QFileDialog, QTableWidgetItem, QWidget

from .components import icon, restyle
from .network import LocalAPI
from .theme import STYLE
from .views import DashboardViews, PAGES
from aiflame.devices.models import YEELIGHT_MODELS, hardware_model

NAMES = {
    "idle": "待机",
    "working": "进行中",
    "done": "已完成",
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
}
VERIFICATION_STATUS = {
    "needs_input": "等待输入",
    "unknown": "状态未知",
    "waiting": "等待新任务",
    "working": "进行中",
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
            "连接火焰灯接收任务提醒，屏幕同步显示状态。" if self.hardware_mode()
            else "屏幕烛火随任务变化，不查找或连接设备。"
        )

    def brightness_changed(self, value, sender=None):
        for control in self.brightness_controls:
            if control is not sender:
                with QSignalBlocker(control):
                    control.setValue(value)
        for value_label in self.brightness_labels:
            value_label.setText(f"{value}%")
        self.brightness_note.setVisible(value == 0)
        if self.model and self.selected_device_id and not self.device_selection_loading:
            self.pending_device_brightness = {"hardware_model": self.selected_device_model,
                                             "device_id": self.selected_device_id,
                                             "settings": {"brightness": value}}
            self.brightness_timer.start()

    def flush_device_brightness(self):
        self.brightness_timer.stop()
        pending, self.pending_device_brightness = self.pending_device_brightness, None
        if pending:
            self.act("/api/device/light-settings", pending)

    def device_light_setting(self, key, value):
        if self.ready and self.model and self.selected_device_id and not self.device_selection_loading:
            self.flush_device_brightness()
            self.act("/api/device/light-settings", {"hardware_model": self.selected_device_model,
                     "device_id": self.selected_device_id, "settings": {key: value}})

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

    def test_current_device(self):
        if self.device_selection_loading or not self.selected_device_id or not self.detail_device().get("online"):
            self.tell("请先连接当前设备", error=True)
            return
        reason = ("此设备亮度为 0%，请先提高上方灯光亮度。" if self.brightness.value() == 0 else self.light_block_reason())
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
        self.device_model_description.setText(model["description"])
        yeelight = model_id in YEELIGHT_MODELS
        self.yeelight_card.setVisible(yeelight)
        if yeelight:
            self.yeelight_discover_button.setText("搜索 " + model["name"].removeprefix("Yeelight "))
            self.yeelight_light_hint.setText(model["light_description"])
        for widget in (self.matrix_connection_card, self.matrix_wifi_card, self.matrix_maintenance_card):
            widget.setVisible(not yeelight)
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
        self.wifi_available = False
        self.connection_paused = False
        self.wifi_scan_running = False
        with QSignalBlocker(self.wifi_choices):
            self.wifi_choices.clear()
        self.update_busy()
        self.selected_device_state = {"online": False}
        self.open_device_details(model_id)
        self.yeelight_host.clear()
        self.yeelight_choices.clear()
        self.rendered_yeelight_discovery = None
        self.password.clear()
        self.ssid.clear()
        self.confirm_board.setChecked(False)
        self.confirm_flash.setChecked(False)
        self.ports.clear()
        self.ports.addItem("点击刷新，查找 USB 设备", "")
        self.scan_ports()
        self.poll(force=True)

    def choose_device_instance(self, _index):
        self.flush_device_brightness()
        self.generation += 1
        self.selected_device_id = self.device_instances.currentData() or ""
        self.port_restore_pending = True
        self.device_selection_loading = True
        self.device_operation_id = None
        self.wifi_available = False
        self.connection_paused = False
        self.wifi_scan_running = False
        with QSignalBlocker(self.wifi_choices):
            self.wifi_choices.clear()
        self.update_busy()
        self.selected_device_state = {"online": False}
        self.yeelight_host.clear()
        self.yeelight_choices.clear()
        self.rendered_yeelight_discovery = None
        self.password.clear()
        self.ssid.clear()
        self.confirm_board.setChecked(False)
        self.confirm_flash.setChecked(False)
        self.ports.clear()
        self.ports.addItem("点击刷新，查找 USB 设备", "")
        self.scan_ports()
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
            self.home_top.setDirection(
                QBoxLayout.Direction.TopToBottom
                if compact
                else QBoxLayout.Direction.LeftToRight
            )
            # Keep the flame and its preview controls fully inside the scene
            # when the overview stacks vertically on a narrow window.
            scene_height = max(380, self.scene_frame.layout().minimumSize().height())
            self.scene_frame.setMinimumHeight(scene_height if compact else 0)
            self.scene_frame.setMaximumHeight(scene_height if compact else 16777215)

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
        if self.preview_phase or self.hardware_preview_id:
            self.stop_preview()
            return
        if self.hardware_mode():
            reason = self.light_block_reason(source=None)
            if not reason and not self.model["device"]["online"]:
                reason = "请先连接设备，再开始同步灯效演示。"
            if reason:
                self.tell(reason, error=True)
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
            self.scene_badge.setText("演示 · 仅屏幕")
            self.scene_badge.show()
            self.flame.set_state(self.preview_phase, True, reduced=reduced)
            self.display_title.setText(NAMES[self.preview_phase])
            return
        if not self.model or not self.ready:
            self.poll_timer.setInterval(1500)
            self.preview_button.setText("灯效演示")
            self.preview_caption.hide()
            self.scene_badge.hide()
            self.flame.set_state("idle", reduced=reduced)
            self.display_title.setText("待机")
            return
        data = self.model
        settings, state = data["settings"], data["display"]["state"]
        preview = data["display"].get("preview")
        if preview and self.hardware_preview_id == preview:
            self.poll_timer.setInterval(150)
            confirmed = data["display"].get("device_confirmed")
            self.scene_badge.setText("演示 · 屏幕与设备" if confirmed else "演示 · 正在同步")
            self.scene_badge.show()
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
        self.scene_badge.setText("测试" if data["display"].get("simulated") else "实时任务")
        self.scene_badge.setVisible(state != "idle")
        self.flame.set_state(state, reduced=reduced, live=True)
        if self.brightness_off():
            title = "已熄灭"
        elif settings["paused"]:
            title = "已暂停"
        elif quiet_now(settings):
            title = "静默中"
        else:
            title = NAMES.get(state, "待机")
        self.display_title.setText(title)

    def render_notice(self):
        data, settings = self.model, self.model["settings"]
        if data["maintenance"]:
            kind, text, action = (
                "maintenance",
                "设备维护进行中，请保持 USB 连接。",
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
        connection = "wifi" if port.startswith("wifi://") else "usb" if port else None
        connection_name = "Wi-Fi" if connection == "wifi" else "USB" if connection else "设备"
        count = device.get("connected_count", int(bool(online)))
        connected_title = f"已连接 {count} 台设备"
        for model_id, button in self.device_model_buttons.items():
            connected = sum(item.get("online", False) for item in data.get("devices", []) if item.get("hardware_model") == model_id)
            button.set_connection_count(connected)
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
        if not hardware and was_hardware:
            self.ports.clear()
            self.ports.addItem("虚拟模式 · 不查找设备", "")
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
        self.counts.setText(
            f"进行中  {data['active_count']}    /    待提醒  {data['queue_count']}"
        )
        self.counts.setVisible(bool(data["active_count"] or data["queue_count"]))
        self.delivery.setVisible(bool(online and data["display"]["state"] != "idle"))
        self.delivery.setText(
            "APP 状态"
            if not device["online"]
            else "设备已确认"
            if data["display"].get("device_confirmed")
            else "等待回执"
        )
        selected = self.detail_device()
        selected_online = hardware and selected.get("online", False)
        self.device_summary.setText("设备已连接" if selected_online else selected.get("status", "设备尚未连接"))
        self.device_version.setText(
            "灯光固件 " + str(selected.get("firmware_version") or "未知（设备未上报）")
            + " · 协议 " + str(selected.get("protocol_version") or "未知")
            if selected_online else "连接后读取固件版本"
        )
        self.device_summary_icon.setVisible(selected_online)
        if selected_online:
            selected_transport = "wifi" if selected.get("port", "").startswith("wifi://") else "usb"
            self.device_summary_icon.setPixmap(icon(selected_transport, "#38735b", 18).pixmap(18, 18))
        self.device_empty_notice.setText("此设备尚未连接；其他已连接设备继续同步 AI 状态。")
        self.device_empty_notice.setVisible(not selected_online)
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
        enabled_brands = {}
        for source in data["sources"]:
            brand = source["id"].removesuffix("-web").removesuffix("-desktop")
            if source["enabled"] and brand in self.home_source_icons:
                enabled_brands.setdefault(brand, []).append(source["name"].replace("（实验）", ""))
        for brand, button in self.home_source_icons.items():
            button.setVisible(brand in enabled_brands)
            button.setToolTip("已开启：" + "、".join(enabled_brands.get(brand, [])))
        self.home_sources_row.setVisible(bool(enabled_brands))
        self.home_sources_empty.setVisible(not enabled_brands)
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
        key = [(e["id"], e["status"], e["reason"]) for e in data["events"]]
        if key != self.history_key:
            self.history_key = key
            self.render_history()
            self.render_recent()
        self.update_busy()
        if hardware and not was_hardware and self.tabs.currentIndex() == 1:
            self.scan_ports()
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
            RESULTS.get(event["status"], event["status"]),
        )

    def render_recent(self):
        events = self.model["events"][:3]
        self.recent_empty.setVisible(not events)
        for index, labels in enumerate(self.recent_rows):
            for item in labels:
                item.setVisible(index < len(events))
            if index < len(events):
                values = list(self.event_values(events[index]))
                values[0] = values[0][-8:]
                for item, value in zip(labels, values):
                    item.setText(value)

    def render_history(self):
        if not self.model:
            return
        query = self.history_search.text().strip().casefold()
        kind = self.history_filter.currentData()
        events = [
            e
            for e in self.model["events"]
            if (kind == "all" or is_test(e) == (kind == "test"))
            and (not query or query in " ".join(self.event_values(e)).casefold())
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
        help_text = {
            "screen": "APP 已接收并呈现任务状态，无需硬件。此记录不代表实体灯已确认；连接灯具后请用新任务验证设备回执。",
            "applied": "已收到事件 → 来源已匹配 → 指令已发送 → 设备已确认。回执代表接受了调度，实际发光请观察实体设备。",
            "queued": "已收到事件，等待提醒调度。有灯具时可另查设备回执；真实来源验证不要求购买硬件。",
            "suppressed": "事件因暂停、熄灭、亮度为 0 或静默而未点亮。请在总览查看原因；恢复后不会补播。",
            "unmatched": "该来源已关闭。请到「应用接入」开启来源，再发起新任务。",
            "expired": "事件已过期，不再提醒；请保持 APP 运行后发起新任务。",
            "lease_expired": "来源长时间没有更新状态，已停止工作提示。超时不代表完成。",
            "dropped": "待提醒队列已满，这条事件已被淘汰。可缩短提示时长，减少积压。",
            "ended_on_reconnect": "连接恢复后不重复播放旧提示；新任务会按当前设置提醒。",
        }.get(event["status"], "请检查设备与来源配置，再用一条新任务验证。")
        statuses = {"pending": "等待桌面应用领取", "claimed": "桌面应用处理中", "submitted": "已提交系统（显示取决于系统通知设置）",
                    "denied": "系统通知权限未允许", "unavailable": "系统通知不可用", "failed": "投递失败", "disabled": "已关闭",
                    "not_requested": "本事件无需弹窗", "interrupted": "进程重启，未重播", "unconfirmed": "提交结果未确认", "superseded": "任务状态已变化"}
        receipts = [f"{ {'flame': '火焰灯', 'desktop': '桌面通知'}.get(r['output'], r['output'])}：{statuses.get(r['status'], RESULTS.get(r['status'], r['status']))}"
                    for r in self.model.get("deliveries", []) if r["event"] == event["id"]]
        if receipts:
            help_text += "\n\n" + "\n".join(receipts)
        self.event_detail.setText(
            " · ".join(self.event_values(event)[:3]) + "\n\n" + help_text
        )

    def export_history(self):
        if not self.visible_events:
            self.tell("当前筛选下没有可导出的记录。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出当前筛选的记录", "SeeEnd-events.csv", "CSV 文件 (*.csv)"
        )
        if not path:
            return
        try:
            with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("时间", "应用 / 来源", "任务状态", "处理结果"))
                writer.writerows(self.event_values(e) for e in self.visible_events)
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
        if self.wifi_scan_running:
            self.api.request("/api/wifi-networks?" + urlencode({"device_id": self.selected_device_id or ""}), lambda data: self.render_wifi_scan(data) if generation == self.generation else None)
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

    def scan_ports(self):
        generation = self.generation
        if not self.ready or not self.hardware_mode() or self.job_busy or self.selected_device_model in YEELIGHT_MODELS:
            return
        self.scan_button.setEnabled(False)
        self.scan_button.setText("查找中…")

        def finished():
            self.scan_button.setText("刷新")
            self.update_busy()

        def received(data):
            if generation != self.generation or not self.hardware_mode():
                finished()
                return
            selected = self.ports.currentData()
            self.ports.clear()
            for port in data["ports"]:
                self.ports.addItem(
                    port["description"] + " · " + port["port"], port["port"]
                )
            if not data["ports"]:
                self.ports.addItem("未找到 USB 设备", "")
            index = self.ports.findData(selected)
            if index >= 0:
                self.ports.setCurrentIndex(index)
            finished()
            self.update_busy()

        self.api.request("/api/ports", received, failed=finished)

    def device_action(self, action, callback=None, **values):
        if self.device_selection_loading:
            self.tell("正在读取此设备，请稍候。", error=True)
            return
        if not self.hardware_mode():
            self.tell("请先在总览切换至硬件模式。", error=True)
            return
        if (
            action not in ("connect", "disconnect", "discover") or values.get("transport") == "usb"
        ) and not self.ports.currentData():
            self.tell("请先连接 USB 数据线并选择设备。", error=True)
            return

        def received(job):
            self.device_operation_id = job.get("id")
            self.render_device({"job": job})
            if callback:
                callback(job)

        self.act(
            "/api/device",
            {"action": action, "port": self.ports.currentData() or "", "hardware_model": self.selected_device_model, "device_id": self.selected_device_id or "", **values},
            received,
        )

    def scan_wifi(self):
        if self.device_selection_loading or not self.selected_device_id or self.wifi_scan_running or not self.ready or not self.hardware_mode() or self.job_busy:
            return
        if self.wifi_choices.count() == 0:
            typed = self.ssid.text()
            with QSignalBlocker(self.wifi_choices):
                self.wifi_choices.addItem("正在读取附近网络…")
                self.wifi_choices.model().item(0).setEnabled(False)
                self.wifi_choices.setEditText(typed)
        self.wifi_scan_running = True
        self.wifi_scan_hint.setText("ESP32 正在搜索附近的 2.4 GHz Wi-Fi…")
        def failed():
            if generation == self.generation:
                self.render_wifi_scan({"state": "failed", "message": "搜索失败，可重试或手动输入网络名。"})
        generation = self.generation
        self.api.request("/api/wifi-networks", lambda data: self.render_wifi_scan(data) if generation == self.generation else None, body={"device_id": self.selected_device_id or ""}, failed=failed)

    def render_wifi_scan(self, data):
        self.wifi_scan_running = data["state"] == "running"
        self.wifi_scan_hint.setText(data.get("message", ""))
        if data["state"] == "succeeded":
            typed = self.ssid.text()
            popup_open = self.wifi_choices.view().isVisible()
            with QSignalBlocker(self.wifi_choices):
                self.wifi_choices.clear()
                for network in data.get("networks", []):
                    self.wifi_choices.addItem(network["ssid"])
                    self.wifi_choices.setItemData(self.wifi_choices.count() - 1, str(network["signal"]) + " dBm", Qt.ItemDataRole.ToolTipRole)
                if not data.get("networks"):
                    self.wifi_choices.addItem("未发现网络，可手动输入")
                    self.wifi_choices.model().item(0).setEnabled(False)
                self.wifi_choices.setEditText(typed)
            if popup_open:
                self.wifi_choices.reopen_popup()
        elif data["state"] == "failed":
            typed = self.ssid.text()
            popup_open = self.wifi_choices.view().isVisible()
            with QSignalBlocker(self.wifi_choices):
                self.wifi_choices.clear()
                self.wifi_choices.addItem("暂时无法读取，可手动输入")
                self.wifi_choices.model().item(0).setEnabled(False)
                self.wifi_choices.setEditText(typed)
            if popup_open:
                self.wifi_choices.reopen_popup()
        self.update_busy()

    def clear_wifi_error(self, *_):
        self.wifi_error.hide()
        restyle(self.ssid, "invalid", False)
        restyle(self.password, "invalid", False)

    def open_network_changed(self, value):
        self.password.setEnabled(not value)
        self.show_password.setEnabled(not value)
        if value:
            self.password.clear()
            self.show_password.setChecked(False)
        self.clear_wifi_error()

    def provision(self):
        self.clear_wifi_error()
        ssid, password = self.ssid.text(), self.password.text()
        invalid, message = None, ""
        if not 1 <= len(ssid.encode("utf-8")) <= 32:
            invalid, message = (
                self.ssid,
                "请输入 Wi-Fi 名称（最多 32 字节，中文通常每字占 3 字节）。",
            )
        elif not self.open_network.isChecked() and not 8 <= len(password) <= 63:
            invalid, message = (
                self.password,
                "密码需要 8–63 个字符；无密码网络请勾选「此网络无需密码」。",
            )
        if invalid:
            restyle(invalid, "invalid", True)
            self.wifi_error.setText(message)
            self.wifi_error.show()
            invalid.setFocus()
            return

        def submitted(_):
            self.password.clear()
            self.show_password.setChecked(False)

        self.device_action(
            "provision",
            submitted,
            ssid=ssid,
            password="" if self.open_network.isChecked() else password,
            open_network=self.open_network.isChecked(),
        )

    def choose_yeelight_address(self, _index):
        host = self.yeelight_choices.currentData()
        if host:
            self.yeelight_host.setText(host)

    def render_device(self, data):
        was_job_busy = self.job_busy
        job = data.get("job")
        if (job and job.get("id") == self.device_operation_id and job["state"] == "succeeded"
                and job.get("hardware_model") == self.selected_device_model and not self.selected_device_id
                and job.get("result", {}).get("device_id")):
            self.selected_device_id = job["result"]["device_id"]
            data = {**data, "selected_device_id": self.selected_device_id}
            self.device_operation_id = None
        if "selected_device_id" in data:
            self.device_selection_loading = False
            self.selected_device_id = data["selected_device_id"]
            self.selected_device_state = data.get("device", {"online": False})
            if not self.writing and not self.brightness_timer.isActive() and not self.brightness.isSliderDown():
                appearance = data.get("light_settings", {"brightness": 20, "reduced": False})
                with QSignalBlocker(self.brightness), QSignalBlocker(self.device_reduced):
                    self.brightness.setValue(appearance["brightness"])
                    self.device_reduced.setChecked(appearance["reduced"])
                self.brightness_value.setText(f"{appearance['brightness']}%")
                self.brightness_note.setVisible(bool(self.selected_device_id) and appearance["brightness"] == 0)
            self.device_light_hint.setVisible(not self.selected_device_id)
            with QSignalBlocker(self.device_instances):
                self.device_instances.clear()
                self.device_instances.addItem("添加此型号设备…", "")
                for item in data.get("devices", []):
                    state = "已连接" if item.get("online") else "已断开" if item.get("connection_paused") else "离线"
                    self.device_instances.addItem(item["name"] + " · " + item["device_id"] + " · " + state, item["device_id"])
                self.device_instances.setCurrentIndex(max(0, self.device_instances.findData(self.selected_device_id)))
            if self.port_restore_pending and self.selected_device_model in YEELIGHT_MODELS:
                self.yeelight_host.setText(data.get("yeelight_host", ""))
                self.port_restore_pending = False
            if self.port_restore_pending and data.get("selected_port") and not self.ports.hasFocus():
                index = self.ports.findData(data["selected_port"])
                if index >= 0:
                    self.ports.setCurrentIndex(index)
                    self.port_restore_pending = False
        job = data.get("job")
        if "hardware_model" in data and not (job and job["state"] == "running"):
            if not self.device_details.isHidden():
                self.open_device_details(data["hardware_model"])
        if "wifi_available" in data:
            self.wifi_available = data["wifi_available"]
            self.connection_paused = data.get("connection_paused", False)
            ssid = data.get("wifi_ssid")
            if data.get("wifi_info_current"):
                text = ("设备已记忆：" + ssid + (" · 已连入网络" if data.get("wifi_connected") else " · 尚未连入网络")) if ssid else "设备尚未配置 Wi-Fi"
            elif data.get("wifi_info_known"):
                text = "上次记录：" + (ssid or "未配置 Wi-Fi") + " · 等待设备确认"
            else:
                text = "网络名未知 · 连接并更新灯光程序后可读取"
            self.wifi_network.setText(text)
        if "transport" in data:
            self.pairing.setText(
                (
                    "已配对：" + data["paired_device"]
                    if data["paired_device"]
                    else "尚未配对"
                )
                + " · "
                + ("Wi-Fi 局域网" if data["transport"] in ("wifi", "yeelight") else "USB")
            )
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

        if (was_job_busy and job and job.get("action") == "select_model"
                and job["state"] == "succeeded" and self.tabs.currentIndex() == 1
                and not self.device_details.isHidden()):
            self.scan_ports()

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
            lambda: self.test_message.setText(self.message.text()),
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
            self.job_busy
            or self.test_busy
            or self.writing
            or bool(self.model and self.model["maintenance"])
        )
        hardware = connected and self.hardware_mode() and not self.device_selection_loading
        for button in self.device_model_buttons.values():
            button.setEnabled(connected)
        for button in self.mode_buttons.values():
            button.setEnabled(connected and not busy)
        for button in self.trigger_buttons:
            button.setEnabled(connected and not busy)
        for button in self.maintenance_buttons:
            button.setEnabled(hardware and not busy)
        for button in self.port_buttons:
            button.setEnabled(hardware and not busy and bool(self.ports.currentData()))
            button.setToolTip(
                "" if self.ports.currentData() else "请先接入 USB 数据线并选择设备"
            )
        device = self.detail_device()
        online = hardware and device.get("online", False)
        connecting = device.get("connecting", False)
        port = device.get("port", "")
        wifi_online = online and port.startswith("wifi://")
        usb_online = online and not wifi_online
        same_usb = usb_online and port == self.ports.currentData()
        available = hardware and not busy and not connecting
        self.yeelight_discover_button.setEnabled(hardware and not busy)
        self.yeelight_connect_button.setEnabled(available and bool(self.yeelight_host.text().strip()))
        self.usb_connect_button.setEnabled(available and bool(self.ports.currentData()) and not same_usb)
        self.usb_connect_button.setText("USB 已连接" if same_usb else "切换此设备串口" if usb_online else "此设备改用 USB" if wifi_online else "连接此设备 USB")
        self.wifi_connect_button.setEnabled(available and self.wifi_available and not wifi_online)
        self.wifi_connect_button.setText("Wi-Fi 已连接" if wifi_online else "此设备改用 Wi-Fi" if usb_online else "连接此设备 Wi-Fi")
        self.wifi_connect_button.setToolTip("" if self.wifi_available else "请先通过 USB 配置网络")
        self.disconnect_button.setEnabled(hardware and not busy and not self.connection_paused and (online or connecting or bool(self.selected_device_id)))
        self.flash_button.setEnabled(
            hardware
            and not busy
            and bool(self.ports.currentData())
            and self.confirm_board.isChecked()
            and self.confirm_flash.isChecked()
        )
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
            check.setEnabled(connected)
        for control in [*self.brightness_controls, self.device_reduced]:
            control.setEnabled(connected and not self.writing and not self.device_selection_loading and bool(self.selected_device_id))
        for control in [
            self.duration,
            *self.checks.values(),
        ]:
            control.setEnabled(connected)
        for edit in self.times.values():
            edit.setEnabled(connected and self.checks["quiet_enabled"].isChecked())
        self.ports.setEnabled(hardware and not busy)
        self.scan_button.setEnabled(hardware and not busy)
        self.wifi_choices.setEnabled(hardware and not busy)

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
                 received, lambda: target.setText(self.message.text()))

    def cli_action(self, source, action):
        body = self.integration_parameters(source)
        if source in self.cli_profiles and "profile" not in body:
            self.cli_messages[source].setText("请选择实际使用的已有 Profile。")
            return
        target = self.cli_messages[source]
        target.setText("正在增量配置…" if action == "install" else "正在移除本应用接入…")
        self.act("/api/integrations/" + action, body,
                 lambda data: (target.setText(data.get("message", "接入已更新。")), self.check_cli(source)),
                 lambda: target.setText(self.message.text()))

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
                    message.setText("检查未完成：" + self.message.text())
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
                         failed=lambda: self.codex_message.setText("检查未完成：" + self.message.text()))

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
            self, "保存下载包", filename, "ZIP 文件 (*.zip)"
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

    def flash(self):
        if not self.hardware_mode():
            self.tell("请先在总览切换至硬件模式。", error=True)
            return
        if not self.confirm_board.isChecked() or not self.confirm_flash.isChecked():
            self.tell("请确认开发板型号，并确认清除板上的程序和配置。", error=True)
            return
        if not self.ports.currentData():
            self.tell("请先选择 USB 设备。", error=True)
            return
        self.device_action("flash_base", confirm_board=True, confirm_flash=True)

    def shutdown(self):
        self.device_test_timer.stop()
        self.poll_timer.stop()
        self.brightness_timer.stop()
        self.preview_timer.stop()
        self.message_timer.stop()
        self.flame.animation_timer.stop()
        self.actions.clear()
        self.api.close()
