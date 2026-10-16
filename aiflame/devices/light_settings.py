"""Persistent light appearance for each physical output."""
from aiflame.core.events import EventError
import re
from aiflame.devices.models import YEELIGHT_MODEL, YEELIGHT_MODELS, MIJIA_PLUG_MODELS, YEELIGHT_CUBE_MODEL, hardware_model

from aiflame.devices.cube_effects import CUBE_MODES

DEFAULTS = {"brightness": 50, "reduced": False}
COLOR_DEFAULTS = {"working": "#FACC15", "done": "#52C788", "needs_input": "#FF6B6B"}
STATE_BRIGHTNESS_DEFAULTS = {"working": 50, "done": 100, "needs_input": 100}
FRONT_BRIGHTNESS_DEFAULTS = {"working": 50, "done": 50, "needs_input": 50}
FRONT_TEMPERATURE_DEFAULTS = {"working": 2700, "done": 4000, "needs_input": 2700}
FRONT_FLASH_DEFAULTS = {"enabled": True, "on_ms": 600, "off_ms": 600}
FLASH_MIN_CYCLE_MS = 1200
FRONT_FIELDS = {"reminder_mode", "front_state_brightness", "front_state_temperature", "front_flash"}
PLUG_DEFAULTS = {"state_power": {"working": False, "done": True, "needs_input": True}}


def front_brightness_defaults(model_id):
    return {**FRONT_BRIGHTNESS_DEFAULTS, **({"done": 100} if model_id == YEELIGHT_MODEL else {})}


def temperature_rgb(kelvin):
    """Illustrative RGB white tint shared by the swatch and Beam LED frames.

    These anchors describe visual warmth, not calibrated physical CCT.
    """
    anchors = ((1700, (255, 174, 101)), (2700, (255, 208, 160)),
               (4000, (255, 241, 217)), (6500, (239, 245, 255)))
    lower, upper = next(((a, b) for a, b in zip(anchors, anchors[1:]) if kelvin <= b[0]), (anchors[-2], anchors[-1]))
    ratio = max(0, min(1, (kelvin - lower[0]) / (upper[0] - lower[0])))
    return tuple(round(a + (b - a) * ratio) for a, b in zip(lower[1], upper[1]))


def migrate_attention_settings(settings):
    """Preserve old shared attention preferences without exporting error keys."""
    result = dict(settings)
    for field in ('colors', 'state_brightness', 'front_state_brightness', 'front_state_temperature', 'state_power'):
        values = result.get(field)
        if isinstance(values, dict) and 'error' in values:
            values = dict(values)
            values.setdefault('needs_input', values.pop('error'))
            result[field] = values
    return result


def light_settings(record):
    if record.get("hardware_model") in MIJIA_PLUG_MODELS:
        saved = migrate_attention_settings(record.get("light_settings", {}))
        return {"brightness": 100, "reduced": False,
                "state_power": {**PLUG_DEFAULTS["state_power"], **saved.get("state_power", {})}}
    settings = {**DEFAULTS, **migrate_attention_settings(record.get("light_settings", {}))}
    if record.get("hardware_model") in YEELIGHT_MODELS:
        settings["reduced"] = False
        settings["colors"] = {**COLOR_DEFAULTS, **settings.get("colors", {})}
        defaults = {key: 0 for key in STATE_BRIGHTNESS_DEFAULTS} if settings["brightness"] == 0 else STATE_BRIGHTNESS_DEFAULTS
        settings["state_brightness"] = {**defaults, **settings.get("state_brightness", {})}
    if record.get("hardware_model") in YEELIGHT_MODELS:
        settings["reminder_mode"] = settings.get("reminder_mode", "back")
        if record.get("hardware_model") == YEELIGHT_CUBE_MODEL and settings["reminder_mode"] in ("tasks", "flame"):
            settings["reminder_mode"] = "status"
        if not hardware_model(record["hardware_model"]).get("white_alerts", True):
            settings["reminder_mode"] = "back"
        settings["front_state_brightness"] = {**front_brightness_defaults(record.get("hardware_model")), **settings.get("front_state_brightness", {})}
        settings["front_state_temperature"] = {**FRONT_TEMPERATURE_DEFAULTS, **settings.get("front_state_temperature", {})}
        settings["front_flash"] = {**FRONT_FLASH_DEFAULTS, **settings.get("front_flash", {})}
        if settings["reminder_mode"] == "front":
            settings["brightness"] = max(settings["front_state_brightness"].values())
    return settings


