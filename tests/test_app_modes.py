"""Independent channel persistence and hardware isolation without contacting real devices."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.hub.server import Hub


class AppModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hub = Hub(runtime=self.root, config={
            "hub_id": "fixture-hub", "device_id": "fixture-lamp",
            "transport": "yeelight", "hardware_model": "yeelight-yltd003",
        })
        self.addCleanup(self.hub.stop)

    def test_defaults_and_device_switch_survive_restart(self):
        self.assertEqual(self.hub.engine.settings["hardware_notifications"], False)
        self.assertFalse(self.hub.engine.settings["desktop_notifications"])
        self.assertFalse(self.hub.engine.settings["wechat_notifications"])
        self.assertTrue(self.hub.engine.settings["desktop_flame_enabled"])
        self.assertNotIn("mode", self.hub.engine.settings)
        for enabled in (True, False):
            self.hub.update_settings({"hardware_notifications": enabled})
            reopened = Engine(self.root / "events.sqlite")
            try:
                self.assertEqual(reopened.settings["hardware_notifications"], enabled)
            finally:
                reopened.db.close()

    def test_legacy_settings_gain_channel_switches_and_keep_preferences(self):
        self.hub.engine.db.execute(
            "INSERT OR REPLACE INTO settings VALUES ('main', ?)",
            (json.dumps({"brightness": 13, "duration": 15}),),
        )
        self.hub.engine.db.commit()
        reopened = Engine(self.root / "events.sqlite")
        try:
            self.assertEqual(reopened.settings["hardware_notifications"], False)
            self.assertEqual(reopened.settings["brightness"], 13)
            self.assertTrue(reopened.settings["desktop_flame_enabled"])
        finally:
            reopened.db.close()

    def test_saved_legacy_modes_migrate_without_changing_other_channels(self):
        for mode in ("virtual", "hardware"):
            self.hub.engine.db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('main', ?)",
                (json.dumps({"mode": mode, "desktop_notifications": True,
                             "wechat_notifications": True, "desktop_flame_enabled": False}),),
            )
            self.hub.engine.db.commit()
            reopened = Engine(self.root / "events.sqlite")
            try:
                self.assertNotIn("mode", reopened.settings)
                self.assertEqual(reopened.settings["hardware_notifications"], mode == "hardware")
                self.assertTrue(reopened.settings["desktop_notifications"])
                self.assertTrue(reopened.settings["wechat_notifications"])
                self.assertFalse(reopened.settings["desktop_flame_enabled"])
                reopened.update_settings({"hardware_notifications": mode != "hardware"})
                self.assertTrue(reopened.settings["desktop_notifications"])
                self.assertTrue(reopened.settings["wechat_notifications"])
                self.assertFalse(reopened.settings["desktop_flame_enabled"])
            finally:
                reopened.db.close()


    def test_switch_starts_stops_and_restores_saved_hardware(self):
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.YeelightTransport") as wifi:
            worker = wifi.return_value
            worker.is_alive.return_value = False
            self.hub.update_settings({"hardware_notifications": True})
            worker.start.assert_called_once()
            self.hub.update_settings({"hardware_notifications": True, "brightness": 12})
            worker.start.assert_called_once()
            self.hub.engine.device["online"] = True
            self.hub.update_settings({"hardware_notifications": False})
            end = time.monotonic() + 1
            while self.hub.transport.worker and time.monotonic() < end:
                time.sleep(0.005)
            worker.stopping.set.assert_called_once()
            worker.join.assert_called_once()
            self.assertIsNone(self.hub.transport.worker)
            self.assertFalse(self.hub.engine.device["online"])
            self.assertEqual(self.hub.config["device_id"], "fixture-lamp")
            self.hub.update_settings({"hardware_notifications": True})
            self.assertEqual(wifi.call_count, 2)
            self.hub.update_settings({"hardware_notifications": False})
            reaper = self.hub.transport.reaper
            if reaper:
                reaper.join(1)

    def test_invalid_patch_does_not_stop_connection_or_change_settings(self):
        for patch_value in ({"hardware_notifications": "bad"}, {"hardware_notifications": []}, {"hardware_notifications": True, "brightness": 100}):
            with patch.object(self.hub.transport, "suspend") as stop:
                with self.assertRaises(EventError):
                    self.hub.update_settings(patch_value)
                stop.assert_not_called()
                self.assertEqual(self.hub.engine.settings["hardware_notifications"], False)

    def test_busy_operations_preserve_mode(self):
        self.hub.update_settings({"hardware_notifications": True})
        for service in (self.hub.devices, self.hub.tests):
            with patch.object(service, "busy", return_value=True):
                with self.assertRaisesRegex(EventError, "结束后再切换设备提醒"):
                    self.hub.update_settings({"hardware_notifications": False})
                self.assertEqual(self.hub.engine.settings["hardware_notifications"], True)

    def test_virtual_events_still_reach_screen_and_are_not_replayed(self):
        engine = self.hub.engine
        for seq, state in ((1, "working"), (2, "done")):
            engine.accept(make_event("local-script", "mode-task", state, seq), "local-script")
        self.assertEqual(engine.snapshot()["display"]["state"], "done")
        self.hub.update_settings({"hardware_notifications": True})
        engine.device["online"] = True
        self.assertEqual(engine.snapshot()["display"]["state"], "idle")



if __name__ == "__main__":
    unittest.main()
