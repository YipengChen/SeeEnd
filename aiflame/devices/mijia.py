"""Bounded, authenticated miIO UDP transport for cuco.plug.v3."""
import hashlib
import hmac
import ipaddress
import json
import socket
import struct
import time
import threading

from aiflame.devices.yeelight import lan_host as yeelight_host
from aiflame.devices.token_store import valid_token
from aiflame.devices.mijia_crypto import aes

HELLO = bytes.fromhex("21310020" + "ff" * 28)


def lan_host(host):
    value = yeelight_host(host)
    if not ipaddress.IPv4Address(value).is_private:
        raise ValueError("请使用插座的局域网 IPv4 地址")
    return value


def parse_hello(data):
    if len(data) != 32 or data[:4] != b"\x21\x31\x00\x20":
        raise ValueError("无效的 miIO 握手")
    identity, stamp = struct.unpack(">II", data[8:16])
    if identity in (0, 0xffffffff):
        raise ValueError("无效的设备身份")
    return str(identity), stamp


def discover(host=None, stopping=None, timeout=2):
    stopping = stopping or threading.Event()
    target = lan_host(host) if host else "255.255.255.255"
    rows = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        connection.settimeout(.2)
        for _ in range(3):
            connection.sendto(HELLO, (target, 54321))
        end = time.monotonic() + timeout
        while time.monotonic() < end and not stopping.is_set():
            try:
                data, address = connection.recvfrom(1024)
                identity, _ = parse_hello(data)
                rows[identity] = {"did": identity, "host": lan_host(address[0]),
                                  "name": "米家设备（身份待验证）"}
            except socket.timeout:
                continue
            except ValueError:
                continue
    return list(rows.values())


class PlugClient:
    def __init__(self, host, token, did, stopping=None):
        self.host = lan_host(host)
        if not valid_token(token) or not str(did).isdigit() or not 0 < int(did) < 0xffffffff:
            raise ValueError("插座身份或 Token 无效")
        self.did, self.token = int(did), bytes.fromhex(token)
        self.key = hashlib.md5(self.token).digest()
        self.iv = hashlib.md5(self.key + self.token).digest()
        self.stopping = stopping or threading.Event()
        self.sequence = 0
        self.connection = None

    def open(self):
        self.connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.connection.connect((self.host, 54321))
        self.connection.settimeout(.2)
        self.connection.send(HELLO)
        identity, self.stamp = parse_hello(self._receive())
        if int(identity) != self.did:
            self.close()
            raise ValueError("此 IP 对应另一台设备，请重新扫描局域网")
        self.handshake_at = time.monotonic()
        return self

    def _receive(self):
        end = time.monotonic() + 2
        while not self.stopping.is_set() and time.monotonic() < end:
            try:
                return self.connection.recv(65536)
            except socket.timeout:
                pass
        raise OSError("插座未响应，请检查 IP、Token 和局域网连接")

    def command(self, method, params=None):
        self.sequence += 1
        payload = aes(json.dumps({"id": self.sequence, "method": method, "params": params or []},
                         separators=(",", ":")).encode(), self.key, self.iv)
        header = struct.pack(">HHIII", 0x2131, 32 + len(payload), 0, self.did,
                             self.stamp + int(time.monotonic() - self.handshake_at) + 1)
        self.connection.send(header + hashlib.md5(header + self.token + payload).digest() + payload)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            data = self._receive()
            if (len(data) < 48 or data[:2] != b"\x21\x31" or struct.unpack(">H", data[2:4])[0] != len(data)
                    or struct.unpack(">I", data[8:12])[0] != self.did):
                continue
            if not hmac.compare_digest(data[16:32], hashlib.md5(data[:16] + self.token + data[32:]).digest()):
                raise ValueError("插座 Token 校验失败，请重新扫码获取")
            response = json.loads(aes(data[32:], self.key, self.iv, decrypt=True).rstrip(b"\x00"))
            if response.get("id") != self.sequence:
                continue
            if "error" in response or "result" not in response:
                raise ValueError("插座未接受控制指令，请检查型号及固件兼容性")
            return response
        raise OSError("插座回执超时")

    def power(self):
        rows = self.command("get_properties", [{"did": str(self.did), "siid": 2, "piid": 1}])["result"]
        row = next((item for item in rows if item.get("siid") == 2 and item.get("piid") == 1), None)
        if not row or row.get("code") != 0 or type(row.get("value")) is not bool:
            raise ValueError("插座未提供有效的开关状态")
        return row["value"]

    def set_power(self, power):
        response = self.command("set_properties", [{"did": str(self.did), "siid": 2, "piid": 1, "value": bool(power)}])
        rows = response["result"]
        if not rows or any(row.get("code") != 0 for row in rows):
            raise ValueError("插座未确认目标开关状态")
        for _ in range(5):
            if self.power() == power:
                return response
            if self.stopping.wait(.1):
                break
        raise ValueError("插座未确认目标开关状态")

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None
