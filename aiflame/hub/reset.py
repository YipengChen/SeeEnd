"""Reset owned application data while retaining the live server and instance lock."""
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from aiflame import __version__
from aiflame.core.engine import Engine
from aiflame.core.events import EventError
from aiflame.core.paths import new_config, write_private
from aiflame.devices.fleet import FleetDeviceService
from aiflame.devices.models import DEFAULT_MODEL, MIJIA_PLUG_MODELS
from aiflame.integrations.registry import SOURCES
from aiflame.integrations.testing import TriggerTests
from aiflame.transports.fleet import FleetTransportManager
from aiflame.integrations.wechat.service import WechatService
from aiflame.outputs.wechat import WechatOutput


DATABASES = ("events.sqlite", "codex-sequences.sqlite", "cli-lifecycle.sqlite", "cli-origin-cache.sqlite")
OWNED = ("credentials.json", "device-tokens", "cube-sessions", "wechat-credentials", "cli-integrations.json", "firmware-cache", "BrowserBridge",
         "cli/deepseek-bundle", "live-test.txt", "desktop-adapters-live.json", "desktop-flame.json",
         *(name + suffix for name in DATABASES for suffix in ("", "-wal", "-shm", "-journal")))


def reset_app(hub):
    old_config = hub.config
    old_engine = hub.engine
    old_wechat_credentials = hub.wechat.credentials
    # Cache only the reference, so reset can remove the old Keychain account
    # after moving the profile marker (without recovering any token).
    try:
        old_wechat_credentials._account_id()
    except (ValueError, OSError, TypeError):
        pass
    try:
        staging = Path(tempfile.mkdtemp(prefix=".reset-", dir=hub.runtime))
    except OSError as error:
        raise EventError("无法清理 APP 数据，请检查本机文件权限", 500) from error
    hub.demos.cancel()
    hub.devices.close()
    # Workers can acquire the engine lock while shutting down; join outside it.
    hub.wechat.stop()
    hub.transport.stop()
    moved = []
    with hub.lock:
        hub.engine.db.close()
        try:
            for name in OWNED:
                path = hub.runtime / name
                # A nested cache below a redirected directory is not ours to clear.
                if any((hub.runtime / parent).is_symlink() for parent in Path(name).parents if parent != Path(".")):
                    continue
                if path.exists() or path.is_symlink():
                    destination = staging / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(path, destination)
                    moved.append(name)
            config = new_config()
            write_private(hub.runtime / "credentials.json", config)
            engine = Engine(hub.runtime / "events.sqlite", sources=SOURCES)
        except Exception as error:
            # Remove only fresh files, then put the original data back.
            for name in ("credentials.json", *("events.sqlite" + suffix for suffix in ("", "-wal", "-shm", "-journal"))):
                if name not in moved or (staging / name).exists():
                    (hub.runtime / name).unlink(missing_ok=True)
            for name in reversed(moved):
                os.replace(staging / name, hub.runtime / name)
            connection = sqlite3.connect(str(hub.runtime / "events.sqlite"), check_same_thread=False)
            connection.row_factory = sqlite3.Row
            old_engine.db = old_engine.router.db = old_engine.flame.db = old_engine.desktop.db = old_engine.sound.db = old_engine.origins.db = connection
            hub.engine = old_engine
            hub.config = old_config
            _services(hub)
            shutil.rmtree(staging)
            raise EventError("重置失败，原有配置已恢复，请检查本机文件权限", 500) from error
        # Remove only credentials owned by records from the successfully reset profile.
        from aiflame.devices.token_store import TokenStore
        references = list(old_config.get("pending_device_token_cleanup", []))
        for device in old_config.get("devices", {}).values():
            if device.get("hardware_model") in MIJIA_PLUG_MODELS:
                references.append({"service": device.get("miio_token_ref"), "device_id": device["device_id"]})
        pending, seen = [], set()
        for reference in references:
            key = (reference.get("service"), reference["device_id"])
            if key in seen:
                continue
            seen.add(key)
            try:
                store = TokenStore(hub.runtime, service=key[0])
                # The original profile has already moved into reset staging.
                # Preserve its service identity while reading local cleanup markers.
                if (staging / "device-tokens").exists():
                    store.runtime = staging
                store.delete(key[1])
            except (RuntimeError, OSError):
                pending.append({"service": key[0], "device_id": key[1]})
        if pending:
            # Only identifiers are retained, never the token or cloud login.
            config["pending_device_token_cleanup"] = pending
        hub.config, hub.engine = config, engine
        from aiflame.hub.task_returns import TaskReturns
        hub.task_returns.close()
        hub.task_returns = TaskReturns()
        _services(hub)
        hub.limits.clear()
        log = hub.runtime / "desktop.log"
        if log.is_file() and not log.is_symlink() and log.stat().st_nlink == 1:
            try:
                with log.open("r+", encoding="utf-8") as stream:
                    stream.truncate(0)
            except OSError:
                pass  # An open log can be locked by the platform; retain it safely.
        result = {**hub.engine.snapshot(), "app_version": __version__, "app_name": "SeeEnd",
                  "maintenance": False, "hardware_paired": False, "devices": [], "hardware_model": DEFAULT_MODEL,
                  "wechat": hub.wechat.snapshot()}
        if pending:
            result["token_cleanup_pending"] = True
            result["message"] = "APP 已重置；部分插座 Token 未清理，已保留清理记录。允许系统凭据访问后，再次重置可重试。"
    # Platform credential operations can wait for user authorization. Keep them
    # outside the engine lock so status polling remains responsive.
    try:
        old_wechat_credentials.clear()
    except (RuntimeError, OSError, ValueError):
        # Its old reference was moved away; it cannot rebind the fresh profile.
        pass
    shutil.rmtree(staging)
    return result


def _services(hub):
    hub.transport = FleetTransportManager(hub.engine.flame, hub.lock, hub.config)
    hub.engine.flame.transport = hub.transport
    hub.devices = FleetDeviceService(hub.config, hub.runtime, hub.transport, hub.lock)
    hub.tests = TriggerTests(hub)
    hub.wechat = WechatService(hub)
    hub.engine.router.outputs.pop("wechat", None)
    hub.engine.router.register(WechatOutput(hub.wechat))
    hub.wechat.start()
    if hub.hardware_enabled:
        hub.transport.start()
