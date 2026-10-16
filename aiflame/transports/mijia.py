"""Independent switch-only reminder session; no rapid relay flashing."""
import threading
import time
import uuid

from aiflame.devices.mijia import PlugClient, discover
from aiflame.devices.token_store import TokenStore
from aiflame.devices.models import hardware_model, MIJIA_PLUG_MODEL


def desired_power(desired, settings):
    if desired.get("device_test") and not desired.get("continuous"):
        return desired["state"] == "working"
    state = desired["state"]
    return bool(settings.get("state_power", {}).get(state, False))


class MijiaTransport(threading.Thread):
    def __init__(self, engine, lock, record):
        super().__init__(daemon=True)
        self.engine, self.lock, self.record = engine, lock, record
        self.stopping = threading.Event()
        self.client = None

    def connect(self):
        token = TokenStore(service=self.record["miio_token_ref"]).get(self.record["device_id"])
        if not token:
            raise ValueError("未找到插座凭据，请重新扫码接入")
        did = self.record["miio_did"]
        rows = discover(self.record.get("miio_host"), stopping=self.stopping)
        item = next((row for row in rows if row["did"] == did), None)
        if not item:
            item = next((row for row in discover(stopping=self.stopping) if row["did"] == did), None)
        if not item:
            raise OSError("未发现已添加的插座，请检查同一局域网连接")
        self.client = PlugClient(item["host"], token, did, self.stopping)
        self.client.open()
        info = self.client.command("miIO.info")["result"]
        if info.get("model") != hardware_model(self.record.get("hardware_model", MIJIA_PLUG_MODEL))["lan_model"]:
            raise ValueError("设备型号不匹配，未接管此设备")
        power = self.client.power()
        with self.lock:
            if self.stopping.is_set():
                raise OSError("服务已停止")
            self.record["miio_host"] = item["host"]
            self.engine.session_changed()
            self.engine.device = {"online": True, "connecting": False,
                "device_id": self.record["device_id"], "session_id": uuid.uuid4().hex,
                "connected_at": time.time(), "status": "米家插座局域网已连接", "last_error": None,
                "port": f"wifi://{item['host']}:54321", "firmware_version": info.get("fw_ver", ""),
                "protocol_version": "miIO / MIoT", "power": power, "capabilities": ["switch", "hold-until-activity"]}

    def run(self):
        previous, actual, heartbeat, next_change = None, None, 0, 0
        try:
            while not self.stopping.is_set():
                try:
                    if not self.client:
                        self.connect()
                        previous, actual = None, self.engine.device.get("power")
                    with self.lock:
                        desired, settings = self.engine.desired().copy(), self.engine.settings.copy()
                    power = desired_power(desired, settings)
                    signature = (desired["key"], desired["state"], power)
                    if signature != previous or time.monotonic() >= heartbeat:
                        # Check again immediately before mutating physical power.
                        with self.lock:
                            if self.engine.desired() != desired or desired_power(desired, self.engine.settings) != power:
                                continue
                        if actual != power:
                            if time.monotonic() < next_change:
                                self.stopping.wait(.1)
                                continue
                            self.client.set_power(power)
                            next_change = time.monotonic() + 1
                        actual = self.client.power()
                        if actual != power:
                            raise OSError("插座状态未与提醒一致")
                        with self.lock:
                            if not self.stopping.is_set() and self.engine.desired() == desired:
                                self.engine.device.update(power=actual, last_heartbeat=time.time())
                                self.engine.applied(desired, {"result": "applied", "command_id": "miio:" + str(self.client.sequence)})
                        previous, heartbeat = signature, time.monotonic() + 5
                    self.stopping.wait(.1)
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    if self.client:
                        self.client.close()
                    self.client = None
                    with self.lock:
                        self.engine.device.update(online=False, connecting=not self.stopping.is_set(),
                            status="插座连接未完成", last_error="请检查局域网；Token 失效时重新扫码接入")
                        self.engine.session_changed()
                    self.stopping.wait(3)
        finally:
            if self.client:
                # Shutdown is a bounded best effort; cancellation must not block receive.
                self.client.stopping = threading.Event()
                try:
                    self.client.set_power(False)
                except (OSError, ValueError):
                    pass
                self.client.close()
            with self.lock:
                self.engine.device.update(online=False, connecting=False)
