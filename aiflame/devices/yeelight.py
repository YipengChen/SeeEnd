"""Yeelight LAN discovery and bounded request/response control (official LAN spec)."""
import ipaddress
import json
import socket
import time
from urllib.parse import urlsplit
from aiflame.devices.models import YEELIGHT_MODELS, hardware_model

DISCOVERY_ADDRESS = ("239.255.255.250", 1982)
CONTROL_PORT = 55443
# W3 (colorb, fw_ver 7) can accept TCP before its command handler is ready.
# Queries sent immediately may be silently lost; a bounded settle period avoids
# this on both the maintenance connection and the persistent transport.
CONNECTION_READY_DELAY = .5
REQUEST = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1982\r\n'
           b'MAN: "ssdp:discover"\r\nST: wifi_bulb\r\n\r\n')


def channel_properties(model_id):
    prefix = hardware_model(model_id)["command_prefix"]
    return [prefix + name for name in ("power", "bright", "rgb")]


def matches_model(item, model_id):
    return item.get("model") == hardware_model(model_id)["lan_model"]


def lan_host(value):
    try:
        address = ipaddress.IPv4Address(value)
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise ValueError("请输入灯具的有效 IPv4 地址") from None
    if address.is_loopback or address.is_multicast or address.is_unspecified or int(address) == 0xffffffff:
        raise ValueError("请输入灯具的局域网 IPv4 地址")
    return str(address)


def parse_discovery(data, sender, model_id=None):
    headers = {}
    for line in data.decode("utf-8").splitlines()[1:]:
        key, sep, value = line.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()
    location = urlsplit(headers.get("location", ""))
    host = lan_host(location.hostname)
    port = location.port or CONTROL_PORT
    device_id = headers.get("id", "")
    support = set(headers.get("support", "").split())
    model = next((hardware_model(key) for key in YEELIGHT_MODELS
                  if headers.get("model") == hardware_model(key)["lan_model"]
                  and (model_id is None or key == model_id)), None)
    if model is None:
        return None
    prefix = model["command_prefix"]
    required = {"get_prop", prefix + "set_power", prefix + "start_cf"}
    if (location.scheme != "yeelight" or host != sender or port != CONTROL_PORT
            or not device_id
            or len(device_id) > 80 or not required.issubset(support)):
        return None
    return {"device_id": "yeelight:" + device_id, "host": host, "port": port,
            "model": model["lan_model"], "hardware_model": model["id"],
            "name": headers.get("name") or model["name"],
            "firmware_version": headers.get("fw_ver", ""), "support": sorted(support)}


def discover(host=None, timeout=1.5, *, model_id=None):
    host = lan_host(host) if host else None
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        udp.sendto(REQUEST, (host, 1982) if host else DISCOVERY_ADDRESS)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            udp.settimeout(max(.01, end - time.monotonic()))
            try:
                data, sender = udp.recvfrom(4096)
            except socket.timeout:
                break
            try:
                item = parse_discovery(data, sender[0], model_id)
            except (ValueError, UnicodeError):
                continue
            if item and (not host or item["host"] == host):
                found[item["device_id"]] = item
    return list(found.values())


class YeelightClient:
    def __init__(self, host, stopping=None):
        self.host, self.stopping = lan_host(host), stopping
        self.sock = None
        self.buffer = bytearray()
        self.seq = 0

    def open(self):
        try:
            self.sock = socket.create_connection((self.host, CONTROL_PORT), 2)
        except TimeoutError:
            raise TimeoutError(f"无法连接灯具 {self.host}:55443：连接超时，请检查同一局域网、路由器设备隔离及局域网控制开关") from None
        except ConnectionRefusedError:
            raise ConnectionRefusedError(f"灯具 {self.host}:55443 拒绝连接，请在 Yeelight APP 中开启局域网控制，或断电重启灯具后重试") from None
        except OSError as error:
            raise OSError(f"无法连接灯具 {self.host}:55443：{error}") from None
        self.sock.settimeout(.2)
        if self.stopping:
            if self.stopping.wait(CONNECTION_READY_DELAY):
                self.close()
                raise OSError("服务已停止")
        else:
            time.sleep(CONNECTION_READY_DELAY)
        return self

    def command(self, method, params):
        self.seq += 1
        packet = {"id": self.seq, "method": method, "params": params}
        self.sock.sendall((json.dumps(packet, separators=(",", ":")) + "\r\n").encode())
        end = time.monotonic() + 2
        while time.monotonic() < end:
            if self.stopping and self.stopping.is_set():
                raise OSError("服务已停止")
            while b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer[:] = rest
                response = json.loads(line)
                if not isinstance(response, dict) or response.get("id") != self.seq:
                    continue  # Ignore asynchronous props notifications and unrelated replies.
                if "error" in response:
                    raise ValueError("Yeelight 拒绝指令：" + str(response["error"]))
                if not isinstance(response.get("result"), list):
                    raise ValueError("Yeelight 回执格式无效")
                return response
            try:
                block = self.sock.recv(4096)
            except socket.timeout:
                continue
            if not block:
                raise OSError("Yeelight 连接已断开")
            self.buffer.extend(block)
            if len(self.buffer) > 16384:
                raise ValueError("Yeelight 回执过长")
        raise TimeoutError(f"已连接灯具 {self.host}:55443，但 {method} 指令在 2 秒内未收到回执。请在 Yeelight APP 中关闭再开启局域网控制，或断电重启灯具后重试")

    def close(self):
        if self.sock:
            self.sock.close()
            self.sock = None
