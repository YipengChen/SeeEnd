"""Short notification sounds, without blocking the desktop event loop."""

from pathlib import Path
import shutil
import sys
import time

from PySide6.QtCore import QObject, QProcess, Signal

from .localization import t


SOUND_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "sounds"
SOUND_CATEGORIES = ("chime", "pop", "cat")


class SoundPlayer(QObject):
    failed = Signal(str)
    completed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled = True
        self.closed = False
        self.process = QProcess(self)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)
        self._pending = None
        self._stopping = False
        self._reported_failure = False
        self._windows = None
        self._windows_active = False
        self._windows_until = 0.0

    def play(self, category):
        """Replace any current sound; return whether playback was submitted."""
        if self.closed or not self.enabled:
            return False
        if category not in SOUND_CATEGORIES:
            self.failed.emit(t("音效类别无效。"))
            return False
        path = SOUND_DIRECTORY / f"{category}.wav"
        if not path.is_file():
            self.failed.emit(t("音效文件缺失，请重新安装 SeeEnd。"))
            return False
        if sys.platform == "win32":
            try:
                import winsound
                self.stop()
                self._windows = winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC
                                   | winsound.SND_NODEFAULT)
                self._windows_active = True
                self._windows_until = time.monotonic() + 1.2
                self.completed.emit("submitted")
                return True
            except (ImportError, OSError, RuntimeError):
                self.failed.emit(t("音效播放失败，请检查系统声音输出后重试。"))
                return False
        program = "/usr/bin/afplay" if sys.platform == "darwin" else (
            shutil.which("paplay") or shutil.which("aplay"))
        if not program:
            self.failed.emit(t("当前系统没有可用的音效播放器。"))
            return False
        command = (program, [str(path)])
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._pending = command
            self._stopping = True
            self.process.kill()
        else:
            self._start(command)
        return True

    def _start(self, command):
        self._stopping = False
        self._reported_failure = False
        self.process.start(*command)

    def _error(self, error):
        if not self.closed and not self._stopping and not self._reported_failure:
            self._reported_failure = True
            self.failed.emit(t("音效播放失败，请检查系统声音输出后重试。"))
            self.completed.emit("failed")

    def _finished(self, exit_code, exit_status):
        if not self._stopping and not self.closed and not self._reported_failure:
            if exit_code != 0 or exit_status != QProcess.ExitStatus.NormalExit:
                self._reported_failure = True
                self.failed.emit(t("音效播放失败，请检查系统声音输出后重试。"))
                self.completed.emit("failed")
            else:
                self.completed.emit("submitted")
        self._stopping = False
        pending, self._pending = self._pending, None
        if pending is not None and self.enabled and not self.closed:
            self._start(pending)

    def is_playing(self):
        """Include pending replacements and Windows' short async playback window."""
        if self.closed or not self.enabled:
            return False
        return bool(self._pending is not None
                    or self.process.state() != QProcess.ProcessState.NotRunning
                    or (self._windows_active and time.monotonic() < self._windows_until))

    def stop(self):
        """Cancel the current sound and any queued replacement."""
        self._pending = None
        self._windows_until = 0.0
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self._stopping = True
            self.process.kill()
        if self._windows_active:
            self._windows_active = False
            try:
                self._windows.PlaySound(None, 0)
            except (OSError, RuntimeError):
                if not self.closed:
                    self.failed.emit(t("无法停止音效，请重试。"))

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop()

    def shutdown(self):
        self.closed = True
        self.stop()
