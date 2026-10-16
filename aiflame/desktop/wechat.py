"""Native Weixin binding and notification controls."""
import base64

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout
from .components import label
from .localization import QLineEdit, QWidget

RECEIPTS = {
    "disabled": "已关闭", "pending": "等待发送", "submitted": "已提交微信（请在手机确认收到）",
    "unconfirmed": "发送结果未确认，未自动重发", "failed": "发送失败", "unconnected": "微信未连接",
    "context_missing": "需先在微信给机器人发一条消息", "not_requested": "本事件无需微信通知",
    "unmatched": "来源已停用", "suppressed": "提醒已停止", "expired": "事件已过期", "dropped": "队列已淘汰",
    "interrupted": "进程重启，未重播", "superseded": "任务状态已变化",
}
LOGIN_COPY = {
    "loading": "正在获取二维码…", "wait": "用本人微信扫一扫，并在微信中确认绑定。",
    "scaned": "已扫码，请在微信中确认绑定。", "need_verifycode": "请输入微信中显示的验证码。",
    "verifying": "正在验证…", "verify_code_blocked": "验证码尝试次数过多，请重新扫码。",
    "expired": "二维码已过期，请重新扫码。",
    "binded_redirect": "此微信已有绑定，请在微信中确认或解除原绑定后重试。",
    "failed": "绑定未完成，请重试。",
}


