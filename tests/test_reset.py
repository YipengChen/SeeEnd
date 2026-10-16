"""Reset only isolated SeeEnd runtime state; never invoke external installers."""
from copy import deepcopy
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.core.paths import load_config, write_private
from aiflame.devices.models import DEFAULT_MODEL
from aiflame.hub.server import Hub
from aiflame.integrations.registry import SOURCES
from live_client import DashboardClient


class ResetFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.env = patch.dict(os.environ, {"AIFLAME_DATA_DIR": str(self.runtime)})
        self.env.start()
        self.addCleanup(self.env.stop)
        config = load_config()
        config["devices"] = {"old-lamp": {"device_id": "old-lamp", "hardware_model": DEFAULT_MODEL,
                                           "connection_paused": True, "wifi_device_secret": "old-pairing"}}
        write_private(self.runtime / "credentials.json", config)
        self.hub = Hub(port=0, config=config, runtime=self.runtime)
        self.addCleanup(self.hub.stop)

    def seed_history(self):
        self.hub.engine.update_settings({"language": "en", "duration": 45, "brightness": 7,
                                         "hold_until_activity": True, "desktop_flame_enabled": False})
        self.hub.engine.accept(make_event("manual", "old-task", "done"), "manual")
        self.hub.engine.db.execute("INSERT INTO self_tests VALUES (?,?)", ("old-test", 1))
        self.hub.engine.db.commit()


