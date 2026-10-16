"""20 x 5 RGB frames for Cube Lite; no hardware or task mutations."""
from collections import deque
from functools import lru_cache
import math
import random
import threading

CUBE_EFFECTS = (
    ("status", "像素状态牌", "四格分别显示四个任务，位置保持稳定；空格熄灭，超过四个时等待空位，当前提醒优先显示。工作中左侧四点依次跳动，完成对勾，等待问号。"),
    ("comet", "彗星巡航", "工作中拖尾光点往返，完成时从中心展开，等待输入时两端交替。动画不代表完成百分比。"),
    ("snake", "贪吃蛇", "豆子使用已完成颜色。工作中随机吃豆，沿安全最短路线增长至满屏；待输入时停在豆子前，蛇头呼吸、豆子常亮；完成时吃豆，完成色从头传到尾，再盘起休息。蛇身长度不代表任务进度。"),
    ("text", "文字状态", "工作中显示 WORK，完成显示 DONE，等待输入显示 WAIT。文字沿用各状态的颜色与亮度。"),
)
CUBE_MODES = tuple(item[0] for item in CUBE_EFFECTS)
GLYPHS = {
    "done": ("00000", "00001", "00010", "10100", "01000"),
    "needs_input": ("01110", "10001", "00110", "00000", "00100"),

}


TEXT_STATES = {"working": "WORK", "done": "DONE", "needs_input": "WAIT"}
LETTERS = {
    "D": ("1110", "1001", "1001", "1001", "1110"),
    "O": ("0110", "1001", "1001", "1001", "0110"),
    "N": ("1001", "1101", "1011", "1001", "1001"),
    "E": ("1111", "1000", "1110", "1000", "1111"),
    "W": ("10001", "10001", "10101", "10101", "01010"),
    "R": ("1110", "1001", "1110", "1010", "1001"),
    "K": ("1001", "1010", "1100", "1010", "1001"),
    "A": ("0110", "1001", "1111", "1001", "1001"),
    "I": ("111", "010", "010", "010", "111"),
    "T": ("111", "010", "010", "010", "010"),
}


# A closed, non-crossing route over all 100 pixels. Every consecutive pair,
# including the wrap, shares an edge, so neither the head nor tail teleports.
SNAKE_PATH = tuple([(x, 0) for x in range(20)] + [
    (x, y) for x in range(19, -1, -1)
    for y in (range(1, 5) if x % 2 else range(4, 0, -1))])


SNAKE_INDEX = {point: index for index, point in enumerate(SNAKE_PATH)}


class SnakeGame:
    """Shortest safe shortcuts on a cycle, retaining an escape route at all sizes."""
    def __init__(self, seed=None):
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.reset()

    def reset(self):
        self.body = [SNAKE_PATH[-i % 100] for i in range(4)]
        self.step = 0
        self.full_ticks = 0
        self.spawn_food()

    def spawn_food(self):
        free = [point for point in SNAKE_PATH if point not in self.body]
        self.food = self.rng.choice(free) if free else None

    def next_position(self):
        head = SNAKE_INDEX[self.body[0]]
        tail_distance = (SNAKE_INDEX[self.body[-1]] - head) % 100
        food_distance = (SNAKE_INDEX[self.food] - head) % 100
        # Preserve circular body ordering. The forward free arc is an escape
        # corridor, even when nearly full; arbitrary shortest paths can seal it.
        # BFS finds the shortest route within this safe corridor. If the food
        # lies in a gap behind the body, follow the cycle until the tail frees it.
        if food_distance < tail_distance:
            queue = deque([self.body[0]])
            parents = {self.body[0]: None}
            while queue:
                point = queue.popleft()
                if point == self.food:
                    while parents[point] != self.body[0]:
                        point = parents[point]
                    return point
                x, y = point
                distance = (SNAKE_INDEX[point] - head) % 100
                for neighbor in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
                    if neighbor not in SNAKE_INDEX or neighbor in parents:
                        continue
                    forward = (SNAKE_INDEX[neighbor] - head) % 100
                    if distance < forward <= food_distance:
                        parents[neighbor] = point
                        queue.append(neighbor)
        return SNAKE_PATH[(head + 1) % 100]

    def advance(self):
        self.step += 1
        if self.food is None:
            self.full_ticks += 1
            if self.full_ticks >= 8:
                step = self.step
                self.reset()
                self.step = step
            return
        point = self.next_position()
        eating = point == self.food
        self.body.insert(0, point)
        if eating:
            self.spawn_food()
        else:
            self.body.pop()

    def at(self, step):
        with self.lock:
            if step < self.step:
                self.reset()
            while self.step < step:
                self.advance()
            return tuple(self.body), self.food


@lru_cache(maxsize=16)
def snake_game(key):
    return SnakeGame()


def snake_positions(elapsed, key='default'):
    return snake_game(key).at(max(0, int(elapsed * 4)))


# State vignettes use one continuous body: approach the food, then curl inward.
SNAKE_REST_ROUTE = ((12, 1), (11, 1), (10, 1), (9, 1), (8, 1), (7, 1),
                    (7, 2), (7, 3), (8, 3), (9, 3), (10, 3), (11, 3),
                    (12, 3), (12, 2), (11, 2), (10, 2))


