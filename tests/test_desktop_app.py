"""Desktop startup ownership and cleanup, without user services or hardware."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from aiflame.desktop import app as shell
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "Install desktop dependencies to test the app shell")
class DesktopAppTests(unittest.TestCase):
    def test_existing_hub_requires_matching_release_and_brand(self):
        with patch.object(shell.urllib.request, "build_opener"), patch.object(shell.json, "load") as load:
            for data, expected in (
                ({"app_version": shell.__version__}, False),
                ({"app_version": "0.8.0", "app_name": "SeeEnd"}, False),
                ({"app_version": shell.__version__, "app_name": "SeeEnd"}, True),
            ):
                load.return_value = data
                self.assertEqual(shell.existing_hub(), expected)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_forwarded_launch_never_constructs_hub_or_window(self):
        class Application:
            def __init__(self, args): pass
            def setApplicationName(self, value): pass
            def setOrganizationName(self, value): pass
        with (
            patch("PySide6.QtWidgets.QApplication", Application),
            patch("aiflame.desktop.instance.DesktopInstance") as owner,
            patch.object(shell, "run_primary") as primary,
        ):
            owner.return_value.start.return_value = False
            self.assertEqual(shell.main(), 0)
        primary.assert_not_called()
        owner.return_value.close.assert_called_once()

    def test_failed_ui_startup_stops_owned_hub(self):
        with (
            patch.object(shell, "existing_hub", return_value=False),
            patch("aiflame.hub.server.Hub") as hub,
            patch("aiflame.desktop.widgets.Dashboard", side_effect=RuntimeError("UI failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "UI failed"):
                shell.run_primary(self.app, Mock())
        hub.return_value.start.assert_called_once()
        hub.return_value.stop.assert_called_once()

    def test_cleanup_failure_does_not_leave_owned_hub_running(self):
        dashboard = QWidget()
        dashboard.shutdown = Mock(side_effect=RuntimeError("worker cleanup failed"))
        with (
            patch.object(shell, "existing_hub", return_value=False),
            patch("aiflame.hub.server.Hub") as hub,
            patch("aiflame.desktop.widgets.Dashboard", return_value=dashboard),
            patch("aiflame.desktop.window.WindowRestorer", side_effect=RuntimeError("startup failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "worker cleanup failed"):
                shell.run_primary(self.app, Mock())
        dashboard.shutdown.assert_called_once()
        hub.return_value.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
