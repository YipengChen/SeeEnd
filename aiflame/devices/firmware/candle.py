"""Small monochrome candle renderer; no hardware or wall-clock dependency.

render(now_ms, state, brightness, reduced, fade=1) returns the same model-sized
row-major buffer each call. now_ms is an unwrapped, monotonic elapsed clock.
The caller must send/copy it before the next render. brightness is 0..25.
"""
from math import sin, exp
WIDTH = 9
HEIGHT = 16
MAX_PWM = 16
TRANSITION_MS = 400


def _clamp(value, lower, upper):
    return min(upper, max(lower, value))


def _lattice(index):
    value = (index * 1664525 + 1013904223) & 0xffffffff
    value = ((value ^ (value >> 16)) * 2246822519) & 0xffffffff
    return (value & 65535) / 32767.5 - 1.0


# Build once: MicroPython's large-integer hash arithmetic must stay out of the
# frame loop. Each noise band traverses this table at a different rate.
_NOISE = tuple(_lattice(i) for i in range(256))


def _noise(position, seed):
    """Smooth deterministic value noise; no random state or frame-rate dependence."""
    cell = int(position)
    blend = position - cell
    blend = blend * blend * (3.0 - 2.0 * blend)
    left = _NOISE[(cell + seed) & 255]
    right = _NOISE[(cell + seed + 1) & 255]
    return left + (right - left) * blend


# The full reference shows a three-second wind burst: collapse, spreading
# rebound, a second sideways fold, then regrowth (13s, 40s and 72s).
# Time, relative height, lateral bend in columns, broken-up plume strength.
_WIND_KEYS = (
    (0.00, 1.00,  0.0, 0.00),
    (0.18, 0.25, -0.5, 0.15),
    (0.52, 0.23,  0.0, 0.10),
    (0.78, 0.35, -3.4, 0.10),
    (0.96, 0.90, -2.6, 0.15),
    (1.12, 0.80,  1.0, 0.65),
    (1.30, 0.55,  2.8, 1.00),
    (1.44, 0.70, -3.3, 0.95),
    (1.64, 0.90, -1.8, 0.45),
    (1.84, 0.85,  2.7, 0.25),
    (2.04, 0.25,  3.2, 0.35),
    (2.32, 0.24,  1.7, 0.30),
    (2.55, 0.25, -0.8, 0.10),
    (2.70, 0.40,  1.0, 0.70),
    (2.86, 1.00, -1.4, 0.15),
    (3.10, 1.00,  0.0, 0.00),
)


def _wind_shape(t):
    # Calm intervals vary by several seconds; the first burst starts at 13s.
    # Local event time keeps both ends neutral, including a slot boundary.
    event = int(t / 29.0)
    start = 13.0 + 29.0 * event + 2.2 * (_NOISE[event & 255] - _NOISE[0])
    speed = 1.0 + 0.12 * (_NOISE[(event + 37) & 255] - _NOISE[37])
    phase = (t - start) / speed
    if phase <= 0.0 or phase >= 3.10:
        return 1.0, 0.0, 0.0
    mirror = 1.0 if event % 2 == 0 else -1.0
    left = _WIND_KEYS[0]
    for right in _WIND_KEYS:
        if phase <= right[0]:
            blend = (phase - left[0]) / (right[0] - left[0])
            blend = blend * blend * (3.0 - 2.0 * blend)
            return (left[1] + (right[1] - left[1]) * blend,
                    mirror * (left[2] + (right[2] - left[2]) * blend),
                    left[3] + (right[3] - left[3]) * blend)
        left = right
    return 1.0, 0.0, 0.0


def _profile(state):
    if state in ("working",):
        return (5.5, 0.30, 0.18, 0.0)
    if state in ("done",):
        return (12.0, 1.0, 1.0, 1.0)
    if state in ("error",):
        return (7.0, 0.60, 0.55, 1.0)
    return (7.0, 0.0, 0.0, 0.0)


