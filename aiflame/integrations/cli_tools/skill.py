"""Shared Skill entry point for coding-tool setup and maintenance."""

from .providers import CLI_SOURCES

SKILL_URL = "https://github.com/YipengChen/SeeEnd/tree/main/skill/seeend"
NAMES = {"codex": "Codex", **CLI_SOURCES}


def installation_prompt(source=None, parameters=None, translate=lambda value: value):
    """Copyable prose without credentials or assumed host installation paths."""
    parameters = parameters or {}
    target = NAMES[source] if source else translate("当前编程工具")
    lines = [
        translate("请安装此 Skill，并用它为{tool}接入 SeeEnd：").format(tool=target),
        SKILL_URL,
    ]
    if parameters.get("config_dir"):
        lines.append(translate("配置目录：") + parameters["config_dir"])
    if parameters.get("profile"):
        lines.append("Profile: " + parameters["profile"])
    return "\n".join(lines)
