"""Export the app icon from its SVG source (requires PySide6 and Pillow)."""

import os
from pathlib import Path

def main():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    assets = Path(__file__).resolve().parents[1] / "aiflame" / "assets"
    app = QApplication.instance() or QApplication([])
    renderer = QSvgRenderer(str(assets / "icon.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid app icon SVG")
    image = QImage(1024, 1024, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    png = assets / "icon.png"
    if not image.save(str(png)):
        raise OSError(f"Could not write {png}")
    with Image.open(png) as source:
        source.save(assets / "icon.ico", sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
        source.save(assets / "icon.icns")
    print("Exported icon.png, icon.ico and icon.icns from icon.svg")
    app.quit()


if __name__ == "__main__":
    main()
