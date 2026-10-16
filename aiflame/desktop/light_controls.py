"""Local-only light selectors and previews. Never issue device commands."""
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QElapsedTimer, QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QHBoxLayout, QSizePolicy, QVBoxLayout
from aiflame.desktop.localization import QWidget, QFrame, QPushButton
from aiflame.desktop.components import label
from aiflame.desktop.theme import COLORS
from aiflame.devices.models import (YEELIGHT_CUBE_MODEL, YEELIGHT_MODEL,
                                    MIJIA_PLUG_MODELS)
from aiflame.devices.cube_effects import CUBE_MODES, frame
from aiflame.devices.light_settings import light_settings
from aiflame.transports.yeelight import beam_pixel
from aiflame.transports.mijia import desired_power


class LightModePicker(QWidget):
    """Visible exclusive choices with the existing selector's data interface."""
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None, columns=3):
        super().__init__(parent)
        self.columns = columns
        self.entries = []
        self.index = -1
        self.group = QButtonGroup(self)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def addItem(self, title, data):
        button = QPushButton(title)
        button.setObjectName('lightModeChoice')
        button.setCheckable(True)
        button.setMinimumHeight(44)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.installEventFilter(self)
        index = len(self.entries)
        self.entries.append((button, data))
        self.group.addButton(button, index)
        button.clicked.connect(lambda checked=False, index=index: self.setCurrentIndex(index))
        self.grid.addWidget(button, index // self.columns, index % self.columns)
        if self.index == -1:
            self.setCurrentIndex(0)

    def clear(self):
        for button, _ in self.entries:
            self.group.removeButton(button)
            self.grid.removeWidget(button)
            button.hide()
            button.deleteLater()
        self.entries = []
        self.index = -1

    def count(self):
        return len(self.entries)

    def currentIndex(self):
        return self.index

    def setCurrentIndex(self, index):
        if not 0 <= index < self.count():
            return
        changed = index != self.index
        self.index = index
        self.entries[index][0].setChecked(True)
        if changed:
            self.currentIndexChanged.emit(index)

    def currentData(self):
        return self.entries[self.index][1] if 0 <= self.index < self.count() else None

    def currentText(self):
        return self.itemText(self.index) if self.index >= 0 else ''

    def itemText(self, index):
        return self.entries[index][0].text()

    def setItemText(self, index, title):
        self.entries[index][0].setText(title)

    def findData(self, value):
        return next((i for i, (_, data) in enumerate(self.entries) if data == value), -1)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            step = 1
            if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
                step = -1
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                step *= min(self.columns, self.count())
            index = (self.group.id(obj) + step) % self.count()
            self.entries[index][0].setFocus()
            self.setCurrentIndex(index)
            return True
        return super().eventFilter(obj, event)


@lru_cache(maxsize=8)
def device_svg(model):
    return (Path(__file__).resolve().parents[1] / 'assets/devices' / (model + '.svg')).read_text(encoding='utf-8')


def screenbar_glow(rgb, front):
    """Layered fading light cones; SVG gradients work in Qt without blur filters."""
    peak = max(rgb)
    if not peak:
        return ''
    hue = QColor(*(round(channel * 255 / peak) for channel in rgb)).name()
    strength = peak / 255
    start, end = (83, 195) if front else (74, 0)
    defs = f'<defs><linearGradient id="scatter" gradientUnits="userSpaceOnUse" x1="105" y1="{start}" x2="105" y2="{end}">'
    for offset, opacity in ((0, .85), (.25, .68), (.65, .3), (1, 0)):
        defs += f'<stop offset="{offset}" stop-color="{hue}" stop-opacity="{opacity * strength}"/>'
    defs += '</linearGradient></defs>'
    # Overlapping broad cones feather the sides instead of drawing a hard wedge.
    cones = ''
    for inset, opacity in ((0, .18), (5, .22), (10, .25), (16, .28)):
        cones += (f'<path d="M{28 + inset / 2} {start} H{182 - inset / 2} '
                  f'L{210 - inset} {end} H{inset} Z" fill="url(#scatter)" opacity="{opacity}"/>')
    # Quiet radial streaks make the volume visible without outlining the cone.
    rays = ''
    for x in (42, 68, 96, 124, 152, 174):
        spread = (x - 105) * 1.85 + 105
        rays += (f'<path d="M{x - 3} {start} H{x + 3} L{spread + 9} {end} '
                 f'H{spread - 9} Z" fill="url(#scatter)" opacity=".2"/>')
    return defs + cones + rays


class LightStateCanvas(QWidget):
    def __init__(self, state, panel):
        super().__init__(panel)
        self.state, self.panel = state, panel
        self.setMinimumHeight(92)
        self.setMinimumWidth(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def pixels(self, elapsed):
        panel = self.panel
        desired = {'key': 'local-preview', 'state': self.state, 'duration_ms': 10000,
                   'cube_animation_id': ('local-preview', id(panel), panel.revision)}
        if panel.model == YEELIGHT_CUBE_MODEL and panel.settings.get('reminder_mode') in CUBE_MODES:
            return frame(desired, panel.settings, elapsed % 30 if self.state == "working" else elapsed)
        return beam_pixel(desired, panel.settings, elapsed) * 100

    def paintEvent(self, event):
        panel = self.panel
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        elapsed = panel.clock.elapsed() / 1000
        rect = QRectF(self.rect()).adjusted(6, 4, -6, -4)
        if panel.model == YEELIGHT_CUBE_MODEL:
            data = self.pixels(elapsed)
            cell = min(rect.width() / 20, 10)
            left = rect.center().x() - cell * 10
            top = rect.center().y() - cell * 2.5
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#252723'))
            painter.drawRoundedRect(QRectF(left - 4, top - 4, cell * 20 + 8, cell * 5 + 8), 6, 6)
            for y in range(5):
                for x in range(20):
                    offset = ((4 - y) * 20 + x) * 3
                    rgb = data[offset:offset + 3]
                    painter.setBrush(QColor(*rgb) if any(rgb) else QColor('#373b34'))
                    painter.drawRoundedRect(QRectF(left + x * cell + 1, top + y * cell + 1,
                                                  cell - 2, cell - 2), 1, 1)
        else:
            plug = panel.model in MIJIA_PLUG_MODELS
            if plug:
                on = desired_power({'state': self.state}, panel.settings)
                rgb = (255, 208, 160) if on else (0, 0, 0)
            else:
                rgb = tuple(beam_pixel({'state': self.state}, panel.settings, elapsed))
            tint = QColor(*rgb).name() if any(rgb) else '#595c57'
            size = min(rect.height(), rect.width())
            area = QRectF(rect.center().x() - size / 2, rect.top(), size, size)
            if plug:
                plug_area = QRectF(rect.center().x() - 71, rect.center().y() - 15, 56, 56)
                QSvgRenderer(QByteArray(device_svg(panel.model).encode())).render(painter, plug_area)
                area = QRectF(rect.center().x() + 1, rect.center().y() - 45, 76, 86)
                painter.setPen(QPen(QColor(COLORS['muted']), 1))
                painter.drawLine(plug_area.right() - 8, rect.center().y() + 27,
                                 area.left() + 22, rect.center().y() + 27)
                connector = QRectF(plug_area.right() - 10, rect.center().y() + 19, 20, 16)
                QSvgRenderer(QByteArray(device_svg('power-plug').encode())).render(painter, connector)
            svg = device_svg('desk-lamp' if plug else panel.model)
            svg = svg.replace('fill="url(#color)"', f'fill="{tint}"')
            svg = svg.replace('fill="url(#rgb)"', f'fill="{tint}"')
            if plug and not on:
                svg = svg.replace('opacity=".16"', 'opacity="0"').replace('opacity=".2"', 'opacity="0"')
            if panel.model == YEELIGHT_MODEL:
                front = panel.settings.get('reminder_mode') == 'front'
                svg = svg.replace('viewBox="0 0 210 210"', 'viewBox="-30 0 270 210"')
                svg = svg.replace('fill="#e9e5dc"', 'fill="#485054"')
                area = QRectF(rect.center().x() - size * 270 / 420, rect.top(), size * 270 / 210, size)
                svg = svg.replace('fill="#fff0cc"', f'fill="{tint if front else "#595c57"}"')
                svg = svg.replace(f'<rect x="19" y="72" width="172" height="12" rx="6" fill="{tint}" opacity=".3"/>', '')
                glow = screenbar_glow(rgb, front)
                if front:
                    svg = svg.replace('</svg>', glow + '</svg>')
                else:
                    svg = svg.replace('</defs>', '</defs>' + glow, 1)
            QSvgRenderer(QByteArray(svg.encode())).render(painter, area)
        painter.end()


class LightPreviewPanel(QWidget):
    stateSelected = Signal(str)
    STATES = (('working', '工作中'), ('needs_input', '待输入'), ('done', '已完成'))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_state = None
        self.model = YEELIGHT_CUBE_MODEL
        self.settings = {}
        self.revision = 0
        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.animate)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(label('状态预览', role='lightStateTitle'))
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(label('点击状态预览，展开参数并同步设备试亮。', True))
        self.grid = QGridLayout()
        self.grid.setSpacing(8)
        layout.addLayout(self.grid)
        self.cards, self.canvases, self.captions = [], {}, {}
        for state, title in self.STATES:
            card = QPushButton()
            card.setCheckable(True)
            card.setMinimumHeight(180)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            card.setCursor(Qt.CursorShape.PointingHandCursor)
            card.setAccessibleName(title + '状态参数')
            card.clicked.connect(lambda _, key=state: self.select_state(key))
            card.installEventFilter(self)
            card.setObjectName('lightPreviewTile')
            box = QVBoxLayout(card)
            box.setContentsMargins(10, 12, 10, 10)
            box.setSpacing(4)
            box.addWidget(label(title, role='lightStateTitle'))
            canvas = LightStateCanvas(state, self)
            canvas.setAccessibleName(title + '灯效示意')
            box.addWidget(canvas)
            caption = label('', True)
            caption.setMinimumHeight(32)
            box.addWidget(caption)
            for child in (canvas, caption, box.itemAt(0).widget()):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.cards.append(card)
            self.canvases[state] = canvas
            self.captions[state] = caption
        self.columns = 0
        self.reflow()
        self.configure(self.model, light_settings({'hardware_model': self.model}))

    def select_state(self, state):
        if state is not None and state not in dict(self.STATES):
            return
        if state and state != self.selected_state:
            self.clock.restart()
        self.selected_state = state
        for card, (key, _) in zip(self.cards, self.STATES):
            card.setChecked(key == state)
        self.stateSelected.emit(state or '')

    def eventFilter(self, watched, event):
        if watched in self.cards and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            index = self.cards.index(watched)
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
                step = -1 if key in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
                index = (index + step) % len(self.cards)
                self.cards[index].setFocus()
                self.select_state(self.STATES[index][0])
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.select_state(self.STATES[index][0])
                return True
        return super().eventFilter(watched, event)

    def configure(self, model, settings):
        if self.model == model and self.settings == settings:
            return
        self.model, self.settings = model, deepcopy(settings)
        self.revision += 1
        self.clock.restart()
        mode = settings.get('reminder_mode')
        patterns = {
            'status': ('左侧四点跳动', '问号 · 单个任务', '对勾 · 单个任务'),
            'comet': ('拖尾往返', '两端交替', '从中心展开'),
            'snake': ('随机吃豆', '停下等你喂食', '吃饱盘起休息'),
            'text': ('WORK', 'WAIT', 'DONE'),
        }
        for index, (state, _) in enumerate(self.STATES):
            group = state if state in ('working', 'done') else 'needs_input'
            if model in MIJIA_PLUG_MODELS:
                caption = '灯具亮起' if desired_power({'state': state}, settings) else '灯具熄灭'
            elif model == YEELIGHT_CUBE_MODEL and mode in patterns:
                caption = patterns[mode][index]
            elif mode == 'front':
                caption = '白灯闪烁' if group == 'needs_input' and settings.get('front_flash', {}).get('enabled') else '白灯常亮'
            else:
                caption = '彩灯常亮'
            brightness = settings.get('front_state_brightness' if mode == 'front' else 'state_brightness', {}).get(group, 50)
            if model not in MIJIA_PLUG_MODELS and brightness == 0:
                caption = '灯具熄灭'
            self.captions[state].setText(caption)
            self.canvases[state].setAccessibleDescription(caption)
            self.canvases[state].update()

    def reflow(self):
        columns = 3 if self.width() >= 480 else 1
        if columns != self.columns:
            self.columns = columns
            for index, card in enumerate(self.cards):
                self.grid.addWidget(card, index // columns, index % columns)

    def resizeEvent(self, event):
        self.reflow()
        super().resizeEvent(event)

    def showEvent(self, event):
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def animate(self):
        if not self.visibleRegion().isEmpty():
            for canvas in self.canvases.values():
                canvas.update()
