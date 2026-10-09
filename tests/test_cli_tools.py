import json
import os
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import SourcePolicy
from aiflame.integrations.cli_tools.lifecycle import from_hook, from_hooks
from aiflame.integrations.cli_tools.install import install
from aiflame.integrations.cli_tools.diagnostics import diagnostics
from aiflame.integrations.cli_tools.providers import PROVIDERS
from aiflame.integrations.cli_tools.hook import main as hook_main


class CliToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"AIFLAME_DATA_DIR": str(self.root / "runtime")})
        self.environment.start()
        sources = [SourcePolicy(source, p.name, "", authenticated=True, requires_start=True, ignore_unregistered=True, allow_late_cancel=True) for source, p in PROVIDERS.items()]
        self.engine = Engine(self.root / "events.sqlite", sources=sources)

    def tearDown(self):
        self.engine.db.close()
        self.environment.stop()
        self.temp.cleanup()

    def event(self, kind, source="claude-code", **kwargs):
        return from_hook(source, {"hook_event_name": kind, "session_id": "session", **kwargs}, self.root / "runtime")

    def test_native_session_consecutive_turns_and_content(self):
        first = self.event("UserPromptSubmit", prompt="PRIVATE")
        stop = self.event("Stop", transcript_path="PRIVATE")
        self.assertEqual(first["task_id"], stop["task_id"])
        self.assertNotIn("PRIVATE", str(first) + str(stop))
        self.assertEqual(stop, {**self.event("Stop"), "occurred_at": stop["occurred_at"]})
        self.assertIsNone(self.event("SessionEnd"))
        second = self.event("UserPromptSubmit")
        self.assertNotEqual(first["task_id"], second["task_id"])

    def test_explicit_turn_start_is_idempotent(self):
        a = self.event("TurnStart", "deepseek-harness", turn_id="2")
        b = self.event("TurnStart", "deepseek-harness", turn_id="2")
        self.assertEqual(a["event_id"], b["event_id"])
        self.assertEqual(a["task_id"], b["task_id"])
        self.assertEqual(self.event("TurnActivity", "deepseek-harness", turn_id="2")["seq"], 2)
        self.assertEqual(self.event("TurnEnd", "deepseek-harness", turn_id="2", reason="completed")["state"], "done")

    def test_hook_entrypoint_delivers_actual_event(self):
        payload = {"hook_event_name": "UserPromptSubmit", "session_id": "hook-session", "turn_id": "turn"}
        with patch("sys.argv", ["helper", "claude-code", "--port", "9123"]), patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("aiflame.integrations.cli_tools.hook.deliver") as deliver:
            hook_main()
        deliver.assert_called_once()
        event, port = deliver.call_args.args
        self.assertEqual(event["source_id"], "claude-code")
        self.assertEqual(event["state"], "working")
        self.assertEqual(port, 9123)

    def test_failed_tool_is_activity_failed_turn_is_error(self):
        self.event("UserPromptSubmit")
        self.assertEqual(self.event("PostToolUseFailure")["state"], "working")
        self.assertEqual(self.event("StopFailure")["state"], "error")
        self.assertIsNone(self.event("PreToolUse"))

    def test_cancel_does_not_become_success(self):
        start = self.event("UserPromptSubmit")
        cancel = self.event("SessionEnd")
        self.engine.accept(start, "claude-code")
        self.engine.accept(cancel, "claude-code")
        self.assertEqual(self.engine.desired()["state"], "idle")
        self.assertEqual(cancel["reason"], "cancelled")

    def test_ignored_payload_does_not_create_runtime(self):
        unused = self.root / "unused"
        for payload in ({}, {"hook_event_name": "SubagentStop", "session_id": "s"}):
            self.assertIsNone(from_hook("qoder", payload, unused))
        self.assertFalse(unused.exists())

    def test_stop_without_start_and_continuing_stop_completes(self):
        self.assertIsNone(self.event("Stop"))
        self.event("UserPromptSubmit")
        self.assertEqual(self.event("Stop", stop_hook_active=True)["state"], "done")

    def test_native_new_prompt_cancels_prior_task(self):
        start = self.event("UserPromptSubmit")
        events = from_hooks("claude-code", {"hook_event_name": "UserPromptSubmit", "session_id": "session"}, self.root / "runtime")
        self.assertEqual([e["state"] for e in events], ["idle", "working"])
        self.assertEqual(events[0]["task_id"], start["task_id"])
        self.assertNotEqual(events[1]["task_id"], start["task_id"])

    def test_deepseek_end_reasons(self):
        for reason in ("error", "blocked", "max-tokens", "unknown"):
            self.event("TurnStart", "deepseek-harness", turn_id=reason)
            self.assertEqual(self.event("TurnEnd", "deepseek-harness", turn_id=reason, reason=reason)["state"], "error")
        self.event("TurnStart", "deepseek-harness", turn_id="abort")
        self.assertEqual(self.event("TurnEnd", "deepseek-harness", turn_id="abort", reason="aborted")["state"], "idle")

    def test_install_preserves_third_party_and_uninstall(self):
        home = self.root / "custom claude"
        home.mkdir()
        path = home / "settings.json"
        third = {"type": "command", "command": "third-party"}
        path.write_text(json.dumps({"env": {"EXAMPLE": "keep"}, "hooks": {"Stop": [{"matcher": "keep", "hooks": [third]}]}}))
        install("claude-code", config_dir=home)
        install("claude-code")
        data = json.loads(path.read_text())
        self.assertEqual(data["env"], {"EXAMPLE": "keep"})
        self.assertEqual(len(data["hooks"]["Stop"]), 2)
        result = diagnostics(self.engine.db)["integrations"][0]
        self.assertTrue(result["configuration_ready"])
        install("claude-code", uninstall=True)
        self.assertEqual(json.loads(path.read_text())["hooks"], {"Stop": [{"matcher": "keep", "hooks": [third]}]})

    def test_malformed_configuration_not_written(self):
        home = self.root / "qoder"
        home.mkdir()
        path = home / "settings.json"
        original = '{"hooks":{"Stop":"wrong"}}'
        path.write_text(original)
        with self.assertRaises(ValueError):
            install("qoder", config_dir=home)
        self.assertEqual(path.read_text(), original)

    def test_shared_directory_does_not_remove_other_cli(self):
        home = self.root / "shared"
        install("claude-code", config_dir=home)
        install("qoder", config_dir=home)
        install("claude-code", config_dir=home, uninstall=True)
        hooks = json.loads((home / "settings.json").read_text())["hooks"]
        self.assertEqual(len(hooks["Stop"]), 1)
        self.assertIn("qoder", hooks["Stop"][0]["hooks"][0]["command"])

    def test_deepseek_requires_profile_and_installs_bridge(self):
        with self.assertRaises(ValueError):
            install("deepseek-harness")
        profile_root = self.root / "dsh"
        (profile_root / "profiles" / "default").mkdir(parents=True)
        manifest = profile_root / "profiles" / "default" / "package.json"
        manifest.write_text(json.dumps({"dependencies": {"keep": "1"}, "dsh": {"profile": {"bundles": ["keep"]}}}))
        with patch.dict(os.environ, {"DSH_HOME": str(profile_root)}), patch("aiflame.integrations.cli_tools.install.subprocess.run") as run:
            run.return_value.returncode = 0
            def added(arguments, **kwargs):
                package = json.loads(manifest.read_text())
                package["dependencies"]["aiflame-deepseek-harness"] = arguments[-1]
                package["dsh"]["profile"]["bundles"].append("aiflame-deepseek-harness")
                manifest.write_text(json.dumps(package))
                return run.return_value
            run.side_effect = added
            bundle = install("deepseek-harness", profile="default")
            self.assertEqual(run.call_args.args[0][1:5], ["plugin", "--profile", "default", "add"])
            bridge = json.loads((bundle / "bridge.json").read_text())
            self.assertEqual(bridge["helperArgv"][-2:], ["--cli-hook", "deepseek-harness"])
            row = next(row for row in diagnostics(self.engine.db)["integrations"] if row["source_id"] == "deepseek-harness")
            self.assertTrue(row["configuration_ready"])
            self.assertEqual(row["profiles"], ["default"])
            self.assertEqual(row["installed_profiles"], ["default"])
            manifest.write_text(json.dumps({"dependencies": {}, "dsh": {"profile": {"bundles": []}}}))
            row = next(row for row in diagnostics(self.engine.db)["integrations"] if row["source_id"] == "deepseek-harness")
            self.assertFalse(row["configuration_ready"])
            self.assertEqual(row["installed_profiles"], [])
            self.assertEqual(row["recorded_profiles"], ["default"])

    def test_diagnostics_excludes_simulated_events(self):
        event = self.event("UserPromptSubmit")
        self.engine.accept(event, "claude-code")
        self.engine.db.execute("INSERT INTO self_tests VALUES (?,?)", (event["event_id"], 0))
        self.engine.db.commit()
        result = diagnostics(self.engine.db)["integrations"][0]
        self.assertEqual(result["signals"]["start"]["received"], 0)


class NewAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {'AIFLAME_DATA_DIR': str(self.root / 'runtime')})
        self.env.start()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.env.stop)

    def test_gemini_uses_milliseconds_and_preserves_existing_hooks(self):
        directory = self.root / 'gemini'
        directory.mkdir()
        path = directory / 'settings.json'
        original = {'model': {'name': 'keep'}, 'hooks': {'AfterAgent': [{'hooks': [{'type': 'command', 'command': 'other'}]}]}}
        path.write_text(json.dumps(original))
        install('gemini-cli', config_dir=directory)
        install('gemini-cli', config_dir=directory)
        data = json.loads(path.read_text())
        self.assertEqual(len(data['hooks']['AfterAgent']), 2)
        handler = data['hooks']['AfterAgent'][-1]['hooks'][0]
        self.assertEqual(handler['timeout'], 5000)
        self.assertNotIn('statusMessage', handler)
        install('gemini-cli', config_dir=directory, uninstall=True)
        self.assertEqual(json.loads(path.read_text()), original)

    def test_gemini_permission_is_nonterminal_and_content_not_retained(self):
        def event(kind, **extra):
            return from_hook('gemini-cli', {'hook_event_name': kind, 'session_id': 'session', 'prompt': 'PRIVATE_TEXT', **extra}, self.root)
        start = event('BeforeAgent')
        wait = event('Notification', notification_type='ToolPermission', message='PRIVATE_TEXT')
        resumed = event('BeforeTool')
        done = event('AfterAgent', prompt_response='PRIVATE_TEXT')
        self.assertEqual([x['state'] for x in (start, wait, resumed, done)], ['working', 'needs_input', 'working', 'done'])
        self.assertEqual(len({x['task_id'] for x in (start, wait, resumed, done)}), 1)
        self.assertIsNone(event('SessionEnd'))
        self.assertNotIn(b'PRIVATE_TEXT', (self.root / 'cli-lifecycle.sqlite').read_bytes())

    def test_opencode_install_remove_and_foreign_file(self):
        from aiflame.integrations.cli_tools.opencode import plugin_ready
        directory = self.root / 'opencode'
        path = install('opencode', config_dir=directory)
        install('opencode', config_dir=directory)
        self.assertTrue(plugin_ready(directory))
        other = path.with_name('other.js')
        other.write_text('// keep')
        install('opencode', config_dir=directory, uninstall=True)
        self.assertFalse(path.exists())
        self.assertEqual(other.read_text(), '// keep')
        path.write_text('// foreign')
        with self.assertRaises(ValueError):
            install('opencode', config_dir=directory)
        self.assertEqual(path.read_text(), '// foreign')


if __name__ == "__main__":
    unittest.main()
