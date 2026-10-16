"""Native controls and a vector light with bounded, visible-only motion."""

import math
from html import escape
from pathlib import Path
from functools import lru_cache

from PySide6.QtCore import QByteArray, QElapsedTimer, QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBitmap,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QRegion,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QToolTip, QVBoxLayout, QTabBar
from aiflame.desktop.localization import QCheckBox, QFrame, QLabel, QPushButton, QSlider, QWidget, localized_type, t
from aiflame.desktop.theme import COLORS

ICONS = {
    "home": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "device": '<rect x="6" y="3" width="12" height="18" rx="4"/><path d="M10 17h4M10 7h4"/>',
    "apps": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><path d="M14 17.5h7m-3.5-3.5v7"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2" fill="{fill}"/><circle cx="16" cy="12" r="2" fill="{fill}"/><circle cx="8" cy="18" r="2" fill="{fill}"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    "message": '<path d="M21 11a8 8 0 0 1-8 8H8l-5 3 1-6a8 8 0 0 1-1-5 8 8 0 0 1 8-8h2a8 8 0 0 1 8 8zM8 10h8M8 14h5"/>',
    "history": '<path d="M3 11a9 9 0 1 1 3 7M3 5v6h6m3-4v5l3 2"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4m0 3h.01"/>',
    "arrow": '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    "back": '<path d="M19 12H5m5-5-5 5 5 5"/>',
    "chevron": '<path d="m9 5 7 7-7 7"/>',
    "chevron-down": '<path d="m5 9 7 7 7-7"/>',
    "external": '<path d="M14 3h7v7m0-7L10 14M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/>',
    "close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "play": '<path d="m8 5 11 7-11 7z"/>',
    "shield": '<path d="m12 3 8 3v6c0 4-5 8-8 9-3-1-8-5-8-9V6zM8 12l3 3 5-6"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "flame": '<path d="M13 2c4 8-7 9-5 15 1-4 4-4 4-8 8 6 10 10 5 13-6 4-15-1-12-7 0-3 3-5 3-7 0 2 1 3 1 4-1-6 6-6 4-10z"/>',
    "power": '<path d="M12 2v9M6 5a9 9 0 1 0 12 0"/>',
    "wifi": '<path d="M3 8a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0M9 16a5 5 0 0 1 6 0"/><circle cx="12" cy="20" r=".7"/>',
}


def icon(name, color="#767266", size=20):
    body = ICONS.get(name, ICONS["home"]).replace("{fill}", "#eeede8")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


@lru_cache(maxsize=None)
def brand_icon(source):
    brand = {"claude-code": "claude", "deepseek-harness": "deepseek", "gemini-cli": "gemini"}.get(
        source, source.removesuffix("-web")
    )
    if brand in {
        "codex", "claude", "qoder", "gemini", "opencode", "chatgpt", "deepseek",
        "qianwen", "doubao", "yuanbao", "kimi", "zhipu"
    }:
        asset = Path(__file__).resolve().parents[1] / "assets/brands" / f"{brand}.png"
        return QIcon(str(asset))
    glyph = {"local-script": "apps", "manual": "flame", "demo": "play"}.get(source, "apps")
    return icon(glyph, size=30)


class ElidedLabel(QLabel):
    """Keep full accessible text while allowing a one-line label to shrink."""
    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def paintEvent(self, event):
        from PySide6.QtGui import QPalette
        painter = QPainter(self)
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(self.contentsRect(), self.alignment() | Qt.TextFlag.TextSingleLine,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.contentsRect().width()))


class ReadableLabel(QLabel):
    """Measure wrapped text at the same bounded width used to draw it."""

    def heightForWidth(self, width):
        return super().heightForWidth(min(width, self.maximumWidth()))


def label(text="", muted=False, role=None, *, elide=False, readable=False):
    widget = ElidedLabel(text) if elide else ReadableLabel(text) if readable else QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(role not in ("eyebrow", "badge", "brand"))
    widget.setProperty("role", role or ("muted" if muted else "body"))
    if readable:
        widget.setMaximumWidth(960)
    if role == "sectionTitle":
        widget.setIndent(0)
    return widget


