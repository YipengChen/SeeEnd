"""Loopback-only HTTP service, embeddable in a desktop app or run headlessly."""

import http.cookies
import json
import secrets
import signal
import threading
import time
import uuid
from urllib.parse import urlsplit, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from aiflame import __version__
from aiflame.core.paths import ensure_runtime, load_config
from aiflame.core.engine import Engine
from aiflame.core.activity import ActivityMonitor
from aiflame.core.verification import begin_verification
from aiflame.core.verification import verification_summary, verification_evidence
from aiflame.core.events import EventError
from aiflame.transports.fleet import FleetTransportManager as TransportManager
from aiflame.devices.fleet import FleetDeviceService as DeviceService
from aiflame.devices.models import DEFAULT_MODEL
from aiflame.integrations.codex.diagnostics import diagnostics
from aiflame.integrations.manual.actions import DemoTasks, test as manual_test
from aiflame.integrations.chatgpt_web.downloads import extension_archive, extension_folder_info, prepare_extension_folder
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES
from aiflame.integrations.testing import TriggerTests
from aiflame.integrations.registry import SOURCES, integration_contract
from aiflame.integrations.desktop_clients.service import DesktopClients
from aiflame.integrations.cli_tools.providers import CLI_SOURCES


class Hub:
    def __init__(self, port=8765, config=None, runtime=None):
        self.port = port
        self.stopping = threading.Event()
        self.operation_lock = threading.RLock()
        self.request_guard = threading.RLock()
        self.active_posts = 0
        self.resetting = False
        self.runtime = runtime or ensure_runtime()
        self.config = config if config is not None else load_config()
        self.lock = threading.RLock()
        self.engine = Engine(self.runtime / "events.sqlite", sources=SOURCES)
        self.transport = TransportManager(self.engine.flame, self.lock, self.config)
        self.engine.flame.transport = self.transport
        self.devices = DeviceService(
            self.config, self.runtime, self.transport, self.lock
        )
        self.tests = TriggerTests(self)
        self.limits = {}
        self.demos = DemoTasks(self)
        self.desktop_clients = DesktopClients(self)
        self.server = None
        self.thread = None
        self.hardware_enabled = False
        self.lifecycle_thread = threading.Thread(target=self._lifecycle_loop, daemon=True)
        self.activity_monitor = ActivityMonitor(self)

    def start(self, hardware=True):
        self.hardware_enabled = hardware
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        self.server.context = self
        self.port = self.server.server_address[1]
        if hardware:
            self.transport.start()
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
        )
        self.thread.start()
        self.lifecycle_thread.start()
        self.activity_monitor.start()
        if hardware:
            self.desktop_clients.start()

    def _lifecycle_loop(self):
        while not self.stopping.wait(0.5):
            with self.lock:
                self.engine.tick()
                self.engine.desktop.expire()

    def update_settings(self, patch):
        # Revoke the old worker before saving, without waiting for its I/O.
        with self.operation_lock:
            if self.stopping.is_set():
                raise EventError("应用正在退出", 409)
            with self.lock:
                settings = self.engine.validate_settings(patch)
                changed = settings["mode"] != self.engine.settings["mode"]
            if changed and (self.devices.busy() or self.tests.busy()):
                raise EventError("请等待设备操作或诊断结束后再切换模式", 409)
            if changed:
                self.transport.suspend()
            with self.lock:
                self.engine.update_settings(patch)
                if changed:
                    self.engine.device.update(
                        online=False, last_error=None,
                        connecting=bool(settings["mode"] == "hardware" and self.hardware_enabled and self.config.get("device_id")),
                        status="虚拟模式" if settings["mode"] == "virtual" else "正在连接设备" if self.hardware_enabled and self.config.get("device_id") else "未连接设备",
                    )
            if changed and self.hardware_enabled:
                self.transport.start()
            with self.lock:
                return self.engine.snapshot()

    def stop(self):
        with self.operation_lock:
            if self.devices.busy():
                raise RuntimeError("设备操作正在进行，请等待完成后退出")
            self.stopping.set()
            with self.lock:
                self.engine.cancel_preview()
        if self.lifecycle_thread.is_alive():
            self.lifecycle_thread.join(timeout=2)
        if self.activity_monitor.is_alive():
            self.activity_monitor.join(timeout=2)
        self.desktop_clients.stop()
        if self.tests.thread:
            self.tests.thread.join(timeout=12)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.transport.stop()
        with self.lock:
            self.engine.db.close()

    def reset_app(self):
        from aiflame.hub.reset import reset_app
        with self.request_guard:
            if self.resetting or self.active_posts:
                raise EventError("请等待当前操作结束后重置 APP", 409)
            self.resetting = True
        try:
            with self.operation_lock:
                if self.stopping.is_set() or self.devices.busy() or self.tests.busy():
                    raise EventError("请等待设备操作或诊断结束后重置 APP", 409)
                return reset_app(self)
        finally:
            with self.request_guard:
                self.resetting = False


