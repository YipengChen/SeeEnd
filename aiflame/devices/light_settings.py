"""Persistent light appearance for each physical output."""
from aiflame.core.events import EventError

DEFAULTS = {"brightness": 20, "reduced": False}


def light_settings(record):
    return {**DEFAULTS, **record.get("light_settings", {})}


def migrate_light_settings(record, legacy):
    if "light_settings" not in record:
        record["light_settings"] = {key: legacy[key] for key in DEFAULTS}


def validate_patch(patch):
    if not isinstance(patch, dict) or not patch or set(patch) - DEFAULTS.keys():
        raise EventError("请选择灯光亮度或动态效果")
    if "brightness" in patch and (type(patch["brightness"]) is not int or not 0 <= patch["brightness"] <= 25):
        raise EventError("灯光亮度范围为 0–25%")
    if "reduced" in patch and type(patch["reduced"]) is not bool:
        raise EventError("动态效果设置格式错误")
