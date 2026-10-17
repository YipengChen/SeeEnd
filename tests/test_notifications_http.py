import os
import tempfile
import unittest
from unittest.mock import patch
from aiflame.hub.server import Hub
from aiflame.core.paths import load_config
from aiflame.core.events import make_event
from live_client import DashboardClient


class NotificationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'AIFLAME_DATA_DIR': self.temp.name})
        self.env.start()
        self.hub = Hub(port=0, config=load_config())
        self.hub.start(hardware=False)
        self.hub.update_settings({'desktop_notifications': True})
        self.client = DashboardClient(f'http://127.0.0.1:{self.hub.port}')

    def tearDown(self):
        self.hub.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_authenticated_claim_ack_and_separate_receipts(self):
        self.assertEqual(self.client.request('/api/notifications/claim', {}, authenticated=False)[0], 401)
        self.client.call('/api/settings', {'brightness': 0})
        with self.hub.lock:
            self.hub.engine.accept(make_event('manual', 'test', 'done'), 'manual')
        message, = self.client.call('/api/notifications/claim', {})['notifications']
        self.assertEqual(self.client.call('/api/notifications/claim', {})['notifications'], [])
        receipt = {'event_id': message['event_id'], 'claim': message['claim'], 'status': 'denied'}
        self.assertEqual(self.client.call('/api/notifications/ack', receipt)['status'], 'denied')
        receipts = {r['output']: r['status'] for r in self.client.call('/api/status')['deliveries']}
        self.assertEqual(receipts, {'flame': 'suppressed', 'desktop': 'denied', 'sound': 'disabled', 'wechat': 'disabled'})
        self.assertEqual(self.client.request('/api/notifications/ack', receipt)[0], 409)

    def test_single_task_unknown_recovery_requires_dashboard_and_rejects_stale_event(self):
        event = make_event('codex', 'residual', 'working')
        with self.hub.lock:
            self.hub.engine.accept(event, 'codex')
        body = {'event_id': event['event_id']}
        self.assertEqual(self.client.request('/api/tasks/mark-unknown', body, authenticated=False)[0], 401)
        self.assertEqual(self.client.call('/api/tasks/mark-unknown', body)['state'], 'unknown')
        self.assertEqual(self.client.request('/api/tasks/mark-unknown', body)[0], 409)
        self.assertEqual(self.client.request('/api/tasks/mark-unknown', {**body, 'all': True})[0], 400)
        with self.hub.lock:
            self.hub.engine.accept(make_event('codex', 'residual', 'done', 2), 'codex')
        self.assertEqual(self.client.request('/api/tasks/mark-unknown', body)[0], 409)
