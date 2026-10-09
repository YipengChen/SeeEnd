#!/usr/bin/python3
"""Restricted native-messaging bridge. No chat content or token leaves this host."""

import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parent
HOST = "com.aiflame.chatgpt"
MAX_BYTES = 4096
# Keep the existing development installation usable during store migration.
LEGACY_EXTENSION_ID = "hifannlfdbbhmmgecknoakpbhfihihbg"
# Display order: Similarweb worldwide monthly visits, September 2026.
# Provider-published traffic estimates, not vendor-reported clicks or app MAU.
# Sources: https://www.similarweb.com/website/<domain>/ (selected month: September 2026)
# chatgpt.com 5.9B; gemini.google.com 2.6B; claude.ai 1B;
# chat.deepseek.com 313.1M; doubao.com 154M; qianwen.com 30.1M;
# kimi.com 25.6M; yuanbao.tencent.com 10.8M; chatglm.cn 5.3M.
WEB_SOURCES = {
    "chatgpt-web": "ChatGPT",
    "gemini-web": "Gemini",
    "claude-web": "Claude",
    "deepseek-web": "DeepSeek",
    "doubao-web": "豆包",
    "qianwen-web": "千问",
    "kimi-web": "Kimi",
    "yuanbao-web": "元宝",
    "zhipu-web": "智谱清言",
}

WEB_URLS = {'claude-web': 'https://claude.ai/', 'gemini-web': 'https://gemini.google.com/', 'chatgpt-web': 'https://chatgpt.com/', 'deepseek-web': 'https://chat.deepseek.com/', 'qianwen-web': 'https://www.qianwen.com/', 'doubao-web': 'https://www.doubao.com/chat/', 'yuanbao-web': 'https://yuanbao.tencent.com/', 'kimi-web': 'https://www.kimi.com/', 'zhipu-web': 'https://chatglm.cn/'}

def extension_id(root=ROOT):
    installed = root / "bridge-config.json"
    if installed.exists():
        return json.loads(installed.read_text())["extension_id"]
    manifest = json.loads((root / "extension/manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    return "".join(chr(97 + int(c, 16)) for c in digest)


def extension_origins(root=ROOT):
    return tuple(dict.fromkeys(
        "chrome-extension://" + item + "/"
        for item in (extension_id(root), LEGACY_EXTENSION_ID)
    ))


def sanitize(message):
    fields = {
        "schema_version",
        "event_id",
        "source_id",
        "task_id",
        "seq",
        "state",
        "occurred_at",
        "ttl_ms",
    }
    if (
        not isinstance(message, dict)
        or set(message) - fields - {"reason"}
        or not fields <= set(message)
    ):
        raise ValueError("invalid_fields")
    if (
        not isinstance(message["source_id"], str)
        or message["source_id"] not in WEB_SOURCES
        or message["schema_version"] != "1.0"
    ):
        raise ValueError("invalid_source")
    for key in ("event_id", "task_id"):
        if not isinstance(message[key], str):
            raise ValueError("invalid_id")
        uuid.UUID(message[key])
    if type(message["seq"]) is not int or not 1 <= message["seq"] <= 2147483647:
        raise ValueError("invalid_sequence")
    if message["state"] not in ("working", "done", "idle", "error", "needs_input", "unknown", "cancelled", "timeout"):
        raise ValueError("invalid_state")
    if message["ttl_ms"] != 60000 or not isinstance(message["occurred_at"], str):
        raise ValueError("invalid_time")
    if message.get("reason") not in (
        None,
        "completed",
        "cancelled",
        "timeout",
        "failed",
    ):
        raise ValueError("invalid_reason")
    return {key: message[key] for key in fields | {"reason"} if key in message}


def forward(message):
    ping = isinstance(message, dict) and message.get("kind") == "ping"
    if ping and (
        set(message) - {"kind", "source_id"}
        or not isinstance(message.get("source_id", "chatgpt-web"), str)
        or message.get("source_id", "chatgpt-web") not in WEB_SOURCES
    ):
        raise ValueError("invalid_ping")
    event = None if ping else sanitize(message)
    source = message.get("source_id", "chatgpt-web") if ping else event["source_id"]
    installed = ROOT / "bridge-config.json"
    if installed.exists():
        config = json.loads(installed.read_text())
        # Retain old ChatGPT installations; other sources require bridge repair.
        token = config.get("tokens", {}).get(source)
        if token is None and source == "chatgpt-web":
            token = config.get("token")
        if not token:
            return {"ok": False, "error": "bridge_update_required"}
    else:
        token = __import__(
            "aiflame.core.paths", fromlist=["load_config"]
        ).load_config()["tokens"][source]
    request = urllib.request.Request(
        (
            "http://127.0.0.1:8765/v1/status"
            if ping
            else "http://127.0.0.1:8765/v1/events"
        ),
        data=None if ping else json.dumps(event).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        result = json.load(response)
    if ping:
        return {"ok": True, **result}
    return {
        "ok": True,
        "event_id": result["event_id"],
        "result": result.get(
            "result", "duplicate" if result.get("duplicate") else "received"
        ),
    }


def read_message(stream):
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise ValueError("invalid_frame")
    length = struct.unpack("=I", header)[0]
    if not 0 < length <= MAX_BYTES:
        raise ValueError("message_too_large")
    body = stream.read(length)
    if len(body) != length:
        raise ValueError("invalid_frame")
    return json.loads(body)


def write_message(stream, value):
    body = json.dumps(value).encode()
    stream.write(struct.pack("=I", len(body)) + body)
    stream.flush()


def main():
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    if len(sys.argv) < 2 or sys.argv[1] not in extension_origins():
        return 1
    while True:
        try:
            message = read_message(sys.stdin.buffer)
            if message is None:
                return 0
            result = forward(message)
        except urllib.error.HTTPError as error:
            result = {"ok": False, "error": "hub_rejected", "status": error.code}
        except (OSError, KeyError):
            result = {"ok": False, "error": "hub_unavailable"}
        except (ValueError, TypeError):
            write_message(sys.stdout.buffer, {"ok": False, "error": "invalid_message"})
            return 1
        write_message(sys.stdout.buffer, result)


if __name__ == "__main__":
    raise SystemExit(main())
