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
from aiflame.devices.models import YEELIGHT_MODEL, YEELIGHT_BULB_MODEL, YEELIGHT_BEAM_MODEL, YEELIGHT_CUBE_MODEL

BACKGROUND = "#f6f5f1"
INK = "#26322b"
MUTED = "#737b73"
STATES = (("working", "Working"), ("needs_input", "Waiting for input"), ("done", "Done"))
FPS = 10
SECONDS = 3


class Clock:
    milliseconds = 0

    def elapsed(self):
        return self.milliseconds


def preview(model, mode):
    panel = QWidget()
    panel.model = model
    panel.settings = light_settings({"hardware_model": model, "light_settings": {"reminder_mode": mode}})
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


def text(painter, rect, value, size=17, color=INK):
    font = QFont("Arial", size)
    font.setPixelSize(size)
    painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, value)


def legend(painter, width, y, state):
    for index, (key, label) in enumerate(STATES):
        active = state == key
        text(painter, QRectF(index * width / 3, y, width / 3, 32),
             ("● " if active else "") + label, 19, INK if active else MUTED)


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
    palette = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    indexed[0].save(path, save_all=True, append_images=indexed[1:], duration=1000 // FPS,
                    loop=0, optimize=True, disposal=1)
    print(f"{path.relative_to(ROOT)}: {path.stat().st_size:,} bytes")


def lights(path):
    devices = [(YEELIGHT_MODEL, "Screen Light Bar Pro"), (YEELIGHT_BULB_MODEL, "W3 Color Bulb"),
               (YEELIGHT_BEAM_MODEL, "Beam")]
    panels = [preview(model, "back") for model, _ in devices]
    frames = []
    for mode, label in (("back", "COLOR"), ("front", "WHITE")):
        for panel, _ in panels:
            panel.settings = light_settings({"hardware_model": panel.model, "light_settings": {"reminder_mode": mode}})
        for state, _ in STATES:
            for tick in range(SECONDS * FPS):
                output = image(960, 330)
                painter = QPainter(output)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                text(painter, QRectF(0, 12, 960, 28), label, 16, MUTED)
                for index, ((panel, canvas), (_, title)) in enumerate(zip(panels, devices)):
                    panel.clock.milliseconds = tick * 1000 // FPS
                    canvas.state = state
                    capture(canvas, painter, index * 320 + 10, 66, 1)
                    text(painter, QRectF(index * 320, 214, 320, 30), title, 18)
                legend(painter, 960, 280, state)
                painter.end()
                frames.append(pillow(output))
    save(frames, path)


def cube(path, mode):
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
            output = image(640, 270)
            painter = QPainter(output)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            capture(canvas, painter, 20, 0, 2)
            legend(painter, 640, 224, state)
            painter.end()
            frames.append(pillow(output))
    save(frames, path)


def main():
    application = QApplication.instance() or QApplication([])
    destination = ROOT / "docs/images/light-effects"
    destination.mkdir(parents=True, exist_ok=True)
    lights(destination / "lights.gif")
    for mode in ("status", "comet", "snake", "text"):
        cube(destination / f"cube-{mode}.gif", mode)
    application.quit()


if __name__ == "__main__":
    main()
