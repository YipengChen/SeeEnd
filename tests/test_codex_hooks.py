import sys, tempfile, unittest
from pathlib import Path
import os
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiflame.integrations.registry import SOURCES
from aiflame.core.engine import Engine
from aiflame.core.events import EventError
from aiflame.integrations.codex.events import from_hook


class TestHooks(unittest.TestCase):
    def test_import_and_ignored_payload_do_not_create_runtime_data(self):
        directory = Path(self.temp.name) / "unused-runtime"
        environment = {**os.environ, "AIFLAME_DATA_DIR": str(directory)}
        for arguments, payload in (
            (["-c", "import aiflame.integrations.codex.events"], None),
            (["-m", "aiflame", "--codex-hook"], "{}"),
        ):
            result = subprocess.run(
                [sys.executable, *arguments],
                input=payload,
                text=True,
                capture_output=True,
                env=environment,
                timeout=10,
                cwd=Path(__file__).resolve().parents[1],
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(directory.exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Path(self.temp.name) / "events.db", sources=SOURCES)

    def tearDown(self):
        self.engine.db.close()
        self.temp.cleanup()

    def hook(self, kind, turn="turn"):
        return from_hook(
            {
                "hook_event_name": kind,
                "session_id": "session",
                "turn_id": turn,
                "prompt": "PRIVATE",
                "tool_input": {"command": "PRIVATE"},
                "transcript_path": "PRIVATE",
            },
            self.temp.name,
        )

    def done(self, turn="turn"):
        return self.hook("Stop", turn)

    def accept(self, event):
        return self.engine.accept(event, "codex")

    def test_start_finish_correlate(self):
        start = self.hook("UserPromptSubmit")
        done = self.done()
        self.assertEqual(start["task_id"], done["task_id"])
        self.assertNotEqual(start["event_id"], done["event_id"])
        self.accept(start)
        self.assertEqual(self.engine.desired()["state"], "working")
        self.accept(done)
        self.assertEqual(self.engine.desired()["state"], "done")
        self.assertFalse(self.engine.active)

    def test_duplicate_stop_is_idempotent(self):
        self.accept(self.hook("UserPromptSubmit"))
        stop = self.hook("Stop")
        self.assertEqual(stop["event_id"], self.done()["event_id"])
        self.accept(stop)
        self.assertEqual(self.engine.desired()["state"], "done")
        self.assertTrue(self.accept(self.done())[0]["duplicate"])

    def test_cancel_blocks_late_done(self):
        self.accept(self.hook("UserPromptSubmit"))
        self.accept(self.hook("Interrupt"))
        with self.assertRaises(EventError):
            self.accept(self.done())
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_cancel_clears_already_queued_done(self):
        self.accept(self.hook("UserPromptSubmit"))
        self.accept(self.done())
        self.engine.desired()
        self.accept(self.hook("Interrupt"))
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_real_activity_renews_only_active_turn(self):
        start = self.hook("UserPromptSubmit")
        a = self.hook("PreToolUse")
        b = self.hook("PostToolUse")
        self.assertLess(start["seq"], a["seq"])
        self.assertLess(a["seq"], b["seq"])
        for event in (start, a, b):
            self.accept(event)
        self.assertEqual(len(self.engine.active), 1)
        self.accept(self.done())
        with self.assertRaises(EventError):
            self.accept(self.hook("PreToolUse"))

    def test_no_content_or_false_completion(self):
        for kind in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Interrupt"):
            event = self.hook(kind)
            self.assertNotIn("PRIVATE", str(event))
            self.assertNotEqual(event["state"], "done")
        for kind in ("SessionEnd", "PermissionRequest", "SubagentStop"):
            self.assertIsNone(self.hook(kind))
        self.assertEqual(self.hook("Stop")["state"], "done")
        self.assertIsNone(
            from_hook({"hook_event_name": "UserPromptSubmit", "session_id": "s"})
        )

    def test_parallel_cancel_preserves_other_task(self):
        self.accept(self.hook("UserPromptSubmit", "a"))
        self.accept(self.hook("UserPromptSubmit", "b"))
        self.accept(self.hook("Interrupt", "a"))
        self.assertEqual(len(self.engine.active), 1)
        self.assertEqual(self.engine.desired()["state"], "working")

    def test_duplicate_start_does_not_restart(self):
        first = self.hook("UserPromptSubmit")
        self.accept(first)
        self.assertTrue(self.accept(self.hook("UserPromptSubmit"))[0]["duplicate"])

    def test_unregistered_completion_does_not_interrupt_user_turn(self):
        self.accept(self.hook("UserPromptSubmit"))
        for _ in range(2):
            result, status = self.accept(self.done("background"))
            self.assertEqual(result["result"], "ignored_unregistered_turn")
            self.assertEqual(status, 200)
        self.assertEqual(self.engine.desired()["state"], "working")
        self.assertEqual(
            self.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
        )
        self.accept(self.done())
        self.assertEqual(self.engine.desired()["state"], "done")
        self.assertTrue(self.accept(self.done())[0]["duplicate"])

    def test_tool_activity_does_not_admit_background_turn(self):
        result, _ = self.accept(self.hook("PreToolUse", "background"))
        self.assertEqual(result["result"], "ignored_unregistered_turn")
        result, _ = self.accept(self.done("background"))
        self.assertEqual(result["result"], "ignored_unregistered_turn")
        self.assertEqual(self.engine.desired()["state"], "idle")

    def test_submission_survives_hub_restart(self):
        self.accept(self.hook("UserPromptSubmit"))
        self.engine.db.close()
        self.engine = Engine(Path(self.temp.name) / "events.db", sources=SOURCES)
        self.accept(self.done())
        self.assertEqual(self.engine.desired()["state"], "done")

    def test_parallel_user_turns_can_each_finish(self):
        for turn in ("a", "b"):
            self.accept(self.hook("UserPromptSubmit", turn))
            self.accept(self.done(turn))
        self.assertEqual(len(self.engine.queue), 2)


if __name__ == "__main__":
    unittest.main()
