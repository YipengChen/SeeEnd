import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest.mock import patch, Mock
from aiflame.core.events import EventError
from aiflame.core.paths import user_data_dir, runtime_dir, write_private
from aiflame.devices.service import DeviceService
from aiflame.hub.server import Hub
from aiflame.integrations.chatgpt_web.install import manifest_path
from aiflame.integrations.codex.install import install


class PathsAndInstallTests(unittest.TestCase):
    def test_malformed_hook_groups_are_rejected_without_rewriting_configuration(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            path = home / "hooks.json"
            for group in ({"hooks": "invalid"}, {"hooks": [None]}, "invalid"):
                original = {"hooks": {"Stop": [group]}}
                write_private(path, original)
                with self.assertRaises(ValueError):
                    install(home)
                self.assertEqual(json.loads(path.read_text()), original)

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
            data = json.loads(path.read_text())
            # Migrate an earlier unmarked command in a mixed group as well.
            data["hooks"]["Stop"][0]["hooks"][0].pop("statusMessage")
            unrelated = {"type": "command", "command": "other-app", "enabled": False}
            data["hooks"]["Stop"][0]["hooks"].append(unrelated)
            data["hooks"]["Stop"][0]["matcher"] = "keep-me"
            write_private(path, data)
            with patch("aiflame.integrations.codex.install.command", return_value=new):
                install(home)
                install(home)
                migrated = json.loads(path.read_text())["hooks"]
                for event, groups in migrated.items():
                    handlers = [h for group in groups for h in group["hooks"]]
                    self.assertEqual(
                        sum(h["command"] != "other-app" for h in handlers), 1
                    )
                self.assertEqual(
                    migrated["Stop"][0], {"hooks": [unrelated], "matcher": "keep-me"}
                )
                install(home, uninstall=True)
            self.assertEqual(
                json.loads(path.read_text())["hooks"],
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
            data = json.loads((home / "hooks.json").read_text())
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            install(home, uninstall=True)
            self.assertEqual(json.loads((home / "hooks.json").read_text()), original)


class BundledFirmwareTests(unittest.TestCase):
    def test_real_bundled_image_matches_manifest_and_esp32s3_header(self):
        from aiflame.devices import base_firmware

        image = base_firmware.bundled_image()
        manifest = json.loads((base_firmware.DIRECTORY / "manifest.json").read_text())
        payload = image.read_bytes()
        self.assertEqual(image.name, manifest["file"])
        self.assertGreater(len(payload), 1024)
        self.assertEqual(payload[0], 0xE9)
        self.assertEqual(int.from_bytes(payload[12:14], "little"), 9)
        self.assertEqual(hashlib.sha256(payload).hexdigest(), manifest["sha256"])

    def test_invalid_bundled_images_are_rejected_even_with_matching_hash(self):
        from aiflame.devices import base_firmware

        valid = bytearray(2048)
        valid[0], valid[12] = 0xE9, 9
        wrong_magic = bytearray(valid)
        wrong_magic[0] = 0
        wrong_chip = bytearray(valid)
        wrong_chip[12] = 0
        for payload in (b"", bytes(valid[:512]), bytes(wrong_magic), bytes(wrong_chip)):
            with self.subTest(size=len(payload), header=payload[:14]):
                with tempfile.TemporaryDirectory() as root:
                    directory = Path(root)
                    (directory / "firmware.bin").write_bytes(payload)
                    (directory / "manifest.json").write_text(json.dumps({
                        "file": "firmware.bin",
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }))
                    with patch.object(base_firmware, "DIRECTORY", directory):
                        with self.assertRaises(EventError):
                            base_firmware.bundled_image()


class DeviceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.transport = Mock()
        self.transport.engine.settings = {"mode": "hardware"}
        self.transport.engine.device = {"online": False, "device_id": "paired"}
        self.config = {
            "device_id": "paired",
            "transport": "wifi",
            "wifi_device_secret": "abc",
        }
        self.service = DeviceService(
            self.config, self.temp.name, self.transport, threading.RLock()
        )
        self.ports = patch.object(
            self.service, "ports", return_value=[{"port": "COM5"}]
        )
        self.ports.start()
        self.addCleanup(self.ports.stop)

    def wait(self):
        end = time.monotonic() + 3
        while self.service.busy() and time.monotonic() < end:
            time.sleep(0.01)
        self.assertFalse(self.service.busy())

    def test_disconnect_preserves_pairing_and_connect_resumes(self):
        self.service.start({"action": "disconnect"})
        self.wait()
        self.assertTrue(self.config["connection_paused"])
        self.assertEqual(self.config["device_id"], "paired")
        self.assertEqual(self.config["wifi_device_secret"], "abc")
        self.transport.start.assert_not_called()
        self.assertEqual(self.transport.engine.device["status"], "已主动断开")
        self.service.start({"action": "connect", "transport": "wifi"})
        self.wait()
        self.assertFalse(self.config["connection_paused"])
        self.transport.start.assert_called_once()

    def test_network_name_distinguishes_report_from_cache(self):
        self.config.update(wifi_ssid="Old network", wifi_ssid_device_id="paired")
        self.transport.engine.device.update(online=True, wifi_ssid="Device network", wifi_connected=True)
        data = self.service.status()
        self.assertEqual(data["wifi_ssid"], "Device network")
        self.assertTrue(data["wifi_info_current"])
        self.assertNotIn("wifi_device_secret", data)
        self.transport.engine.device["online"] = False
        self.assertFalse(self.service.status()["wifi_info_current"])
        self.transport.engine.device = {"online": True, "device_id": "paired"}
        self.assertEqual(self.service.status()["wifi_ssid"], "Old network")
        self.assertFalse(self.service.status()["wifi_info_current"])
        self.config["device_id"] = "different"
        self.assertFalse(self.service.status()["wifi_info_known"])

    def test_reject_arbitrary_serial_path_and_invalid_wifi(self):
        for value in (
            {"action": "probe", "port": "/etc/passwd"},
            {"action": "provision", "port": "COM5", "ssid": "abc", "password": "short"},
            {"action": "provision", "port": "COM5", "ssid": "abc", "password": ""},
        ):
            with self.assertRaises(EventError):
                self.service.start(value)
        self.transport.stop.assert_not_called()

    def test_wifi_address_retained_when_broadcast_is_blocked(self):
        with patch.object(
            self.service,
            "mp",
            return_value='boot\n{"connected":true,"ip":"192.168.1.20"}\n',
        ):
            self.assertEqual(self.service.wifi_address("COM5"), "192.168.1.20")
        with patch.object(
            self.service, "mp", return_value='{"connected":false,"ip":"0.0.0.0"}'
        ):
            with self.assertRaises(RuntimeError):
                self.service.wifi_address("COM5")

    def test_flash_requires_image_type_and_explicit_confirmation(self):
        with self.assertRaises(EventError):
            self.service.stage_image(b"x" * 1024)
        valid = bytearray(2048)
        valid[0] = 0xE9
        valid[12] = 9
        image = self.service.stage_image(bytes(valid))
        with self.assertRaises(EventError):
            self.service.start(
                {"action": "flash_base", "port": "COM5", "image_id": image["image_id"]}
            )

    def test_bundled_flash_requires_both_confirmations_before_touching_device(self):
        for confirmations in (
            {},
            {"confirm_flash": True},
            {"confirm_board": True},
            {"confirm_flash": True, "confirm_board": False},
        ):
            with self.subTest(confirmations=confirmations):
                with self.assertRaises(EventError):
                    self.service.start(
                        {"action": "flash_base", "port": "COM5", **confirmations}
                    )
        self.transport.stop.assert_not_called()

    def test_bundled_flash_writes_verified_image_at_zero(self):
        from aiflame.devices.base_firmware import bundled_image

        self.config["wifi_last_ip"] = "192.168.1.20"
        image = bundled_image()
        with patch(
            "aiflame.devices.service.run", return_value=Mock(returncode=0)
        ) as run:
            self.service.start(
                {
                    "action": "flash_base",
                    "port": "COM5",
                    "confirm_flash": True,
                    "confirm_board": True,
                }
            )
            self.wait()
        self.assertEqual(self.service.status()["job"]["state"], "succeeded")
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args, (
            "--esptool", "--chip", "esp32s3", "--port", "COM5",
            "erase-flash",
        ))
        self.assertEqual(run.call_args_list[1].args, (
            "--esptool", "--chip", "esp32s3", "--port", "COM5",
            "--baud", "460800", "write-flash", "0x0", str(image),
        ))
        self.transport.start.assert_called_once()
        self.assertEqual(self.config["device_id"], "")
        self.assertEqual(self.config["transport"], "usb")
        self.assertEqual(self.config["port"], "COM5")
        self.assertNotIn("wifi_device_secret", self.config)
        self.assertNotIn("wifi_last_ip", self.config)
        saved = json.loads((Path(self.temp.name) / "credentials.json").read_text())
        self.assertEqual(saved, self.config)

    def test_bundled_erase_failure_prevents_write_and_restores_transport(self):
        self.transport.worker = None
        with patch(
            "aiflame.devices.service.run", return_value=Mock(returncode=1)
        ) as run:
            self.service.start({
                "action": "flash_base", "port": "COM5",
                "confirm_flash": True, "confirm_board": True,
            })
            self.wait()
        self.assertEqual(self.service.status()["job"]["state"], "failed")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[-1], "erase-flash")
        self.transport.start.assert_called_once()
        self.assertEqual(self.config["device_id"], "paired")
        self.assertEqual(self.config["transport"], "wifi")
        self.assertEqual(self.config["wifi_device_secret"], "abc")

    def test_custom_flash_keeps_upload_flow_without_bundled_board_confirmation(self):
        valid = bytearray(2048)
        valid[0], valid[12] = 0xE9, 9
        staged = self.service.stage_image(bytes(valid))
        with patch(
            "aiflame.devices.service.run", return_value=Mock(returncode=0)
        ) as run:
            self.service.start(
                {
                    "action": "flash_base", "port": "COM5",
                    "image_id": staged["image_id"], "confirm_flash": True,
                }
            )
            self.wait()
        self.assertEqual(self.service.status()["job"]["state"], "succeeded")
        run.assert_called_once()
        self.assertEqual(run.call_args.args[-3:-1], ("write-flash", "0x0"))

    def test_corrupted_bundled_image_is_rejected_before_usb_operation(self):
        from aiflame.devices import base_firmware

        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            image = bytearray(2048)
            image[0], image[12] = 0xE9, 9
            (directory / "firmware.bin").write_bytes(image)
            (directory / "manifest.json").write_text(json.dumps({
                "file": "firmware.bin", "sha256": "0" * 64,
            }))
            with patch.object(base_firmware, "DIRECTORY", directory):
                with self.assertRaises(EventError):
                    self.service.start({
                        "action": "flash_base", "port": "COM5",
                        "confirm_flash": True, "confirm_board": True,
                    })
        self.transport.stop.assert_not_called()

    def test_failure_restores_transport_and_redacts_tool_output(self):
        result = Mock(
            returncode=1, stdout="SSID SECRET PASSWORD", stderr="SSID SECRET PASSWORD"
        )
        self.transport.worker = None
        with patch("aiflame.devices.service.run", return_value=result):
            self.service.start({"action": "probe", "port": "COM5"})
            self.wait()
        status = self.service.status()
        self.assertEqual(status["job"]["state"], "failed")
        self.assertNotIn("SECRET", json.dumps(status))
        self.transport.start.assert_called_once()

    def test_concurrent_usb_jobs_are_rejected(self):
        entered, release = threading.Event(), threading.Event()

        def probe(port):
            entered.set()
            release.wait(2)
            return {"device_id": "id", "board": "ESP32S3"}

        with (
            patch.object(self.service, "probe", side_effect=probe),
            patch.object(self.service, "mp"),
        ):
            self.service.start({"action": "probe", "port": "COM5"})
            self.assertTrue(entered.wait(1))
            with self.assertRaises(EventError):
                self.service.start({"action": "probe", "port": "COM5"})
            release.set()
            self.wait()
        self.transport.stop.assert_called_once()
        self.transport.start.assert_called_once()

    def test_missing_program_refuses_wifi_without_writing_files(self):
        with patch.object(self.service, "probe", return_value={"device_id": "paired", "board": "ESP32S3"}), patch.object(self.service, "mp", return_value="{}") as mp:
            self.service.start({"action": "provision", "port": "COM5", "ssid": "new", "password": "password"})
            self.wait()
        job = self.service.status()["job"]
        self.assertEqual(job["state"], "failed")
        self.assertIn("先", job["message"])
        self.assertFalse(any("fs" in call.args for call in mp.call_args_list))

    def test_failed_new_network_keeps_usb_control(self):
        self.transport.worker = None
        with patch.object(self.service, "probe", return_value={"device_id": "paired", "board": "ESP32S3"}), patch.object(self.service, "installed_secret", return_value="a" * 64), patch.object(self.service, "mp"), patch.object(self.service, "wifi_address", side_effect=RuntimeError("Wi-Fi 尚未连通")):
            self.service.start({"action": "provision", "port": "COM5", "ssid": "new", "password": "password"})
            self.wait()
        self.assertEqual(self.config["transport"], "usb")
        self.assertEqual(self.config["wifi_ssid"], "new")
        self.assertEqual(self.service.status()["job"]["state"], "failed")
        self.transport.start.assert_called_once()

    def test_wifi_verification_reconnects_instead_of_accepting_previous_network(self):
        with patch.object(self.service, "mp", return_value='{"connected":true,"ip":"192.168.1.20"}') as mp:
            self.service.wifi_address("COM5")
        code = mp.call_args.args[2]
        self.assertLess(code.index("w.active(False)"), code.index("w.connect("))
        self.assertNotIn("if not w.isconnected(): w.connect", code)

    def test_install_preserves_network_configuration(self):
        with patch.object(self.service, "probe", return_value={"device_id": "paired", "board": "ESP32S3"}), patch.object(self.service, "mp") as mp:
            self.service.start({"action": "install", "port": "COM5"})
            self.wait()
        self.assertEqual(self.service.status()["job"]["state"], "succeeded")
        uploads = [call.args[i + 2] for call in mp.call_args_list for i, value in enumerate(call.args) if value == "cp"]
        self.assertIn(":main.py.new", uploads)
        self.assertNotIn(":wifi.json.new", uploads)

    def test_provision_only_writes_private_network_file_without_reinstalling(self):
        seen = []

        def mp(port, *args, **kwargs):
            seen.append(args)
            self.assertNotIn("PrivatePassword", repr(args))
            if "fs" in args:
                for i, arg in enumerate(args):
                    if arg == "cp":
                        path = Path(args[i + 1])
                        if path.name == "wifi.json":
                            self.assertEqual(
                                json.loads(path.read_text())["password"],
                                "PrivatePassword",
                            )
                            if os.name != "nt":
                                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            return ""

        with (
            patch.object(
                self.service,
                "probe",
                return_value={"device_id": "paired", "board": "ESP32S3"},
            ),
            patch.object(self.service, "mp", side_effect=mp),
            patch.object(self.service, "installed_secret", return_value="a" * 64),
            patch.object(self.service, "wifi_address", return_value="192.168.1.9"),
        ):
            self.service.start(
                {
                    "action": "provision",
                    "port": "COM5",
                    "ssid": "WiFi",
                    "password": "PrivatePassword",
                }
            )
            self.wait()
        self.assertEqual(self.service.status()["job"]["state"], "succeeded")
        self.assertNotIn(
            "PrivatePassword", (Path(self.temp.name) / "credentials.json").read_text()
        )
        self.assertNotIn("PrivatePassword", repr(self.service.status()))
        self.assertEqual(self.config["transport"], "wifi")
        uploads = [args[i + 2] for args in seen for i, arg in enumerate(args) if arg == "cp"]
        self.assertEqual(uploads, [":wifi.json.new"])
        self.assertNotIn("a" * 64, repr(self.service.status()))


