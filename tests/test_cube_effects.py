"""Pixel geometry, animation, real task projection and persisted Cube modes."""
import threading
import tempfile
import unittest
from pathlib import Path

from aiflame.integrations.registry import SOURCES
from aiflame.core.engine import Engine
from aiflame.core.events import EventError, make_event
from aiflame.devices.cube_effects import (CUBE_MODES, frame, SnakeGame,
                                         snake_game, snake_positions, snake_waiting_positions,
                                         mini_snake_positions, mini_snake_game)
from aiflame.devices.light_settings import light_settings, validate_patch, migrate_light_settings
from aiflame.devices.models import YEELIGHT_CUBE_MODEL, YEELIGHT_BEAM_MODEL
from aiflame.devices.fleet import FleetDeviceService
from aiflame.transports.fleet import FleetTransportManager


class CubeEffectsTests(unittest.TestCase):
    def setUp(self):
        snake_game.cache_clear()
        mini_snake_game.cache_clear()
        self.settings = light_settings({'hardware_model': YEELIGHT_CUBE_MODEL})

    def draw(self, mode, state, elapsed=0, **extra):
        return frame({'key': state, 'state': state, **extra},
                     {**self.settings, 'reminder_mode': mode}, elapsed)

    def test_modes_are_cube_only_and_validate_before_save(self):
        for mode in CUBE_MODES:
            validate_patch({'reminder_mode': mode}, YEELIGHT_CUBE_MODEL)
            with self.assertRaises(EventError):
                validate_patch({'reminder_mode': mode}, YEELIGHT_BEAM_MODEL)
        with self.assertRaises(EventError):
            validate_patch({'reminder_mode': 'unsupported'}, YEELIGHT_CUBE_MODEL)

    def test_frames_stay_bounded_and_idle_or_muted_stays_black(self):
        for mode in CUBE_MODES:
            for state in ('working', 'done', 'needs_input', 'idle'):
                for elapsed in (0, .2, 1, 4, 100):
                    with self.subTest(mode=mode, state=state, elapsed=elapsed):
                        data = self.draw(mode, state, elapsed)
                        self.assertEqual(len(data), 300)
                        if state == 'idle':
                            self.assertEqual(data, bytes(300))
            for group in self.settings['state_brightness']:
                self.settings['state_brightness'][group] = 0
            self.assertEqual(self.draw(mode, 'working'), bytes(300))
            self.setUp()

    def test_animation_and_state_shapes_are_distinct(self):
        for mode in ('status', 'comet', 'snake'):
            self.assertNotEqual(self.draw(mode, 'working', 0), self.draw(mode, 'working', .5))
            self.assertNotEqual(self.draw(mode, 'needs_input'), self.draw(mode, 'done'))
            self.assertNotEqual(self.draw(mode, 'done'), self.draw(mode, 'working'))
        self.assertNotEqual(self.draw('comet', 'done', 0), self.draw('comet', 'done', 1))
        self.assertEqual(self.draw('comet', 'done', 1), self.draw('comet', 'done', 8))

    def test_waiting_respects_coral_and_custom_colors_in_every_pixel_mode(self):
        for color in ('#FF6B6B', '#ff6b6b', '#A78BFA'):
            self.settings['colors']['needs_input'] = color
            for brightness in (50, 100):
                self.settings['state_brightness']['needs_input'] = brightness
                expected = bytes(round(channel * brightness / 100) for channel in bytes.fromhex(color[1:]))
                for mode in CUBE_MODES:
                    with self.subTest(color=color, brightness=brightness, mode=mode):
                        data = self.draw(mode, 'needs_input', 1.2)
                        # Snake's frozen head reaches full intensity at 1.2 s;
                        # food intentionally keeps the completion color.
                        if mode == 'snake':
                            (x, y), *_ = snake_waiting_positions('needs_input')[0]
                            offset = ((4 - y) * 20 + x) * 3
                            self.assertEqual(data[offset:offset + 3], expected)
                        else:
                            lit = {data[i:i + 3] for i in range(0, len(data), 3) if any(data[i:i + 3])}
                            self.assertEqual(lit, {expected})

    def test_comet_moves_between_columns_without_rounding_the_head(self):
        start = self.draw('comet', 'working', 0)
        between = self.draw('comet', 'working', .05)
        self.assertNotEqual(start, between)
        self.assertTrue(any(between[(2 * 20) * 3:(2 * 20 + 1) * 3]))
        self.assertTrue(any(between[(2 * 20 + 1) * 3:(2 * 20 + 2) * 3]))

    def test_asymmetric_glyphs_use_bottom_first_hardware_rows(self):
        # Decode in physical wire order; these fixtures also catch horizontal
        # mirroring, which a symmetric fire or question mark alone would miss.
        for mode, state, expected in (
            ('status', 'needs_input', ('00100', '00000', '00110', '10001', '01110')),
            ('status', 'done', ('01000', '10100', '00010', '00001', '00000')),
        ):
            with self.subTest(mode=mode, state=state):
                data = self.draw(mode, state)
                rows = tuple(''.join('1' if any(data[(y * 20 + x) * 3:(y * 20 + x + 1) * 3])
                                     else '0' for x in range(5)) for y in range(5))
                self.assertEqual(rows, expected)

    def test_comet_tail_is_continuous_at_both_turns(self):
        for turn in (19 / 6, 38 / 6, 57 / 6):
            before = self.draw('comet', 'working', turn - .0001)
            after = self.draw('comet', 'working', turn + .0001)
            self.assertLessEqual(sum(abs(a - b) for a, b in zip(before, after)), 6)
            # At a turn the existing trail is still present inside the panel.
            side = range(15, 19) if turn == 19 / 6 else range(1, 5) if turn == 38 / 6 else range(15, 19)
            self.assertTrue(all(any(after[(2 * 20 + x) * 3:(2 * 20 + x + 1) * 3]) for x in side))

    def test_removed_modes_migrate_without_losing_custom_colors(self):
        self.assertEqual(set(CUBE_MODES), {'status', 'comet', 'text', 'snake'})
        for mode in ('tasks', 'flame'):
            record = {'hardware_model': YEELIGHT_CUBE_MODEL,
                      'light_settings': {'reminder_mode': mode, 'colors': {'done': '#123456'}}}
            self.assertEqual(light_settings(record)['reminder_mode'], 'status')
            migrate_light_settings(record)
            self.assertEqual(record['light_settings']['reminder_mode'], 'status')
            self.assertEqual(record['light_settings']['colors']['done'], '#123456')
            with self.assertRaises(EventError):
                validate_patch({'reminder_mode': mode}, YEELIGHT_CUBE_MODEL)

    def test_done_text_is_complete_upright_and_uses_completion_color(self):
        self.settings['colors']['done'] = '#123456'
        self.settings['state_brightness']['done'] = 100
        data = self.draw('text', 'done')
        expected = (
            '1110 0110 1001 1111 ',
            '1001 1001 1101 1000 ',
            '1001 1001 1011 1110 ',
            '1001 1001 1001 1000 ',
            '1110 0110 1001 1111 ',
        )
        for y, row in enumerate(expected):
            for x, bit in enumerate(row):
                offset = ((4 - y) * 20 + x) * 3
                self.assertEqual(data[offset:offset + 3], bytes.fromhex('123456') if bit == '1' else bytes(3))
        self.assertEqual(data, self.draw('text', 'done', 20))
        frames = {self.draw('text', state) for state in ('working', 'done', 'needs_input')}
        self.assertEqual(len(frames), 3)

    def test_snake_random_food_safe_movement_and_full_screen(self):
        first_foods = set()
        for seed in range(12):
            game = SnakeGame(seed)
            first_foods.add(game.food)
            for step in range(12000):
                old_body, old_food = tuple(game.body), game.food
                self.assertNotIn(old_food, old_body)
                game.advance()
                body = game.body
                self.assertEqual(len(set(body)), len(body))
                self.assertTrue(all(0 <= x < 20 and 0 <= y < 5 for x, y in body))
                for a, b in zip(body, body[1:]):
                    self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)
                self.assertEqual(body[1:], list(old_body[:len(body) - 1]))
                if body[0] == old_food:
                    self.assertEqual(len(body), len(old_body) + 1)
                else:
                    self.assertEqual(len(body), len(old_body))
                    self.assertEqual(game.food, old_food)
                if len(body) == 100:
                    self.assertIsNone(game.food)
                    break
            else:
                self.fail(f'Seed {seed} did not fill the screen')
            full = tuple(game.body)
            for _ in range(7):
                game.advance()
                self.assertEqual(tuple(game.body), full)
            game.advance()
            self.assertEqual(len(game.body), 4)
        self.assertGreater(len(first_foods), 8)

    def test_snake_takes_shortcut_and_rendering_is_stable_between_ticks(self):
        game = SnakeGame(1)
        # The straight route takes 3 steps versus 23 steps around the cycle.
        game.body = [(0, 0), (0, 1), (0, 2), (0, 3)]
        game.food = (2, 1)
        for _ in range(3):
            game.advance()
        self.assertEqual(game.body[0], (2, 1))
        self.assertEqual(len(game.body), 5)
        snapshot = game.at(3)
        self.assertEqual(game.at(3), snapshot)

    def test_snake_completion_immediately_shows_full_win_in_right_three_panels(self):
        self.settings['colors']['done'] = '#123456'
        self.settings['state_brightness']['done'] = 50
        final = self.draw('snake', 'done', 0)
        def right_panels(data):
            return b''.join(data[y * 60 + 15:(y + 1) * 60] for y in range(5))
        for elapsed in (.1, .2, .4, .8, 1.2, 1.3, 3, 4.2, 60):
            self.assertEqual(right_panels(final), right_panels(self.draw('snake', 'done', elapsed)))
        expected = ('10001' '01110' '10001',
                    '10001' '00100' '11001',
                    '10101' '00100' '10101',
                    '10101' '00100' '10011',
                    '01010' '01110' '10001')
        for y, row in enumerate(expected):
            for x, bit in enumerate(row):
                offset = ((4 - y) * 20 + 5 + x) * 3
                self.assertEqual(final[offset:offset + 3], bytes((9, 26, 43)) if bit == '1' else bytes(3))
        self.assertNotEqual(final, self.draw('snake', 'done', 3))
        body, food = mini_snake_positions(0, 'done')
        left_lit = {(x, y) for y in range(5) for x in range(5)
                    if any(final[((4 - y) * 20 + x) * 3:((4 - y) * 20 + x + 1) * 3])}
        self.assertEqual(left_lit, set(body) | {food})
        for tick in range(13):
            self.assertTrue(any(self.draw('snake', 'done', tick / 10)))
        self.assertEqual(self.draw('snake', 'done', -1), self.draw('snake', 'done', 0))

    def test_mini_snake_grows_inside_left_four_columns_then_restarts_when_full(self):
        previous = None
        sizes = set()
        full_at = None
        for tick in range(1000):
            body, food = mini_snake_positions(tick / 2)
            sizes.add(len(body))
            self.assertEqual(len(set(body)), len(body))
            self.assertNotIn(food, body)
            self.assertTrue(all(0 <= x < 4 and 0 <= y < 5 for x, y in body))
            if food is not None:
                self.assertTrue(0 <= food[0] < 4 and 0 <= food[1] < 5)
            for a, b in zip(body, body[1:]):
                self.assertEqual(abs(a[0] - b[0]) + abs(a[1] - b[1]), 1)
            if previous:
                old_body, old_food = previous
                if body[0] == old_food:
                    self.assertEqual(len(body), len(old_body) + 1)
                    self.assertEqual(body[1:], old_body)
                    self.assertNotEqual(food, old_food)
                else:
                    self.assertEqual(len(body), len(old_body))
                    self.assertEqual(body[1:], old_body[:-1])
                    self.assertEqual(food, old_food)
            previous = body, food
            if len(body) == 20:
                full_at = tick
                self.assertIsNone(food)
                break
        self.assertIsNotNone(full_at)
        self.assertEqual(sizes, set(range(4, 21)))
        for tick in range(full_at, full_at + 8):
            self.assertEqual(mini_snake_positions(tick / 2), previous)
        self.assertEqual(len(mini_snake_positions((full_at + 8) / 2)[0]), 4)

    def test_mini_snake_game_never_lights_separator_column_or_changes_win(self):
        initial = self.draw('snake', 'done', 0)
        for tick in range(160):
            data = self.draw('snake', 'done', tick / 2)
            for y in range(5):
                self.assertEqual(data[y * 60 + 12:y * 60 + 15], bytes(3))
                self.assertEqual(data[y * 60 + 15:(y + 1) * 60], initial[y * 60 + 15:(y + 1) * 60])

    def test_snake_attention_holds_position_and_breathes_with_state_brightness(self):
        def lit(data):
            return {i // 3 for i in range(0, 300, 3) if any(data[i:i + 3])}
        body, food = snake_positions(12, 'inherited', 'working')
        working = self.draw('snake', 'working', 12, cube_animation_id='inherited', cube_animation_phase='working')
        first = self.draw('snake', 'needs_input', 0, cube_animation_id='inherited', cube_animation_phase='waiting')
        later = self.draw('snake', 'needs_input', 1.3, cube_animation_id='inherited', cube_animation_phase='waiting')
        self.assertEqual(lit(first), lit(later))
        self.assertEqual(lit(first), lit(working))
        self.assertEqual(snake_waiting_positions('inherited', 'waiting'), (body, food))
        self.assertEqual(snake_game('inherited').step, 48)
        self.assertEqual(lit(first), lit(self.draw('snake', 'needs_input', 60,
                                                cube_animation_id='inherited', cube_animation_phase='waiting')))
        self.assertNotEqual(first, later)
        self.settings['state_brightness']['needs_input'] = 0
        self.settings['state_brightness']['done'] = 0
        for state in ('needs_input', 'done'):
            for elapsed in (0, .4, 1, 2, 3, 5):
                self.assertEqual(self.draw('snake', state, elapsed), bytes(300))

    def test_snake_wait_snapshot_refreshes_after_work_without_explicit_phase(self):
        first = snake_positions(1, 'implicit-phase')
        self.assertEqual(snake_waiting_positions('implicit-phase'), first)
        current = snake_positions(2, 'implicit-phase')
        self.assertNotEqual(first, current)
        self.assertEqual(snake_waiting_positions('implicit-phase'), current)
        self.assertEqual(snake_waiting_positions('implicit-phase'), current)

    def test_snake_food_uses_done_color_and_current_state_brightness(self):
        self.settings['colors'].update(working='#FF0000', done='#00FF00', needs_input='#0000FF')
        self.settings['state_brightness'].update(working=40, done=90, needs_input=20)
        for state, elapsed, food, value in (
            ('working', 0, None, 102),
            ('working', .3, None, 102),
            ('needs_input', 0, None, 46),
            ('needs_input', 1.3, None, 46),
        ):
            data = self.draw('snake', state, elapsed)
            x, y = (snake_waiting_positions('needs_input')[1] if state == 'needs_input'
                    else snake_positions(elapsed, 'working')[1])
            start = ((4 - y) * 20 + x) * 3
            self.assertEqual(data[start:start + 3], bytes((0, value, 0)))
        before = self.draw('snake', 'done', 0)
        after = self.draw('snake', 'done', 1.05)
        self.assertFalse(any(before[i] for i in range(0, 300, 3)))
        self.assertFalse(any(after[i] for i in range(0, 300, 3)))

    def fixture(self, directory):
        engine = Engine(':memory:', sources=SOURCES)
        self.addCleanup(engine.db.close)
        engine.update_settings({'hardware_notifications': True})
        record = {'device_id': 'cube', 'hardware_model': YEELIGHT_CUBE_MODEL,
                  'connection_paused': True, 'light_settings': {'reminder_mode': 'text'}}
        config = {'devices': {'cube': record}}
        fleet = FleetTransportManager(engine.flame, threading.RLock(), config)
        engine.flame.transport = fleet
        output = fleet.outputs['cube']
        output.device = {'online': True, 'session_id': 'one', 'connected_at': 1}
        return engine, fleet, output, FleetDeviceService(config, directory, fleet, fleet.lock)

    def test_current_attention_is_preserved_then_working_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, fleet, output, _ = self.fixture(directory)
            for task in ('a', 'b'):
                engine.accept(make_event('manual', task, 'working', 1), 'manual')
            engine.accept(make_event('manual', 'b', 'needs_input', 2), 'manual')
            desired = output.desired()
            self.assertEqual(desired['cube_state'], 'needs_input')
            self.assertNotIn('cube_tasks', desired)
            output.applied(desired, {'result': 'sent'})
            engine.flame.current['deadline'] = engine.flame.clock() - 1
            self.assertEqual(output.desired()['cube_state'], 'working')
            engine.stop()
            self.assertEqual(output.desired()['state'], 'idle')

    def test_pending_results_appear_without_premature_delivery_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, fleet, output, _ = self.fixture(directory)
            for task in ('a', 'b'):
                engine.accept(make_event('manual', task, 'working', 1), 'manual')
            output.desired()
            for task in ('a', 'b'):
                engine.accept(make_event('manual', task, 'done', 2), 'manual')
            desired = output.desired()
            self.assertEqual(desired['cube_state'], 'done')
            pending = engine.flame.queue[0]
            output.applied(desired, {'result': 'sent'})
            self.assertFalse(pending['applied'])
            self.assertIsNone(pending['deadline'])

    def test_projection_respects_target_and_reconnect_without_replaying_done(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, fleet, output, _ = self.fixture(directory)
            engine.active[('manual', 'elsewhere')] = {'id': 'elsewhere', 'device_id': 'other', 'until': None}
            engine.accept(make_event('manual', 'mine', 'working', 1), 'manual')
            self.assertEqual(output.desired()['state'], 'working')
            engine.accept(make_event('manual', 'mine', 'done', 2), 'manual')
            self.assertEqual(output.desired()['cube_state'], 'done')
            output.device = {'online': True, 'session_id': 'two', 'connected_at': 2}
            self.assertEqual(output.desired()['state'], 'idle')

    def test_four_status_slots_keep_task_positions_across_updates_and_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, fleet, output, _ = self.fixture(directory)
            output.record['light_settings']['reminder_mode'] = 'status'
            for task in ('a', 'b', 'c', 'd', 'e'):
                engine.accept(make_event('manual', task, 'working', 1), 'manual')
            tasks = output.desired()['cube_tasks']
            self.assertEqual([item[0][1] for item in tasks], list('abcd'))
            engine.accept(make_event('manual', 'b', 'needs_input', 2), 'manual')
            tasks = output.desired()['cube_tasks']
            self.assertEqual([item[0][1] for item in tasks], list('abcd'))
            self.assertEqual(tasks[1][1], 'needs_input')
            engine.accept(make_event('manual', 'e', 'needs_input', 2), 'manual')
            engine.accept(make_event('manual', 'b', 'working', 3), 'manual')
            tasks = output.desired()['cube_tasks']
            self.assertEqual([item[0][1] for item in tasks], list('abce'))
            self.assertEqual(tasks[3][1], 'needs_input')
            engine.accept(make_event('manual', 'a', 'cancelled', 2), 'manual')
            tasks = output.desired()['cube_tasks']
            self.assertEqual([item[0][1] for item in tasks], list('dbce'))
            engine.stop()
            self.assertEqual(output.desired()['state'], 'idle')
            self.assertEqual(output.cube_slots, [None] * 4)

    def test_status_slots_do_not_replay_old_session_or_ack_queued_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            engine, fleet, output, _ = self.fixture(directory)
            output.record['light_settings']['reminder_mode'] = 'status'
            for task in ('a', 'b', 'c'):
                engine.accept(make_event('manual', task, 'working', 1), 'manual')
            output.desired()
            for task in ('a', 'b'):
                engine.accept(make_event('manual', task, 'done', 2), 'manual')
            desired = output.desired()
            self.assertEqual([item[1] for item in desired['cube_tasks'] if item], ['done', 'done', 'working'])
            pending = engine.flame.queue[0]
            output.applied(desired, {'result': 'sent'})
            self.assertFalse(pending['applied'])
            self.assertIsNone(pending['deadline'])
            output.device = {'online': True, 'session_id': 'two', 'connected_at': 2}
            tasks = output.desired()['cube_tasks']
            self.assertEqual([item[1] for item in tasks if item], ['working'])

    def test_four_status_glyphs_fit_separate_five_column_cells(self):
        states = ('working', 'done', 'needs_input', 'working')
        tasks = tuple((str(i), state) for i, state in enumerate(states))
        combined = self.draw('status', 'working', cube_tasks=tasks)
        for slot, state in enumerate(states):
            single = self.draw('status', state)
            for row in range(5):
                start = (row * 20 + slot * 5) * 3
                self.assertEqual(combined[start:start + 15], single[row * 60:row * 60 + 15])
        blank = self.draw('status', 'working', cube_tasks=(None, None, None, ('a', 'done')))
        for row in range(5):
            self.assertEqual(blank[row * 60:row * 60 + 45], bytes(45))

    def test_all_modes_persist_and_color_settings_survive_switches(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            engine, fleet, output, service = self.fixture(directory)
            for mode in (*CUBE_MODES, 'front', 'back'):
                service.update_light_settings({'hardware_model': YEELIGHT_CUBE_MODEL, 'device_id': 'cube',
                                               'settings': {'reminder_mode': mode, 'colors': {'done': '#123456'}}})
                saved = json.loads((Path(directory) / 'credentials.json').read_text())
                settings = light_settings(saved['devices']['cube'])
                self.assertEqual(settings['reminder_mode'], mode)
                self.assertEqual(settings['colors']['done'], '#123456')
