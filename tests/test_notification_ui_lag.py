"""New valid claims must survive a dashboard snapshot that is one poll behind."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from types import SimpleNamespace
from importlib.util import find_spec
import unittest

from aiflame.core.engine import Engine
from aiflame.core.events import SourcePolicy, make_event
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.notifications import NotificationConsumer
    from aiflame.desktop.sound_consumer import SoundConsumer
    from test_sound_consumer import FakeDashboard, FakePlayer


@unittest.skipUnless(HAS_QT, "Install desktop dependencies to test notification consumption")
class NotificationSnapshotLagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_new_completion_delivers_before_the_dashboard_status_poll_catches_up(self):
        for channel in ("desktop", "sound"):
            with self.subTest(channel=channel):
                engine = Engine(":memory:", sources=[SourcePolicy("test", "Test", "test")])
                engine.update_settings({"desktop_notifications": True, "sound_notifications": True})
                engine.accept(make_event("test", "task", "working", 1), "test")
                cached = engine.snapshot()
                result, _ = engine.accept(make_event("test", "task", "done", 2), "test")
                output = getattr(engine, channel)
                message, = output.claim()
                proof = {key: message[key] for key in ("event_id", "claim", "generation")}
                self.assertTrue(output.validate_claim(proof)["valid"])
                self.assertEqual(cached["tasks"][0]["seq"], 1)
                self.assertEqual(message["seq"], 2)
                dashboard = FakeDashboard()
                dashboard.model = cached
                calls, sent = [], []
                def request(path, callback=None, **kwargs):
                    calls.append(path)
                    if path.endswith("/validate"):
                        callback(output.validate_claim(kwargs["body"]))
                    elif path.endswith("/ack"):
                        output.acknowledge(kwargs["body"])
                dashboard.api = SimpleNamespace(request=request)
                if channel == "desktop":
                    def submit(item, completed):
                        def post():
                            sent.append(item)
                            completed("submitted")
                        item["_before_submit"](post)
                    consumer = NotificationConsumer(dashboard, SimpleNamespace(submit=submit))
                else:
                    player = FakePlayer()
                    player.synchronous_completion = True
                    sent = player.played
                    consumer = SoundConsumer(dashboard, player)
                    consumer.timer.stop()
                try:
                    consumer.received({"notifications" if channel == "desktop" else "sounds": [message]})
                    self.app.processEvents()
                    self.assertEqual(len(sent), 1)
                    row = engine.db.execute("SELECT status FROM deliveries WHERE event=? AND output=?",
                                            (result["event_id"], channel)).fetchone()
                    self.assertEqual(row["status"], "submitted")
                    self.assertTrue(any(path.endswith("/validate") for path in calls))
                finally:
                    consumer.shutdown()
                    engine.db.close()


if __name__ == "__main__":
    unittest.main()
