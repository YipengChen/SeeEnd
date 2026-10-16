"""Supported original-firmware lights, plugs and channel metadata."""

from aiflame.core.events import EventError

DEFAULT_MODEL = "yeelight-yltd003"
YEELIGHT_MODEL = "yeelight-yltd003"
YEELIGHT_BULB_MODEL = "yeelight-yldp005"
YEELIGHT_BEAM_MODEL = "yeelight-ylfwd0021"
YEELIGHT_CUBE_MODEL = "yeelight-cube-lite"
MIJIA_PLUG4_MODEL = "mijia-zncz401kk"
MIJIA_PLUG_MODEL = "mijia-zncz301kk"
YEELIGHT_MODELS = (YEELIGHT_MODEL, YEELIGHT_BULB_MODEL, YEELIGHT_BEAM_MODEL, YEELIGHT_CUBE_MODEL)
MIJIA_PLUG_MODELS = (MIJIA_PLUG_MODEL, MIJIA_PLUG4_MODEL)
SUPPORTED_MODELS = (*YEELIGHT_MODELS, *MIJIA_PLUG_MODELS)
MODELS = (
    {"id": YEELIGHT_MODEL, "name": "Yeelight 屏幕挂灯 Pro",
     "card_description": "YLTD003 · 背部彩灯 / 前部白灯",
     "description": "屏幕挂灯 · 彩色背光与白光照明 · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "lamp15", "command_prefix": "bg_",
     "white_temperature_min": 2700,
     "light_description": "选择背部彩灯或前部白灯进行任务提醒，两种模式的设置分别保存。待机时熄灭提醒灯。"},
    {"id": YEELIGHT_BULB_MODEL, "name": "Yeelight W3 彩色灯泡",
     "card_description": "YLDP005 · 彩灯 / 白灯",
     "description": "W3 彩色灯泡 · 彩光与白光照明 · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "colorb", "command_prefix": "",
     "white_temperature_min": 1700,
     "light_description": "选择彩灯或白灯进行任务提醒，两种模式的设置分别保存。待机时熄灭灯泡。"},
    {"id": YEELIGHT_BEAM_MODEL, "name": "Yeelight Beam 塔灯",
     "card_description": "YLFWD-0021 · 彩灯 / 白灯",
     "description": "Beam 幻彩桌面氛围塔灯 · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "RaysLight", "command_prefix": "", "white_temperature_min": 2700,
     "native_flow": False, "white_alerts": True, "pixel_count": 168,
     "required_methods": ("get_prop", "set_power", "set_bright", "udp_sess_new", "udp_sess_keep_alive", "activate_fx_mode", "update_leds"),
     "light_description": "彩灯与白灯分别保存设置，双塔同步提醒。白灯通过 RGB 模拟暖冷色温，支持常亮与闪烁；结束时切为全黑，不触发开关灯渐变。断网或异常退出后灯具可能恢复原厂灯效。"},

    {"id": YEELIGHT_CUBE_MODEL, "name": "Yeelight Cube Lite",
     "card_description": "Cube Lite · 四种点阵灯效 / 彩灯 / 白灯",
     "description": "Cube Lite 点阵氛围灯 · Wi-Fi 局域网", "connection": "yeelight",
     "lan_model": "cubelite", "lan_models": ("cubelite", "CubeLite", "clt4", "yeelink.light.clt4"),
     "command_prefix": "", "white_temperature_min": 2700,
     "native_flow": False, "white_alerts": True, "pixel_count": 100,
     "required_methods": ("set_power", "set_bright", "activate_fx_mode", "update_leds"),
     "light_description": "可选择像素状态牌、彗星巡航、贪吃蛇、文字状态或整屏彩灯、白灯。点阵灯效沿用彩灯的状态颜色与亮度；白灯设置独立保存。白光由 RGB 模拟，待机显示全黑。断网或异常退出后可能保持最后画面。"},

    {"id": MIJIA_PLUG_MODEL, "name": "米家智能插座 3",
     "card_description": "ZNCZ301KK · 灯具亮灭",
     "description": "通过插座通断电控制灯具 · Wi-Fi 局域网", "connection": "miio",
     "lan_model": "cuco.plug.v3", "white_alerts": False,
     "light_description": "灯具自身开关保持打开，并确认恢复供电后会自动亮。按任务状态控制插座通断电；待机或正常退出时尝试熄灭。断网或异常退出后可能保持最后状态。"},
    {"id": MIJIA_PLUG4_MODEL, "name": "米家智能插座 4",
     "card_description": "ZNCZ401KK · 灯具亮灭",
     "description": "通过插座通断电控制灯具 · Wi-Fi 局域网", "connection": "miio",
     "lan_model": "xiaomi.plug.04", "white_alerts": False,
     "light_description": "灯具自身开关保持打开，并确认恢复供电后会自动亮。按任务状态控制插座通断电；待机或正常退出时尝试熄灭。断网或异常退出后可能保持最后状态。"},
)
CONNECTION_KEYS = (
    "device_id", "port", "transport", "connection_paused", "wifi_device_secret",
    "wifi_last_ip", "wifi_ssid", "wifi_ssid_device_id",
    "yeelight_host", "yeelight_port",
    "miio_host", "miio_did", "miio_token_ref",
)


def hardware_model(model_id=DEFAULT_MODEL):
    for model in MODELS:
        if model["id"] == model_id:
            return dict(model)
    raise EventError("不支持的硬件型号")
