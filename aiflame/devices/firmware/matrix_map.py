"""Calibrated warm LED board, front view: 9 columns and 16 rows.

Coordinates x=0..8 left to right, y=0..15 top to bottom.
Observed: ch0=(16,1), ch1=(15,1), ch8=(8,1), ch17=(15,2), ch143=(1,9).
Each physical column runs bottom to top with 16 consecutive channels.
Dark points confirmed at (16,2) => ch16 and (1,8) => ch127.
"""
MODEL = "ai-flame-v01"
WIDTH = 9
HEIGHT = 16
CALIBRATED = True
CHANNEL_MAP = tuple(x * 16 + 15 - y for y in range(16) for x in range(9))
DISABLED_CHANNELS = (16, 127)
