"""Native controls and a vector light with bounded, visible-only motion."""

import math
from pathlib import Path
from functools import lru_cache

from PySide6.QtCore import QByteArray, QElapsedTimer, QPointF, QRectF, QSize, Qt, QTimer, Signal, QEvent
from PySide6.QtGui import (
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

ICONS = {
    "home": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><rect x="14" y="14" width="7" height="7" rx="2"/>',
    "device": '<rect x="6" y="3" width="12" height="18" rx="4"/><path d="M10 17h4M10 7h4"/>',
    "apps": '<rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="7" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/><path d="M14 17.5h7m-3.5-3.5v7"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2" fill="{fill}"/><circle cx="16" cy="12" r="2" fill="{fill}"/><circle cx="8" cy="18" r="2" fill="{fill}"/>',
    "history": '<path d="M3 11a9 9 0 1 1 3 7M3 5v6h6m3-4v5l3 2"/>',
    "help": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4m0 3h.01"/>',
    "arrow": '<path d="M5 12h14m-5-5 5 5-5 5"/>',
    "play": '<path d="m8 5 11 7-11 7z"/>',
    "shield": '<path d="m12 3 8 3v6c0 4-5 8-8 9-3-1-8-5-8-9V6zM8 12l3 3 5-6"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
    "flame": '<path d="M13 2c4 8-7 9-5 15 1-4 4-4 4-8 8 6 10 10 5 13-6 4-15-1-12-7 0-3 3-5 3-7 0 2 1 3 1 4-1-6 6-6 4-10z"/>',
    "power": '<path d="M12 2v9M6 5a9 9 0 1 0 12 0"/>',
    "wifi": '<path d="M3 8a15 15 0 0 1 18 0M6 12a10 10 0 0 1 12 0M9 16a5 5 0 0 1 6 0"/><circle cx="12" cy="20" r=".7"/>',
    "usb": '<rect x="3" y="4" width="6" height="7" rx="1"/><path d="M5 2v2m2-2v2M6 11v4a5 5 0 0 0 10 0v-2"/><rect x="13" y="7" width="6" height="6" rx="1"/><path d="M15 5v2m2-2v2"/>',
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
        source, source.removesuffix("-web").removesuffix("-desktop")
    )
    if brand in {
        "codex", "claude", "qoder", "gemini", "opencode", "chatgpt", "deepseek",
        "qianwen", "doubao", "yuanbao", "kimi", "zhipu"
    }:
        asset = Path(__file__).resolve().parents[1] / "assets/web/brands" / f"{brand}.png"
        return QIcon(str(asset))
    glyph = {"local-script": "apps", "manual": "flame", "demo": "play"}.get(source, "apps")
    return icon(glyph, size=30)


def label(text="", muted=False, role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(role not in ("eyebrow", "badge", "brand"))
    widget.setProperty("role", role or ("muted" if muted else "body"))
    return widget


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
        layout.addWidget(label(subtitle, True))
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
        self.thumbnail.setProperty("kind", "matrix" if "mapping" in model else "rgb")
        self.thumbnail.setFixedSize(96, 112)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        asset = Path(__file__).resolve().parents[1] / "assets/web/devices" / (model["id"] + ".svg")
        self.thumbnail.setPixmap(QIcon(str(asset)).pixmap(92, 100))
        row.addWidget(self.thumbnail)
        content = QVBoxLayout()
        content.setSpacing(5)
        brand = label("AI FLAME" if "mapping" in model else "YEELIGHT", role="deviceBrand")
        content.addWidget(brand)
        self.title = label(model["name"], role="deviceName")
        content.addWidget(self.title)
        self.detail = label(model["card_description"], True)
        content.addWidget(self.detail)
        connection = label("USB / Wi-Fi" if "mapping" in model else "Wi-Fi 局域网", role="deviceMeta")
        content.addWidget(connection)
        footer = QHBoxLayout()
        self.connection_count = label(role="deviceCount")
        footer.addWidget(self.connection_count)
        footer.addStretch()
        arrow = label("→", role="deviceArrow")
        footer.addWidget(arrow)
        content.addLayout(footer)
        row.addLayout(content, 1)
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.set_connection_count(0)

    def set_connection_count(self, count):
        self.connection_count.setText(f"{count} 台在线" if count else "未连接")
        restyle(self.connection_count, "online", bool(count))
        self.setAccessibleName(f"选择 {self.model['name']}，{self.model['card_description']}，已连接 {count} 台")


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


class NetworkComboBox(QComboBox):
    popup_requested = Signal()

    def setEditable(self, editable):
        super().setEditable(editable)
        if editable:
            self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton and not self.view().isVisible():
            QTimer.singleShot(0, self.showPopup)
        return super().eventFilter(watched, event)

    def showPopup(self):
        self.popup_requested.emit()
        super().showPopup()

    def reopen_popup(self):
        # Refresh an already-open list without requesting a second scan.
        super().showPopup()


class Disclosure(QWidget):
    def __init__(self, title, parent_layout):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.button = QPushButton("＋  " + title)
        self.button.setProperty("variant", "quiet")
        self.button.setCheckable(True)
        self.button.setAccessibleName(title)
        self.body = QWidget()
        self.content = QVBoxLayout(self.body)
        self.content.setContentsMargins(4, 4, 4, 4)
        self.content.setSpacing(12)
        self.body.hide()
        self.button.toggled.connect(self.body.setVisible)
        self.button.toggled.connect(
            lambda value: self.button.setText(("−  " if value else "＋  ") + title)
        )
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.body)
        parent_layout.addWidget(self)


class FlameVisual(QWidget):
    """Blend light states from the current frame, including interrupted changes."""

    TRANSITION_MS = 650

    def __init__(self, parent=None):
        super().__init__(parent)
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
        active = self.state in ("working", "done", "error")
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
        elif self.state == "done":
            flutter = 0.6 * math.sin(seconds * 6.7) + 0.4 * math.sin(seconds * 13.1)
            width, height = 1.0 - 0.012 * flutter, 1.0 + 0.025 * flutter
            opacity, sway, halo_alpha = 0.96 + 0.04 * flutter, 0.022 * flutter, 72
        elif self.state == "error":
            phase = seconds % 4
            pulse = max(0, math.sin(math.pi * phase)) ** 2 if phase < 2 else 0
            opacity, halo_alpha = (0.6 + 0.4 * pulse, 45) if animated else (0.8, 36)
        ember = "#f58d47" if self.state != "error" else "#ed795b"
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        scale = min(self.width() / 380, self.height() / 245)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(scale, scale)
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
