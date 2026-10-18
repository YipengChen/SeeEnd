"""Capture the real Overview widget with public demo data, without a running Hub.

Run: .venv/bin/python tools/render_readme_overview.py
Requires desktop dependencies. Never reads user configuration or contacts devices.
"""

import datetime as dt
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from aiflame.core.engine import Engine
from aiflame.desktop.localization import set_language
from aiflame.desktop.widgets import Dashboard
from aiflame.integrations.registry import SOURCES


class OfflineAPI(QObject):
    error = Signal(str)

    def __init__(self, parent, port):
        super().__init__(parent)

    def request(self, *args, **kwargs):
        pass

    def close(self):
        pass


def capture(app, destination, language):
    set_language(language)
    with tempfile.TemporaryDirectory() as work, patch("aiflame.desktop.widgets.LocalAPI", OfflineAPI):
        engine = Engine(Path(work) / "demo.sqlite", sources=SOURCES)
        widget = Dashboard()
        try:
            widget.ready = True
            widget.resize(1240, 850)
            widget.show()
            snapshot = engine.snapshot()
            snapshot["settings"]["language"] = language
            for source in snapshot["sources"]:
                source["enabled"] = source["id"] in widget.home_source_icons
                snapshot["settings"]["source_enabled"][source["id"]] = source["enabled"]
                if language == "en":
                    source["name"] = source["name"].replace(" 网页版", " Web")
            created = dt.datetime(2026, 10, 18, 14, 32).timestamp()
            events = [
                dict(id=f"demo-{index}", source=source, task=f"readme-demo-{index}",
                     state=state, reason="completed" if state == "done" else None,
                     created=created - index * 60, status="screen")
                for index, (source, state) in enumerate((
                    ("codex", "working"), ("chatgpt-web", "needs_input"),
                    ("claude-code", "done"), ("deepseek-web", "done"),
                ))
            ]
            tasks = [dict(source=event["source"], task=event["task"], state=event["state"],
                          seq=1, updated=event["created"]) for event in events]
            snapshot.update(events=events, task_events=events, tasks=tasks, maintenance=False,
                            unfinished_count=2, active_count=1)
            snapshot["display"]["state"] = "working"
            widget.render(snapshot)
            for _ in range(8):
                app.processEvents()
            with patch.object(widget.flame.clock, "elapsed", return_value=2000), \
                 patch.object(widget.flame.transition_clock, "elapsed", return_value=2000):
                widget.flame._animate()
                if not widget.grab().save(str(destination)):
                    raise RuntimeError(f"Could not save {destination}")
            print(destination.relative_to(ROOT))
        finally:
            widget.shutdown()
            widget.hide()
            engine.db.close()


def main():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    destination = ROOT / "docs/images"
    destination.mkdir(parents=True, exist_ok=True)
    for suffix, language in (("zh", "zh-CN"), ("en", "en")):
        capture(app, destination / f"overview-{suffix}.png", language)
    app.quit()


if __name__ == "__main__":
    main()