class HTTPTests(unittest.TestCase):
    def test_mode_api_blocks_virtual_hardware_access_and_exposes_saved_mode(self):
        with patch("aiflame.devices.service.list_ports.comports") as ports:
            with self.request("/api/ports") as response:
                self.assertEqual(json.load(response)["ports"], [])
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/api/device", {"action": "connect", "transport": "usb"})
            self.assertEqual(error.exception.code, 409)
            error.exception.close()
            ports.assert_not_called()
        with self.request("/api/settings", {"mode": "hardware"}) as response:
            self.assertEqual(json.load(response)["settings"]["mode"], "hardware")
        for path in ("/api/status", "/api/indicator"):
            with self.request(path) as response:
                self.assertEqual(json.load(response)["settings"]["mode"], "hardware")
        with self.request("/api/settings", {"mode": "virtual"}) as response:
            self.assertEqual(json.load(response)["settings"]["mode"], "virtual")

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

    def test_wifi_discovery_is_async_and_accepts_no_extra_fields(self):
        with self.request("/api/wifi-networks") as response:
            self.assertEqual(json.load(response)["state"], "idle")
        with patch.object(self.hub.wifi_scan, "start", return_value={"state": "running", "networks": [], "message": "搜索中"}) as start:
            with self.request("/api/wifi-networks", {}) as response:
                self.assertEqual(response.status, 202)
                self.assertEqual(json.load(response)["state"], "running")
            start.assert_called_once()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/wifi-networks", {"ssid": "unexpected"})
        self.assertEqual(error.exception.code, 400)

    def test_assets_and_download_exclude_credentials(self):
        for path in (
            "/",
            "/style.css",
            *[f"/brands/{brand}.png" for brand in ("codex", "chatgpt", "deepseek", "qianwen", "doubao", "yuanbao", "kimi", "zhipu")],
            "/app.js",
            "/devices.js",
            "/shared.js",
            "/api/firmware/download",
            "/api/browser-extension/download",
        ):
            with self.request(path) as response:
                body = response.read()
                self.assertNotIn(b"cookie-secret", body)
                self.assertNotIn(b"api-secret", body)
                self.assertEqual(response.status, 200)

    def test_firmware_download_includes_verified_base_and_no_pairing_data(self):
        from aiflame.devices import base_firmware

        image = base_firmware.bundled_image()
        with self.request("/api/firmware/download") as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            expected = {
                "main.py", "version.py", "wifi_protocol.py", "README.txt",
                "candle.py", "matrix.py", "matrix_map.py",
                "base/manifest.json", "base/" + image.name,
            }
            if (base_firmware.DIRECTORY / "LICENSE").is_file():
                expected.add("base/LICENSE")
            self.assertEqual(set(archive.namelist()), expected)
            self.assertEqual(archive.read("base/" + image.name), image.read_bytes())
            manifest = json.loads(archive.read("base/manifest.json"))
            self.assertEqual(
                hashlib.sha256(archive.read("base/" + image.name)).hexdigest(),
                manifest["sha256"],
            )
            for name in archive.namelist():
                content = archive.read(name)
                for secret in (b"cookie-secret", b"api-secret", b"codex-secret", b"web-secret"):
                    self.assertNotIn(secret, content)

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

    def test_integration_install_requires_dashboard_and_uses_fixed_installer(self):
        with patch("aiflame.integrations.codex.install.install") as installer:
            with self.request(
                "/api/integrations/install", {"source": "codex"}
            ) as response:
                self.assertIn("Hooks", json.load(response)["message"])
            installer.assert_called_once_with()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(
                "/api/integrations/install",
                {"source": "codex"},
                {"Cookie": "", "Authorization": "Bearer api-secret"},
            )
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

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
