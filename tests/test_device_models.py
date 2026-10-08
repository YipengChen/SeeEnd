"""Hardware model isolation, firmware bundles and physical channel bounds."""

import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock
import zipfile

from aiflame.core.engine import Engine
from aiflame.core.events import EventError
from aiflame.devices.downloads import firmware_archive
from aiflame.devices.models import DEFAULT_MODEL, MODELS, firmware_files
from aiflame.devices.service import DeviceService
from aiflame.devices.firmware import candle, matrix, matrix_map_v02
from aiflame.transports.base import DeviceTransport
from test_matrix_firmware import Bus


class DeviceModelTests(unittest.TestCase):
    def test_v02_mapping_and_unused_physical_channels_stay_off(self):
        mapping = matrix_map_v02.CHANNEL_MAP
        self.assertEqual(len(mapping), 105)
        self.assertEqual(set(mapping), set(range(105)))
        self.assertEqual(mapping[14 * 7], 0)
        self.assertEqual(mapping[6], 104)
        self.assertEqual(matrix_map_v02.DISABLED_CHANNELS, ())
        bus = Bus()
        panel = matrix.Matrix(bus, mapping, width=7, height=15)
        panel.initialize()
        panel.show([255] * 105)
        pwm = next(value for _, register, value in reversed(bus.writes) if register == 0x24)
        self.assertEqual(pwm, bytes([16] * 105 + [0] * 39))
        enabled = next(value for bank, register, value in bus.writes if bank == 0 and register == 0)
        self.assertEqual(enabled, bytes([255] * 13 + [1] + [0] * 4))
        with self.assertRaises(ValueError):
            panel.show(bytes(144))

    def test_v02_render_all_states_and_small_brightness(self):
        for state in ("working", "done", "error"):
            renderer = candle.Renderer(7, 15)
            frame = renderer.render(0, state, 1)
            self.assertEqual(len(frame), 105)
            self.assertGreater(max(frame), 0)
            for now in (500, 13000, 14000, 41000, 74000):
                self.assertIs(renderer.render(now, state, 25), frame)
                self.assertLessEqual(max(frame), 16)
            self.assertEqual(bytes(renderer.render(75000, state, 0)), bytes(105))
            self.assertEqual(bytes(renderer.render(76000, state, 25, fade=0)), bytes(105))
            self.assertEqual(bytes(renderer.render(77000, "idle", 25)), bytes(105))

    def test_exports_and_install_sources_use_the_selected_map(self):
        for model in (item for item in MODELS if "mapping" in item):
            with self.subTest(model=model["id"]):
                files = firmware_files(model["id"])
                with zipfile.ZipFile(io.BytesIO(firmware_archive(model["id"]))) as archive:
                    self.assertEqual(archive.read("matrix_map.py"), files["matrix_map.py"].read_bytes())
                    namespace = {}
                    exec(archive.read("matrix_map.py"), namespace)
                    self.assertEqual(namespace["MODEL"], model["id"])
                    self.assertEqual(len(namespace["CHANNEL_MAP"]), model["width"] * model["height"])
                    self.assertIn(model["name"], archive.read("README.txt").decode())
                    self.assertNotIn("device_auth.json", archive.namelist())

    def test_transport_rejects_other_model_but_accepts_legacy_v01(self):
        for model in (item for item in MODELS if "mapping" in item):
            hello = {"hardware_model": model["id"], "display_width": model["width"],
                     "display_height": model["height"],
                     "capabilities": [f"led-matrix-{model['width']}x{model['height']}", "mono-pwm"]}
            DeviceTransport.validate_display(hello, model["id"])
            other = next(item for item in MODELS if item != model and "mapping" in item)
            with self.assertRaises(ValueError):
                DeviceTransport.validate_display(hello, other["id"])
        DeviceTransport.validate_display({"display_width": 9, "display_height": 16,
                                          "capabilities": ["led-matrix-9x16", "mono-pwm"]})

    def test_profile_switch_preserves_pairing_and_requires_explicit_connect(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(":memory:")
            self.addCleanup(engine.db.close)
            config = {"device_id": "original", "port": "usb-old", "wifi_device_secret": "original-secret"}
            transport = Mock(engine=engine)
            service = DeviceService(config, directory, transport, threading.RLock())

            def select(model):
                service.start({"action": "select_model", "hardware_model": model})
                service.thread.join(2)
                self.assertFalse(service.thread.is_alive())
                self.assertEqual(service.job["state"], "succeeded")

            select(DEFAULT_MODEL)
            transport.stop.assert_not_called()
            select("ai-flame-v02")
            self.assertNotIn("device_id", config)
            self.assertNotIn("wifi_device_secret", config)
            self.assertTrue(config["connection_paused"])
            config.update(device_id="new", port="usb-new", wifi_device_secret="new-secret")
            select(DEFAULT_MODEL)
            self.assertEqual(config["device_id"], "original")
            self.assertEqual(config["wifi_device_secret"], "original-secret")
            select("ai-flame-v02")
            self.assertEqual(config["device_id"], "new")
            self.assertEqual(config["wifi_device_secret"], "new-secret")
            transport.start.assert_not_called()
            saved = json.loads((Path(directory) / "credentials.json").read_text())
            self.assertEqual(saved["hardware_model"], "ai-flame-v02")
            self.assertNotIn("secret", json.dumps(service.status()))
            with self.assertRaises(EventError):
                service.start({"action": "select_model", "hardware_model": "missing"})
            with self.assertRaisesRegex(EventError, "型号已改变"):
                service.start({"action": "install", "hardware_model": DEFAULT_MODEL})