def snake_done_positions(elapsed):
    elapsed = max(0, elapsed)
    body = [(x, 2) for x in range(10, -1, -1)]
    food = (12, 2)
    if elapsed >= .2:
        body = [(11, 2)] + body[:-1]
    if elapsed >= .4:
        body = [(12, 2)] + body
        food = None
    moves = min(len(SNAKE_REST_ROUTE), max(0, int((elapsed - 1.1) * 10)))
    for point in SNAKE_REST_ROUTE[:moves]:
        body = [point] + body[:-1]
    return tuple(body), food


def frame(desired, settings, elapsed):
    """Return device RGB bytes, rows bottom to top and columns left to right."""
    mode = settings.get("reminder_mode")
    state = desired.get("cube_state", desired["state"])
    pixels = [(0, 0, 0)] * 100
    if desired["state"] == "idle" or state not in TEXT_STATES:
        return bytes(300)

    def color(state, scale=1, palette_state=None):
        group = state if state in ("working", "done") else "needs_input"
        defaults = {"working": "#4A90E2", "done": "#52C788", "needs_input": "#FF6B6B"}
        palette = palette_state or group
        value = settings.get("colors", {}).get(palette, defaults[palette])
        rgb = int(value.lstrip('#'), 16)
        brightness = settings.get('state_brightness', {}).get(group, settings.get('brightness', 50))
        return tuple(round(((rgb >> shift) & 255) * max(0, min(100, brightness)) / 100 * scale)
                     for shift in (16, 8, 0))

    def dot(x, y, state, scale=1, palette_state=None):
        if 0 <= x < 20 and 0 <= y < 5:
            pixels[y * 20 + x] = color(state, scale, palette_state)

    def glyph(name, x, state):
        for y, row in enumerate(GLYPHS[name]):
            for col, value in enumerate(row):
                if value == '1':
                    dot(x + col, y, state)

    def status(state, x):
        if state == 'working':
            phase = int(elapsed * 3) % 4
            for i in range(4):
                dot(x + i, 2, state, 1 if i == phase else .2)
        else:
            glyph(state if state in GLYPHS else 'needs_input', x, state)

    if mode == 'text':
        word = TEXT_STATES.get(state, 'WAIT')
        width = sum(len(LETTERS[letter][0]) for letter in word) + len(word) - 1
        x = (20 - width) // 2
        for letter in word:
            for y, row in enumerate(LETTERS[letter]):
                for col, value in enumerate(row):
                    if value == '1':
                        dot(x + col, y, state)
            x += len(LETTERS[letter][0]) + 1
    elif mode == 'snake':
        if state == 'working':
            body, food = snake_positions(elapsed, desired.get('cube_animation_id', desired['key']))
            for offset, (x, y) in enumerate(body):
                dot(x, y, state, 1 if offset == 0 else .55 - .25 * offset / len(body))
            # Food stays steady until eaten.
            if food is not None:
                dot(*food, state, 1, palette_state='done')
        elif state == 'needs_input':
            # One empty pixel separates the nose and food: visibly waiting.
            for x in range(10):
                dot(x, 2, state, .25)
            breath = (1 - math.cos(elapsed * math.tau / 2.4)) / 2
            dot(10, 2, state, .4 + .6 * breath)
            dot(12, 2, state, .9, palette_state='done')
        elif state == 'done':
            body, food = snake_done_positions(elapsed)
            if food is not None:
                dot(*food, state, .8)
            resting = elapsed >= 2.7
            breath = .3 + .15 * (1 - math.cos(max(0, elapsed - 2.7) * math.tau / 3)) / 2
            for offset, (x, y) in enumerate(body):
                if resting:
                    scale = breath + (.15 if offset == 0 else 0)
                    dot(x, y, state, scale)
                else:
                    wave = max(0, min(1, (elapsed - .4 - offset * .04) / .16))
                    fade = max(0, min(1, (elapsed - 2.2) / .5))
                    target = 1 - fade * (.55 if offset == 0 else .7)
                    before = color(state, .35, palette_state='working')
                    after = color(state, target)
                    pixels[y * 20 + x] = tuple(round(a + (b - a) * wave)
                                               for a, b in zip(before, after))
        else:
            status(state, 7)
    elif mode == 'comet':
        if state == 'working':
            # Each tail point follows the head's earlier path. Flipping the
            # whole tail at a turn makes four columns disappear in one frame.
            trail = []
            for tail in range(5):
                distance = elapsed * 6 - tail
                travel = distance % 38
                position = distance if distance < 0 else travel if travel <= 19 else 38 - travel
                trail.append((position, (5 - tail) / 5))
            for x in range(20):
                scale = sum(weight * max(0, 1 - abs(x - position)) for position, weight in trail)
                dot(x, 2, state, min(1, scale))
        elif state == 'done':
            radius = min(10, int(elapsed * 12) + 1)
            for x in range(10 - radius, 10 + radius):
                for y in (1, 2, 3):
                    dot(x, y, state)
        elif state == 'needs_input':
            start = 0 if int(elapsed * 2) % 2 == 0 else 17
            for x in range(start, start + 3):
                for y in range(5):
                    dot(x, y, state)
    else:
        tasks = desired.get('cube_tasks', ((desired['key'], state), None, None, None))
        for slot, task in enumerate(tasks[:4]):
            if task is not None:
                status(task[1], slot * 5)
    # Glyph coordinates start at the visual top left; the physical Cube Lite
    # starts at the bottom left. Reverse rows only, preserving each RGB triplet.
    return bytes(channel for y in range(4, -1, -1)
                 for pixel in pixels[y * 20:(y + 1) * 20] for channel in pixel)
