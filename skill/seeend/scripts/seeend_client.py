#!/usr/bin/env python3
"""Small, local-only SeeEnd management client; never sends task events.

Uses Python 3's standard library. All output is a filtered JSON summary.
The management root key stays local; only instance-bound HMAC proofs go on wire.
"""

import argparse
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from seeend_auth import SignedOpener, NoRedirect as NoRedirect, AuthError


LEGACY_BUILTIN = frozenset({
    "codex", "claude-code", "qoder", "deepseek-harness", "gemini-cli", "opencode",
})
LEGACY_KNOWN = LEGACY_BUILTIN | {"local-script"}
MUTATING = frozenset({"install", "uninstall", "verify"})
COMMANDS = frozenset({"status", "contract", "plan", "check", "evidence", *MUTATING})
LEGACY_VERSIONS = frozenset({"0.8.4", "0.8.5"})
EVENT_FIELDS = frozenset({"schema_version", "event_id", "source_id", "task_id", "seq", "state", "occurred_at", "ttl_ms"})
# Keep the retired error state readable in evidence from older App versions.
EVENT_STATES = frozenset({"idle", "working", "needs_input", "done", "error", "cancelled", "timeout", "unknown"})
CURRENT_EVENT_STATES = EVENT_STATES - {"error"}
EVENT_OPTIONAL_FIELDS = frozenset({"reason", "device_id"})
RETURN_OPTIONAL_FIELDS = EVENT_OPTIONAL_FIELDS | {"return_target", "return_context"}
ERROR_MESSAGES = {
    "integration_conflict": "接入文件归属或配置条件存在冲突；先执行 plan/check 核对，未确认成功。",
    "config_invalid": "接入配置格式无法识别；先修正所选文件格式，未确认成功。",
    "missing_dependency": "接入配置或必要工具不存在；核对所选目录及 App 环境中的 dsh/pnpm。",
    "io_error": "无法读写所选接入目录；核对目录与权限，操作可能部分完成。",
    "plugin_workspace": "pnpm 拒绝在 Profile 工作区根目录操作；核对所选 Profile 与 -w。",
    "plugin_network": "插件管理器无法获取依赖；核对网络与 pnpm 安装源，先检查后重试。",
    "plugin_timeout": "插件管理器超时；操作可能部分完成，先执行 check 核对后再重试。",
}
# Match the App's bounded management client; device/history snapshots can exceed 2 MiB.
MAX_RESPONSE = 32 * 1024 * 1024
MAX_CREDENTIALS = 1024 * 1024
SECRET_KEYS = frozenset({
    "token", "tokens", "cookie", "cookies", "authorization", "credentials",
    "credential", "apikey", "dashboard", "setcookie", "secret", "secrets",
    "accesstoken", "refreshtoken", "password", "passwd", "privatekey",
})


class ClientError(Exception):
    def __init__(self, category, message, http_status=None, error_code=None):
        super().__init__(message)
        self.category, self.message, self.http_status = category, message, http_status
        self.error_code = error_code

    def summary(self):
        result = {"ok": False, "error": {"category": self.category, "message": self.message}}
        if self.http_status is not None:
            result["error"]["http_status"] = self.http_status
        if self.error_code:
            result["error"]["code"] = self.error_code
        return result


