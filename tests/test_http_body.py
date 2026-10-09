"""Reject truncated requests before JSON parsing."""

from email.message import Message
from io import BytesIO
import unittest

from aiflame.core.events import EventError
from aiflame.hub.server import Handler


class RequestBodyTests(unittest.TestCase):
    def handler(self, body, length):
        handler = Handler.__new__(Handler)
        handler.headers = Message()
        handler.headers["Content-Length"] = str(length)
        handler.rfile = BytesIO(body)
        return handler

    def test_complete_body(self):
        self.assertEqual(self.handler(b"{}", 2).read_body(4096), b"{}")

    def test_truncated_json_and_binary_body(self):
        for body in (b"", b"{}", b"\xe9" + bytes(1023)):
            with self.subTest(size=len(body)):
                with self.assertRaisesRegex(EventError, "请求体不完整"):
                    self.handler(body, len(body) + 1).read_body(4096)
