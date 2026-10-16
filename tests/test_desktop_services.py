import json
import os
from pathlib import Path
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
from aiflame.core.paths import load_config, user_data_dir, runtime_dir, write_private
from aiflame.hub.server import Hub
from aiflame.integrations.chatgpt_web.install import manifest_path
from aiflame.integrations.codex.install import install


class PathsAndInstallTests(unittest.TestCase):
    def setUp(self):
        runtime = tempfile.TemporaryDirectory()
        self.addCleanup(runtime.cleanup)
        environment = patch.dict(os.environ, {"AIFLAME_DATA_DIR": runtime.name})
        environment.start()
        self.addCleanup(environment.stop)

    def test_upgrade_removes_retired_credentials_and_enables_background_sources(self):
        from aiflame.core.engine import Engine
        from aiflame.core.events import make_event
        from aiflame.integrations.registry import BACKGROUND_SOURCES, SOURCES

        config = load_config()
        local_token = config['tokens']['local-script']
        config['tokens']['chatgpt-desktop'] = 'retired-token'
        write_private(runtime_dir() / 'credentials.json', config)
        engine = Engine(runtime_dir() / 'events.sqlite', sources=SOURCES)
        settings = {**engine.settings, 'duration': 27, 'source_enabled': {
            **engine.settings['source_enabled'], 'codex': False,
            **dict.fromkeys(BACKGROUND_SOURCES, False), 'chatgpt-desktop': True}}
        engine.db.execute("INSERT OR REPLACE INTO settings VALUES ('main', ?)", (json.dumps(settings),))
        engine.db.commit()
        engine.db.close()
        hub = Hub(port=0)
        try:
            self.assertNotIn('chatgpt-desktop', hub.config['tokens'])
            self.assertNotIn('chatgpt-desktop', hub.engine.settings['source_enabled'])
            self.assertFalse(hasattr(hub, 'desktop_clients'))
            self.assertEqual(hub.config['tokens']['local-script'], local_token)
            self.assertEqual(hub.engine.settings['duration'], 27)
            self.assertFalse(hub.engine.settings['source_enabled']['codex'])
            for source in BACKGROUND_SOURCES:
                self.assertTrue(hub.engine.settings['source_enabled'][source])
                result, status = hub.engine.accept(make_event(source, source, 'working'), source)
                self.assertEqual(status, 202)
                self.assertNotEqual(result['result'], 'unmatched')
                self.assertIn((source, source), hub.engine.active)
        finally:
            hub.stop()

    def test_malformed_hook_groups_are_rejected_without_rewriting_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            path = home / "hooks.json"
            for group in ({"hooks": "invalid"}, {"hooks": [None]}, "invalid"):
                original = {"hooks": {"Stop": [group]}}
                write_private(path, original)
                with self.assertRaises(ValueError):
                    install(home)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_hook_path_migration_and_uninstall_preserve_unrelated_handlers(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            old = [
                "/Applications/Old/AI Flame.app/Contents/MacOS/AI Flame",
                "--codex-hook",
            ]
            new = [
                "/Applications/New/SeeEnd.app/Contents/MacOS/SeeEnd",
                "--codex-hook",
            ]
            with patch("aiflame.integrations.codex.install.command", return_value=old):
                install(home)
            path = home / "hooks.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            # Migrate an earlier unmarked command in a mixed group as well.
            data["hooks"]["Stop"][0]["hooks"][0].pop("statusMessage")
            unrelated = {"type": "command", "command": "other-app", "enabled": False}
            data["hooks"]["Stop"][0]["hooks"].append(unrelated)
            data["hooks"]["Stop"][0]["matcher"] = "keep-me"
            write_private(path, data)
            with patch("aiflame.integrations.codex.install.command", return_value=new):
                install(home)
                install(home)
                migrated = json.loads(path.read_text(encoding="utf-8"))["hooks"]
                for event, groups in migrated.items():
                    handlers = [h for group in groups for h in group["hooks"]]
                    self.assertEqual(
                        sum(h["command"] != "other-app" for h in handlers), 1
                    )
                group = migrated["Stop"][0]
                self.assertEqual(group["matcher"], "keep-me")
                self.assertIn(unrelated, group["hooks"])
                self.assertEqual(len(group["hooks"]), 2)
                owned = next(handler for handler in group["hooks"] if handler != unrelated)
                self.assertIn(new[0], owned["command"])
                install(home, uninstall=True)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["hooks"],
                {
                    "Stop": [{"hooks": [unrelated], "matcher": "keep-me"}],
                },
            )

    def test_legacy_commands_are_owned_without_matching_other_apps(self):
        from aiflame.integrations.codex.registration import owns_hook

        wanted = "current-app --codex-hook"
        for command in (
            "python -m aiflame --codex-hook",
            "python '/tmp/AI Flame/app.py' --codex-hook",
            "python '/tmp/AI Flame/prototype/codex_hook.py'",
            '"C:\\Apps\\AI Flame Helper.exe" --codex-hook',
        ):
            self.assertTrue(
                owns_hook({"type": "command", "command": command}, wanted), command
            )
        for command in (
            "python /tmp/another/app.py --codex-hook",
            "python /tmp/another/prototype/codex_hook.py",
            "other-app --codex-hook",
            "python -m another --codex-hook",
            "'/tmp/AI Flame' --codex-hook && other-app",
        ):
            self.assertFalse(
                owns_hook({"type": "command", "command": command}, wanted), command
            )

    def test_three_os_data_and_browser_locations(self):
        self.assertEqual(
            user_data_dir("darwin", "/home/test", {}).as_posix(),
            "/home/test/Library/Application Support/AI Flame",
        )
        self.assertEqual(
            user_data_dir("linux", "/home/test", {"XDG_DATA_HOME": "/data"}).as_posix(),
            "/data/ai-flame",
        )
        self.assertEqual(
            user_data_dir("win32", "/home/test", {"LOCALAPPDATA": "/local"}).as_posix(),
            "/local/AI Flame",
        )
        self.assertIn(
            "Google/Chrome/NativeMessagingHosts",
            manifest_path("chrome", "darwin", "/home/test", {}).as_posix(),
        )
        self.assertIn(
            "google-chrome/NativeMessagingHosts",
            manifest_path("chrome", "linux", "/home/test", {}).as_posix(),
        )
        self.assertIn(
            "BrowserBridge/chrome",
            manifest_path("chrome", "win32", "/home/test", {}).as_posix(),
        )

    def test_source_and_app_use_system_data_directory(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "aiflame.core.paths.user_data_dir", return_value=Path("/data/ai-flame")
            ),
        ):
            self.assertEqual(runtime_dir(), Path("/data/ai-flame"))

    def test_linux_ignores_empty_and_relative_data_home(self):
        for value in ("", "relative/data", "~/data"):
            with self.subTest(value=value):
                self.assertEqual(
                    user_data_dir("linux", "/home/test", {"XDG_DATA_HOME": value}),
                    Path("/home/test/.local/share/ai-flame"),
                )

    def test_hook_install_idempotent_preserves_unrelated_and_uninstall(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            original = {
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": "other-app"}]}]
                }
            }
            write_private(home / "hooks.json", original)
            install(home)
            install(home)
            data = json.loads((home / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            install(home, uninstall=True)
            self.assertEqual(json.loads((home / "hooks.json").read_text(encoding="utf-8")), original)






class HTTPTests(unittest.TestCase):
    def test_mode_api_blocks_virtual_hardware_access_and_exposes_saved_mode(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/device", {"action": "connect", "transport": "yeelight"})
        self.assertEqual(error.exception.code, 409)
        error.exception.close()
        with self.request("/api/settings", {"hardware_notifications": True}) as response:
            self.assertEqual(json.load(response)["settings"]["hardware_notifications"], True)
        for path in ("/api/status", "/api/indicator"):
            with self.request(path) as response:
                self.assertEqual(json.load(response)["settings"]["hardware_notifications"], True)
        with self.request("/api/settings", {"hardware_notifications": False}) as response:
            self.assertEqual(json.load(response)["settings"]["hardware_notifications"], False)

    def test_verification_requires_dashboard_and_real_source_events(self):
        from aiflame.core.events import make_event

        for headers, code in (
            ({"Cookie": ""}, 401),
            ({"Cookie": "", "Authorization": "Bearer api-secret"}, 403),
        ):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/verification", {"source": "local-script"}, headers)
            self.assertEqual(error.exception.code, code)
            error.exception.close()
        with self.request("/api/verification", {"source": "local-script"}) as response:
            self.assertEqual(json.load(response)["events"], [])
        for seq, state in ((1, "working"), (2, "done")):
            with self.request(
                "/v1/events",
                make_event("local-script", "http-trial", state, seq),
                {"Cookie": "", "Authorization": "Bearer api-secret"},
            ) as response:
                self.assertEqual(response.status, 202)
        with self.request("/api/status") as response:
            result = json.load(response)
        self.assertFalse(result["hardware_paired"])
        self.assertFalse(result["device"]["online"])
        self.assertIsNone(result["last_ack"])
        self.assertEqual(result["verification"]["local-script"]["status"], "verified")
        self.assertEqual(result["verification"]["local-script"]["delivery"], "screen")

    def test_web_bridge_to_http_preserves_each_platform_identity(self):
        from aiflame.integrations.chatgpt_web import host
        from aiflame.core.events import make_event

        root = Path(self.temp.name)
        tokens = {source: "test-" + source for source in host.WEB_SOURCES}
        with self.hub.lock:
            self.hub.config["tokens"].update(tokens)
        (root / "bridge-config.json").write_text(json.dumps({"tokens": tokens}))
        original_request = urllib.request.Request

        def local_request(url, **kwargs):
            return original_request(
                url.replace(":8765/", ":" + str(self.hub.port) + "/"), **kwargs
            )

        with (
            patch.object(host, "ROOT", root),
            patch.object(host.urllib.request, "Request", side_effect=local_request),
        ):
            for source in host.WEB_SOURCES:
                for seq, state in ((1, "working"), (2, "done")):
                    event = make_event(
                        source, "00000000-0000-4000-8000-000000000001", state, seq
                    )
                    self.assertTrue(host.forward(event)["ok"])
                    with self.hub.lock:
                        row = self.hub.engine.db.execute(
                            "SELECT source,state FROM events WHERE id=?",
                            (event["event_id"],),
                        ).fetchone()
                    self.assertEqual((row["source"], row["state"]), (source, state))
                self.assertTrue(
                    host.forward({"kind": "ping", "source_id": source})[
                        "source_enabled"
                    ]
                )
            with self.hub.lock:
                self.hub.engine.update_settings(
                    {
                        "source_enabled": {
                            **self.hub.engine.settings["source_enabled"],
                            "kimi-web": False,
                        }
                    }
                )
            self.assertFalse(
                host.forward({"kind": "ping", "source_id": "kimi-web"})[
                    "source_enabled"
                ]
            )
            self.assertTrue(
                host.forward({"kind": "ping", "source_id": "deepseek-web"})[
                    "source_enabled"
                ]
            )

    def test_each_web_source_uses_shared_installer_and_rejects_malformed_source(self):
        from aiflame.integrations.chatgpt_web.host import WEB_SOURCES

        with patch("aiflame.integrations.chatgpt_web.install.install") as installer:
            for source in WEB_SOURCES:
                with self.request(
                    "/api/integrations/install", {"source": source}
                ) as response:
                    self.assertEqual(response.status, 200)
            self.assertEqual(installer.call_count, len(WEB_SOURCES))
            for source in ([], {}, "unknown-web"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request("/api/integrations/install", {"source": source})
                self.assertEqual(error.exception.code, 400)
                error.exception.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = {
            "hub_id": "hub",
            "device_id": "",
            "port": "",
            "dashboard": "cookie-secret",
            "tokens": {
                "local-script": "api-secret",
                "codex": "codex-secret",
                "chatgpt-web": "web-secret",
            },
        }
        self.hub = Hub(port=0, config=config, runtime=Path(self.temp.name))
        self.hub.start(hardware=False)
        self.addCleanup(self.hub.stop)
        self.base = "http://127.0.0.1:" + str(self.hub.port)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None, headers=None):
        h = {"Cookie": "aiflame=cookie-secret"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if headers:
            h.update(headers)
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers=h,
        )
        return self.opener.open(request, timeout=2)


    def test_extension_folder_preparation_is_console_only_and_cannot_choose_path(self):
        with self.request("/api/browser-extension/prepare", {}) as response:
            result = json.load(response)
        self.assertEqual(Path(result["path"]), Path(self.temp.name) / "SeeEnd" / "BrowserExtension")
        self.assertTrue((Path(result["path"]) / "manifest.json").is_file())
        for body, headers, status in (({"path": "/tmp/other"}, None, 400),
                                      ({}, {"Cookie": "", "Authorization": "Bearer web-secret"}, 403)):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/browser-extension/prepare", body, headers)
            self.assertEqual(error.exception.code, status)
            error.exception.close()
        with self.request("/api/browser-extension") as response:
            self.assertTrue(json.load(response)["prepared"])

    def test_web_console_routes_are_removed_and_desktop_session_is_json(self):
        for path in ("/", "/index.html", "/app.js", "/style.css", "/devices.js",
                     "/shared.js", "/i18n.js", "/locales/en.json",
                     "/brands/codex.png", "/device-icons/yeelight-yldp005.svg"):
            for cookie in ("", "aiflame=cookie-secret"):
                with self.subTest(path=path, cookie=bool(cookie)):
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        self.request(path, headers={"Cookie": cookie})
                    self.assertEqual(error.exception.code, 404)
                    self.assertNotIn("Set-Cookie", error.exception.headers)
                    error.exception.close()
        with self.request("/api/session", headers={"Cookie": ""}) as response:
            self.assertEqual(response.headers.get_content_type(), "application/json")
            self.assertEqual(json.load(response)["app_name"], "SeeEnd")
            self.assertIn("Set-Cookie", response.headers)

        for path, body in (("/api/desktop-clients", None),
                           ("/api/desktop-clients/permissions", {})):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, body)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()

    def test_extension_download_excludes_credentials(self):
        with self.request("/api/browser-extension/download") as response:
            body = response.read()
            self.assertNotIn(b"cookie-secret", body)
            self.assertNotIn(b"api-secret", body)
            self.assertEqual(response.status, 200)

    def test_device_mutations_require_dashboard_and_same_origin(self):
        for headers in (
            {"Cookie": "", "Authorization": "Bearer api-secret"},
            {"Origin": "https://evil.test"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/device", {"action": "probe", "port": "x"}, headers)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()

    def test_integration_install_requires_dashboard_and_passes_resolved_directory(self):
        directory = Path(self.temp.name) / "codex-config"
        with (
            patch("aiflame.integrations.codex.install.install") as installer,
            patch("aiflame.integrations.cli_tools.guidance.directory_for", return_value=directory) as resolve_directory,
        ):
            with self.request(
                "/api/integrations/install", {"source": "codex"}
            ) as response:
                self.assertIn("Hooks", json.load(response)["message"])
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(
                    "/api/integrations/install",
                    {"source": "codex"},
                    {"Cookie": "", "Authorization": "Bearer api-secret"},
                )
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
            resolve_directory.assert_called_once_with("codex", None)
            installer.assert_called_once_with(home=directory, uninstall=False)

    def test_credentials_cannot_be_read_from_status(self):
        for path in ("/api/status", "/api/device", "/api/trigger-test"):
            with self.request(path) as response:
                text = response.read().decode()
                self.assertNotIn("secret", text)

    def test_incorrect_origin_cannot_bootstrap_cookie(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/", headers={"Origin": "https://evil.test"})
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

    def test_api_source_cannot_impersonate_another_trigger(self):
        from aiflame.core.events import make_event

        event = make_event("codex", "task", "working")
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(
                "/v1/events",
                event,
                {"Cookie": "", "Authorization": "Bearer api-secret"},
            )
        error.exception.close()


if __name__ == "__main__":
    unittest.main()