def credentials_path(platform=None, home=None, environ=None):
    """Match the App's per-user profile location without creating anything."""
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    override = environ.get("AIFLAME_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve() / "credentials.json"
    if platform == "win32":
        root = Path(environ.get("LOCALAPPDATA", home / "AppData/Local")) / "AI Flame"
    elif platform == "darwin":
        root = home / "Library/Application Support/AI Flame"
    else:
        configured = environ.get("XDG_DATA_HOME", "")
        root = (Path(configured) if PurePosixPath(configured).is_absolute()
                else home / ".local/share") / "ai-flame"
    return root / "credentials.json"


def read_dashboard_token():
    """Read only for session bootstrap; never create, repair or print credentials."""
    try:
        with credentials_path().open("rb") as stream:
            raw = stream.read(MAX_CREDENTIALS + 1)
        if len(raw) > MAX_CREDENTIALS:
            raise ValueError
        config = json.loads(raw)
        token = config.get("dashboard") if isinstance(config, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
            raise ValueError
        return token
    except (OSError, ValueError, UnicodeError, RuntimeError):
        raise ClientError("credentials_unavailable", "无法读取有效的本机 SeeEnd 桌面凭据；请确认 App 已启动且脚本使用同一用户和数据目录。未创建或修改配置。") from None


def validate_base_url(value):
    """Reject remote URLs, ambiguous authority syntax and extra URL components."""
    try:
        parts = urllib.parse.urlsplit(value)
        port = parts.port
        if (
            not isinstance(value, str) or any(char.isspace() for char in value)
            or
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "localhost"}
            or parts.username is not None or parts.password is not None
            or parts.path not in {"", "/"}
            or "?" in value or "#" in value
            or port is not None and not 1 <= port <= 65535
            or not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::[0-9]+)?", parts.netloc, re.I)
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ClientError("invalid_argument", "base-url 只允许 http://127.0.0.1[:端口] 或 http://localhost[:端口]，不得含凭据或其他路径。") from None
    return value.rstrip("/")


def key_name(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


class Redactor:
    def __init__(self):
        self.secrets = set()

    def learn(self, value):
        """Recognize secret fields without exposing or persisting their contents."""
        if isinstance(value, dict):
            for key, item in value.items():
                if key_name(key) in SECRET_KEYS:
                    self._learn_values(item)
                else:
                    self.learn(item)
        elif isinstance(value, list):
            for item in value:
                self.learn(item)

    def _learn_values(self, value):
        if isinstance(value, str) and value:
            self.secrets.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                self._learn_values(item)
        elif isinstance(value, list):
            for item in value:
                self._learn_values(item)

    def clean(self, value):
        if isinstance(value, dict):
            return {
                key: self.clean(item) for key, item in value.items()
                if key_name(key) not in SECRET_KEYS
            }
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if not isinstance(value, str):
            return value
        text = value
        # A future guide may put credentials inside its JSON snippet string.
        # Inspect JSON structure as well as outer response keys before displaying it.
        if text.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    self.learn(parsed)
                    text = json.dumps(self.clean(parsed), ensure_ascii=False, indent=2)
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[已隐藏]")
        text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [已隐藏]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[已隐藏]", text)
        # Covers credential assignments in JSON, shell snippets and ordinary diagnostics.
        text = re.sub(
            r'''(?ix)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|tokens?|password|secrets?|authorization|cookies?|dashboard)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''',
            r'\1"[已隐藏]"', text,
        )
        return text


def selected(value, fields):
    """Whitelist shallow scalar fields; never copy unexpected objects."""
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in fields if key in value and isinstance(value[key], (str, int, float, bool, type(None)))}


def strings(value):
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def verification_summary(value):
    result = selected(value, (
        "source", "status", "since", "started_at", "finished_at", "last_seen",
        "last_state", "delivery", "complete_tasks",
    ))
    # The Hub aggregates evidence by source, across config directories and Profiles.
    # Always attach this local interpretation, even when the backend omits scope notes.
    result.update(
        evidence_scope="source-wide",
        limitation="验证记录按来源聚合，不证明当前配置目录或 Profile 已验收；仍须结合实际会话、任务和时间确认归属，并在目标会话完成真实新任务验证。",
    )
    return result


def checked_verification(value, source):
    if isinstance(value, dict) and value.get("source", source) != source:
        raise ClientError("incompatible_response", "验证记录与所选来源不匹配；停止处理。")
    return verification_summary(value)


def status_summary(value, source=None):
    if not isinstance(value.get("sources"), list) or not isinstance(value.get("verification"), dict):
        raise ClientError("incompatible_response", "状态响应结构无法识别；停止操作并核对安装版本及接口文档。")
    sources = [selected(item, ("id", "name", "enabled", "status", "last_seen"))
               for item in value["sources"] if isinstance(item, dict)
               and (source is None or item.get("id") == source)]
    result = {
        "outputs": {
            "display": selected(value.get("display"), ("state", "simulated")),
            "settings": selected(value.get("settings"), ("mode", "paused", "brightness", "desktop_flame_enabled", "desktop_notifications", "sound_notifications", "sound_category", "wechat_notifications", "hardware_notifications")),
            "device": selected(value.get("device"), ("online", "connecting")),
            **selected(value, ("brightness_off", "hardware_paired", "maintenance")),
        },
    }
    if source is None:
        result["sources"] = sources
    else:
        if len(sources) != 1:
            raise ClientError("source_unavailable", "当前 SeeEnd 未返回所选来源；请核对版本与来源支持情况。")
        result["source"] = sources[0]
        result["verification"] = checked_verification(value["verification"].get(source), source)
    return result


def plan_summary(value, source):
    if value.get("source_id") != source or not isinstance(value.get("files"), list):
        raise ClientError("incompatible_response", "接入方案与所选来源不匹配或结构无法识别；未执行安装。")
    result = selected(value, ("source_id", "name", "config_dir", "preservation", "docs_url", "snippet", "manual"))
    for key in ("files", "changes", "preparation", "next_steps"):
        result[key] = strings(value.get(key))
    onboarding = value.get("onboarding", {})
    if isinstance(onboarding, dict):
        result["reload_steps"] = strings(onboarding.get("reload_steps"))
        result["limitations"] = strings(onboarding.get("limitations"))
        result.update(selected(onboarding, ("verification_prompt",)))
    return result


def check_summary(value, source):
    # Codex's current diagnostic response does not include source_id.
    if value.get("source_id", source) != source or not isinstance(value.get("checks"), list):
        raise ClientError("incompatible_response", "诊断与所选来源不匹配或结构无法识别；停止自动修复。")
    result = selected(value, (
        "source_id", "name", "config_dir", "config_file", "profile", "status",
        "configuration_ready", "summary", "next_action", "trust_note", "evidence_scope",
    ))
    result["source_id"] = source
    for key in ("issues", "hooks_configured", "limitations"):
        result[key] = strings(value.get(key))
    if source == "deepseek-harness":
        for key in ("profiles", "recorded_profiles", "installed_profiles"):
            result[key] = strings(value.get(key))
    result["checks"] = [selected(item, ("id", "status", "message", "action"))
                        for item in value["checks"] if isinstance(item, dict)]
    result["verification"] = checked_verification(value.get("verification"), source)
    signals = value.get("signals", {})
    if isinstance(signals, dict):
        result["signals"] = {key: selected(signals[key], ("name", "received", "applied", "last_seen", "last_result"))
                             for key in ("start", "activity", "complete", "needs_input", "interrupt", "error") if key in signals}
    return result


def contract_summary(value, version):
    """Validate the server's declarative contract before using its capabilities."""
    if (type(value.get("contract_version")) is not int or value["contract_version"] != 1
            or value.get("app_name") != "SeeEnd" or value.get("app_version") != version
            or value.get("schema_version") != "1.0"
            or set(strings(value.get("required_fields"))) != EVENT_FIELDS
            or set(strings(value.get("optional_fields"))) not in (EVENT_OPTIONAL_FIELDS, RETURN_OPTIONAL_FIELDS)
            or set(strings(value.get("states"))) not in (EVENT_STATES, CURRENT_EVENT_STATES)
            or not {"completed", "failed", "cancelled", "timeout"} <= set(strings(value.get("reasons")))
            or not isinstance(value.get("sources"), list)
            or not isinstance(value.get("capabilities"), dict)):
        raise ClientError("incompatible_contract", "SeeEnd 的管理契约或事件协议不兼容；停止操作并核对安装版本。")
    sources = []
    for item in value["sources"]:
        if (not isinstance(item, dict) or not valid_source(item.get("id"))
                or type(item.get("authenticated")) is not bool):
            raise ClientError("incompatible_contract", "SeeEnd 返回的来源契约无法识别；停止操作。")
        sources.append(selected(item, ("id", "name", "authenticated", "requires_start", "allow_late_cancel", "working_lease_seconds")))
    identities = {item["id"] for item in sources}
    capabilities = value["capabilities"]
    native = strings(capabilities.get("native_sources"))
    if (len(identities) != len(sources) or not native or len(native) != len(set(native))
            or not set(native) <= identities or type(capabilities.get("verification_evidence")) is not bool
            or capabilities.get("deepseek_config_dir") is not False):
        raise ClientError("incompatible_contract", "SeeEnd 返回的接入能力无法识别；停止操作。")
    return {**selected(value, ("contract_version", "app_name", "app_version", "schema_version")),
            "required_fields": sorted(EVENT_FIELDS), "optional_fields": strings(value["optional_fields"]),
            "states": strings(value["states"]), "reasons": strings(value["reasons"]),
            "sources": sources,
            "capabilities": {"native_sources": native, "verification_evidence": capabilities["verification_evidence"],
                             "deepseek_config_dir": False}}


def evidence_summary(value, source):
    """Keep task correlation metadata, without copying task content or raw payloads."""
    if value.get("source") != source or not isinstance(value.get("events"), list):
        raise ClientError("incompatible_response", "验证证据与所选来源不匹配或结构无法识别；停止处理。")
    result = {"source": source, "since": value.get("since"),
              "summary": checked_verification(value.get("summary"), source), "events": []}
    for key in ("total", "limit"):
        if key in value:
            if type(value[key]) is not int or value[key] < 0:
                raise ClientError("incompatible_response", "验证证据数量范围无法识别；停止处理。")
            result[key] = value[key]
    if "truncated" in value:
        if type(value["truncated"]) is not bool:
            raise ClientError("incompatible_response", "验证证据截断标记无法识别；停止处理。")
        result["truncated"] = value["truncated"]
    result["retention"] = selected(value.get("retention"), ("days", "max_events"))
    if result["since"] is not None and (type(result["since"]) not in (int, float) or not math.isfinite(result["since"])):
        raise ClientError("incompatible_response", "验证窗口时间无法识别；停止处理。")
    for item in value["events"]:
        if (not isinstance(item, dict) or not isinstance(item.get("event_id"), str)
                or not isinstance(item.get("task_id"), str) or not 1 <= len(item["task_id"]) <= 120
                or type(item.get("seq")) is not int or not 0 <= item["seq"] <= 2147483647
                or not isinstance(item.get("state"), str) or item["state"] not in EVENT_STATES
                or item.get("reason") not in (None, "completed", "failed", "cancelled", "timeout")
                or type(item.get("created")) not in (int, float) or not math.isfinite(item["created"])
                or type(item.get("simulated")) is not bool
                or not isinstance(item.get("status"), str)):
            raise ClientError("incompatible_response", "验证事件元数据无法识别；停止处理。")
        if item["simulated"] or item["status"] == "unmatched" or (result["since"] is not None and item["created"] < result["since"]):
            continue
        result["events"].append(selected(item, ("event_id", "task_id", "seq", "state", "reason", "created", "simulated", "status")))
    result["limitation"] = "事件属于所选来源与验证窗口；仍需将 task_id、时间与目标宿主会话关联，不能凭来源级 verified 认定当前目录或 Profile 已验收。"
    if result.get("truncated"):
        result["limitation"] += "本次返回已截断；更早的开始或终态可能缺失，不应据此认定完整验收。"
    return result


def valid_source(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value) is not None


class Client:
    def __init__(self, base_url="http://127.0.0.1:8765", credential_reader=None):
        self.base_url = validate_base_url(base_url)
        self.redactor = Redactor()
        self.version = None
        self.contract = None
        self.credential_reader = credential_reader or read_dashboard_token
        def private_key():
            token = self.credential_reader()
            self.redactor._learn_values(token)
            return token
        self.opener = SignedOpener(self.base_url, private_key, max_response=MAX_RESPONSE)

    def request(self, path, body=None, timeout=5):
        if not path.startswith("/") or path.startswith("//"):
            raise ClientError("invalid_argument", "只允许当前本机服务的接口路径。")
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers=headers,
            method="GET" if body is None else "POST",
        )
        try:
            with self.opener.open(request, timeout=timeout) as response:
                code = response.getcode()
                if not isinstance(code, int) or not 200 <= code < 300:
                    raise ClientError("http_error", "SeeEnd 返回非成功 HTTP 状态；操作未确认成功。", code)
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ClientError("incompatible_response", "响应过大；停止处理，未输出原始内容。")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            self.redactor.learn(value)
            return value
        except AuthError:
            raise ClientError("authentication_failed", "本机管理签名或服务身份验证失败；操作结果未确认，请重新连接兼容的 SeeEnd。") from None
        except urllib.error.HTTPError as error:
            category = "redirect_blocked" if 300 <= error.code < 400 else "http_error"
            error_code = None
            # Only a bounded, recognized machine code may affect displayed diagnostics.
            # The server's free-form error, reason, headers and body never reach output.
            if error.code == 409:
                try:
                    raw_error = error.read(16385)
                    parsed_error = json.loads(raw_error) if len(raw_error) <= 16384 else {}
                    candidate = parsed_error.get("error_code") if isinstance(parsed_error, dict) else None
                    if isinstance(candidate, str) and candidate in ERROR_MESSAGES:
                        error_code = candidate
                except (ValueError, TypeError, OSError, UnicodeError):
                    pass
            message = {
                401: "管理会话无效；请核对本机 SeeEnd 实例，不要输出或重置凭据。",
                403: "当前接口拒绝请求；请核对权限和本机服务身份。",
                404: "安装版本未提供该接口；请核对版本和兼容文档。",
                409: "操作存在冲突或配置条件未满足；未确认成功。先执行 plan 与 check 查看具体诊断。",
                429: "请求过于频繁；请稍后只读检查，不要反复安装。",
            }.get(error.code, "本机接口发生重定向，已阻止；未跟随目标。" if category == "redirect_blocked" else "SeeEnd 返回 HTTP 错误；未确认操作成功。")
            raise ClientError(category, ERROR_MESSAGES.get(error_code, message), error.code, error_code) from None
        except (TimeoutError, socket.timeout):
            raise self._timeout(body is not None) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, (TimeoutError, socket.timeout)):
                raise self._timeout(body is not None) from None
            raise ClientError("network_error", "无法连接本机 SeeEnd；请确认应用、端口与执行主机。未确认操作成功。") from None
        except (ConnectionError, OSError):
            raise ClientError("network_error", "本机连接中断；未确认操作成功。") from None
        except (ValueError, UnicodeError):
            raise ClientError("incompatible_response", "接口未返回可识别的 JSON 对象；请核对版本，未输出原始响应。") from None

    @staticmethod
    def _timeout(mutating):
        return ClientError("timeout", "请求超时；操作可能已部分完成。先执行 check 或 status 核对，再决定是否重试，不要并行重复执行。" if mutating else "本机请求超时；未获得检查结果，请确认 SeeEnd 运行正常。")

    def connect(self):
        meta = self.request("/api/session")
        if meta.get("app_name") != "SeeEnd":
            raise ClientError("identity_mismatch", "本机服务未识别为 SeeEnd；停止请求。")
        version = meta.get("app_version")
        if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9.+_-]{1,80}", version):
            raise ClientError("incompatible_response", "SeeEnd 未提供可识别的版本号；停止操作。")
        self.version = version
        try:
            contract = self.request("/api/integration-contract")
        except ClientError as error:
            if error.http_status != 404 or version not in LEGACY_VERSIONS:
                raise
            self.contract = None
        else:
            self.contract = contract_summary(contract, version)

    def validate_target(self, command, source):
        if self.contract is None:
            if command in MUTATING or command == "evidence":
                raise ClientError("incompatible_contract", "该旧版 SeeEnd 缺少管理契约或任务证据接口；仅支持只读 status/plan/check。需要写入或验收时先明确选择兼容的 App 版本。")
            allowed = LEGACY_KNOWN if command == "status" else LEGACY_BUILTIN
        else:
            allowed = ({item["id"] for item in self.contract["sources"] if item.get("authenticated")}
                       if command in {"status", "verify", "evidence"}
                       else set(self.contract["capabilities"]["native_sources"]))
            if command == "evidence" and not self.contract["capabilities"]["verification_evidence"]:
                raise ClientError("incompatible_contract", "当前 SeeEnd 未提供验证证据接口；不能完成同任务验收。")
        if source is not None and source not in allowed:
            raise ClientError("unsupported_source", "当前 SeeEnd 的管理契约不支持所选来源或操作。")

    def run(self, command, source=None, config_dir=None, profile=None, apply=False):
        validate_operation(command, source, config_dir, profile, apply)
        self.connect()
        self.validate_target(command, source)
        result = {"ok": True, "command": command, "version": self.version}
        if self.contract is None:
            result["compatibility"] = "legacy-read-only"
            result["limitation"] = "0.8.4/0.8.5 历史基线只读回退；缺少管理契约与同任务证据接口，未证明当前配置已验收。"
        else:
            result["contract_version"] = self.contract["contract_version"]
        if source:
            result["target_source"] = source
        params = {"source": source}
        if config_dir:
            params["config_dir"] = config_dir
        if profile:
            params["profile"] = profile
        if command == "status":
            result.update(status_summary(self.request("/api/status"), source))
        elif command == "contract":
            result["contract"] = self.contract
        elif command in {"plan", "check"}:
            path = "/api/integration-" + ("guide" if command == "plan" else "check")
            value = self.request(path + "?" + urllib.parse.urlencode(params))
            result[command] = (plan_summary if command == "plan" else check_summary)(value, source)
        elif command == "verify":
            value = self.request("/api/verification", {"source": source})
            result.update(status_summary(value, source))
            result["note"] = "已开始新的验证窗口；等待真实新任务，尚未据此证明接入成功。"
        elif command == "evidence":
            result["evidence"] = evidence_summary(self.request("/api/verification-evidence?" + urllib.parse.urlencode({"source": source})), source)
        else:
            value = self.request("/api/integrations/" + command, params, timeout=60)
            if not isinstance(value.get("message"), str) or "error" in value:
                raise ClientError("incompatible_response", "写入请求返回结构无法识别；可能已部分完成。先 check 核对，再决定是否重试。")
            result["message"] = value["message"]
            result["note"] = "配置操作已返回成功；仍须完成加载、信任与真实新任务验证。" if command == "install" else "移除请求已返回成功；可用 check 核对所选配置。"
        return self.redactor.clean(result)


