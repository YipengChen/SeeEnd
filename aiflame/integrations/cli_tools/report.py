"""Actionable configuration checks, kept distinct from real task evidence."""

import json
from pathlib import Path


def inspect_hooks(path, events, wanted, owns, source):
    path = Path(path)
    result = {"config_file": str(path), "hooks_configured": [], "issues": [], "checks": []}

    def check(key, ok, message, action=""):
        result["checks"].append({"id": key, "status": "ok" if ok else "error", "message": message, "action": action})
        if not ok:
            result["issues"].append(message + ("；" + action if action else ""))

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        check("file", False, "未找到配置文件：" + str(path), "在编程工具中使用 SeeEnd Skill 安装接入")
        return result
    except json.JSONDecodeError as error:
        check("file", False, f"配置 JSON 无法解析：{path}（第 {error.lineno} 行、第 {error.colno} 列）", "修正 JSON 后重试；未自动改写原文件")
        return result
    except (OSError, UnicodeError):
        check("file", False, "无法读取配置文件：" + str(path), "检查目录和读取权限")
        return result
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        check("format", False, "配置中的 hooks 必须是对象：" + str(path), "保留其他配置，修正 hooks 格式后重试")
        return result
    check("file", True, "配置文件可读取：" + str(path))
    hooks = data.get("hooks", {})
    disabled = []
    if source == "claude-code" and data.get("disableAllHooks") is True:
        check("enabled", False, "Claude Code 的 disableAllHooks=true，Hooks 已被关闭", "在 Claude Code 中确认是否启用 Hooks；添加 SeeEnd 接入不会更改此设置")
    if source == "gemini-cli":
        controls = data.get("hooksConfig", {})
        if isinstance(controls, dict):
            if controls.get("enabled") is False:
                check("enabled", False, "Gemini CLI 的 hooksConfig.enabled=false", "在 Gemini CLI 中确认是否启用 Hooks；添加 SeeEnd 接入不会更改此设置")
            disabled = controls.get("disabled", [])
        legacy = hooks.get("disabled", [])
        disabled = [*(disabled if isinstance(disabled, list) else []), *(legacy if isinstance(legacy, list) else [])]
    for event in events:
        groups = hooks.get(event, [])
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or any(not isinstance(h, dict) for h in group["hooks"]) for group in groups):
            check(event, False, f"hooks.{event} 格式不正确：{path}", "该事件须为包含 hooks 数组的组列表；先修复格式")
            continue
        handlers = [h for group in groups for h in group["hooks"]]
        current = [h for h in handlers if h.get("type") == "command" and h.get("command") == wanted]
        active = [h for h in current if h.get("enabled") is not False and h.get("name") not in disabled]
        if active:
            result["hooks_configured"].append(event)
            check(event, True, event + "：SeeEnd Hook 已配置")
        elif current:
            check(event, False, event + "：SeeEnd Hook 被禁用", "在工具中核对禁用设置，仅启用需要的 SeeEnd Hook")
        elif any(owns(h, wanted) for h in handlers):
            check(event, False, event + "：指向旧版 SeeEnd 或旧安装位置", "使用 SeeEnd Skill 修复接入，更新本应用路径后重新加载")
        else:
            check(event, False, event + "：缺少 SeeEnd Hook", "使用 SeeEnd Skill 增量安装或修复接入")
    return result


def summarize(result, enabled=None, verification=None):
    result["configuration_ready"] = not result.get("issues")
    result["connect_label"] = "使用 SeeEnd Skill"
    checks = result.setdefault("checks", [])
    evidence = verification or {}
    if not result["configuration_ready"]:
        issue = result["issues"][0]
        first = next((item for item in checks if item.get("status") == "error"), {})
        result.update(status="configuration_error", summary="接入配置有问题：" + issue, next_action=first.get("action") or "按下方失败项的处理办法修复后重新检查。")
    elif enabled is False:
        result.update(status="disabled", summary="配置已准备，提醒开关尚未开启。", next_action="开启此工具的提醒开关，再使用 SeeEnd Skill 验证接入。")
    elif evidence.get("status") == "verified":
        result.update(status="connected", summary="此来源已有同一真实任务的开始与完成记录；本次配置目录或 Profile 尚需单独验证。", next_action="在实际使用的配置目录或 Profile 会话发起新任务，用 SeeEnd Skill 核对当前验证窗口的事件。")
    elif evidence.get("status") == "working":
        result.update(status="receiving", summary="已收到真实任务开始，正在等待本次完成。", next_action="等工具回复结束后再次检查。")
    elif evidence.get("status") in {"error", "cancelled", "timeout", "needs_input", "incomplete", "unknown"}:
        label = {"error": "任务失败", "cancelled": "任务已取消", "timeout": "任务超时", "needs_input": "等待输入", "incomplete": "开始或完成信号不完整", "unknown": "任务状态未确定"}[evidence["status"]]
        result.update(status="task_incomplete", summary="已收到真实任务信号，最近一次" + label + "；尚未确认完整接入。", next_action="按工具中的任务状态处理，再完成一条新任务验证；任务失败不一定是接入故障。")
    else:
        result.update(status="ready_unverified", summary="配置已就绪，但尚未验证真实任务接入。", next_action="按 SeeEnd Skill 的提示完成加载与授权，再用 Skill 验证一条新任务。")
    checks.append({"id": "source", "status": "error" if enabled is False else "ok" if enabled is True else "pending", "message": "来源开关：" + ("已开启" if enabled is True else "未开启" if enabled is False else "请在 APP 中核对"), "action": ""})
    result["verification"] = evidence
    feedback = {
        "connected": "此来源已有真实接收记录；当前目录或 Profile 的会话仍需单独验证。",
        "disabled": "提醒已关闭。开启此工具的来源开关，再使用 SeeEnd Skill 验证接入。",
        "receiving": "已收到任务开始，等回复结束后再检查。",
        "ready_unverified": "配置已就绪。完成工具加载与信任后，使用 SeeEnd Skill 验证接入。",
        "task_incomplete": "已收到任务信号。处理工具中的任务状态后再试一次。",
    }
    if result["status"] == "configuration_error":
        first = next((item for item in checks if item["status"] == "error"), {})
        if first.get("id") == "file" and first.get("message", "").startswith("未找到"):
            result["feedback"] = "尚未接入。使用 SeeEnd Skill 安装并验证接入。"
            result["next_action"] = "在编程工具中使用 SeeEnd Skill 安装接入。"
        else:
            reason = result["issues"][0].split("；", 1)[0]
            for key in ("config_file", "config_dir"):
                path = result.get(key)
                if path:
                    reason = reason.replace(path, Path(path).name)
            result["feedback"] = "需要处理：" + reason
        if result.get("source_id") == "deepseek-harness":
            if not result.get("profiles"):
                result["feedback"] = "未找到 Profile，请先在 DeepSeek Harness 中准备一个。"
            elif result["issues"][0].startswith(("所选 Profile 尚未接入", "尚无 Profile 安装")):
                result["feedback"] = "使用 SeeEnd Skill 为实际使用的 Profile 安装并验证接入。"
    else:
        result["feedback"] = feedback[result["status"]]
        if result["status"] == "task_incomplete":
            result["feedback"] = "最近一次" + label + "。在工具中处理后，再发一条新任务检查。"
    checks.append({"id": "reception", "status": "ok" if result["status"] == "connected" else "pending", "message": result["summary"], "action": result["next_action"]})
    result["evidence_scope"] = "真实接收记录按工具汇总；本次文件检查不能读取工具实际加载的配置目录或 Hook 信任状态。切换目录／Profile 后请在对应会话中验证新任务。"
    return result
