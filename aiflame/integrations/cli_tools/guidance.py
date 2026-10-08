"""Read-only setup plans using the same paths and Hook entries as installers."""

import json
import os
from pathlib import Path

from aiflame.core.paths import runtime_dir
from aiflame.integrations.codex.install import hook_handler as codex_handler
from aiflame.integrations.codex.registration import EVENTS, quote_command
from .install import deepseek_command, hook_handler, profile_directory, settings
from .providers import PROVIDERS, config_directory

DOCS = {
    "codex": "https://learn.chatgpt.com/docs/hooks",
    "claude-code": "https://code.claude.com/docs/en/hooks",
    "qoder": "https://docs.qoder.com/cli/hooks",
    "gemini-cli": "https://geminicli.com/docs/hooks/",
    "opencode": "https://opencode.ai/docs/plugins/",
    "deepseek-harness": "https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.md",
}
PRESERVATION = "只增量添加或修复 SeeEnd 的 Hooks／插件，保留其他配置。修改已有文件前保存原字节备份（原文件名.seeend-backup-编号）；同名第三方文件或格式不识别时停止，不用模板覆盖原文件。"


def saved_integrations():
    try:
        return settings()[1]
    except (OSError, ValueError, TypeError):
        return {}


def directory_for(source, override=None):
    saved = saved_integrations().get(source, {})
    if source == "codex":
        value = override or saved.get("config_dir") or os.environ.get("CODEX_HOME") or Path.home() / ".codex"
        return Path(value).expanduser().resolve()
    return config_directory(source, override or saved.get("config_dir")).resolve()


def integration_guide(source, config_dir=None, profile=None):
    if source not in DOCS:
        raise ValueError("请选择支持的编程工具")
    name = "Codex" if source == "codex" else PROVIDERS[source].name
    result = {"source_id": source, "name": name, "preservation": PRESERVATION,
              "docs_url": DOCS[source], "automatic": "点击「连接」即可增量配置。",
              "preparation": [f"先安装并登录 {name}。", "核对下方实际配置位置；使用自定义目录时填入该目录。"],
              "next_steps": [f"重新打开 {name} 会话，让新配置加载。", "在工具中发送新任务，等回复结束，再点「检查」。模拟测试不算真实接入验证。"]}
    if source == "deepseek-harness":
        selected = profile or "<请选择已有 Profile>"
        directory = profile_directory() / selected
        bundle = runtime_dir() / "cli/deepseek-bundle"
        result.update(
            files=[str(directory / "package.json"), str(directory / "pnpm-lock.yaml")],
            changes=["仅向所选 Profile 增加 aiflame-deepseek-harness 依赖与 dsh.profile.bundles 条目。", "由 dsh / pnpm 插件管理器维护依赖和锁文件；SeeEnd 不替换整个 Profile。"],
            automatic="先「检查」并选择已有 Profile，再自动安装。需要 SeeEnd 能找到 dsh 和 pnpm；不会自动创建 Profile。",
            snippet=quote_command(deepseek_command(selected, bundle)),
            copy_label="复制插件安装命令",
            manual="先用自动连接准备 SeeEnd 插件包。若管理器安装失败，在能运行 dsh 与 pnpm 的终端执行下面同一条命令；不要手动替换 package.json 或锁文件。其他 Profile 需分别接入。失败时先查看管理器提示，备份不能代替依赖事务回滚。",
        )
        result["preparation"] = ["先安装并登录 DeepSeek Harness，确保 dsh 与 pnpm 可用。", "点击「检查」，选择实际使用的已有 Profile；使用 DSH_HOME 时让 SeeEnd 继承相同环境。"]
        return result
    directory = directory_for(source, config_dir)
    if source == "opencode":
        from .opencode import wrapper
        from aiflame.core.process import command
        module = Path(__file__).with_name("opencode_bundle") / "index.mjs"
        manual_files = {"plugins/aiflame.js": {"content": wrapper()},
                        "aiflame/index.mjs": {"copy_from": str(module)},
                        "aiflame/bridge.json": {"content": {"helperArgv": command("--cli-hook", source)}}}
        result.update(
            files=[str(directory / path) for path in ("plugins/aiflame.js", "aiflame/index.mjs", "aiflame/bridge.json", "aiflame/.seeend-owned.json")],
            changes=["新增专用插件入口、生命周期模块、桥接配置和归属记录；只修复本应用拥有的文件。", "不修改 opencode.json / opencode.jsonc，不改模型、权限或其他插件。"],
            snippet=json.dumps(manual_files, ensure_ascii=False, indent=2), copy_label="复制手动文件清单",
            manual="下方是文件清单，不是 opencode.json 配置。目标目录下仅在对应文件不存在时创建：将 content 保存为该文件，将 copy_from 指定的自带模块复制至目标。不要只创建入口；三个组件齐全后可自动连接生成归属记录。遇到同名文件先核对归属，不覆盖。",
        )
        result["preparation"].append("留空时按 OPENCODE_CONFIG_DIR，或 XDG_CONFIG_HOME/opencode（默认 ~/.config/opencode）定位。")
        return result
    events = EVENTS if source == "codex" else PROVIDERS[source].events
    handler = codex_handler() if source == "codex" else hook_handler(source)
    result.update(
        files=[str(directory / ("hooks.json" if source == "codex" else PROVIDERS[source].filename))],
        changes=["向 hooks 中各生命周期事件数组追加独立的 SeeEnd Hook 组。", "保留已有事件组、matcher、其他 Hooks 和所有无关设置；重复连接不追加重复条目。"],
        snippet=json.dumps({"hooks": {event: [{"hooks": [handler]}] for event in events}}, ensure_ascii=False, indent=2),
        copy_label="复制增量 Hooks 片段",
        manual="先备份配置文件。请增量合并，不替换整个配置文件。下方只是待合并的片段：已有 hooks 时，把每个事件下的 SeeEnd 组追加到原数组；已有 SeeEnd 条目只更新本应用条目，不重复添加。保留所有其他字段和原事件组。",
    )
    if source == "codex":
        result["preparation"].append("留空时使用 CODEX_HOME，默认 ~/.codex；只修改 hooks.json，不修改 config.toml。")
        result["next_steps"].insert(0, "在 Codex 中审阅并信任新增 Hooks；SeeEnd 无法代替你完成信任授权。")
    elif source == "claude-code":
        result["preparation"].append("留空时使用 CLAUDE_CONFIG_DIR，默认 ~/.claude；SeeEnd 不修改项目级配置。")
        result["next_steps"].append("可在 Claude Code 输入 /hooks 核对。若 disableAllHooks 为 true，按你的意图启用，SeeEnd 不擅自改动此开关。")
    elif source == "gemini-cli":
        result["next_steps"].append("可用 /hooks list 核对。若 hooksConfig.enabled 为 false 或 SeeEnd 在禁用列表中，按你的意图调整；SeeEnd 不开启其他 Hooks。")
    return result


def guide_text(guide):
    sections = ["自动备份，增量添加，不替换整个配置文件。", "配置文件：\n" + "\n".join(guide["files"]),
                "修改范围：\n" + "\n".join(guide["changes"])]
    if guide["source_id"] == "codex":
        sections.append("Codex 提示信任 Hooks 时，请先确认。")
    return "\n\n".join(sections)
