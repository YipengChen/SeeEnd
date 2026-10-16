"""Platform paths. Source and installed applications share one user data directory."""

import json
import os
from pathlib import Path, PurePosixPath
import secrets
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[1]
PROJECT = PACKAGE.parent


def user_data_dir(platform=None, home=None, environ=None):
    platform = platform or sys.platform
    home = Path(home) if home is not None else Path.home()
    environ = os.environ if environ is None else environ
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA", home / "AppData/Local")) / "AI Flame"
    if platform == "darwin":
        return home / "Library/Application Support/AI Flame"
    configured_home = environ.get("XDG_DATA_HOME", "")
    data_home = (
        Path(configured_home)
        if PurePosixPath(configured_home).is_absolute()
        else home / ".local/share"
    )
    return data_home / "ai-flame"


def runtime_dir():
    override = os.environ.get("AIFLAME_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return user_data_dir()


def ensure_runtime():
    path = runtime_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt" and path.stat().st_mode & 0o777 != 0o700:
        path.chmod(0o700)
    return path


def write_private(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def new_config():
    import uuid

    from aiflame.integrations.registry import SOURCES
    return {"hub_id": str(uuid.uuid4()), "dashboard": secrets.token_urlsafe(32),
            "tokens": {source.id: secrets.token_urlsafe(32) for source in SOURCES if source.authenticated},
            "devices": {}}


def load_config():

    path = ensure_runtime() / "credentials.json"
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
    else:
        config = new_config()
    changed = not path.exists()
    from aiflame.integrations.registry import SOURCES

    authenticated_sources = {s.id for s in SOURCES if s.authenticated}
    if set(config["tokens"]) - authenticated_sources:
        config["tokens"] = {source: token for source, token in config["tokens"].items()
                            if source in authenticated_sources}
        changed = True
    for source in authenticated_sources:
        if source not in config["tokens"]:
            config["tokens"][source] = secrets.token_urlsafe(32)
            changed = True
    if changed:
        write_private(path, config)
    return config
