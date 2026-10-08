import json
from pathlib import Path
import socket
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiflame.devices.wifi_protocol import proof, equal
from aiflame.transports.wifi import SocketLine, WiFiTransport


class TestWiFiTransport(unittest.TestCase):
    def test_proof_is_stable_and_not_plain_secret(self):
        key = "a1" * 32
        value = proof(key, "b2" * 12, "hub-test")
        self.assertEqual(len(value), 64)
        self.assertTrue(equal(value, proof(key, "b2" * 12, "hub-test")))
        self.assertFalse(equal(value, proof(key, "c3" * 12, "hub-test")))
        self.assertNotIn(key, value)

    def test_fragmented_lines_and_closed_connection(self):
        left, right = socket.socketpair()
        try:
            left.settimeout(1)
            stream = SocketLine(left)
            right.sendall(b'{"a":1}\npart')
            self.assertEqual(stream.read_until(b"\n", 20), b'{"a":1}\n')
            right.sendall(b"ial\n")
            self.assertEqual(stream.read_until(b"\n", 20), b"partial\n")
            right.close()
            with self.assertRaises(OSError):
                stream.read_until(b"\n", 20)
        finally:
            left.close()
            right.close()

    def test_authenticated_hello_and_device_ack(self):
        secret = "fa" * 32
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        errors = []

        def serve():
            try:
                peer, _ = server.accept()
                with peer:
                    f = peer.makefile("rwb")
                    f.write(
                        (
                            json.dumps(
                                {
                                    "type": "challenge",
                                    "nonce": "ab" * 12,
                                    "device_id": "device-test",
                                    "protocol_version": "1.0",
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    f.flush()
                    hello = json.loads(f.readline())
                    assert hello["proof"] == proof(secret, "ab" * 12, "hub-test")
                    f.write(
                        (
                            json.dumps(
                                {
                                    "type": "hello",
                                    "device_id": "device-test",
                                    "session_id": "session",
                                    "protocol_version": "1.0",
                                    "seq": 0,
                                    "firmware_version": "test",
                                    "capabilities": ["led-matrix-9x16", "mono-pwm"],
                                    "display_width": 9, "display_height": 16,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    f.flush()
                    command = json.loads(f.readline())
                    f.write(
                        (
                            json.dumps(
                                {
                                    "type": "ack",
                                    "command_id": command["command_id"],
                                    "result": "applied",
                                    "state": "done",
                                    "seq": command["seq"],
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                    f.flush()
            except Exception as exc:
                errors.append(exc)
            finally:
                server.close()

        thread = threading.Thread(target=serve)
        thread.start()

        class Engine:
            device = {}

            def session_changed(self):
                pass

        transport = WiFiTransport(
            Engine(), threading.RLock(), "hub-test", "device-test", secret
        )
        with patch.object(transport, "discover", return_value=["127.0.0.1"]), patch(
            "aiflame.transports.wifi.PORT", port
        ):
            transport.connect()
            ack = transport.display(
                {"state": "done", "duration_ms": 10000},
                {"brightness": 20, "reduced": False},
            )
        self.assertEqual(ack["result"], "applied")
        self.assertEqual(transport.engine.device["status"], "Wi-Fi 已连接")
        transport.connection.close()
        thread.join(2)
        self.assertFalse(errors)
