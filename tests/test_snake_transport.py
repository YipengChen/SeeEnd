"""Snake geometry survives real transport phase changes without network I/O."""
import base64
import threading
import unittest
from unittest.mock import Mock, patch

from aiflame.devices.cube_effects import snake_game
from aiflame.devices.light_settings import light_settings
from aiflame.devices.models import YEELIGHT_CUBE_MODEL
from aiflame.transports.yeelight import YeelightTransport


class SnakeTransportTests(unittest.TestCase):
    def setUp(self):
        self.settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})
        self.settings.update(reminder_mode='snake', brightness=100)
        self.settings['colors'].update(working='#FF0000', needs_input='#0000FF', done='#00FF00')
        self.settings['state_brightness'].update(working=100, needs_input=100, done=100)

    def worker(self):
        worker = YeelightTransport(Mock(settings=self.settings), threading.RLock(),
                                   {'hardware_model': YEELIGHT_CUBE_MODEL})

        def command(method, params, **kwargs):
            self.assertEqual(method, 'update_leds')
            return {'id': 1, 'result': ['sent'], 'payload': kwargs['params_factory']()}

        worker.command = Mock(side_effect=command)
        return worker

    def draw(self, worker, now, state='working', key=None, **extra):
        desired = {'key': key or state, 'state': state, 'duration_ms': 3000, **extra}
        with patch('aiflame.transports.yeelight.time.monotonic', return_value=now):
            response = worker.display(desired, self.settings)
        return base64.b64decode(response['payload'][0])

    @staticmethod
    def channel_positions(payload, channel):
        return {index // 3 for index in range(channel, len(payload), 3) if payload[index]}

    @staticmethod
    def game(worker):
        return snake_game((worker.cube_animation_id, False, False))

    def grown_worker(self):
        worker = self.worker()
        self.draw(worker, 100)
        # Put a bean one safe step in front of the initial head, so the test
        # checks inheritance of a grown body rather than only the default four.
        self.game(worker).food = (1, 0)
        payload = self.draw(worker, 100.26)
        self.assertEqual(len(self.game(worker).body), 5)
        return worker, payload

    def test_waiting_inherits_grown_geometry_and_resumes_one_tick_at_a_time(self):
        worker, before = self.grown_worker()
        game = self.game(worker)
        snapshot, step = tuple(game.body), game.step
        waiting = self.draw(worker, 101, 'needs_input', key='prompt-1')
        self.assertEqual(self.channel_positions(waiting, 2), self.channel_positions(before, 0))
        self.assertEqual(self.channel_positions(waiting, 1), self.channel_positions(before, 1))
        later = self.draw(worker, 161, 'needs_input', key='prompt-1')
        self.assertEqual(self.channel_positions(later, 2), self.channel_positions(waiting, 2))
        self.assertEqual(tuple(game.body), snapshot)
        self.assertEqual(game.step, step)
        self.assertEqual(self.draw(worker, 162), before)
        self.assertEqual(game.step, step)
        self.draw(worker, 162.26)
        self.assertEqual(game.step, step + 1)
        self.assertNotEqual(tuple(game.body), snapshot)

    def test_cube_state_alone_pauses_and_resumes_without_geometry_reset(self):
        worker = self.worker()
        self.draw(worker, 100, key='same', cube_state='working')
        before = self.draw(worker, 101, key='same', cube_state='working')
        step = self.game(worker).step
        waiting = self.draw(worker, 102, key='same', cube_state='needs_input')
        self.assertEqual(self.channel_positions(waiting, 2), self.channel_positions(before, 0))
        self.draw(worker, 202, key='same', cube_state='needs_input')
        self.assertEqual(self.game(worker).step, step)
        self.assertEqual(self.draw(worker, 203, key='same', cube_state='working'), before)
        self.draw(worker, 203.26, key='same', cube_state='working')
        self.assertEqual(self.game(worker).step, step + 1)

    def test_repeated_long_waits_never_advance_working_clock(self):
        worker, before = self.grown_worker()
        for start in (200, 1000):
            step = self.game(worker).step
            waiting = self.draw(worker, start, 'needs_input', key=f'prompt-{start}')
            self.assertEqual(self.channel_positions(waiting, 2), self.channel_positions(before, 0))
            self.draw(worker, start + 600, 'needs_input', key=f'prompt-{start}')
            self.assertEqual(self.game(worker).step, step)
            self.assertEqual(self.draw(worker, start + 601), before)
            before = self.draw(worker, start + 601.26)
            self.assertEqual(self.game(worker).step, step + 1)

    def test_idle_starts_a_new_snake_session(self):
        worker, _ = self.grown_worker()
        previous_id = worker.cube_animation_id
        self.assertEqual(self.draw(worker, 102, 'idle'), bytes(300))
        self.draw(worker, 103)
        self.assertNotEqual(worker.cube_animation_id, previous_id)
        self.assertEqual(self.game(worker).step, 0)
        self.assertEqual(len(self.game(worker).body), 4)

    def test_transports_keep_independent_snakes(self):
        first, grown = self.grown_worker()
        second = self.worker()
        initial = self.draw(second, 101)
        self.assertNotEqual(first.cube_animation_id, second.cube_animation_id)
        first_wait = self.draw(first, 102, 'needs_input')
        second_wait = self.draw(second, 102, 'needs_input')
        self.assertEqual(self.channel_positions(first_wait, 2), self.channel_positions(grown, 0))
        self.assertEqual(self.channel_positions(second_wait, 2), self.channel_positions(initial, 0))
        self.assertEqual(len(self.channel_positions(first_wait, 2)), 5)
        self.assertEqual(len(self.channel_positions(second_wait, 2)), 4)


if __name__ == '__main__':
    unittest.main()
