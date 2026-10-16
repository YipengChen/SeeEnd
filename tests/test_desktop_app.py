"""Desktop startup ownership and cleanup, without user services or hardware."""

import os
from importlib.util import find_spec
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from aiflame.desktop import app as shell


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
            patch("aiflame.desktop.localization.install_arrow_key_filter"),
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

    @unittest.skipUnless(sys.platform == "darwin", "Dock quit is macOS-specific")
    def test_one_quit_request_exits_with_flame_without_disabling_saved_preference(self):
        script = textwrap.dedent("""
            import sys
            import tempfile
            from pathlib import Path
            from unittest.mock import Mock, patch
            from PySide6.QtCore import QEvent, QTimer, Signal
            from PySide6.QtWidgets import QApplication, QWidget
            from aiflame.desktop import app as shell
            from aiflame.desktop.floating_flame import DesktopFlame

            visible, enabled = map(int, sys.argv[1:])
            widgets = {}
            exited, failures = [], []
            hub = Mock()
            hub.devices.busy.return_value = False
            hub.engine.settings = {'language': 'zh-CN'}

            class Dashboard(QWidget):
                state_changed = Signal(object)
                return_failed = Signal()
                def __init__(self, parent):
                    super().__init__(parent)
                    widgets['window'] = parent
                    self.shutdown = Mock()
                    self.act = Mock()
                    self.return_task = Mock()
                    self.return_failed_message = Mock()
                    widgets['dashboard'] = self

            def create_flame():
                flame = DesktopFlame(Path(work) / 'desktop-flame.json')
                widgets['flame'] = flame
                return flame

            def exercise():
                app = QApplication.instance()
                window, flame = widgets['window'], widgets['flame']
                window.setVisible(bool(visible))
                flame.update_status({'settings': {'desktop_flame_enabled': bool(enabled),
                    'hardware_notifications': False, 'brightness': 50},
                    'maintenance': False, 'muted': False, 'display': {'state': 'idle'}})
                try:
                    # Maintenance must reject Quit before changing any window.
                    hub.devices.busy.return_value = True
                    event = QEvent(QEvent.Type.Quit)
                    app.sendEvent(app, event)
                    assert not event.isAccepted()
                    assert flame.isVisible() == bool(enabled)
                    assert window.isVisible() == bool(visible)
                    assert not window.quitting
                    widgets['dashboard'].act.assert_not_called()
                    hub.devices.busy.return_value = False
                    # One application-level event, as delivered by macOS Dock.
                    app.sendEvent(app, QEvent(QEvent.Type.Quit))
                except BaseException as error:
                    failures.append(repr(error))
                    app.exit(98)

            def start_indicator():
                app = QApplication.instance()
                app.aboutToQuit.connect(lambda: exited.append(True))
                QTimer.singleShot(20, exercise)
                QTimer.singleShot(2000, lambda: app.exit(99))

            with tempfile.TemporaryDirectory() as work, \\
                 patch.object(shell, 'existing_hub', return_value=False), \\
                 patch('aiflame.hub.server.Hub', return_value=hub), \\
                 patch('aiflame.desktop.instance.DesktopInstance') as owner, \\
                 patch('aiflame.desktop.widgets.Dashboard', Dashboard), \\
                 patch('aiflame.desktop.macos_shell.MacTray') as tray, \\
                 patch('aiflame.desktop.macos_shell.activate_application'), \\
                 patch('aiflame.desktop.notifications.NotificationConsumer'), \\
                 patch('aiflame.desktop.tray.TrayIndicator') as indicator, \\
                 patch('aiflame.desktop.floating_flame.DesktopFlame', side_effect=create_flame), \\
                 patch('PySide6.QtWidgets.QMessageBox.information'):
                owner.return_value.start.return_value = True
                indicator.return_value.start.side_effect = start_indicator
                result = shell.main()
                assert not failures, failures
                assert result == 0, result
                assert exited == [True], exited
                widgets['dashboard'].act.assert_not_called()
                assert widgets['flame'].settings['desktop_flame_enabled'] == bool(enabled)
                hub.stop.assert_called_once()
                widgets['dashboard'].shutdown.assert_called_once()
                tray.return_value.shutdown.assert_called_once()
                owner.return_value.close.assert_called_once()
        """)
        for visible, enabled in ((0, 1), (1, 1), (0, 0), (1, 0)):
            with self.subTest(main_visible=visible, flame_enabled=enabled):
                result = subprocess.run(
                    [sys.executable, '-c', script, str(visible), str(enabled)],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
