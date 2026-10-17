"""Native sound-consumer races without sockets or a real audio device."""
import os
from importlib.util import find_spec
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.sound_consumer import SoundConsumer


if HAS_QT:
    class Toggle(QObject):
        toggled = Signal(bool)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.checked = True

        def isChecked(self):
            return self.checked

        def setChecked(self, checked):
            self.checked = checked
            self.toggled.emit(checked)

    class FakeApi:
        def __init__(self):
            self.calls = []
            self.auto_validate = True

        def request(self, path, callback=None, **kwargs):
            self.calls.append((path, callback, kwargs))
            if path == '/api/sounds/validate' and self.auto_validate:
                callback({'valid': True})

        def claims(self):
            return [call for call in self.calls if call[0] == "/api/sounds/claim"]

        def acknowledgements(self):
            return [call[2]["body"] for call in self.calls if call[0] == "/api/sounds/ack"]

    class FakeDashboard(QObject):
        sound_preview_requested = Signal(str)
        state_changed = Signal(dict)

        def __init__(self):
            super().__init__()
            self.ready = True
            self.writing = False
            self.model = {"settings": {"sound_notifications": True, "source_enabled": {"test": True}}}
            self.api = FakeApi()
            self.checks = {"sound_notifications": Toggle(self)}
            self.failures = []

        def sound_failed(self, text):
            self.failures.append(text)

    class FakePlayer(QObject):
        failed = Signal(str)
        completed = Signal(str)

        def __init__(self):
            super().__init__()
            self.played = []
            self.stops = 0
            self.closed = False
            self.playing = False
            self.accept = True
            self.synchronous_completion = False

        def play(self, category):
            self.played.append(category)
            self.playing = self.accept
            if self.synchronous_completion and self.accept:
                self.completed.emit("submitted")
            return self.accept

        def is_playing(self):
            return self.playing

        def stop(self):
            self.stops += 1
            self.playing = False

        def shutdown(self):
            self.closed = True
            self.stop()


