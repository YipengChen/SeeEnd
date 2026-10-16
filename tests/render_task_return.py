"""Render task-return states with isolated metadata, without controlling any app."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.integrations.registry import SOURCES
from aiflame.desktop.widgets import Dashboard
from test_task_return_ui import API


def main():
    output = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/seeend-task-return-ui")
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    with tempfile.TemporaryDirectory() as root, patch("aiflame.desktop.widgets.LocalAPI", API):
        engine = Engine(Path(root) / "events.sqlite", sources=SOURCES)
        targets = [("chatgpt-web", "web-task", {"kind":"web", "client_id":str(uuid.uuid4()), "locator_id":str(uuid.uuid4())}),
                   ("deepseek-web", "deepseek-task", {"kind":"web", "client_id":str(uuid.uuid4()), "locator_id":str(uuid.uuid4())}),
                   ("codex", "terminal-task", {"kind":"terminal", "app":"Terminal", "tty":"/dev/ttys003", "pid":123,
                       "started":"Wed Oct 14 12:00:00 2026", "instance_id":"fixture", "session_id":"fixture", "project":"SeeEnd"})]
        engine.accept(make_event("codex", "legacy-task", "working"), "codex")
        engine.accept(make_event("codex", "legacy-task", "done", 2), "codex")
        for source, task, target in targets:
            engine.accept({**make_event(source, task, "working"), "return_target":target}, source)
            engine.accept(make_event(source, task, "done", 2), source)
        engine.accept({**make_event("codex", "resolve-task", "working"), "return_context": {
            "session_id": str(uuid.uuid4()), "turn_id": str(uuid.uuid4()), "cwd": "/Users/example/project"}}, "codex")
        engine.accept(make_event("codex", "resolve-task", "done", 2), "codex")
        widget = Dashboard()
        widget.ready = True
        widget.show()

        def capture(name):
            for _ in range(5):
                app.processEvents()
            scroll = widget.tabs.currentWidget()
            if name.endswith("-recent"):
                scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
                app.processEvents()
            assert scroll.horizontalScrollBar().maximum() == 0
            widget.grab().save(str(output / (name + ".png")))
            if name.endswith("-recent"):
                widget.recent_frame.grab().save(str(output / (name + "-card.png")))

        for lang in ("zh-CN", "en"):
            engine.update_settings({"language":lang})
            for width, height in ((1180,960), (920,680)):
                widget.resize(width,height)
                widget.render({**engine.snapshot(), "maintenance":False})
                widget.navigate(0)
                capture(f"{lang}-{width}-overview")
                widget.tabs.widget(0).ensureWidgetVisible(widget.recent_frame)
                capture(f"{lang}-{width}-recent")
                widget.navigate(4)
                widget.history.selectRow(0)
                widget.show_event_detail()
                widget.tabs.widget(4).ensureWidgetVisible(widget.event_detail_box)
                capture(f"{lang}-{width}-detail")
                widget.navigate(0)
                widget.return_failed_message("会话「回复123」对应 2 个终端位置，无法唯一定位。", widget.visible_events[0]["id"])
                capture(f"{lang}-{width}-failure")
                from PySide6.QtTest import QTest
                QTest.qWait(2200)
                assert widget.message_timer.interval() == 2000
                assert not widget.statusbar.isVisible()
            widget.navigate(4)
            widget.history.selectRow(4)
            widget.show_event_detail()
            capture(f"{lang}-legacy-detail")
            widget.return_task_button.setFocus()
            from PySide6.QtWidgets import QToolTip
            QToolTip.showText(widget.return_task_button.mapToGlobal(widget.return_task_button.rect().bottomLeft()),
                              widget.return_task_button.toolTip(), widget.return_task_button)
            capture(f"{lang}-unavailable-tooltip")
            from PySide6.QtCore import Qt
            for tip in app.topLevelWidgets():
                if tip.windowType() == Qt.WindowType.ToolTip and tip.isVisible():
                    tip.grab().save(str(output / f"{lang}-tooltip-window.png"))
            QToolTip.hideText()
            widget.history.selectRow(0)
            widget.show_event_detail()
            widget.return_requests[widget.visible_events[0]["id"]] = None
            widget.refresh_return_buttons()
            capture(f"{lang}-pending-detail")
            widget.navigate(0)
            capture(f"{lang}-pending-recent")
            widget.return_requests.clear()
            engine.origins.db.execute("UPDATE terminal_instances SET session='other'")
            widget.render({**engine.snapshot(), "maintenance":False})
            widget.refresh_return_buttons()
            widget.navigate(0)
            capture(f"{lang}-unavailable-recent")
            engine.origins.db.execute("UPDATE terminal_instances SET session='fixture'")
        widget.shutdown()
        widget.hide()
        engine.db.close()
    print(output)


if __name__ == "__main__":
    main()
