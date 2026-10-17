"""Yeelight color and white-light alerts using bounded native flows."""
import threading
import time
import uuid
import base64
import logging

from aiflame.devices.yeelight import YeelightClient, BeamClient, CubeClient, discover, channel_properties, matches_model, verify_control
from aiflame.devices.yeelight import CUBE_COMMAND_INTERVAL, QUOTA_RETRY_DELAY, YeelightQuotaError, CubeControlError, cube_session_path
from aiflame.devices.models import YEELIGHT_MODEL, YEELIGHT_BEAM_MODEL, YEELIGHT_CUBE_MODEL, hardware_model
from aiflame.devices.light_settings import COLOR_DEFAULTS, light_settings, temperature_rgb, FRONT_BRIGHTNESS_DEFAULTS, FRONT_TEMPERATURE_DEFAULTS, FRONT_FLASH_DEFAULTS, FLASH_MIN_CYCLE_MS

from aiflame.devices.cube_effects import CUBE_MODES, frame as cube_frame

COLORS = {state: int(color.lstrip("#"), 16) for state, color in COLOR_DEFAULTS.items()}
APPEARANCE_FIELDS = ("brightness", "reduced", "colors", "state_brightness",
                     "reminder_mode", "front_state_brightness", "front_state_temperature", "front_flash")
LOGGER = logging.getLogger(__name__)


def white_flash_settings(settings):
    return {**FRONT_FLASH_DEFAULTS, **settings.get("front_flash", {})}


def white_flash_interval(settings):
    flash = white_flash_settings(settings)
    return max(FLASH_MIN_CYCLE_MS, flash["on_ms"] + flash["off_ms"]) / 1000


def white_attention(desired, settings):
    return (settings.get("reminder_mode") == "front"
            and white_flash_settings(settings)["enabled"]
            and desired["state"] in ("needs_input",)
            and settings.get("front_state_brightness", {}).get("needs_input", FRONT_BRIGHTNESS_DEFAULTS["needs_input"]) > 0)


def light_flow(desired, settings):
    state = desired["state"]
    if state not in COLORS:
        return None
    color_state = state
    front = settings.get("reminder_mode") == "front"
    brightness = (settings.get("front_state_brightness", {}).get(color_state, FRONT_BRIGHTNESS_DEFAULTS[color_state])
                  if front else settings.get("state_brightness", {}).get(color_state, settings["brightness"]))
    if state == "idle" or not brightness:
        return None
    if front:
        color = settings.get("front_state_temperature", {}).get(color_state, FRONT_TEMPERATURE_DEFAULTS[color_state])
    else:
        custom = settings.get("colors", {}).get(color_state)
        color = int(custom.lstrip("#"), 16) if custom else COLORS[color_state]
    mode = 2 if front else 1
    renewable = (state == "working" and not desired.get("preview") and not desired.get("device_test")) or desired.get("holding")
    duration = 30000 if renewable else max(50, desired["duration_ms"])
    peak = max(1, min(100, brightness))
    if white_attention(desired, settings):
        # A bounded pulse ends with a real power-off (action=2). Native flows
        # cannot dim to 0%; repeating pulses leave a clearly dark interval.
        duration = min(duration, white_flash_settings(settings)["on_ms"])
    # Use the protocol's minimum transition, then hold the exact target.
    # Native expiry still turns the light off if the app connection is lost.
    return ([1, 2, f"50,{mode},{color},{peak}"] if duration < 100 else
            [2, 2, f"50,{mode},{color},{peak},{duration - 50},7,0,0"])


def beam_pixel(desired, settings, elapsed=0):
    state = desired["state"]
    if state not in COLORS:
        return bytes(3)
    front = settings.get("reminder_mode") == "front"
    if front:
        brightness = settings.get("front_state_brightness", {}).get(state, FRONT_BRIGHTNESS_DEFAULTS[state])
        color = temperature_rgb(settings.get("front_state_temperature", {}).get(state, FRONT_TEMPERATURE_DEFAULTS[state]))
        if white_attention(desired, settings):
            flash = white_flash_settings(settings)
            if elapsed * 1000 % (white_flash_interval(settings) * 1000) >= flash["on_ms"]:
                return bytes(3)
    else:
        brightness = settings.get("state_brightness", {}).get(state, settings["brightness"])
        rgb = int(settings.get("colors", {}).get(state, COLOR_DEFAULTS[state]).lstrip("#"), 16)
        color = tuple((rgb >> shift) & 255 for shift in (16, 8, 0))
    return bytes(round(channel * max(0, min(100, brightness)) / 100) for channel in color)


