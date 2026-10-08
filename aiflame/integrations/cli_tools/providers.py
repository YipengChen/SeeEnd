"""Native CLI hook capabilities, kept separate from the event engine."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Provider:
    name: str
    directory: str
    filename: str
    events: tuple[str, ...]


PROVIDERS = {
    "claude-code": Provider("Claude Code", ".claude", "settings.json", ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd")),
    "qoder": Provider("Qoder CLI", ".qoder", "settings.json", ("UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "Stop", "StopFailure", "SessionEnd")),
    "deepseek-harness": Provider("DeepSeek Harness", "", "", ("TurnStart", "TurnActivity", "TurnEnd")),
    "gemini-cli": Provider("Gemini CLI", ".gemini", "settings.json", ("BeforeAgent", "BeforeModel", "BeforeTool", "AfterTool", "AfterAgent", "Notification", "SessionEnd")),
    "opencode": Provider("OpenCode", ".config/opencode", "", ("TurnStart", "TurnActivity", "NeedsInput", "TurnEnd")),
}
CLI_SOURCES = {source: provider.name for source, provider in PROVIDERS.items()}


def config_directory(source, override=None):
    if override:
        return Path(override).expanduser().resolve()
    if source == "opencode":
        return Path(os.environ.get("OPENCODE_CONFIG_DIR") or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode").expanduser()
    value = os.environ.get("CLAUDE_CONFIG_DIR") if source == "claude-code" else None
    return Path(value or Path.home() / PROVIDERS[source].directory).expanduser()
