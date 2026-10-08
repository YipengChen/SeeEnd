"""Read-only Codex wiring and received-signal diagnostics; never return content."""

from aiflame.core.process import command as helper_command
from aiflame.integrations.codex.registration import EVENTS, quote_command
from aiflame.integrations.cli_tools.guidance import directory_for
from aiflame.integrations.cli_tools.report import inspect_hooks, summarize
from aiflame.integrations.codex.registration import owns_hook

SIGNALS = {
    "start": ("state='working' AND seq=1", "提交消息"),
    "activity": ("state='working' AND seq>1", "工具活动"),
    "complete": ("state='done'", "回复完成"),
    "interrupt": ("state='idle' AND reason='cancelled'", "主动中断"),
}


def inspect_configuration(codex_home=None):
    home = directory_for("codex", codex_home)
    wanted = quote_command(helper_command("--codex-hook"))
    result = inspect_hooks(home / "hooks.json", EVENTS, wanted, owns_hook, "codex")
    result["configuration_ready"] = not result["issues"]
    result["trust_note"] = "配置检查不代表 Hook 已获信任；以真实接收记录为准"
    return result


def diagnostics(db, codex_home=None, *, enabled=None, verification=None):
    result = inspect_configuration(codex_home)
    result["signals"] = {}
    self_tests = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='self_tests'"
    ).fetchone()
    for key, (condition, name) in SIGNALS.items():
        if self_tests:
            condition += " AND id NOT IN (SELECT event FROM self_tests)"
        count, applied = db.execute(
            "SELECT COUNT(*), COALESCE(SUM(status='applied'),0) FROM events "
            "WHERE source='codex' AND " + condition
        ).fetchone()
        row = db.execute(
            "SELECT created,status FROM events WHERE source='codex' AND "
            + condition
            + " ORDER BY created DESC,rowid DESC LIMIT 1"
        ).fetchone()
        result["signals"][key] = {
            "name": name,
            "received": count,
            "applied": applied,
            "last_seen": row[0] if row else None,
            "last_result": row[1] if row else None,
        }
    result["history_note"] = "记录来自最近保留的 Codex 事件，可能同时包含 CLI 和桌面端"
    return summarize(result, enabled, verification)