def validate_operation(command, source, config_dir=None, profile=None, apply=False):
    if command not in COMMANDS:
        raise ClientError("invalid_argument", "未知命令。")
    if command not in {"status", "contract"} and not source:
        raise ClientError("invalid_argument", "该命令必须指定 --source。")
    if source is not None and not valid_source(source):
        raise ClientError("invalid_argument", "--source 必须是合法的来源标识；支持情况由当前 App 的管理契约决定。")
    if command == "contract" and (source or config_dir or profile):
        raise ClientError("invalid_argument", "contract 读取当前 App 的完整接入契约，不接受来源或配置参数。")
    if command in MUTATING and not apply:
        raise ClientError("apply_required", "该命令会修改配置或验证窗口；已获用户授权的操作请显式添加 --apply。")
    if command == "status" and (config_dir or profile):
        raise ClientError("invalid_argument", "status 不检查配置目录；指定目录或 Profile 请使用 plan/check。")
    if command in {"verify", "evidence"} and (config_dir or profile):
        raise ClientError("invalid_argument", "verify/evidence 只操作所选来源的验证窗口；目录与 Profile 请在 plan/check 中核对。")
    if source == "deepseek-harness" and config_dir:
        raise ClientError("invalid_argument", "DeepSeek Harness 不支持 --config-dir；使用实际已有 --profile，根目录由 SeeEnd 进程的 DSH_HOME 决定。")
    if profile and source != "deepseek-harness":
        raise ClientError("invalid_argument", "--profile 只用于 deepseek-harness。")
    if command in {"install", "uninstall"} and source == "deepseek-harness" and not profile:
        raise ClientError("invalid_argument", "DeepSeek Harness 安装或移除须指定实际使用的已有 --profile；不得猜测或创建 Profile。")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="status", choices=["status", "contract", "plan", "check", "install", "uninstall", "verify", "evidence"])
    parser.add_argument("--source", help="当前 App 管理契约中的来源标识；contract 可查看支持范围")
    parser.add_argument("--config-dir", help="实际使用的用户配置目录")
    parser.add_argument("--profile", help="DeepSeek Harness 的已有 Profile")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765", help="本机 SeeEnd HTTP 地址")
    parser.add_argument("--apply", action="store_true", help="显式执行安装、移除或开始验证；只用于已授权的操作")
    args = parser.parse_args(argv)
    try:
        # Validate before constructing a client or making any network request.
        validate_operation(args.command, args.source, args.config_dir, args.profile, args.apply)
        result = Client(args.base_url).run(args.command, args.source, args.config_dir, args.profile, args.apply)
        code = 0
    except ClientError as error:
        result, code = error.summary(), 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
