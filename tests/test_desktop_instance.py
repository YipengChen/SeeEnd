"""Real subprocess checks for desktop ownership and activation IPC."""

import os
from importlib.util import find_spec
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.instance import DesktopInstance

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = r'''
import sys
from PySide6.QtCore import QCoreApplication, QTimer
from aiflame.desktop.instance import DesktopInstance
app = QCoreApplication([])
instance = DesktopInstance(directory=sys.argv[1])
instance.activation_requested.connect(lambda: print("ACTIVATED", flush=True))
if sys.argv[2] == "locked":
    instance.directory.mkdir(parents=True, exist_ok=True)
    assert instance.lock.tryLock(0)
    print("LOCKED", flush=True)
else:
    if sys.argv[2] == "noack":
        instance._read = lambda socket: None
    try:
        primary = instance.start(timeout_ms=int(sys.argv[3]))
    except RuntimeError as error:
        print("ERROR:" + str(error), flush=True)
        sys.exit(2)
    print("PRIMARY" if primary else "FORWARDED", flush=True)
    if not primary:
        sys.exit(0)
QTimer.singleShot(15000, app.quit)
app.exec()
instance.close()
'''


@unittest.skipUnless(HAS_QT, "Install the desktop extra to test desktop ownership")
class DesktopInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "user"
        self.processes = []
        probe = DesktopInstance(directory=self.directory)
        self.socket_directory = probe.socket_directory
        self.endpoint = Path(probe.endpoint)
        probe.close()

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
            process.reader_thread.join(timeout=3)
            process.stdout.close()
            process.stderr.close()
        self.endpoint.unlink(missing_ok=True)
        if self.socket_directory.exists():
            self.socket_directory.rmdir()
        self.temp.cleanup()

    def spawn(self, mode="normal", timeout=2000):
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", SCRIPT, str(self.directory), mode, str(timeout)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        process.lines = queue.Queue()

        def read_lines():
            for line in process.stdout:
                process.lines.put(line.strip())
            process.lines.put(None)

        process.reader_thread = threading.Thread(target=read_lines, daemon=True)
        process.reader_thread.start()
        self.processes.append(process)
        return process

    def line(self, process):
        try:
            result = process.lines.get(timeout=5)
        except queue.Empty:
            self.fail("child process did not respond")
        if result is None:
            self.fail(process.stderr.read())
        return result

    def test_concurrent_start_has_one_primary_and_acknowledged_activation(self):
        first, second = self.spawn(), self.spawn()
        results = [self.line(first), self.line(second)]
        self.assertCountEqual(results, ["PRIMARY", "FORWARDED"])
        primary = [first, second][results.index("PRIMARY")]
        secondary = [first, second][results.index("FORWARDED")]
        self.assertEqual(secondary.wait(timeout=3), 0)
        self.assertEqual(self.line(primary), "ACTIVATED")

    def test_dead_owner_and_stale_socket_are_recovered(self):
        first = self.spawn()
        self.assertEqual(self.line(first), "PRIMARY")
        first.kill()
        first.wait(timeout=3)
        second = self.spawn()
        self.assertEqual(self.line(second), "PRIMARY")

    def test_live_unresponsive_owner_is_not_replaced(self):
        owner = self.spawn("locked")
        self.assertEqual(self.line(owner), "LOCKED")
        secondary = self.spawn(timeout=150)
        self.assertIn("已运行但暂未响应", self.line(secondary))
        self.assertEqual(secondary.wait(timeout=3), 2)
        self.assertIsNone(owner.poll())

    def test_secondary_requires_acknowledgment(self):
        owner = self.spawn("noack")
        self.assertEqual(self.line(owner), "PRIMARY")
        secondary = self.spawn(timeout=150)
        self.assertIn("已运行但暂未响应", self.line(secondary))
        self.assertEqual(secondary.wait(timeout=3), 2)

    def test_close_is_idempotent_and_allows_restart(self):
        first = DesktopInstance(directory=self.directory)
        self.assertTrue(first.start())
        first.close()
        first.close()
        second = DesktopInstance(directory=self.directory)
        try:
            self.assertTrue(second.start())
        finally:
            second.close()

    def test_identity_ignores_runtime_override_and_installation(self):
        with patch.dict(os.environ, {"AIFLAME_DATA_DIR": "/tmp/first-install"}):
            first = DesktopInstance()
        with patch.dict(os.environ, {"AIFLAME_DATA_DIR": "/tmp/other-install"}):
            second = DesktopInstance()
        self.assertEqual(first.endpoint, second.endpoint)
        self.assertEqual(first.lock.fileName(), second.lock.fileName())
        self.assertLess(len(first.endpoint.encode()), 100)
        first.close()
        second.close()


if __name__ == "__main__":
    unittest.main()
