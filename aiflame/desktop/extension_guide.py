"""Install the browser extension, connect it and start using reminders."""
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QApplication
from aiflame.desktop.localization import (
    QDialog, QLabel, QPushButton, QLineEdit, QScrollArea, QWidget, QComboBox,
    QCheckBox, t, source_text,
)
from aiflame.desktop.browser_launch import open_in_chrome
from aiflame.desktop.components import Disclosure
from aiflame.integrations.chatgpt_web.guide import (
    STORE_URL, STEPS, TROUBLESHOOTING, HELP_FOR_STEP, UPGRADE,
)
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES, WEB_URLS


class ExtensionGuide(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("extensionGuide")
        self.setWindowTitle("SeeEnd · 安装浏览器扩展")
        self.resize(820, 690)
        self.setMinimumSize(620, 500)
        self.step = 0
        self.installed = self.configured = self.busy = False
        self.store_error = False
        self.snapshot = parent.model or {}
        enabled = {row['id'] for row in self.snapshot.get('sources', []) if row.get('enabled')}
        self.selected_source = next((source for source in WEB_SOURCES if source in enabled), "chatgpt-web")
        self.epoch = 0
        self.pending_status = False
        self.read_error = self.chrome_error = False
        self.notice = None
        self.notice_until = 0
        self.busy_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        self.progress = QLabel()
        self.progress.setProperty("role", "guideProgress")
        self.progress.setProperty("literalText", True)
        layout.addWidget(self.progress)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setProperty("role", "guideTitle")
        layout.addWidget(self.title)
        self.site_panel = QWidget()
        sites = QHBoxLayout(self.site_panel)
        sites.setContentsMargins(0, 0, 0, 0)
        name = QLabel("使用网站")
        self.site_choice = QComboBox()
        self.site_choice.setAccessibleName("选择 AI 网站")
        for source, title in WEB_SOURCES.items():
            self.site_choice.addItem(title, source)
        self.site_choice.setCurrentIndex(self.site_choice.findData(self.selected_source))
        name.setBuddy(self.site_choice)
        self.site_choice.currentIndexChanged.connect(self.choose_source)
        self.source_status = QLabel()
        sites.addWidget(name)
        sites.addWidget(self.site_choice, 1)
        sites.addWidget(self.source_status)
        layout.addWidget(self.site_panel)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status.setProperty("literalText", True)
        self.status.setProperty("role", "guideStatus")
        self.status.setAccessibleName("安装提示")
        layout.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("extensionGuideBody")
        self.content = QVBoxLayout(body)
        self.content.setContentsMargins(0, 0, 12, 12)
        self.content.setSpacing(12)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.previous = QPushButton("上一步")
        self.previous.clicked.connect(lambda: self.go(self.step - 1))
        self.help_button = QPushButton("遇到问题")
        self.help_button.setProperty("variant", "quiet")
        self.help_button.clicked.connect(self.show_help)
        self.primary = QPushButton()
        self.primary.setProperty("variant", "primary")
        self.primary.setAutoDefault(False)
        self.primary.clicked.connect(self.perform_primary)
        for widget in (self.previous, self.help_button):
            nav.addWidget(widget)
        nav.addStretch()
        nav.addWidget(self.primary)
        layout.addLayout(nav)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.check_events)
        self.render_step()

    def retranslate(self):
        super().retranslate()
        if hasattr(self, "content"):
            QTimer.singleShot(0, self.render_step)

    @staticmethod
    def clear(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
            elif item.layout():
                ExtensionGuide.clear(item.layout())
                item.layout().deleteLater()

    def text(self, value, muted=False):
        label = QLabel(value)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        if muted:
            label.setProperty("role", "muted")
        self.content.addWidget(label)
        return label

    def button(self, value, callback):
        button = QPushButton(value)
        button.setProperty("variant", "quiet")
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        self.content.addWidget(button, alignment=Qt.AlignLeft)
        return button

    def render_step(self):
        self.clear(self.content)
        self.progress.setText(t("第 {step} 步，共 {total} 步").format(step=self.step + 1, total=len(STEPS)))
        self.title.setText(STEPS[self.step][0])
        self.site_panel.setVisible(self.step == 1)
        if self.step == 0:
            self.text("在商店点击「添加至 Chrome」，确认安装后回到这里。")
            links = Disclosure("商店链接", self.content)
            address = QLineEdit(STORE_URL)
            address.setReadOnly(True)
            address.setAccessibleName("Chrome 商店安装地址")
            links.content.addWidget(address)
            self.button("复制商店链接", self.copy_store)
            self.text("已安装的用户可直接勾选下方确认。", True)
            self.checkpoint = QCheckBox("我已在 Chrome 安装并启用 SeeEnd 扩展")
            self.checkpoint.setChecked(self.installed)
            self.checkpoint.toggled.connect(self.confirm_install)
            self.content.addWidget(self.checkpoint)
            if self.installed:
                self.button("重新打开商店", self.open_store)
        else:
            if not self.configured:
                self.text("允许扩展连接 SeeEnd，即可在电脑上接收网页任务的提醒。")
            elif not self.source_enabled():
                self.text("开启所选网站的提醒即可开始使用。其他网站的开关保持各自设置。")
            else:
                self.text("在所选网站登录并刷新页面，然后打开 Chrome 工具栏中的 SeeEnd 扩展。")
                self.text("在新对话或已有对话中发送一条普通文字提问。已完成的历史任务不会补发提醒。")
                self.button("在 Chrome 打开", self.open_site)
            if self.configured:
                self.button("重新检查状态", self.check_events)
                self.button("复制网站地址", self.copy_site)
                self.button("重新允许连接", self.connect)
        self.inline_help = Disclosure("遇到问题", self.content)
        items = [TROUBLESHOOTING[i] for i in HELP_FOR_STEP[self.step]]
        for question, answer in items + [("常规升级", UPGRADE)]:
            title = QLabel(question)
            title.setWordWrap(True)
            title.setProperty("role", "title")
            self.inline_help.content.addWidget(title)
            answer_label = QLabel(answer)
            answer_label.setWordWrap(True)
            self.inline_help.content.addWidget(answer_label)
        self.content.addStretch()
        self.refresh_controls()
        self.scroll.verticalScrollBar().setValue(0)
        self.update_timer()

    def confirm_install(self, checked):
        self.installed = checked
        if not checked:
            self.configured = False
            self.invalidate()
        self.notice = None
        self.refresh_controls()

    def source_enabled(self):
        return next((row.get('enabled') for row in self.snapshot.get('sources', []) if row['id'] == self.selected_source), None)

    def refresh_controls(self):
        self.previous.setEnabled(self.step > 0 and not self.busy)
        self.site_choice.setEnabled(not self.busy)
        for button in self.scroll.findChildren(QPushButton):
            button.setEnabled(not self.busy)
        if self.step == 0:
            self.checkpoint.setEnabled(not self.busy)
        self.primary.setEnabled(not self.busy)
        if self.busy:
            action = self.busy_text
        elif self.step == 0:
            action = "继续" if self.installed else "在 Chrome 安装扩展"
        elif self.read_error:
            action = "重新读取状态"
        elif not self.configured:
            action = "允许连接"
        elif self.source_enabled() is None:
            action = "重新读取状态"
        elif not self.source_enabled():
            action = "开启此网站提醒"
        else:
            action = "完成设置"
        self.primary.setText(action)
        enabled = self.source_enabled()
        self.source_status.setText("提醒已开启" if enabled else "提醒已关闭" if enabled is False else "正在读取状态")
        self.update_status()

    def update_status(self):
        name = WEB_SOURCES[self.selected_source]
        tone = 'normal'
        args = {}
        if self.busy:
            key = self.busy_text
        elif self.step == 1 and self.read_error:
            key = "无法读取提醒设置。请保持 SeeEnd 运行，再点击「重新读取状态」。"
            tone = 'warning'
        elif self.notice and time.monotonic() < self.notice_until:
            key, tone, args = self.notice
        elif self.step == 0:
            if self.installed:
                key = "扩展已安装，下一步允许连接并开启提醒。"
            elif self.store_error:
                key = "未能打开 Chrome。请复制商店链接，在 Chrome 中打开并安装扩展。"
                tone = 'warning'
            else:
                key = "请在 Chrome 商店安装扩展，完成后勾选下方确认项。"
        elif not self.configured:
            key = "所选网站：{site}。先允许扩展连接 SeeEnd。"
            args = {'site': name}
        elif self.source_enabled() is None:
            key = "尚未读取到网站开关状态，请重新读取。"
        elif not self.source_enabled():
            key = "{site} 的提醒已关闭。点击下方按钮开启。"
            args = {'site': name}
        elif self.chrome_error:
            key = "未能打开 Chrome。请复制网站地址，在 Chrome 中粘贴打开。"
            tone = 'warning'
        else:
            args = {'site': name}
            key = "连接设置已完成。刷新 {site} 后，即可接收新任务的提醒。"
        self.status.setText(t(key).format(**{arg: t(value) if isinstance(value, str) else value
                                           for arg, value in args.items()}))
        self.status.setProperty('guideTone', tone)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def tell(self, key, tone='normal', **args):
        self.notice = (key, tone, args)
        self.notice_until = time.monotonic() + 8
        self.refresh_controls()

    def invalidate(self):
        self.epoch += 1
        self.pending_status = False
        self.read_error = self.chrome_error = False
        self.notice = None

    def go(self, step):
        if self.busy or not 0 <= step < len(STEPS):
            return
        if step > self.step and not self.installed:
            return
        self.epoch += 1
        self.pending_status = False
        self.notice = None
        self.step = step
        self.render_step()
        if step == 1:
            self.check_events()

    def perform_primary(self):
        if self.busy:
            return
        if self.step == 0:
            self.go(1) if self.installed else self.open_store()
        elif self.read_error:
            self.check_events()
        elif not self.configured:
            self.connect()
        elif self.source_enabled() is None:
            self.check_events()
        elif not self.source_enabled():
            self.enable_source()
        else:
            self.close()

    def choose_source(self):
        self.selected_source = self.site_choice.currentData()
        self.invalidate()
        self.render_step()
        self.check_events()

    def open_store(self):
        self.store_error = not open_in_chrome(STORE_URL)
        self.notice = None
        if not self.store_error:
            self.tell("已请求 Chrome 打开商店。点击「添加至 Chrome」，安装后回到这里继续。")
        self.refresh_controls()

    def copy_store(self):
        QApplication.clipboard().setText(STORE_URL)
        self.tell("商店链接已复制。请在 Chrome 中打开并安装扩展。")

    def copy_site(self):
        QApplication.clipboard().setText(WEB_URLS[self.selected_source])
        self.tell("网站地址已复制。请在 Chrome 中粘贴打开。")

    def set_busy(self, value, text=''):
        self.busy = value
        self.busy_text = text
        self.refresh_controls()

    def connect(self):
        if self.busy:
            return
        self.invalidate()
        self.configured = False
        self.set_busy(True, "正在允许连接…")
        def received(data):
            self.configured = True
            self.set_busy(False)
            self.render_step()
            self.check_events()
        self.parent().act('/api/integrations/install', {'source': 'chatgpt-web'}, received,
                          lambda: self.failed("连接配置未完成。请重试，或查看下方具体原因。"))

    def enable_source(self):
        self.invalidate()
        self.set_busy(True, "正在开启提醒…")
        def received(data):
            self.snapshot = data
            self.set_busy(False)
            self.render_step()
        self.parent().act('/api/settings', {'source_enabled': {self.selected_source: True}}, received,
                          lambda: self.failed("未能开启网站提醒。请重试。"))

    def open_site(self):
        self.chrome_error = not open_in_chrome(WEB_URLS[self.selected_source])
        self.notice = None
        if not self.chrome_error:
            self.tell("已请求 Chrome 打开 {site}。登录后刷新页面，再检查扩展。", site=WEB_SOURCES[self.selected_source])
        self.refresh_controls()

    def failed(self, message):
        self.set_busy(False)
        if getattr(self.parent(), 'message_error', False):
            message += '\n' + t("具体原因：") + source_text(self.parent().message)
        self.tell(message, 'warning')

    def showEvent(self, event):
        super().showEvent(event)
        self.update_timer()
        self.check_events()

    def update_timer(self):
        if self.isVisible() and self.step == 1:
            self.timer.start()
        else:
            self.timer.stop()

    def closeEvent(self, event):
        self.timer.stop()
        self.epoch += 1
        self.pending_status = False
        super().closeEvent(event)

    def check_events(self):
        if not self.isVisible() or self.step != 1 or self.busy or self.pending_status:
            return
        self.pending_status = True
        epoch = self.epoch
        def received(data):
            if epoch != self.epoch:
                return
            self.pending_status = False
            was_enabled = self.source_enabled()
            self.snapshot = data
            self.read_error = False
            if was_enabled is not None and was_enabled != self.source_enabled():
                self.invalidate()
            self.refresh_controls()
        def failed():
            if epoch != self.epoch:
                return
            self.pending_status = False
            self.read_error = True
            self.refresh_controls()
        self.parent().api.request('/api/status', received, failed=failed)

    def show_help(self):
        self.inline_help.button.setChecked(not self.inline_help.button.isChecked())
        self.scroll.ensureWidgetVisible(self.inline_help)
