"""Mutual, instance-bound loopback authentication. The root secret never leaves disk/memory."""

import hashlib
import hmac
from io import BytesIO
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL = "seeend-hmac-v1"
TTL = 30
MAX_NONCES = 4096
HEX = re.compile(r"[0-9a-f]{32}")


class AuthError(ValueError):
    """Safe to display; contains no credentials, headers, URLs or peer payload."""


def _mac(key, values):
    wire = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, wire, hashlib.sha256).hexdigest()


def _key(token):
    if not isinstance(token, str) or not token:
        raise AuthError("本机管理凭据不可用")
    return hmac.digest(token.encode(), b"SeeEnd management key v1", "sha256")


def _digest(body):
    return hashlib.sha256(body or b"").hexdigest()


class ManagementServer:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.instance = secrets.token_hex(16)
        self.generation = secrets.token_hex(16)
        self.key = None
        self.nonces = {}
        self.lock = threading.RLock()

    def _sync(self, token):
        key = _key(token)
        if self.key != key:
            self.key = key
            self.generation = secrets.token_hex(16)
            self.nonces.clear()

    def challenge(self, nonce, token, app_name, app_version):
        if not isinstance(nonce, str) or HEX.fullmatch(nonce) is None:
            raise AuthError("管理挑战格式错误")
        with self.lock:
            self._sync(token)
            result = {"protocol": PROTOCOL, "instance": self.instance,
                      "generation": self.generation, "nonce": nonce,
                      "expires": int(self.clock()) + TTL,
                      "app_name": app_name, "app_version": app_version}
            result["proof"] = _mac(self.key, ["server", result])
            return result

    def verify(self, authorization, method, path, body, token):
        if not isinstance(authorization, str) or not authorization.startswith(PROTOCOL + " "):
            raise AuthError("需要签名的本机管理请求")
        parts = authorization[len(PROTOCOL) + 1:].split(".")
        if len(parts) != 5:
            raise AuthError("管理请求签名格式错误")
        instance, generation, expires, nonce, signature = parts
        if (any(HEX.fullmatch(value) is None for value in (instance, generation, nonce))
                or not re.fullmatch(r"[0-9]{1,12}", expires)
                or not re.fullmatch(r"[0-9a-f]{64}", signature)):
            raise AuthError("管理请求签名格式错误")
        expires = int(expires)
        with self.lock:
            self._sync(token)
            now = int(self.clock())
            if instance != self.instance or generation != self.generation or not now <= expires <= now + TTL:
                raise AuthError("管理会话已变化，请重新连接")
            values = ["request", instance, generation, expires, nonce, method, path, _digest(body)]
            if not hmac.compare_digest(signature, _mac(self.key, values)):
                raise AuthError("管理请求签名无效")
            # Invalid signatures cannot consume or replace a legitimate nonce.
            self.nonces = {value: until for value, until in self.nonces.items() if until >= now}
            if nonce in self.nonces:
                raise AuthError("管理请求已经处理，请勿重放")
            if len(self.nonces) >= MAX_NONCES:
                raise AuthError("管理请求过于频繁，请稍后重试")
            self.nonces[nonce] = expires
            return {"key": self.key, "instance": instance, "generation": generation, "nonce": nonce}


def response_proof(context, status, body):
    return _mac(context["key"], ["response", context["instance"], context["generation"],
                                 context["nonce"], status, _digest(body)])


def request_header(context, method, path, body=None, clock=time.time):
    nonce, expires = secrets.token_hex(16), int(clock()) + TTL
    signature = _mac(context["key"], ["request", context["instance"], context["generation"],
                     expires, nonce, method, path, _digest(body)])
    return PROTOCOL + " " + ".".join((context["instance"], context["generation"], str(expires), nonce, signature))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise urllib.error.HTTPError(request.full_url, code, "redirect blocked", headers, response)


class VerifiedResponse(BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status = self.code = status
        self.headers = headers

    def getcode(self):
        return self.status


class SignedOpener:
    """Every response is authenticated; no CookieJar or root Bearer is used."""
    def __init__(self, base, token_reader, opener=None, clock=time.time, max_response=32 * 1024 * 1024):
        self.base = base.rstrip("/")
        parsed = urllib.parse.urlsplit(self.base)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
            raise AuthError("管理连接只允许本机回环地址")
        self.token_reader, self.clock, self.max_response = token_reader, clock, max_response
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.context = None
        self.valid_until = 0
        self.lock = threading.RLock()

    def _challenge(self, key, timeout):
        nonce = secrets.token_hex(16)
        request = urllib.request.Request(self.base + "/api/auth/challenge?nonce=" + nonce)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                raw = response.read(4097)
                if len(raw) > 4096:
                    raise AuthError("本机服务身份验证失败")
                payload = json.loads(raw)
            proof = payload.pop("proof")
            now = int(self.clock())
            if (set(payload) != {"protocol", "instance", "generation", "nonce", "expires", "app_name", "app_version"}
                    or payload["protocol"] != PROTOCOL or payload["nonce"] != nonce
                    or payload["app_name"] != "SeeEnd"
                    or not isinstance(payload["app_version"], str)
                    or re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", payload["app_version"]) is None
                    or any(not isinstance(payload[field], str) or HEX.fullmatch(payload[field]) is None
                           for field in ("instance", "generation"))
                    or type(payload["expires"]) is not int or not now <= payload["expires"] <= now + TTL
                    or not isinstance(proof, str) or re.fullmatch(r"[0-9a-f]{64}", proof) is None
                    or not hmac.compare_digest(proof, _mac(key, ["server", payload]))):
                raise AuthError("本机服务身份验证失败")
        except urllib.error.HTTPError as error:
            error.close()
            raise AuthError("本机服务身份验证失败") from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AuthError("本机服务身份验证失败") from None
        self.context = {"key": key, "instance": payload["instance"], "generation": payload["generation"]}
        self.valid_until = payload["expires"]

    def open(self, request, timeout=5):
        request = urllib.request.Request(request) if isinstance(request, str) else request
        if not request.full_url.startswith(self.base + "/"):
            raise AuthError("管理请求地址无效")
        if request.has_header("Authorization") or request.has_header("Cookie"):
            raise AuthError("管理请求不能混用 Bearer 或 Cookie")
        key = _key(self.token_reader())
        path = request.full_url[len(self.base):]
        with self.lock:
            if self.context is None or self.context["key"] != key or self.valid_until <= int(self.clock()):
                self._challenge(key, min(timeout, 5))
            header = request_header(self.context, request.get_method(), path, request.data, self.clock)
            context = {**self.context, "nonce": header.split(".")[-2]}
        request.add_header("Authorization", header)
        failed = False
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response, failed = error, True
        with response:
            status, headers = response.getcode(), response.headers
            body = response.read(self.max_response + 1)
        proof = headers.get("X-SeeEnd-Proof", "")
        if (type(status) is not int or len(body) > self.max_response or not isinstance(proof, str)
                or re.fullmatch(r"[0-9a-f]{64}", proof) is None
                or not hmac.compare_digest(proof, response_proof(context, status, body))):
            self.context = None
            raise AuthError("本机响应验证失败，请重新连接；操作结果未确认")
        if failed:
            raise urllib.error.HTTPError(request.full_url, status, "authenticated request failed", headers, BytesIO(body))
        return VerifiedResponse(body, status, headers)
