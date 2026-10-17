"""Overview sound-channel saves and cancellation without a local server."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import QObject, QEvent

from tests import test_sound_preferences as preferences
from tests import test_sound_consumer as consumer_fakes
from aiflame.desktop.sound_consumer import SoundConsumer


class HomeSoundTests(unittest.TestCase):
    setUpClass = classmethod(preferences.SoundPreferencesTests.setUpClass.__func__)
    setUp = preferences.SoundPreferencesTests.setUp
    tearDown = preferences.SoundPreferencesTests.tearDown
    settle = preferences.SoundPreferencesTests.settle

    def capture_requests(self):
        requests = []

        def request(path, callback=None, body=None, **options):
            requests.append((path, callback, body, options))

        self.widget.api.request = request
        return requests

    def sound_checks(self):
        return (self.widget.home_channel_checks["sound_notifications"],
                self.widget.checks["sound_notifications"])

    def confirm_settings(self, request):
        _, callback, body, _ = request
        self.engine.update_settings(body)
        callback({**self.engine.snapshot(), "maintenance": False})

    def test_demo_start_and_stop_preserve_channel_states_without_disable_events(self):
        self.engine.update_settings({"hardware_notifications": True})
        self.engine.device.update(online=True, port="wifi://192.0.2.1:55443")
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.widget.navigate(0)
        self.settle()
        controls = self.widget.home_channel_checks
        before = {key: (toggle.isEnabled(), toggle.isChecked()) for key, toggle in controls.items()}
        transitions = []

        class Watcher(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Type.EnabledChange:
                    transitions.append(watched.isEnabled())
                return False

        watcher = Watcher(self.widget)
        for toggle in controls.values():
            toggle.installEventFilter(watcher)
        requests = self.capture_requests()
        self.widget.preview_button.click()
        start = next(item for item in requests if item[0] == "/api/preview")
        self.assertEqual(before, {key: (toggle.isEnabled(), toggle.isChecked()) for key, toggle in controls.items()})
        self.engine.start_preview(start[2]["id"])
        start[1]({**self.engine.snapshot(), "maintenance": False})
        self.widget.preview_button.click()
        stop = next(item for item in reversed(requests) if item[0] == "/api/preview")
        self.assertEqual(stop[2]["action"], "stop")
        self.engine.cancel_preview(stop[2]["id"])
        stop[1]({**self.engine.snapshot(), "maintenance": False})
        self.widget.preview_button.click()
        rejected = next(item for item in reversed(requests) if item[0] == "/api/preview")
        rejected[3]["failed"]()
        self.assertEqual(before, {key: (toggle.isEnabled(), toggle.isChecked()) for key, toggle in controls.items()})
        self.assertEqual(transitions, [])

    def test_channel_save_during_demo_request_stays_serialized_and_pending(self):
        self.engine.update_settings({"hardware_notifications": True})
        self.engine.device.update(online=True, port="wifi://192.0.2.1:55443")
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.widget.navigate(0)
        requests = self.capture_requests()
        self.widget.preview_button.click()
        self.sound_checks()[0].click()
        self.assertTrue(all(check.isChecked() for check in self.sound_checks()))
        self.assertTrue(all(not check.isEnabled() for check in self.sound_checks()))
        self.assertEqual([item[0] for item in requests], ["/api/preview"])
        preview = requests[0]
        self.engine.start_preview(preview[2]["id"])
        preview[1]({**self.engine.snapshot(), "maintenance": False})
        self.confirm_settings(next(item for item in requests if item[0] == "/api/settings"))
        self.assertTrue(all(check.isChecked() and check.isEnabled() for check in self.sound_checks()))
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_home_enable_syncs_both_pending_controls_silently_and_preserves_category(self):
        self.engine.update_settings({"sound_category": "cat"})
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        self.widget.navigate(0)
        requests = self.capture_requests()
        self.sound_checks()[0].click()
        self.assertTrue(all(check.isChecked() for check in self.sound_checks()))
        self.assertFalse(self.engine.settings["sound_notifications"])
        self.assertFalse(self.widget.model["settings"]["sound_notifications"])
        self.assertTrue(self.widget.statusbar.isHidden())
        self.assertNotIn("sound_notifications", self.widget.save_feedback)
        # A poll carrying the last confirmed value cannot undo the pending choice.
        self.widget.render(self.widget.model)
        self.assertTrue(all(check.isChecked() for check in self.sound_checks()))
        self.confirm_settings(next(item for item in requests if item[0] == "/api/settings"))
        self.assertTrue(all(check.isChecked() for check in self.sound_checks()))
        self.assertEqual(self.widget.sound_category.currentData(), "cat")
        self.assertEqual(self.widget.model["settings"]["sound_category"], "cat")
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_preferences_disable_syncs_overview_before_save_response(self):
        self.engine.update_settings({"sound_notifications": True, "sound_category": "cat"})
        self.widget.render({**self.engine.snapshot(), "maintenance": False})
        requests = self.capture_requests()
        self.sound_checks()[1].click()
        self.assertTrue(all(not check.isChecked() for check in self.sound_checks()))
        self.assertTrue(self.engine.settings["sound_notifications"])
        self.confirm_settings(next(item for item in requests if item[0] == "/api/settings"))
        self.assertFalse(self.engine.settings["sound_notifications"])
        self.assertTrue(all(not check.isChecked() for check in self.sound_checks()))
        self.assertEqual(self.engine.settings["sound_category"], "cat")
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_save_failure_restores_confirmed_values_and_explains_retry_on_both_controls(self):
        self.widget.navigate(0)
        requests = self.capture_requests()
        self.sound_checks()[0].click()
        self.assertTrue(all(check.isChecked() for check in self.sound_checks()))
        _, _, _, options = next(item for item in requests if item[0] == "/api/settings")
        options["error_callback"]("音效设置保存失败")
        options["failed"]()
        self.assertFalse(self.engine.settings["sound_notifications"])
        for check in self.sound_checks():
            self.assertFalse(check.isChecked())
            self.assertIn("未保存", check.toolTip())
            self.assertIn("音效设置保存失败", check.toolTip())
        self.assertTrue(self.widget.statusbar.isHidden())

    def test_home_disable_stops_audio_and_rejects_claim_while_primary_still_enabled(self):
        dashboard = consumer_fakes.FakeDashboard()
        home = consumer_fakes.Toggle(dashboard)
        dashboard.home_channel_checks = {"sound_notifications": home}
        player = consumer_fakes.FakePlayer()
        consumer = SoundConsumer(dashboard, player)
        consumer.timer.stop()
        self.addCleanup(consumer.shutdown)
        message = {"event_id": "first", "claim": "first-token", "category": "cat", "expires": 1015,
                   "source_id": "test", "task_id": "task", "seq": 1, "generation": "generation"}
        with patch("aiflame.desktop.notifications.time.time", return_value=1000):
            consumer.received({"sounds": [message]})
            self.assertTrue(player.playing)
            home.setChecked(False)
            self.assertFalse(player.playing)
            self.assertTrue(dashboard.checks["sound_notifications"].isChecked())
            self.assertEqual(dashboard.api.acknowledgements()[0]["status"], "unavailable")
            home.setChecked(True)
            consumer.poll()
            claim_callback = dashboard.api.claims()[-1][1]
            home.setChecked(False)
            claim_callback({"sounds": [{**message, "event_id": "late", "claim": "late-token"}]})
        self.assertEqual(player.played, ["cat"])
        self.assertEqual(dashboard.api.acknowledgements()[-1],
                         {"event_id": "late", "claim": "late-token", "status": "unavailable"})


if __name__ == "__main__":
    unittest.main()
