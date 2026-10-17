"""Two actions: install the store extension and allow local communication."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QApplication, QSizePolicy
from aiflame.desktop.localization import QDialog, QLabel, QPushButton, t, source_text
from aiflame.desktop.browser_launch import open_in_chrome
from aiflame.integrations.chatgpt_web.guide import STORE_URL, STEPS


class ExtensionGuide(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("extensionGuide")
        self.setWindowTitle("SeeEnd · 安装浏览器扩展")
        self.resize(580, 360)
        self.setMinimumSize(480, 340)
        self.configured = self.busy = False
        self.store_message = self.connection_error = ""
        self.titles = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        for index, (_, description, _) in enumerate(STEPS):
            if index:
                layout.addSpacing(16)
            title = QLabel()
            title.setProperty("role", "guideTitle")
            title.setProperty("literalText", True)
            title.setWordWrap(True)
            self.titles.append(title)
            layout.addWidget(title)
            text = QLabel(description)
            text.setWordWrap(True)
            text.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            layout.addWidget(text)
            if index == 0:
                self.store_button = QPushButton("前往应用商店")
                self.store_button.clicked.connect(self.open_store)
                self.store_button.setAutoDefault(False)
                layout.addWidget(self.store_button, alignment=Qt.AlignLeft)
                self.store_status = self.status_label(layout)
                self.copy_button = QPushButton("复制商店链接")
                self.copy_button.setProperty("variant", "quiet")
                self.copy_button.setAutoDefault(False)
                self.copy_button.clicked.connect(self.copy_store)
                self.copy_button.hide()
                layout.addWidget(self.copy_button, alignment=Qt.AlignLeft)
            else:
                self.primary = QPushButton("允许连接")
                self.primary.setProperty("variant", "primary")
                self.primary.setAutoDefault(False)
                self.primary.clicked.connect(self.perform_primary)
                layout.addWidget(self.primary, alignment=Qt.AlignLeft)
                self.status = self.status_label(layout)
        layout.addStretch()
        self.refresh_controls()

    @staticmethod
    def status_label(layout):
        label = QLabel()
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setProperty("literalText", True)
        label.setProperty("role", "guideStatus")
        label.hide()
        layout.addWidget(label)
        return label

    def retranslate(self):
        super().retranslate()
        if hasattr(self, "status"):
            self.refresh_controls()

    @staticmethod
    def show_status(label, message, tone):
        label.setText(message)
        label.setVisible(bool(message))
        label.setProperty("guideTone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def refresh_controls(self):
        for index, title in enumerate(self.titles):
            title.setText(f"{index + 1}. {t(STEPS[index][0])}")
        self.store_button.setEnabled(not self.busy)
        self.copy_button.setEnabled(not self.busy)
        self.primary.setEnabled(not self.busy)
        self.primary.setText("正在允许连接…" if self.busy else "完成" if self.configured else "允许连接")
        self.show_status(self.store_status, t(self.store_message), "warning")
        message = t(self.connection_error)
        if self.connection_error and getattr(self.parent(), "message_error", False):
            message += "\n" + t("具体原因：") + t(source_text(self.parent().message))
        if self.configured:
            message = t("已允许扩展连接 SeeEnd。")
        self.show_status(self.status, message, "success" if self.configured else "warning")
        if self.store_message or message:
            self.adjustSize()

    def open_store(self):
        if self.busy:
            return
        opened = open_in_chrome(STORE_URL)
        self.store_message = "" if opened else "未能打开 Chrome。请复制商店链接，在 Chrome 中打开并安装扩展。"
        self.copy_button.setVisible(not opened)
        self.refresh_controls()

    def copy_store(self):
        QApplication.clipboard().setText(STORE_URL)

    def perform_primary(self):
        if self.configured:
            self.accept()
        else:
            self.connect()

    def connect(self):
        if self.busy:
            return
        self.busy = True
        self.connection_error = ""
        self.refresh_controls()

        def received(data):
            self.busy = False
            self.configured = True
            self.refresh_controls()

        def failed():
            self.busy = False
            self.connection_error = "未能允许连接，请重试。"
            self.refresh_controls()

        self.parent().act('/api/integrations/install', {'source': 'chatgpt-web'}, received, failed)
