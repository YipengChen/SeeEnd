"""Native notification consumer behavior without posting real OS notifications."""
import os
from importlib.util import find_spec
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace
HAS_QT = find_spec("PySide6") is not None
if HAS_QT:
    from PySide6.QtWidgets import QApplication
    from aiflame.desktop.notifications import NotificationConsumer


@unittest.skipUnless(HAS_QT, "Install desktop dependencies to test notifications")
class ConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.message = {"event_id": "e", "claim": "c", "title": "title", "body": "source"}
        self.calls = []
        self.sent = []
        self.dashboard = SimpleNamespace(ready=True, api=SimpleNamespace(request=lambda *a, **kw: self.calls.append((a, kw))))
        self.backend = SimpleNamespace(submit=lambda message, done: (self.sent.append(message), done('submitted')))
        self.consumer = NotificationConsumer(self.dashboard, self.backend)
        self.addCleanup(self.consumer.shutdown)

    def test_pending_claim_blocks_duplicate_poll_and_submission_is_acknowledged(self):
        self.consumer.poll()
        self.assertEqual(self.calls[0][0][0], '/api/notifications/claim')
        self.consumer.poll()
        self.assertEqual(len(self.calls), 1)
        self.consumer.received({'notifications': [self.message]})
        self.app.processEvents()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.calls[-1][1]['body']['status'], 'submitted')

    def test_backend_exception_records_failure(self):
        def fail(*args): raise RuntimeError('backend missing')
        self.backend.submit = fail
        self.consumer.received({'notifications': [self.message]})
        self.app.processEvents()
        self.assertEqual(self.calls[-1][1]['body']['status'], 'failed')

    def test_denied_permission_and_shutdown(self):
        self.backend.submit = lambda msg, done: done('denied')
        self.consumer.received({'notifications': [self.message]})
        self.app.processEvents()
        self.assertEqual(self.calls[-1][1]['body']['status'], 'denied')
        self.consumer.shutdown()
        before = len(self.calls)
        self.consumer.poll()
        self.consumer.received({'notifications': [self.message]})
        self.assertEqual(len(self.calls), before)

    def test_unavailable_permission_acknowledges_without_posting_or_prompting(self):
        self.dashboard.notification_status = 'not_determined'
        self.consumer.received({'notifications': [self.message]})
        self.app.processEvents()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.calls[-1][1]['body']['status'], 'denied')

    def test_old_permission_response_cannot_override_a_newer_check(self):
        callbacks, statuses = [], []
        self.backend.status = callbacks.append
        self.consumer.permission_status.connect(statuses.append)
        self.consumer.check_status()
        self.consumer.check_status()
        callbacks[1]('denied')
        callbacks[0]('authorized')
        self.assertEqual(statuses, ['denied'])

    def test_permission_request_does_not_submit_a_notification(self):
        callbacks, statuses = [], []
        self.backend.authorize = callbacks.append
        self.backend.status = lambda callback: self.fail('Do not recheck while authorization is pending')
        self.consumer.permission_status.connect(statuses.append)
        self.consumer.authorize_notifications()
        self.consumer.check_status()
        self.assertEqual(self.sent, [])
        callbacks[0]('authorized')
        self.assertEqual(statuses, ['authorized'])
        self.assertFalse(self.consumer.authorizing)

    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'macOS native backend')
    def test_macos_status_reads_current_app_permission_each_time_without_submitting(self):
        from aiflame.desktop.notifications import SystemNotifications
        codes, statuses = iter((0, 2, 1)), []
        class Center:
            def getNotificationSettingsWithCompletionHandler_(self, callback):
                code = next(codes)
                callback(SimpleNamespace(authorizationStatus=lambda: code))
        backend = SystemNotifications.__new__(SystemNotifications)
        backend.center = Center()
        for _ in range(3):
            backend.status(statuses.append)
        self.assertEqual(statuses, ['not_determined', 'authorized', 'denied'])

    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'macOS native backend')
    def test_macos_native_request_and_delegate_without_posting_notification(self):
        from unittest.mock import patch
        import Foundation
        import UserNotifications as UN
        from aiflame.desktop.notifications import SystemNotifications
        statuses, requests = [], []
        class Center:
            def setDelegate_(self, delegate): self.delegate = delegate
            def requestAuthorizationWithOptions_completionHandler_(self, options, callback): callback(True, None)
            def addNotificationRequest_withCompletionHandler_(self, request, callback):
                requests.append(request)
                callback(None)
        center = Center()
        bundle = SimpleNamespace(bundleIdentifier=lambda: 'com.aiflame.desktop', bundlePath=lambda: '/tmp/SeeEnd.app')
        with patch.object(Foundation, 'NSBundle', SimpleNamespace(mainBundle=lambda: bundle)), patch.object(UN, 'UNUserNotificationCenter', SimpleNamespace(currentNotificationCenter=lambda: center)):
            backend = SystemNotifications(None)
            backend.submit({'event_id': 'test', 'title': 'Title', 'body': 'Source'}, statuses.append)
        self.assertEqual(statuses, ['submitted'])
        self.assertEqual(requests[0].identifier(), 'test')
        self.assertEqual(requests[0].content().body(), 'Source')
        presentation = []
        center.delegate.userNotificationCenter_willPresentNotification_withCompletionHandler_(None, None, presentation.append)
        self.assertTrue(presentation[0] & UN.UNNotificationPresentationOptionBanner)
        activated, completions = [], []
        backend.activated.connect(activated.append)
        response = SimpleNamespace(notification=lambda: SimpleNamespace(request=lambda: requests[0]))
        center.delegate.userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
            None, response, lambda: completions.append(True))
        self.assertEqual(activated, ['test'])
        self.assertEqual(completions, [True])

    def test_click_returns_its_own_event_only_and_never_returns_test_notification(self):
        returned = []
        self.dashboard.return_task = returned.append
        self.consumer.return_task('event-a')
        self.consumer.return_task('seeend-notification-test')
        self.consumer.shutdown()
        self.consumer.return_task('event-b')
        self.assertEqual(returned, ['event-a'])

    @unittest.skipUnless(__import__('sys').platform == 'darwin', 'macOS native backend')
    def test_macos_completion_accepts_signal_result_across_native_void_boundary(self):
        import Foundation
        from aiflame.desktop.notifications import SystemNotifications
        callbacks, statuses = [], []
        class Center:
            def requestAuthorizationWithOptions_completionHandler_(self, options, callback):
                callback(True, None)
            def addNotificationRequest_withCompletionHandler_(self, request, callback):
                callbacks.append(callback)
        backend = SystemNotifications.__new__(SystemNotifications)
        backend.center = Center()
        self.consumer.test_result.connect(statuses.append)
        backend.submit({'event_id': 'void-test', 'title': 'Title', 'body': 'Source'},
                       self.consumer.test_result.emit)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(statuses, [])  # Completion happens after submit returns.
        error = Foundation.NSError.errorWithDomain_code_userInfo_('SeeEndTest', 1, None)
        for outcome in (None, error):
            def native_completion():
                return callbacks[0](outcome)
            # A real Objective-C void block rejects an accidental bool return.
            Foundation.NSBlockOperation.blockOperationWithBlock_(native_completion).start()
            self.app.processEvents()
        self.assertEqual(statuses, ['submitted', 'failed'])