class HelpButton(QPushButton):
    """Native hover help, also available by mouse click or keyboard activation."""

    TEXT_WIDTH = 360

    def __init__(self, text, title="设置"):
        super().__init__()
        self.setObjectName("preferenceHelp")
        self.setIcon(icon("help", size=16))
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.WhatsThisCursor)
        self.setAccessibleName(title + "说明")
        self.set_help(text)
        self.clicked.connect(self.show_help)

    def set_help(self, text):
        self.setToolTip(text)
        self.setAccessibleDescription(text)

    def show_help(self):
        self._show_at(self.mapToGlobal(QPoint(0, self.height())))

    def _show_at(self, position):
        width = min(self.TEXT_WIDTH, max(1, self.screen().availableGeometry().width() - 48))
        body = "<p>" + escape(self.toolTip()).replace("\n", "<br>") + "</p>"
        # Keep native rich-text help at a consistent reading width.
        html = (f'<table width="{width}" cellspacing="0" cellpadding="0">'
                f"<tr><td>{body}</td></tr></table>")
        QToolTip.showText(position, html, self, self.rect(), 20000)

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            self._show_at(event.globalPos())
            event.accept()
            return True
        return super().event(event)


class ValueSlider(QSlider):
    """Keep wheel scrolling available to the page without changing a value."""
    def wheelEvent(self, event):
        event.ignore()


class TemperatureSwatch(QWidget):
    """Illustrative warm-to-cool white swatch, independent of lamp brightness."""
    def __init__(self):
        super().__init__()
        self.setFixedSize(20, 20)
        self.set_temperature(2700)

    def set_temperature(self, kelvin):
        self.kelvin = kelvin
        from aiflame.devices.light_settings import temperature_rgb
        self.color = QColor(*temperature_rgb(kelvin))
        self.setToolTip(f"{kelvin}K · 色温示意")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#dedcd3"), 1))
        painter.setBrush(self.color)
        painter.drawRoundedRect(QRectF(.5, .5, self.width() - 1, self.height() - 1), 4, 4)


def restyle(widget, name, value):
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def card(parent_layout, title=None, subtitle=None, name="card"):
    frame = QFrame()
    frame.setObjectName(name)
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    if title:
        layout.addWidget(label(title, role="title"))
    if subtitle:
        layout.addWidget(label(subtitle, True, readable=True))
    parent_layout.addWidget(frame)
    return layout


def separator(layout, name="separator"):
    line = QFrame()
    line.setObjectName(name)
    line.setFixedHeight(1)
    layout.addWidget(line)


class DeviceModelCard(QPushButton):
    """A single accessible click target with independent device information."""

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.setObjectName("deviceModelCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(300)
        self.setFixedHeight(172)
        self.setAccessibleDescription("选择此型号，添加或配置你的设备")
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 18, 18, 18)
        row.setSpacing(16)
        self.thumbnail = label()
        self.thumbnail.setObjectName("deviceThumbnail")
        self.thumbnail.setProperty("kind", "rgb")
        self.thumbnail.setFixedSize(96, 112)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        asset = Path(__file__).resolve().parents[1] / "assets/devices" / (model["id"] + ".svg")
        self.thumbnail.setPixmap(QIcon(str(asset)).pixmap(92, 100))
        row.addWidget(self.thumbnail)
        content = QVBoxLayout()
        content.setSpacing(5)
        brand = label("米家" if model["id"].startswith("mijia-") else "Yeelight", role="deviceBrand")
        content.addWidget(brand)
        self.title = label(model["name"], role="deviceName")
        content.addWidget(self.title)
        self.detail = label(model["card_description"], True)
        content.addWidget(self.detail)
        connection = label("Wi-Fi 局域网", role="deviceMeta")
        content.addWidget(connection)
        footer = QHBoxLayout()
        self.connection_count = label(role="deviceCount")
        footer.addWidget(self.connection_count)
        footer.addStretch()
        arrow = label()
        arrow.setPixmap(icon("chevron", "#625f57").pixmap(20, 20))
        footer.addWidget(arrow)
        content.addLayout(footer)
        row.addLayout(content, 1)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.set_connection_count(0)

    def set_connection_count(self, online, offline=0):
        self.connection_count.setText(f"在线 {online} · 离线 {offline}" if online + offline else "未添加设备")
        restyle(self.connection_count, "online", bool(online))
        self.setAccessibleName(f"选择 {self.model['name']}，{self.model['card_description']}，在线 {online} 台，离线 {offline} 台")


