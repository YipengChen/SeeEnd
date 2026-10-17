"""Desktop flame behavior with real task delivery and a hidden main window."""

import json
import os
from importlib.util import find_spec
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from aiflame.core.events import make_event
from aiflame.hub.server import Hub
from live_client import DashboardClient

HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, QRect, QSize, Qt
    from PySide6.QtGui import QAction, QContextMenuEvent, QMouseEvent, QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget
    from aiflame.desktop.floating_flame import DesktopFlame, bounded_position
    from aiflame.desktop.localization import set_language
    from aiflame.desktop.tray import TrayIndicator
    from aiflame.desktop.widgets import Dashboard
    from aiflame.desktop.window import WindowRestorer


@unittest.skipUnless(HAS_QT, "Install desktop dependencies")
class DesktopFlameHitRegionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_panel_has_real_activation_protection_and_leaves_other_panels_unchanged(self):
        if self.app.platformName() != "cocoa":
            self.skipTest("Requires the macOS native window backend")
        import objc
        from AppKit import NSWindowStyleMaskNonactivatingPanel
        pet = DesktopFlame()
        try:
            for _ in range(2):
                pet.show()
                self.app.processEvents()
                panel = objc.objc_object(c_void_p=int(pet.winId())).window()
                self.assertTrue(panel.styleMask() & NSWindowStyleMaskNonactivatingPanel)
                # Reading the style alone gave false positives: setStyleMask
                # does not update this AppKit/WindowServer activation state.
                self.assertTrue(panel._preventsActivation())
                self.assertTrue(panel.becomesKeyOnlyIfNeeded())
                self.assertFalse(panel.canBecomeKeyWindow())
                pet.hide()
            ordinary = QWidget(None, Qt.WindowType.Tool)
            try:
                ordinary.show()
                self.app.processEvents()
                panel = objc.objc_object(c_void_p=int(ordinary.winId())).window()
                self.assertFalse(panel.styleMask() & NSWindowStyleMaskNonactivatingPanel)
                self.assertFalse(panel._preventsActivation())
            finally:
                ordinary.close()
                ordinary.deleteLater()
        finally:
            pet.shutdown()

    def test_activation_before_mouse_press_does_not_reopen_main_window(self):
        pet = DesktopFlame()
        main = QWidget()
        pet.show()
        self.app.processEvents()
        restorer = WindowRestorer(self.app, main)
        restorer.activation_guard = pet.pointer_interaction_active
        try:
            main.hide()
            with patch("aiflame.desktop.floating_flame.QGuiApplication.platformName", return_value="offscreen"), \
                 patch("aiflame.desktop.floating_flame.QGuiApplication.mouseButtons", return_value=Qt.MouseButton.LeftButton), \
                 patch("aiflame.desktop.floating_flame.QCursor.pos", return_value=pet.mapToGlobal(QPoint(92, 103))):
                self.assertIsNone(pet.drag_offset)
                self.app.sendEvent(self.app, QEvent(QEvent.Type.ApplicationActivate))
                self.app.processEvents()
                self.assertTrue(main.isHidden())
            # Dock/explicit open still works after the pointer gesture ends.
            with patch.object(pet, "pointer_interaction_active", return_value=False):
                restorer.activation_guard = pet.pointer_interaction_active
                self.app.sendEvent(self.app, QEvent(QEvent.Type.ApplicationActivate))
                self.app.processEvents()
                self.assertTrue(main.isVisible())
        finally:
            restorer.close()
            pet.shutdown()
            main.close()

    def test_mask_tracks_painted_pixels_in_every_state_and_animation_phase(self):
        pet = DesktopFlame()
        try:
            pet.show()
            self.app.processEvents()

            regions = []
            for state in ("idle", "working", "done", "needs_input"):
                for elapsed in (0, 1000, 2000, 3000):
                    with self.subTest(state=state, elapsed=elapsed):
                        pet.flame.set_state(state, live=True)
                        pet.flame.transition_from = None
                        with patch.object(pet.flame.clock, "elapsed", return_value=elapsed):
                            pixels = QPixmap(pet.flame.size())
                            pixels.fill(Qt.GlobalColor.transparent)
                            pet.flame.render(pixels, renderFlags=QWidget.RenderFlag.DrawChildren)
                            image = pixels.toImage()
                        region = pet.mask()
                        regions.append(region)
                        self.assertFalse(region.isEmpty())
                        self.assertTrue(region.contains(QPoint(92, 103)))  # Base.
                        self.assertFalse(region.contains(QPoint(20, 70)))
                        self.assertFalse(region.contains(QPoint(92, 10)))
                        self.assertFalse(region.contains(QPoint(160, 110)))
                        ratio = image.devicePixelRatio()
                        painted = 0
                        for y in range(image.height()):
                            for x in range(image.width()):
                                if image.pixelColor(x, y).alpha() >= 20:
                                    painted += 1
                                    self.assertTrue(region.contains(
                                        QPoint(int(x / ratio), int(y / ratio)) + pet.flame.pos()),
                                        (x, y, image.pixelColor(x, y).getRgb()))
                        self.assertGreater(painted, 100)
            self.assertNotEqual(regions[0], regions[8])  # Idle vs full flame.
        finally:
            pet.shutdown()
            self.app.processEvents()


