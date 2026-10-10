"""Small, cached status icons and lightweight polling while the window is hidden."""

from PySide6.QtCore import QByteArray, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from .components import ICONS
from .localization import t, set_language


def light_status(data):
    state, title = _light_status(data)
    if data:
        if data["settings"].get("mode") == "hardware" and not data["device"]["online"]:
            title += " · 正在连接设备 · 屏幕提醒" if data["device"].get("connecting") and not data["maintenance"] else " · 硬件未连接 · 屏幕提醒"
        elif data["settings"].get("mode") == "virtual":
            title += " · 虚拟模式 · 屏幕提醒"
    return state, title


def _light_status(data):
    if data is None:
        return "unavailable", "本地服务未连接"
    settings = data["settings"]
    if data["maintenance"]:
        return "maintenance", "设备维护中"
    if settings["paused"]:
        return "paused", "灯光提醒已暂停"
    if data.get("brightness_off", settings["brightness"] == 0):
        return "off", "灯光已熄灭 · 亮度为 0%"
    if data["muted"]:
        return "quiet", "静默时段"
    state = data["display"]["state"]
    title = {
        "idle": "当前无任务",
        "working": "任务进行中",
        "done": "任务已完成",
        "error": "需关注",
    }.get(state)
    if title and data["display"].get("simulated"):
        title += " · 含测试提醒，不计入验证"
    return (state, title) if title else ("unavailable", "灯光状态未知")


def status_icon(state, dark=False):
    """Use both silhouette and badges, so color is not the only status cue."""
    ink = "#eee8db" if dark else "#353b32"
    rim = "#252a24" if dark else "#fffaf0"
    active = state in ("working", "done", "error")
    fill = {"working": "#d78e36", "done": "#ffc16b", "error": "#ef775d"}.get(
        state, "none"
    )
    scale = 0.91 if state in ("done", "error") else 0.70
    # Keep the light-state flame distinct from the app's eye-shaped brand icon.
    flame = ICONS["flame"]
    body = (
        f'<g transform="translate(10.5 21.5) scale({scale}) translate(-12 -22)">'
        f'<g fill="{fill}" stroke="{rim}" stroke-width="3.5">{flame}</g>'
        f'<g fill="{fill}" stroke="{ink}" stroke-width="1.6">{flame}</g></g>'
    )
    badge = {
        "done": '<path d="m15 17 2 2 3.5-4"/>',
        "error": '<path d="M18 13.5v4m0 2v.1"/>',
        "paused": '<path d="M16.4 14v5m3.2-5v5"/>',
        "quiet": '<path d="M19 13a3.5 3.5 0 1 0 2 5 3.8 3.8 0 0 1-2-5z"/>',
        "off": '<path d="m15.5 19 5-5"/>',
        "offline": '<path d="m16 15 4 4m0-4-4 4"/>',
        "unavailable": '<path d="M16.5 15a1.5 1.5 0 0 1 3 0c0 1-1.5 1-1.5 2m0 2v.1"/>',
        "maintenance": '<path d="M15.5 15h5m-5 3h5m-3-4v2m1 1v2"/>',
    }.get(state)
    if badge:
        badge_fill = fill if active else rim
        badge_ink = "#403124" if active else ink
        body += (
            f'<circle cx="18" cy="17" r="5.4" fill="{badge_fill}" stroke="{rim}" stroke-width="1"/>'
            f'<g fill="none" stroke="{badge_ink}" stroke-width="1.6" stroke-linecap="round" '
            f'stroke-linejoin="round">{badge}</g>'
        )
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    icon = QIcon()
    for ratio in (1, 2, 3):
        pixmap = QPixmap(22 * ratio, 22 * ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        icon.addPixmap(pixmap)
    return icon


class TrayIndicator(QObject):
    state_changed = Signal(object)

    def __init__(self, tray, dashboard, status_action, parent=None):
        super().__init__(parent)
        self.tray, self.dashboard, self.status_action = tray, dashboard, status_action
        self.icons = {}
        self.data = None
        self.icon_key = None
        self.title = None
        self.closed = False
        self.hints = QGuiApplication.styleHints()
        self.hints.colorSchemeChanged.connect(self.refresh_icon)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.poll)
        self.update(None)

    def start(self):
        if not self.closed:
            self.timer.start()
            self.poll()

    def poll(self):
        if self.closed:
            return
        if not self.dashboard.ready:
            self.dashboard.api.request(
                "/api/session", self.connected, failed=self.disconnected
            )
            return
        generation = self.dashboard.generation

        def received(data):
            if not self.closed and generation == self.dashboard.generation:
                self.update(data)

        self.dashboard.api.request("/api/indicator", received, failed=self.disconnected)

    def connected(self, _):
        if not self.closed:
            self.dashboard.ready = True
            self.poll()

    def disconnected(self):
        if not self.closed:
            self.dashboard.ready = False
            self.update(None)

    def update(self, data):
        if self.closed:
            return
        self.data = data
        if data:
            set_language(data["settings"].get("language", "zh-CN"))
        self.refresh_icon()
        self.state_changed.emit(data)

    def set_paused(self, paused):
        if not self.closed and self.data is not None:
            self.dashboard.act("/api/settings", {"paused": paused})

    def refresh_icon(self, _scheme=None):
        if self.closed:
            return
        state, title = light_status(self.data)
        title = t(title)
        key = state, self.hints.colorScheme() == Qt.ColorScheme.Dark
        if key != self.icon_key:
            if key not in self.icons:
                self.icons[key] = status_icon(*key)
            self.tray.setIcon(self.icons[key])
            self.icon_key = key
        if title != self.title:
            self.tray.setToolTip("SeeEnd · " + title)
            self.status_action.setText("灯光 · " + light_status(self.data)[1])
            self.title = title

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        self.hints.colorSchemeChanged.disconnect(self.refresh_icon)
