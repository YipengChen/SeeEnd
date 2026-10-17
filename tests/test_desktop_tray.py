"""Exercise tray updates through an isolated Hub, including a hidden dashboard."""

import json
import os
from importlib.util import find_spec
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from aiflame.core.events import make_event
from aiflame.hub.server import Hub
from live_client import DashboardClient

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtGui import QAction, QImage
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon
    from aiflame.desktop.tray import TrayIndicator, status_icon
    from aiflame.desktop.widgets import Dashboard


@unittest.skipUnless(HAS_QT, "Install the desktop extra to test tray updates")
class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.hub = Hub(
            port=0,
            runtime=Path(self.temp.name),
            config={
                "dashboard": "test-cookie",
                "tokens": {"local-script": "test-token"},
            },
        )
        self.hub.start(hardware=False)
        credential = patch("aiflame.desktop.network.dashboard_token",
                           side_effect=lambda: self.hub.config["dashboard"])
        credential.start()
        self.addCleanup(credential.stop)
        self.widget = Dashboard(port=self.hub.port)
        self.widget.show()
        self.wait(lambda: self.widget.model is not None and not self.widget.api.pending)
        self.client = DashboardClient(f"http://127.0.0.1:{self.hub.port}", dashboard_token=self.hub.config["dashboard"])
        self.tray = QSystemTrayIcon(self.widget)
        self.status_action = QAction(self.widget)
        self.indicator = TrayIndicator(
            self.tray, self.widget, self.status_action, self.widget
        )
        self.hub.update_settings({"hardware_notifications": True})
        with self.hub.lock:
            self.hub.engine.device["online"] = True

    def tearDown(self):
        self.indicator.shutdown()
        self.widget.shutdown()
        self.widget.close()
        self.widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.hub.stop()
        self.temp.cleanup()

    def wait(self, predicate, timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Tray condition timed out")

    def refresh(self, expected):
        self.indicator.poll()
        self.wait(lambda: not self.widget.api.pending)
        self.assertEqual(self.indicator.icon_key[0], expected)

    def test_connecting_status_matches_dashboard(self):
        from aiflame.desktop.tray import light_status
        with self.hub.lock:
            self.hub.engine.device.update(online=False, connecting=True)
        status, data = self.client.request("/api/indicator")
        self.assertEqual(status, 200)
        self.assertTrue(data["device"]["connecting"])
        title = light_status(data)[1]
        self.assertIn("正在连接设备", title)
        self.assertNotIn("设备未连接", title)

    def test_indicator_endpoint_is_small_and_requires_dashboard_auth(self):
        status, _ = self.client.request("/api/indicator", authenticated=False)
        self.assertEqual(status, 401)
        status, _ = self.client.request(
            "/api/indicator",
            authenticated=False,
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(status, 404)
        with patch.object(self.hub.engine, "snapshot") as full_snapshot:
            data = self.client.call("/api/indicator")
        full_snapshot.assert_not_called()
        self.assertEqual(data["display"]["state"], "idle")
        self.assertLess(len(json.dumps(data)), 512)
        self.assertTrue(data["settings"]["desktop_flame_enabled"])
        self.assertIn("reduced", data["settings"])
        self.assertFalse({"events", "tasks", "sources"} & data.keys())

    def test_hardware_warning_clears_when_device_alerts_are_off(self):
        self.hub.engine.device["online"] = False
        self.refresh("idle")
        self.assertIn("设备未连接", self.tray.toolTip())
        self.client.call("/api/settings", {"hardware_notifications": False})
        self.refresh("idle")
        self.assertNotIn("模式", self.tray.toolTip())
        self.assertNotIn("设备未连接", self.tray.toolTip())

    def test_hidden_window_keeps_tracking_actual_states_without_rendering_dashboard(
        self,
    ):
        self.widget.hide()
        self.wait(lambda: not self.widget.api.pending)
        self.indicator.timer.setInterval(40)
        self.indicator.start()
        self.wait(lambda: self.indicator.icon_key[0] == "idle")
        with patch.object(self.widget, "render") as render:
            for state, seq, title in (
                ("working", 1, "任务进行中"),
                ("done", 2, "任务已完成"),
                ("needs_input", 3, "等待输入"),
                ("idle", 4, "当前无任务"),
            ):
                with self.hub.lock:
                    self.hub.engine.clear_prompts("expired")
                    self.hub.engine.active.clear()
                    self.hub.engine.accept(
                        make_event("local-script", f"tray-{seq}", state), "local-script"
                    )
                self.wait(lambda: self.indicator.icon_key[0] == state)
                self.assertEqual(self.tray.toolTip(), "SeeEnd · " + title)
                self.assertIn(title, self.status_action.text())
            render.assert_not_called()
        self.assertFalse(self.widget.poll_timer.isActive())
        self.indicator.shutdown()
        self.assertFalse(self.indicator.timer.isActive())

    def test_muting_and_maintenance_override_but_offline_keeps_screen_status(self):
        self.client.call("/api/settings", {"brightness": 0})
        self.refresh("off")
        self.client.call("/api/settings", {"brightness": 8})
        with self.hub.lock:
            self.hub.engine.device["online"] = False
        self.refresh("idle")
        self.assertIn("设备未连接", self.tray.toolTip())
        with self.hub.lock:
            self.hub.engine.accept(
                make_event("local-script", "no-lamp", "working"), "local-script"
            )
        self.refresh("working")
        self.assertIn("设备未连接", self.tray.toolTip())
        self.client.call("/api/settings", {"brightness": 0})
        self.refresh("off")
        with patch.object(self.hub.devices, "busy", return_value=True):
            self.refresh("maintenance")

    def test_screen_preview_does_not_change_tray_or_restart_unchanged_icons(self):
        self.refresh("idle")
        self.widget.start_preview()
        self.widget.preview_started -= 5.5
        self.widget.advance_preview()
        self.assertEqual(self.widget.flame.state, "needs_input")
        with patch.object(self.tray, "setIcon") as change_icon:
            self.refresh("idle")
            change_icon.assert_not_called()
        self.assertEqual(self.tray.toolTip(), "SeeEnd · 当前无任务")

    def test_adapter_diagnostics_are_labeled_in_tray(self):
        with self.hub.lock:
            self.hub.engine.accept(
                make_event("manual", "tray-test", "working"), "manual"
            )
        self.refresh("working")
        self.assertIn("测试提醒", self.tray.toolTip())
        self.assertIn("测试提醒", self.status_action.text())

    def test_connection_failure_and_retry_do_not_leave_stale_success(self):
        self.refresh("idle")
        perform = self.widget.api._perform
        with patch.object(
            self.widget.api,
            "_perform",
            side_effect=lambda opener, job: (
                (job[0], None, "本地服务未连接，请重新打开 SeeEnd 后重试。")
                if job[1] == "/api/indicator"
                else perform(opener, job)
            ),
        ):
            self.indicator.poll()
            self.wait(lambda: not self.widget.api.pending)
        self.assertEqual(self.indicator.icon_key[0], "unavailable")
        self.assertFalse(self.widget.ready)
        self.refresh("idle")
        self.assertTrue(self.widget.ready)
        callback = []
        with patch.object(
            self.widget.api,
            "request",
            side_effect=lambda path, received, **kw: callback.append(received),
        ):
            self.indicator.poll()
        self.indicator.shutdown()
        callback[0](None)
        self.assertEqual(self.indicator.icon_key[0], "idle")

    def test_cached_icons_adapt_to_theme_and_are_visually_distinct(self):
        self.refresh("idle")
        hints = Mock()
        hints.colorScheme.return_value = Qt.ColorScheme.Light
        with patch.object(self.indicator, "hints", hints):
            self.indicator.refresh_icon()
            old = self.tray.icon().cacheKey()
            hints.colorScheme.return_value = Qt.ColorScheme.Dark
            self.indicator.refresh_icon()
            self.assertNotEqual(old, self.tray.icon().cacheKey())
        for dark in (False, True):
            images = []
            for state in (
                "idle",
                "working",
                "done",
                "needs_input",
                "off",
                "unavailable",
                "maintenance",
            ):
                rendered = (
                    status_icon(state, dark)
                    .pixmap(44)
                    .toImage()
                    .convertToFormat(QImage.Format.Format_RGBA8888)
                )
                pixels = bytes(rendered.constBits())
                self.assertTrue(any(pixels[3::4]))
                self.assertTrue(any(alpha == 0 for alpha in pixels[3::4]))
                images.append(pixels)
            self.assertEqual(len(set(images)), len(images))
