"""Persistent light appearance for each physical output."""
from aiflame.core.events import EventError
import re
from aiflame.devices.models import YEELIGHT_MODELS

DEFAULTS = {"brightness": 50, "reduced": False}
COLOR_DEFAULTS = {"working": "#4A90E2", "done": "#52C788", "error": "#FF9F43"}
STATE_BRIGHTNESS_DEFAULTS = {"working": 20, "done": 50, "error": 50}


def light_settings(record):
    settings = {**DEFAULTS, **record.get("light_settings", {})}
    if record.get("hardware_model") in YEELIGHT_MODELS:
        settings["reduced"] = False
        settings["colors"] = {**COLOR_DEFAULTS, **settings.get("colors", {})}
        defaults = {key: 0 for key in STATE_BRIGHTNESS_DEFAULTS} if settings["brightness"] == 0 else STATE_BRIGHTNESS_DEFAULTS
        settings["state_brightness"] = {**defaults, **settings.get("state_brightness", {})}
    return settings


def migrate_light_settings(record, legacy):
    if "light_settings" not in record:
        record["light_settings"] = dict(DEFAULTS)


def validate_patch(patch, model_id=None):
    if not isinstance(patch, dict) or not patch or set(patch) - (DEFAULTS.keys() | {"colors", "state_brightness"}):
        raise EventError("请选择灯光亮度、动态效果或提醒颜色")
    if model_id not in YEELIGHT_MODELS:
        raise EventError("不支持的硬件型号")
    maximum = 100
    if "brightness" in patch and (type(patch["brightness"]) is not int or not 0 <= patch["brightness"] <= maximum):
        raise EventError(f"灯光亮度范围为 0–{maximum}%")
    if "reduced" in patch and type(patch["reduced"]) is not bool:
        raise EventError("动态效果设置格式错误")
    if "state_brightness" in patch:
        values = patch["state_brightness"]
        if (model_id not in YEELIGHT_MODELS or not isinstance(values, dict) or not values
                or set(values) - STATE_BRIGHTNESS_DEFAULTS.keys()
                or any(type(value) is not int or not 0 <= value <= 100 for value in values.values())):
            raise EventError("任务状态亮度范围为 0–100%")
    if "colors" in patch:
        colors = patch["colors"]
        if (not isinstance(colors, dict) or not colors or set(colors) - COLOR_DEFAULTS.keys()
                or any(not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value) for value in colors.values())):
            raise EventError("请选择有效的 RGB 颜色（#RRGGBB）")
