"""Manual verifier regressions using snapshots, without a Hub or real lamps."""
from copy import deepcopy
import unittest

from live_client import hardware_targets, hardware_applied, targets_connected
from verify_live import wait_for_state


class LiveVerificationTests(unittest.TestCase):
    def snapshot(self):
        return {
            "settings": {"mode": "hardware", "paused": False}, "emergency": False,
            "devices": [{"device_id": key, "online": True, "connection_paused": False,
                         "light_settings": {"brightness": 20}, "session_id": key,
                         "connected_at": 1} for key in ("one", "two")],
            "display": {"state": "done", "device_confirmed": True},
            "last_ack": {"seq": 999, "state": "done"},
            "deliveries": [{"event": "event", "output": "flame", "status": "applied"},
                           {"event": "event", "output": "flame:one", "status": "applied"}],
        }

    def test_first_lamp_and_global_ack_cannot_confirm_second_lamp(self):
        snapshot = self.snapshot()
        targets = hardware_targets(snapshot)
        self.assertFalse(hardware_applied(snapshot, targets, "event"))
        snapshot["deliveries"].append({"event": "other", "output": "flame:two", "status": "applied"})
        self.assertFalse(hardware_applied(snapshot, targets, "event"))
        snapshot["deliveries"].append({"event": "event", "output": "flame:two", "status": "applied"})
        self.assertTrue(hardware_applied(snapshot, targets, "event"))
        snapshot["devices"][1]["session_id"] = "rebooted"
        self.assertFalse(hardware_applied(snapshot, targets, "event"))

    def test_targets_require_hardware_connected_nonzero_sessions(self):
        original = self.snapshot()
        for mutation in (
            lambda s: s["settings"].update(mode="virtual"),
            lambda s: s["settings"].update(paused=True),
            lambda s: s.update(emergency=True),
            lambda s: s["display"].update(key="muted"),
            lambda s: s["devices"][1].update(online=False),
            lambda s: s["devices"][1]["light_settings"].update(brightness=0),
        ):
            snapshot = deepcopy(original)
            mutation(snapshot)
            with self.assertRaises(AssertionError):
                hardware_targets(snapshot, ["one", "two"])
        with self.assertRaises(AssertionError):
            hardware_targets(original, ["missing"])
        original["devices"][1]["light_settings"]["brightness"] = 0
        self.assertEqual(set(hardware_targets(original)), {"one"})

    def test_selected_targets_do_not_rely_on_another_online_lamp(self):
        snapshot = self.snapshot()
        targets = hardware_targets(snapshot, ["two"])
        snapshot["devices"][1]["online"] = False
        self.assertFalse(targets_connected(snapshot, targets))

    def test_pause_confirmation_uses_fleet_display_instead_of_global_seq(self):
        snapshot = self.snapshot()
        targets = hardware_targets(snapshot)
        snapshot["settings"]["paused"] = True
        snapshot["display"] = {"state": "idle", "device_confirmed": True}
        snapshot["last_ack"] = {"seq": 1, "state": "done"}

        class Client:
            def request(self, _path):
                return 200, snapshot

        self.assertIs(wait_for_state(Client(), "idle", targets), snapshot)
        snapshot["display"]["device_confirmed"] = False
        with self.assertRaises(AssertionError):
            wait_for_state(Client(), "idle", targets, seconds=0.01)
