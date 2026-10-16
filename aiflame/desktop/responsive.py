"""Follow native display scaling and keep the window inside its current screen."""

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QToolTip
from shiboken6 import isValid


class ScreenAdaptation(QObject):
    """React to resolution, work-area and monitor changes without overriding DPI."""

    def __init__(self, dashboard):
        super().__init__(dashboard)
        self.root = dashboard
        self.window = dashboard.window()
        self.screen = self.handle = None
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.window.installEventFilter(self)
        self.schedule()

    def schedule(self, *_):
        if not self.closed and not self.timer.isActive():
            self.timer.start(0)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in (
            QEvent.Type.Show, QEvent.Type.ScreenChangeInternal,
            QEvent.Type.DevicePixelRatioChange,
        ):
            self.schedule()
        return False

    def bind_screen(self):
        handle = self.window.windowHandle()
        if handle is not self.handle:
            if self.handle and isValid(self.handle):
                self.handle.screenChanged.disconnect(self.schedule)
            self.handle = handle
            if handle:
                handle.screenChanged.connect(self.schedule)
        screen = handle.screen() if handle else self.window.screen()
        if screen is self.screen:
            return
        self.disconnect_screen()
        self.screen = screen
        if screen:
            for signal in (screen.geometryChanged, screen.availableGeometryChanged,
                           screen.logicalDotsPerInchChanged):
                signal.connect(self.schedule)

    def disconnect_screen(self):
        if self.screen and isValid(self.screen):
            for signal in (self.screen.geometryChanged, self.screen.availableGeometryChanged,
                           self.screen.logicalDotsPerInchChanged):
                signal.disconnect(self.schedule)

    def refresh(self):
        if self.closed:
            return
        self.bind_screen()
        if self.screen:
            self.fit_window(self.screen.availableGeometry())
        QToolTip.hideText()
        self.root.reflow_layout()

    def fit_window(self, available):
        if self.window is self.root or available.isEmpty():
            return
        # QScreen and QWidget coordinates are both logical pixels. Native Qt
        # handles Retina/DPI; applying devicePixelRatio here would double-scale.
        window = self.window
        frame = window.frameGeometry()
        max_width = max(1, available.width() - max(0, frame.width() - window.width()))
        max_height = max(1, available.height() - max(0, frame.height() - window.height()))
        window.setMinimumSize(min(920, max_width), min(680, max_height))
        if window.isMaximized() or window.isFullScreen():
            return
        if window.width() > max_width or window.height() > max_height:
            window.resize(min(window.width(), max_width), min(window.height(), max_height))
        frame = window.frameGeometry()
        x = max(available.left(), min(frame.x(), available.right() - frame.width() + 1))
        y = max(available.top(), min(frame.y(), available.bottom() - frame.height() + 1))
        if frame.x() != x or frame.y() != y:
            window.move(x, y)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.window.removeEventFilter(self)
        if self.handle and isValid(self.handle):
            self.handle.screenChanged.disconnect(self.schedule)
        self.disconnect_screen()
