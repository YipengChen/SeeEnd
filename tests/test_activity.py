"""System input sampling must never fabricate activity on failure."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from aiflame.core.activity import ActivityMonitor


class ActivityTests(unittest.TestCase):
    def hub(self):
        stopping = Mock()
        stopping.wait.side_effect = [False, True]
        return SimpleNamespace(stopping=stopping, lock=threading.RLock(), engine=Mock())

    def test_input_timestamp_accounts_for_idle_time(self):
        hub = self.hub()
        with patch('aiflame.core.activity.idle_reader', return_value=lambda: 12), patch('aiflame.core.activity.time.monotonic', return_value=100):
            ActivityMonitor(hub).run()
        hub.engine.human_activity.assert_called_once_with(87.95)

    def test_unavailable_detection_does_not_end_hold(self):
        for value in (None, -1):
            hub = self.hub()
            with patch('aiflame.core.activity.idle_reader', return_value=lambda: value):
                ActivityMonitor(hub).run()
            hub.engine.human_activity.assert_not_called()
        hub = self.hub()
        with patch('aiflame.core.activity.idle_reader', side_effect=OSError):
            ActivityMonitor(hub).run()
        hub.engine.human_activity.assert_not_called()