class Handler(BaseHTTPRequestHandler):
    server_version = "SeeEnd/" + __version__

    @property
    def c(self):
        return self.server.context

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def reply(self, code, payload, content_type=None, cookie=False, filename=None):
        if isinstance(payload, bytes):
            body = payload
        elif isinstance(payload, str):
            body = payload.encode("utf-8")
        else:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header(
            "Content-Type", content_type or "application/json; charset=utf-8"
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'",
        )
        if cookie:
            self.send_header(
                "Set-Cookie",
                "aiflame="
                + self.c.config["dashboard"]
                + "; HttpOnly; SameSite=Strict; Path=/",
            )
        if filename:
            self.send_header(
                "Content-Disposition", 'attachment; filename="' + filename + '"'
            )
        self.end_headers()
        self.wfile.write(body)

    def validate_host(self):
        host = self.headers.get("Host", "")
        if host not in (f"127.0.0.1:{self.c.port}", f"localhost:{self.c.port}"):
            raise EventError("Host 不被允许", 403)
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            raise EventError("禁止跨来源请求", 403)
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise EventError("禁止跨站请求", 403)

    def identity(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            for source, saved in self.c.config["tokens"].items():
                if secrets.compare_digest(auth[7:].encode(), saved.encode()):
                    return source
        try:
            cookies = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            if "aiflame" in cookies and secrets.compare_digest(
                cookies["aiflame"].value.encode(), self.c.config["dashboard"].encode()
            ):
                return "dashboard"
        except http.cookies.CookieError:
            pass
        raise EventError("需要有效凭据", 401)

    def rate_limit(self, source):
        now = time.monotonic()
        tokens, updated = self.c.limits.get(source, (30, now))
        tokens = min(30, tokens + (now - updated) * 10)
        if tokens < 1:
            raise EventError("请求过于频繁，请稍后重试", 429)
        self.c.limits[source] = (tokens - 1, now)

    def do_GET(self):
        try:
            self.validate_host()
            if not self.path.startswith(("/api/", "/v1/")):
                raise EventError("路径不存在", 404)
            if self.path == "/api/session":
                self.reply(200, {"app_name": "SeeEnd", "app_version": __version__}, cookie=True)
                return
            identity = self.identity()
            c = self.c
            if urlsplit(self.path).path in ("/api/integration-contract", "/api/verification-evidence"):
                if identity != "dashboard":
                    raise EventError("需要桌面 App 会话", 403)
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if urlsplit(self.path).path == "/api/integration-contract":
                    if query:
                        raise EventError("契约查询不接受额外参数")
                    self.reply(200, integration_contract())
                else:
                    if set(query) != {"source"} or len(query["source"]) != 1:
                        raise EventError("请选择一个真实来源，不接受额外或重复参数")
                    with c.lock:
                        self.reply(200, verification_evidence(c.engine, query["source"][0]))
                return
            if urlsplit(self.path).path in ("/api/integration-guide", "/api/integration-check"):
                if identity != "dashboard":
                    raise EventError("需要桌面 App 会话", 403)
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - {"source", "config_dir", "profile"} or any(len(values) != 1 for values in query.values()):
                    raise EventError("接入查询包含未知或重复参数")
                source = query.get("source", [""])[0]
                directory = query.get("config_dir", [None])[0] or None
                profile = query.get("profile", [None])[0] or None
                if source == "deepseek-harness" and directory is not None:
                    raise EventError("DeepSeek Harness 不支持 config_dir；请通过 DSH_HOME 选择实际配置根目录", 400, "config_invalid")
                from aiflame.integrations.cli_tools.guidance import integration_guide
                try:
                    guide = integration_guide(source, directory, profile)
                except (ValueError, OSError):
                    raise EventError("请选择支持的编程工具，并核对配置目录")
                if urlsplit(self.path).path == "/api/integration-guide":
                    self.reply(200, guide)
                    return
                with c.lock:
                    enabled = c.engine.settings["source_enabled"]
                    evidence = verification_summary(c.engine)
                    if source == "codex":
                        report = diagnostics(c.engine.db, directory, enabled=enabled.get(source), verification=evidence.get(source))
                    else:
                        from aiflame.integrations.cli_tools.diagnostics import diagnostics as cli_diagnostics
                        report = cli_diagnostics(c.engine.db, source=source, config_dirs={source: directory},
                                                 profiles={source: profile}, source_enabled=enabled,
                                                 verification=evidence)["integrations"][0]
                self.reply(200, report)
                return
            if identity == "dashboard" and self.path == "/api/cli-integrations":
                from aiflame.integrations.cli_tools.diagnostics import diagnostics as cli_diagnostics

                with c.lock:
                    result = cli_diagnostics(c.engine.db, source_enabled=c.engine.settings["source_enabled"],
                                             verification=verification_summary(c.engine))
                self.reply(200, result)
                return
            if identity == "dashboard" and self.path == "/api/desktop-clients":
                self.reply(200, c.desktop_clients.status())
                return
            if identity == "dashboard" and urlsplit(self.path).path == "/api/device":
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                self.reply(200, c.devices.status(query.get("model", [None])[0], query.get("device_id", [None])[0]))
                return
            if identity == "dashboard" and self.path == "/api/browser-extension":
                self.reply(200, extension_folder_info(c.runtime))
                return
            if identity == "dashboard" and self.path == "/api/trigger-test":
                self.reply(200, c.tests.status())
                return
            if (
                identity == "dashboard"
                and self.path == "/api/browser-extension/download"
            ):
                self.reply(
                    200,
                    extension_archive(),
                    "application/zip",
                    filename="SeeEnd-Web-extension.zip",
                )
                return
            with c.lock:
                if self.path == "/api/indicator" and identity == "dashboard":
                    self.reply(
                        200,
                        {
                            "display": {
                                key: value
                                for key, value in c.engine.display_status().items()
                                if key in ("state", "simulated")
                            },
                            "settings": {
                                key: c.engine.settings[key]
                                for key in ("paused", "brightness", "mode", "language")
                            },
                            "emergency": c.engine.emergency,
                            "muted": c.engine.muted(),
                            "brightness_off": c.engine.flame.brightness_off(),
                            "device": {"online": c.engine.device["online"], "connecting": c.engine.device.get("connecting", False)},
                            "maintenance": c.devices.busy(),
                            "hardware_paired": bool(c.transport.records),
                            "devices": c.transport.snapshot(),
                            "hardware_model": c.config.get("hardware_model", DEFAULT_MODEL),
                        },
                    )
                elif self.path == "/api/status" and identity == "dashboard":
                    self.reply(
                        200,
                        {
                            **c.engine.snapshot(),
                            "app_version": __version__,
                            "app_name": "SeeEnd",
                            "maintenance": c.devices.busy(),
                            "hardware_paired": bool(c.transport.records),
                            "devices": c.transport.snapshot(),
                            "hardware_model": c.config.get("hardware_model", DEFAULT_MODEL),
                        },
                    )
                elif self.path == "/api/codex" and identity == "dashboard":
                    self.reply(
                        200,
                        diagnostics(c.engine.db, enabled=c.engine.settings["source_enabled"].get("codex"),
                                    verification=verification_summary(c.engine).get("codex")),
                    )
                elif self.path == "/v1/status" and identity != "dashboard":
                    self.reply(
                        200,
                        {
                            "source_enabled": c.engine.settings["source_enabled"].get(
                                identity, False
                            ),
                            "device_online": c.engine.device["online"],
                            "muted": c.engine.muted(),
                        },
                    )
                elif self.path.startswith("/v1/events/"):
                    row = c.engine.db.execute(
                        "SELECT * FROM events WHERE id=?",
                        (self.path.rsplit("/", 1)[1],),
                    ).fetchone()
                    if not row:
                        raise EventError("事件不存在", 404)
                    if identity != "dashboard" and identity != row["source"]:
                        raise EventError("无权读取该来源", 403)
                    self.reply(200, dict(row))
                else:
                    raise EventError("页面不存在", 404)
        except EventError as error:
            self.reply(error.status, {"error": str(error), "error_code": error.error_code})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def read_body(self, max_size):
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
            raise EventError("无效的 Content-Length")
        try:
            length = int(lengths[0])
        except ValueError:
            raise EventError("无效的 Content-Length")
        if not 0 < length <= max_size:
            raise EventError("请求大小不受支持", 413)
        body = self.rfile.read(length)
        if len(body) != length:
            raise EventError("请求体不完整", 400)
        return body

    def do_POST(self):
        if self.path == "/api/reset":
            return self._do_POST()
        with self.c.request_guard:
            if self.c.resetting:
                self.reply(503, {"error": "APP 正在重置，请稍后重试"})
                return
            self.c.active_posts += 1
        try:
            return self._do_POST()
        finally:
            with self.c.request_guard:
                self.c.active_posts -= 1

    def _do_POST(self):
        try:
            self.validate_host()
            identity = self.identity()
            c = self.c
            with c.lock:
                self.rate_limit(identity)
            if self.headers.get_content_type() != "application/json":
                raise EventError("请发送 JSON")
            try:
                body = json.loads(self.read_body(4096))
            except (ValueError, UnicodeError):
                raise EventError("JSON 格式错误")
            if not isinstance(body, dict):
                raise EventError("请求必须为对象")
            if self.path == "/v1/events":
                if identity == "dashboard":
                    raise EventError("此接口需要来源凭据", 403)
                with c.lock:
                    response, code = c.engine.accept(body, identity)
                self.reply(code, response)
                return
            if identity != "dashboard":
                raise EventError("需要桌面 App 会话", 403)
            if self.path == "/api/browser-extension/prepare":
                if body:
                    raise EventError("准备扩展文件夹不接受额外字段")
                self.reply(200, prepare_extension_folder(c.runtime))
                return
            if self.path == "/api/reset":
                if set(body) != {"confirm"} or body["confirm"] is not True:
                    raise EventError("请先确认重置 APP")
                self.reply(200, c.reset_app(), cookie=True)
                return
            if self.path == "/api/notifications/claim":
                if body:
                    raise EventError("通知领取请求不接受额外字段")
                with c.lock:
                    c.engine.tick()
                    self.reply(200, {"notifications": c.engine.desktop.claim()})
                return
            if self.path == "/api/notifications/ack":
                with c.lock:
                    self.reply(200, c.engine.desktop.acknowledge(body))
                return
            if self.path == "/api/desktop-clients/permissions":
                if body:
                    raise EventError("权限请求不接受额外字段")
                self.reply(200, c.desktop_clients.request_permissions())
                return
            if self.path in ("/api/integrations/install", "/api/integrations/uninstall"):
                source = body.get("source")
                removing = self.path.endswith("/uninstall")
                if set(body) - {"source", "config_dir", "profile"}:
                    raise EventError("接入请求包含未知字段")
                for key in ("config_dir", "profile"):
                    if key in body and not isinstance(body[key], str):
                        raise EventError("接入配置必须为文字")
                try:
                    with c.operation_lock:
                        if isinstance(source, str) and source in CLI_SOURCES:
                            from aiflame.integrations.cli_tools.install import install, settings, configured, registered_directories
                            from aiflame.integrations.cli_tools.guidance import directory_for

                            _, before = settings()
                            if source == "deepseek-harness":
                                repairing = body.get("profile") in before.get(source, {}).get("profiles", [])
                            else:
                                directory = directory_for(source, body.get("config_dir") or None)
                                repairing = str(directory.resolve()) in registered_directories(source, before) or configured(source, directory)
                            install(source, config_dir=body.get("config_dir") or None,
                                    profile=body.get("profile") or None, uninstall=removing)
                            with c.lock:
                                enabled = dict(c.engine.settings["source_enabled"])
                            if removing:
                                _, saved = settings()
                                remaining = (saved.get(source, {}).get("profiles", []) if source == "deepseek-harness"
                                             else registered_directories(source, saved))
                                enabled[source] = enabled[source] and bool(remaining)
                            elif not repairing:
                                enabled[source] = True
                            c.update_settings({"source_enabled": enabled})
                            if not removing and enabled[source]:
                                with c.lock:
                                    begin_verification(c.engine, source)
                            message = ("已移除所选 SeeEnd 接入；其他配置已保留。" if removing else
                                       "配置好了。重开工具，发一条新任务后点击「检查」。")
                        elif source == "codex":
                            from aiflame.integrations.codex.install import install
                            from aiflame.integrations.cli_tools.guidance import directory_for
                            from aiflame.integrations.cli_tools.install import settings, configured, registered_directories

                            directory = directory_for(source, body.get("config_dir") or None)
                            _, before = settings()
                            repairing = str(directory.resolve()) in registered_directories(source, before) or configured(source, directory)
                            install(home=directory, uninstall=removing)
                            with c.lock:
                                enabled = dict(c.engine.settings["source_enabled"])
                            if removing:
                                _, saved = settings()
                                enabled[source] = enabled[source] and bool(registered_directories(source, saved))
                            elif not repairing:
                                enabled[source] = True
                            c.update_settings({"source_enabled": enabled})
                            if not removing and enabled[source]:
                                with c.lock:
                                    begin_verification(c.engine, source)
                            message = ("已移除 SeeEnd Hooks，其他配置已保留。" if removing else
                                       "配置好了。如 Codex 提示信任 Hooks，请确认；重开会话，发一条新任务后点击「检查」。")
                        elif isinstance(source, str) and source in WEB_SOURCES and not removing:
                            from aiflame.integrations.chatgpt_web.install import install

                            install("chrome")
                            message = "通信配置已准备。请刷新 AI 对话网页，并发送一条新提问验证；配置完成还不代表网页接入已验证。"
                        else:
                            raise EventError("接入方式不受支持")
                except json.JSONDecodeError as error:
                    raise EventError(f"未修改配置：JSON 第 {error.lineno} 行、第 {error.colno} 列无法解析。先修正格式后重试。", 409, "config_invalid")
                except FileNotFoundError:
                    raise EventError("自动接入未完成：配置文件或必要工具不存在。核对目录；DeepSeek 需让 SeeEnd 找到 dsh 和 pnpm。", 409, "missing_dependency")
                except OSError:
                    raise EventError("自动接入未完成：无法读写配置目录，请检查权限。", 409, "io_error")
                except (ValueError, RuntimeError) as error:
                    raise EventError("自动接入未完成：" + str(error), 409, getattr(error, "error_code", "integration_conflict"))
                self.reply(200, {"message": message})
                return
            if self.path == "/api/device/light-settings":
                with c.operation_lock:
                    self.reply(200, c.devices.update_light_settings(body))
                return
            if self.path == "/api/device/test-light":
                if set(body) != {"device_id"}:
                    raise EventError("请选择要试亮的设备")
                with c.operation_lock:
                    if c.devices.busy() or c.tests.busy():
                        raise EventError("请等待设备维护或诊断结束", 409)
                    self.reply(202, c.transport.test_light(body["device_id"]))
                return
            if self.path == "/api/device/delete":
                with c.operation_lock:
                    if c.tests.busy():
                        raise EventError("请等待触发测试结束", 409)
                    self.reply(200, c.devices.delete(body))
                return
            if self.path == "/api/device":
                with c.operation_lock:
                    if c.tests.busy():
                        raise EventError("请等待触发测试结束", 409)
                    with c.lock:
                        c.engine.cancel_preview()
                    self.reply(202, c.devices.start(body))
                return
            if self.path == "/api/trigger-test":
                with c.operation_lock:
                    with c.lock:
                        c.engine.cancel_preview()
                    self.reply(202, c.tests.start(body.get("source")))
                return
            if self.path == "/api/preview":
                if set(body) != {"action", "id"} or body["action"] not in ("start", "stop"):
                    raise EventError("演示请求格式错误")
                try:
                    uuid.UUID(body["id"])
                except (ValueError, TypeError, AttributeError):
                    raise EventError("演示标识必须为 UUID")
                with c.operation_lock:
                    if body["action"] == "start" and (
                        c.stopping.is_set() or c.devices.busy() or c.tests.busy()
                    ):
                        raise EventError("请等待设备操作或诊断结束后再开始演示", 409)
                    with c.lock:
                        if body["action"] == "start":
                            c.engine.start_preview(body["id"])
                        else:
                            c.engine.cancel_preview(body["id"])
                        self.reply(200, c.engine.snapshot())
                return
            if self.path == "/api/settings":
                self.reply(200, c.update_settings(body))
                return
            with c.lock:
                if self.path == "/api/verification":
                    if set(body) != {"source"}:
                        raise EventError("请选择一个验证来源")
                    begin_verification(c.engine, body["source"])
                elif self.path == "/api/test":
                    response, code = manual_test(c.engine, body)
                    self.reply(code, response)
                    return
                elif self.path == "/api/demo":
                    self.reply(202, c.demos.start(body))
                    return
                else:
                    raise EventError("接口不存在", 404)
                self.reply(200, c.engine.snapshot())
        except EventError as error:
            self.reply(error.status, {"error": str(error), "error_code": error.error_code})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass


def main():
    hub = Hub()
    hub.start()
    stopped = threading.Event()

    def stop(*_):
        if not hub.devices.busy():
            stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("SeeEnd local API: http://127.0.0.1:8765 (desktop App)", flush=True)
    try:
        stopped.wait()
    finally:
        hub.stop()
