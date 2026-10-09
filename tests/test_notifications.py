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