@unittest.skipUnless(HAS_QT, "Install desktop dependencies")
class DesktopFlameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        set_language("zh-CN")
        self.work = tempfile.TemporaryDirectory()
        self.root = Path(self.work.name)
        self.hub = Hub(port=0, runtime=self.root, config={
            "dashboard": "test-cookie", "tokens": {"local-script": "test-token"},
        })
        self.hub.start(hardware=False)
        credential = patch("aiflame.desktop.network.dashboard_token",
                           side_effect=lambda: self.hub.config["dashboard"])
        credential.start()
        self.addCleanup(credential.stop)
        self.dashboard = Dashboard(port=self.hub.port)
        self.dashboard.show()
        self.wait(lambda: self.dashboard.model is not None and not self.dashboard.api.pending)
        self.client = DashboardClient(f"http://127.0.0.1:{self.hub.port}", dashboard_token=self.hub.config["dashboard"])
        self.tray = QSystemTrayIcon(self.dashboard)
        self.indicator = TrayIndicator(self.tray, self.dashboard, QAction(self.dashboard), self.dashboard)
        self.pet = DesktopFlame(self.root / "desktop-flame.json")
        self.indicator.state_changed.connect(self.pet.update_status)
        self.dashboard.state_changed.connect(self.pet.update_status)
        self.refresh()

    def tearDown(self):
        self.pet.shutdown()
        self.indicator.shutdown()
        self.dashboard.shutdown()
        self.dashboard.close()
        self.dashboard.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.hub.stop()
        self.work.cleanup()
        set_language("zh-CN")

    def wait(self, predicate, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail("Desktop flame condition timed out")

    def refresh(self):
        self.indicator.poll()
        self.wait(lambda: not self.dashboard.api.pending)

    def accept(self, state):
        with self.hub.lock:
            self.hub.engine.clear_prompts("expired")
            self.hub.engine.active.clear()
            task = f"pet-{self.hub.engine.settings['hardware_notifications']}-{state}"
            self.hub.engine.accept(make_event("local-script", task, state), "local-script")

    def test_hidden_main_window_tracks_real_tasks_without_rendering_or_activating(self):
        self.dashboard.hide()
        self.wait(lambda: not self.dashboard.api.pending)
        self.indicator.timer.setInterval(40)
        self.indicator.start()
        with patch.object(self.dashboard, "render") as render, patch.object(self.pet, "activateWindow") as activate:
            for enabled in (False, True):
                self.client.call("/api/settings", {"hardware_notifications": enabled})
                self.wait(lambda: self.indicator.data["settings"]["hardware_notifications"] == enabled)
                for state, title in (("working", "任务进行中"), ("done", "任务已完成"),
                                     ("needs_input", "等待输入"), ("needs_input", "等待输入"), ("idle", "当前无任务")):
                    self.accept(state)
                    self.wait(lambda: self.pet.state == state)
                    self.assertTrue(self.pet.isVisible())
                    self.assertEqual(self.pet.flame.state, state)
                    self.assertIn(title, self.pet.accessibleDescription())
                    if state == "needs_input":
                        self.assertEqual(self.indicator.data["display"]["state"], "needs_input")
            render.assert_not_called()
            activate.assert_not_called()
        self.assertFalse(self.dashboard.poll_timer.isActive())
        self.assertTrue(self.pet.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(self.pet.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow))
        self.assertTrue(self.pet.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)

    def test_preference_toggle_persists_and_hidden_changes_and_modes_are_observed(self):
        self.dashboard.navigate(3)
        self.dashboard.checks["desktop_flame_enabled"].click()
        self.wait(lambda: not self.dashboard.writing and not self.dashboard.api.pending and not self.pet.isVisible())
        self.assertFalse(self.hub.engine.settings["desktop_flame_enabled"])
        self.assertFalse(self.hub.engine.settings["desktop_notifications"])
        self.assertNotIn("desktop_flame_enabled", self.dashboard.save_feedback)
        self.assertTrue(self.dashboard.statusbar.isHidden())
        self.dashboard.hide()
        self.wait(lambda: not self.dashboard.api.pending)
        self.indicator.timer.setInterval(40)
        self.indicator.start()
        with patch.object(self.dashboard, "render") as render:
            for settings, visible in (({"desktop_flame_enabled": True}, True),
                                      ({"hardware_notifications": True}, True),
                                      ({"desktop_flame_enabled": False}, False),
                                      ({"hardware_notifications": False}, False),
                                      ({"hardware_notifications": True}, False),
                                      ({"hardware_notifications": False, "desktop_flame_enabled": True}, True)):
                self.client.call("/api/settings", settings)
                self.wait(lambda: self.indicator.data
                          and all(self.indicator.data["settings"].get(key) == value for key, value in settings.items())
                          and self.pet.isVisible() == visible)
            render.assert_not_called()

    def test_connection_loss_clears_active_flames_and_recovers(self):
        self.accept("done")
        self.refresh()
        self.assertEqual(self.pet.flame.state, "done")
        self.indicator.disconnected()
        self.assertEqual(self.pet.state, "unavailable")
        self.assertEqual(self.pet.flame.state, "idle")
        self.assertIn("服务未连接", self.pet.accessibleDescription())
        self.refresh()
        self.assertNotEqual(self.pet.state, "unavailable")

    def test_drag_restores_position_and_offscreen_saved_position_is_clamped(self):
        opened = Mock()
        self.pet.open_requested.connect(opened)
        self.pet.move(50, 50)
        def mouse(kind, local, buttons):
            global_pos = self.pet.pos() + local
            event = QMouseEvent(kind, QPointF(local), QPointF(global_pos),
                                Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
                                buttons, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(self.pet, event)
        mouse(QEvent.Type.MouseButtonPress, QPoint(60, 50), Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseMove, QPoint(100, 80), Qt.MouseButton.LeftButton)
        mouse(QEvent.Type.MouseButtonRelease, QPoint(100, 80), Qt.MouseButton.NoButton)
        self.assertEqual(self.pet.pos(), QPoint(90, 80))
        self.assertEqual(json.loads(self.pet.position_path.read_text()), [90, 80])
        opened.assert_not_called()
        for saved in ([90, 80], [999999, -999999]):
            self.pet.position_path.write_text(json.dumps(saved))
            restored = DesktopFlame(self.pet.position_path)
            try:
                restored.update_status(self.indicator.data)
                if saved == [90, 80]:
                    self.assertEqual(restored.pos(), QPoint(*saved))
                self.assertTrue(restored.screen().availableGeometry().contains(restored.geometry()))
            finally:
                restored.shutdown()
        self.assertEqual(bounded_position(QPoint(-900, 50), QSize(184, 144),
                                          [QRect(-1280, 0, 1280, 800), QRect(0, 0, 1280, 800)]), QPoint(-900, 50))

    def test_click_is_inert_and_shutdown_rejects_late_updates(self):
        opened, disabled = Mock(), Mock()
        self.pet.open_requested.connect(opened)
        self.pet.disable_requested.connect(disabled)
        QTest.mouseClick(self.pet, Qt.MouseButton.LeftButton)
        opened.assert_not_called()
        QTest.mouseDClick(self.pet, Qt.MouseButton.LeftButton)
        opened.assert_not_called()
        self.pet.close()
        disabled.assert_called_once()
        self.assertTrue(self.pet.isVisible())  # The confirmed saved preference controls visibility.
        self.accept("working")
        self.refresh()
        self.pet.shutdown()
        self.assertFalse(self.pet.isVisible())
        self.assertFalse(self.pet.flame.animation_timer.isActive())
        self.pet.update_status(self.indicator.data)
        self.assertFalse(self.pet.isVisible())

    def test_hidden_click_with_active_task_never_returns_or_opens(self):
        self.dashboard.hide()
        events = []
        for task in ("first", "second"):
            event = {**make_event("chatgpt-web", task, "working"), "return_target": {
                "kind": "web", "client_id": str(uuid.uuid4()), "locator_id": str(uuid.uuid4()),
            }}
            with self.hub.lock:
                self.hub.engine.accept(event, "chatgpt-web")
            events.append(event)
        self.refresh()
        self.assertFalse(self.dashboard.model["events"])
        with patch.object(self.dashboard.api, "request") as request:
            for _ in range(2):
                QTest.mouseClick(self.pet, Qt.MouseButton.LeftButton)
            request.assert_not_called()
        self.assertTrue(self.dashboard.isHidden())
        self.assertIsNone(self.pet.drag_offset)
        self.assertEqual(self.pet.cursor().shape(), Qt.CursorShape.OpenHandCursor)

    def test_double_click_returns_latest_task_only_on_release(self):
        self.dashboard.hide()
        self.pet.return_requested.connect(self.dashboard.return_task)
        events = []
        for task in ("first", "second"):
            event = {**make_event("chatgpt-web", task, "working"), "return_target": {
                "kind": "web", "client_id": str(uuid.uuid4()), "locator_id": str(uuid.uuid4()),
            }}
            with self.hub.lock:
                self.hub.engine.accept(event, "chatgpt-web")
            events.append(event)
        self.refresh()
        latest = self.pet.latest_task["id"]
        with self.hub.lock:
            expected = self.hub.engine.db.execute(
                "SELECT id FROM events WHERE task='second' ORDER BY rowid DESC LIMIT 1").fetchone()["id"]
        self.assertEqual(latest, expected)
        with patch.object(self.dashboard.api, "request") as request:
            QTest.mouseDClick(self.pet, Qt.MouseButton.LeftButton, pos=QPoint(92, 100))
            request.assert_not_called()
            QTest.mouseRelease(self.pet, Qt.MouseButton.LeftButton, pos=QPoint(92, 100))
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], "/api/task-return")
            self.assertEqual(request.call_args.kwargs["body"], {"event_id": expected})
        self.assertTrue(self.dashboard.isHidden())

    def test_double_press_then_drag_does_not_return(self):
        returned = Mock()
        self.pet.return_requested.connect(returned)
        self.pet.latest_task = {"id": "latest", "return_to": {"kind": "web"}}
        QTest.mouseDClick(self.pet, Qt.MouseButton.LeftButton, pos=QPoint(92, 100))
        QTest.mouseMove(self.pet, QPoint(132, 100))
        QTest.mouseRelease(self.pet, Qt.MouseButton.LeftButton, pos=QPoint(92, 100))
        returned.assert_not_called()

    def test_small_pointer_jitter_neither_opens_nor_moves(self):
        requested = Mock()
        self.pet.open_requested.connect(requested)
        start = self.pet.pos()
        QTest.mousePress(self.pet, Qt.MouseButton.LeftButton, pos=QPoint(92, 100))
        requested.assert_not_called()
        QTest.mouseMove(self.pet, QPoint(93, 100))
        self.assertEqual(self.pet.pos(), start)
        QTest.mouseRelease(self.pet, Qt.MouseButton.LeftButton, pos=QPoint(93, 100))
        requested.assert_not_called()

    def test_dragging_out_and_back_never_becomes_a_click(self):
        requested = Mock()
        self.pet.open_requested.connect(requested)
        start = self.pet.pos()
        origin = self.pet.mapToGlobal(QPoint(92, 100))
        for kind, global_pos in (
            (QEvent.Type.MouseButtonPress, origin),
            (QEvent.Type.MouseMove, origin + QPoint(40, 20)),
            (QEvent.Type.MouseMove, origin),
            (QEvent.Type.MouseButtonRelease, origin),
        ):
            moving = kind == QEvent.Type.MouseMove
            release = kind == QEvent.Type.MouseButtonRelease
            event = QMouseEvent(kind, QPointF(self.pet.mapFromGlobal(global_pos)), QPointF(global_pos),
                                Qt.MouseButton.NoButton if moving else Qt.MouseButton.LeftButton,
                                Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(self.pet, event)
        self.assertEqual(self.pet.pos(), start)
        requested.assert_not_called()

    def test_context_menu_keeps_main_window_hidden_and_releases_menus(self):
        restorer = WindowRestorer(self.app, self.dashboard)
        self.pet.menu_visible_changed.connect(restorer.set_activation_suppressed)
        self.pet.open_requested.connect(restorer.request)
        open_app = False

        def run_menu(_position):
            self.assertTrue(restorer.activation_suppressed)
            self.app.sendEvent(self.app, QEvent(QEvent.Type.ApplicationActivate))
            self.app.processEvents()
            self.assertTrue(self.dashboard.isHidden())
            if open_app:
                self.pet.findChildren(QMenu)[0].actions()[0].trigger()
                self.app.processEvents()
                self.assertTrue(self.dashboard.isVisible())

        def create_menu(parent):
            menu = QMenu(parent)
            menu.exec = Mock(side_effect=run_menu)
            return menu

        try:
            for open_app in (False, False, True):
                self.dashboard.hide()
                point = QPoint(50, 50)
                event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                          self.pet.mapToGlobal(point))
                with patch("aiflame.desktop.floating_flame.QMenu", side_effect=create_menu):
                    self.pet.contextMenuEvent(event)
                self.assertFalse(restorer.activation_suppressed)
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                self.assertEqual(self.pet.findChildren(QMenu), [])
        finally:
            restorer.close()


if __name__ == "__main__":
    unittest.main()
