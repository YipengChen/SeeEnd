"""20 x 5 RGB frames for Cube Lite; no hardware or task mutations."""
from collections import deque
from functools import lru_cache
import math
import random
import threading

CUBE_EFFECTS = (
    ("status", "像素状态牌", "四格分别显示四个任务，位置保持稳定；空格熄灭，超过四个时等待空位，当前提醒优先显示。工作中左侧四点依次跳动，完成对勾，等待问号。"),
    ("comet", "彗星巡航", "工作中拖尾光点往返，完成时从中心展开，等待输入时两端交替。动画不代表完成百分比。"),
    ("snake", "贪吃蛇", "豆子使用已完成颜色。工作中随机吃豆，沿安全最短路线增长至满屏；待输入时保留当前蛇身与豆子位置，原地暂停、蛇头呼吸；完成时立即显示迷你贪吃蛇和豆子仅在左格的左四列活动，吃豆后蛇身增长，填满后重新开始；右三格显示 WIN 并常亮。蛇身长度不代表任务进度。"),
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
    def __init__(self, seed=None, path=SNAKE_PATH):
        self.path = path
        self.index = {point: index for index, point in enumerate(path)}
        self.size = len(path)
        self.rng = random.Random(seed)
        self.lock = threading.RLock()
        self.phase = None
        self.phase_step = 0
        self.waiting_phase = object()
        self.waiting_snapshot = None
        self.reset()

    def reset(self):
        self.body = [self.path[-i % self.size] for i in range(min(4, self.size - 1))]
        self.step = 0
        self.full_ticks = 0
        self.spawn_food()

    def spawn_food(self):
        free = [point for point in self.path if point not in self.body]
        self.food = self.rng.choice(free) if free else None

    def next_position(self):
        head = self.index[self.body[0]]
        tail_distance = (self.index[self.body[-1]] - head) % self.size
        food_distance = (self.index[self.food] - head) % self.size
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
                distance = (self.index[point] - head) % self.size
                for neighbor in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
                    if neighbor not in self.index or neighbor in parents:
                        continue
                    forward = (self.index[neighbor] - head) % self.size
                    if distance < forward <= food_distance:
                        parents[neighbor] = point
                        queue.append(neighbor)
        return self.path[(head + 1) % self.size]

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


def snake_positions(elapsed, key='default', phase=None):
    game = snake_game(key)
    with game.lock:
        if phase is None:
            # Callers without explicit phase IDs signal resume by rendering
            # work again; the next wait must freeze this newer game state.
            game.waiting_snapshot = None
        if phase != game.phase:
            game.phase, game.phase_step = phase, game.step
        return game.at(game.phase_step + max(0, int(elapsed * 4)))


def snake_waiting_positions(key='default', phase=None):
    game = snake_game(key)
    with game.lock:
        if game.waiting_snapshot is None or phase != game.waiting_phase:
            game.waiting_phase = phase
            game.waiting_snapshot = (tuple(game.body), game.food)
        return game.waiting_snapshot


# The completion scene reserves the first panel for a growing mini snake.
# W, I and N each occupy their own 5 x 5 panel, without crossing a seam.
SNAKE_WIN_GLYPHS = (
    LETTERS['W'],
    ('01110', '00100', '00100', '00100', '01110'),
    ('10001', '11001', '10101', '10011', '10001'),
)
# Leave column 4 dark to separate the mini game from the W panel.
MINI_SNAKE_PATH = tuple([(x, 0) for x in range(4)] + [
    (x, y) for x in range(3, -1, -1)
    for y in (range(1, 5) if x % 2 else range(4, 0, -1))])


@lru_cache(maxsize=16)
def mini_snake_game(key):
    return SnakeGame(0, path=MINI_SNAKE_PATH)


def mini_snake_positions(elapsed, key='default'):
    step = max(0, int(elapsed * 2))
    game = mini_snake_game(key)
    with game.lock:
        if step < game.step:
            # Replaying a completion scene must reproduce its initial frame.
            game.rng.seed(0)
            game.reset()
        return game.at(step)


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
        animation_id = desired.get('cube_animation_id', desired['key'])
        phase = desired.get('cube_animation_phase')
        if state == 'working':
            body, food = snake_positions(elapsed, animation_id, phase)
            for offset, (x, y) in enumerate(body):
                dot(x, y, state, 1 if offset == 0 else .55 - .25 * offset / len(body))
            # Food stays steady until eaten.
            if food is not None:
                dot(*food, state, 1, palette_state='done')
        elif state == 'needs_input':
            body, food = snake_waiting_positions(animation_id, phase)
            for x, y in body[1:]:
                dot(x, y, state, .25)
            breath = (1 - math.cos(elapsed * math.tau / 2.4)) / 2
            dot(*body[0], state, .4 + .6 * breath)
            if food is not None:
                dot(*food, state, .9, palette_state='done')
        elif state == 'done':
            body, food = mini_snake_positions(max(0, elapsed), animation_id)
            for offset, (x, y) in enumerate(body):
                dot(x, y, state, 1 if offset == 0 else .4)
            if food is not None:
                dot(*food, state, .85)
            for panel, rows in enumerate(SNAKE_WIN_GLYPHS, start=1):
                for y, row in enumerate(rows):
                    for x, value in enumerate(row):
                        if value == '1':
                            dot(panel * 5 + x, y, state)
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
