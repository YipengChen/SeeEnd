"""Demonstration completion and cancellation after moving it out of HTTP routes."""

from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.integrations.manual.actions import DemoTasks
from aiflame.integrations.registry import SOURCES


class TestDemo(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.engine = Engine(Path(temporary.name) / "events.db", sources=SOURCES)
        self.addCleanup(self.engine.db.close)
        self.context = SimpleNamespace(
            engine=self.engine,
            lock=threading.RLock(),
            stopping=Mock(
                wait=Mock(return_value=False), is_set=Mock(return_value=False)
            ),
        )
        self.demo = DemoTasks(self.context)

    def begin(self):
        with patch("aiflame.integrations.manual.actions.threading.Thread"):
            return self.demo.start({"outcome": "done"})["task_id"]

    def test_explicit_demo_completion(self):
        task = self.begin()
        self.assertEqual(self.engine.desired()["state"], "working")
        self.demo._finish(task, "done", self.demo.generation)
        self.assertEqual(self.engine.desired()["state"], "done")

    def test_cancel_prevents_late_completion_after_resume(self):
        task = self.begin()
        generation = self.demo.generation
        self.demo.cancel()
        self.engine.stop()
        self.engine.emergency = False
        self.demo._finish(task, "done", generation)
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.assertEqual(
            self.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )

    def test_shutdown_prevents_completion(self):
        task = self.begin()
        self.context.stopping.is_set.return_value = True
        self.demo._finish(task, "done", self.demo.generation)
        self.assertEqual(
            self.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )
