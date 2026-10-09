"""Restore one existing window from Dock, tray, or another desktop launch."""

from aiflame.desktop.localization import t

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QMainWindow, QMessageBox


class DesktopWindow(QMainWindow):
    def __init__(self, hub=None):
        super().__init__()
        self.hub = hub
        self.background_available = False
        self.quitting = False

    def closeEvent(self, event):
        if self.hub and self.hub.devices.busy():
            QMessageBox.information(
                self, t("设备维护中"), t("设备连接操作正在进行，请等待完成后退出。")
            )
            event.ignore()
        elif self.background_available and not self.quitting:
            self.hide()
            event.ignore()
        else:
            event.accept()


class WindowRestorer(QObject):
    def __init__(self, app, window, activate=None):
        super().__init__(app)
        self.app, self.window = app, window
        self.activate = activate
        self.pending = False
        self.closed = False
        app.installEventFilter(self)

    def request(self):
        if not self.closed and not self.pending:
            self.pending = True
            # Native menu actions must finish before we activate the window.
            QTimer.singleShot(0, self.restore)

    def restore(self):
        if self.closed:
            return
        try:
            if self.window.isMinimized():
                self.window.setWindowState(
                    self.window.windowState() & ~Qt.WindowState.WindowMinimized
                )
            self.window.show()
            if self.activate:
                self.activate()
            self.window.raise_()
            self.window.activateWindow()
            modal = self.app.activeModalWidget()
            if modal and modal is not self.window:
                modal.raise_()
                modal.activateWindow()
        finally:
            self.pending = False

    def eventFilter(self, watched, event):
        if (
            watched is self.app
            and event.type() == QEvent.Type.ApplicationActivate
            and (self.window.isHidden() or self.window.isMinimized())
        ):
            # Qt forwards Cocoa's applicationShouldHandleReopen to this event,
            # even if the app is already active with all its windows hidden.
            self.request()
        return False

    def close(self):
        self.closed = True
        self.app.removeEventFilter(self)
