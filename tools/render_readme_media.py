"""Export documentation GIFs from the app's local-only light preview widgets.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_readme_media.py
Requires the desktop dependencies and Pillow. No Hub, device or user config is used.
"""

import io
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QWidget

from aiflame.desktop.light_controls import LightStateCanvas
from aiflame.devices.cube_effects import mini_snake_game, snake_game
from aiflame.devices.light_settings import light_settings
from aiflame.devices.light_settings import COLOR_DEFAULTS
from aiflame.devices.models import (YEELIGHT_MODEL, YEELIGHT_BULB_MODEL, YEELIGHT_BEAM_MODEL,
                                    YEELIGHT_CUBE_MODEL, MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL)
from aiflame.transports.mijia import desired_power

BACKGROUND = "#14201a"
INK = "#f3f6ef"
STATES = (("working", "Working"), ("needs_input", "Waiting for input"), ("done", "Done"))
FPS = 10
SECONDS = 3
LABELS = {
    "zh": {"working": "工作中", "needs_input": "等待输入", "done": "已完成",
           "back": "彩灯", "front": "白灯", "power_on": "已通电", "power_off": "已断电",
           "status": "像素状态牌", "comet": "彗星巡航", "snake": "贪吃蛇", "text": "文字状态"},
    "en": {"working": "Working", "needs_input": "Waiting for input", "done": "Done",
           "back": "Color", "front": "White", "power_on": "Power on", "power_off": "Power off",
           "status": "Pixel status", "comet": "Comet", "snake": "Snake", "text": "Text"},
}


class Clock:
    milliseconds = 0

    def elapsed(self):
        return self.milliseconds


def display_settings(model, mode):
    settings = light_settings({"hardware_model": model, "light_settings": {"reminder_mode": mode}})
    if model not in (MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL):
        # Documentation uses supported high-brightness parameters, not user defaults.
        settings["state_brightness"] = {state: 100 for state, _ in STATES}
        settings["front_state_brightness"] = {state: 100 for state, _ in STATES}
    return settings


def preview(model, mode):
    panel = QWidget()
    panel.model = model
    panel.settings = display_settings(model, mode)
    panel.clock = Clock()
    panel.revision = panel.selection_revision = 0
    canvas = LightStateCanvas("working", panel)
    palette = canvas.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BACKGROUND))
    canvas.setPalette(palette)
    canvas.setAutoFillBackground(True)
    canvas.resize(300, 120)
    return panel, canvas


def image(width, height):
    result = QImage(width, height, QImage.Format.Format_RGB32)
    result.fill(QColor(BACKGROUND))
    return result


def text(painter, rect, value, size=17, color=INK, language="en", bold=False):
    font = QFont("PingFang SC" if language == "zh" else "Arial", size)
    font.setPixelSize(size)
    font.setBold(bold)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, value)


def status_bar(painter, width, height, state, detail, language):
    y = height - 92
    painter.fillRect(QRectF(0, y, width, 92), QColor(COLOR_DEFAULTS[state]))
    text(painter, QRectF(12, y + 5, width - 24, 28), LABELS[language][detail],
         23, BACKGROUND, language)
    text(painter, QRectF(12, y + 31, width - 24, 54), LABELS[language][state],
         42, BACKGROUND, language, bold=True)


def capture(canvas, painter, x, y, scale=1):
    painter.save()
    painter.translate(x, y)
    painter.scale(scale, scale)
    canvas.render(painter, QPoint(0, 0))
    painter.restore()


def pillow(qimage):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    qimage.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGB")


def save(frames, path):
    # A shared palette keeps constant backgrounds and LED colors stable between frames.
    sample = Image.new("RGB", (frames[0].width, frames[0].height * len(frames)))
    for index, frame in enumerate(frames):
        sample.paste(frame, (0, index * frame.height))
    palette = sample.quantize(colors=125, method=Image.Quantize.MEDIANCUT)
    colors = palette.getpalette()[:125 * 3]
    for value in COLOR_DEFAULTS.values():
        colors.extend(tuple(QColor(value).getRgb()[:3]))
    palette.putpalette(colors)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(path, save_all=True, append_images=indexed[1:], duration=1000 // FPS,
                    loop=0, optimize=True, disposal=1)
    print(f"{path.relative_to(ROOT)}: {path.stat().st_size:,} bytes")


def light(path, model, language):
    panel, canvas = preview(model, "back")
    canvas.resize(300, 140)
    frames = []
    for mode in ("back", "front"):
        panel.settings = display_settings(model, mode)
        for state, _ in STATES:
            for tick in range(SECONDS * FPS):
                output = image(640, 390)
                painter = QPainter(output)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                panel.clock.milliseconds = tick * 1000 // FPS
                canvas.state = state
                capture(canvas, painter, 20, 10, 2)
                status_bar(painter, 640, 390, state, mode, language)
                painter.end()
                frames.append(pillow(output))
    save(frames, path)


def plug(path, model, language):
    panel, canvas = preview(model, "back")
    canvas.resize(300, 140)
    frames = []
    # The first still shows the connected lamp on; all three actual power rules
    # remain visible in the repeating sequence (waiting/done on, working off).
    for state, _ in STATES[1:] + STATES[:1]:
        canvas.state = state
        output = image(640, 390)
        painter = QPainter(output)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        capture(canvas, painter, 20, 10, 2)
        power = "power_on" if desired_power({"state": state}, panel.settings) else "power_off"
        status_bar(painter, 640, 390, state, power, language)
        painter.end()
        for _ in range(SECONDS * FPS):
            frames.append(pillow(output))
    save(frames, path)


def cube(path, mode, language):
    panel, canvas = preview(YEELIGHT_CUBE_MODEL, mode)
    # Use the exact animation identity used by LightStateCanvas, with reproducible food.
    key = ("local-preview", id(panel), panel.revision)
    game = snake_game(key)
    game.rng.seed(0)
    game.reset()
    mini_snake_game(key).reset()
    frames = []
    for index, (state, _) in enumerate(STATES):
        panel.selection_revision = index
        for tick in range(SECONDS * FPS):
            panel.clock.milliseconds = tick * 1000 // FPS
            canvas.state = state
            output = image(640, 300)
            painter = QPainter(output)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            capture(canvas, painter, 20, 0, 2)
            status_bar(painter, 640, 300, state, mode, language)
            painter.end()
            frames.append(pillow(output))
    save(frames, path)


def main():
    application = QApplication.instance() or QApplication([])
    destination = ROOT / "docs/images/light-effects"
    destination.mkdir(parents=True, exist_ok=True)
    for language in LABELS:
        localized = destination / language
        localized.mkdir(exist_ok=True)
        for model, name in ((YEELIGHT_MODEL, "screen-light-bar"), (YEELIGHT_BULB_MODEL, "w3-bulb"),
                            (YEELIGHT_BEAM_MODEL, "beam")):
            light(localized / f"{name}.gif", model, language)
        for model, name in ((MIJIA_PLUG_MODEL, "mijia-plug-3"), (MIJIA_PLUG4_MODEL, "mijia-plug-4")):
            plug(localized / f"{name}.gif", model, language)
        for mode in ("status", "comet", "snake", "text"):
            cube(localized / f"cube-{mode}.gif", mode, language)
    application.quit()


if __name__ == "__main__":
    main()
