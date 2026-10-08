"""Yeelight RGB notifications; native finite flows avoid TCP frame streaming."""
import threading
import time
import uuid

from aiflame.devices.yeelight import YeelightClient, discover, channel_properties, matches_model
from aiflame.devices.models import YEELIGHT_MODEL, hardware_model

COLORS = {"working": 0x4A90E2, "done": 0x52C788, "error": 0xFF9F43}


def light_flow(desired, settings):
    state = desired["state"]
    brightness = settings["brightness"]
    if state == "idle" or not brightness:
        return None
    color = COLORS.get(state, COLORS["error"])
    renewable = (state == "working" and not desired.get("preview")) or desired.get("holding")
    duration = 30000 if renewable else max(50, desired["duration_ms"])
    peak = max(1, min(25, brightness if state != "working" else round(brightness * .6)))
    if settings["reduced"]:
        return ([1, 2, f"50,1,{color},{peak}"] if duration < 100 else
                [2, 2, f"50,1,{color},{peak},{duration - 50},7,0,0"])
    # Slow breathing; flow count is the number of transitions, not cycles.
    step = 1500 if state == "working" else 2000 if state == "done" else 900
    count = max(1, duration // step)
    step = max(50, duration // count)
    low = max(1, round(peak * .35))
    expression = f"{step},1,{color},{peak},{step},1,{color},{low}"
    return [count, 2, expression]  # action=2 switches the rear light off on expiry.


class YeelightTransport(threading.Thread):
    def __init__(self, engine, lock, record):
        super().__init__(daemon=True)
        self.engine, self.lock, self.record = engine, lock, record
        self.model = hardware_model(record.get("hardware_model", YEELIGHT_MODEL))
        self.prefix = self.model["command_prefix"]
        self.stopping = threading.Event()
        self.client = None
        self.next_command = 0
        self.support = set()

    def method(self, name):
        return self.prefix + name

    def command(self, method, params, shutdown=False, desired=None, settings=None):
        # One command per 1.1 seconds, including heartbeats and reconnects:
        # below the official per-connection quota of 60 commands/minute.
        delay = max(0, self.next_command - time.monotonic())
        if shutdown:
            time.sleep(delay)
        elif self.stopping.wait(delay):
            raise OSError("服务已停止")
        if desired is not None:
            with self.lock:
                current = self.engine.desired()
                if (current["key"] != desired["key"] or current["state"] != desired["state"]
                        or current.get("holding", False) != desired.get("holding", False)
                        or any(self.engine.settings[field] != settings[field] for field in ("brightness", "reduced"))):
                    return None
                if method in (self.method("start_cf"), self.method("set_scene")):
                    flow = light_flow(current, settings)
                    if flow is None:
                        return None
                    params = ["cf", *flow] if method == self.method("set_scene") else flow
        self.next_command = time.monotonic() + 1.1
        result = self.client.command(method, params)
        if method != "get_prop" and result["result"] != ["ok"]:
            raise ValueError("Yeelight 未确认灯光指令")
        return result

    def connect(self):
        expected = self.record["device_id"]
        candidates = discover(self.record.get("yeelight_host"), model_id=self.model["id"])
        if not any(item["device_id"] == expected for item in candidates):
            candidates = discover(model_id=self.model["id"])
        item = next((row for row in candidates if row["device_id"] == expected and matches_model(row, self.model["id"])), None)
        if not item:
            raise OSError(f"未发现已添加的 {self.model['name']}，请检查同一局域网及 Yeelight 局域网控制开关")
        self.support = set(item["support"])
        self.client = YeelightClient(item["host"], self.stopping).open()
        response = self.command("get_prop", channel_properties(self.model["id"]))
        if len(response["result"]) != 3 or response["result"][0] not in ("on", "off"):
            raise ValueError("此灯具未提供所选型号的 RGB 控制")
        with self.lock:
            if self.stopping.is_set():
                raise OSError("服务已停止")
            self.record["yeelight_host"] = item["host"]
            self.engine.session_changed()
            self.engine.device = {"online": True, "connecting": False, "device_id": expected,
                                  "session_id": uuid.uuid4().hex, "connected_at": time.time(),
                                  "status": "Yeelight 局域网已连接", "last_error": None,
                                  "port": f"wifi://{item['host']}:55443", "firmware_version": item["firmware_version"],
                                  "protocol_version": "Yeelight LAN", "capabilities": ["rgb-backlight" if self.prefix else "rgb-light", "hold-until-activity"]}

    def display(self, desired, settings):
        flow = light_flow(desired, settings)
        if flow is None:
            return self.command(self.method("set_power"), ["off", "sudden", 0], desired=desired, settings=settings)
        if self.method("set_scene") in self.support:
            return self.command(self.method("set_scene"), ["cf", *flow], desired=desired, settings=settings)
        if self.command(self.method("set_power"), ["on", "sudden", 0], desired=desired, settings=settings) is None:
            return None
        return self.command(self.method("start_cf"), flow, desired=desired, settings=settings)

    def run(self):
        signature = None
        refresh = heartbeat = 0
        try:
            while not self.stopping.is_set():
                try:
                    if not self.client:
                        self.connect()
                        signature = None
                    with self.lock:
                        desired = self.engine.desired().copy()
                        settings = self.engine.settings.copy()
                    key = (desired["key"], desired["state"], settings["brightness"], settings["reduced"], desired.get("holding", False))
                    now = time.monotonic()
                    renewable = (desired["state"] == "working" and not desired.get("preview")) or desired.get("holding")
                    if key != signature or (renewable and now >= refresh):
                        response = self.display(desired, settings)
                        if response is None:
                            continue
                        with self.lock:
                            if not self.stopping.is_set() and self.engine.desired()["key"] == desired["key"]:
                                self.engine.applied(desired, {"command_id": f"yeelight:{response['id']}", "result": "applied"})
                        signature = key
                        refresh = time.monotonic() + 20
                        heartbeat = time.monotonic() + 5
                    elif now >= heartbeat:
                        response = self.command("get_prop", [self.method("power")])
                        expected_power = "off" if desired["state"] == "idle" else "on"
                        if response["result"] != [expected_power]:
                            signature = None
                        with self.lock:
                            self.engine.device["last_heartbeat"] = time.time()
                            if desired["state"] == "working" and signature is not None:
                                self.engine.applied(desired, self.engine.last_command)
                        heartbeat = time.monotonic() + 5
                    self.stopping.wait(.05)
                except (OSError, ValueError, TimeoutError) as error:
                    if self.stopping.is_set():
                        break
                    if self.client:
                        self.client.close()
                    self.client = None
                    with self.lock:
                        self.engine.device.update(online=False, connecting=False, status="设备离线", last_error=str(error))
                    self.stopping.wait(3)
        finally:
            if self.client:
                # Turn off only the channel used by this hardware model.
                self.client.stopping = None
                try:
                    self.command(self.method("set_power"), ["off", "sudden", 0], shutdown=True)
                except (OSError, ValueError):
                    pass
                self.client.close()
