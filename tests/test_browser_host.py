import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiflame.integrations.registry import SOURCES
from aiflame.integrations.chatgpt_web.host import (
    WEB_SOURCES,
    sanitize,
    read_message,
    write_message,
    extension_id,
    forward,
)
from aiflame.integrations.chatgpt_web.install import install
from aiflame.integrations.chatgpt_web.downloads import extension_archive
from aiflame.core.paths import load_config
from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event


class TestBrowserHost(unittest.TestCase):
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
                install()
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
                ["chrome-extension://" + extension_id() + "/"],
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
        self.assertEqual(extension_id(), "hifannlfdbbhmmgecknoakpbhfihihbg")

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

    def test_saved_settings_persist_across_restart(self):
        with tempfile.TemporaryDirectory() as t:
            engine = Engine(Path(t) / "db", sources=SOURCES)
            settings = dict(engine.settings)
            settings["brightness"] = 7
            settings["source_enabled"] = {**settings["source_enabled"], "codex": False}
            engine.update_settings(settings)
            engine.db.close()
            engine = Engine(Path(t) / "db", sources=SOURCES)
            try:
                self.assertEqual(engine.settings, settings)
            finally:
                engine.db.close()
