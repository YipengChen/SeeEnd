"""Read-only configuration checks and content-free real lifecycle evidence."""

import json
from pathlib import Path
import shutil

from aiflame.core.process import command
from aiflame.integrations.codex.registration import quote_command
from .install import settings, available_profiles, profile_directory, owns_hook, owned_dependency
from .providers import PROVIDERS
from .guidance import directory_for
from .report import inspect_hooks, summarize

SIGNALS = {
    "start": ("state='working' AND seq=1", "提交消息"),
    "activity": ("state='working' AND seq>1", "工具活动"),
    "complete": ("state='done'", "回复完成"),
    "needs_input": ("state='needs_input'", "等待输入"),
    "interrupt": ("state='idle' AND reason='cancelled'", "主动中断"),
    "ended": ("state='unknown' AND reason='failed'", "任务未正常结束"),
}


def diagnostics(db, *, source=None, config_dirs=None, profiles=None,
                source_enabled=None, verification=None):
    try:
        _, saved = settings()
    except (OSError, ValueError, TypeError):
        saved = {}
    integrations = []
    self_tests = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='self_tests'").fetchone()
    for current, provider in PROVIDERS.items():
        if source and source != current:
            continue
        result = {"source_id": current, "name": provider.name, "issues": [],
                  "profiles": [], "hooks_configured": [], "signals": {}, "checks": []}
        try:
            if current == "deepseek-harness":
                if (config_dirs or {}).get(current) is not None:
                    result["issues"].append("DeepSeek Harness 不支持 config_dir；请通过 DSH_HOME 选择实际配置根目录")
                config = saved.get(current, {})
                result["profiles"] = available_profiles()
                result["recorded_profiles"] = config.get("profiles", [])
                result["installed_profiles"] = []
                profile_errors = {}
                for profile in result["profiles"]:
                    manifest = profile_directory() / profile / "package.json"
                    try:
                        package = json.loads(manifest.read_text(encoding="utf-8"))
                        dependencies = package.get("dependencies", {})
                        bundles = package.get("dsh", {}).get("profile", {}).get("bundles", [])
                        if isinstance(dependencies, dict) and isinstance(bundles, list) and "aiflame-deepseek-harness" in dependencies and "aiflame-deepseek-harness" in bundles:
                            if owned_dependency(dependencies["aiflame-deepseek-harness"], config.get("bundle", "/nonexistent-seeend-bundle"), manifest.parent):
                                result["installed_profiles"].append(profile)
                            else:
                                profile_errors[profile] = "Profile 中同名插件指向其他位置：" + str(manifest) + "；请核对归属，不直接覆盖"
                    except (OSError, ValueError, TypeError, AttributeError):
                        profile_errors[profile] = "无法读取 Profile 配置：" + str(manifest)
                selected = (profiles or {}).get(current)
                result["profile"] = selected
                if not result["profiles"]:
                    result["issues"].append("未发现已有 Profile：" + str(profile_directory()) + "；先在 DeepSeek Harness 中准备 Profile，再检查")
                elif selected and selected not in result["profiles"]:
                    result["issues"].append("所选 Profile 不存在：" + selected + "；重新选择实际使用的 Profile")
                elif selected in profile_errors:
                    result["issues"].append(profile_errors[selected] + "；先修正格式或权限")
                elif selected and selected not in result["installed_profiles"]:
                    result["issues"].append("所选 Profile 尚未接入：" + str(profile_directory() / selected / "package.json") + "；使用 SeeEnd Skill 为这个 Profile 安装接入")
                elif not result["installed_profiles"]:
                    result["issues"].append("尚无 Profile 安装 SeeEnd 插件；使用 SeeEnd Skill 为实际使用的 Profile 安装接入")
                if result["installed_profiles"]:
                    bridge_path = Path(config.get("bundle", "")) / "bridge.json"
                    try:
                        bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
                        if bridge.get("helperArgv") != command("--cli-hook", current):
                            result["issues"].append("DeepSeek 桥接指向旧版 SeeEnd：" + str(bridge_path) + "；使用 SeeEnd Skill 修复所选 Profile 的接入路径")
                    except (OSError, ValueError, AttributeError):
                        result["issues"].append("DeepSeek 桥接配置缺失或无法读取：" + str(bridge_path) + "；先核对读取权限，再使用 SeeEnd Skill 按具体诊断修复")
                result["limitations"] = ["仅检查所选／已安装的 Profile；其他 Profile 需分别连接。"]
                for executable in ("dsh", "pnpm"):
                    found = bool(shutil.which(executable))
                    result["checks"].append({"id": executable, "status": "ok" if found else "warning", "message": executable + ("：SeeEnd 可调用" if found else "：SeeEnd 找不到，自动添加不可用"), "action": "在能运行此命令的环境启动 SeeEnd，再使用 SeeEnd Skill 重试" if not found else ""})
            elif current == "opencode":
                from .opencode import wrapper, plugin_ready
                directory = directory_for(current, (config_dirs or {}).get(current))
                result["config_dir"] = str(directory)
                expected = {
                    "plugins/aiflame.js": wrapper().encode(),
                    "aiflame/index.mjs": (Path(__file__).with_name("opencode_bundle") / "index.mjs").read_bytes(),
                }
                for relative, content in expected.items():
                    target = directory / relative
                    if not target.exists():
                        result["issues"].append("缺少 OpenCode 插件文件：" + str(target) + "；使用 SeeEnd Skill 安装或修复接入，补齐插件")
                    elif target.read_bytes() != content:
                        result["issues"].append("OpenCode 插件内容不匹配：" + str(target) + "；先核对是否为第三方或手动修改，不能直接覆盖")
                    else:
                        result["checks"].append({"id": relative, "status": "ok", "message": "插件文件已就绪：" + str(target), "action": ""})
                target = directory / "aiflame/bridge.json"
                try:
                    bridge = json.loads(target.read_text(encoding="utf-8"))
                    if bridge.get("helperArgv") != command("--cli-hook", current):
                        result["issues"].append("OpenCode 桥接指向旧版 SeeEnd：" + str(target) + "；使用 SeeEnd Skill 修复接入，仅更新本应用拥有的文件")
                except (OSError, ValueError, AttributeError):
                    result["issues"].append("OpenCode 桥接配置缺失或无效：" + str(target) + "；使用 SeeEnd Skill 核对配置位置及读取权限，按具体诊断修复")
                if not result["issues"] and not plugin_ready(directory):
                    result["issues"].append("无法确认 OpenCode 插件归属；请核对归属记录与现有文件，不能直接覆盖")
                result["limitations"] = ["重启 OpenCode 后加载插件；不修改 opencode.json / opencode.jsonc。"]
                if not result["issues"]:
                    result["hooks_configured"] = list(provider.events)
            else:
                directory = directory_for(current, (config_dirs or {}).get(current))
                result["config_dir"] = str(directory)
                wanted = quote_command(command("--cli-hook", current))
                result.update(inspect_hooks(directory / provider.filename, provider.events, wanted,
                                            lambda h, w: owns_hook(h, w, current), current))
                result["limitations"] = ["配置检查无法读取工具中的 Hook 信任状态；须用真实新任务确认。", "中断及迟到事件的处理受工具生命周期能力限制。"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            result["issues"].append("无法读取该工具的接入配置；核对上方配置位置、文件格式及权限")
        result["trust_note"] = "配置检查不代表 Hook 已获信任；以真实接收记录为准"
        for key, (condition, name) in SIGNALS.items():
            if self_tests:
                condition += " AND id NOT IN (SELECT event FROM self_tests)"
            count, applied = db.execute("SELECT COUNT(*), COALESCE(SUM(status='applied'),0) FROM events WHERE source=? AND " + condition, (current,)).fetchone()
            row = db.execute("SELECT created,status FROM events WHERE source=? AND " + condition + " ORDER BY created DESC,rowid DESC LIMIT 1", (current,)).fetchone()
            result["signals"][key] = {"name": name, "received": count, "applied": applied, "last_seen": row[0] if row else None, "last_result": row[1] if row else None}
        for index, issue in enumerate(result["issues"]):
            if not any(check["message"] in issue for check in result["checks"] if check["status"] == "error"):
                result["checks"].append({"id": f"issue-{index}", "status": "error", "message": issue, "action": ""})
        integrations.append(summarize(result, (source_enabled or {}).get(current), (verification or {}).get(current)))
    return {"integrations": integrations}
