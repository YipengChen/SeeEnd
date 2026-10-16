"""Yeelight LAN discovery and bounded request/response control (official LAN spec)."""
import ipaddress
import base64
import json
import socket
import time
from urllib.parse import urlsplit
from aiflame.devices.models import YEELIGHT_MODELS, YEELIGHT_CUBE_MODEL, hardware_model

DISCOVERY_ADDRESS = ("239.255.255.250", 1982)
CONTROL_PORT = 55443
DISCOVERY_TIMEOUT = 4.0
DISCOVERY_ATTEMPTS = 3
DISCOVERY_RETRY_INTERVAL = 1.0
# W3 (colorb, fw_ver 7) can accept TCP before its command handler is ready.
# Queries sent immediately may be silently lost; a bounded settle period avoids
# this on both the maintenance connection and the persistent transport.
CONNECTION_READY_DELAY = .5
# Cube uses its UDP pixel session, not quota-limited TCP frame commands.
CUBE_COMMAND_INTERVAL = .1
QUOTA_RETRY_DELAY = 65
REQUEST = (b'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1982\r\n'
           b'MAN: "ssdp:discover"\r\nST: wifi_bulb\r\n\r\n')


class YeelightQuotaError(ValueError):
    """Device explicitly refused traffic because its command quota was used."""


def raise_command_error(error):
    if isinstance(error, dict) and 'quota' in str(error.get('message', '')).lower():
        raise YeelightQuotaError("灯具指令限额已用完，等待 65 秒后自动重试")
    raise ValueError("Yeelight 拒绝指令：" + str(error))


def channel_properties(model_id):
    prefix = hardware_model(model_id)["command_prefix"]
    return [prefix + name for name in ("power", "bright", "rgb")]


def verify_control(client, model_id, properties=None):
    """Validate a real command reply without inventing device state.

    CubeLite 1.0.0 acknowledges setters but silently ignores get_prop. Its
    direct renderer uses full master brightness and scales pixels itself, so
    normalize that brightness here and require the actual setter ACK.
    """
    if model_id == YEELIGHT_CUBE_MODEL:
        response = client.command("set_bright", [100])
        if response.get("result") != ["ok"]:
            raise ValueError("Cube Lite 未确认亮度控制指令")
    else:
        response = client.command("get_prop", properties or channel_properties(model_id))
        if len(response["result"]) != 3 or response["result"][0] not in ("on", "off"):
            raise ValueError("此灯具未提供所选提醒灯的控制")


def matches_model(item, model_id):
    model = hardware_model(model_id)
    return item.get("model") in model.get("lan_models", (model["lan_model"],))


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
                  if matches_model(headers, key)
                  and (model_id is None or key == model_id)), None)
    if model is None:
        return None
    prefix = model["command_prefix"]
    required = set(model.get("required_methods", ("get_prop", prefix + "set_power", prefix + "start_cf")))
    # CubeLite firmware 1.0.0 advertises an empty support header on real
    # hardware. Discovery is only identification; connection still requires
    # a real control-command acknowledgement before persisting the device.
    omitted_cube_support = model["id"] == YEELIGHT_CUBE_MODEL and not support
    if (location.scheme != "yeelight" or host != sender or port != CONTROL_PORT
            or not device_id
            or len(device_id) > 80
            or (not omitted_cube_support and not required.issubset(support))):
        return None
    return {"device_id": "yeelight:" + device_id, "host": host, "port": port,
            "model": headers["model"], "hardware_model": model["id"],
            "name": headers.get("name") or model["name"],
            "firmware_version": headers.get("fw_ver", ""), "support": sorted(support)}


def discover(host=None, timeout=DISCOVERY_TIMEOUT, *, model_id=None, stopping=None):
    """Retry lossy UDP queries on one socket and merge all verified replies.

    Keep the original socket open so delayed replies to earlier rounds are not
    lost. A targeted lookup may finish as soon as that host is verified; a LAN
    search uses its full bounded window to include slower and multiple lamps.
    """
    host = lan_host(host) if host else None
    if timeout <= 0 or (stopping and stopping.is_set()):
        return []
    found = {}
    interval = min(DISCOVERY_RETRY_INTERVAL, timeout / DISCOVERY_ATTEMPTS)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        udp.bind(("", 0))
        started = time.monotonic()
        end, next_query, attempts = started + timeout, started, 0
        while True:
            now = time.monotonic()
            if now >= end or (stopping and stopping.is_set()):
                break
            if attempts < DISCOVERY_ATTEMPTS and now >= next_query:
                udp.sendto(REQUEST, (host, 1982) if host else DISCOVERY_ADDRESS)
                attempts += 1
                next_query = now + interval
            wait = end - time.monotonic()
            if attempts < DISCOVERY_ATTEMPTS:
                wait = min(wait, next_query - time.monotonic())
            if stopping:
                wait = min(wait, .2)
            if wait <= 0:
                continue
            udp.settimeout(wait)
            try:
                data, sender = udp.recvfrom(4096)
            except socket.timeout:
                continue  # A quiet round is not the end of the search.
            try:
                item = parse_discovery(data, sender[0], model_id)
            except (ValueError, UnicodeError):
                continue
            if item and (not host or item["host"] == host):
                found[item["device_id"]] = item
                if host:
                    break
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
                    raise_command_error(response["error"])
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