# key, Chinese label, minimum, maximum, step, default, group
TUNING_FIELDS = (
    ('height', '火焰高度', 2, 15, .1, 12, '形状'),
    ('width', '火焰宽度', .25, 2, .01, 1, '形状'),
    ('center', '水平位置', 1, 7, .1, 4, '形状'),
    ('base', '底部位置', 10, 15, .1, 14.2, '形状'),
    ('tip', '尖端收束', .2, 2, .01, .72, '形状'),
    ('taper', '上部收窄', 0, .9, .01, .52, '形状'),
    ('peak', '火芯强度', .05, 1, .01, 1, '明暗'),
    ('brightness', '总亮度（%）', 0, 25, 1, 20, '明暗'),
    ('edge', '边缘柔化', .1, 1, .01, .45, '明暗'),
    ('vertical', '顶部亮度衰减', 0, .9, .01, .22, '明暗'),
    ('flicker', '亮度闪烁', 0, .5, .01, .04, '明暗'),
    ('flicker_speed', '闪烁频率', .1, 8, .1, 2.3, '明暗'),
    ('speed', '整体速度', .1, 3, .05, 1, '运动'),
    ('motion', '运动幅度', 0, 2, .01, 1, '运动'),
    ('sway', '慢摆幅度', 0, 5, .05, 2.65, '运动'),
    ('sway_speed', '慢摆频率', .1, 4, .01, .83, '运动'),
    ('fast_sway', '快摆幅度', 0, 2, .01, .65, '运动'),
    ('fast_speed', '快摆频率', .1, 8, .1, 2.1, '运动'),
    ('curl', '弯曲幅度', 0, 3, .05, .85, '运动'),
    ('curl_speed', '弯曲频率', .1, 5, .01, 1.47, '运动'),
    ('flutter', '尖端抖动', 0, 3, .05, 1, '运动'),
    ('flutter_speed', '尖端抖动频率', .1, 10, .1, 3.7, '运动'),
    ('height_noise', '高度起伏', 0, 3, .05, .85, '运动'),
    ('ripple', '轮廓波动', 0, .4, .01, .1, '运动'),
    ('ripple_speed', '轮廓流动速度', .1, 20, .1, 7.3, '运动'),
    ('wind', '阵风强度', 0, 1, .01, 1, '阵风'),
    ('wind_speed', '阵风节奏', .25, 4, .05, 1, '阵风'),
    ('scatter', '碎焰强度', 0, 1.5, .05, 1, '阵风'),
)


def validate_tuning(values):
    if not isinstance(values, dict):
        raise ValueError('tuning-object')
    known = {field[0]: field for field in TUNING_FIELDS}
    result = {}
    for key, value in values.items():
        if key not in known or type(value) not in (int, float):
            raise ValueError('tuning-field')
        field = known[key]
        if not field[2] <= value <= field[3]:
            raise ValueError('tuning-range:' + key)
        result[key] = value
    return result


