"""Pinned official MicroPython image shipped with SeeEnd."""

import hashlib
import json
from pathlib import Path

from aiflame.core.events import EventError
from aiflame.core.paths import PACKAGE

DIRECTORY = PACKAGE / "devices/firmware/base"
BOARD = "ESP32-S3-N16R8"
VERSION = "1.29.0"


def bundled_image():
    try:
        metadata = json.loads((DIRECTORY / "manifest.json").read_text())
        filename = metadata["file"]
        if Path(filename).name != filename:
            raise ValueError("Invalid image filename")
        path = DIRECTORY / filename
        data = path.read_bytes()
        if (
            not 1024 <= len(data) <= 16 * 1024 * 1024
            or data[0] != 0xE9
            or int.from_bytes(data[12:14], "little") != 9
            or hashlib.sha256(data).hexdigest() != metadata["sha256"]
        ):
            raise ValueError("Invalid image")
        return path
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise EventError("内置底层固件缺失或损坏，请重新安装 SeeEnd APP") from error