class BeamClient(YeelightClient):
    """Beam direct LED frames over UDP, with TCP status and session ACKs."""
    def __init__(self, host, stopping=None):
        super().__init__(host, stopping)
        self.udp = None
        self.token = None
        self.udp_seq = 0
        self.last_keep_alive = 0

    def udp_command(self, method, params, *, acknowledge=True):
        self.udp_seq += 1
        packet = {"id": self.udp_seq, "method": method, "params": params}
        if self.token:
            packet["token"] = self.token
        self.udp.send((json.dumps(packet, separators=(",", ":")) + "\r\n").encode())
        if not acknowledge:
            return {"id": self.udp_seq, "result": ["sent"]}
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self.stopping and self.stopping.is_set():
                raise OSError("服务已停止")
            try:
                response = json.loads(self.udp.recv(8192))
            except socket.timeout:
                continue
            if not isinstance(response, dict) or response.get("id") != self.udp_seq:
                continue
            if "error" in response:
                raise ValueError("Yeelight 拒绝指令：" + str(response["error"]))
            return response
        raise TimeoutError("Beam UDP 局域网会话未收到回执")

    def keep_alive(self):
        response = self.udp_command("udp_sess_keep_alive", ["keeplive_interval", 10])
        if response.get("result") != ["ok"]:
            raise ValueError("Beam 局域网会话回执无效")
        self.last_keep_alive = time.monotonic()

    def open(self):
        try:
            super().open()
            power = super().command("get_prop", ["power"])["result"]
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.connect((self.host, 55444))
            self.udp.settimeout(.2)
            response = self.udp_command("udp_sess_new", [])
            self.token = response.get("params", {}).get("token")
            if not isinstance(self.token, str) or not self.token or len(self.token) > 128:
                raise ValueError("Beam 局域网会话 token 无效")
            self.keep_alive()
            response = self.udp_command("activate_fx_mode", [{"mode": "direct"}])
            if response.get("result") != ["ok"]:
                raise ValueError("Beam 未确认直接灯点模式")
            self.udp_command("update_leds", [base64.b64encode(bytes(168 * 3)).decode()], acknowledge=False)
            initialization = [("set_bright", [100, "sudden", 0])]
            if power != ["on"]:
                initialization.append(("set_power", ["on", "sudden"]))
            for method, params in initialization:
                if self.udp_command(method, params).get("result") != ["ok"]:
                    raise ValueError("Beam 未确认灯点显示初始化")
            return self
        except Exception:
            self.close()
            raise

    def command(self, method, params):
        if method == "update_leds":
            response = self.udp_command(method, params, acknowledge=False)
            # Direct mode needs continuous frames. Do not wait for a session
            # ACK on each frame; renew independently every five seconds.
            if time.monotonic() - self.last_keep_alive >= 5:
                self.keep_alive()
            return response
        self.keep_alive()
        return super().command(method, params)

    def close(self):
        if self.udp:
            self.udp.close()
            self.udp = None
        self.token = None
        super().close()


