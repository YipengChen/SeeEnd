"""Slow and late device I/O must not hold up a saved mode change."""

import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from aiflame.hub.server import Hub


class ModeSwitchLatencyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.hub = Hub(runtime=Path(self.temp.name), config={
            "hub_id": "fixture-hub", "device_id": "fixture-lamp",
            "transport": "yeelight", "hardware_model": "yeelight-yltd003",
        })
        self.addCleanup(self.hub.stop)
        self.hub.update_settings({"hardware_notifications": True})

    def test_switch_back_reports_connecting_until_real_handshake(self):
        self.hub.update_settings({"hardware_notifications": False})
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.YeelightTransport") as factory:
            factory.return_value.is_alive.return_value = False
            result = self.hub.update_settings({"hardware_notifications": True})
            self.assertFalse(result["device"]["online"])
            self.assertTrue(result["device"]["connecting"])
            self.assertEqual(result["device"]["status"], "正在连接设备")
            self.hub.transport.stop()
            self.assertFalse(self.hub.engine.device["connecting"])

    def test_dead_worker_is_replaced_and_cannot_keep_stale_online_status(self):
        from aiflame.transports.manager import TransportManager
        engine = Mock(settings={'hardware_notifications': True}, device={'online': True})
        manager = TransportManager(engine, threading.RLock(),
                                   {'device_id': 'cube', 'hardware_model': 'yeelight-cube-lite'})
        dead = Mock()
        dead.is_alive.return_value = False
        manager.worker = manager.retiring_worker = dead
        with patch('aiflame.transports.manager.YeelightTransport') as factory:
            manager.start()
            replacement = factory.return_value
            self.assertIs(manager.worker, replacement)
            replacement.start.assert_called_once()
            self.assertFalse(engine.device['online'])
            self.assertTrue(engine.device['connecting'])
            manager._reap(dead)
            self.assertIs(manager.worker, replacement)
            replacement.start.assert_called_once()




    def test_explicit_disconnect_survives_mode_changes(self):
        self.hub.transport.records["fixture-lamp"]["connection_paused"] = True
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.YeelightTransport") as factory:
            self.hub.update_settings({"hardware_notifications": False})
            result = self.hub.update_settings({"hardware_notifications": True})
            factory.assert_not_called()
            self.assertFalse(result["device"]["online"])
            self.assertFalse(result["device"]["connecting"])
            self.assertEqual(result["device"]["status"], "已主动断开")

    def blocked_worker(self):
        left, peer = socket.socketpair()
        left.settimeout(4)
        entered = threading.Event()
        def run():
            left.sendall(b'{"command_id":"fixture"}\n')
            entered.set()
            try:
                left.recv(4096)
            except OSError:
                pass
        worker = threading.Thread(target=run, daemon=True)
        worker.stopping = threading.Event()
        self.hub.engine.device['online'] = True
        self.hub.transport.worker = worker
        self.hub.transport.running = True
        def cleanup():
            worker.stopping.set()
            peer.close()
            worker.join(5)
            left.close()
        self.addCleanup(cleanup)
        worker.start()
        command = json.loads(peer.recv(4096))
        self.assertTrue(entered.wait(1))
        return worker, peer, command

    def release_ack(self, peer, command):
        peer.sendall((json.dumps({
            "type": "ack", "command_id": command["command_id"], "result": "applied",
        }) + "\n").encode())


    def test_fast_switch_back_waits_for_old_connection_before_starting(self):
        worker, peer, command = self.blocked_worker()
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.YeelightTransport") as factory:
            replacement = factory.return_value
            replacement.is_alive.return_value = False
            self.hub.update_settings({"hardware_notifications": False})
            self.hub.update_settings({"hardware_notifications": True})
            reaper = self.hub.transport.reaper
            factory.assert_not_called()
            self.assertIs(self.hub.transport.worker, worker)
            self.release_ack(peer, command)
            reaper.join(1)
            factory.assert_called_once()
            replacement.start.assert_called_once()
            self.assertFalse(worker.is_alive())
            self.assertIs(self.hub.transport.worker, replacement)
            self.hub.transport.stop()

    def test_maintenance_stop_cancels_deferred_restart(self):
        worker, peer, command = self.blocked_worker()
        self.hub.hardware_enabled = True
        with patch("aiflame.transports.manager.YeelightTransport") as factory:
            self.hub.update_settings({"hardware_notifications": False})
            self.hub.update_settings({"hardware_notifications": True})
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
        with patch("aiflame.transports.manager.YeelightTransport", return_value=replacement):
            self.hub.update_settings({"hardware_notifications": False})
            with self.hub.transport.guard:
                self.release_ack(peer, command)
                worker.join(1)
                self.assertFalse(worker.is_alive())
                old_reaper = self.hub.transport.reaper
                self.hub.transport.stop()
                self.hub.update_settings({"hardware_notifications": True})
                self.assertIs(self.hub.transport.worker, replacement)
                self.hub.update_settings({"hardware_notifications": False})
                new_reaper = self.hub.transport.reaper
                self.assertIsNot(old_reaper, new_reaper)
            release_replacement.set()
            old_reaper.join(1)
            new_reaper.join(1)
            self.assertIsNone(self.hub.transport.worker)
            self.assertEqual(self.hub.engine.device["status"], "设备提醒已关闭")



if __name__ == "__main__":
    unittest.main()
