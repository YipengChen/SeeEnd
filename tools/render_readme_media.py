"""Export transparent documentation animations from local-only preview widgets.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python tools/render_readme_media.py
Requires the desktop dependencies and Pillow. No Hub, device or user config is used.
"""

import io
import os
from functools import lru_cache
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QWidget

from aiflame.desktop.light_controls import LightStateCanvas
from aiflame.devices.cube_effects import mini_snake_game, snake_game
from aiflame.devices.light_settings import light_settings
from aiflame.devices.models import (YEELIGHT_MODEL, YEELIGHT_BULB_MODEL, YEELIGHT_BEAM_MODEL,
                                    YEELIGHT_CUBE_MODEL, MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL)

THEMES = {"light": "#24292f", "dark": "#f0f6fc"}
STATES = (("working", "Working"), ("needs_input", "Waiting for input"), ("done", "Done"))
FPS = 10
STATE_DURATION_MS = 1200
FRAMES_PER_STATE = STATE_DURATION_MS * FPS // 1000
LABELS = {
    "zh": {"working": "工作中", "needs_input": "等待输入", "done": "已完成"},
    "en": {"working": "Working", "needs_input": "Waiting for input", "done": "Done"},
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
    canvas.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    canvas.setAutoFillBackground(False)
    canvas.resize(300, 120)
    return panel, canvas


def image(width, height):
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    return result


def caption(state, language):
    return ("任务状态：" if language == "zh" else "Task status: ") + LABELS[language][state]


@lru_cache(maxsize=4)
def caption_layout(width, language):
    # Keep font size fixed as states change; fit the longest status.
    for size in range(42, 15, -1):
        font = QFont("PingFang SC" if language == "zh" else "Arial")
        font.setPixelSize(size)
        font.setBold(True)
        metrics = QFontMetricsF(font)
        if max(metrics.horizontalAdvance(caption(key, language)) for key, _ in STATES) <= width - 24:
            return font
    raise RuntimeError("Caption does not fit the animation width")


def status_bar(painter, width, height, state, language, theme):
    painter.setFont(caption_layout(width, language))
    painter.setPen(QColor(THEMES[theme]))
    painter.drawText(QRectF(12, height - 76, width - 24, 60), Qt.AlignmentFlag.AlignCenter, caption(state, language))


def capture(canvas, painter, x, y, scale=1):
    painter.save()
    painter.translate(x, y)
    painter.scale(scale, scale)
    canvas.render(painter, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
    painter.restore()


def pillow(qimage):
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    qimage.save(buffer, "PNG")
    return Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA")


def save(frames, path):
    # Lossless animated WebP preserves soft alpha edges and avoids a fixed matte.
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=1000 // FPS,
                   loop=0, lossless=True, background=(0, 0, 0, 0), method=4)
    print(f"{path.relative_to(ROOT)}: {path.stat().st_size:,} bytes")


def light(path, model, mode, language, theme):
    panel, canvas = preview(model, mode)
    canvas.resize(300, 140)
    frames = []
    for state, _ in STATES:
        for tick in range(FRAMES_PER_STATE):
            output = image(640, 390)
            painter = QPainter(output)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            panel.clock.milliseconds = tick * 1000 // FPS
            canvas.state = state
            capture(canvas, painter, 20, 10, 2)
            status_bar(painter, 640, 390, state, language, theme)
            painter.end()
            frames.append(pillow(output))
    save(frames, path)


def plug(path, model, language, theme):
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
        status_bar(painter, 640, 390, state, language, theme)
        painter.end()
        for _ in range(FRAMES_PER_STATE):
            frames.append(pillow(output))
    save(frames, path)


def cube(path, mode, language, theme):
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
        for tick in range(FRAMES_PER_STATE):
            panel.clock.milliseconds = tick * 1000 // FPS
            canvas.state = state
            output = image(640, 300)
            painter = QPainter(output)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            capture(canvas, painter, 20, 0, 2)
            status_bar(painter, 640, 300, state, language, theme)
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
        for theme in THEMES:
            themed = localized / theme
            themed.mkdir(exist_ok=True)
            for model, name in ((YEELIGHT_MODEL, "screen-light-bar"), (YEELIGHT_BULB_MODEL, "w3-bulb"),
                                (YEELIGHT_BEAM_MODEL, "beam")):
                for mode, suffix in (("front", "white"), ("back", "color")):
                    light(themed / f"{name}-{suffix}.webp", model, mode, language, theme)
            for model, name in ((MIJIA_PLUG_MODEL, "mijia-plug-3"), (MIJIA_PLUG4_MODEL, "mijia-plug-4")):
                plug(themed / f"{name}.webp", model, language, theme)
            for mode in ("status", "comet", "snake", "text"):
                cube(themed / f"cube-{mode}.webp", mode, language, theme)
    application.quit()


if __name__ == "__main__":
    main()
