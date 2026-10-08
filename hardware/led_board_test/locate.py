"""Hold selected IS31FL3731 channels on for assisted physical mapping.

python hardware/led_board_test/locate.py --port /dev/cu.usbserial-10 --channels 0
Coordinates are observed from the front: x=0..8 left to right, y=0..15 top to bottom.
This only runs a temporary REPL command; no device files or pairing data are changed.
"""
import argparse
from pathlib import Path
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--port", required=True)
parser.add_argument("--channels", default="", help="comma-separated channel IDs, 0..143; empty turns all off")
args = parser.parse_args()
channels = [int(value) for value in args.channels.split(",") if value.strip()]
if any(not 0 <= channel < 144 for channel in channels):
    parser.error("channel must be 0..143")
source = Path(__file__).with_name("main.py").read_text().rsplit("\nmain()", 1)[0]
code = source + "\nbus=I2C(0,sda=Pin(21),scl=Pin(18),freq=100000,timeout=100000)\n"
code += "addresses=[a for a in bus.scan() if 0x74<=a<=0x77]\nassert len(addresses)==1, 'Expected one LED driver'\n"
code += "board=LEDBoard(bus,addresses[0]); board.initialize()\n"
for channel in channels:
    code += "board.write(%d, 16)\n" % (0x24 + channel)
code += "print('Locator channels:', %r)\n" % channels
subprocess.run([sys.executable, "-m", "mpremote", "connect", args.port, "exec", code], check=True)
