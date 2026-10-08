"""Transport lifecycle is independent of the UI and OS."""

import threading

from aiflame.transports.usb import USBTransport
from aiflame.transports.wifi import WiFiTransport
from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS
from aiflame.transports.yeelight import YeelightTransport


class TransportManager:
    def __init__(self, engine, lock, config):
        self.engine, self.lock, self.config = engine, lock, config
        self.worker = None
        self.guard = threading.RLock()
        self.reaper = None
        self.retiring_worker = None
        self.running = False

    def start(self):
        with self.guard:
            with self.lock:
                if self.engine.settings["mode"] != "hardware":
                    self.running = False
                    self.engine.device.update(online=False, connecting=False, status="虚拟模式", last_error=None)
                    return
            if self.config.get("connection_paused"):
                self.running = False
                with self.lock:
                    self.engine.device.update(online=False, connecting=False, status="已主动断开", last_error=None)
                return
            self.running = True
            with self.lock:
                if self.config.get("device_id") and not self.engine.device["online"]:
                    self.engine.device.update(connecting=True, status="正在连接设备", last_error=None)
            self._start_worker()

    def _start_worker(self):
        # Called under guard. A retiring worker keeps its place until its final
        # lights-off command and connection close have both completed.
        if self.worker:
            return
        c = self.config
        if not c.get("device_id"):
            self.engine.device.update(online=False, connecting=False, status="未连接设备，请连接火焰灯")
            return
        if c.get("hardware_model") in YEELIGHT_MODELS:
            self.worker = YeelightTransport(self.engine, self.lock, c)
        elif c.get("transport") == "wifi" and c.get("wifi_device_secret"):
            self.worker = WiFiTransport(
                self.engine,
                self.lock,
                getattr(self, "hub_id", c.get("hub_id", "")),
                c["device_id"],
                c["wifi_device_secret"],
                c.get("wifi_last_ip"),
            )
        else:
            self.worker = USBTransport(
                self.engine, self.lock, getattr(self, "hub_id", c.get("hub_id", "")), c["device_id"], c.get("port", "")
            )
        self.worker.strict_port = getattr(self, "strict_port", False)
        self.worker.model_id = c.get("hardware_model", DEFAULT_MODEL)
        self.worker.start()

    def suspend(self):
        """Revoke hardware immediately; finish slow I/O cleanup off the API path."""
        with self.guard:
            self.running = False
            with self.lock:
                worker = self.worker
                if worker:
                    worker.stopping.set()
            if worker and self.retiring_worker is not worker:
                self.retiring_worker = worker
                self.reaper = threading.Thread(target=self._reap, args=(worker,), daemon=True)
                self.reaper.start()

    def _reap(self, worker):
        worker.join()
        with self.guard:
            if self.worker is worker:
                self.worker = None
            if self.retiring_worker is worker:
                self.retiring_worker = None
                self.reaper = None
            if self.running and self.worker is None:
                self.start()

    def stop(self):
        # Maintenance and app shutdown still require exclusive access to a
        # fully released device; mode changes use suspend() instead.
        with self.guard:
            self.running = False
            with self.lock:
                worker = self.worker
                if worker:
                    worker.stopping.set()
        if worker:
            worker.join(timeout=12)
            if worker.is_alive():
                raise RuntimeError("设备连接仍在退出，请稍后重试")
            with self.guard:
                if self.worker is worker:
                    self.worker = None
        with self.lock:
            self.engine.device.update(online=False, connecting=False, status="设备维护中")
