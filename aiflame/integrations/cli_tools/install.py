"""Install only owned lifecycle hooks and an explicit DeepSeek profile plugin."""

import json
import os
from pathlib import Path
import shutil
import subprocess

from aiflame.core.paths import runtime_dir, write_private
from aiflame.core.process import command
from aiflame.integrations.codex.registration import owns_command, quote_command
from aiflame.integrations.codex.install import backup_file, write_incremental, merge_owned_hooks
from aiflame.integrations.cli_tools.providers import PROVIDERS, config_directory

MARKER = "SeeEnd：同步 CLI 灯光状态"


class IntegrationInstallError(RuntimeError):
    def __init__(self, message, error_code):
        super().__init__(message)
        self.error_code = error_code


def profile_directory():
    home = Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")).expanduser()
    return home / "profiles"


def available_profiles():
    directory = profile_directory()
    return sorted(path.name for path in directory.iterdir() if path.is_dir()) if directory.is_dir() else []


def settings(runtime=None):
    root = Path(runtime) if runtime is not None else runtime_dir()
    path = root / "cli-integrations.json"
    if path.is_symlink():
        raise ValueError("CLI 接入记录是符号链接，未修改")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict) or any(not isinstance(value, dict) for value in data.values()):
        raise ValueError("无法识别 CLI 接入配置")
    for value in data.values():
        if ("profiles" in value and (not isinstance(value["profiles"], list) or any(not isinstance(profile, str) for profile in value["profiles"]))) or any(key in value and not isinstance(value[key], str) for key in ("bundle", "config_dir")):
            raise ValueError("无法识别 CLI 接入配置")
        if "config_dirs" in value and (not isinstance(value["config_dirs"], list) or any(not isinstance(item, str) for item in value["config_dirs"])):
            raise ValueError("无法识别 CLI 接入配置")
    return path, data


def configured(source, directory):
    """Recognize an existing registration without treating a saved path as proof."""
    directory = Path(directory)
    if source == "opencode":
        from .opencode import plugin_ready
        return plugin_ready(directory)
    from aiflame.integrations.codex.registration import owns_hook as codex_owns
    suffix = ("--codex-hook",) if source == "codex" else ("--cli-hook", source)
    path = directory / ("hooks.json" if source == "codex" else PROVIDERS[source].filename)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {})
        wanted = quote_command(command(*suffix))
        return any((codex_owns(handler, wanted) if source == "codex" else owns_hook(handler, wanted, source))
                   for groups in hooks.values() if isinstance(groups, list)
                   for group in groups if isinstance(group, dict) and isinstance(group.get("hooks"), list)
                   for handler in group["hooks"] if isinstance(handler, dict))
    except (OSError, ValueError, AttributeError):
        return False


def registered_directories(source, saved):
    record = saved.get(source, {})
    if "config_dirs" in record:
        return list(record["config_dirs"])
    previous = record.get("config_dir")
    return [previous] if previous and configured(source, previous) else []


def record_registration(saved_path, saved, source, directory, uninstall=False):
    directories = set(registered_directories(source, saved))
    target = str(Path(directory).resolve())
    directories.discard(target) if uninstall else directories.add(target)
    saved[source] = {**saved.get(source, {}), "config_dir": sorted(directories)[-1] if uninstall and directories else target,
                     "config_dirs": sorted(directories)}
    write_incremental(saved_path, saved)


def owns_hook(handler, wanted, source):
    return handler.get("type") == "command" and owns_command(handler.get("command"), wanted, ("--cli-hook", source))


def hook_handler(source):
    handler = {"type": "command", "command": quote_command(command("--cli-hook", source)), "timeout": 5000 if source == "gemini-cli" else 5}
    if source == "gemini-cli":
        handler.update(name="aiflame-" + source, description=MARKER)
    else:
        handler["statusMessage"] = MARKER + " · " + source
    return handler


def owned_dependency(value, bundle, profile_dir):
    if not isinstance(value, str) or not value.startswith(("file:", "link:")):
        return False
    path = Path(value.split(":", 1)[1])
    if not path.is_absolute():
        path = Path(profile_dir) / path
    return path.resolve() == Path(bundle).resolve()


def deepseek_command(profile, bundle, uninstall=False):
    arguments = ["dsh", "plugin", "--profile", profile, "remove" if uninstall else "add"]
    if (profile_directory() / profile / "pnpm-workspace.yaml").is_file():
        arguments.append("-w")
    arguments.append("aiflame-deepseek-harness" if uninstall else "file:" + str(bundle))
    return arguments


