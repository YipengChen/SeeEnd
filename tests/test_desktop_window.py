"""Window restore behavior shared by Dock, tray, and single-instance IPC."""

import os
from importlib.util import find_spec
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtWidgets import QApplication, QStackedWidget, QWidget
    from aiflame.desktop.window import DesktopWindow, WindowRestorer


@unittest.skipUnless(HAS_QT, "Install the desktop extra to test window restoration")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.hub = Mock()
        self.hub.devices.busy.return_value = False
        self.window = DesktopWindow(self.hub)
        self.window.resize(1000, 750)
        self.window.background_available = True
        self.pages = QStackedWidget()
        for _ in range(3):
            self.pages.addWidget(QWidget())
        self.pages.setCurrentIndex(2)
        self.window.setCentralWidget(self.pages)
        self.activate = Mock()
        self.restorer = WindowRestorer(self.app, self.window, self.activate)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.restorer.close()
        self.hub.devices.busy.return_value = False
        self.window.quitting = True
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_close_hides_without_stopping_hub_and_dock_reopens_same_page(self):
        self.window.close()
        self.assertTrue(self.window.isHidden())
        self.hub.stop.assert_not_called()
        self.app.sendEvent(self.app, QEvent(QEvent.Type.ApplicationActivate))
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.pages.currentIndex(), 2)
        self.assertEqual(self.window.size().width(), 1000)
        self.activate.assert_called_once()

    def test_repeated_requests_coalesce_and_restore_minimized_maximized_state(self):
        self.window.setWindowState(Qt.WindowState.WindowMaximized | Qt.WindowState.WindowMinimized)
        for _ in range(10):
            self.restorer.request()
        self.app.processEvents()
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.isMaximized())
        self.activate.assert_called_once()
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_restore_does_not_change_normal_size_or_page(self):
        before = self.window.size()
        self.restorer.request()
        self.app.processEvents()
        self.assertEqual(self.window.size(), before)
        self.assertEqual(self.pages.currentIndex(), 2)
        self.activate.assert_called_once()

    def test_reopen_preserves_maximized_window_and_current_page(self):
        self.window.showMaximized()
        self.app.processEvents()
        self.window.close()
        self.assertTrue(self.window.isHidden())
        self.restorer.request()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.isMaximized())
        self.assertFalse(self.window.isFullScreen())
        self.assertEqual(self.pages.currentIndex(), 2)

    def test_maintenance_prevents_close_and_shutdown_cancels_pending_restore(self):
        self.hub.devices.busy.return_value = True
        with patch("aiflame.desktop.window.QMessageBox.information") as notice:
            self.window.close()
        self.assertTrue(self.window.isVisible())
        notice.assert_called_once()
        self.restorer.request()
        self.restorer.close()
        self.app.processEvents()
        self.activate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
