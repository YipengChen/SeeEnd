"""Install/uninstall only our lifecycle hooks; leave other integrations intact."""

import argparse
import json
import os
from pathlib import Path
from aiflame.core.paths import write_private
from aiflame.core.process import command

from aiflame.integrations.codex.registration import (
    EVENTS,
    STATUS_MESSAGE,
    owns_hook,
    quote_command,
)


def backup_file(path):
    """Keep exact original bytes in a new file; never replace an older backup."""
    path = Path(path)
    if not path.exists():
        return None
    content = path.read_bytes()
    index = 1
    while True:
        suffix = f".seeend-backup-{index}"
        backup = path.with_name(path.name + suffix)
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
            return backup
        except FileExistsError:
            index += 1


def write_incremental(path, data):
    """Write a semantic merge only when needed, preserving a recovery copy."""
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) == data:
            return
        backup_file(path)
    write_private(path, data)


def hook_handler():
    return {
        "type": "command",
        "command": quote_command(command("--codex-hook")),
        "timeout": 5,
        "statusMessage": STATUS_MESSAGE,
    }


def install(home=None, uninstall=False):
    home = (
        Path(home).expanduser().resolve()
        if home
        else Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    )
    path = home / "hooks.json"
    if path.is_symlink():
        raise ValueError("hooks.json 是符号链接，请选择实际配置目录，未修改")
    if uninstall and not path.exists():
        return path
    data = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"hooks": {}}
    )
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("无法识别现有 hooks 格式，未修改")
    if uninstall and "hooks" not in data:
        return path
    hooks = data.setdefault("hooks", {})
    wanted = quote_command(command("--codex-hook"))
    for event in EVENTS:
        groups = hooks.get(event, [])
        if not isinstance(groups, list) or any(
            not isinstance(group, dict)
            or not isinstance(group.get("hooks"), list)
            or any(not isinstance(handler, dict) for handler in group["hooks"])
            for group in groups
        ):
            raise ValueError("无法识别现有 hooks 格式，未修改")
        retained = []
        for group in groups:
            handlers = [h for h in group.get("hooks", []) if not owns_hook(h, wanted)]
            if handlers or not group["hooks"]:
                retained.append({**group, "hooks": handlers})
        if not uninstall:
            retained.append(
                {
                    "hooks": [
                        hook_handler()
                    ]
                }
            )
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event, None)
    write_incremental(path, data)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--home", help="Codex 配置目录；只增量合并其中的 hooks.json")
    args = parser.parse_args()
    print(install(home=args.home, uninstall=args.uninstall))
    print(
        "仅更新 SeeEnd Hooks；真实接收情况需在 Codex 中验证，首次使用可能需要信任 Hook。"
    )


if __name__ == "__main__":
    main()