def install(source, config_dir=None, profile=None, uninstall=False):
    if source not in PROVIDERS:
        raise ValueError("CLI 来源不受支持")
    saved_path, saved = settings()
    provider = PROVIDERS[source]
    if source == "deepseek-harness":
        if config_dir is not None:
            raise IntegrationInstallError("DeepSeek Harness 不支持 config_dir；请通过 DSH_HOME 选择实际配置根目录，并选择 Profile", "config_invalid")
        if not isinstance(profile, str) or not profile.strip() or profile.startswith("-"):
            raise ValueError("请选择 DeepSeek Harness profile")
        if profile not in available_profiles():
            raise ValueError("DeepSeek Harness profile 不存在")
        bundle = saved_path.parent / "cli" / "deepseek-bundle"
        manifest = profile_directory() / profile / "package.json"
        lockfile = manifest.with_name("pnpm-lock.yaml")
        if manifest.is_symlink() or lockfile.is_symlink():
            raise ValueError("DeepSeek profile 配置是符号链接，未修改")
        package = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
        if not isinstance(package, dict) or not isinstance(package.get("dependencies", {}), dict) or not isinstance(package.get("dsh", {}), dict):
            raise ValueError("无法识别 DeepSeek profile 配置，未修改")
        dsh_profile = package.get("dsh", {}).get("profile", {})
        if not isinstance(dsh_profile, dict) or not isinstance(dsh_profile.get("bundles", []), list):
            raise ValueError("无法识别 DeepSeek profile 配置，未修改")
        dependency = package.get("dependencies", {}).get("aiflame-deepseek-harness")
        if dependency is not None or "aiflame-deepseek-harness" in dsh_profile.get("bundles", []):
            recorded = saved.get(source, {})
            if (profile not in recorded.get("profiles", []) or recorded.get("bundle") != str(bundle)
                    or not owned_dependency(dependency, bundle, manifest.parent)):
                raise ValueError("DeepSeek profile 中同名插件归属不明，未修改")
        if uninstall and dependency is None:
            recorded = saved.get(source, {})
            if profile in recorded.get("profiles", []):
                saved[source] = {**recorded, "profiles": [item for item in recorded["profiles"] if item != profile]}
                write_incremental(saved_path, saved)
            return bundle
        backup_file(manifest)
        backup_file(lockfile)
        if uninstall:
            arguments = deepseek_command(profile, bundle, uninstall=True)
        else:
            shutil.copytree(Path(__file__).with_name("deepseek_bundle"), bundle, dirs_exist_ok=True)
            write_private(bundle / "bridge.json", {"helperArgv": command("--cli-hook", source)})
            arguments = deepseek_command(profile, bundle)
        try:
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            raise IntegrationInstallError("DeepSeek Harness 插件管理超时", "plugin_timeout") from error
        if result.returncode:
            detail = (result.stderr or "") + (result.stdout or "")
            if "ERR_PNPM_ADDING_TO_ROOT" in detail:
                raise IntegrationInstallError("pnpm 拒绝在 Profile 工作区根目录安装；请按终端提示使用 -w，并核对所选 Profile", "plugin_workspace")
            if any(value in detail for value in ("EACCES", "EPERM", "Permission denied")):
                raise IntegrationInstallError("DeepSeek Profile 或依赖目录没有写权限，请检查所选 Profile 的权限", "io_error")
            if any(value in detail for value in ("ENOTFOUND", "ETIMEDOUT", "ERR_PNPM_FETCH", "ECONNREFUSED")):
                raise IntegrationInstallError("DeepSeek 插件管理器无法获取依赖，请检查网络与 pnpm 安装源后重试", "plugin_network")
            raise IntegrationInstallError("DeepSeek Harness profile 插件安装未完成", "integration_conflict")
        profiles = set(saved.get(source, {}).get("profiles", []))
        profiles.discard(profile) if uninstall else profiles.add(profile)
        saved[source] = {"profiles": sorted(profiles), "bundle": str(bundle)}
        write_incremental(saved_path, saved)
        return bundle
    directory = config_directory(source, config_dir or saved.get(source, {}).get("config_dir"))
    if source == "opencode":
        from aiflame.integrations.cli_tools.opencode import install_plugin
        path = install_plugin(directory, uninstall=uninstall)
        record_registration(saved_path, saved, source, directory, uninstall)
        return path
    path = directory / provider.filename
    if path.is_symlink():
        raise ValueError("settings.json 是符号链接，请选择实际配置目录，未修改")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError("无法识别现有 hooks 格式，未修改")
    if uninstall and (not path.exists() or "hooks" not in data):
        if source in saved:
            record_registration(saved_path, saved, source, directory, uninstall=True)
        return path
    hooks = data.setdefault("hooks", {})
    wanted = quote_command(command("--cli-hook", source))
    # Validate all affected groups before writing anything.
    for event in provider.events:
        groups = hooks.get(event, [])
        if not isinstance(groups, list) or any(not isinstance(group, dict) or not isinstance(group.get("hooks"), list) or any(not isinstance(h, dict) for h in group["hooks"]) for group in groups):
            raise ValueError("无法识别现有 hooks 格式，未修改")
    for event in provider.events:
        retained = merge_owned_hooks(hooks.get(event, []), lambda handler: owns_hook(handler, wanted, source), hook_handler(source), uninstall)
        if retained:
            hooks[event] = retained
        else:
            hooks.pop(event, None)
    if not (uninstall and not path.exists()):
        write_incremental(path, data)
    record_registration(saved_path, saved, source, directory, uninstall)
    return path
