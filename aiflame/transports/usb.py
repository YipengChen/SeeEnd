"""USB serial connection for the shared device protocol."""

import time
import serial
from serial.tools import list_ports
from aiflame.transports.base import DeviceTransport


class USBTransport(DeviceTransport):
    def __init__(self, engine, lock, hub_id, expected_device, port):
        super().__init__(engine, lock, hub_id, expected_device)
        self.port = port

    def connect(self):
        if self.stopping.is_set():
            raise OSError("服务已停止")
        candidates = [self.port] + ([] if getattr(self, "strict_port", False) else [
            p.device
            for p in list_ports.comports()
            if p.vid in (0x1A86, 0x303A) and p.device != self.port
        ])
        errors = []
        for path in candidates:
            if self.stopping.is_set():
                raise OSError("服务已停止")
            try:
                connection = serial.Serial(
                    baudrate=115200, timeout=0.15, write_timeout=2
                )
                connection.dtr = False
                connection.rts = False
                connection.port = path
                connection.open()
                self.connection = connection
                # An already-running board can answer immediately. Retry hello
                # for boards still booting instead of delaying every connection.
                for timeout in (0.4, 0.8, 2):
                    self.send({"type": "hello", "hub_id": self.hub_id})
                    try:
                        hello = self.receive(lambda x: x.get("type") == "hello", timeout)
                        break
                    except TimeoutError:
                        if timeout == 2:
                            raise
                if hello.get("device_id") != self.expected_device:
                    raise ValueError("设备身份不匹配")
                if hello.get("protocol_version") != "1.0":
                    raise ValueError("设备协议不兼容")
                self.validate_display(hello, getattr(self, "model_id", "ai-flame-v01"))
                with self.lock:
                    if self.stopping.is_set():
                        raise OSError("服务已停止")
                    if self.session and self.session != hello["session_id"]:
                        self.engine.session_changed()
                    self.session = hello["session_id"]
                    self.seq = hello.get("seq", -1)
                    self.engine.device = {
                        **hello,
                        "online": True,
                        "status": "已连接",
                        "port": path,
                        "last_error": None,
                        "connected_at": time.time(),
                    }
                return
            except (OSError, serial.SerialException, ValueError, TimeoutError) as error:
                errors.append(path + ": " + str(error))
                if self.connection:
                    self.connection.close()
                self.connection = None
        raise OSError("未找到已配对开发板；" + "; ".join(errors))
