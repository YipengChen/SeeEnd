"""On-demand Codex metadata resolution, ambiguity and process identity guards."""

from contextlib import closing, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.core.task_origins import validate_context
from aiflame.hub.task_returns import TaskReturns
from aiflame.integrations.codex.hook import main, return_context
from aiflame.integrations.codex import return_location as location
from aiflame.integrations.registry import SOURCES


def context(cwd="/work", **updates):
    return {"session_id": str(uuid.uuid4()), "turn_id": str(uuid.uuid4()), "cwd": cwd, **updates}


class ContextTests(unittest.TestCase):
    def test_metadata_is_bounded_source_scoped_and_has_no_content_fields(self):
        value = context()
        self.assertEqual(validate_context(value, "codex"), value)
        payload = {**value, "prompt": "PRIVATE PROMPT", "last_assistant_message": "PRIVATE REPLY",
                   "tool_input": {"command": "PRIVATE COMMAND"}}
        self.assertEqual(return_context(payload), value)
        for bad in ({**value, "prompt": "PRIVATE"}, {**value, "cwd": "relative"},
                    {**value, "cwd": "/x\nsecret"}, {**value, "turn_id": True},
                    {**value, "session_id": "invalid"}, {**value, "cwd": "/" + "x" * 512}):
            with self.subTest(bad=bad), self.assertRaises(EventError):
                validate_context(bad, "codex")
        with self.assertRaises(EventError):
            validate_context(value, "claude-code")

    def test_windows_absolute_context_accepts_drive_and_unc_but_not_relative_paths(self):
        for cwd in (r"C:\Projects\SeeEnd", "C:/Projects/SeeEnd", r"\\server\share\SeeEnd"):
            value = context(cwd=cwd)
            with self.subTest(cwd=cwd):
                self.assertEqual(validate_context(value, "codex"), value)
                self.assertEqual(return_context(value), value)
        for cwd in ("C:relative", "C:", r"\root", r"\\server"):
            with self.subTest(cwd=cwd), self.assertRaises(EventError):
                validate_context(context(cwd=cwd), "codex")

    def test_daemon_hook_forwards_metadata_without_changing_task_identity_or_stdout(self):
        from aiflame.integrations.codex.events import from_hook
        for kind in ("UserPromptSubmit", "Stop", "Interrupt"):
            payload = {**context(), "hook_event_name": kind, "prompt": "PRIVATE", "instance_id": "stable"}
            output = io.StringIO()
            with patch("aiflame.integrations.codex.hook.sys.argv", ["codex-hook"]), \
                    patch("aiflame.integrations.codex.hook.sys.stdin", io.StringIO(json.dumps(payload))), \
                    patch("aiflame.integrations.codex.hook.prepare", return_value=(payload, None)), \
                    patch("aiflame.integrations.codex.hook.deliver") as deliver, redirect_stdout(output):
                main()
            event = deliver.call_args.args[0]
            self.assertEqual(event["task_id"], from_hook(payload)["task_id"])
            self.assertEqual(event["return_context"], {k: payload[k] for k in ("session_id", "turn_id", "cwd")})
            self.assertNotIn("PRIVATE", json.dumps(event))
            self.assertNotIn("return_target", event)
            self.assertEqual(output.getvalue(), "")

    def test_invalid_location_metadata_does_not_drop_lifecycle(self):
        payload = {**context(cwd="relative"), "hook_event_name": "UserPromptSubmit"}
        with patch("aiflame.integrations.codex.hook.sys.argv", ["codex-hook"]), \
                patch("aiflame.integrations.codex.hook.sys.stdin", io.StringIO(json.dumps(payload))), \
                patch("aiflame.integrations.codex.hook.prepare", return_value=(payload, None)), \
                patch("aiflame.integrations.codex.hook.deliver") as deliver:
            main()
        event = deliver.call_args.args[0]
        self.assertEqual(event["state"], "working")
        self.assertNotIn("return_context", event)

    def test_context_persists_without_background_resolution_and_cannot_be_redirected(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "events.sqlite"
            engine = Engine(path, sources=SOURCES)
            value = context()
            start = {**make_event("codex", "task", "working"), "return_context": value}
            with patch.object(location, "resolve") as resolve:
                engine.accept(start, "codex")
                summary = engine.snapshot()["events"][0]["return_to"]
                self.assertTrue(summary["requires_resolution"])
                resolve.assert_not_called()
            engine.accept({**make_event("codex", "task", "done", 2), "return_context": context()}, "codex")
            self.assertEqual(engine.return_target(start["event_id"])[0], {"kind": "codex-session", **value})
            engine.db.close()
            engine = Engine(path, sources=SOURCES)
            self.assertEqual(engine.origins.context("codex", "task"), value)
            engine.db.execute("UPDATE events SET created=0")
            engine.cleanup(force=True)
            self.assertEqual(engine.db.execute("SELECT count(*) FROM task_return_contexts").fetchone()[0], 0)
            engine.db.close()


@unittest.skipUnless(sys.platform == "darwin", "Requires a macOS application scripting dictionary")
class ScriptCompilationTests(unittest.TestCase):
    def compile_scripts(self, app, scripts):
        if not Path(app).exists():
            self.skipTest("Application is not installed: " + app)
        with tempfile.TemporaryDirectory() as root:
            for index, script in enumerate(scripts):
                # Resolve the installed dictionary by path before Launch Services
                # has registered a newly installed app; compile without executing.
                source = Path(root) / f"script-{index}.applescript"
                bundle = "com.apple.Terminal" if "Terminal.app" in app else "com.googlecode.iterm2"
                source.write_text(script.replace(f'application id "{bundle}"', f'application "{app}"'), encoding="utf-8")
                result = subprocess.run(["/usr/bin/osacompile", "-o", str(source.with_suffix(".scpt")), str(source)],
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_terminal_scripts_compile_against_installed_dictionary(self):
        from aiflame.integrations.terminal_return import TERMINAL_SCRIPT
        self.compile_scripts("/System/Applications/Utilities/Terminal.app", [location.TERMINAL_METADATA, TERMINAL_SCRIPT])

    def test_iterm_scripts_compile_against_installed_dictionary(self):
        from aiflame.integrations.terminal_return import ITERM_SCRIPT
        self.compile_scripts("/Applications/iTerm.app", [location.ITERM_METADATA, ITERM_SCRIPT])


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "project"
        self.cwd.mkdir()
        self.value = context(str(self.cwd))
        self.database = self.root / "state_5.sqlite"
        with closing(sqlite3.connect(self.database)) as db, db:
            # No body/preview/title columns: resolving must not need them.
            db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY,name TEXT,cwd TEXT,archived INTEGER)")
            db.execute("INSERT INTO threads VALUES (?,?,?,0)", (self.value["session_id"], "回复234", str(self.cwd)))
        self.table = {500: {"ppid": 1, "command": "/Applications/Terminal", "tty": "??", "started": "app"},
                      600: {"ppid": 500, "command": "-zsh", "tty": "ttys005", "started": "shell"},
                      700: {"ppid": 600, "command": "/vendor/codex", "tty": "ttys005",
                            "started": "Wed Oct 14 13:57:23 2026"}}
        self.tabs = [{"app": "Terminal", "tty": "/dev/ttys005", "title": "回复234 | project", "window_id": "12"}]
        self.platform = patch.object(location.sys, "platform", "darwin")
        self.platform.start()
        self.addCleanup(self.platform.stop)

    def resolve(self, **updates):
        args = {"home": self.root, "table": self.table, "tabs": self.tabs, "cwds": {700: str(self.cwd)}}
        return location.resolve(self.value, **{**args, **updates})

    def add_thread(self, name="回复234", cwd=None, archived=0):
        sid = str(uuid.uuid4())
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("INSERT INTO threads VALUES (?,?,?,?)", (sid, name, str(cwd or self.cwd), archived))
        return sid

    def test_unique_name_directory_and_live_pid_resolve_existing_terminal(self):
        result = self.resolve()
        self.assertEqual(result.destination["pid"], 700)
        self.assertEqual(result.destination["session_id"], self.value["session_id"])
        self.assertEqual(result.destination["tty"], "/dev/ttys005")
        self.assertEqual(result.title, self.tabs[0]["title"])

    def test_same_name_full_directory_history_does_not_block_one_live_position(self):
        self.add_thread()
        self.add_thread(archived=1)
        self.assertEqual(self.resolve().destination["pid"], 700)

    def test_same_name_in_different_directory_does_not_create_a_false_collision(self):
        other = self.root / "another-project"
        other.mkdir()
        self.add_thread(cwd=other)
        self.assertEqual(self.resolve().destination["pid"], 700)

    def test_identical_visible_positions_across_terminal_and_iterm_are_ambiguous(self):
        tabs = self.tabs + [{**self.tabs[0], "app": "iTerm2", "tty": "/dev/ttys006",
                             "window_id": "13", "session_guid": str(uuid.uuid4())}]
        table = {**self.table,
                 800: {**self.table[500], "command": "/Applications/iTerm2"},
                 801: {**self.table[600], "ppid": 800, "tty": "ttys006"},
                 802: {**self.table[700], "ppid": 801, "tty": "ttys006"}}
        with self.assertRaisesRegex(EventError, "2 个终端位置"):
            self.resolve(tabs=tabs, table=table, cwds={700: str(self.cwd), 802: str(self.cwd)})

    def test_same_title_without_live_codex_is_not_an_ambiguity(self):
        tabs = self.tabs + [{**self.tabs[0], "tty": "/dev/ttys006", "window_id": "13"}]
        self.assertEqual(self.resolve(tabs=tabs).destination["pid"], 700)
        with self.assertRaisesRegex(EventError, "未找到对应终端"):
            self.resolve(table={500: self.table[500], 600: self.table[600]})

    def test_exited_duplicate_stops_counting_while_the_shell_and_title_remain(self):
        tabs = self.tabs + [{**self.tabs[0], "tty": "/dev/ttys006", "window_id": "13"}]
        table = {**self.table, 601: {**self.table[600], "tty": "ttys006"},
                 701: {**self.table[700], "ppid": 601, "tty": "ttys006"}}
        cwds = {700: str(self.cwd), 701: str(self.cwd)}
        with self.assertRaisesRegex(EventError, "2 个终端位置"):
            self.resolve(tabs=tabs, table=table, cwds=cwds)
        del table[701]
        self.assertEqual(self.resolve(tabs=tabs, table=table, cwds=cwds).destination["pid"], 700)

    def test_identical_title_with_different_full_directory_is_not_an_ambiguity(self):
        other = self.root / "other" / "project"
        other.mkdir(parents=True)
        tabs = self.tabs + [{**self.tabs[0], "tty": "/dev/ttys006", "window_id": "13"}]
        table = {**self.table, 601: {**self.table[600], "tty": "ttys006"},
                 701: {**self.table[700], "ppid": 601, "tty": "ttys006"}}
        self.assertEqual(self.resolve(tabs=tabs, table=table,
                                     cwds={700: str(self.cwd), 701: str(other)}).destination["pid"], 700)
        with self.assertRaisesRegex(EventError, "读取不完整"):
            self.resolve(tabs=tabs, table=table)

    def test_iterm_pane_retains_guid_tty_and_actual_client_identity(self):
        table = {**self.table, 500: {**self.table[500], "command": "/Applications/iTerm2"}}
        tab = {**self.tabs[0], "app": "iTerm2", "session_guid": str(uuid.uuid4())}
        result = self.resolve(table=table, tabs=[tab])
        self.assertEqual(result.destination["session_guid"], tab["session_guid"])
        self.assertEqual(result.destination["app"], "iTerm2")

    def test_incomplete_enumeration_never_declares_the_only_visible_match_unique(self):
        table = {**self.table, 701: {**self.table[700], "tty": "ttys006"}}
        with self.assertRaisesRegex(EventError, "读取不完整"):
            self.resolve(table=table)

    def test_multiple_clients_on_one_tty_or_unverifiable_cwd_are_refused(self):
        table = {**self.table, 701: {**self.table[700]}}
        with self.assertRaisesRegex(EventError, "读取不完整"):
            self.resolve(table=table)
        with self.assertRaisesRegex(EventError, "读取不完整"):
            self.resolve(cwds={})
        with self.assertRaisesRegex(EventError, "未找到对应终端"):
            self.resolve(cwds={700: str(self.root)})

    def test_pty_without_supported_terminal_ancestry_is_not_a_location(self):
        table = {**self.table, 600: {**self.table[600], "ppid": 1}}
        with self.assertRaisesRegex(EventError, "未找到对应终端"):
            self.resolve(table=table)

    def test_name_is_reread_after_rename_and_second_turn_keeps_same_conversation(self):
        first = self.resolve()
        self.value["turn_id"] = str(uuid.uuid4())
        self.assertEqual(self.resolve(), first)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE threads SET name='新的名字'")
        with self.assertRaisesRegex(EventError, "未找到对应终端"):
            self.resolve()
        changed = self.resolve(tabs=[{**self.tabs[0], "title": "新的名字 | project"}])
        self.assertEqual(changed.destination["session_id"], first.destination["session_id"])

    def test_no_name_changed_directory_and_unsupported_latest_schema_are_actionable(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE threads SET name=NULL")
        with self.assertRaisesRegex(EventError, "尚未生成名称"):
            self.resolve()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE threads SET name='回复234',cwd=?", (str(self.root),))
        with self.assertRaisesRegex(EventError, "目录已变化"):
            self.resolve()
        with closing(sqlite3.connect(self.root / "state_6.sqlite")) as db, db:
            db.execute("CREATE TABLE future_schema(id TEXT)")
        with self.assertRaisesRegex(EventError, "无法读取 Codex"):
            self.resolve()

    def test_git_project_title_and_exact_non_fuzzy_name_matching(self):
        (self.cwd / ".git").mkdir()
        child = self.cwd / "subdirectory"
        child.mkdir()
        self.value["cwd"] = str(child)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE threads SET cwd=?", (str(child),))
        self.assertEqual(self.resolve(cwds={700: str(child)}).destination["project"], "project")
        with self.assertRaisesRegex(EventError, "未找到对应终端"):
            self.resolve(cwds={700: str(child)}, tabs=[{**self.tabs[0], "title": "回复234其他内容 | project"}])

    def test_metadata_parser_rejects_partial_or_unsafe_tab_data(self):
        raw = "OK" + location.RS + location.FS.join(["/dev/ttys005", "回复234 | project", "12", "1"]) + location.RS
        self.assertEqual(location._parse_tabs(raw, "Terminal"), self.tabs)
        for bad in ("UNREADABLE", raw + "truncated", raw.replace("ttys005", "ttys005;open")):
            with self.assertRaises(EventError):
                location._parse_tabs(bad, "Terminal")
        with patch.object(location.subprocess, "run", return_value=SimpleNamespace(returncode=1, stderr="-1743", stdout="")):
            with self.assertRaisesRegex(EventError, "自动化"):
                location.terminal_tabs(self.table)

    def test_title_change_after_authorization_never_selects_a_tab(self):
        from aiflame.integrations import terminal_return
        old = self.resolve()
        changed = location.ResolvedTerminal(old.destination, "另一个标题 | project", old.window_id)
        with patch.object(location, "resolve", side_effect=[old, changed]), \
                patch.object(terminal_return, "processes", return_value=self.table), \
                patch.object(terminal_return.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="", stdout="opened")) as run:
            with self.assertRaisesRegex(EventError, "标题已变化"):
                location.resolve_and_activate(self.value)
            self.assertEqual(run.call_count, 1)  # Permission query only; no selection.

    def test_new_live_duplicate_after_authorization_never_selects_a_pane(self):
        from aiflame.integrations import terminal_return
        actual_resolve = location.resolve
        table = {**self.table, 601: {**self.table[600], "tty": "ttys006"},
                 701: {**self.table[700], "ppid": 601, "tty": "ttys006"}}
        tabs = self.tabs + [{**self.tabs[0], "tty": "/dev/ttys006", "window_id": "13"}]
        with patch.object(location, "resolve", side_effect=lambda value: actual_resolve(value, home=self.root)), \
                patch.object(location, "processes", side_effect=[self.table, table]), \
                patch.object(location, "terminal_tabs", side_effect=[self.tabs, tabs]), \
                patch.object(location, "process_cwds", return_value={700: str(self.cwd), 701: str(self.cwd)}), \
                patch.object(terminal_return, "processes", return_value=self.table), \
                patch.object(terminal_return.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="", stdout="opened")) as run:
            with self.assertRaisesRegex(EventError, "2 个终端位置"):
                location.resolve_and_activate(self.value)
            self.assertEqual(run.call_count, 1)

    def test_unchanged_location_passes_title_and_window_as_arguments(self):
        from aiflame.integrations import terminal_return
        resolved = self.resolve()
        with patch.object(location, "resolve", return_value=resolved), \
                patch.object(terminal_return, "processes", return_value=self.table), \
                patch.object(terminal_return.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr="", stdout="opened")) as run:
            self.assertEqual(location.resolve_and_activate(self.value), "opened")
            self.assertEqual(run.call_args.args[0][-3:], ["/dev/ttys005", "回复234 | project", "12"])


