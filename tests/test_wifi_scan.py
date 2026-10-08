import threading
import time
import unittest
from unittest.mock import Mock
from aiflame.devices.wifi_scan import normalize, WiFiScan
from aiflame.transports.base import DeviceTransport


class WiFiScanTests(unittest.TestCase):
    def setUp(self):
        self.transport = Mock()
        self.transport.lock = threading.RLock()
        self.transport.engine.settings = {"mode": "hardware"}
        self.transport.engine.device = {"online": True, "capabilities": ["wifi-scan"]}
        self.transport.worker.stopping.is_set.return_value = False
        self.scan = WiFiScan(self.transport)

    def test_sort_and_deduplicate_esp32_results(self):
        self.transport.worker.scan_networks.return_value = [
            {"ssid": "Studio", "signal": -70}, {"ssid": "Guest", "signal": -55},
            {"ssid": "Studio", "signal": -40}, {"ssid": "", "signal": -10}]
        self.scan._scan()
        result = self.scan.status()
        self.assertEqual(result["source"], "esp32")
        self.assertEqual(result["networks"], [{"ssid": "Studio", "signal": -40}, {"ssid": "Guest", "signal": -55}])

    def test_offline_and_old_firmware_are_explicit(self):
        self.transport.engine.device["online"] = False
        self.scan._scan()
        self.assertIn("连接 ESP32", self.scan.status()["message"])
        self.transport.engine.device.update(online=True, capabilities=[])
        self.scan._scan()
        self.assertIn("0.4.4", self.scan.status()["message"])
        self.transport.worker.scan_networks.assert_not_called()

    def test_disconnect_discards_results(self):
        self.transport.worker.scan_networks.return_value = [{"ssid": "Old", "signal": -40}]
        self.transport.worker.stopping.is_set.return_value = True
        self.scan._scan()
        self.assertEqual(self.scan.status()["state"], "failed")
        self.assertEqual(self.scan.status()["networks"], [])

    def test_transport_scan_is_serialized_paginated_and_handles_slow_radio(self):
        worker = DeviceTransport(Mock(), threading.RLock(), "hub", "lamp")
        worker.session = "session"
        worker.send = Mock()
        packets = iter([
            {"networks": [{"ssid": "one", "signal": -40}], "next": 1, "done": False},
            {"networks": [{"ssid": "two", "signal": -60}], "next": 2, "done": True},
        ])
        def receive(predicate, timeout, **options):
            self.assertGreater(timeout, 2)
            self.assertFalse(options["cancelled"]())
            return next(packets)
        worker.receive = Mock(side_effect=receive)
        request = {"id": "scan", "done": threading.Event(), "deadline": time.monotonic() + 15}
        worker.scan_requests.put(request)
        worker.process_scan()
        self.assertTrue(request["done"].is_set())
        self.assertEqual(len(request["networks"]), 2)
        self.assertEqual(worker.send.call_args_list[1].args[0]["offset"], 1)
        self.assertEqual(worker.seq, 0)

    def test_scan_failure_does_not_disconnect_device(self):
        worker = DeviceTransport(Mock(), threading.RLock(), "hub", "lamp")
        worker.send = Mock()
        worker.receive = Mock(return_value={"error": "scan-failed"})
        request = {"id": "scan", "done": threading.Event(), "deadline": time.monotonic() + 15}
        worker.scan_requests.put(request)
        worker.process_scan()
        self.assertIn("ESP32", request["error"])
        self.assertTrue(request["done"].is_set())
        self.assertFalse(worker.stopping.is_set())