@unittest.skipUnless(HAS_QT, "Install desktop dependencies to test sound consumption")
class SoundConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dashboard = FakeDashboard()
        self.player = FakePlayer()
        self.consumer = SoundConsumer(self.dashboard, self.player)
        self.consumer.timer.stop()
        self.addCleanup(self.consumer.shutdown)
        self.time = patch("aiflame.desktop.notifications.time.time", return_value=1000)
        self.time.start()
        self.addCleanup(self.time.stop)
        self.message = {"event_id": "event-a", "claim": "token-a", "category": "cat", "expires": 1015,
                        "source_id": "test", "task_id": "task", "seq": 1, "generation": "generation"}

    def receive_claim(self):
        self.consumer.poll()
        claim = self.dashboard.api.claims()[-1]
        claim[1]({"sounds": [dict(self.message)]})

    def test_play_completion_acknowledges_once_and_does_not_poll_during_audio(self):
        self.receive_claim()
        self.assertEqual(self.player.played, ["cat"])
        self.assertEqual(self.dashboard.api.acknowledgements(), [])
        self.consumer.poll()
        self.assertEqual(len(self.dashboard.api.claims()), 1)
        self.player.playing = False
        self.player.completed.emit("submitted")
        self.player.completed.emit("submitted")
        self.assertEqual(self.dashboard.api.acknowledgements(), [
            {"event_id": "event-a", "claim": "token-a", "status": "submitted"}])
        self.assertFalse(self.consumer.deadline.isActive())

    def test_synchronous_windows_completion_is_acknowledged_without_deadline(self):
        self.player.synchronous_completion = True
        self.receive_claim()
        self.assertIsNone(self.consumer.active)
        self.assertFalse(self.consumer.deadline.isActive())
        self.assertEqual(self.dashboard.api.acknowledgements()[0]["status"], "submitted")
        self.consumer.poll()
        self.assertEqual(len(self.dashboard.api.claims()), 1)

    def test_disabling_while_claim_is_pending_does_not_play_before_save_returns(self):
        self.consumer.poll()
        self.dashboard.writing = True
        self.dashboard.checks["sound_notifications"].setChecked(False)
        # The persisted model still says enabled until the settings request completes.
        self.dashboard.api.claims()[0][1]({"sounds": [self.message]})
        self.assertEqual(self.player.played, [])
        self.assertEqual(self.dashboard.api.acknowledgements()[0]["status"], "unavailable")

    def test_disabled_model_and_expired_claim_are_acknowledged_without_audio(self):
        for enabled, expires in ((False, 1015), (True, 1000)):
            self.dashboard.model["settings"]["sound_notifications"] = enabled
            self.message["expires"] = expires
            self.consumer.received({"sounds": [dict(self.message)]})
        self.assertEqual(self.player.played, [])
        self.assertEqual([body["status"] for body in self.dashboard.api.acknowledgements()],
                         ["unavailable", "unavailable"])

    def test_preview_can_play_with_reminders_disabled(self):
        self.dashboard.model["settings"]["sound_notifications"] = False
        self.dashboard.sound_preview_requested.emit("pop")
        self.assertEqual(self.player.played, ["pop"])
        self.player.completed.emit("submitted")
        self.assertEqual(self.dashboard.api.acknowledgements(), [])
        self.consumer.poll()
        self.assertEqual(self.dashboard.api.claims(), [])

    def test_disabling_active_audio_stops_it_and_acknowledges_unavailable(self):
        self.receive_claim()
        self.dashboard.model["settings"]["sound_notifications"] = False
        self.dashboard.state_changed.emit(self.dashboard.model)
        self.assertFalse(self.player.playing)
        self.assertIsNone(self.consumer.active)
        self.assertEqual(self.dashboard.api.acknowledgements()[0]["status"], "unavailable")

    def test_timeout_stops_audio_and_acknowledges_failure_once(self):
        self.receive_claim()
        self.consumer.deadline.timeout.emit()
        self.assertFalse(self.player.playing)
        self.assertTrue(self.dashboard.failures)
        self.assertEqual(self.dashboard.api.acknowledgements()[0]["status"], "failed")
        self.player.completed.emit("submitted")
        self.assertEqual(len(self.dashboard.api.acknowledgements()), 1)

    def test_submission_failure_and_claim_failure_release_busy_state(self):
        self.player.accept = False
        self.receive_claim()
        self.assertIsNone(self.consumer.active)
        self.assertFalse(self.consumer.deadline.isActive())
        self.assertEqual(self.dashboard.api.acknowledgements()[0]["status"], "failed")
        self.consumer.poll()
        self.dashboard.api.claims()[-1][2]["failed"]()
        self.assertFalse(self.consumer.pending)

    def test_shutdown_ignores_late_claim_completion_and_preview(self):
        self.consumer.poll()
        callback = self.dashboard.api.claims()[0][1]
        self.consumer.shutdown()
        callback({"sounds": [self.message]})
        self.player.completed.emit("submitted")
        self.dashboard.sound_preview_requested.emit("cat")
        self.consumer.poll()
        self.assertEqual(self.player.played, [])
        self.assertEqual(self.dashboard.api.acknowledgements(), [])
        self.assertTrue(self.player.closed)
        self.assertFalse(self.consumer.timer.isActive())

    def test_pending_claim_prevents_duplicate_poll(self):
        self.consumer.poll()
        self.consumer.poll()
        self.assertEqual(len(self.dashboard.api.claims()), 1)

    def test_validate_response_rechecks_source_and_does_not_stop_a_new_preview(self):
        self.dashboard.source_checks={'test':Toggle()}
        self.dashboard.api.auto_validate=False
        self.receive_claim()
        callback=[call[1] for call in self.dashboard.api.calls if call[0]=='/api/sounds/validate'][-1]
        self.dashboard.source_checks['test'].setChecked(False)
        self.dashboard.source_checks['test'].setChecked(True)
        self.consumer.preview('pop')
        stops=self.player.stops
        callback({'valid':True})
        self.assertEqual(self.player.played,['pop'])
        self.assertTrue(self.player.playing)
        self.assertEqual(self.player.stops,stops)

    def test_validate_response_rechecks_channel_task_expiry_and_reset(self):
        self.dashboard.api.auto_validate=False
        for reason in ('channel','task','expiry','reset'):
            with self.subTest(reason=reason):
                self.consumer.cancel()
                self.dashboard.model={'settings':{'sound_notifications':True,'source_enabled':{'test':True}}}
                self.dashboard.writing=False
                self.dashboard.checks['sound_notifications'].setChecked(True)
                self.consumer.received({'sounds':[dict(self.message)]})
                callback=[call[1] for call in self.dashboard.api.calls if call[0]=='/api/sounds/validate'][-1]
                if reason=='channel':
                    self.dashboard.checks['sound_notifications'].setChecked(False)
                    self.dashboard.checks['sound_notifications'].setChecked(True)
                elif reason=='task':
                    self.dashboard.model['tasks']=[{'source':'test','task':'task','seq':2}]
                elif reason=='reset':
                    self.dashboard.reset_pending=True
                with patch('aiflame.desktop.notifications.time.time',return_value=2000 if reason=='expiry' else 1000):
                    callback({'valid':True})
                self.assertEqual(self.player.played,[])
                self.dashboard.reset_pending=False

    def test_pending_claim_cannot_survive_source_disable_reenable(self):
        self.dashboard.source_checks={'test':Toggle()}
        self.consumer.poll()
        callback=self.dashboard.api.claims()[-1][1]
        self.dashboard.source_checks['test'].setChecked(False)
        self.dashboard.source_checks['test'].setChecked(True)
        callback({'sounds':[self.message]})
        self.assertEqual(self.player.played,[])

    def test_unrelated_ui_settings_do_not_cancel_a_validated_sound(self):
        self.dashboard.api.auto_validate=False
        self.receive_claim()
        callback=[call[1] for call in self.dashboard.api.calls if call[0]=='/api/sounds/validate'][-1]
        self.dashboard.model['settings']['brightness']=5
        self.dashboard.model['settings']['source_enabled']['other']=False
        self.dashboard.writing=True
        callback({'valid':True})
        self.assertEqual(self.player.played,['cat'])
