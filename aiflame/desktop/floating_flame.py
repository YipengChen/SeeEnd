"""A quiet, draggable task flame that stays visible outside the main window."""

import json

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QMenu, QVBoxLayout

from aiflame.core.paths import runtime_dir, write_private
from .components import FlameVisual
from .localization import QWidget, set_language, t
from .tray import light_status
from .task_return import return_unavailable_message


def bounded_position(position, size, screens):
    """Keep the whole pet on the closest available desktop, including negative coordinates."""
    if not screens:
        return position
    frame = QRect(position, size)
    def proximity(screen):
        overlap = frame.intersected(screen)
        distance = frame.center() - screen.center()
        return overlap.width() * overlap.height(), -(distance.x() ** 2 + distance.y() ** 2)
    screen = max(screens, key=proximity)
    return QPoint(
        max(screen.left(), min(position.x(), screen.right() - size.width() + 1)),
        max(screen.top(), min(position.y(), screen.bottom() - size.height() + 1)),
    )


class DesktopFlame(QWidget):
    open_requested = Signal()
    return_requested = Signal(str)
    disable_requested = Signal()
    visibility_changed = Signal(bool)
    menu_visible_changed = Signal(bool)

    def __init__(self, position_path=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle("SeeEnd · 桌面火焰")
        self.setAccessibleName("桌面火焰")
        self.setFixedSize(184, 144)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.closed = False
        self.settings = None
        self.state = "unavailable"
        self.latest_task = None
        self.double_click_pending = False
        self.last_release_dragged = False
        self.drag_offset = None
        self.press_position = None
        self.drag_started = False
        self.position_path = position_path or runtime_dir() / "desktop-flame.json"
        self.positioned = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 8)
        layout.setSpacing(0)
        self.flame = FlameVisual(self, show_environment=False)
        self.flame.setMinimumSize(0, 0)
        self.flame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.flame.silhouette_changed.connect(self.update_input_region)
        layout.addWidget(self.flame, 1)
        if QGuiApplication.platformName() == "cocoa":
            from .macos_panel import create_nonactivating_panel
            create_nonactivating_panel(self)
        QGuiApplication.instance().screenRemoved.connect(self.keep_on_screen)

    def pointer_interaction_active(self):
        if self.closed or not self.isVisible():
            return False
        if self.drag_offset is not None:
            return True
        buttons = bool(QGuiApplication.mouseButtons())
        if QGuiApplication.platformName() == "cocoa":
            from AppKit import NSEvent
            # Native activation may arrive before Qt updates mouseButtons().
            buttons = bool(NSEvent.pressedMouseButtons())
        return buttons and self.mask().contains(self.mapFromGlobal(QCursor.pos()))

    def update_input_region(self, region):
        # A native window mask lets clicks reach windows underneath the empty
        # canvas; ignoring a mouse event alone would still swallow that click.
        region = region.translated(self.flame.pos())
        if region != self.mask():
            self.setMask(region)

    def update_status(self, data):
        if self.closed:
            return
        if data:
            self.settings = data["settings"]
            set_language(self.settings.get("language", "zh-CN"))
        self.latest_task = data.get("latest_task") if data else None
        visible = bool(self.settings and self.settings.get("desktop_flame_enabled", True))
        state, title = light_status(data)
        self.state = state
        self.setAccessibleDescription(t(title))
        self.setToolTip(t(title) + "\n" + t("双击返回最新任务，拖动调整位置，右键可打开 SeeEnd 或关闭桌面火焰。"))
        self.flame.set_state(state if state in ("working", "done", "needs_input") else "idle",
                             reduced=bool(self.settings and self.settings.get("reduced")), live=True)
        if visible and self.drag_offset is None:
            self.keep_on_screen()
        was_visible = self.isVisible()
        self.setVisible(visible)
        if visible != was_visible:
            self.visibility_changed.emit(visible)

    def keep_on_screen(self, _screen=None):
        if self.closed:
            return
        screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
        if not self.positioned:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            area = screen.availableGeometry()
            position = area.bottomRight() - QPoint(self.width() + 23, self.height() + 23)
            try:
                saved = json.loads(self.position_path.read_text())
                if isinstance(saved, list) and len(saved) == 2 and all(type(value) is int for value in saved):
                    position = QPoint(*saved)
            except (OSError, ValueError):
                pass
            self.move(position)
            self.positioned = True
        self.move(bounded_position(self.pos(), self.size(), screens))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.pos()
            self.press_position = event.globalPosition().toPoint()
            self.drag_started = False
            self.double_click_pending = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if (event.globalPosition().toPoint() - self.press_position).manhattanLength() >= QGuiApplication.styleHints().startDragDistance():
                self.drag_started = True
            if self.drag_started:
                self.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.drag_offset is not None:
            dragged = self.drag_started or (
                event.globalPosition().toPoint() - self.press_position
            ).manhattanLength() >= QGuiApplication.styleHints().startDragDistance()
            self.last_release_dragged = dragged
            return_task = self.double_click_pending and not dragged
            self.double_click_pending = False
            self.drag_offset = None
            self.press_position = None
            self.drag_started = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.keep_on_screen()
            if dragged:
                try:
                    write_private(self.position_path, [self.x(), self.y()])
                except OSError:
                    pass
            if return_task:
                message = ("本地服务未连接，请重新打开 SeeEnd。" if self.state == "unavailable"
                           else return_unavailable_message(self.latest_task))
                if message:
                    from PySide6.QtWidgets import QToolTip
                    QToolTip.showText(event.globalPosition().toPoint(), t(message), self)
                else:
                    self.return_requested.emit(self.latest_task["id"])
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Wait for release: the second press may still become a drag.
            self.mousePressEvent(event)
            self.double_click_pending = not self.last_release_dragged
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction(t("打开 SeeEnd"), self.open_requested.emit)
        menu.addAction(t("关闭桌面火焰"), self.disable_requested.emit)
        self.menu_visible_changed.emit(True)
        try:
            menu.exec(event.globalPos())
        finally:
            self.menu_visible_changed.emit(False)
            menu.deleteLater()

    def closeEvent(self, event):
        if self.closed:
            event.accept()
        else:
            event.ignore()
            self.disable_requested.emit()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        QGuiApplication.instance().screenRemoved.disconnect(self.keep_on_screen)
        self.flame.animation_timer.stop()
        self.hide()
        self.close()
        self.deleteLater()
