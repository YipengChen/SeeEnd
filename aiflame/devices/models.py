"""Supported hardware models and the files installed on each board."""

from aiflame.core.events import EventError
from aiflame.core.paths import PACKAGE

DEFAULT_MODEL = "ai-flame-v01"
YEELIGHT_MODEL = "yeelight-yltd003"
YEELIGHT_BULB_MODEL = "yeelight-yldp005"
YEELIGHT_MODELS = (YEELIGHT_MODEL, YEELIGHT_BULB_MODEL)
MODELS = (
    {"id": DEFAULT_MODEL, "name": "AI Flame 0.1", "width": 9, "height": 16,
     "card_description": "9 × 16 · 暖黄色灯板",
     "mapping": "matrix_map.py", "description": "9 × 16 LED · USB / Wi-Fi"},
    {"id": "ai-flame-v02", "name": "AI Flame 0.2", "width": 7, "height": 15,
     "card_description": "7 × 15 · 暖黄色灯板",
     "mapping": "matrix_map_v02.py", "description": "7 × 15 LED · USB / Wi-Fi"},
    {"id": YEELIGHT_MODEL, "name": "Yeelight YLTD003",
     "card_description": "屏幕挂灯 Pro · RGB 背光",
     "description": "屏幕挂灯 · RGB 背光 · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "lamp15", "command_prefix": "bg_",
     "light_description": "背光：工作中蓝色呼吸，完成绿色，异常 / 待输入橙色；待机或静默熄灭背光。前方照明沿用灯具设置。"},
    {"id": YEELIGHT_BULB_MODEL, "name": "Yeelight YLDP005",
     "card_description": "W3 彩色灯泡 · RGB",
     "description": "W3 彩色灯泡 · RGB · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "colorb", "command_prefix": "",
     "light_description": "灯泡：工作中蓝色呼吸，完成绿色，异常 / 待输入橙色；待机或静默时熄灭灯泡。连接期间整颗灯泡用于任务提醒。"},
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


def firmware_files(model_id=DEFAULT_MODEL):
    model = hardware_model(model_id)
    if "mapping" not in model:
        raise EventError("此设备使用原厂程序，不支持安装或导出 AI Flame 固件")
    directory = PACKAGE / "devices/firmware"
    return {
        "version.py": directory / "version.py",
        "candle.py": directory / "candle.py",
        "matrix.py": directory / "matrix.py",
        "matrix_map.py": directory / model["mapping"],
        "wifi_protocol.py": PACKAGE / "devices/wifi_protocol.py",
        "main.py": directory / "main.py",
    }
