"""Recover saved lights missed by multicast without serial timeout delays."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.devices.fleet import FleetDeviceService
from aiflame.devices.models import YEELIGHT_BULB_MODEL, YEELIGHT_MODEL
from aiflame.devices.yeelight import discover_with_known_devices
from aiflame.transports.fleet import FleetTransportManager


def saved(identity, host, model=YEELIGHT_BULB_MODEL):
    return {"device_id": identity, "yeelight_host": host, "hardware_model": model,
            "connection_paused": True}


def reply(identity, host):
    return {"device_id": identity, "host": host, "hardware_model": YEELIGHT_BULB_MODEL,
            "model": "colorb", "port": 55443}


class KnownDeviceDiscoveryTests(unittest.TestCase):
    def test_only_missing_saved_same_model_addresses_are_probed(self):
        found = reply("found", "192.168.1.90")
        recovered = reply("missed", "192.168.1.52")
        records = [saved("found", "192.168.1.51"),  # DHCP changed; do not query stale IP.
                   saved("missed", "192.168.1.52"), saved("duplicate", "192.168.1.52"),
                   saved("other", "192.168.1.53", YEELIGHT_MODEL),
                   saved("bad", "127.0.0.1"), saved("unknown", ""),
                   saved("same-host", found["host"])]
        before = deepcopy(records)

        def search(host=None, **kwargs):
            return [found] if host is None else [recovered]

        with patch("aiflame.devices.yeelight.discover", side_effect=search) as discover:
            result = discover_with_known_devices(records, model_id=YEELIGHT_BULB_MODEL)
        self.assertEqual(result, [found, recovered])
        self.assertEqual([call.args for call in discover.call_args_list], [(), ("192.168.1.52",)])
        self.assertEqual(records, before)

    def test_offline_address_does_not_discard_multicast_or_other_replies(self):
        found = reply("found", "192.168.1.50")
        recovered = reply("recovered", "192.168.1.52")

        def search(host=None, **kwargs):
            if host == "192.168.1.51":
                raise OSError("unreachable")
            return [found] if host is None else [recovered]

        with patch("aiflame.devices.yeelight.discover", side_effect=search):
            result = discover_with_known_devices(
                [saved("offline", "192.168.1.51"), saved("recovered", "192.168.1.52")],
                model_id=YEELIGHT_BULB_MODEL)
        self.assertEqual({row["device_id"] for row in result}, {"found", "recovered"})

    def test_reassigned_ip_uses_current_reply_identity_and_deduplicates(self):
        actual = reply("current-owner", "192.168.1.52")
        with patch("aiflame.devices.yeelight.discover", side_effect=[[], [actual, actual]]):
            result = discover_with_known_devices([saved("previous-owner", actual["host"])],
                                                 model_id=YEELIGHT_BULB_MODEL)
        self.assertEqual(result, [actual])

    def test_no_cached_address_and_cancelled_search_do_not_start_unicast(self):
        for records, stopping in (([], None), ([saved("one", "192.168.1.52")], threading.Event())):
            if stopping:
                stopping.set()
            with patch("aiflame.devices.yeelight.discover", return_value=[]) as discover:
                self.assertEqual(discover_with_known_devices(records, stopping=stopping), [])
            self.assertEqual(discover.call_count, 1)

    def test_four_probes_run_together_and_remaining_probes_wait(self):
        records = [saved(str(i), f"192.168.1.{50 + i}") for i in range(7)]
        guard, release, first_four = threading.Lock(), threading.Event(), threading.Event()
        started, active, peak = [], 0, 0
        output, errors = [], []

        def search(host=None, **kwargs):
            nonlocal active, peak
            if host is None:
                return []
            with guard:
                started.append(host)
                active += 1
                peak = max(peak, active)
                if active == 4:
                    first_four.set()
            try:
                if not release.wait(3):
                    raise AssertionError("parallel probes were not released")
                return [reply(host, host)]
            finally:
                with guard:
                    active -= 1

        def run():
            try:
                output.extend(discover_with_known_devices(records, model_id=YEELIGHT_BULB_MODEL))
            except Exception as exc:
                errors.append(exc)

        with patch("aiflame.devices.yeelight.discover", side_effect=search):
            worker = threading.Thread(target=run)
            worker.start()
            try:
                self.assertTrue(first_four.wait(2), "probes ran serially")
                with guard:
                    self.assertEqual(len(started), 4)
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(peak, 4)
        self.assertEqual(len(output), 7)

    def test_stop_during_multicast_skips_all_supplementary_queries(self):
        stopping = threading.Event()
        found = reply("found", "192.168.1.50")

        def search(**kwargs):
            stopping.set()
            return [found]

        with patch("aiflame.devices.yeelight.discover", side_effect=search) as discover:
            self.assertEqual(discover_with_known_devices([saved("missed", "192.168.1.52")],
                                                         stopping=stopping), [found])
        self.assertEqual(discover.call_count, 1)

    def test_new_device_search_recovers_saved_paused_lights_without_reconnecting(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Engine(":memory:")
            self.addCleanup(engine.db.close)
            engine.update_settings({"hardware_notifications": True})
            records = [saved("one", "192.168.1.51"), saved("two", "192.168.1.52"),
                       saved("other", "192.168.1.53", YEELIGHT_MODEL)]
            config = {"devices": {record["device_id"]: record for record in records}}
            lock = threading.RLock()
            fleet = FleetTransportManager(engine.flame, lock, config)
            service = FleetDeviceService(config, directory, fleet, lock)
            managers = [fleet.connection(record["device_id"]) for record in records]
            for manager in managers:
                manager.start, manager.stop = Mock(), Mock()
            before = deepcopy(config)
            disk_before = (Path(directory) / "credentials.json").read_bytes()

            def search(host=None, **kwargs):
                return [] if host is None else [reply(host, host)]

            with patch("aiflame.devices.yeelight.discover", side_effect=search) as discover:
                service.start({"action": "discover", "hardware_model": YEELIGHT_BULB_MODEL,
                               "device_id": ""})
                service.thread.join(3)
            self.assertFalse(service.thread.is_alive())
            self.assertEqual(service.job["state"], "succeeded", service.job)
            self.assertEqual({row["host"] for row in service.job["result"]["devices"]},
                             {"192.168.1.51", "192.168.1.52"})
            self.assertEqual({call.args[0] for call in discover.call_args_list if call.args},
                             {"192.168.1.51", "192.168.1.52"})
            self.assertEqual(config, before)
            self.assertEqual((Path(directory) / "credentials.json").read_bytes(), disk_before)
            for manager in managers:
                manager.start.assert_not_called()
                manager.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