class Renderer:
    def __init__(self, width=WIDTH, height=HEIGHT):
        if (width, height) not in ((9, 16), (7, 15)):
            raise ValueError("Unsupported display dimensions")
        self.output = None if (width, height) == (WIDTH, HEIGHT) else bytearray(width * height)
        self.samples = []
        if self.output is not None:
            for y in range(height):
                sy = y * (HEIGHT - 1) / (height - 1)
                y0 = int(sy)
                fy = sy - y0
                for x in range(width):
                    sx = x * (WIDTH - 1) / (width - 1)
                    x0 = int(sx)
                    fx = sx - x0
                    self.samples.append((y0 * WIDTH + x0, y0 * WIDTH + min(x0 + 1, WIDTH - 1),
                                         min(y0 + 1, HEIGHT - 1) * WIDTH + x0,
                                         min(y0 + 1, HEIGHT - 1) * WIDTH + min(x0 + 1, WIDTH - 1), fx, fy))
        self.tuning = {}
        self.pixels = bytearray(WIDTH * HEIGHT)
        self._zero = bytes(WIDTH * HEIGHT)
        self._coverage = [0.0] * WIDTH
        self._state = None
        self._start_ms = 0
        self._from = (7.0, 0.0, 0.0, 0.0)
        self._target = self._from
        self._current = self._from

    def render(self, now_ms, state, brightness, reduced=False, fade=1):
        pixels = self._render_native(now_ms, state, brightness, reduced, fade)
        if self.output is None:
            return pixels
        # Sample the same normalized flame into the smaller physical matrix.
        # Indices and weights are precomputed; no allocations in the frame loop.
        brightest = 0.0
        brightest_index = 0
        any_lit = False
        for i, (a, b, c, d, fx, fy) in enumerate(self.samples):
            top = pixels[a] * (1 - fx) + pixels[b] * fx
            bottom = pixels[c] * (1 - fx) + pixels[d] * fx
            value = top * (1 - fy) + bottom * fy
            self.output[i] = min(MAX_PWM, int(value + 0.5))
            any_lit = any_lit or self.output[i] > 0
            if value > brightest:
                brightest, brightest_index = value, i
        if not any_lit and brightest > 0 and fade >= 1:
            self.output[brightest_index] = 1
        return self.output

    def _render_native(self, now_ms, state, brightness, reduced=False, fade=1):
        if state != self._state:
            self._from = self._current
            self._target = _profile(state)
            self._start_ms = now_ms
            self._state = state
            # First frame has visible intensity even when waking from darkness.
            if self._from[1] == 0 and self._target[1] > 0:
                self._from = (self._from[0], min(0.12, self._target[1]), 0.0, 0.0)
        progress = _clamp((now_ms - self._start_ms) / TRANSITION_MS, 0.0, 1.0)
        progress = progress * progress * (3.0 - 2.0 * progress)
        height = self._from[0] + (self._target[0] - self._from[0]) * progress
        peak = self._from[1] + (self._target[1] - self._from[1]) * progress
        motion = self._from[2] + (self._target[2] - self._from[2]) * progress
        wind_weight = self._from[3] + (self._target[3] - self._from[3]) * progress
        self._current = (height, peak, motion, wind_weight)
        scale = MAX_PWM * _clamp(brightness, 0, 25) / 25.0 * _clamp(fade, 0.0, 1.0)
        # Dark states and explicit zero brightness switch off immediately.
        if self._target[1] == 0 or scale <= 0:
            if self._target[1] == 0:
                self._current = self._target
            for i in range(WIDTH * HEIGHT):
                self.pixels[i] = 0
            return self.pixels
        p = self.tuning
        if p:
            height = p.get('height', height)
            peak = p.get('peak', peak)
            motion = p.get('motion', motion)
            wind_weight = p.get('wind', wind_weight)
        t = now_ms / 1000.0 * p.get('speed', 1)
        # Reference candle: a persistent body, irregular lean/recovery and a
        # quicker tip. Independent smooth noise bands avoid a pendulum loop or
        # the old scheduled near-extinction every eight seconds.
        wind = curl = flutter = gust = scatter = 0.0
        if not reduced:
            wind = p.get('sway', 2.65) * _noise(t * p.get('sway_speed', .83), 17) + p.get('fast_sway', .65) * _noise(t * p.get('fast_speed', 2.1), 91)
            curl = p.get('curl', .85) * _noise(t * p.get('curl_speed', 1.47), 43)
            flutter = p.get('flutter', 1) * _noise(t * p.get('flutter_speed', 3.7), 131)
            if wind_weight > 0.0:
                relative_height, bend, scatter = _wind_shape(t * p.get('wind_speed', 1))
                gust = (1.0 - relative_height) * wind_weight
                scatter *= wind_weight * p.get('scatter', 1)
                bend *= wind_weight
                height *= 1.0 - gust
                # Suppress ordinary sway as the wind takes over the silhouette.
                wind = wind * (1.0 - min(1.0, gust * 2.0)) + bend
            height += motion * (1.0 - gust) * (p.get('height_noise', .85) * _noise(t * 1.13, 67)
                                + 0.28 * flutter - 0.18 * abs(wind))
            peak *= 0.94 + p.get('flicker', .04) * _noise(t * p.get('flicker_speed', 2.3), 113)
            if state in ("error",):
                phase = t % 4.0
                peak *= 1.0 - 0.65 * (exp(-((phase - 0.7) / 0.28) ** 2) + exp(-((phase - 1.65) / 0.28) ** 2))
        # Preserve the identical four subpixel samples, but accumulate rows.
        # Hoist scale/divisors and skip samples beyond the flame boundary.
        pixels = self.pixels
        pixels[:] = self._zero
        coverage = self._coverage
        gain = 0.25 * peak * scale
        height = max(.5, height)
        inverse_height = 1.0 / height
        sine = sin
        ripple_phase = t * p.get('ripple_speed', 7.3)
        base = p.get('base', 14.2)
        first_y = int(base - .25 - height)
        if first_y < 0:
            first_y = 0
        brightest = 0.0
        brightest_index = 0
        any_lit = False
        for y in range(first_y, 15):
            for x in range(9):
                coverage[x] = 0.0
            row = y * 9
            for sample in range(2):
                rise = base + .25 - y if sample == 0 else base - .25 - y
                u = rise * inverse_height
                if u < 0.0 or u >= 1.0:
                    continue
                center = p.get('center', 4.0)
                if not reduced:
                    # Motion grows up the flame; independent curvature lets
                    # the tip catch up while the lower body holds its position.
                    center += motion * u * (wind * (0.40 + 0.60 * u)
                                            + curl * (u - 0.35)
                                            + 0.22 * flutter * u * u)
                radius = 0.26 + 2.35 * (sine(3.14159265 * u) ** p.get('tip', .72)) * (1.0 - p.get('taper', .52) * u)
                radius *= p.get('width', 1)
                radius *= 1.05 - 0.05 * wind_weight + 0.60 * gust
                if scatter > 0.0:
                    # Torn, independently moving upper contour; the base stays
                    # connected while the upper core thins and opens into wisps.
                    center += scatter * u * 0.80 * sine(t * 23.0 - u * 11.0)
                    radius *= 1.0 - 0.40 * scatter * u
                ripple = 0.0
                if not reduced:
                    ripple = sine(ripple_phase - u * 5.0)
                    # A travelling small constriction and swelling, strongest
                    # above the bright base, keep the outline alive as it leans.
                    radius *= 1.0 + motion * (p.get('ripple', .10) * ripple
                                              + 0.06 * flutter) * u
                lower = rise / 0.65
                if lower > 1.0:
                    lower = 1.0
                upper = (height - rise) / 0.7
                if upper > 1.0:
                    upper = 1.0
                vertical = lower * upper * (1.0 - p.get('vertical', .22) * u)
                if not reduced:
                    vertical *= 1.0 + 0.09 * motion * ripple * u
                if scatter > 0.0:
                    vertical *= 1.0 - 0.60 * scatter * u
                inv_radius = 1.0 / (radius + 0.01)
                softness = p.get('edge', .45)
                edge_limit = radius + softness
                edge_gain = 1 / (2 * softness)
                first_x = int(center - radius - softness - .25)
                last_x = int(center + radius + softness + .25) + 1
                if first_x < 0:
                    first_x = 0
                if last_x > 9:
                    last_x = 9
                for x in range(first_x, last_x):
                    # Unroll horizontal samples to avoid tuple iteration and
                    # function calls in the hottest loop.
                    distance = x - 0.25 - center
                    if distance < 0.0:
                        distance = -distance
                    edge = (edge_limit - distance) * edge_gain
                    if edge > 0.0:
                        if edge > 1.0:
                            edge = 1.0
                        core = 1.0 - distance * inv_radius
                        if core < 0.0:
                            core = 0.0
                        coverage[x] += vertical * edge * (0.08 + 0.92 * core * (0.30 + 0.70 * core))
                    distance = x + 0.25 - center
                    if distance < 0.0:
                        distance = -distance
                    edge = (edge_limit - distance) * edge_gain
                    if edge > 0.0:
                        if edge > 1.0:
                            edge = 1.0
                        core = 1.0 - distance * inv_radius
                        if core < 0.0:
                            core = 0.0
                        coverage[x] += vertical * edge * (0.08 + 0.92 * core * (0.30 + 0.70 * core))
            for x in range(9):
                intensity = coverage[x] * gain
                if intensity > brightest:
                    brightest, brightest_index = intensity, row + x
                value = int(intensity + 0.5)
                pixels[row + x] = 16 if value > 16 else value
                any_lit = any_lit or value > 0
        if scatter > 0.04:
            # Separate advected tongues, rather than simply widening one blob.
            # These bounded ellipses run only during a burst; no per-pixel exp.
            self._wisp(4.0 - wind * 0.55 + 0.35 * flutter,
                       14.0 - height * 0.67, 1.35, 2.0,
                       -0.45 * wind, peak * scale * scatter * 0.85)
            self._wisp(4.0 + wind * 0.70,
                       14.0 - height * 0.94, 0.95, 1.30,
                       0.25 * wind, peak * scale * scatter * 0.50)
        # A nonzero user brightness must not quantize the whole flame to black.
        # Let the disconnect fade reach darkness naturally.
        if not any_lit and brightest > 0 and fade >= 1:
            pixels[brightest_index] = 1
        return pixels

    def _wisp(self, cx, cy, rx, ry, lean, gain):
        pixels = self.pixels
        for y in range(max(0, int(cy - ry)), min(15, int(cy + ry) + 2)):
            dy = (y - cy) / ry
            vertical = 1.0 - dy * dy
            if vertical <= 0.0:
                continue
            center = cx + lean * dy
            for x in range(max(0, int(center - rx)), min(9, int(center + rx) + 2)):
                dx = (x - center) / rx
                coverage = vertical - dx * dx
                if coverage > 0.0:
                    value = int(gain * coverage * coverage + 0.5)
                    index = y * 9 + x
                    pixels[index] = min(MAX_PWM, pixels[index] + value)