class Toggle(QCheckBox):
    """Native accessible checkbox with a generously sized switch hit target."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        width = 48 + (
            self.fontMetrics().horizontalAdvance(self.text()) + 10 if self.text() else 0
        )
        return QSize(width, 32)

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, point):
        return self.rect().contains(point)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(1 if self.isEnabled() else 0.45)
        y = (self.height() - 22) / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#c85b2b" if self.isChecked() else "#cbc8be"))
        painter.drawRoundedRect(QRectF(1, y, 38, 22), 11, 11)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(20 if self.isChecked() else 4, y + 3, 16, 16))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#a04a24"), 1))
            painter.drawRoundedRect(QRectF(0, y - 3, 42, 28), 13, 13)
        painter.setPen(QColor("#4c4940"))
        painter.drawText(
            self.rect().adjusted(50, 0, 0, 0),
            Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )


class Disclosure(QWidget):
    def __init__(self, title, parent_layout):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.button = QPushButton(title)
        self.button.setIcon(icon("chevron"))
        self.button.setProperty("variant", "quiet")
        self.button.setCheckable(True)
        self.button.setAccessibleName(title)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(4, 4, 4, 4)
        self.content.setSpacing(12)
        self.body.hide()
        self.button.toggled.connect(self.body.setVisible)
        self.button.toggled.connect(lambda value: self.button.setIcon(icon("chevron-down" if value else "chevron")))
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.body)
        parent_layout.addWidget(self)


class CategoryTabs(localized_type(QTabBar)):
    """Native tabs with the same data lookup used by the page controller."""

    def __init__(self, parent=None):
        self._captions = []
        super().__init__(parent)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setUsesScrollButtons(False)
        # Selected-tab typography is polished after the selection changes.
        self.currentChanged.connect(lambda _: QTimer.singleShot(0, self, self.updateGeometry))

    def addItem(self, text, data):
        self._captions.append(text)
        index = self.addTab(t(text))
        self.setTabData(index, data)

    def itemData(self, index):
        return self.tabData(index)

    def currentData(self):
        return self.tabData(self.currentIndex())

    def findData(self, data):
        return next((index for index in range(self.count()) if self.tabData(index) == data), -1)

    def retranslate(self):
        super().retranslate()
        for index, caption in enumerate(self._captions):
            self.setTabText(index, t(caption))


class HomeSourceButton(QPushButton):
    """Icon shortcut with a full name and official address on hover."""
    def __init__(self, source, name, link):
        super().__init__()
        self.link = link
        self.setObjectName("homeSourceButton")
        self.setProperty("variant", "brand")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 44)
        self.setIcon(brand_icon(source))
        self.setIconSize(QSize(32, 32))
        self.setAccessibleDescription(link)
        self.set_name(name)

    def sizeHint(self):
        # Brand buttons have no text; retain a usable target under the shared QSS.
        return QSize(44, 44)

    def set_name(self, name):
        self.setAccessibleName(name)
        self.setToolTip(
            '<p style="white-space: nowrap; margin: 0;"><b>' + escape(name) + '</b><br/>'
            '<span style="color: ' + COLORS['secondary'] + '; font-size: 12px;">'
            + escape(self.link) + '</span></p>'
        )


class DeviceInstanceRow(QPushButton):
    """A registered device is one accessible target, with its current status."""

    def __init__(self, model):
        super().__init__()
        self.model_name = model["name"]
        self.setObjectName("deviceModelCard")
        self.setProperty("instance", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(88)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(16)
        thumbnail = label()
        asset = Path(__file__).resolve().parents[1] / "assets/devices" / (model["id"] + ".svg")
        thumbnail.setPixmap(QIcon(str(asset)).pixmap(52, 56))
        thumbnail.setFixedSize(56, 60)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(thumbnail)
        copy = QVBoxLayout()
        copy.setSpacing(4)
        self.name = label(role="deviceName", elide=True)
        self.name.setProperty("literalText", True)
        self.description = label(model["card_description"], True, elide=True)
        self.name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for text in (self.name, self.description):
            text.setContentsMargins(0, 0, 0, 0)
            text.setIndent(0)
        copy.addWidget(self.name)
        copy.addWidget(self.description)
        row.addLayout(copy, 1)
        self.status = label(role="sourceStatus")
        row.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        arrow = label()
        arrow.setPixmap(icon("chevron").pixmap(20, 20))
        row.addWidget(arrow)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def update_device(self, device):
        name = device.get("name") or t(self.model_name)
        if name == self.model_name:
            name = t(name)
        self.name.setText(name)
        status = "正在连接" if device.get("connecting") else "在线" if device.get("online") else "已断开" if device.get("connection_paused") else "离线"
        self.status.setText(status)
        restyle(self.status, "tone", "success" if device.get("online") else "neutral")
        self.setAccessibleName(name + " · " + status + " · 查看设备")
        self.setToolTip(name + " · " + device["device_id"])


class FlameVisual(QWidget):
    """Blend light states from the current frame, including interrupted changes."""

    return_requested = Signal()
    silhouette_changed = Signal(QRegion)
    TRANSITION_MS = 650

    def __init__(self, parent=None, *, show_environment=True):
        super().__init__(parent)
        self.show_environment = show_environment
        self.state = "idle"
        self.preview = False
        self.live = False
        self.reduced = False
        self.clock = QElapsedTimer()
        self.clock.start()
        self.transition_clock = QElapsedTimer()
        self.transition_from = None
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(50)
        self.animation_timer.timeout.connect(self._animate)
        self.setMinimumSize(200, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("屏幕任务状态；连接灯具后可另行检查实体发光")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.return_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def set_state(self, state, preview=False, reduced=False, live=False):
        if (self.state, self.preview, self.reduced, self.live) != (
            state,
            preview,
            reduced,
            live,
        ):
            current = self._frame()
            if (self.state, self.preview) != (state, preview):
                self.clock.restart()
            self.state, self.preview, self.reduced = state, preview, reduced
            self.live = live
            if self.isVisible() and not reduced:
                self.transition_from = current
                self.transition_clock.start()
            else:
                self.transition_from = None
            self._sync_animation()
            self.update()

    def _sync_animation(self):
        if self.transition_from is not None and (
            self.reduced
            or not self.isVisible()
            or self.transition_clock.elapsed() >= self.TRANSITION_MS
        ):
            self.transition_from = None
        previewing = (
            (self.preview or self.live)
            and not self.reduced
            and self.state != "idle"
            and (self.live or self.clock.elapsed() < 6000)
        )
        if self.isVisible() and (self.transition_from is not None or previewing):
            # Smooth the brief transition at 60 Hz; breathing needs only 20 Hz.
            self.animation_timer.setInterval(16 if self.transition_from else 50)
            self.animation_timer.start()
        else:
            self.animation_timer.stop()

    def _animate(self):
        self._sync_animation()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event):
        self.animation_timer.stop()
        self.transition_from = None
        super().hideEvent(event)

    def _appearance(self):
        active = self.state in ("working", "done", "needs_input")
        animated = (self.preview or self.live) and not self.reduced
        seconds = self.clock.elapsed() / 1000 if animated else 0
        width, height, opacity, sway, halo_alpha = 1.0, 1.0, 1.0, 0.0, 16
        if self.state == "idle":
            # Match working at the midpoint of its gentle breathing cycle.
            width, height = 0.71, 0.60
        elif self.state == "working":
            # Same slow four-second breathing rhythm as the device; always lower
            # and dimmer than completion, including at its brightest frame.
            breath = (1 - math.cos(seconds * math.pi / 2)) / 2 if animated else 0.5
            width, height = 0.69 + 0.04 * breath, 0.57 + 0.06 * breath
            opacity, halo_alpha = 0.58 + 0.20 * breath, int(15 + 14 * breath)
            if not self.show_environment:
                # Keep the desktop flame visible at the dimmest breathing phase.
                opacity = 0.40 + 0.60 * breath
        elif self.state == "done":
            flutter = 0.6 * math.sin(seconds * 6.7) + 0.4 * math.sin(seconds * 13.1)
            width, height = 1.0 - 0.012 * flutter, 1.0 + 0.025 * flutter
            opacity, sway, halo_alpha = 0.96 + 0.04 * flutter, 0.022 * flutter, 72
        elif self.state in ("needs_input",):
            phase = seconds % 4
            pulse = max(0, math.sin(math.pi * phase)) ** 2 if phase < 2 else 0
            opacity, halo_alpha = (0.6 + 0.4 * pulse, 45) if animated else (0.8, 36)
        ember = "#ed795b" if self.state in ("needs_input",) else "#f58d47"
        if self.state == "working":
            palette = ("#efc57d", "#d79543", "#ad632c", "#734525")
        elif active:
            palette = ("#ffe9b5", "#ffc475", ember, "#c86031")
        else:
            palette = ("#c9c6ae", "#959681", "#6a6e5c", "#4b5143")
        spread = min(seconds / 0.7, 1) if animated else 1
        halo_color = QColor(ember)
        halo_color.setAlpha(halo_alpha)
        return {
            "width": width,
            "height": height,
            "opacity": opacity,
            "sway": sway,
            "halo": halo_color,
            "ring_spread": spread,
            "ring_alpha": 140 - 40 * spread if self.state == "done" else 0,
            **dict(zip(("top", "upper", "lower", "bottom"), map(QColor, palette))),
            "outline": QColor("#dfb77a" if active else "#939683"),
            "core_top": 100 if self.state == "working" else 215 if active else 36,
            "core_bottom": 40 if self.state == "working" else 75 if active else 16,
            "indicator": QColor("#eaa56c" if active else "#8a907b"),
        }

    def _frame(self):
        target = self._appearance()
        if self.transition_from is None:
            return target
        progress = min(max(self.transition_clock.elapsed() / self.TRANSITION_MS, 0), 1)
        # Zero velocity and acceleration at both ends: no abrupt start or stop.
        mix = progress**3 * (progress * (progress * 6 - 15) + 10)
        frame = {}
        for key, end in target.items():
            start = self.transition_from[key]
            if isinstance(end, QColor):
                frame[key] = QColor.fromRgbF(
                    *(a + (b - a) * mix for a, b in zip(start.getRgbF(), end.getRgbF()))
                )
            else:
                frame[key] = start + (end - start) * mix
        return frame

    def paintEvent(self, event):
        frame = self._frame()
        surface = None
        if not self.show_environment:
            # Render once, using those same pixels for the desktop input mask.
            # Keep Retina rendering sharp while the mask uses logical pixels.
            ratio = self.devicePixelRatioF()
            surface = QPixmap(round(self.width() * ratio), round(self.height() * ratio))
            surface.setDevicePixelRatio(ratio)
            surface.fill(Qt.GlobalColor.transparent)
        painter = QPainter(surface if surface is not None else self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 380, self.height() / 245)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(scale, scale)
        if self.show_environment:
            halo = QRadialGradient(QPointF(0, 9), 155)
            halo.setColorAt(0, frame["halo"])
            edge = QColor(frame["halo"])
            edge.setAlpha(0)
            halo.setColorAt(1, edge)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(QRectF(-172, -141, 344, 300))
            painter.setPen(QPen(QColor("#43453a"), 0.8))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(-144, 72, 288, 42))
            painter.drawEllipse(QRectF(-113, 77, 226, 30))
            if frame["ring_alpha"] > 0:
                spread = frame["ring_spread"]
                painter.setPen(QPen(QColor(240, 181, 109, round(frame["ring_alpha"])), 1.5))
                painter.drawEllipse(
                    QRectF(
                        -118 - 15 * spread,
                        72 - 3 * spread,
                        236 + 30 * spread,
                        35 + 6 * spread,
                    )
                )
            shadow = QRadialGradient(QPointF(0, 90), 88)
            shadow.setColorAt(0, QColor(0, 0, 0, 100))
            shadow.setColorAt(1, QColor(0, 0, 0, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawEllipse(QRectF(-96, 73, 192, 40))
        painter.setPen(Qt.PenStyle.NoPen)
        base = QLinearGradient(-66, 60, 66, 90)
        base.setColorAt(0, QColor("#57574d"))
        base.setColorAt(0.5, QColor("#34362f"))
        base.setColorAt(1, QColor("#181c17"))
        painter.setBrush(base)
        painter.drawRoundedRect(QRectF(-57, 56, 114, 37), 15, 15)
        painter.setBrush(QColor("#656358"))
        painter.drawEllipse(QRectF(-57, 46, 114, 29))
        painter.setBrush(QColor("#45463d"))
        painter.drawEllipse(QRectF(-48, 49, 96, 21))
        painter.save()
        # Anchor every state to the same light base, including breathing and sway.
        painter.translate(0, 63)
        painter.shear(frame["sway"], 0)
        painter.scale(frame["width"], frame["height"])
        painter.translate(0, -63)
        painter.setOpacity(frame["opacity"])
        flame = QPainterPath(QPointF(14, -110))
        flame.cubicTo(40, -64, -23, -49, -9, -5)
        flame.cubicTo(2, -28, 33, -41, 29, -72)
        flame.cubicTo(99, -3, 58, 63, 1, 63)
        flame.cubicTo(-59, 62, -73, 19, -46, -19)
        flame.cubicTo(-41, -1, -33, 5, -32, 5)
        flame.cubicTo(-39, -52, 12, -66, 14, -110)
        glow = QLinearGradient(0, -110, 0, 64)
        for position, key in zip(
            (0, 0.42, 0.76, 1), ("top", "upper", "lower", "bottom")
        ):
            glow.setColorAt(position, frame[key])
        painter.setBrush(glow)
        painter.setPen(QPen(frame["outline"], 0.7))
        painter.drawPath(flame)
        core = QPainterPath(QPointF(7, -26))
        core.cubicTo(33, 4, 39, 41, 6, 52)
        core.cubicTo(-24, 54, -34, 30, -15, 7)
        core.cubicTo(-16, 21, -5, 22, -4, 25)
        core.cubicTo(-5, 0, 9, -7, 7, -26)
        inner = QLinearGradient(0, -26, 0, 52)
        inner.setColorAt(
            0,
            QColor(255, 248, 222, round(frame["core_top"])),
        )
        inner.setColorAt(
            1,
            QColor(255, 206, 115, round(frame["core_bottom"])),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(inner)
        painter.drawPath(core)
        painter.restore()
        painter.setBrush(frame["indicator"])
        painter.drawEllipse(QRectF(-2, 78, 4, 4))
        painter.end()
        if surface is not None:
            painter = QPainter(self)
            painter.drawPixmap(0, 0, surface)
            painter.end()
            pixels = surface.toImage().scaled(self.size(), Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
            # Include even faint breathing/antialiasing pixels, excluding only
            # fully transparent pixels (an alpha threshold would lose them).
            mask = QBitmap.fromImage(pixels.createMaskFromColor(0, Qt.MaskMode.MaskInColor))
            self.silhouette_changed.emit(QRegion(mask))
