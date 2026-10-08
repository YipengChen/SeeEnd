"""Exercise actual matrix frames, rendering and firmware command handling."""

import ast
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from aiflame.devices.firmware import candle, matrix, matrix_map
from aiflame.devices import wifi_protocol
from aiflame.transports.base import DeviceTransport


class Bus:
    def __init__(self):
        self.bank = 0
        self.writes = []
        self.fail_pwm = False

    def scan(self):
        return [0x74]

    def writeto_mem(self, address, register, value):
        value = bytes(value)
        if register == 0x24 and self.fail_pwm:
            raise OSError("I2C fault")
        self.writes.append((self.bank, register, value))
        if register == 0xFD:
            self.bank = value[0]


class MatrixTests(unittest.TestCase):
    def test_calibrated_map_matches_observed_positions(self):
        self.assertTrue(matrix_map.CALIBRATED)
        self.assertEqual(set(matrix_map.CHANNEL_MAP), set(range(144)))
        for channel, row, column in ((0, 16, 1), (1, 15, 1), (8, 8, 1), (17, 15, 2), (143, 1, 9)):
            self.assertEqual(matrix_map.CHANNEL_MAP[(row - 1) * 9 + column - 1], channel)
        self.assertEqual(matrix_map.DISABLED_CHANNELS, (16, 127))

    def test_both_frames_initialized_and_hidden_frame_written_before_flip(self):
        bus = Bus()
        mapping = list(reversed(range(144)))
        panel = matrix.Matrix(bus, mapping, [143])
        panel.initialize()
        for frame in (0, 1):
            self.assertIn((frame, 0x12, bytes(18)), bus.writes)
            self.assertIn((frame, 0x24, bytes(144)), bus.writes)
            enabled = next(data for bank, reg, data in bus.writes if bank == frame and reg == 0)
            self.assertFalse(enabled[17] & 0x80)
        bus.writes.clear()
        panel.show([255] * 144)
        self.assertEqual(bus.writes[1], (1, 0x24, bytes([16] * 143 + [0])))
        self.assertEqual(bus.writes[-1], (0x0B, 0x01, b"\x01"))
        self.assertEqual(panel.frames_shown, 1)

    def test_failure_never_flips_incomplete_frame_and_attempts_shutdown(self):
        bus = Bus()
        panel = matrix.Matrix(bus, list(range(144)))
        panel.initialize()
        bus.writes.clear()
        bus.fail_pwm = True
        with self.assertRaises(OSError):
            panel.show([1] * 144)
        self.assertNotIn((0x0B, 0x01, b"\x01"), bus.writes)
        self.assertEqual(bus.writes[-1], (0x0B, 0x0A, b"\x00"))
        self.assertFalse(panel.ready)

    def test_invalid_maps_and_ambiguous_driver_rejected(self):
        for mapping in ([0] * 144, list(range(143)), [True] + list(range(1, 144)), [144] + list(range(1, 144))):
            with self.assertRaises(ValueError):
                matrix.Matrix(Bus(), mapping)
        bus = Bus()
        bus.scan = lambda: [0x74, 0x75]
        with self.assertRaises(ValueError):
            matrix.Matrix(bus, list(range(144)))


