#!/usr/bin/python3
"""Restricted native-messaging bridge. No chat content or token leaves this host."""

import base64
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlencode

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
    "chatgpt-web": "ChatGPT 网页版",
    "gemini-web": "Gemini 网页版",
    "claude-web": "Claude 网页版",
    "deepseek-web": "DeepSeek 网页版",
    "doubao-web": "豆包 网页版",
    "qianwen-web": "千问 网页版",
    "kimi-web": "Kimi 网页版",
    "yuanbao-web": "腾讯元宝 网页版",
    "zhipu-web": "智谱清言 网页版",
}

WEB_URLS = {'claude-web': 'https://claude.ai/', 'gemini-web': 'https://gemini.google.com/', 'chatgpt-web': 'https://chatgpt.com/', 'deepseek-web': 'https://chat.deepseek.com/', 'qianwen-web': 'https://www.qianwen.com/', 'doubao-web': 'https://www.doubao.com/chat/', 'yuanbao-web': 'https://yuanbao.tencent.com/', 'kimi-web': 'https://www.kimi.com/', 'zhipu-web': 'https://chatglm.cn/'}

def extension_id(root=ROOT):
    installed = root / "bridge-config.json"
    if installed.exists():
        return json.loads(installed.read_text(encoding="utf-8"))["extension_id"]
    manifest = json.loads((root / "extension/manifest.json").read_text(encoding="utf-8"))
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
        or set(message) - fields - {"reason", "return_target"}
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
    if message["state"] == "error":
        message = {**message, "state": "unknown", "reason": message.get("reason") or "failed"}
    if message["state"] not in ("working", "done", "idle", "needs_input", "unknown", "cancelled", "timeout"):
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
    if "return_target" in message:
        target = message["return_target"]
        if not isinstance(target, dict) or set(target) != {"kind", "client_id", "locator_id"} or target["kind"] != "web":
            raise ValueError("invalid_return_target")
        for key in ("client_id", "locator_id"):
            if not isinstance(target[key], str):
                raise ValueError("invalid_return_target")
            uuid.UUID(target[key])
    return {key: message[key] for key in fields | {"reason", "return_target"} if key in message}


def token_for(source):
    installed = ROOT / "bridge-config.json"
    if installed.exists():
        config = json.loads(installed.read_text(encoding="utf-8"))
        token = config.get("tokens", {}).get(source)
        if token is None and source == "chatgpt-web":
            token = config.get("token")
        return token
    return __import__("aiflame.core.paths", fromlist=["load_config"]).load_config()["tokens"][source]


def bridge_request(path, body=None, *, port=8765, timeout=7):
    token = token_for("chatgpt-web")
    if not token:
        raise ValueError("bridge_update_required")
    request = urllib.request.Request(f"http://127.0.0.1:{port}" + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def forward(message, port=8765):
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
        config = json.loads(installed.read_text(encoding="utf-8"))
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
            f"http://127.0.0.1:{port}/v1/status"
            if ping
            else f"http://127.0.0.1:{port}/v1/events"
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
    class SilentParser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)
    parser = SilentParser(add_help=False)
    parser.add_argument("origin")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--parent-window")  # Chromium supplies this on Windows.
    try:
        args = parser.parse_args()
    except ValueError:
        return 1
    if args.origin not in extension_origins() or not 1 <= args.port <= 65535:
        return 1
    stopping = threading.Event()
    write_lock = threading.Lock()
    connection = None

    def emit(value):
        with write_lock:
            if not stopping.is_set():
                write_message(sys.stdout.buffer, value)

    def poll_returns(client_id, connection_id):
        while not stopping.is_set():
            try:
                query = urlencode({"client_id": client_id, "connection_id": connection_id})
                response = bridge_request("/v1/task-return/poll?" + query, port=args.port)
                for command in response["commands"]:
                    emit(command)
            except (OSError, ValueError, KeyError):
                try:
                    emit({"kind": "return-disconnected"})
                except OSError:
                    pass
                return

    while True:
        try:
            message = read_message(sys.stdin.buffer)
            if message is None:
                stopping.set()
                return 0
            kind = message.get("kind") if isinstance(message, dict) else None
            if kind == "return-hello":
                if connection is not None or set(message) != {"kind", "client_id"} or not isinstance(message["client_id"], str):
                    raise ValueError("invalid_return_hello")
                uuid.UUID(message["client_id"])
                result = bridge_request("/v1/task-return/connect", {"client_id": message["client_id"]}, port=args.port)
                connection = {"client_id": message["client_id"], "connection_id": result["connection_id"]}
                emit({"kind": "return-ready", "ok": True})
                threading.Thread(target=poll_returns, args=tuple(connection.values()), daemon=True).start()
                continue
            elif kind == "return-result":
                if connection is None or set(message) != {"kind", "request_id", "status"} or not isinstance(message["request_id"], str) or message["status"] not in ("opened", "missing", "failed"):
                    raise ValueError("invalid_return_result")
                result = bridge_request("/v1/task-return/result", {**connection, "request_id": message["request_id"], "status": message["status"]}, port=args.port)
                result["kind"] = "return-ack"
            else:
                result = forward(message, args.port)
        except urllib.error.HTTPError as error:
            result = {"ok": False, "error": "hub_rejected", "status": error.code}
        except (OSError, KeyError):
            result = {"ok": False, "error": "hub_unavailable"}
        except (ValueError, TypeError):
            emit({"ok": False, "error": "invalid_message"})
            stopping.set()
            return 1
        emit(result)


if __name__ == "__main__":
    raise SystemExit(main())