class ResetTests(ResetFixture, unittest.TestCase):
    def test_reset_removes_wechat_profile_and_restores_disabled_channel(self):
        from aiflame.integrations.wechat.credentials import WechatCredentials
        from test_wechat import ACCOUNT
        old = self.hub.wechat
        with patch.object(WechatCredentials, "_call", return_value=None):
            old.credentials.save(ACCOUNT)
            old.bound = True
            old.account = dict(ACCOUNT)
            self.hub.engine.update_settings({"wechat_notifications": True})
            result = self.hub.reset_app()
        self.assertTrue(old.closed)
        self.assertFalse(result["settings"]["wechat_notifications"])
        self.assertFalse(result["wechat"]["bound"])
        self.assertFalse(WechatCredentials(self.runtime).has_account())
        self.assertIn("wechat", self.hub.engine.router.outputs)

    def test_reset_restores_defaults_and_persists_rotated_empty_state(self):
        self.seed_history()
        before = deepcopy(self.hub.config)
        old_engine = self.hub.engine
        for base in ("codex-sequences.sqlite", "cli-lifecycle.sqlite"):
            with closing(sqlite3.connect(self.runtime / base)) as db:
                db.execute("CREATE TABLE old_data(value TEXT)")
            for suffix in ("-wal", "-shm", "-journal"):
                (self.runtime / (base + suffix)).write_text("owned old cache")
        for name in ("cli-integrations.json", "live-test.txt", "desktop-adapters-live.json", "desktop-flame.json"):
            (self.runtime / name).write_text("owned old state")
        for name in ("firmware-cache", "cli/deepseek-bundle"):
            directory = self.runtime / name
            directory.mkdir(parents=True)
            (directory / "cache.bin").write_bytes(b"old cache")
        lock = self.runtime / "desktop.lock"
        lock.write_text("current process lock")
        unknown = self.runtime / "my-notes.txt"
        unknown.write_text("user notes")
        outside = self.root / "external-config.json"
        outside.write_text('{"user": "settings"}')
        link = self.runtime / "unknown-link"
        link.symlink_to(outside)
        log_path = self.runtime / "desktop.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("old log\n")
            log.flush()
            result = self.hub.reset_app()
            self.assertEqual(log_path.read_text(), "")
            log.write("new log\n")
            log.flush()
            self.assertEqual(log_path.read_text(), "new log\n")
        self.assertEqual(result["events"], [])
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["devices"], [])
        self.assertFalse(result["hardware_paired"])
        self.assertFalse(result["maintenance"])
        self.assertEqual(result["hardware_model"], DEFAULT_MODEL)
        fresh = Engine(":memory:", sources=SOURCES)
        try:
            self.assertEqual(result["settings"], fresh.settings)
        finally:
            fresh.db.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            old_engine.db.execute("SELECT 1")
        self.assertNotEqual(self.hub.config["hub_id"], before["hub_id"])
        self.assertNotEqual(self.hub.config["dashboard"], before["dashboard"])
        self.assertEqual(set(self.hub.config["tokens"]), set(before["tokens"]))
        self.assertTrue(all(token != before["tokens"][source] for source, token in self.hub.config["tokens"].items()))
        saved = json.loads((self.runtime / "credentials.json").read_text())
        self.assertEqual(saved, self.hub.config)
        self.assertNotIn("old-pairing", json.dumps(saved))
        for name in ("codex-sequences.sqlite", "cli-lifecycle.sqlite", "cli-integrations.json",
                     "live-test.txt", "desktop-adapters-live.json", "desktop-flame.json", "firmware-cache", "cli/deepseek-bundle"):
            self.assertFalse((self.runtime / name).exists(), name)
        for base in ("codex-sequences.sqlite", "cli-lifecycle.sqlite"):
            for suffix in ("-wal", "-shm", "-journal"):
                self.assertFalse((self.runtime / (base + suffix)).exists())
        self.assertEqual(lock.read_text(), "current process lock")
        self.assertEqual(unknown.read_text(), "user notes")
        self.assertTrue(link.is_symlink())
        self.assertEqual(outside.read_text(), '{"user": "settings"}')
        with closing(sqlite3.connect(self.runtime / "events.sqlite")) as db:
            for table in ("events", "tasks", "self_tests", "deliveries"):
                self.assertEqual(db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)
        self.assertEqual(load_config(), saved)

    def seed_plug_credentials(self):
        from aiflame.devices.models import MIJIA_PLUG_MODEL
        self.hub.config["devices"].update({
            identity: {"hardware_model": MIJIA_PLUG_MODEL, "device_id": identity,
                       "miio_token_ref": "owned-test-vault", "connection_paused": True}
            for identity in ("miio:123", "miio:456")})

    def test_reset_removes_only_owned_vault_tokens_and_cloud_candidates(self):
        self.seed_plug_credentials()
        tokens = {("owned-test-vault", "miio:123"): "secret-1",
                  ("owned-test-vault", "miio:456"): "secret-2",
                  ("other-app", "unrelated"): "keep"}
        self.hub.devices.mijia.devices = {"123": {"token": "temporary-cloud-token"}}
        old_login = self.hub.devices.mijia
        from types import SimpleNamespace
        def vault(runtime, service):
            return SimpleNamespace(delete=lambda identity: tokens.pop((service, identity), None))
        with patch("aiflame.devices.token_store.TokenStore", side_effect=vault) as cleanup:
            self.hub.reset_app()
        self.assertEqual(cleanup.call_count, 2)
        self.assertEqual(tokens, {("other-app", "unrelated"): "keep"})
        self.assertEqual(old_login.devices, {})
        self.assertEqual(self.hub.config["devices"], {})
        self.assertNotIn("pending_device_token_cleanup", self.hub.config)

    def test_reset_removes_windows_ciphertext_files_including_orphans(self):
        from aiflame.devices.token_store import TokenStore
        from tests.test_mijia import TOKEN
        self.seed_plug_credentials()
        store = TokenStore(self.runtime, "owned-test-vault")
        with patch.object(store, "_dpapi", return_value=b"encrypted-only"):
            store._windows("set", "miio:123", TOKEN)
            store._windows("set", "orphan", TOKEN)
        self.assertNotIn(TOKEN, store._path("miio:123").read_text())
        with patch.object(TokenStore, "delete", lambda store, account: store._windows("delete", account)), patch.object(TokenStore, "_dpapi") as decrypt:
            self.hub.reset_app()
            decrypt.assert_not_called()
        self.assertFalse((self.runtime / "device-tokens").exists())
        self.assertFalse(list(self.runtime.glob(".reset-*")))

    def test_reset_removes_local_ciphertext_and_key_without_keychain(self):
        from aiflame.devices.token_store import TokenStore
        from tests.test_mijia import TOKEN
        self.seed_plug_credentials()
        store = TokenStore(self.runtime, "owned-test-vault")
        with patch("aiflame.devices.token_store.sys.platform", "darwin"), patch.object(TokenStore, "_call", side_effect=AssertionError("keychain accessed")):
            store.set("miio:123", TOKEN)
            store.set("miio:456", TOKEN)
            result = self.hub.reset_app()
        self.assertFalse(result.get("token_cleanup_pending"))
        self.assertFalse((self.runtime / "device-tokens").exists())
        self.assertFalse(list(self.runtime.glob(".reset-*")))

    def test_reset_preserves_failed_token_cleanup_references_for_retry(self):
        self.seed_plug_credentials()
        with patch("aiflame.devices.token_store.TokenStore.delete", side_effect=RuntimeError("vault denied")):
            result = self.hub.reset_app()
        self.assertTrue(result["token_cleanup_pending"])
        self.assertIn("Token 未清理", result["message"])
        expected = [{"service": "owned-test-vault", "device_id": identity} for identity in ("miio:123", "miio:456")]
        self.assertEqual(self.hub.config["pending_device_token_cleanup"], expected)
        self.assertEqual(json.loads((self.runtime / "credentials.json").read_text())["pending_device_token_cleanup"], expected)
        with patch("aiflame.devices.token_store.TokenStore.delete") as cleanup:
            result = self.hub.reset_app()
        self.assertEqual(cleanup.call_count, 2)
        self.assertNotIn("pending_device_token_cleanup", self.hub.config)
        self.assertNotIn("token_cleanup_pending", result)

    def test_owned_cache_symlink_is_removed_without_touching_its_target(self):
        external = self.root / "external-cache"
        external.mkdir()
        (external / "important.bin").write_bytes(b"keep")
        (self.runtime / "firmware-cache").symlink_to(external, target_is_directory=True)
        self.hub.reset_app()
        self.assertFalse((self.runtime / "firmware-cache").is_symlink())
        self.assertEqual((external / "important.bin").read_bytes(), b"keep")

    def test_symlink_parent_of_owned_nested_cache_does_not_remove_external_files(self):
        external = self.root / "external-cli"
        bundle = external / "deepseek-bundle"
        bundle.mkdir(parents=True)
        (bundle / "user-data.txt").write_text("keep external files")
        (self.runtime / "cli").symlink_to(external, target_is_directory=True)
        self.hub.reset_app()
        self.assertEqual((bundle / "user-data.txt").read_text(), "keep external files")
        self.assertTrue((self.runtime / "cli").is_symlink())

    def test_busy_maintenance_or_diagnostics_reject_without_changing_state(self):
        self.seed_history()
        before = deepcopy(self.hub.config)
        engine = self.hub.engine
        for service in (self.hub.devices, self.hub.tests):
            with self.subTest(service=type(service).__name__), patch.object(service, "busy", return_value=True):
                with self.assertRaises(EventError) as raised:
                    self.hub.reset_app()
                self.assertEqual(raised.exception.status, 409)
            self.assertIs(self.hub.engine, engine)
            self.assertEqual(self.hub.config, before)
            self.assertEqual(self.hub.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)

    def test_write_failure_restores_configuration_history_and_cache(self):
        self.seed_history()
        before = deepcopy(self.hub.config)
        self.hub.engine.update_settings({'language': 'en', 'duration': 8})
        rows = [dict(row) for row in self.hub.engine.db.execute('SELECT * FROM events')]
        cache = self.runtime / 'firmware-cache'
        cache.mkdir()
        (cache / 'image.bin').write_bytes(b'original firmware cache')
        with patch('aiflame.hub.reset.write_private', side_effect=OSError('cannot write')):
            with self.assertRaises(EventError) as raised:
                self.hub.reset_app()
        self.assertEqual(raised.exception.status, 500)
        self.assertFalse(self.hub.resetting)
        self.assertEqual(self.hub.config, before)
        self.assertEqual(self.hub.engine.settings['language'], 'en')
        self.assertEqual(self.hub.engine.settings['duration'], 8)
        self.assertEqual([dict(row) for row in self.hub.engine.db.execute('SELECT * FROM events')], rows)
        self.assertEqual((cache / 'image.bin').read_bytes(), b'original firmware cache')
        self.assertEqual(json.loads((self.runtime / 'credentials.json').read_text()), before)
        self.assertFalse(list(self.runtime.glob('.reset-*')))

    def test_unwritable_staging_does_not_stop_services_or_clear_data(self):
        self.seed_history()
        with patch('aiflame.hub.reset.tempfile.mkdtemp', side_effect=OSError('denied')), \
             patch.object(self.hub.transport, 'stop') as stop:
            with self.assertRaises(EventError):
                self.hub.reset_app()
            stop.assert_not_called()
        self.assertEqual(self.hub.engine.db.execute('SELECT COUNT(*) FROM events').fetchone()[0], 1)
        self.assertFalse(self.hub.resetting)

    def test_old_demo_completion_cannot_write_into_new_database(self):
        old_demo = self.hub.demos
        generation = old_demo.generation
        with patch("aiflame.integrations.manual.actions.threading.Thread"):
            task = old_demo.start({})["task_id"]
        self.hub.reset_app()
        with patch.object(self.hub.stopping, "wait", return_value=False):
            old_demo._finish(task, "done", generation)
        self.assertEqual(self.hub.engine.db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)


