"""Bounded HTTPS iLink transport, based on Tencent's published client protocol.

Protocol reference: Tencent/openclaw-weixin 2.4.10, docs/protocol.md.
No OpenClaw runtime, model service, or incoming chat content is required here.
"""
import base64
import json
import re
import secrets
import socket
import struct
import uuid
import zlib
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from aiflame import __version__

ORIGIN = "https://ilinkai.weixin.qq.com"
PROTOCOL_VERSION = "2.4.10"
CLIENT_VERSION = 132106
MAX_RESPONSE = 1024 * 1024


class WechatError(Exception):
    def __init__(self, code="network"):
        self.code = code
        super().__init__("Weixin request failed: " + str(code))


def trusted_origin(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+weixin\.qq\.com", host)
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError()
        return "https://" + host
    except (ValueError, TypeError, AttributeError):
        raise WechatError("untrusted_host") from None


def bounded_string(value, maximum=8192):
    return isinstance(value, str) and 0 < len(value) <= maximum and "\x00" not in value


def qr_image(value):
    """Encode the supplied QR content locally; never fetch its URL."""
    import qrcode
    if not bounded_string(value, 2048):
        raise WechatError("invalid_response")
    code = qrcode.QRCode(box_size=6, border=4)
    code.add_data(value)
    code.make(fit=True)
    rows = code.get_matrix()
    scale, size = 6, len(rows) * 6
    pixels = b"".join((b"\0" + b"".join((b"\0" if bit else b"\xff") * scale for bit in row)) * scale
                      for row in rows)
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ILinkClient:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(NoRedirect())

    def request(self, origin, path, *, token=None, body=None, query=None, timeout=15):
        origin = trusted_origin(origin)
        if not path.startswith("/ilink/bot/"):
            raise WechatError("invalid_path")
        url = origin + path + ("?" + urlencode(query) if query else "")
        headers = {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str(CLIENT_VERSION)}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.update({"Content-Type": "application/json", "AuthorizationType": "ilink_bot_token",
                            "X-WECHAT-UIN": base64.b64encode(str(secrets.randbits(32)).encode()).decode()})
            if token:
                headers["Authorization"] = "Bearer " + token
        try:
            with self.opener.open(Request(url, data=data, headers=headers), timeout=timeout) as response:
                if response.status != 200:
                    raise WechatError("http")
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise WechatError("invalid_response")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise WechatError("invalid_response")
            for key in ("ret", "errcode"):
                if key in result and (type(result[key]) is not int or result[key] != 0):
                    raise WechatError(result[key] if type(result[key]) is int else "invalid_response")
            return result
        except WechatError:
            raise
        except (TimeoutError, socket.timeout):
            raise WechatError("timeout") from None
        except HTTPError:
            raise WechatError("http") from None
        except URLError as error:
            raise WechatError("timeout" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "network") from None
        except (ValueError, UnicodeError, OSError):
            raise WechatError("network") from None

    def new_qr(self, tokens=()):
        result = self.request(ORIGIN, "/ilink/bot/get_bot_qrcode", query={"bot_type": "3"},
                              body={"local_token_list": list(tokens)[-10:]})
        if not bounded_string(result.get("qrcode")) or not bounded_string(result.get("qrcode_img_content"), 2048):
            raise WechatError("invalid_response")
        return result

    def qr_status(self, origin, code, verify_code=None):
        query = {"qrcode": code}
        if verify_code:
            query["verify_code"] = verify_code
        return self.request(origin, "/ilink/bot/get_qrcode_status", query=query, timeout=35)

    def call(self, account, method, body, timeout=15):
        return self.request(account["base_url"], "/ilink/bot/" + method, token=account["token"],
                            body={**body, "base_info": {"channel_version": PROTOCOL_VERSION,
                                                      "bot_agent": "SeeEnd/" + __version__}}, timeout=timeout)

    def updates(self, account, cursor):
        try:
            result = self.call(account, "getupdates", {"get_updates_buf": cursor}, timeout=35)
        except WechatError as error:
            if error.code == "timeout":
                # A transport timeout may precede any server response. Keep it
                # distinct from a successful, empty long-poll response.
                return None
            raise
        # iLink omits optional default-valued fields in successful responses.
        # request() already rejects explicit nonzero/invalid business codes.
        # Match the official monitor's msgs ?? [] without discarding a cursor.
        messages = result.get("msgs")
        if messages is None:
            result = {**result, "msgs": []}
        elif not isinstance(messages, list):
            raise WechatError("invalid_response")
        return result

    def send(self, account, context, text, client_id=None):
        self.call(account, "sendmessage", {"msg": {
            "from_user_id": "", "to_user_id": account["user_id"], "client_id": client_id or str(uuid.uuid4()),
            "message_type": 2, "message_state": 2, "context_token": context,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        }})
        # A valid HTTP 200 JSON response may omit ret, including an empty object.
        # Explicit ret/errcode failures are still rejected by request().
