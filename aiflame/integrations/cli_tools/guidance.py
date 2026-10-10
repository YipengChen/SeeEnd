"""Read-only setup plans using the same paths and Hook entries as installers."""

import json
import os
from pathlib import Path

from aiflame.core.paths import runtime_dir
from aiflame.integrations.codex.install import hook_handler as codex_handler
from aiflame.integrations.codex.registration import EVENTS, quote_command
from .install import deepseek_command, hook_handler, profile_directory, settings
from .providers import PROVIDERS, config_directory
from .skill import SKILL_URL

DOCS = {
    "codex": "https://learn.chatgpt.com/docs/hooks",
    "claude-code": "https://code.claude.com/docs/en/hooks",
    "qoder": "https://docs.qoder.com/cli/hooks",
    "gemini-cli": "https://geminicli.com/docs/hooks/",
    "opencode": "https://opencode.ai/docs/plugins/",
    "deepseek-harness": "https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/develop/basic/publish.md",
}
PRESERVATION = "只增量添加或修复 SeeEnd 的 Hooks／插件，保留其他配置。修改已有文件前保存原字节备份（原文件名.seeend-backup-编号）；同名第三方文件或格式不识别时停止，不用模板覆盖原文件。"
VERIFICATION_PROMPT = "请只回复：SeeEnd 接入测试成功。不要读取或修改文件，也不要调用工具。"


