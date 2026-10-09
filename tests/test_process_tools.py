"""Check standalone tool dispatch in both supported execution modes."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from aiflame.core.process import command


class ToolDispatchTests(unittest.TestCase):
    def test_source_tool_runs_upstream_cli(self):
        result = subprocess.run(command("--esptool", "version"),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("5.4.0", result.stdout)

    def test_frozen_tool_uses_separate_executable(self):
        with patch.object(sys, "frozen", True, create=True), \
             patch.object(sys, "_MEIPASS", "/tmp/SeeEnd resources", create=True):
            result = command("--esptool", "--port", "/dev/test", "version")
        self.assertEqual(Path(result[0]).parent,
                         Path("/tmp/SeeEnd resources/third_party/esptool"))
        self.assertEqual(result[1:], ["--port", "/dev/test", "version"])
