"""Common device protocol, retries, heartbeats and display lifecycle."""

import json
import queue
import threading
import time
import uuid
from aiflame.devices.models import DEFAULT_MODEL, hardware_model


class DeviceTransport(threading.Thread):
    def __init__(self, engine, lock, hub_id, expected_device):
        super().__init__(daemon=True)
        self.engine, self.lock, self.hub_id = engine, lock, hub_id
        self.expected_device = expected_device
        self.stopping = threading.Event()
        self.connection = None
        self.session = None
        self.seq = 0
        self.scan_requests = queue.Queue(maxsize=1)

    def send(self, value, *, shutdown=False):
        if self.stopping.is_set() and not shutdown:
            raise OSError("服务已停止")
        self.connection.write(
            (json.dumps(value, separators=(",", ":")) + "\n").encode()
        )
        self.connection.flush()

    def receive(self, predicate, timeout=2, *, cancelled=None):
        end = time.monotonic() + timeout
        previous_timeout = self.connection.timeout if cancelled else None
        if cancelled:
            self.connection.timeout = 0.1
        try:
            while time.monotonic() < end and not self.stopping.is_set():
                if cancelled and cancelled():
                    return None
                try:
                    line = self.connection.read_until(b"\n", 4097)
                except TimeoutError:
                    if cancelled:
                        continue
                    raise
                if self.stopping.is_set():
                    raise OSError("服务已停止")
                if cancelled and cancelled():
                    return None
                if len(line) > 4096:
                    continue
                try:
                    value = json.loads(line)
                    if isinstance(value, dict) and predicate(value):
                        return value
                except (ValueError, UnicodeError):
                    continue
        finally:
            if cancelled:
                self.connection.timeout = previous_timeout
        raise TimeoutError("设备未回执")

    def connect(self):
        raise NotImplementedError

    @staticmethod
    def validate_display(hello, model_id=DEFAULT_MODEL):
        model = hardware_model(model_id)
        if "mapping" not in model:
            raise ValueError("此设备不使用 AI Flame 灯板协议")
        capabilities = hello.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or f"led-matrix-{model['width']}x{model['height']}" not in capabilities
            or "mono-pwm" not in capabilities
            or hello.get("display_width") != model["width"]
            or hello.get("display_height") != model["height"]
            or hello.get("hardware_model", model_id) != model_id
        ):
            raise ValueError(f"设备型号或灯板规格不匹配，请选择正确型号并通过 USB 更新灯光程序（{model['name']} · {model['width']}×{model['height']}）")

    def display(self, desired, settings):
        self.seq += 1
        # Matrix firmware controls the smaller, dimmer working flame locally.
        brightness = settings["brightness"]
        if desired["state"] == "done":
            brightness = min(25, round(brightness * 1.25))
        command = {
            "type": "display",
            "command_id": str(uuid.uuid4()),
            "session_id": self.session,
            "hub_id": self.hub_id,
            "seq": self.seq,
            "state": desired["state"],
            "effect": "reduced" if settings["reduced"] else "standard",
            "brightness": brightness,
            "duration_ms": (60000 if desired.get("holding") and "hold-until-activity" not in self.engine.device.get("capabilities", []) else desired["duration_ms"]),
            "lease_ms": 30000,
        }

        if desired.get('tuning') is not None:
            command.update(tuning=desired['tuning'], brightness=int(desired['tuning'].get('brightness', 20)), effect='standard', lease_ms=7000)

        def superseded():
            with self.lock:
                return self.engine.desired()["key"] != desired["key"]

        for attempt, delay in enumerate((0, 0.5, 1, 2)):
            if self.stopping.wait(delay):
                raise OSError("服务已停止")
            if desired.get("preview") or desired.get("device_test") or desired.get("tuning") is not None:
                with self.lock:
                    current = self.engine.desired()
                    if current["key"] != desired["key"]:
                        return None
                    command["duration_ms"] = current["duration_ms"]
                    # Firmware fades during the last two seconds of its lease.
                    command["lease_ms"] = current["duration_ms"] + 2000
            self.send(command)
            try:
                options = {"cancelled": superseded} if desired.get("preview") or desired.get("device_test") or desired.get("tuning") is not None else {}
                ack = self.receive(
                    lambda x: x.get("type") == "ack"
                    and x.get("command_id") == command["command_id"],
                    **options,
                )
                if ack is None:
                    return None
                if ack.get("result") != "applied":
                    error = str(ack.get("error"))
                    detail = {
                        "matrix-i2c": "灯板未响应，请检查供电、共地与 SDA=21 / SCL=18 接线",
                        "mapping-not-calibrated": "灯板位置映射尚未完成，请先完成通道定位",
                    }.get(error, error)
                    raise ValueError("设备拒绝命令：" + detail)
                return ack
            except TimeoutError:
                if (desired.get("preview") or desired.get("device_test") or desired.get("tuning") is not None) and superseded():
                    return None
                if attempt == 3:
                    raise

    def scan_networks(self):
        request = {"id": str(uuid.uuid4()), "done": threading.Event(), "deadline": time.monotonic() + 20}
        if self.stopping.is_set():
            raise OSError("设备已断开，请重新连接后搜索。")
        try:
            self.scan_requests.put_nowait(request)
        except queue.Full:
            raise RuntimeError("设备正在搜索网络，请稍候。")
        if self.stopping.is_set():
            raise OSError("设备已断开，请重新连接后搜索。")
        if not request["done"].wait(20):
            raise TimeoutError("ESP32 搜索超时，请检查连接后重新展开列表。")
        if request.get("error"):
            raise RuntimeError(request["error"])
        return request["networks"]

    def process_scan(self):
        # Only this transport thread may read/write the live connection.
        try:
            request = self.scan_requests.get_nowait()
        except queue.Empty:
            return
        try:
            if time.monotonic() >= request["deadline"]:
                raise RuntimeError("搜索请求已过期，请重新展开列表。")
            self.send({"type": "wifi_scan", "request_id": request["id"],
                       "hub_id": self.hub_id, "session_id": self.session})
            networks = []
            while True:
                remaining = request["deadline"] - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("ESP32 搜索超时，请重新展开列表。")
                packet = self.receive(lambda x: x.get("type") == "wifi_scan_result" and x.get("request_id") == request["id"] and x.get("session_id") == self.session, remaining, cancelled=lambda: False)
                if packet.get("error"):
                    raise RuntimeError("ESP32 暂时无法搜索网络，请稍后重试。")
                rows = packet.get("networks")
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("设备返回的网络列表无效")
                networks.extend(rows)
                if packet.get("done"):
                    break
                offset = packet.get("next")
                if type(offset) is not int or offset != len(networks):
                    raise ValueError("设备返回的网络分页无效")
                self.send({"type": "wifi_scan", "request_id": request["id"], "offset": offset,
                           "hub_id": self.hub_id, "session_id": self.session})
            request["networks"] = networks
        except RuntimeError as error:
            request["error"] = str(error)
        except (OSError, ValueError) as error:
            request["error"] = "ESP32 搜索中断，请检查设备连接后重试。"
            raise
        finally:
            request["done"].set()

    def run(self):
        signature = None
        ping_time = 0
        try:
            while not self.stopping.is_set():
                try:
                    if not self.connection:
                        self.connect()
                        signature = None
                        ping_time = 0
                    with self.lock:
                        if self.stopping.is_set():
                            break
                        desired = self.engine.desired().copy()
                        settings = self.engine.settings.copy()
                    key = (
                        desired["key"],
                        desired["state"],
                        settings["brightness"],
                        settings["reduced"],
                        desired.get("holding", False),
                    )
                    if key != signature:
                        ack = self.display(desired, settings)
                        if ack is None:
                            continue
                        with self.lock:
                            if not self.stopping.is_set():
                                self.engine.applied(desired, ack)
                        signature = key
                        ping_time = time.monotonic()
                    elif time.monotonic() - ping_time >= 1:
                        self.send(
                            {
                                "type": "ping",
                                "session_id": self.session,
                                "hub_id": self.hub_id,
                            }
                        )
                        pong = self.receive(
                            lambda x: x.get("type") == "pong"
                            and x.get("session_id") == self.session
                        )
                        if pong.get("hardware_error"):
                            raise ValueError("灯板驱动异常，请检查灯板接线并通过 USB 更新灯光程序")
                        with self.lock:
                            if not self.stopping.is_set():
                                self.engine.device["last_heartbeat"] = time.time()
                                for field in ("wifi_ssid", "wifi_connected"):
                                    if field in pong:
                                        self.engine.device[field] = pong[field]
                                if desired["state"] == "working":
                                    self.engine.applied(desired, self.engine.last_command)
                        if pong.get("state") != desired["state"] or (desired.get("holding") and "hold-until-activity" not in self.engine.device.get("capabilities", [])):
                            signature = None
                        ping_time = time.monotonic()
                    self.process_scan()
                    self.stopping.wait(0.05)
                except (
                    OSError,
                    ValueError,
                    TimeoutError,
                ) as error:
                    with self.lock:
                        if self.stopping.is_set():
                            break
                        self.engine.device.update(
                            online=False, connecting=False, status="设备离线", last_error=str(error)
                        )
                    if self.connection:
                        self.connection.close()
                    self.connection = None
                    self.stopping.wait(2)
        finally:
            while not self.scan_requests.empty():
                request = self.scan_requests.get_nowait()
                request["error"] = "设备已断开，请重新连接后搜索。"
                request["done"].set()
            if self.connection:
                try:
                    self.seq += 1
                    self.send(
                        {
                            "type": "display",
                            "command_id": str(uuid.uuid4()),
                            "session_id": self.session,
                            "hub_id": self.hub_id,
                            "seq": self.seq,
                            "state": "idle",
                            "effect": "standard",
                            "brightness": 0,
                            "duration_ms": 0,
                            "lease_ms": 1000,
                        },
                        shutdown=True,
                    )
                    time.sleep(0.2)
                except Exception:
                    pass
                self.connection.close()
