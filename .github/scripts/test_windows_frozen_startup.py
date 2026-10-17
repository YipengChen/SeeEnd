"""CI-only regression checks for the frozen GUI startup port guard."""

import unittest
from unittest.mock import patch

import windows_frozen_startup as startup


class PortGuardTests(unittest.TestCase):
    def test_free_port_uses_exclusive_bind_without_connecting(self):
        with patch.object(startup.socket, "SO_EXCLUSIVEADDRUSE", 0x1234, create=True), \
                patch.object(startup.socket, "socket") as factory, \
                patch.object(startup.socket, "create_connection", side_effect=TimeoutError) as connect:
            probe = factory.return_value.__enter__.return_value
            startup.verify_port_available()
            probe.setsockopt.assert_called_once_with(startup.socket.SOL_SOCKET, 0x1234, 1)
            probe.bind.assert_called_once_with(("127.0.0.1", 8765))
            factory.return_value.__exit__.assert_called_once()
            connect.assert_not_called()

    def test_occupied_port_aborts_and_closes_probe(self):
        with patch.object(startup.socket, "socket") as factory:
            probe = factory.return_value.__enter__.return_value
            probe.bind.side_effect = OSError("address already in use")
            with self.assertRaisesRegex(RuntimeError, "Port 8765 is unavailable"):
                startup.verify_port_available()
            factory.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
