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
from aiflame.devices.models import DEFAULT_MODEL
from aiflame.devices.wifi_scan import FleetWiFiScan
from aiflame.integrations.desktop_clients.service import DesktopClients
from aiflame.integrations.registry import SOURCES
from aiflame.integrations.testing import TriggerTests
from aiflame.transports.fleet import FleetTransportManager


DATABASES = ("events.sqlite", "codex-sequences.sqlite", "cli-lifecycle.sqlite")
OWNED = ("credentials.json", "cli-integrations.json", "firmware-cache", "BrowserBridge",
         "cli/deepseek-bundle", "live-test.txt", "desktop-adapters-live.json",
         *(name + suffix for name in DATABASES for suffix in ("", "-wal", "-shm", "-journal")))


def reset_app(hub):
    old_config = hub.config
    old_engine = hub.engine
    try:
        staging = Path(tempfile.mkdtemp(prefix=".reset-", dir=hub.runtime))
    except OSError as error:
        raise EventError("无法清理 APP 数据，请检查本机文件权限", 500) from error
    hub.demos.cancel()
    # Workers can acquire the engine lock while shutting down; join outside it.
    hub.desktop_clients.stop()
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
            old_engine.db = old_engine.router.db = old_engine.flame.db = old_engine.desktop.db = connection
            hub.engine = old_engine
            hub.config = old_config
            _services(hub)
            shutil.rmtree(staging)
            raise EventError("重置失败，原有配置已恢复，请检查本机文件权限", 500) from error
        hub.config, hub.engine = config, engine
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
                  "maintenance": False, "hardware_paired": False, "devices": [], "hardware_model": DEFAULT_MODEL}
    shutil.rmtree(staging)
    return result


def _services(hub):
    hub.transport = FleetTransportManager(hub.engine.flame, hub.lock, hub.config)
    hub.engine.flame.transport = hub.transport
    hub.devices = FleetDeviceService(hub.config, hub.runtime, hub.transport, hub.lock)
    hub.wifi_scan = FleetWiFiScan(hub.transport)
    hub.tests = TriggerTests(hub)
    hub.desktop_clients = DesktopClients(hub)
    if hub.hardware_enabled:
        hub.transport.start()
        hub.desktop_clients.start()
