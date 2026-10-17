"""Transport lifecycle is independent of the UI and OS."""

import threading

from aiflame.devices.models import DEFAULT_MODEL, YEELIGHT_MODELS, SUPPORTED_MODELS, MIJIA_PLUG_MODELS
from aiflame.transports.yeelight import YeelightTransport
from aiflame.transports.mijia import MijiaTransport


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
                if not self.engine.settings["hardware_notifications"]:
                    self.running = False
                    self.engine.device.update(online=False, connecting=False, status="设备提醒已关闭", last_error=None)
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
            if self.worker.is_alive():
                return
            self.worker = None
            with self.lock:
                self.engine.device.update(online=False, connecting=True, status="正在连接设备", last_error=None)
        c = self.config
        if not c.get("device_id"):
            self.engine.device.update(online=False, connecting=False, status="未连接设备，请连接 Yeelight 灯具")
            return
        if c.get("hardware_model", DEFAULT_MODEL) not in SUPPORTED_MODELS:
            self.running = False
            self.engine.device.update(online=False, connecting=False, status="不支持的硬件型号")
            return
        transport_type = MijiaTransport if c.get("hardware_model") in MIJIA_PLUG_MODELS else YeelightTransport
        self.worker = transport_type(self.engine, self.lock, c)
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
        # fully released device; reminder changes use suspend() instead.
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
