"""Render deterministic design fixtures, without hardware or user configuration.

Run from the repository: .venv/bin/python -B tests/render_desktop.py /tmp/flame-ui
"""

import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.integrations.registry import SOURCES
from aiflame.desktop.widgets import Dashboard


class OfflineAPI(QObject):
    error = Signal(str)

    def __init__(self, parent, port):
        super().__init__(parent)

    def request(self, *args, **kwargs):
        pass

    def close(self):
        pass


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/flame-ui")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    def settle():
        # Word-wrapped labels and scroll areas can queue a second layout pass.
        for _ in range(4):
            app.processEvents()

    with (
        tempfile.TemporaryDirectory() as work,
        patch("aiflame.desktop.widgets.LocalAPI", OfflineAPI),
    ):
        engine = Engine(Path(work) / "events.sqlite", sources=SOURCES)
        widget = Dashboard()
        widget.ready = True
        widget.resize(1180, 850)
        widget.show()
        snapshot = {**engine.snapshot(), "maintenance": False}
        widget.render(snapshot)
        settle()
        widget.grab().save(str(output / "01-first-use.png"))
        widget.start_preview()
        settle()
        with (
            patch.object(widget.flame.clock, "elapsed", return_value=2500),
            patch.object(widget.flame.transition_clock, "elapsed", return_value=2500),
        ):
            widget.flame._animate()
            widget.grab().save(str(output / "02-preview.png"))
        with patch.object(widget.flame.clock, "elapsed", return_value=2500):
            widget.preview_started -= 3.5
            widget.advance_preview()
        for ms in (0, 160, 325, 500, 650, 1000):
            with (
                patch.object(widget.flame.clock, "elapsed", return_value=ms),
                patch.object(widget.flame.transition_clock, "elapsed", return_value=ms),
            ):
                widget.grab().save(str(output / f"transition-{ms:04d}.png"))
                if ms == 1000:
                    widget.grab().save(str(output / "03-preview-done.png"))
        widget.stop_preview()
        for seq, state in ((1, "working"), (2, "done")):
            engine.accept(make_event("codex", "screen-fixture", state, seq), "codex")
            widget.render({**engine.snapshot(), "maintenance": False})
            settle()
            with patch.object(widget.flame.clock, "elapsed", return_value=2000), patch.object(widget.flame.transition_clock, "elapsed", return_value=2000):
                widget.grab().save(str(output / f"real-screen-{state}.png"))
        widget.navigate(2)
        settle()
        widget.grab().save(str(output / "verified-no-hardware.png"))
        widget.navigate(1)
        settle()
        widget.grab().save(str(output / "virtual-device.png"))
        engine.update_settings({"hardware_notifications": True})
        widget.navigate(0)
        widget.render({**engine.snapshot(), "maintenance": False, "hardware_paired": False})
        settle()
        widget.grab().save(str(output / "hardware-unpaired.png"))
        widget.navigate(3)
        settle()
        widget.grab().save(str(output / "hardware-warning-preferences.png"))
        widget.navigate(0)
        widget.render({**engine.snapshot(), "maintenance": False, "hardware_paired": True})
        settle()
        widget.grab().save(str(output / "paired-offline.png"))
        engine.device.update(online=True, device_id="fixture", hardware_model="yeelight-yltd003",
                             transport="yeelight", port="wifi://192.0.2.1:55443", firmware_version="38")
        widget.render({**engine.snapshot(), "maintenance": False})
        settle()
        widget.grab().save(str(output / "connected-wifi.png"))
        widget.navigate(1)
        settle()
        widget.grab().save(str(output / "connected-wifi-device.png"))
        widget.navigate(0)
        engine.start_preview("render-preview")
        widget.hardware_preview_id = "render-preview"
        for state in ("working", "done"):
            if state == "done":
                engine.preview["started"] -= 3.2
            engine.applied(engine.desired(), {"result": "applied"})
            widget.render({**engine.snapshot(), "maintenance": False})
            settle()
            with patch.object(widget.flame.transition_clock, "elapsed", return_value=1000):
                widget.grab().save(str(output / f"hardware-preview-{state}.png"))
        engine.cancel_preview()
        widget.stop_preview()
        engine.accept(make_event("codex", "design-fixture", "working"), "codex")
        snapshot = {**engine.snapshot(), "maintenance": False}
        widget.render(snapshot)
        for index, name in enumerate(
            ("overview", "device", "sources", "preferences", "activity", "help")
        ):
            widget.navigate(index)
            settle()
            widget.grab().save(str(output / f"page-{name}.png"))
        for width, height in ((920, 680), (1440, 960)):
            widget.resize(width, height)
            for index in range(6):
                widget.navigate(index)
                settle()
                scroll = widget.tabs.widget(index)
                print(
                    f"{width}x{height} page {index}: horizontal overflow={scroll.horizontalScrollBar().maximum()}"
                )
                assert scroll.horizontalScrollBar().maximum() == 0
                widget.grab().save(str(output / f"{width}-page-{index}.png"))
        widget.resize(1180, 850)
        widget.navigate(4)
        widget.history.clearSelection()
        settle()
        widget.grab().save(str(output / "activity-unselected.png"))
        widget.history.selectRow(0)
        settle()
        widget.grab().save(str(output / "activity-selected.png"))
        widget.navigate(3)
        settle()
        widget.grab().save(str(output / "preferences.png"))
        for name, settings in (
            ("zero", {"brightness": 0}),
        ):
            widget.render({**snapshot, "settings": {**snapshot["settings"], **settings}, "display": {**snapshot["display"], "state": "idle"}})
            settle()
            widget.grab().save(str(output / f"preferences-{name}.png"))
            widget.navigate(0)
            settle()
            widget.grab().save(str(output / f"overview-{name}.png"))
            widget.navigate(3)
        widget.render(snapshot)
        widget.navigate(0)
        widget.tell("偏好已保存")
        settle()
        widget.grab().save(str(output / "overview-success.png"))
        widget.on_error("保存失败，请重试。")
        settle()
        widget.grab().save(str(output / "overview-error.png"))
        widget.navigate(3)
        widget.on_error("本地服务未连接，请重新打开 SeeEnd 后重试。")
        settle()
        widget.grab().save(str(output / "service-disconnected.png"))
        widget.shutdown()
        widget.hide()
        engine.db.close()
    print(output)


if __name__ == "__main__":
    main()
