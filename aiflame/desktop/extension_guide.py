"""Four observable stages, one current action, and a fresh real-task check."""
import sys
import time
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QApplication, QSizePolicy
from aiflame.desktop.localization import (
    QDialog, QLabel, QPushButton, QLineEdit, QScrollArea, QWidget, QComboBox,
    QCheckBox, t, source_text,
)
from aiflame.desktop.browser_launch import open_in_chrome
from aiflame.integrations.chatgpt_web.guide import (
    IMAGES, STEPS, SHOTS, CAPTIONS, PATH_HELP, TROUBLESHOOTING, HELP_FOR_STEP,
    UPGRADE, verification_state,
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
        self.prepared = self.configured = self.busy = False
        self.path = ""
        self.snapshot = parent.model or {}
        enabled = {row['id'] for row in self.snapshot.get('sources', []) if row.get('enabled')}
        self.selected_source = next((source for source in WEB_SOURCES if source in enabled), "chatgpt-web")
        self.confirmed = {1: False, 2: False}
        self.baseline = None
        self.verified = False
        self.epoch = 0
        self.pending_status = False
        self.read_error = self.chrome_error = False
        self.notice = None
        self.notice_until = 0
        self.busy_text = ""
        self.pictures = []
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
        name = QLabel("验证网站")
        self.site_choice = QComboBox()
        self.site_choice.setAccessibleName("要验证的 AI 网站")
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
        self.status.setAccessibleName("当前安装与验证状态")
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
        self.pictures = []
        self.progress.setText(t("第 {step} 步，共 {total} 步").format(step=self.step + 1, total=len(STEPS)))
        self.title.setText(STEPS[self.step][0])
        self.site_panel.setVisible(self.step == 3)
        if self.step == 0:
            self.text(STEPS[0][1])
            self.path_field()
            self.text("无需下载或解压，也不需要灯具。", True)
            if self.prepared:
                self.button("重新准备安装文件", self.prepare)
        elif self.step == 1:
            self.text(STEPS[1][1])
            address = QLineEdit("chrome://extensions/")
            address.setReadOnly(True)
            address.setAccessibleName("Chrome 扩展管理地址")
            self.content.addWidget(address)
            self.confirmation("Chrome 的文件夹选择窗口已打开")
        elif self.step == 2:
            self.path_field()
            self.text(PATH_HELP.get(sys.platform, PATH_HELP['linux']))
            self.confirmation("Chrome 已显示 SeeEnd 卡片，且开关已开启")
            if sys.platform == "darwin":
                self.button("查看粘贴路径的位置", lambda: self.enlarge("chrome-folder-path.jpg", (0, 0, 920, 220), "macOS 路径输入窗口；请粘贴你自己的完整路径"))
        else:
            if not self.configured:
                self.text("允许连接会设置本机通信。完成后还需要一条新任务来验证。")
            elif not self.source_enabled():
                self.text("开启所选网站的提醒后，即可开始验证。其他网站的开关保持各自设置。")
            else:
                self.text("在所选网站登录并刷新页面，然后打开 Chrome 工具栏中的 SeeEnd 扩展。")
                self.text("1. 点击扩展中的「检查当前页面」。\n2. 检查通过后，发送一条新的普通文字提问。\n3. 等回答完成，回到这里查看结果。")
                self.text("页面检查通过表示能连接；新任务的开始和完成才是接入证据。", True)
                if self.muted():
                    self.button("前往提醒偏好", lambda: self.parent().navigate(3))
            if self.configured:
                self.button("重新检查状态", self.check_events)
                self.button("复制网站地址", self.copy_site)
                self.button("重新允许连接", self.connect)
        shots = SHOTS.get(self.step, ())
        if shots:
            row = QHBoxLayout()
            row.setSpacing(24)
            self.content.addLayout(row)
        for filename, crop, caption in shots[:2]:
            column = QVBoxLayout()
            column.setSpacing(8)
            row.addLayout(column, 1)
            description = QLabel(caption)
            description.setWordWrap(True)
            description.setProperty('role', 'muted')
            column.addWidget(description)
            picture = QLabel()
            picture.setAlignment(Qt.AlignLeft)
            picture.setMinimumSize(1, 1)
            picture.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            picture.setAccessibleName(caption + "：真实截图局部")
            pixmap = QPixmap(str(IMAGES / filename)).copy(*crop)
            self.pictures.append((picture, pixmap))
            column.addWidget(picture)
            zoom = QPushButton("放大此处")
            zoom.setProperty('variant', 'quiet')
            zoom.setAutoDefault(False)
            zoom.clicked.connect(lambda checked=False, f=filename, c=crop, title=caption: self.enlarge(f, c, title))
            column.addWidget(zoom, alignment=Qt.AlignLeft)
            column.addStretch()
        if len(shots) > 2:
            filename, crop, caption = shots[2]
            self.button("查看加载成功的卡片", lambda: self.enlarge(filename, crop, caption))
        if shots:
            self.text(CAPTIONS[self.step], True)
        self.content.addStretch()
        self.fit_images()
        self.refresh_controls()
        self.scroll.verticalScrollBar().setValue(0)
        self.update_timer()

    def path_field(self):
        self.folder_path = QLineEdit(self.path if self.prepared else "")
        self.folder_path.setReadOnly(True)
        self.folder_path.setPlaceholderText("准备后显示完整文件夹路径")
        self.folder_path.setAccessibleName("安装文件夹完整路径")
        self.content.addWidget(self.folder_path)

    def confirmation(self, value):
        checkbox = QCheckBox(value)
        checkbox.setChecked(self.confirmed[self.step])
        checkbox.toggled.connect(self.confirm_step)
        self.content.addWidget(checkbox)
        self.checkpoint = checkbox

    def confirm_step(self, checked):
        self.confirmed[self.step] = checked
        self.notice = None
        self.refresh_controls()

    def source_enabled(self):
        return next((row.get('enabled') for row in self.snapshot.get('sources', []) if row['id'] == self.selected_source), None)

    def muted(self):
        return self.snapshot.get('settings', {}).get('paused') or self.snapshot.get('display', {}).get('key') == 'muted'

    def state(self):
        return verification_state(self.snapshot, self.baseline, self.selected_source) if self.baseline is not None else 'waiting'

    def refresh_controls(self):
        self.previous.setEnabled(self.step > 0 and not self.busy)
        self.site_choice.setEnabled(not self.busy)
        for button in self.scroll.findChildren(QPushButton):
            button.setEnabled(not self.busy)
        if hasattr(self, 'checkpoint') and self.step in (1, 2):
            self.checkpoint.setEnabled(not self.busy)
        self.primary.setEnabled(not self.busy)
        if self.busy:
            action = self.busy_text
        elif self.step == 0:
            action = "继续" if self.prepared else "准备安装文件"
        elif self.step == 1:
            action = "继续" if self.confirmed[1] else "复制 Chrome 地址"
        elif self.step == 2:
            action = "继续" if self.confirmed[2] else "复制文件夹路径"
            self.primary.setEnabled(self.prepared and bool(self.path))
        elif self.verified:
            action = "完成设置"
        elif self.read_error:
            action = "重新读取状态"
        elif not self.configured:
            action = "允许连接"
        elif self.source_enabled() is None:
            action = "重新读取状态"
        elif not self.source_enabled():
            action = "开启此网站提醒"
        elif self.chrome_error:
            action = "复制网站地址"
        elif self.state() == 'working':
            action = "等待回答完成…"
            self.primary.setEnabled(False)
        else:
            action = "返回 Chrome" if self.baseline is not None else "在 Chrome 打开"
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
        elif self.step == 3 and self.read_error:
            key = "状态读取失败；已保留本次验证成功的结果。可重新检查当前状态。" if self.verified else "无法读取接入状态。请保持 SeeEnd 运行，再点击「重新读取状态」。"
            tone = 'warning'
        elif self.step == 3 and self.verified:
            key = "{site}：网页接入已验证。已收到新任务的开始和回答完成。"
            args = {'site': name}
            tone = 'success'
            if self.muted():
                key += "\n提醒已暂停或静默，可在提醒偏好中恢复。"
        elif self.notice and time.monotonic() < self.notice_until and not (self.step == 3 and self.baseline is not None and self.state() in ('working', 'failed', 'muted')):
            key, tone, args = self.notice
        elif self.step == 0:
            key = "安装文件已准备。" if self.prepared else "等待准备安装文件。"
            tone = 'success' if self.prepared else 'normal'
        elif self.step == 1:
            key = "文件夹选择窗口已打开，可以继续。" if self.confirmed[1] else "打开文件夹选择窗口后，勾选下方确认项。"
        elif self.step == 2:
            key = "已确认 Chrome 加载扩展，下一步设置连接。" if self.confirmed[2] else "先复制完整路径；加载后确认 SeeEnd 卡片和开关。"
        elif not self.configured:
            key = "所选网站：{site}。先允许扩展连接 SeeEnd。"
            args = {'site': name}
        elif self.source_enabled() is None:
            key = "尚未读取到网站开关状态，请重新读取。"
        elif not self.source_enabled():
            key = "{site} 的提醒已关闭。点击下方按钮开启后验证。"
            args = {'site': name}
        elif self.chrome_error:
            key = "未能打开 Chrome。复制网站地址，在 Chrome 中粘贴打开，再按下方说明验证。"
            tone = 'warning'
        elif self.baseline is None:
            key = "连接配置已准备。接下来在 Chrome 打开 {site} 并开始验证。"
            args = {'site': name}
        else:
            args = {'site': name}
            state = self.state()
            key = {
                'working': "{site}：已收到任务开始，正在等待回答完成。",
                'failed': "{site}：这条任务没有正常完成。请重新发送一条普通文字提问。",
                'muted': "提醒已暂停或静默。可前往提醒偏好恢复，再发送新提问。",
                'waiting': "等待 {site} 的新任务。请检查扩展，再发送一条普通文字提问。",
            }.get(state, "等待 {site} 的新任务。请检查扩展，再发送一条普通文字提问。")
            tone = 'warning' if state in ('failed', 'muted') else 'normal'
        self.status.setText(t(key).format(**args))
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
        self.baseline = None
        self.verified = self.read_error = self.chrome_error = False
        self.notice = None

    def go(self, step):
        if self.busy or not 0 <= step < len(STEPS):
            return
        if step > self.step and (not self.prepared or (self.step in (1, 2) and not self.confirmed[self.step])):
            return
        self.epoch += 1
        self.pending_status = False
        self.notice = None
        self.step = step
        self.render_step()
        if step == 3:
            self.check_events()

    def perform_primary(self):
        if self.busy:
            return
        if self.step == 0:
            self.go(1) if self.prepared else self.prepare()
        elif self.step == 1:
            self.go(2) if self.confirmed[1] else self.copy_chrome()
        elif self.step == 2:
            self.go(3) if self.confirmed[2] else self.copy_folder()
        elif self.verified:
            self.close()
        elif self.read_error:
            self.check_events()
        elif not self.configured:
            self.connect()
        elif self.source_enabled() is None:
            self.check_events()
        elif not self.source_enabled():
            self.enable_source()
        elif self.chrome_error:
            self.copy_site()
        elif self.baseline is None:
            self.start_verification()
        else:
            self.open_site()

    def choose_source(self):
        self.selected_source = self.site_choice.currentData()
        self.invalidate()
        self.render_step()
        self.check_events()

    def copy_chrome(self):
        QApplication.clipboard().setText('chrome://extensions/')
        self.tell("地址已复制。在 Chrome 地址栏粘贴并回车，再按下方截图操作。")

    def copy_folder(self):
        if self.prepared and self.path:
            QApplication.clipboard().setText(self.path)
            self.tell("路径已复制。切换到文件夹选择窗口，按下方说明粘贴。")

    def copy_site(self):
        QApplication.clipboard().setText(WEB_URLS[self.selected_source])
        self.tell("网站地址已复制。请在 Chrome 中粘贴打开。")

    def set_busy(self, value, text=''):
        self.busy = value
        self.busy_text = text
        self.refresh_controls()

    def prepare(self):
        if self.busy:
            return
        self.invalidate()
        self.prepared = self.configured = False
        self.confirmed = {1: False, 2: False}
        self.set_busy(True, "正在准备文件…")
        def received(data):
            self.prepared = bool(data.get('prepared') and data.get('path'))
            self.path = data.get('path', '')
            self.set_busy(False)
            self.render_step()
            if not self.prepared:
                self.failed("安装文件未准备好。请重试。")
        self.parent().act('/api/browser-extension/prepare', {}, received,
                          lambda: self.failed("未能准备安装文件。请重试，或查看下方具体原因。"))

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

    def start_verification(self):
        self.invalidate()
        self.set_busy(True, "正在开始验证…")
        def received(data):
            since = data.get('verification', {}).get(self.selected_source, {}).get('since')
            if not isinstance(since, (int, float)):
                self.failed("未能开始本次验证。请重试。")
                return
            self.snapshot = data
            self.baseline = {self.selected_source: since - 0.000001}
            self.set_busy(False)
            self.open_site()
        self.parent().act('/api/verification', {'source': self.selected_source}, received,
                          lambda: self.failed("未能开始本次验证。请重试。"))

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
        epoch = self.epoch
        def received(data):
            if self.busy or epoch != self.epoch:
                return
            path = data.get('path', '')
            prepared = bool(data.get('prepared') and path)
            if self.prepared and (not prepared or path != self.path):
                self.invalidate()
                self.configured = False
                self.confirmed = {1: False, 2: False}
                self.step = 0
            self.path, self.prepared = path, prepared
            self.render_step()
        self.parent().api.request('/api/browser-extension', received)
        self.update_timer()
        self.check_events()

    def update_timer(self):
        if self.isVisible() and self.step == 3:
            self.timer.start()
        else:
            self.timer.stop()

    def closeEvent(self, event):
        self.timer.stop()
        self.epoch += 1
        self.pending_status = False
        super().closeEvent(event)

    def check_events(self):
        if not self.isVisible() or self.step != 3 or self.busy or self.pending_status:
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
            if self.baseline is not None:
                observed = self.state()
                if observed in ('working', 'verified', 'failed'):
                    self.chrome_error = False
                    self.notice = None
                if observed == 'verified':
                    self.verified = True
            self.refresh_controls()
        def failed():
            if epoch != self.epoch:
                return
            self.pending_status = False
            self.read_error = True
            self.refresh_controls()
        self.parent().api.request('/api/status', received, failed=failed)

    def show_help(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("安装遇到问题")
        dialog.resize(640, 540)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        details = QVBoxLayout(body)
        items = [TROUBLESHOOTING[i] for i in HELP_FOR_STEP[self.step]]
        if self.step == 2:
            items.append(("其他系统的路径操作", '\n'.join(PATH_HELP.values())))
        items.append(("常规升级", UPGRADE))
        for question, answer in items:
            title = QLabel(question)
            title.setProperty('role', 'title')
            details.addWidget(title)
            label = QLabel(answer)
            label.setWordWrap(True)
            details.addWidget(label)
        details.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignRight)
        dialog.exec()

    def enlarge(self, filename, crop, caption):
        dialog = QDialog(self)
        dialog.setWindowTitle("截图局部放大")
        dialog.resize(880, 610)
        layout = QVBoxLayout(dialog)
        title = QLabel(caption)
        title.setWordWrap(True)
        layout.addWidget(title)
        controls = QHBoxLayout()
        zoom = QComboBox()
        zoom.setAccessibleName("截图缩放比例")
        for name, scale in (("适合窗口", 0), ("100%", 1), ("200%", 2)):
            zoom.addItem(name, scale)
        controls.addWidget(zoom)
        controls.addStretch()
        layout.addLayout(controls)
        scroll = QScrollArea()
        picture = QLabel()
        picture.setAccessibleName(caption + "：真实截图局部")
        pixmap = QPixmap(str(IMAGES / filename)).copy(*crop)
        scroll.setWidget(picture)
        layout.addWidget(scroll, 1)
        def scale_image():
            factor = zoom.currentData() or min(1, max(1, scroll.viewport().width() - 8) / pixmap.width())
            scaled = pixmap.scaled(round(pixmap.width()*factor), round(pixmap.height()*factor), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            picture.setPixmap(scaled)
            picture.resize(scaled.size())
        zoom.currentIndexChanged.connect(scale_image)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close, alignment=Qt.AlignRight)
        dialog.show()
        scale_image()
        dialog.exec()

    def fit_images(self):
        for label, pixmap in getattr(self, 'pictures', []):
            # Source captures are 2x; scale down to logical pixels, never upsample.
            width = min(pixmap.width() // 2, max(1, self.scroll.viewport().width() - 16))
            ratio = self.devicePixelRatioF()
            scaled = pixmap.scaledToWidth(min(pixmap.width(), round(width * ratio)), Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(ratio)
            label.setPixmap(scaled)
            label.setFixedHeight(round(scaled.height() / ratio))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'scroll'):
            self.fit_images()