class CubeClient(YeelightClient):
    """Cube Lite's token-authenticated UDP pixel session on port 55444.

    TCP update_leds consumes the ordinary command quota. UDP supports sustained
    frames and acknowledged setup/heartbeats; frames themselves remain sent.
    """
    def __init__(self, host, stopping=None):
        super().__init__(host, stopping)
        self.udp = None
        self.token = None
        self.direct = False
        self.last_keep_alive = 0
        self.last_frame = None
        self.last_frame_at = 0
        self.last_frame_response = None
        self.pending_heartbeat = None

    def udp_command(self, method, params, *, acknowledge=True):
        self.seq += 1
        packet = {"id": self.seq, "method": method, "params": params}
        if self.token:
            packet["token"] = self.token
        wire = (json.dumps(packet, separators=(",", ":")) + "\r\n").encode()
        self.udp.send(wire)
        if not acknowledge:
            return {"id": self.seq, "result": ["sent"]}
        deadline = time.monotonic() + 2
        retry_at = time.monotonic() + .25
        retries = 0
        while time.monotonic() < deadline:
            if self.stopping and self.stopping.is_set():
                raise OSError("服务已停止")
            try:
                response = json.loads(self.udp.recv(8192))
            except socket.timeout:
                # Retrying these idempotent controls with the SAME request id
                # avoids turning one lost UDP ACK into a full reconnection.
                # Session creation is excluded: a retry may rotate its token.
                if method != "udp_sess_new" and retries < 2 and time.monotonic() >= retry_at:
                    self.udp.send(wire)
                    retries += 1
                    retry_at = time.monotonic() + .25
                continue
            if not isinstance(response, dict):
                continue
            # Errors for earlier unacknowledged frames must not be discarded
            # while waiting for the next heartbeat's id.
            if "error" in response:
                raise_command_error(response["error"])
            if response.get("id") == self.seq:
                return response
        raise TimeoutError("Cube Lite UDP 局域网会话未收到回执")

    def keep_alive(self):
        response = self.udp_command("udp_sess_keep_alive", ["keeplive_interval", 10])
        if response.get("result") != ["ok"]:
            raise ValueError("Cube Lite 局域网会话回执无效")
        self.last_keep_alive = time.monotonic()

    def open(self):
        try:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.connect((self.host, 55444))
            self.udp.settimeout(.05)
            response = self.udp_command("udp_sess_new", [])
            self.token = response.get("params", {}).get("token")
            if not isinstance(self.token, str) or not self.token or len(self.token) > 128:
                raise ValueError("Cube Lite 局域网会话 token 无效")
            self.keep_alive()
            return self
        except Exception:
            self.close()
            raise

    def check_connection(self):
        import select
        if self.udp is None:
            raise OSError("Cube Lite 连接已断开")
        for _ in range(64):
            if not select.select([self.udp], [], [], 0)[0]:
                break
            response = json.loads(self.udp.recv(8192))
            if not isinstance(response, dict):
                continue
            if "error" in response:
                raise_command_error(response["error"])
            pending = self.pending_heartbeat
            if pending and response.get("id") == pending["id"]:
                if response.get("result") != ["ok"]:
                    raise ValueError("Cube Lite 局域网会话回执无效")
                self.last_keep_alive = time.monotonic()
                self.pending_heartbeat = None
        now = time.monotonic()
        pending = self.pending_heartbeat
        if pending:
            if now >= pending["deadline"]:
                raise TimeoutError("Cube Lite UDP 局域网会话未收到回执")
            if now >= pending["retry_at"] and pending["retries"] < 2:
                self.udp.send(pending["wire"])
                pending["retries"] += 1
                pending["retry_at"] = now + .25
        elif now - self.last_keep_alive >= 5:
            self.seq += 1
            wire = (json.dumps({"id": self.seq, "method": "udp_sess_keep_alive",
                                "params": ["keeplive_interval", 10], "token": self.token},
                               separators=(",", ":")) + "\r\n").encode()
            self.udp.send(wire)
            self.pending_heartbeat = {"id": self.seq, "wire": wire, "deadline": now + 2,
                                      "retry_at": now + .25, "retries": 0}

    def prepare_frame(self):
        """Finish potentially slow setup before the caller samples its state."""
        self.check_connection()
        if not self.direct:
            for name, values in (("activate_fx_mode", [{"mode": "direct"}]),
                                 ("set_bright", [100, "sudden", 0]),
                                 ("set_power", ["on", "sudden"])):
                if self.udp_command(name, values).get("result") != ["ok"]:
                    raise ValueError("Cube Lite 未确认灯点显示初始化")
            self.direct = True

    def command(self, method, params):
        if method != "update_leds":
            return self.udp_command(method, params)
        self.prepare_frame()
        # A periodic resend also repairs isolated UDP packet loss, even for
        # static screens. It is independent of the 5-second session heartbeat.
        if params == self.last_frame and time.monotonic() - self.last_frame_at < .2:
            return dict(self.last_frame_response)
        response = self.udp_command(method, params, acknowledge=False)
        self.last_frame = list(params)
        self.last_frame_at = time.monotonic()
        self.last_frame_response = dict(response)
        return response

    def close(self):
        if self.udp:
            self.udp.close()
        self.udp = None
        self.token = None
        self.direct = False
        self.last_frame = None
        self.last_frame_response = None
        self.pending_heartbeat = None
        super().close()
