"""Slow and late device I/O must not hold up a saved mode change."""

import json
from contextlib import ExitStack
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from aiflame.core.engine import Engine
from aiflame.core.events import make_event
from aiflame.hub.server import Hub
from aiflame.transports.base import DeviceTransport
from aiflame.transports.usb import USBTransport
from aiflame.transports.wifi import SocketLine, WiFiTransport


class ModeSwitchLatencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hub = Hub(runtime=Path(self.temp.name), config={
            "hub_id": "fixture-hub", "device_id": "fixture-lamp",
            "transport": "wifi", "wifi_device_secret": "fixture-secret",
        })
        self.addCleanup(self.hub.stop)
        self.hub.update_settings({"mode": "hardware"})

    def test_switch_back_reports_connecting_until_real_handshake(self):
        self.hub.update_settings({"mode": "virtual"})
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.WiFiTransport") as factory:
            factory.return_value.is_alive.return_value = False
            result = self.hub.update_settings({"mode": "hardware"})
            self.assertFalse(result["device"]["online"])
            self.assertTrue(result["device"]["connecting"])
            self.assertEqual(result["device"]["status"], "正在连接设备")
            self.hub.transport.stop()
            self.assertFalse(self.hub.engine.device["connecting"])

    def test_usb_ready_board_has_no_fixed_startup_wait(self):
        worker = USBTransport(self.hub.engine, self.hub.lock, "hub", "lamp", "COM1")
        hello = {"type": "hello", "device_id": "lamp", "protocol_version": "1.0",
                 "session_id": "session", "capabilities": ["led-matrix-9x16", "mono-pwm"],
                 "display_width": 9, "display_height": 16}
        for replies in ([hello], [TimeoutError(), TimeoutError(), hello]):
            with patch("aiflame.transports.usb.list_ports.comports", return_value=[]), patch("aiflame.transports.usb.serial.Serial"), patch.object(worker, "receive", side_effect=replies), patch.object(worker.stopping, "wait") as wait:
                worker.connect()
                wait.assert_not_called()
                self.assertTrue(self.hub.engine.device["online"])

    def test_wifi_cached_address_is_tried_before_discovery(self):
        worker = WiFiTransport(self.hub.engine, self.hub.lock, "hub", "lamp", "secret", "192.0.2.1")
        with patch.object(worker, "discover", return_value=["192.0.2.1", "192.0.2.2"]) as discover:
            addresses = worker.addresses()
            self.assertEqual(next(addresses), "192.0.2.1")
            discover.assert_not_called()
            self.assertEqual(list(addresses), ["192.0.2.2"])

    def test_failed_handshake_clears_connecting_and_reports_offline(self):
        worker = DeviceTransport(self.hub.engine, self.hub.lock, "hub", "lamp")
        self.hub.engine.device.update(online=False, connecting=True)
        with patch.object(worker, "connect", side_effect=OSError("unplugged")), patch.object(worker.stopping, "wait", side_effect=lambda _: worker.stopping.set()):
            worker.run()
        self.assertFalse(self.hub.engine.device["online"])
        self.assertFalse(self.hub.engine.device["connecting"])
        self.assertEqual(self.hub.engine.device["last_error"], "unplugged")

    def test_explicit_disconnect_survives_mode_changes(self):
        self.hub.transport.records["fixture-lamp"]["connection_paused"] = True
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.WiFiTransport") as factory:
            self.hub.update_settings({"mode": "virtual"})
            result = self.hub.update_settings({"mode": "hardware"})
            factory.assert_not_called()
            self.assertFalse(result["device"]["online"])
            self.assertFalse(result["device"]["connecting"])
            self.assertEqual(result["device"]["status"], "已主动断开")

    def blocked_worker(self):
        left, peer = socket.socketpair()
        left.settimeout(4)
        peer.settimeout(2)
        entered = threading.Event()

        class Stream(SocketLine):
            def read_until(self, mark, limit):
                entered.set()
                return super().read_until(mark, limit)

        worker = DeviceTransport(self.hub.engine, self.hub.lock, "hub", "lamp")
        worker.connection = Stream(left)
        worker.session = "fixture-session"
        self.hub.engine.device["online"] = True
        self.hub.engine.accept(make_event("local-script", "task", "done"), "local-script")
        self.hub.transport.worker = worker
        self.hub.transport.running = True

        def cleanup():
            worker.stopping.set()
            peer.close()
            worker.join(5)
            left.close()

        self.addCleanup(cleanup)
        worker.start()
        stream = SocketLine(peer)
        command = json.loads(stream.read_until(b"\n", 4096))
        self.assertTrue(entered.wait(1))
        return worker, stream, command

    def release_ack(self, peer, command):
        peer.write((json.dumps({
            "type": "ack", "command_id": command["command_id"], "result": "applied",
        }) + "\n").encode())

    def test_blocked_ack_does_not_delay_mode_or_overwrite_virtual_state(self):
        worker, peer, command = self.blocked_worker()
        started = time.monotonic()
        result = self.hub.update_settings({"mode": "virtual"})
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertTrue(worker.is_alive())
        self.assertEqual(result["settings"]["mode"], "virtual")
        self.assertFalse(result["device"]["online"])
        reopened = Engine(Path(self.temp.name) / "events.sqlite")
        self.assertEqual(reopened.settings["mode"], "virtual")
        reopened.db.close()
        self.release_ack(peer, command)
        off = json.loads(peer.read_until(b"\n", 4096))
        self.assertEqual((off["state"], off["brightness"]), ("idle", 0))
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.hub.engine.last_command)
        self.assertEqual(self.hub.engine.device["status"], "虚拟模式")
        self.assertIsNone(self.hub.engine.device["last_error"])

    def test_fast_switch_back_waits_for_old_connection_before_starting(self):
        worker, peer, command = self.blocked_worker()
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.WiFiTransport") as factory:
            replacement = factory.return_value
            replacement.is_alive.return_value = False
            self.hub.update_settings({"mode": "virtual"})
            self.hub.update_settings({"mode": "hardware"})
            reaper = self.hub.transport.reaper
            factory.assert_not_called()
            self.assertIs(self.hub.transport.worker, worker)
            self.release_ack(peer, command)
            self.assertEqual(json.loads(peer.read_until(b"\n", 4096))["brightness"], 0)
            reaper.join(1)
            factory.assert_called_once()
            replacement.start.assert_called_once()
            self.assertFalse(worker.is_alive())
            self.assertIs(self.hub.transport.worker, replacement)
            self.hub.transport.stop()

    def test_maintenance_stop_cancels_deferred_restart(self):
        worker, peer, command = self.blocked_worker()
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.WiFiTransport") as factory:
            self.hub.update_settings({"mode": "virtual"})
            self.hub.update_settings({"mode": "hardware"})
            # Hold the lifecycle guard to force the retirement callback to run
            # after maintenance has cancelled the pending restart.
            with self.hub.transport.guard:
                self.release_ack(peer, command)
                worker.join(1)
                self.assertFalse(worker.is_alive())
                reaper = self.hub.transport.reaper
                self.hub.transport.stop()
            reaper.join(1)
            factory.assert_not_called()
            self.assertIsNone(self.hub.transport.worker)

    def test_late_reaper_does_not_lose_a_replacement_workers_cleanup(self):
        worker, peer, command = self.blocked_worker()
        self.hub.hardware_enabled = True
        release_replacement = threading.Event()
        replacement = threading.Thread(target=release_replacement.wait, daemon=True)
        replacement.stopping = threading.Event()
        self.addCleanup(release_replacement.set)
        with patch("aiflame.transports.manager.WiFiTransport", return_value=replacement):
            self.hub.update_settings({"mode": "virtual"})
            with self.hub.transport.guard:
                self.release_ack(peer, command)
                worker.join(1)
                self.assertFalse(worker.is_alive())
                old_reaper = self.hub.transport.reaper
                self.hub.transport.stop()
                self.hub.update_settings({"mode": "hardware"})
                self.assertIs(self.hub.transport.worker, replacement)
                self.hub.update_settings({"mode": "virtual"})
                new_reaper = self.hub.transport.reaper
                self.assertIsNot(old_reaper, new_reaper)
            release_replacement.set()
            old_reaper.join(1)
            new_reaper.join(1)
            self.assertIsNone(self.hub.transport.worker)
            self.assertEqual(self.hub.engine.device["status"], "虚拟模式")

    def test_handshake_ready_before_cancellation_cannot_restore_device_state(self):
        for transport in (USBTransport, WiFiTransport):
            with self.subTest(transport=transport.__name__), ExitStack() as patches:
                worker = transport(self.hub.engine, self.hub.lock, "hub", "lamp", "COM1" if transport is USBTransport else "aa" * 32)
                worker.session = "old-session"
                ready, release = threading.Event(), threading.Event()
                errors = []

                def receive_hello(*_):
                    ready.set()
                    release.wait(2)
                    return {"type": "hello", "device_id": "lamp", "protocol_version": "1.0", "session_id": "new-session", "capabilities": ["led-matrix-9x16", "mono-pwm"], "display_width": 9, "display_height": 16}

                if transport is USBTransport:
                    patches.enter_context(patch("aiflame.transports.usb.list_ports.comports", return_value=[]))
                    patches.enter_context(patch("aiflame.transports.usb.serial.Serial"))
                    patches.enter_context(patch.object(worker.stopping, "wait", return_value=False))
                    patches.enter_context(patch.object(worker, "receive", side_effect=receive_hello))
                else:
                    patches.enter_context(patch.object(worker, "discover", return_value=["127.0.0.1"]))
                    patches.enter_context(patch("aiflame.transports.wifi.socket.create_connection"))
                    calls = iter([{"type": "challenge", "device_id": "lamp", "protocol_version": "1.0", "nonce": "bb" * 12}])
                    patches.enter_context(patch.object(worker, "receive", side_effect=lambda *_: next(calls, None) or receive_hello()))
                changed = patches.enter_context(patch.object(self.hub.engine, "session_changed", new=Mock()))

                def connect():
                    try:
                        worker.connect()
                    except OSError as error:
                        errors.append(error)

                thread = threading.Thread(target=connect, daemon=True)
                thread.start()
                try:
                    self.assertTrue(ready.wait(1))
                    with self.hub.lock:
                        worker.stopping.set()
                        self.hub.engine.device.update(online=False, status="虚拟模式")
                    release.set()
                    thread.join(1)
                    self.assertFalse(thread.is_alive())
                    self.assertTrue(errors)
                    changed.assert_not_called()
                    self.assertFalse(self.hub.engine.device["online"])
                    self.assertEqual(self.hub.engine.device["status"], "虚拟模式")
                finally:
                    release.set()
                    thread.join(2)


if __name__ == "__main__":
    unittest.main()