def onboarding(source, name, profile=None):
    """Plain-language first-use instructions, separate from manual file recipes."""
    preparation = [
        f"先确认本机的 {name} 已安装，完成它要求的登录或模型配置，并能正常回复。SeeEnd 不会替你安装或登录该工具。",
        "保持 SeeEnd 运行；macOS 请先把 SeeEnd.app 放入「应用程序」，再进行接入设置。无需连接灯具，也无需向 SeeEnd 提供模型 API Key。",
        "通常保留推荐的配置位置即可。只有你曾为工具指定其他配置目录时才更改；这里填写配置文件所在文件夹，不是项目文件夹或某个 JSON 文件。",
    ]
    limitations = [
        "此引导设置本机当前用户的接入。网页、云端、SSH 远程主机、容器和 WSL 中的工具不会自动读取这份本机配置；须在实际运行环境另行接入。",
        "配置文件检查通过，只说明 SeeEnd 接入内容已写好；不能证明工具已加载或信任它。请在实际使用的会话完成一条新任务。",
        "验证消息会作为该工具的一次普通 AI 请求，按其账号计入额度或费用；也可用你接下来原本要做的任务验证。SeeEnd 不会替你发送。",
    ]
    reload_steps = {
        "codex": [
            "等当前任务结束，再退出并重新打开本机 Codex 会话。终端用户重新运行 codex；使用 App 或 IDE 时，退出并重开对应客户端。",
            "在 Codex CLI 输入 /hooks，找到 SeeEnd 项，审阅它运行的命令并信任当前定义。新增或更新 Hooks 后需要重新审阅，写入配置不等于已获信任。",
            "准备好你实际使用的 Codex 会话，先不要发送验证任务。若 App 或 IDE 版本不支持这些 Hooks，请准备本机 CLI 会话，并核对该客户端的支持情况。",
        ],
        "claude-code": [
            "在本机 Claude Code 输入 /hooks，核对列表里有 SeeEnd，且来源为你刚设置的用户配置文件。",
            "Claude Code 通常会自动读取配置变更。若没有新条目，等当前任务结束后退出并重新打开 Claude Code，再核对 /hooks。",
            "若工具提示 Hooks 被关闭或受组织策略限制，按工具中的提示处理；SeeEnd 不会替你更改禁用开关或组织策略。",
        ],
        "qoder": [
            "使用本机 Qoder CLI 验证；Qoder 编辑器或 QoderWork 属于不同产品，不能凭本次 CLI 设置认定已接入。",
            "Qoder CLI 会读取配置变更。若仍未收到新任务，等当前任务结束后退出，再按原来的启动方式打开 Qoder CLI；保留原有自定义配置目录参数。",
        ],
        "gemini-cli": [
            "等当前任务结束后退出并重新打开本机 Gemini CLI，让新配置重新加载。",
            "在 Gemini CLI 输入 /hooks panel，查看 SeeEnd 的 aiflame-gemini-cli 项。若它被禁用，按你的意图只启用需要的 SeeEnd 项；不要为接入批量开启其他 Hooks。",
        ],
        "opencode": [
            "等当前任务结束后退出并重新启动本机 OpenCode；本地插件在启动时加载，只新建聊天可能不会重新加载插件。",
            "如果客户端连接已有 OpenCode 服务，需重新启动运行插件的服务进程，再让客户端重新连接；只刷新客户端界面不足以加载新插件。",
        ],
        "deepseek-harness": [
            "等当前任务结束后退出 DeepSeek Harness，再用这里选中的同一个 Profile 启动。只换一条聊天或启动其他 Profile 不会验证这次安装。",
            ("终端启动命令：" + quote_command(["dsh", "--profile", profile])) if profile else "终端启动时使用 dsh --profile 后跟你选中的 Profile 名称。",
        ],
    }[source]
    if source == "deepseek-harness":
        preparation = [
            "先确认 DeepSeek Harness 已安装并能正常回复；保持 SeeEnd 运行。macOS 先把 SeeEnd.app 放入「应用程序」。无需灯具或向 SeeEnd 提供模型 API Key。",
            "Profile 是你启动 DeepSeek Harness 时使用的一套配置。选择你实际使用的已有 Profile；名字要与 dsh --profile 后的名称一致。一个 Profile 接入不会影响其他 Profile。",
            "SeeEnd 需要找到 dsh 和 pnpm 才能添加插件。若检查提示找不到，请从能运行这两个命令的环境启动 SeeEnd。使用 DSH_HOME 时，两个程序必须使用同一个值。",
            "没有你使用的 Profile 时，先在 DeepSeek Harness 中准备它，再用 SeeEnd Skill 检查；SeeEnd 不会自动创建 Profile。",
        ]
    return {"intro": f"使用 SeeEnd Skill 为 {name} 安装接入，完成加载与授权后，用同一 Skill 验证一条新任务。",
            "preparation": preparation, "reload_steps": reload_steps,
            "verification_prompt": VERIFICATION_PROMPT, "limitations": limitations}


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
    first_use = onboarding(source, name, profile)
    result = {"source_id": source, "name": name, "preservation": PRESERVATION,
              "docs_url": DOCS[source], "skill_url": SKILL_URL,
              "automatic": "在编程工具中使用 SeeEnd Skill 安装接入，并按 Skill 的提示验证新任务。",
              "onboarding": first_use,
              "preparation": [f"先确认本机 {name} 能正常回复。", "核对下方实际配置位置；通常无需更改，自定义时填写配置文件所在文件夹。"],
              "next_steps": [*first_use["reload_steps"], "使用 SeeEnd Skill 开始验证，再在工具中发送新任务并等回复结束。模拟测试不算真实接入验证。"]}
    if source == "deepseek-harness":
        selected = profile or "<请选择已有 Profile>"
        directory = profile_directory() / selected
        bundle = runtime_dir() / "cli/deepseek-bundle"
        result.update(
            config_dir=str(directory),
            files=[str(directory / "package.json"), str(directory / "pnpm-lock.yaml")],
            changes=["仅向所选 Profile 增加 aiflame-deepseek-harness 依赖与 dsh.profile.bundles 条目。", "由 dsh / pnpm 插件管理器维护依赖和锁文件；SeeEnd 不替换整个 Profile。"],
            automatic="使用 SeeEnd Skill 为实际使用的已有 Profile 安装接入。需要 SeeEnd 能找到 dsh 和 pnpm；不会自动创建 Profile。",
            snippet=quote_command(deepseek_command(selected, bundle)),
            copy_label="复制插件安装命令",
            manual="优先使用 SeeEnd Skill 调用安装接口准备插件包。若管理器安装失败，在能运行 dsh 与 pnpm 的终端执行下面同一条命令；不要手动替换 package.json 或锁文件。其他 Profile 需分别接入。失败时先查看管理器提示，备份不能代替依赖事务回滚。",
        )
        result["preparation"] = result["onboarding"]["preparation"]
        return result
    directory = directory_for(source, config_dir)
    result["config_dir"] = str(directory)
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
            manual="下方是文件清单，不是 opencode.json 配置。优先让 SeeEnd Skill 调用增量安装接口生成所有组件与归属记录。手动创建时仅在文件不存在时保存 content 或复制 copy_from 模块。遇到同名文件先核对归属，不覆盖。",
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
        result["preparation"].append("需要支持 Hooks 的本机 Codex 版本；不为 Codex 云端会话安装接入。")
    elif source == "claude-code":
        result["preparation"].append("留空时使用 CLAUDE_CONFIG_DIR，默认 ~/.claude；SeeEnd 不修改项目级配置。")
        result["next_steps"].append("可在 Claude Code 输入 /hooks 核对。若 disableAllHooks 为 true，按你的意图启用，SeeEnd 不擅自改动此开关。")
    elif source == "gemini-cli":
        result["next_steps"].append("若 hooksConfig.enabled 为 false 或 SeeEnd 在禁用列表中，按你的意图调整；SeeEnd 不开启其他 Hooks。")
    return result


def guide_text(guide):
    sections = ["自动备份，增量添加，不替换整个配置文件。", "配置文件：\n" + "\n".join(guide["files"]),
                "修改范围：\n" + "\n".join(guide["changes"])]
    if guide["source_id"] == "codex":
        sections.append("Codex 提示信任 Hooks 时，请先确认。")
    return "\n\n".join(sections)