class WechatConnection:
    def build_wechat(self, body):
        self.wechat_notice = ""
        actions = self.preference_toggle(
            body, "wechat_notifications", "微信通知",
            help_text="任务完成或等待输入时发送微信通知，需保持 SeeEnd 运行。仅发送来源、任务状态和时间。关闭通知保留绑定，其他提醒方式独立工作。"
        )
        self.checks["wechat_notifications"].setToolTip("通过本人微信 ClawBot 接收任务提醒。")
        self.wechat_login_box = QWidget()
        qr_layout = QVBoxLayout(self.wechat_login_box)
        qr_layout.setContentsMargins(0, 0, 0, 0)
        qr_layout.setSpacing(12)
        self.wechat_qr = label()
        self.wechat_qr.setFixedSize(220, 220)
        self.wechat_qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.wechat_qr.setAccessibleName("微信绑定二维码，请用手机微信扫一扫")
        self.wechat_qr.setStyleSheet("background: white; border: 1px solid #dedcd3;")
        qr_layout.addWidget(self.wechat_qr, 0, Qt.AlignmentFlag.AlignLeft)
        self.wechat_login_hint = label("", role="preferenceHint")
        qr_layout.addWidget(self.wechat_login_hint)
        body.addWidget(self.wechat_login_box)
        self.wechat_verify_box = QWidget()
        verify_layout = QHBoxLayout(self.wechat_verify_box)
        verify_layout.setContentsMargins(0, 0, 0, 0)
        self.wechat_verify = QLineEdit()
        self.wechat_verify.setPlaceholderText("微信验证码")
        self.wechat_verify.setAccessibleName("微信验证码")
        self.wechat_verify.setMaxLength(16)
        self.wechat_verify.setFixedWidth(160)
        verify_layout.addWidget(self.wechat_verify)
        self.wechat_verify_button = self.button(verify_layout, "提交验证码", self.verify_wechat)
        verify_layout.addStretch()
        body.addWidget(self.wechat_verify_box)
        self.wechat_login_button = self.button(actions, "扫码绑定", lambda: self.wechat_action("login"))
        self.wechat_prepare_button = self.button(actions, "检查微信连接", lambda: self.wechat_action("prepare"))
        self.wechat_test_button = self.button(actions, "发送测试提醒", lambda: self.wechat_action("test"))
        self.wechat_cancel_button = self.button(actions, "取消扫码", lambda: self.wechat_action("cancel"), variant="quiet")
        self.wechat_disconnect_button = self.button(actions, "解除绑定", lambda: self.wechat_action("disconnect"), variant="quiet")
        self.wechat_feedback = label("", role="preferenceHint")
        body.addWidget(self.wechat_feedback)
        self.wechat_data = {}
        self.wechat_image = None
        self.render_wechat({})

    def wechat_action(self, action, **fields):
        self.act("/api/wechat", {"action": action, **fields}, self.render_wechat)

    def verify_wechat(self):
        code = self.wechat_verify.text().strip()
        self.wechat_verify.clear()
        self.wechat_action("verify", code=code)

    def render_wechat(self, data):
        previous_test = self.wechat_data.get("test_status")
        self.wechat_data = data
        login = data.get("login", {})
        state, image = login.get("state", "idle"), login.get("image", "")
        bound = data.get("bound", False)
        available = self.notification_channel_available("wechat_notifications")
        active = state in ("loading", "wait", "scaned", "need_verifycode", "verifying")
        if data.get("busy"):
            title, hint = "同步中…", ""
        elif available:
            title, hint = "", ""
        elif bound and data.get("needs_message"):
            title, hint = "", "先在微信中给机器人发一条消息。"
        elif bound and data.get("preparing"):
            title, hint = "正在连接微信…", ""
        elif bound:
            title, hint = "请先检查微信连接，再开启通知。", ""
        elif active:
            title, hint = "绑定中…", ""
        else:
            title, hint = "请先扫码绑定微信，再开启通知。", ""
        if data.get("error"):
            title, hint = "微信连接需处理", data["error"]
        self.wechat_notice = hint or title
        self.update_preference_help("wechat_notifications", title, hint)
        self.wechat_login_box.setVisible(active or state in LOGIN_COPY)
        self.wechat_login_hint.setText(LOGIN_COPY.get(state, ""))
        self.wechat_qr.setVisible(bool(image))
        if image != self.wechat_image:
            self.wechat_image = image
            pixmap = QPixmap()
            try:
                if image:
                    pixmap.loadFromData(base64.b64decode(image, validate=True), "PNG")
            except (ValueError, TypeError):
                pass
            if pixmap.isNull():
                self.wechat_qr.clear()
            else:
                self.wechat_qr.setPixmap(pixmap.scaled(220, 220, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.FastTransformation))
        self.wechat_verify_box.setVisible(state in ("need_verifycode", "verifying"))
        if state not in ("need_verifycode", "verifying"):
            self.wechat_verify.clear()
        self.wechat_login_button.setText("重新扫码" if state in ("expired", "failed", "verify_code_blocked", "binded_redirect") else "扫码绑定")
        self.wechat_login_button.setVisible(not bound and not active)
        self.wechat_cancel_button.setVisible(active)
        self.wechat_prepare_button.setVisible(bound and not available and not data.get("preparing"))
        self.wechat_test_button.setVisible(bound)
        self.wechat_disconnect_button.setVisible(bound)
        test_status = data.get("test_status")
        feedback = "" if test_status == "submitted" else RECEIPTS.get(test_status, "")
        self.wechat_feedback.setText(feedback)
        self.wechat_feedback.setVisible(bool(feedback))
        if test_status == "submitted" and previous_test != "submitted":
            self.tell("测试提醒已提交，请在微信中查看。")
        self.update_wechat_controls()

    def update_wechat_controls(self):
        data = self.wechat_data
        available = self.ready and self.model is not None and not self.reset_pending and not self.writing and not data.get("busy")
        for button in (self.wechat_login_button, self.wechat_cancel_button, self.wechat_disconnect_button):
            button.setEnabled(available)
        self.wechat_prepare_button.setEnabled(available and not data.get("preparing"))
        eligible = self.notification_channel_available("wechat_notifications")
        toggle = self.checks["wechat_notifications"]
        with QSignalBlocker(toggle):
            toggle.setChecked(self.ready and not self.reset_pending and eligible and
                              self.notification_toggle_value("wechat_notifications"))
        toggle.setEnabled(self.channel_toggle_editable("wechat_notifications") and not data.get("busy") and eligible)
        toggle.setToolTip(self.channel_save_feedback("wechat_notifications",
            "通过本人微信 ClawBot 接收任务提醒。" if eligible else self.wechat_notice))
        self.wechat_test_button.setEnabled(available and data.get("ready", False) and data.get("test_status") != "pending")
        self.wechat_test_button.setToolTip("" if data.get("ready") else "请开启通知，并先在微信中给机器人发一条消息。")
        verify = available and data.get("login", {}).get("state") == "need_verifycode"
        self.wechat_verify.setEnabled(verify)
        self.wechat_verify_button.setEnabled(verify)
        self.update_channel_controls()
