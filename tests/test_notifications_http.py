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
        self.assertEqual(receipts, {'flame': 'suppressed', 'desktop': 'denied'})
        self.assertEqual(self.client.request('/api/notifications/ack', receipt)[0], 409)
