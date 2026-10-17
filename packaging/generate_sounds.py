"""Reproduce the original chime and pop; cat.wav is a licensed field recording."""

from array import array
import math
from pathlib import Path
import sys
import wave

RATE = 24000
ROOT = Path(__file__).resolve().parents[1] / "aiflame/assets/sounds"


def save(name, samples):
    peak = max(abs(value) for value in samples) or 1
    data = array("h", (round(value / peak * 14000) for value in samples))
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(ROOT / (name + ".wav")), "wb") as output:
        output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes(data.tobytes())


def chime(t):
    value = 0
    for start, frequency in ((0, 659.25), (0.16, 987.77)):
        age = t - start
        if age >= 0:
            value += (1 - math.exp(-age * 180)) * math.exp(-age * 6) * (
                math.sin(math.tau * frequency * age)
                + 0.18 * math.sin(math.tau * frequency * 2 * age))
    return value * min(1, max(0, (1.1 - t) / 0.08))


def pop(t):
    return math.sin(math.tau * (880 * t - 560 * t * t)) * (
        1 - math.exp(-t * 300)) * math.exp(-t * 18)



if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    save("chime", [chime(i / RATE) for i in range(int(1.1 * RATE))])
    save("pop", [pop(i / RATE) for i in range(int(0.38 * RATE))])
