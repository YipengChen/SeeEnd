import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiflame.integrations.registry import SOURCES
from aiflame.integrations.chatgpt_web.host import (
    WEB_SOURCES,
    sanitize,
    read_message,
    write_message,
    extension_id,
    extension_origins,
    LEGACY_EXTENSION_ID,
    forward,
)
from aiflame.integrations.chatgpt_web.install import install
from aiflame.integrations.chatgpt_web.downloads import extension_archive, prepare_extension_folder, extension_folder_info, extension_folder_path
from aiflame.core.paths import load_config
from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event


class TestBrowserHost(unittest.TestCase):
    def test_browser_entrypoint_accepts_chromium_window_argument_and_explicit_port(self):
        from aiflame.integrations.chatgpt_web.host import main
        incoming, outgoing = io.BytesIO(), io.BytesIO()
        write_message(incoming, {'kind': 'ping'})
        incoming.seek(0)
        with patch('sys.argv', ['host', extension_origins()[0], '--parent-window=0', '--port', '19321']), patch('sys.stdin', SimpleNamespace(buffer=incoming)), patch('sys.stdout', SimpleNamespace(buffer=outgoing)), patch('aiflame.integrations.chatgpt_web.host.forward', return_value={'ok': True}) as forward_message, patch('aiflame.integrations.chatgpt_web.host.os.name', 'posix'):
            self.assertEqual(main(), 0)
        forward_message.assert_called_once_with({'kind': 'ping'}, 19321)
        outgoing.seek(0)
        self.assertEqual(read_message(outgoing), {'ok': True})

    def test_all_web_sources_have_independent_lifecycles(self):
        with tempfile.TemporaryDirectory() as t:
            engine = Engine(Path(t) / "db", sources=SOURCES)
            self.addCleanup(engine.db.close)
            for source in WEB_SOURCES:
                with self.subTest(source=source):
                    task = "00000000-0000-4000-8000-000000000001"
                    event = make_event(source, task, "working")
                    self.assertEqual(sanitize(event), event)
                    with self.assertRaises(EventError):
                        engine.accept(make_event(source, task, "done", 2), source)
                    engine.accept(event, source)
                    engine.accept(make_event(source, task, "done", 2), source)
                    self.assertEqual(engine.sources[source].requires_start, True)
            self.assertEqual(
                engine.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], len(WEB_SOURCES)
            )

    def test_bridge_uses_only_matching_source_token_for_events_and_ping(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            tokens = {source: "test-" + source for source in WEB_SOURCES}
            (root / "bridge-config.json").write_text(json.dumps({"tokens": tokens}))
            requests = []

            class Opener:
                def open(self, request, timeout):
                    requests.append(request)
                    return io.BytesIO(
                        json.dumps(
                            {
                                "event_id": "test",
                                "result": "queued",
                                "source_enabled": True,
                            }
                        ).encode()
                    )

            with (
                patch("aiflame.integrations.chatgpt_web.host.ROOT", root),
                patch(
                    "aiflame.integrations.chatgpt_web.host.urllib.request.build_opener",
                    return_value=Opener(),
                ),
            ):
                for source in WEB_SOURCES:
                    event = make_event(
                        source, "00000000-0000-4000-8000-000000000001", "working"
                    )
                    self.assertTrue(forward(event)["ok"])
                    self.assertEqual(json.loads(requests[-1].data), event)
                    self.assertEqual(
                        requests[-1].get_header("Authorization"),
                        "Bearer " + tokens[source],
                    )
                    self.assertTrue(
                        forward({"kind": "ping", "source_id": source})["ok"]
                    )
                    self.assertEqual(
                        requests[-1].get_header("Authorization"),
                        "Bearer " + tokens[source],
                    )
                    self.assertIsNone(requests[-1].data)
                for message in (
                    {"kind": "ping", "source_id": "codex"},
                    {"kind": "ping", "source_id": []},
                    {"kind": "ping", "prompt": "private"},
                ):
                    with self.assertRaises(ValueError):
                        forward(message)

    def test_old_bridge_retains_chatgpt_and_requires_repair_for_new_sources(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "bridge-config.json").write_text(
                json.dumps({"token": "old-web-token"})
            )
            with (
                patch("aiflame.integrations.chatgpt_web.host.ROOT", root),
                patch(
                    "aiflame.integrations.chatgpt_web.host.urllib.request.build_opener"
                ) as opener,
            ):
                self.assertEqual(
                    forward({"kind": "ping", "source_id": "kimi-web"}),
                    {"ok": False, "error": "bridge_update_required"},
                )
                opener.assert_not_called()
                opener.return_value.open.return_value = io.BytesIO(
                    b'{"source_enabled":true}'
                )
                self.assertTrue(forward({"kind": "ping"})["ok"])
                self.assertEqual(
                    opener.return_value.open.call_args.args[0].get_header(
                        "Authorization"
                    ),
                    "Bearer old-web-token",
                )

    def test_install_copies_only_web_tokens_and_keeps_extension_identity(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            tokens = {source: "test-" + source for source in WEB_SOURCES} | {
                "codex": "private-codex",
                "local-script": "private-api",
            }
            with (
                patch(
                    "aiflame.integrations.chatgpt_web.install.sys.platform", "darwin"
                ),
                patch(
                    "aiflame.integrations.chatgpt_web.install.user_data_dir",
                    return_value=root,
                ),
                patch(
                    "aiflame.integrations.chatgpt_web.install.manifest_path",
                    return_value=root / "native.json",
                ),
                patch(
                    "aiflame.integrations.chatgpt_web.install.load_config",
                    return_value={"tokens": tokens},
                ),
            ):
                install()
                manifest = json.loads((root / "native.json").read_text())
                manifest["allowed_origins"] = ["chrome-extension://" + LEGACY_EXTENSION_ID + "/"]
                (root / "native.json").write_text(json.dumps(manifest))
                install()
                manifest = json.loads((root / "native.json").read_text())
                manifest["allowed_origins"].append("chrome-extension://" + "a" * 32 + "/")
                (root / "native.json").write_text(json.dumps(manifest))
                with self.assertRaisesRegex(RuntimeError, "属于其他配置"):
                    install()
                manifest["allowed_origins"] = list(extension_origins())
                (root / "native.json").write_text(json.dumps(manifest))
            copied = root / "BrowserBridge/chrome"
            self.assertEqual(
                json.loads((copied / "bridge-config.json").read_text())["tokens"],
                {source: tokens[source] for source in WEB_SOURCES},
            )
            self.assertIn("deepseek-web", (copied / "host.py").read_text())
            self.assertNotIn(
                "private-codex", (copied / "bridge-config.json").read_text()
            )
            self.assertEqual(
                json.loads((root / "native.json").read_text())["allowed_origins"],
                list(extension_origins()),
            )

    def test_old_credentials_gain_separate_tokens_without_changing_existing_tokens(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as t,
            patch("aiflame.core.paths.runtime_dir", return_value=Path(t)),
        ):
            root = Path(t)
            root.joinpath("credentials.json").write_text(
                json.dumps(
                    {
                        "hub_id": "old",
                        "tokens": {"codex": "keep", "chatgpt-web": "keep-web"},
                    }
                )
            )
            config = load_config()
            self.assertEqual(config["tokens"]["codex"], "keep")
            self.assertEqual(config["tokens"]["chatgpt-web"], "keep-web")
            web_tokens = [config["tokens"][source] for source in WEB_SOURCES]
            self.assertEqual(len(set(web_tokens)), len(WEB_SOURCES))
            self.assertEqual(load_config(), config)

    def test_upgrade_preserves_old_switches_and_enables_new_web_sources(self):
        old_sources = tuple(
            s for s in SOURCES if s.id not in WEB_SOURCES or s.id == "chatgpt-web"
        )
        with tempfile.TemporaryDirectory() as t:
            db = Path(t) / "db"
            old = Engine(db, sources=old_sources)
            old.update_settings(
                {
                    "brightness": 7,
                    "source_enabled": {
                        s.id: s.id != "chatgpt-web" for s in old_sources
                    },
                }
            )
            old.db.close()
            current = Engine(db, sources=SOURCES)
            self.assertEqual(current.settings["brightness"], 7)
            self.assertFalse(current.settings["source_enabled"]["chatgpt-web"])
            self.assertTrue(
                all(
                    current.settings["source_enabled"][s]
                    for s in WEB_SOURCES
                    if s != "chatgpt-web"
                )
            )
            expected = current.settings
            current.update_settings({"duration": 12})
            current.db.close()
            restarted = Engine(db, sources=SOURCES)
            self.assertEqual(
                restarted.settings["source_enabled"], expected["source_enabled"]
            )
            restarted.db.close()

    def test_extension_download_includes_provider_adapters(self):
        import zipfile

        with zipfile.ZipFile(io.BytesIO(extension_archive())) as archive:
            self.assertIn("SeeEnd-Web-extension/providers.js", archive.namelist())
            manifest = json.loads(
                archive.read("SeeEnd-Web-extension/manifest.json")
            )
            self.assertEqual(manifest["content_scripts"][0]["js"][0], "detector.js")
            for content in manifest["content_scripts"]:
                for script in content["js"]:
                    self.assertIn("SeeEnd-Web-extension/" + script, archive.namelist())
            self.assertFalse(
                any(
                    "config" in name or "credentials" in name
                    for name in archive.namelist()
                )
            )

    def test_prepared_extension_is_stable_complete_and_updates_without_zip(self):
        with tempfile.TemporaryDirectory() as root:
            result = prepare_extension_folder(root)
            folder = Path(result["path"])
            self.assertTrue(result["prepared"])
            manifest = json.loads((folder / "manifest.json").read_text())
            for content in manifest["content_scripts"]:
                for script in content["js"]:
                    self.assertTrue((folder / script).is_file())
            self.assertTrue((folder / manifest["background"]["service_worker"]).is_file())
            self.assertTrue((folder / manifest["action"]["default_popup"]).is_file())
            self.assertFalse(any(path.suffix == ".zip" for path in folder.iterdir()))
            (folder / "providers.js").write_text("outdated")
            self.assertFalse(extension_folder_info(root)["prepared"])
            self.assertEqual(prepare_extension_folder(root)["path"], str(folder))
            self.assertNotEqual((folder / "providers.js").read_text(), "outdated")
            self.assertNotIn("credentials.json", [p.name for p in folder.iterdir()])
            (folder / "providers.js").unlink()
            self.assertFalse(extension_folder_info(root)["prepared"])
            self.assertTrue(prepare_extension_folder(root)["prepared"])

    def test_visible_extension_path_uses_seeend_and_custom_runtimes_stay_isolated(self):
        for old, new in (("AI Flame", "SeeEnd"), ("ai-flame", "seeend")):
            runtime = Path("/tmp/platform-data") / old
            with patch("aiflame.integrations.chatgpt_web.downloads.user_data_dir", return_value=runtime):
                self.assertEqual(extension_folder_path(runtime), runtime.parent / new / "BrowserExtension")
        with tempfile.TemporaryDirectory() as runtime:
            self.assertEqual(extension_folder_path(runtime), Path(runtime) / "SeeEnd" / "BrowserExtension")

    def test_offline_guide_has_real_images_and_no_console_or_script(self):
        from aiflame.integrations.chatgpt_web.guide import offline_html, STEPS, IMAGES
        content = offline_html()
        self.assertNotIn("<script", content)
        self.assertNotIn("<svg", content)
        self.assertNotIn("href=", content)
        self.assertNotIn("SeeEnd 控制台", content)
        for _, _, filename in STEPS:
            if filename:
                magic = b"\x89PNG" if filename.endswith(".png") else b"\xff\xd8"
                self.assertTrue((IMAGES / filename).read_bytes().startswith(magic))
        self.assertIn("加载未打包的扩展程序", content)
        self.assertIn("BrowserExtension", content)
        self.assertIn("等待安装文件夹路径显示", content)

    def test_prepare_extension_does_not_overwrite_foreign_folders_or_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            folder = Path(root) / "SeeEnd" / "BrowserExtension"
            folder.mkdir(parents=True)
            personal = folder / "manifest.json"
            personal.write_text("unrelated")
            with self.assertRaises(EventError):
                prepare_extension_folder(root)
            self.assertEqual(personal.read_text(), "unrelated")
            personal.unlink()
            folder.rmdir()
            folder.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(EventError):
                prepare_extension_folder(root)
            self.assertEqual(list(Path(outside).iterdir()), [])

    def event(self, state="working", seq=1):
        return make_event(
            "chatgpt-web", "00000000-0000-4000-8000-000000000001", state, seq
        )

    def test_content_and_cross_source_rejected(self):
        e = self.event()
        self.assertEqual(sanitize(e), e)
        e["prompt"] = "PRIVATE"
        with self.assertRaises(ValueError):
            sanitize(e)
        e = self.event()
        e["source_id"] = "codex"
        with self.assertRaises(ValueError):
            sanitize(e)

    def test_native_frames_are_bounded_and_exact(self):
        stream = io.BytesIO()
        write_message(stream, self.event())
        stream.seek(0)
        self.assertEqual(read_message(stream)["state"], "working")
        for data in (b"\x01", struct.pack("=I", 5000), struct.pack("=I", 4) + b"{}"):
            with self.assertRaises(ValueError):
                read_message(io.BytesIO(data))

    def test_extension_id_matches_manifest_key(self):
        self.assertEqual(extension_id(), "miokmdeomlchjpihkcnommpnheagoimc")

    def test_web_requires_start_and_cancel_is_terminal(self):
        with tempfile.TemporaryDirectory() as t:
            engine = Engine(Path(t) / "db", sources=SOURCES)
            try:
                with self.assertRaises(EventError):
                    engine.accept(self.event("done", 2), "chatgpt-web")
                engine.accept(self.event(), "chatgpt-web")
                cancelled = self.event("idle", 2)
                cancelled["reason"] = "cancelled"
                engine.accept(cancelled, "chatgpt-web")
                with self.assertRaises(EventError):
                    engine.accept(self.event("done", 3), "chatgpt-web")
            finally:
                engine.db.close()
