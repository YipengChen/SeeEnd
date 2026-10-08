"""AI Flame 0.2, front view: 7 columns x 15 rows.

Same orientation as 0.1: columns left to right, each bottom to top.
Channels 0..104 are consecutive in groups of 15; 105..143 stay disabled.
No 0.1 board-specific dark channels are inherited.
"""
MODEL = "ai-flame-v02"
WIDTH = 7
HEIGHT = 15
CALIBRATED = True
CHANNEL_MAP = tuple(x * HEIGHT + HEIGHT - 1 - y for y in range(HEIGHT) for x in range(WIDTH))
DISABLED_CHANNELS = ()
