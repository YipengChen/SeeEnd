"""Wi-Fi transport for the existing task and device command protocol."""

import json
import socket
import time
from aiflame.transports.base import DeviceTransport
from aiflame.devices.wifi_protocol import PORT, DISCOVERY_PORT, proof


class SocketLine:
    def __init__(self, sock):
        self.sock = sock
        self.buffer = bytearray()

    @property
    def timeout(self):
        return self.sock.gettimeout()

    @timeout.setter
    def timeout(self, value):
        self.sock.settimeout(value)

    def write(self, data):
        self.sock.sendall(data)

    def flush(self):
        pass

    def read_until(self, mark, limit):
        while len(self.buffer) < limit:
            end = self.buffer.find(mark)
            if end >= 0:
                result = self.buffer[: end + 1]
                del self.buffer[: end + 1]
                return bytes(result)
            block = self.sock.recv(min(1024, limit - len(self.buffer)))
            if not block:
                raise OSError("Wi-Fi 连接已断开")
            self.buffer.extend(block)
        result = bytes(self.buffer)
        self.buffer.clear()
        return result

    def close(self):
        self.sock.close()


class WiFiTransport(DeviceTransport):
    def __init__(self, engine, lock, hub_id, expected_device, secret, last_ip=None):
        super().__init__(engine, lock, hub_id, expected_device)
        self.secret = secret
        self.last_ip = last_ip

    def discover(self):
        if self.stopping.is_set():
            return []
        candidates = [self.last_ip] if self.last_ip else []
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            udp.settimeout(0.7)
            request = json.dumps(
                {"type": "discover", "device_id": self.expected_device}
            ).encode()
            udp.sendto(request, ("255.255.255.255", DISCOVERY_PORT))
            end = time.monotonic() + 0.7
            while time.monotonic() < end and not self.stopping.is_set():
                try:
                    udp.settimeout(max(0.01, end - time.monotonic()))
                    data, address = udp.recvfrom(256)
                except socket.timeout:
                    break
                try:
                    result = json.loads(data)
                    if (
                        result.get("device_id") == self.expected_device
                        and result.get("port") == PORT
                    ):
                        candidates.append(address[0])
                except (ValueError, AttributeError):
                    continue
        except OSError:
            pass
        finally:
            udp.close()
        return list(dict.fromkeys(ip for ip in candidates if ip))

    def addresses(self):
        # Try the last authenticated address without waiting for broadcast.
        cached = self.last_ip
        if cached:
            yield cached
        for address in self.discover():
            if address != cached:
                yield address

    def connect(self):
        errors = []
        for address in self.addresses():
            if self.stopping.is_set():
                raise OSError("服务已停止")
            try:
                sock = socket.create_connection((address, PORT), 2)
                sock.settimeout(2)
                self.connection = SocketLine(sock)
                challenge = self.receive(lambda x: x.get("type") == "challenge", 2)
                if (
                    challenge.get("device_id") != self.expected_device
                    or challenge.get("protocol_version") != "1.0"
                ):
                    raise ValueError("设备身份或协议不匹配")
                nonce = challenge.get("nonce")
                if not isinstance(nonce, str):
                    raise ValueError("握手挑战无效")
                self.send(
                    {
                        "type": "hello",
                        "hub_id": self.hub_id,
                        "proof": proof(self.secret, nonce, self.hub_id),
                    }
                )
                hello = self.receive(
                    lambda x: x.get("type") in ("hello", "rejected"), 2
                )
                if (
                    hello.get("type") != "hello"
                    or hello.get("device_id") != self.expected_device
                ):
                    raise ValueError("设备认证失败")
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
                    self.last_ip = address
                    self.engine.device = {
                        **hello,
                        "online": True,
                        "status": "Wi-Fi 已连接",
                        "port": "wifi://" + address + ":" + str(PORT),
                        "last_error": None,
                        "connected_at": time.time(),
                    }
                return
            except (OSError, ValueError, TimeoutError, KeyError) as error:
                errors.append(address + ": " + str(error))
                if self.connection:
                    self.connection.close()
                self.connection = None
        raise OSError(
            "未发现已配对 Wi-Fi 开发板" + ("；" + "; ".join(errors) if errors else "")
        )
