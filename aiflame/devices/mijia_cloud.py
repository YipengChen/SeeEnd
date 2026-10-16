"""One-shot Xiaomi QR login; only supported plug credentials survive selection.

Protocol adapted from Xiaomi-cloud-tokens-extractor (MIT, Piotr Machowski).
No password input, external callback listener, persistent cloud session or logs.
"""
import base64
import hashlib
import http.cookiejar
import json
import os
import socket
import threading
import time
import uuid
from urllib import request, parse, error

from aiflame.devices.models import MIJIA_PLUG_MODELS, hardware_model
from aiflame.core.events import EventError
from aiflame.devices.mijia_crypto import rc4
from aiflame.devices.token_store import valid_token

REGIONS = ("cn", "de", "us", "ru", "tw", "sg", "in", "i2")


def xiaomi_url(url):
    parsed = parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not any(parsed.hostname == host or (parsed.hostname or "").endswith("." + host)
                       for host in ("mi.com", "xiaomi.com"))):
        raise ValueError("登录服务器地址未通过验证，请稍后重新扫码")
    return url


class XiaomiRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, xiaomi_url(newurl))


class QRCloud:
    def __init__(self):
        self.cookies = http.cookiejar.CookieJar()
        self.opener = request.build_opener(request.HTTPCookieProcessor(self.cookies), XiaomiRedirect())
        self.agent = uuid.uuid4().hex + " APP/com.xiaomi.mihome APPV/10.5.201"
        self.user_id = self.security = self.service_token = self.location = None

    def fetch(self, url, fields=None, headers=None, timeout=10, limit=2_000_000):
        body = parse.urlencode(fields).encode() if fields is not None else None
        headers = {"User-Agent": self.agent, **(headers or {})}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        with self.opener.open(request.Request(xiaomi_url(url), data=body, headers=headers), timeout=timeout) as response:
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ValueError("小米服务器响应过大，请稍后重试")
            return data

    @staticmethod
    def json(data):
        return json.loads(data.decode().removeprefix("&&&START&&&"))

    def begin(self):
        query = parse.urlencode({"_qrsize": "480", "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "callback": "https://sts.api.io.mi.com/sts", "_hasLogo": "false", "sid": "xiaomiio",
            "_locale": "en_GB", "_dc": str(int(time.time() * 1000))})
        data = self.json(self.fetch("https://account.xiaomi.com/longPolling/loginUrl?" + query))
        image = self.fetch(xiaomi_url(data["qr"]), limit=300_000)
        return {"image": base64.b64encode(image).decode(), "login_url": xiaomi_url(data["loginUrl"]),
                "poll_url": xiaomi_url(data["lp"]), "timeout": min(180, max(30, int(data.get("timeout", 120))))}

    def poll(self, url, timeout=120):
        # The confirmation is one-shot. Never poll it again to recover an STS
        # callback timeout: that would require the user to confirm a second time.
        data = self.json(self.fetch(url, timeout=timeout))
        self.user_id = str(data["userId"])
        self.security = data["ssecurity"]
        self.location = xiaomi_url(data["location"])
        if not self.user_id.isdigit() or len(base64.b64decode(self.security, validate=True)) < 16:
            raise ValueError("小米登录未完成，请重新扫码")

    def authorize(self):
        token = next((cookie.value for cookie in self.cookies if cookie.name == "serviceToken"), None)
        if not token:
            self.fetch(self.location)
            token = next((cookie.value for cookie in self.cookies if cookie.name == "serviceToken"), None)
        if not token:
            raise ValueError("小米授权未完成")
        self.service_token = token

    def api(self, region, path, data):
        nonce = base64.b64encode(os.urandom(8) + (int(time.time()) // 60).to_bytes(4, "big")).decode()
        key = hashlib.sha256(base64.b64decode(self.security) + base64.b64decode(nonce)).digest()
        signed_nonce = base64.b64encode(key).decode()
        fields = {"data": json.dumps(data, separators=(",", ":"))}

        def signature(values):
            parts = ["POST", path, *[f"{key}={value}" for key, value in values.items()], signed_nonce]
            return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()

        fields["rc4_hash__"] = signature(fields)
        fields = {name: base64.b64encode(rc4(value.encode(), key)).decode() for name, value in fields.items()}
        fields["signature"] = signature(fields)
        fields.update(ssecurity=self.security, _nonce=nonce)
        server = "https://" + ("" if region == "cn" else region + ".") + "api.io.mi.com/app"
        headers = {"MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4", "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "Cookie": f"userId={self.user_id}; serviceToken={self.service_token}; yetAnotherServiceToken={self.service_token}"}
        response = self.fetch(server + path, fields, headers)
        try:
            result = self.json(rc4(base64.b64decode(response), key))
        except (ValueError, UnicodeError):
            raise ValueError("无法读取米家设备，请确认地区后重新扫码") from None
        if result.get("code") != 0 or "result" not in result:
            raise ValueError("米家设备查询未成功，请确认地区后重新扫码")
        return result["result"]

    def plugs(self, region, stopping):
        homes = self.api(region, "/v2/homeroom/gethome", {"fg": True, "fetch_share": True,
            "fetch_share_dev": True, "limit": 300, "app_ver": 7})
        selected = {}
        for home in homes.get("homelist", []):
            if stopping.is_set():
                break
            devices = self.api(region, "/v2/home/home_device_list", {"home_owner": int(self.user_id),
                "home_id": home["id"], "limit": 200, "get_split_device": True, "support_smart_home": True})
            for device in devices.get("device_info") or []:
                did, token = str(device.get("did", "")), device.get("token", "")
                model = next((hardware_model(key) for key in MIJIA_PLUG_MODELS
                              if hardware_model(key)["lan_model"] == device.get("model")), None)
                if model and did.isdigit() and valid_token(token):
                    selected[did] = {"did": did, "name": str(device.get("name", model["name"]))[:100],
                        "host": str(device.get("localip", "")), "token": token, "hardware_model": model["id"]}
            # Other device credentials are not retained by this object.
        return selected

    def close(self):
        self.cookies.clear()
        self.user_id = self.security = self.service_token = self.location = None


class MijiaLogin:
    def __init__(self, factory=QRCloud):
        self.factory, self.guard = factory, threading.RLock()
        self.session = None
        self.devices = {}
        self.stopping = threading.Event()
        self.thread = None

    def status(self):
        with self.guard:
            return dict(self.session) if self.session else None

    def start(self, region="cn"):
        if region not in REGIONS:
            raise EventError("请选择米家账号所在地区")
        self.cancel()
        with self.guard:
            self.stopping = threading.Event()
            self.session = {"id": uuid.uuid4().hex, "state": "starting", "region": region,
                            "message": "正在获取小米登录二维码"}
            identity, stopping = self.session["id"], self.stopping
            self.thread = threading.Thread(target=self._work, args=(identity, region, stopping), daemon=True)
            self.thread.start()
            return dict(self.session)

    def update(self, identity, **values):
        with self.guard:
            if self.session and self.session["id"] == identity and not self.stopping.is_set():
                self.session.update(values)
                return True
        return False

    def _work(self, identity, region, stopping):
        cloud = self.factory()
        stage = "waiting"
        try:
            qr = cloud.begin()
            deadline = time.monotonic() + qr["timeout"]
            if not self.update(identity, state="waiting", qr_image=qr["image"], login_url=qr["login_url"],
                    expires_at=time.time() + qr["timeout"], message="请扫码登录插座所属的小米账号"):
                return
            while not stopping.is_set() and time.monotonic() < deadline:
                try:
                    cloud.poll(qr["poll_url"], timeout=max(1, deadline - time.monotonic()))
                    break
                except (TimeoutError, socket.timeout):
                    continue
                except error.URLError as problem:
                    if isinstance(problem.reason, (TimeoutError, socket.timeout)):
                        continue
                    raise
            else:
                self.update(identity, state="expired", qr_image="", login_url="", message="二维码已过期，请重新扫码")
                return
            if stopping.is_set():
                return
            stage = "authorizing"
            if not self.update(identity, state=stage, qr_image="", login_url="",
                               message="手机已确认登录，正在完成小米授权…"):
                return
            self._retry_cloud(cloud.authorize, stopping)
            if stopping.is_set():
                return
            stage = "loading"
            if not self.update(identity, state=stage, message="登录已完成，正在查找米家智能插座 3 / 4…"):
                return
            devices = self._retry_cloud(lambda: cloud.plugs(region, stopping), stopping)
            cloud.close()
            with self.guard:
                if stopping.is_set() or not self.session or self.session["id"] != identity:
                    return
                self.devices = devices
                self.session.update(state="selecting", expires_at=time.time() + 180,
                    message="请选择要接入的插座" if devices else "未找到米家智能插座 3 / 4，请确认账号和地区后重试",
                    devices=[{key: value for key, value in device.items() if key != "token"} for device in devices.values()])
            if not stopping.wait(180):
                with self.guard:
                    if self.session and self.session["id"] == identity:
                        self.devices.clear()
                        self.session.update(state="expired", devices=[], message="选择已过期，请重新扫码")
        except Exception:
            self.update(identity, state="failed", qr_image="", login_url="", devices=[],
                        message={"authorizing": "手机已确认登录，但小米授权未完成。请检查网络后重新登录。",
                                 "loading": "小米登录已完成，但设备查询未成功。请检查网络和账号地区后重试。"}.get(
                                     stage, "小米登录或设备查询未完成，请检查网络、地区后重新扫码"))
        finally:
            cloud.close()

    @staticmethod
    def _retry_cloud(operation, stopping):
        for attempt in range(3):
            if stopping.is_set():
                return None
            try:
                return operation()
            except (TimeoutError, ConnectionError, error.URLError) as problem:
                if (isinstance(problem, error.HTTPError) and problem.code not in (429, 502, 503, 504)) or attempt == 2:
                    raise
                if stopping.wait(0.5 * (attempt + 1)):
                    return None

    def take(self, identity, did):
        with self.guard:
            if (not self.session or self.session["id"] != identity or self.session["state"] != "selecting"
                    or self.session["expires_at"] <= time.time() or did not in self.devices):
                raise EventError("请重新扫码并选择插座", 409)
            device = dict(self.devices[did])
            self.devices.clear()
            self.stopping.set()
            self.session.update(state="finished", devices=[], message="凭据已交给本地连接，云登录会话已结束")
            return device

    def cancel(self, identity=None):
        with self.guard:
            if identity and (not self.session or self.session["id"] != identity):
                return
            self.stopping.set()
            self.devices.clear()
            if self.session:
                self.session.update(state="cancelled", qr_image="", login_url="", devices=[], message="扫码已取消")