class CandleTests(unittest.TestCase):
    def settled(self, state, reduced=False):
        renderer = candle.Renderer()
        renderer.render(0, state, 25, reduced)
        return renderer, bytes(renderer.render(1000, state, 25, reduced))

    def test_working_is_smaller_dimmer_and_all_states_obey_pwm_budget(self):
        _, working = self.settled("working")
        _, done = self.settled("done")
        self.assertLess(sum(working), sum(done))
        self.assertLess(max(working), max(done))
        self.assertGreater(next(i // 9 for i, v in enumerate(working) if v), next(i // 9 for i, v in enumerate(done) if v))
        renderer = candle.Renderer()
        for state in ("working", "done", "error"):
            for t in range(0, 5000, 40):
                pixels = renderer.render(t, state, 100)
                self.assertEqual(len(pixels), 144)
                self.assertLessEqual(max(pixels), 16)
        self.assertEqual(bytes(renderer.render(6000, "idle", 25)), bytes(144))
        self.assertEqual(bytes(renderer.render(6001, "done", 0)), bytes(144))

    def test_flame_core_is_brighter_than_shoulders_and_outer_edge(self):
        _, pixels = self.settled("done", reduced=True)
        # Wide part of the flame: center, shoulder, outer edge.
        row = pixels[10 * 9:11 * 9]
        self.assertGreater(row[2], 0)
        self.assertGreaterEqual(row[4], row[3] * 2)
        self.assertGreaterEqual(row[4], row[2] * 8)
        self.assertEqual(row, row[::-1])

    def test_working_stays_small_and_upright_across_gust_cycles(self):
        renderer = candle.Renderer()
        renderer.render(0, "working", 25)
        for t in range(500, 64000, 80):
            pixels = renderer.render(t, "working", 25)
            self.assertGreater(sum(pixels), 0)
            height = max(14 - i // 9 for i, value in enumerate(pixels) if value)
            columns = {i % 9 for i, value in enumerate(pixels) if value}
            center = sum((i % 9) * value for i, value in enumerate(pixels)) / sum(pixels)
            self.assertGreaterEqual(height, 4)  # No gust-induced collapse.
            self.assertLessEqual(height, 5)
            self.assertLessEqual(len(columns), 4)
            self.assertLess(abs(center - 4), 0.25)

    def test_nonzero_low_brightness_never_quantizes_to_black(self):
        for brightness in range(1, 6):
            renderer = candle.Renderer()
            for t in range(0, 32000, 80):
                self.assertGreater(sum(renderer.render(t, "working", brightness)), 0)
            self.assertFalse(any(renderer.render(33000, "working", 0)))
            self.assertFalse(any(renderer.render(34000, "working", brightness, fade=0)))

    def test_working_shoulders_are_wider_with_same_height(self):
        _, pixels = self.settled("working", reduced=True)
        row = pixels[12 * 9:13 * 9]
        self.assertGreaterEqual(row[3], 2)
        self.assertGreaterEqual(row[5], 2)
        self.assertGreater(row[4], row[3])

    def test_reduced_is_static_and_normal_flame_moves_without_buffer_allocations(self):
        renderer, first = self.settled("done", True)
        buffer = renderer.pixels
        self.assertEqual(bytes(renderer.render(2000, "done", 25, True)), first)
        self.assertIs(renderer.render(3000, "done", 25, True), buffer)
        renderer, first = self.settled("done")
        self.assertNotEqual(bytes(renderer.render(2000, "done", 25)), first)
        self.assertEqual(bytes(renderer.render(3000, "done", 25, fade=0)), bytes(144))

    def test_upper_flame_has_visible_side_to_side_sway(self):
        renderer = candle.Renderer()
        renderer.render(0, "done", 25)
        centers = []
        for t in range(500, 32500, 40):
            upper = renderer.render(t, "done", 25)[:72]
            if sum(upper):
                centers.append(sum((i % 9) * value for i, value in enumerate(upper)) / sum(upper))
        self.assertGreater(max(centers) - min(centers), 2.5)


    def test_full_reference_has_sideways_folds_and_broken_up_plumes(self):
        renderer = candle.Renderer()
        renderer.render(0, "done", 25)
        folded = [0, 0, 0]
        split = [0, 0, 0]
        recovered = [False, False, False]
        for t in range(500, 76500, 40):
            frame = bytes(renderer.render(t, "done", 25))
            self.assertGreater(sum(frame), 0)
            self.assertTrue(any(frame[13 * 9:15 * 9]))
            self.assertLessEqual(max(frame), 16)
            points = [(i % 9, i // 9) for i, value in enumerate(frame) if value >= 2]
            height = max(y for x, y in points) - min(y for x, y in points) + 1
            width = max(x for x, y in points) - min(x for x, y in points) + 1
            # Three full bursts across a video-length run. A shorter upright
            # teardrop cannot satisfy either of these topology checks.
            for event, (start, end) in enumerate(((13000, 17000), (38000, 44000), (69000, 75000))):
                if start <= t < end:
                    if width > height and height <= 4:
                        folded[event] += 1
                    for y in range(3, 13):
                        row = frame[y * 9:y * 9 + 9]
                        if any(row[x] >= 3 and row[x + 1] <= 1 and max(row[x + 2:]) >= 3
                               for x in range(6)):
                            split[event] += 1
                            break
                if end <= t < end + 1000 and height >= 10:
                    recovered[event] = True
        self.assertTrue(all(count >= 8 for count in folded), folded)
        self.assertTrue(all(count >= 3 for count in split), split)
        self.assertTrue(all(recovered), recovered)

    def test_wind_geometry_is_continuous_even_during_fast_reversal(self):
        renderer = candle.Renderer()
        renderer.render(0, "done", 25)
        # Fast movement is intentional; test continuity at 1ms instead of
        # banning the reference's rapid shape changes over a full 40ms frame.
        for start in (12980, 14180, 14280, 14380, 15000, 16080):
            previous = bytes(renderer.render(start, "done", 25))
            for t in range(start + 1, start + 101):
                frame = bytes(renderer.render(t, "done", 25))
                self.assertLessEqual(sum(abs(a - b) for a, b in zip(frame, previous)), 12)
                previous = frame

    def test_switching_state_during_a_fold_preserves_transition_geometry(self):
        for before, after in (("done", "working"), ("working", "done")):
            renderer = candle.Renderer()
            renderer.render(0, before, 25)
            previous = bytes(renderer.render(13300, before, 25))
            first = bytes(renderer.render(13301, after, 25))
            self.assertLess(sum(abs(a - b) for a, b in zip(previous, first)), 12)
            renderer.render(13400, after, 25)
            # A second, interrupted transition must start from the current
            # interpolated wind participation, not from a hard-coded profile.
            previous = bytes(renderer.render(13401, after, 25))
            first = bytes(renderer.render(13402, before, 25))
            self.assertLess(sum(abs(a - b) for a, b in zip(previous, first)), 12)

    def test_reference_motion_does_not_repeat_every_eight_seconds(self):
        renderer = candle.Renderer()
        renderer.render(0, "done", 25)
        first = [bytes(renderer.render(t, "done", 25)) for t in range(1000, 9000, 80)]
        second = [bytes(renderer.render(t, "done", 25)) for t in range(9000, 17000, 80)]
        self.assertGreater(sum(a != b for a, b in zip(first, second)), 95)

    def test_render_motion_is_independent_of_frame_history(self):
        dense, sparse = candle.Renderer(), candle.Renderer()
        dense.render(0, "done", 25)
        sparse.render(0, "done", 25)
        for t in range(40, 47000, 40):
            dense.render(t, "done", 25)
        self.assertEqual(bytes(dense.render(47000, "done", 25)),
                         bytes(sparse.render(47000, "done", 25)))


class FirmwareCommandTests(unittest.TestCase):
    def setUp(self):
        self.bus = Bus()
        clock = types.SimpleNamespace(ticks_ms=lambda: 1000, ticks_diff=lambda a, b: a - b)
        machine = types.SimpleNamespace(Pin=Mock(), I2C=lambda *a, **k: self.bus, unique_id=lambda: b"board")
        neo = Mock()
        neo.__setitem__ = Mock()
        modules = {
            "time": clock, "machine": machine,
            "neopixel": types.SimpleNamespace(NeoPixel=lambda *a: neo),
            "network": types.SimpleNamespace(WLAN=Mock(), AP_IF=0, STA_IF=1),
            "select": types.SimpleNamespace(poll=Mock(), POLLIN=1),
            "candle": candle, "matrix": matrix,
            "matrix_map": types.SimpleNamespace(CALIBRATED=True, CHANNEL_MAP=tuple(range(144)), DISABLED_CHANNELS=()),
            "wifi_protocol": wifi_protocol,
            "version": __import__("aiflame.devices.firmware.version", fromlist=["FIRMWARE_VERSION"]),
        }
        tree = ast.parse(Path("aiflame/devices/firmware/main.py").read_text())
        loop = next(node.body[0] for node in tree.body if isinstance(node, ast.Try) and node.body and isinstance(node.body[0], ast.While))
        start = next(i for i, node in enumerate(loop.body) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "elapsed" for t in node.targets))
        self.frame_tick = compile(ast.Module(body=loop.body[start:-1], type_ignores=[]), "firmware-tick", "exec")
        # Execute boot setup and the production command functions, excluding
        # socket creation and the infinite hardware loop.
        index = next(i for i, node in enumerate(tree.body) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "tcp_listener" for t in node.targets))
        self.globals = {}
        with patch.dict(sys.modules, modules):
            exec(compile(ast.Module(body=tree.body[:index], type_ignores=[]), "firmware", "exec"), self.globals)
        self.events = []
        self.globals["output"] = lambda value, target=None: self.events.append((dict(value), len(self.bus.writes)))
        self.globals["command"]({"type": "hello", "hub_id": "hub"})

    def display(self, cid="one"):
        self.globals["command"]({
            "type": "display", "hub_id": "hub", "session_id": self.globals["SESSION"],
            "command_id": cid, "seq": 1, "state": "done", "effect": "standard",
            "brightness": 25, "duration_ms": 1000, "lease_ms": 30000,
        })

    def test_esp32_scan_pages_sorted_networks_without_changing_light(self):
        self.display()
        before = (self.globals["state"], self.globals["last_seq"], len(self.bus.writes))
        rows = [(('网' * 9 + str(i)).encode(), b"bssid", 6, -30-i, 3, False) for i in range(25)]
        rows += [(rows[0][0], b"other", 1, -90, 3, False), (b"", b"hidden", 1, -20, 3, True)]
        self.globals["wlan"].scan.return_value = rows
        request = {"type": "wifi_scan", "request_id": "scan", "hub_id": "hub", "session_id": self.globals["SESSION"]}
        results = []
        while True:
            self.globals["command"](request)
            packet = self.events[-1][0]
            self.assertLess(len(json.dumps(packet).encode()), 4096)
            results.extend(packet["networks"])
            if packet["done"]:
                break
            request["offset"] = packet["next"]
        self.assertEqual(len(results), 25)
        self.assertEqual([row["signal"] for row in results], sorted((row["signal"] for row in results), reverse=True))
        self.globals["wlan"].scan.assert_called_once()
        self.assertEqual((self.globals["state"], self.globals["last_seq"], len(self.bus.writes)), before)

    def test_scan_requires_session_and_preserves_state_on_radio_error(self):
        self.display()
        request = {"type": "wifi_scan", "request_id": "scan", "hub_id": "hub", "session_id": "wrong"}
        self.globals["command"](request)
        self.globals["wlan"].scan.assert_not_called()
        request["session_id"] = self.globals["SESSION"]
        self.globals["wlan"].scan.side_effect = OSError("busy")
        self.globals["command"](request)
        self.assertEqual(self.events[-1][0]["error"], "scan-failed")
        self.assertEqual(self.globals["state"], "done")

    def test_network_report_contains_name_and_status_but_no_password(self):
        self.globals["wifi_config"] = {"ssid": "Studio", "password": "private-password"}
        self.globals["wlan"].isconnected.return_value = True
        self.globals["command"]({"type": "hello", "hub_id": "hub"})
        hello = self.events[-1][0]
        self.assertEqual(hello["wifi_ssid"], "Studio")
        self.assertTrue(hello["wifi_connected"])
        self.assertNotIn("private-password", str(hello))
        self.globals["wlan"].isconnected.return_value = False
        self.globals["command"]({"type": "ping", "hub_id": "hub", "session_id": self.globals["SESSION"]})
        pong = self.events[-1][0]
        self.assertEqual(pong["wifi_ssid"], "Studio")
        self.assertFalse(pong["wifi_connected"])
        self.assertNotIn("private-password", str(pong))

    def test_ack_after_frame_commit_and_duplicate_does_not_restart_or_write(self):
        self.display()
        ack, writes = self.events[-1]
        self.assertEqual(ack["result"], "applied")
        self.assertEqual(self.bus.writes[writes - 1], (0x0B, 1, b"\x01"))
        self.globals["animation_ms"] = 2000
        self.display()
        self.assertEqual(self.events[-1][1], writes)
        self.assertEqual(self.globals["renderer"]._start_ms, 0)

    def test_i2c_error_is_not_applied_and_ping_still_works(self):
        self.bus.fail_pwm = True
        self.display()
        self.assertEqual(self.events[-1][0]["result"], "error")
        self.assertEqual(self.globals["last_seq"], -1)
        self.globals["command"]({"type": "ping", "hub_id": "hub", "session_id": self.globals["SESSION"]})
        self.assertEqual(self.events[-1][0]["type"], "pong")
        self.assertEqual(self.events[-1][0]["hardware_error"], "matrix-i2c")

    def test_hold_survives_duration_but_still_obeys_disconnect_lease(self):
        from aiflame.devices.firmware.version import FIRMWARE_VERSION
        self.assertEqual(self.events[0][0]["firmware_version"], FIRMWARE_VERSION)
        self.globals["command"]({
            "type": "display", "hub_id": "hub", "session_id": self.globals["SESSION"],
            "command_id": "held", "seq": 1, "state": "done", "effect": "standard",
            "brightness": 25, "duration_ms": 0, "lease_ms": 30000,
        })
        self.assertEqual(self.events[-1][0]["result"], "applied")
        self.globals.update(now=90000, contact=90000, last_tick=0)
        exec(self.frame_tick, self.globals)
        self.assertEqual(self.globals["state"], "done")
        self.globals.update(now=121000, last_tick=0)
        exec(self.frame_tick, self.globals)
        self.assertEqual(self.globals["state"], "idle")

    def test_expired_notification_or_lease_writes_black_frame(self):
        for state, now, contact in (("done", 2200, 2200), ("working", 32000, 1000)):
            with self.subTest(state=state):
                self.globals["cache"].clear()
                self.globals["last_seq"] = -1
                self.display(state)
                self.globals.update(state=state, now=now, contact=contact, began=1000, last_tick=0)
                exec(self.frame_tick, self.globals)
                self.assertEqual(self.globals["state"], "idle")
                self.assertEqual(next(data for _, reg, data in reversed(self.bus.writes) if reg == 0x24), bytes(144))

    def test_transient_panel_error_recovers_without_app_reconnect(self):
        self.display()
        self.bus.fail_pwm = True
        self.globals.update(now=1400, last_tick=1000)
        exec(self.frame_tick, self.globals)
        self.assertIsNone(self.globals["panel"])
        self.bus.fail_pwm = False
        self.globals.update(now=2500, contact=2500, duration=0, last_tick=1400)
        exec(self.frame_tick, self.globals)
        self.assertIsNotNone(self.globals["panel"])
        self.assertIsNone(self.globals["hardware_error"])
        self.assertEqual(self.globals["state"], "done")

    def test_periodic_i2c_failure_reports_fault_and_next_command_recovers(self):
        self.display()
        self.bus.fail_pwm = True
        self.globals.update(now=1400, last_tick=1000)
        exec(self.frame_tick, self.globals)
        self.assertEqual(self.events[-1][0]["type"], "hardware_error")
        self.globals["command"]({"type": "ping", "hub_id": "hub", "session_id": self.globals["SESSION"]})
        self.assertEqual(self.events[-1][0]["hardware_error"], "matrix-i2c")
        self.bus.fail_pwm = False
        self.globals["last_seq"] = -1
        self.display("recover")
        self.assertEqual(self.events[-1][0]["result"], "applied")
        self.assertIsNone(self.globals["hardware_error"])


class MatrixHandshakeTests(unittest.TestCase):
    def test_legacy_or_wrong_shape_prompts_usb_update(self):
        valid = {"capabilities": ["led-matrix-9x16", "mono-pwm"], "display_width": 9, "display_height": 16}
        DeviceTransport.validate_display(valid)
        for hello in ({}, {**valid, "display_width": 16}, {**valid, "capabilities": ["rgb-single"]}):
            with self.assertRaisesRegex(ValueError, "USB"):
                DeviceTransport.validate_display(hello)
