"""Supported original-firmware Yeelight models and channel metadata."""

from aiflame.core.events import EventError

DEFAULT_MODEL = "yeelight-yltd003"
YEELIGHT_MODEL = "yeelight-yltd003"
YEELIGHT_BULB_MODEL = "yeelight-yldp005"
YEELIGHT_MODELS = (YEELIGHT_MODEL, YEELIGHT_BULB_MODEL)
MODELS = (
    {"id": YEELIGHT_MODEL, "name": "Yeelight 屏幕挂灯 Pro",
     "card_description": "YLTD003 · RGB 背光",
     "description": "屏幕挂灯 · RGB 背光 · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "lamp15", "command_prefix": "bg_",
     "light_description": "仅控制 RGB 背光；每台灯具的任务提醒颜色与亮度独立保存，待机或静默时熄灭背光。前方照明沿用灯具设置。"},
    {"id": YEELIGHT_BULB_MODEL, "name": "Yeelight W3 彩色灯泡",
     "card_description": "YLDP005 · RGB",
     "description": "W3 彩色灯泡 · RGB · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "colorb", "command_prefix": "",
     "light_description": "整颗灯泡用于任务提醒；每台灯具的任务提醒颜色与亮度独立保存，待机或静默时熄灭灯泡。"},

)
CONNECTION_KEYS = (
    "device_id", "port", "transport", "connection_paused", "wifi_device_secret",
    "wifi_last_ip", "wifi_ssid", "wifi_ssid_device_id",
    "yeelight_host", "yeelight_port",
)


def hardware_model(model_id=DEFAULT_MODEL):
    for model in MODELS:
        if model["id"] == model_id:
            return dict(model)
    raise EventError("不支持的硬件型号")
