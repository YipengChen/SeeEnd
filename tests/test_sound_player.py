"""Sound playback routing and lifecycle without using the speaker."""

from importlib.util import find_spec
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QProcess
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.sounds import SoundPlayer


@unittest.skipUnless(HAS_QT, "Install desktop dependencies to test sound playback")
class SoundPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        for category in ("chime", "pop", "cat"):
            (self.directory / f"{category}.wav").write_bytes(b"RIFFfixture")
        self.directory_patch = patch("aiflame.desktop.sounds.SOUND_DIRECTORY", self.directory)
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)
        self.process_patch = patch("aiflame.desktop.sounds.QProcess", wraps=QProcess)
        self.process_class = self.process_patch.start()
        self.process_class.ProcessState = QProcess.ProcessState
        self.process_class.ExitStatus = QProcess.ExitStatus
        self.addCleanup(self.process_patch.stop)
        self.process = Mock()
        self.process.state.return_value = QProcess.ProcessState.NotRunning
        self.process_class.return_value = self.process
        self.player = SoundPlayer()
        self.failures = []
        self.player.failed.connect(self.failures.append)
        self.completions = []
        self.player.completed.connect(self.completions.append)
        self.addCleanup(self.player.shutdown)

    def finish(self, code=0):
        self.process.state.return_value = QProcess.ProcessState.NotRunning
        self.player._finished(code, QProcess.ExitStatus.NormalExit)

    def test_mac_uses_absolute_player_and_validated_resource_path(self):
        with patch("aiflame.desktop.sounds.sys.platform", "darwin"):
            self.assertTrue(self.player.play("cat"))
        self.process.start.assert_called_once_with("/usr/bin/afplay", [str(self.directory / "cat.wav")])
        self.process.waitForStarted.assert_not_called()
        self.process.waitForFinished.assert_not_called()

    def test_linux_prefers_paplay_and_falls_back_to_aplay(self):
        with patch("aiflame.desktop.sounds.sys.platform", "linux"), patch(
                "aiflame.desktop.sounds.shutil.which", side_effect=lambda name: {
                    "paplay": None, "aplay": "/usr/bin/aplay"}[name]):
            self.assertTrue(self.player.play("pop"))
        self.process.start.assert_called_once_with("/usr/bin/aplay", [str(self.directory / "pop.wav")])
        self.process.start.reset_mock()
        with patch("aiflame.desktop.sounds.sys.platform", "linux"), patch(
                "aiflame.desktop.sounds.shutil.which", return_value="/usr/bin/paplay") as which:
            self.assertTrue(self.player.play("chime"))
        which.assert_called_once_with("paplay")
        self.process.start.assert_called_once_with("/usr/bin/paplay", [str(self.directory / "chime.wav")])

    def test_missing_resource_invalid_category_and_missing_player_report_failure(self):
        (self.directory / "cat.wav").unlink()
        self.assertFalse(self.player.play("cat"))
        self.assertFalse(self.player.play("../cat"))
        with patch("aiflame.desktop.sounds.sys.platform", "linux"), patch(
                "aiflame.desktop.sounds.shutil.which", return_value=None):
            self.assertFalse(self.player.play("pop"))
        self.assertEqual(len(self.failures), 3)
        self.assertEqual(self.completions, [])
        self.process.start.assert_not_called()

    def test_replacement_waits_for_old_process_and_keeps_only_latest_sound(self):
        with patch("aiflame.desktop.sounds.sys.platform", "darwin"):
            self.player.play("chime")
            self.process.state.return_value = QProcess.ProcessState.Running
            self.player.play("pop")
            self.player.play("cat")
        self.assertEqual(self.process.start.call_count, 1)
        self.assertEqual(self.process.kill.call_count, 2)
        self.player._error(QProcess.ProcessError.Crashed)
        self.finish(9)
        self.assertEqual(self.process.start.call_count, 2)
        self.process.start.assert_called_with("/usr/bin/afplay", [str(self.directory / "cat.wav")])
        self.assertEqual(self.failures, [])
        self.assertEqual(self.completions, [])

    def test_process_error_is_reported_once_and_intentional_stop_is_silent(self):
        self.player._error(QProcess.ProcessError.FailedToStart)
        self.finish(1)
        self.assertEqual(len(self.failures), 1)
        self.assertEqual(self.completions, ["failed"])
        self.player._reported_failure = False
        self.finish(1)
        self.assertEqual(len(self.failures), 2)
        self.assertEqual(self.completions, ["failed", "failed"])
        self.process.state.return_value = QProcess.ProcessState.Running
        self.player.stop()
        self.player._error(QProcess.ProcessError.Crashed)
        self.finish(9)
        self.assertEqual(len(self.failures), 2)
        self.assertEqual(self.completions, ["failed", "failed"])

    def test_successful_process_completion_is_reported_and_playback_state_clears(self):
        self.assertFalse(self.player.is_playing())
        with patch("aiflame.desktop.sounds.sys.platform", "darwin"):
            self.assertTrue(self.player.play("chime"))
        self.assertEqual(self.completions, [])
        self.process.state.return_value = QProcess.ProcessState.Running
        self.assertTrue(self.player.is_playing())
        self.finish()
        self.assertEqual(self.completions, ["submitted"])
        self.assertFalse(self.player.is_playing())
        self.assertEqual(self.failures, [])

    def test_pending_replacement_counts_as_playback_until_disabled(self):
        self.process.state.return_value = QProcess.ProcessState.Running
        with patch("aiflame.desktop.sounds.sys.platform", "darwin"):
            self.player.play("cat")
        self.process.state.return_value = QProcess.ProcessState.NotRunning
        self.assertTrue(self.player.is_playing())
        self.player.set_enabled(False)
        self.assertFalse(self.player.is_playing())
        self.assertEqual(self.completions, [])

    def test_disable_cancels_playback_and_replacement_and_can_be_reenabled(self):
        with patch("aiflame.desktop.sounds.sys.platform", "darwin"):
            self.player.play("chime")
            self.process.state.return_value = QProcess.ProcessState.Running
            self.player.play("cat")
            self.player.set_enabled(False)
            self.assertFalse(self.player.play("pop"))
            self.finish(9)
            self.assertEqual(self.process.start.call_count, 1)
            self.player.set_enabled(True)
            self.assertTrue(self.player.play("pop"))
        self.assertEqual(self.process.start.call_count, 2)
        self.assertEqual(self.failures, [])
        self.assertEqual(self.completions, [])

    def test_shutdown_cancels_playback_and_replacement_permanently(self):
        with patch("aiflame.desktop.sounds.sys.platform", "darwin"):
            self.player.play("chime")
            self.process.state.return_value = QProcess.ProcessState.Running
            self.player.play("cat")
            self.player.shutdown()
            self.finish(9)
            self.player.set_enabled(True)
            self.assertFalse(self.player.play("pop"))
        self.assertEqual(self.process.start.call_count, 1)
        self.assertEqual(self.failures, [])
        self.assertEqual(self.completions, [])
        self.assertFalse(self.player.is_playing())

    def test_windows_plays_async_replaces_and_stops_sound(self):
        winsound = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4)
        with patch("aiflame.desktop.sounds.sys.platform", "win32"), patch.dict(sys.modules, winsound=winsound):
            self.assertTrue(self.player.play("chime"))
            self.assertTrue(self.player.play("cat"))
            self.player.set_enabled(False)
        self.assertEqual(winsound.PlaySound.call_args_list, [
            unittest.mock.call(str(self.directory / "chime.wav"), 7),
            unittest.mock.call(None, 0),
            unittest.mock.call(str(self.directory / "cat.wav"), 7),
            unittest.mock.call(None, 0),
        ])
        self.process.start.assert_not_called()
        self.assertEqual(self.completions, ["submitted", "submitted"])

    def test_windows_playback_window_prevents_claiming_the_next_sound_too_soon(self):
        winsound = SimpleNamespace(PlaySound=Mock(), SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4)
        with patch("aiflame.desktop.sounds.sys.platform", "win32"), patch.dict(sys.modules, winsound=winsound), patch(
                "aiflame.desktop.sounds.time.monotonic", return_value=100.0) as now:
            self.assertTrue(self.player.play("cat"))
            self.assertTrue(self.player.is_playing())
            now.return_value = 101.19
            self.assertTrue(self.player.is_playing())
            now.return_value = 101.21
            self.assertFalse(self.player.is_playing())
            self.player.play("pop")
            self.assertTrue(self.player.is_playing())
            self.player.stop()
            self.assertFalse(self.player.is_playing())

    def test_windows_playback_failure_is_reported(self):
        winsound = SimpleNamespace(PlaySound=Mock(side_effect=RuntimeError("speaker unavailable")),
                                  SND_FILENAME=1, SND_ASYNC=2, SND_NODEFAULT=4)
        with patch("aiflame.desktop.sounds.sys.platform", "win32"), patch.dict(sys.modules, winsound=winsound):
            self.assertFalse(self.player.play("cat"))
        self.assertEqual(len(self.failures), 1)


if __name__ == "__main__":
    unittest.main()