@unittest.skipUnless(sys.platform == "darwin", "Requires macOS lsof")
class ProcessDirectoryTests(unittest.TestCase):
    def test_chinese_working_directory_with_desktop_and_c_locales(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "设计所 空格" / "code"
            directory.mkdir(parents=True)
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"],
                cwd=directory, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "ready")
                for locale in (None, "C"):
                    with self.subTest(locale=locale), patch.dict(os.environ):
                        for key in ("LANG", "LC_ALL", "LC_CTYPE"):
                            os.environ.pop(key, None)
                        if locale:
                            os.environ["LC_ALL"] = locale
                        self.assertEqual(location.process_cwds([child.pid]),
                                         {child.pid: str(directory.resolve())})
                        self.assertEqual(os.environ.get("LC_ALL"), locale)
            finally:
                child.communicate(timeout=5)


class RequestTests(unittest.TestCase):
    def test_expired_request_cannot_select_after_a_permission_wait(self):
        now = [0]
        service = TaskReturns(clock=lambda: now[0])
        value = {"kind": "codex-session", **context()}
        with patch("aiflame.hub.task_returns.threading.Thread") as thread:
            request = service.open("event", value, "codex")["request_id"]
            self.assertEqual(service.open("event", value, "codex")["request_id"], request)
            self.assertEqual(thread.call_count, 1)
        now[0] = 181
        with patch.object(location, "resolve_and_activate") as activate:
            service._terminal(request, value, None)
            activate.assert_not_called()
        self.assertEqual(service.status(request)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
