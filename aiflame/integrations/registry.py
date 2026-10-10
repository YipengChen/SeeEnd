"""Trigger metadata and lifecycle policies injected into the generic engine."""

from aiflame.core.events import SourcePolicy
from aiflame.integrations.chatgpt_web.host import WEB_SOURCES
from aiflame.integrations.desktop_clients.providers import DESKTOP_SOURCES
from aiflame.integrations.cli_tools.providers import CLI_SOURCES

SOURCES = (
    SourcePolicy("manual", "手动试亮", "四态测试：查看屏幕提醒，有灯具时另查设备回执"),
    SourcePolicy("demo", "模拟任务演示", "明确标记的模拟任务，不代表 AI 结果"),
    SourcePolicy(
        "local-script", "本地 API", "接收鉴权的标准任务状态事件", authenticated=True
    ),
    SourcePolicy(
        "codex",
        "Codex",
        "开始 / 完成 / 中断",
        authenticated=True,
        requires_start=True,
        ignore_unregistered=True,
        allow_late_cancel=True,
        working_lease_seconds=None,
    ),
    *(
        SourcePolicy(
            source, name, "原生生命周期接入 · 每次回复完成提醒",
            authenticated=True, requires_start=True, ignore_unregistered=True,
            allow_late_cancel=True,
            working_lease_seconds=None, default_enabled=False,
        )
        for source, name in CLI_SOURCES.items()
    ),
    *(
        SourcePolicy(
            source,
            name + " 网页",
            "共用 Chrome 扩展 · 在安装引导中设置，刷新网页后用新对话验证",
            authenticated=True,
            requires_start=True,
        )
        for source, name in WEB_SOURCES.items()
    ),
    *(
        SourcePolicy(
            source,
            name + " 桌面客户端（实验）",
            "macOS · 需辅助功能与输入监控权限 · 控件识别待实机验收",
            authenticated=True,
            requires_start=True,
            default_enabled=False,
        )
        for source, name in DESKTOP_SOURCES.items()
    ),
)


def integration_contract():
    """Pure public metadata for App/Skill compatibility checks, without runtime I/O."""
    from dataclasses import asdict
    from aiflame import __version__
    from aiflame.core.events import REQUIRED_FIELDS, OPTIONAL_FIELDS, STATES, REASONS

    native_sources = ["codex", *CLI_SOURCES]
    return {
        "contract_version": 1, "app_name": "SeeEnd", "app_version": __version__,
        "schema_version": "1.0", "required_fields": list(REQUIRED_FIELDS),
        "optional_fields": list(OPTIONAL_FIELDS), "states": list(STATES), "reasons": list(REASONS),
        "sources": [{**asdict(source), "native_integration": source.id in native_sources} for source in SOURCES],
        "capabilities": {"native_sources": native_sources, "verification_evidence": True,
                         "deepseek_config_dir": False, "device_targeting": True},
    }