def migrate_light_settings(record):
    if "light_settings" not in record:
        record["light_settings"] = dict(DEFAULTS)
    record["light_settings"] = migrate_attention_settings(record["light_settings"])
    if record.get("hardware_model") in MIJIA_PLUG_MODELS:
        record["light_settings"].pop("enabled", None)
    if (record.get("hardware_model") == YEELIGHT_CUBE_MODEL
            and record["light_settings"].get("reminder_mode") in ("tasks", "flame")):
        record["light_settings"]["reminder_mode"] = "status"


def validate_patch(patch, model_id=None):
    if model_id in MIJIA_PLUG_MODELS:
        if (not isinstance(patch, dict) or not patch or set(patch) - PLUG_DEFAULTS.keys()
                or ("state_power" in patch and (not isinstance(patch["state_power"], dict) or not patch["state_power"]
                    or set(patch["state_power"]) - PLUG_DEFAULTS["state_power"].keys()
                    or any(type(value) is not bool for value in patch["state_power"].values())))):
            raise EventError("请选择插座任务亮灭规则")
        return
    if not isinstance(patch, dict) or not patch or set(patch) - (DEFAULTS.keys() | {"colors", "state_brightness"} | FRONT_FIELDS):
        raise EventError("请选择灯光亮度或提醒颜色")
    if model_id not in YEELIGHT_MODELS:
        raise EventError("不支持的硬件型号")
    if not hardware_model(model_id).get("white_alerts", True) and (patch.get("reminder_mode") == "front" or set(patch) & (FRONT_FIELDS - {"reminder_mode"})):
        raise EventError("此型号目前支持彩灯提醒")
    if "reminder_mode" in patch and patch["reminder_mode"] not in (("back", "front", *CUBE_MODES) if model_id == YEELIGHT_CUBE_MODEL else ("back", "front")):
        raise EventError("请选择此设备支持的提醒灯效")
    if "front_flash" in patch:
        values = patch["front_flash"]
        if (not isinstance(values, dict) or not values or set(values) - FRONT_FLASH_DEFAULTS.keys()
                or ("enabled" in values and type(values["enabled"]) is not bool)
                or any(key in values and (type(values[key]) is not int or not minimum <= values[key] <= maximum)
                       for key, minimum, maximum in (("on_ms", 100, 10000), ("off_ms", 100, 10000)))):
            raise EventError("闪烁设置无效：亮灯时长为 0.1–10 秒，熄灭时长为 0.1–10 秒")
    minimum_temperature = hardware_model(model_id)["white_temperature_min"]
    for field, minimum, maximum, message in (
        ("front_state_brightness", 0, 100, "白灯亮度范围为 0–100%"),
        ("front_state_temperature", minimum_temperature, 6500, f"白灯色温范围为 {minimum_temperature}–6500K"),
    ):
        if field in patch:
            values = patch[field]
            if (not isinstance(values, dict) or not values or set(values) - STATE_BRIGHTNESS_DEFAULTS.keys()
                    or any(type(value) is not int or not minimum <= value <= maximum for value in values.values())):
                raise EventError(message)
    maximum = 100
    if "brightness" in patch and (type(patch["brightness"]) is not int or not 0 <= patch["brightness"] <= maximum):
        raise EventError(f"灯光亮度范围为 0–{maximum}%")
    if "reduced" in patch and type(patch["reduced"]) is not bool:
        raise EventError("灯光兼容设置格式错误")
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


def validate_flash_cycle(values):
    if values["on_ms"] + values["off_ms"] < FLASH_MIN_CYCLE_MS:
        raise EventError("闪烁总周期（亮灯时长＋熄灭时长）不能短于 1.2 秒")
