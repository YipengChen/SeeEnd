"""On-demand ESP32 discovery through the active, authenticated device session."""
import threading


def normalize(networks):
    strongest = {}
    for network in networks:
        ssid, signal = network.get("ssid"), network.get("signal")
        if not isinstance(ssid, str) or not ssid or type(signal) is not int:
            continue
        if ssid not in strongest or signal > strongest[ssid]["signal"]:
            strongest[ssid] = {"ssid": ssid, "signal": signal}
    return sorted(strongest.values(), key=lambda x: (-x["signal"], x["ssid"]))


class WiFiScan:
    def __init__(self, transport):
        self.transport = transport
        self.lock = threading.Lock()
        self.result = {"state": "idle", "networks": [], "message": "展开列表，由 ESP32 搜索附近的 2.4 GHz Wi-Fi。"}

    def status(self):
        with self.lock:
            return dict(self.result)

    def start(self):
        with self.lock:
            if self.result["state"] != "running":
                self.result = {"state": "running", "networks": [], "message": "ESP32 正在搜索附近网络…"}
                threading.Thread(target=self._scan, daemon=True).start()
            return dict(self.result)

    def _scan(self):
        try:
            with self.transport.lock:
                device = self.transport.engine.device
                worker = self.transport.worker
                if self.transport.engine.settings["mode"] != "hardware" or not device["online"] or worker is None:
                    raise RuntimeError("请先通过 USB 或 Wi-Fi 连接 ESP32，再展开列表；也可手动输入。")
                if "wifi-scan" not in device.get("capabilities", []):
                    raise RuntimeError("请先通过 USB 更新灯光程序至 0.4.4 或更新版本，再读取 ESP32 的网络列表。")
            networks = normalize(worker.scan_networks())
            with self.transport.lock:
                if worker is not self.transport.worker or worker.stopping.is_set() or not self.transport.engine.device["online"]:
                    raise RuntimeError("设备连接已变化，请重新展开列表。")
            result = {"state": "succeeded", "networks": networks, "source": "esp32",
                      "message": f"ESP32 找到 {len(networks)} 个网络，按信号由强到弱排列。" if networks else "ESP32 未发现网络；可重新展开列表或手动输入隐藏网络。"}
        except (RuntimeError, OSError, ValueError) as error:
            result = {"state": "failed", "networks": [], "source": "esp32", "message": str(error)}
        with self.lock:
            self.result = result


class FleetWiFiScan:
    """Keep scan results and workers scoped to the requested physical lamp."""
    def __init__(self, transport):
        self.transport = transport
        self.scans = {}

    def scanner(self, device_id=None):
        from aiflame.core.events import EventError
        if device_id is None:
            device_id = self.transport.config.get("device_id") or next(iter(self.transport.records), "")
        if device_id and device_id not in self.transport.records:
            raise EventError("未知目标设备", 422)
        manager = self.transport.connection(device_id) if device_id else self.transport.fallback
        if device_id not in self.scans:
            self.scans[device_id] = WiFiScan(manager)
        elif self.scans[device_id].transport is not manager:
            self.scans[device_id] = WiFiScan(manager)
        return self.scans[device_id]

    def start(self, device_id=None):
        return self.scanner(device_id).start()

    def status(self, device_id=None):
        return self.scanner(device_id).status()
