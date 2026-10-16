"""Package the browser extension's public assets."""

import io
import json
import os
from pathlib import Path
import tempfile
import threading
import zipfile
from aiflame.core.paths import PACKAGE, user_data_dir
from aiflame.core.events import EventError


def extension_archive():
    buffer = io.BytesIO()
    directory = PACKAGE / "integrations/chatgpt_web/extension"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for asset in sorted(directory.iterdir()):
            if asset.is_file() and asset.suffix in (".json", ".js", ".html", ".md", ".png"):
                archive.write(asset, "SeeEnd-Web-extension/" + asset.name)
    return buffer.getvalue()


_PREPARE_LOCK = threading.Lock()
_MARKER = ".seeend-extension.json"


def extension_folder_path(runtime):
    """Keep visible installation files separate from legacy private app data."""
    runtime = Path(runtime).absolute()
    legacy = user_data_dir().absolute()
    if runtime == legacy:
        name = "seeend" if legacy.name == "ai-flame" else "SeeEnd"
        return legacy.parent / name / "BrowserExtension"
    # Custom/test runtimes remain isolated in their designated directory.
    return runtime / "SeeEnd" / "BrowserExtension"


def extension_folder_info(runtime):
    folder = extension_folder_path(runtime)
    marker = folder / _MARKER
    prepared = False
    try:
        if folder.is_dir() and not folder.is_symlink() and marker.is_file() and not marker.is_symlink():
            owned = json.loads(marker.read_text(encoding="utf-8")) == {"owner": "SeeEnd"}
            source = PACKAGE / "integrations/chatgpt_web/extension"
            prepared = owned and all((folder / asset.name).is_file() and not (folder / asset.name).is_symlink()
                                     and (folder / asset.name).read_bytes() == asset.read_bytes()
                                     for asset in source.iterdir()
                                     if asset.is_file() and asset.suffix in (".json", ".js", ".html", ".md", ".png"))
    except (OSError, ValueError):
        pass
    return {"path": str(folder.absolute()), "prepared": prepared,
            "message": "扩展文件夹已准备，无需下载或解压。" if prepared else "请先准备扩展文件夹。"}


def prepare_extension_folder(runtime):
    """Copy public extension assets to a stable, app-owned folder for Chrome."""
    folder = extension_folder_path(runtime)
    try:
        with _PREPARE_LOCK:
            marker = folder / _MARKER
            if folder.parent.is_symlink() or folder.is_symlink() or marker.is_symlink():
                raise EventError("扩展文件夹路径不可用，请移除该路径的符号链接后重试")
            if folder.exists() and any(folder.iterdir()):
                if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != {"owner": "SeeEnd"}:
                    raise EventError("扩展文件夹包含其他文件，未覆盖；请先移走该文件夹后重试")
            folder.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"owner": "SeeEnd"}), encoding="utf-8")
            source = PACKAGE / "integrations/chatgpt_web/extension"
            for asset in sorted(source.iterdir()):
                if asset.is_file() and asset.suffix in (".json", ".js", ".html", ".md", ".png"):
                    # Replace files atomically; never follow an existing file symlink.
                    fd, temporary = tempfile.mkstemp(prefix=".prepare-", dir=folder)
                    try:
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(asset.read_bytes())
                        os.replace(temporary, folder / asset.name)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
    except (OSError, ValueError) as error:
        raise EventError("无法准备扩展文件夹，请检查本机文件权限后重试") from error
    return extension_folder_info(runtime)
