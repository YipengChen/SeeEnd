"""Mode persistence and hardware isolation without contacting real devices."""

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

    def test_default_and_both_modes_survive_restart(self):
        self.assertEqual(self.hub.engine.settings["mode"], "virtual")
        for mode in ("hardware", "virtual"):
            self.hub.update_settings({"mode": mode})
            reopened = Engine(self.root / "events.sqlite")
            try:
                self.assertEqual(reopened.settings["mode"], mode)
            finally:
                reopened.db.close()

    def test_legacy_settings_gain_virtual_mode_and_keep_preferences(self):
        self.hub.engine.db.execute(
            "INSERT OR REPLACE INTO settings VALUES ('main', ?)",
            (json.dumps({"brightness": 13, "duration": 15}),),
        )
        self.hub.engine.db.commit()
        reopened = Engine(self.root / "events.sqlite")
        try:
            self.assertEqual(reopened.settings["mode"], "virtual")
            self.assertEqual(reopened.settings["brightness"], 13)
        finally:
            reopened.db.close()


    def test_switch_starts_stops_and_restores_saved_hardware(self):
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.YeelightTransport") as wifi:
            worker = wifi.return_value
            worker.is_alive.return_value = False
            self.hub.update_settings({"mode": "hardware"})
            worker.start.assert_called_once()
            self.hub.update_settings({"mode": "hardware", "brightness": 12})
            worker.start.assert_called_once()
            self.hub.engine.device["online"] = True
            self.hub.update_settings({"mode": "virtual"})
            end = time.monotonic() + 1
            while self.hub.transport.worker and time.monotonic() < end:
                time.sleep(0.005)
            worker.stopping.set.assert_called_once()
            worker.join.assert_called_once()
            self.assertIsNone(self.hub.transport.worker)
            self.assertFalse(self.hub.engine.device["online"])
            self.assertEqual(self.hub.config["device_id"], "fixture-lamp")
            self.hub.update_settings({"mode": "hardware"})
            self.assertEqual(wifi.call_count, 2)
            self.hub.update_settings({"mode": "virtual"})
            reaper = self.hub.transport.reaper
            if reaper:
                reaper.join(1)

    def test_invalid_patch_does_not_stop_connection_or_change_settings(self):
        for patch_value in ({"mode": "bad"}, {"mode": []}, {"mode": "hardware", "brightness": 100}):
            with patch.object(self.hub.transport, "suspend") as stop:
                with self.assertRaises(EventError):
                    self.hub.update_settings(patch_value)
                stop.assert_not_called()
                self.assertEqual(self.hub.engine.settings["mode"], "virtual")

    def test_busy_operations_preserve_mode(self):
        self.hub.update_settings({"mode": "hardware"})
        for service in (self.hub.devices, self.hub.tests):
            with patch.object(service, "busy", return_value=True):
                with self.assertRaisesRegex(EventError, "结束后再切换"):
                    self.hub.update_settings({"mode": "virtual"})
                self.assertEqual(self.hub.engine.settings["mode"], "hardware")

    def test_virtual_events_still_reach_screen_and_are_not_replayed(self):
        engine = self.hub.engine
        for seq, state in ((1, "working"), (2, "done")):
            engine.accept(make_event("local-script", "mode-task", state, seq), "local-script")
        self.assertEqual(engine.snapshot()["display"]["state"], "done")
        self.hub.update_settings({"mode": "hardware"})
        engine.device["online"] = True
        self.assertEqual(engine.snapshot()["display"]["state"], "idle")



if __name__ == "__main__":
    unittest.main()