class YeelightTransport(threading.Thread):
    def __init__(self, engine, lock, record):
        super().__init__(daemon=True)
        self.engine, self.lock, self.record = engine, lock, record
        self.model = hardware_model(record.get("hardware_model", YEELIGHT_MODEL))
        self.prefix = "" if light_settings(record).get("reminder_mode") == "front" else self.model["command_prefix"]
        self.stopping = threading.Event()
        self.client = None
        self.next_command = 0
        self.support = set()
        self.beam_phase_key = None
        self.beam_phase_started = 0
        self.cube_animation_id = uuid.uuid4().hex

    def method(self, name):
        return self.prefix + name

    def command(self, method, params, shutdown=False, desired=None, settings=None, params_factory=None):
        # Ordinary lamps use 1.1 s spacing. Cube frames use a bounded 10 FPS
        # UDP session; Beam frames use their own UDP session.
        direct_frame = bool(self.model.get("pixel_count")) and method == "update_leds"
        cube = self.model["id"] == YEELIGHT_CUBE_MODEL
        delay = 0 if direct_frame and not cube else max(0, self.next_command - time.monotonic())
        if shutdown:
            time.sleep(delay)
        elif self.stopping.wait(delay):
            raise OSError("服务已停止")
        if direct_frame and cube:
            self.client.prepare_frame()
        if desired is not None:
            with self.lock:
                current = self.engine.desired()
                if (current["key"] != desired["key"] or current["state"] != desired["state"]
                        or getattr(current, "delivery_context", None) != getattr(desired, "delivery_context", None)
                        or current.get("cube_state") != desired.get("cube_state")
                        or current.get("cube_tasks") != desired.get("cube_tasks")
                        or current.get("holding", False) != desired.get("holding", False)
                        or any(self.engine.settings.get(field) != settings.get(field) for field in APPEARANCE_FIELDS)):
                    return None
                if direct_frame and cube:
                    if self.stopping.is_set():
                        raise OSError("服务已停止")
                    if params_factory is not None:
                        params = params_factory()
                    result = self.client.command(method, params)
                    finished = time.monotonic()
                    next_slot = self.next_command + CUBE_COMMAND_INTERVAL
                    # Keep the 100 ms clock instead of adding render/send time
                    # to every frame. After missing a full slot, resume normally
                    # rather than sending a catch-up burst.
                    self.next_command = (next_slot if next_slot > finished
                                         else finished + CUBE_COMMAND_INTERVAL)
                    if result.get("result") != ["sent"]:
                        raise ValueError("Yeelight 未确认灯光指令")
                    return result
                if method in (self.method("start_cf"), self.method("set_scene")):
                    flow = light_flow(current, settings)
                    if flow is None:
                        return None
                    params = ["cf", *flow] if method == self.method("set_scene") else flow
        if not direct_frame or cube:
            self.next_command = time.monotonic() + (CUBE_COMMAND_INTERVAL if cube else 1.1)
        result = self.client.command(method, params)
        if method != "get_prop" and result.get("result") != (["sent"] if method == "update_leds" else ["ok"]):
            raise ValueError("Yeelight 未确认灯光指令")
        return result

    def connect(self):
        expected = self.record["device_id"]
        candidates = discover(self.record.get("yeelight_host"), model_id=self.model["id"], stopping=self.stopping)
        if not any(item["device_id"] == expected for item in candidates):
            candidates = discover(model_id=self.model["id"], stopping=self.stopping)
        item = next((row for row in candidates if row["device_id"] == expected and matches_model(row, self.model["id"])), None)
        if not item:
            raise OSError(f"未发现已添加的 {self.model['name']}，请检查同一局域网及 Yeelight 局域网控制开关")
        self.support = set(item["support"])
        settings = self.engine.settings
        self.prefix = "" if settings.get("reminder_mode") == "front" else self.model["command_prefix"]
        if self.model.get("native_flow", True) and settings.get("reminder_mode") == "front" and not {"set_power", "start_cf"}.issubset(self.support):
            raise ValueError("此灯具固件未提供白灯提醒控制")
        client_type = (CubeClient if self.model["id"] == YEELIGHT_CUBE_MODEL else
                       BeamClient if self.model["id"] == YEELIGHT_BEAM_MODEL else YeelightClient)
        if self.model["id"] == YEELIGHT_CUBE_MODEL:
            from aiflame.core.paths import runtime_dir
            session_path = cube_session_path(runtime_dir(), expected)
            self.client = client_type(item["host"], self.stopping, session_path=session_path).open()
        else:
            self.client = client_type(item["host"], self.stopping).open()
        properties = ["power", "bright", "ct"] if settings.get("reminder_mode") == "front" else channel_properties(self.model["id"])
        verify_control(self, self.model["id"], properties)
        with self.lock:
            if self.stopping.is_set():
                raise OSError("服务已停止")
            self.record["yeelight_host"] = item["host"]
            self.engine.session_changed()
            self.engine.device = {"online": True, "connecting": False, "device_id": expected,
                                  "session_id": uuid.uuid4().hex, "connected_at": time.time(),
                                  "status": "Yeelight 局域网已连接", "last_error": None,
                                  "port": f"wifi://{item['host']}:" + ("55444" if self.model["id"] == YEELIGHT_CUBE_MODEL else "55443"), "firmware_version": item["firmware_version"],
                                  "protocol_version": "Yeelight LAN", "capabilities": ["rgb-backlight" if self.prefix else ("white-front" if self.model["id"] == YEELIGHT_MODEL else "white-light") if settings.get("reminder_mode") == "front" else "rgb-light", "hold-until-activity"]}
            if self.model["id"] == YEELIGHT_CUBE_MODEL:
                self.engine.device["session_reused"] = self.client.session_reused is True

    def display(self, desired, settings):
        if settings.get("reminder_mode") == "front" and not self.model.get("white_alerts", True):
            raise ValueError("此型号目前支持彩灯提醒")
        prefix = "" if settings.get("reminder_mode") == "front" else self.model["command_prefix"]
        if self.model.get("native_flow", True) and settings.get("reminder_mode") == "front" and not {"set_power", "start_cf"}.issubset(self.support):
            raise ValueError("此灯具固件未提供白灯提醒控制")
        if prefix != self.prefix:
            if self.command(self.method("set_power"), ["off", "sudden", 0], desired=desired, settings=settings) is None:
                return None
            self.prefix = prefix
        flow = light_flow(desired, settings)
        if bool(self.model.get("pixel_count")):
            phase = (desired.get("key"), desired["state"], settings.get("reminder_mode"),
                     tuple(sorted(white_flash_settings(settings).items())), desired.get("cube_state"))
            now = time.monotonic()
            if phase != self.beam_phase_key:
                if (phase[1] == 'idle' or phase[2] != 'snake'
                        or (self.beam_phase_key is not None and self.beam_phase_key[2] != 'snake')):
                    self.cube_animation_id = uuid.uuid4().hex
                self.beam_phase_key, self.beam_phase_started = phase, now
            def render():
                elapsed = time.monotonic() - self.beam_phase_started
                payload = (cube_frame({**desired,
                                      "cube_animation_id": (self.cube_animation_id, bool(desired.get('preview')),
                                                            bool(desired.get('device_test'))),
                                      "cube_animation_phase": self.beam_phase_started}, settings, elapsed)
                           if self.model["id"] == YEELIGHT_CUBE_MODEL and settings.get("reminder_mode") in CUBE_MODES
                           else beam_pixel(desired, settings, elapsed) * self.model["pixel_count"])
                return [base64.b64encode(payload).decode()]
            if self.model["id"] == YEELIGHT_CUBE_MODEL:
                return self.command("update_leds", None, desired=desired, settings=settings, params_factory=render)
            return self.command("update_leds", render(), desired=desired, settings=settings)
        if flow is None:
            return self.command(self.method("set_power"), ["off", "sudden", 0], desired=desired, settings=settings)
        if self.method("set_scene") in self.support:
            return self.command(self.method("set_scene"), ["cf", *flow], desired=desired, settings=settings)
        if self.command(self.method("set_power"), ["on", "sudden", 0], desired=desired, settings=settings) is None:
            return None
        return self.command(self.method("start_cf"), flow, desired=desired, settings=settings)

    def run(self):
        signature = None
        last_submission = None
        refresh = heartbeat = 0
        try:
            while not self.stopping.is_set():
                try:
                    if not self.client:
                        self.connect()
                        signature = None
                        last_submission = None
                    with self.lock:
                        desired = self.engine.desired().copy()
                        settings = self.engine.settings.copy()
                    key = (getattr(desired, "delivery_context", None), desired["key"], desired["state"], desired.get("cube_state"), desired.get("cube_tasks"), settings["brightness"], settings["reduced"],
                           tuple(sorted(settings.get("colors", {}).items())),
                           tuple(sorted(settings.get("state_brightness", {}).items())), desired.get("holding", False),
                           settings.get("reminder_mode"), tuple(sorted(settings.get("front_state_brightness", {}).items())),
                           tuple(sorted(settings.get("front_state_temperature", {}).items())),
                           tuple(sorted(settings.get("front_flash", {}).items())))
                    now = time.monotonic()
                    renewable = self.model.get("native_flow", True) and ((desired["state"] == "working" and not desired.get("preview") and not desired.get("device_test")) or desired.get("holding"))
                    flashing = white_attention(desired, settings)
                    direct = bool(self.model.get("pixel_count"))
                    changed = key != signature
                    if changed or ((renewable or flashing or direct) and now >= refresh):
                        if (changed and self.model["id"] == YEELIGHT_CUBE_MODEL and last_submission
                                and getattr(desired, "delivery_context", None) != getattr(last_submission[0], "delivery_context", None)):
                            # A new recipient needs a new packet even when its
                            # static pixels match the previous task's frame.
                            self.client.last_frame = None
                        response = self.display(desired, settings)
                        if response is None:
                            continue
                        ack = {"command_id": f"yeelight:{response['id']}", "result": "sent" if response["result"] == ["sent"] else "applied"}
                        last_submission = (desired, ack)
                        if changed or not direct:
                            with self.lock:
                                if not self.stopping.is_set() and self.engine.desired()["key"] == desired["key"]:
                                    self.engine.applied(desired, ack)
                        signature = key
                        interval = ((CUBE_COMMAND_INTERVAL if self.model["id"] == YEELIGHT_CUBE_MODEL else .05)
                                    if direct else white_flash_interval(settings) if flashing else 20)
                        # Keep a stable frame clock instead of accumulating
                        # rendering/network time into every animation step.
                        if self.model["id"] == YEELIGHT_CUBE_MODEL:
                            refresh = self.next_command
                        else:
                            refresh = (max(refresh + interval, time.monotonic()) if direct and not changed
                                       else time.monotonic() + interval)
                        if changed or not direct:
                            heartbeat = time.monotonic() + 5
                    if time.monotonic() >= heartbeat:
                        if self.model["id"] == YEELIGHT_CUBE_MODEL:
                            self.client.check_connection()
                        else:
                            response = self.command("get_prop", [self.method("power")])
                            expected_power = "on" if bool(self.model.get("pixel_count")) else "off" if light_flow(desired, settings) is None else "on"
                            if response["result"] != [expected_power]:
                                if bool(self.model.get("pixel_count")):
                                    raise OSError("Beam 灯具电源已改变，正在重新建立灯点会话")
                                signature = None
                        with self.lock:
                            if self.model["id"] == YEELIGHT_CUBE_MODEL:
                                # These are actual ACK times, not the time we
                                # queued an unanswered heartbeat or pixel frame.
                                now = time.monotonic()
                                self.engine.device["last_heartbeat"] = time.time() - max(0, now - self.client.last_keep_alive)
                                self.engine.device["last_control_ack"] = time.time() - max(0, now - self.client.last_control_ack)
                            else:
                                self.engine.device["last_heartbeat"] = time.time()
                            if desired["state"] == "working" and signature is not None and last_submission:
                                self.engine.applied(*last_submission)
                        heartbeat = time.monotonic() + 5
                    self.stopping.wait(min(.05, max(0, refresh - time.monotonic())) if direct else .05)
                except (OSError, ValueError, TimeoutError) as error:
                    if self.stopping.is_set():
                        break
                    if self.client:
                        self.client.close()
                    self.client = None
                    with self.lock:
                        self.engine.device.update(online=False, connecting=False, status="设备离线", last_error=str(error))
                    if self.model["id"] == YEELIGHT_CUBE_MODEL:
                        LOGGER.warning("Cube Lite %s connection failed: %s", self.record.get("device_id"), error)
                    self.stopping.wait(QUOTA_RETRY_DELAY if isinstance(error, (YeelightQuotaError, CubeControlError)) else 3)
        finally:
            if self.client:
                # Turn off only the channel used by this hardware model.
                self.client.stopping = None
                try:
                    if self.model["id"] == YEELIGHT_CUBE_MODEL:
                        # Stopping before the first frame must never run
                        # prepare_frame(), which activates and powers on LEDs.
                        # A lost/unacknowledged black frame must not prevent
                        # the real power-off request from being attempted.
                        if self.client.direct:
                            try:
                                self.command("update_leds", [base64.b64encode(bytes(self.model["pixel_count"] * 3)).decode()], shutdown=True)
                            except (OSError, ValueError):
                                pass
                        self.command("set_power", ["off", "sudden", 0], shutdown=True)
                    elif bool(self.model.get("pixel_count")):
                        self.command("update_leds", [base64.b64encode(bytes(self.model["pixel_count"] * 3)).decode()], shutdown=True)
                    else:
                        self.command(self.method("set_power"), ["off", "sudden", 0], shutdown=True)
                except (OSError, ValueError):
                    pass
                self.client.close()