class ResetHttpTests(ResetFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.hub.start(hardware=False)
        self.client = DashboardClient(f"http://127.0.0.1:{self.hub.port}")

    def test_http_reset_requires_dashboard_origin_and_exact_confirmation(self):
        old_config = deepcopy(self.hub.config)
        self.assertEqual(self.client.request("/api/reset", {"confirm": True}, authenticated=False)[0], 401)
        self.assertEqual(self.client.request("/api/reset", {"confirm": True}, authenticated=False,
                                           headers={"Authorization": "Bearer " + old_config["tokens"]["local-script"]})[0], 403)
        self.assertEqual(self.client.request("/api/reset", {"confirm": True},
                                           headers={"Origin": "http://unrelated.example"})[0], 403)
        for body in ({}, {"confirm": False}, {"confirm": 1}, {"confirm": "true"}, {"confirm": True, "extra": 1}):
            with self.subTest(body=body):
                self.assertEqual(self.client.request("/api/reset", body)[0], 400)
        self.assertEqual(self.hub.config, old_config)

    def test_success_rotates_session_and_credentials_without_restarting_http(self):
        self.seed_history()
        old = deepcopy(self.hub.config)
        server, thread, port = self.hub.server, self.hub.thread, self.hub.port
        request = urllib.request.Request(self.client.base + "/api/reset", data=b'{"confirm":true}',
                                         headers={"Content-Type": "application/json"})
        with self.client.opener.open(request, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn("aiflame=" + self.hub.config["dashboard"], response.headers["Set-Cookie"])
            self.assertEqual(json.load(response)["events"], [])
        self.assertIs(self.hub.server, server)
        self.assertIs(self.hub.thread, thread)
        self.assertEqual(self.hub.port, port)
        self.assertTrue(thread.is_alive())
        self.assertEqual(self.client.call("/api/status")["devices"], [])
        self.assertEqual(self.client.request("/api/status", authenticated=False,
                                           headers={"Cookie": "aiflame=" + old["dashboard"]})[0], 401)
        self.assertEqual(self.client.request("/v1/status", authenticated=False,
                                           headers={"Authorization": "Bearer " + old["tokens"]["local-script"]})[0], 401)
        self.assertEqual(self.client.request("/v1/status", authenticated=False,
                                           headers={"Authorization": "Bearer " + self.hub.config["tokens"]["local-script"]})[0], 200)

    def test_reset_rejects_active_posts_and_blocks_new_mutations_until_finished(self):
        original = deepcopy(self.hub.config)
        with self.hub.request_guard:
            self.hub.active_posts = 1
        try:
            self.assertEqual(self.client.request("/api/reset", {"confirm": True})[0], 409)
            self.assertEqual(self.hub.config, original)
        finally:
            with self.hub.request_guard:
                self.hub.active_posts = 0
        entered, release = threading.Event(), threading.Event()
        result, errors = [], []

        def stop_monitor():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("test failed to release reset")

        def reset():
            try:
                result.append(self.client.request("/api/reset", {"confirm": True}))
            except Exception as error:
                errors.append(error)

        with patch.object(self.hub.transport, "stop", side_effect=stop_monitor):
            thread = threading.Thread(target=reset)
            thread.start()
            try:
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.client.request("/api/settings", {"duration": 45})[0], 503)
            finally:
                release.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result[0][0], 200)
        self.assertEqual(self.client.call("/api/status")["settings"]["duration"], 10)
