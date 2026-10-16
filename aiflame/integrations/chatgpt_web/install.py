"""Native messaging registration, with explicit OS-specific paths."""

import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from aiflame.core.paths import PACKAGE, user_data_dir, load_config, write_private
from aiflame.core.process import command
from aiflame.integrations.chatgpt_web.host import HOST, WEB_SOURCES, extension_id, extension_origins


def manifest_path(browser, platform=None, home=None, environ=None):
    platform = platform or sys.platform
    home = Path(home) if home else Path.home()
    environ = os.environ if environ is None else environ
    if platform == "darwin":
        vendor = "Google/Chrome" if browser == "chrome" else "Microsoft Edge"
        return (
            home
            / "Library/Application Support"
            / vendor
            / "NativeMessagingHosts"
            / (HOST + ".json")
        )
    if platform == "win32":
        return (
            user_data_dir(platform, home, environ)
            / "BrowserBridge"
            / browser
            / (HOST + ".json")
        )
    vendor = "google-chrome" if browser == "chrome" else "microsoft-edge"
    return (
        Path(environ.get("XDG_CONFIG_HOME", home / ".config"))
        / vendor
        / "NativeMessagingHosts"
        / (HOST + ".json")
    )


def install(browser="chrome", uninstall=False, output=None):
    target = Path(output) if output else manifest_path(browser)
    installed = user_data_dir() / "BrowserBridge" / browser
    if sys.platform == "win32":
        executable = Path(sys.executable).with_name("SeeEnd Bridge.exe")
        if not output and not executable.is_file():
            raise RuntimeError(
                "Windows 原生桥接请使用打包后的 App（包含 SeeEnd Bridge.exe）"
            )
        launcher = executable
    else:
        launcher = installed / "browser_host_launcher.sh"
    manifest = {
        "name": HOST,
        "description": "SeeEnd AI chat websites status bridge",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": list(extension_origins()),
    }
    if target.exists():
        previous = json.loads(target.read_text(encoding="utf-8"))
        if (
            previous.get("name") != HOST
            or previous.get("type") != "stdio"
            or previous.get("path") != str(launcher)
            or not isinstance(previous.get("allowed_origins"), list)
            or not previous["allowed_origins"]
            or not set(previous["allowed_origins"]) <= set(manifest["allowed_origins"])
        ):
            raise RuntimeError("同名桥接属于其他配置，未修改")
    if uninstall:
        target.unlink(missing_ok=True)
        if sys.platform == "win32" and not output:
            import winreg

            key = (
                (
                    "Software\\Google\\Chrome"
                    if browser == "chrome"
                    else "Software\\Microsoft\\Edge"
                )
                + "\\NativeMessagingHosts\\"
                + HOST
            )
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass
        return target
    if not output:
        installed.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sys.platform != "win32":
            # Keep source-mode code outside Documents/Desktop for macOS TCC.
            args = command("--browser-host")
            if not getattr(sys, "frozen", False):
                host = installed / "host.py"
                host.write_bytes(
                    (PACKAGE / "integrations/chatgpt_web/host.py").read_bytes()
                )
                write_private(
                    installed / "bridge-config.json",
                    {
                        "extension_id": extension_id(),
                        "tokens": {
                            source: token
                            for source, token in load_config()["tokens"].items()
                            if source in WEB_SOURCES
                        },
                    },
                )
                args = [str(Path(sys.executable).resolve()), str(host)]
            launcher.write_text("#!/bin/sh\nexec " + shlex.join(args) + ' "$@"\n', encoding="utf-8")
            launcher.chmod(0o700)
    write_private(target, manifest)
    if sys.platform == "win32" and not output:
        import winreg

        key = (
            (
                "Software\\Google\\Chrome"
                if browser == "chrome"
                else "Software\\Microsoft\\Edge"
            )
            + "\\NativeMessagingHosts\\"
            + HOST
        )
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, str(target))
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", choices=("chrome", "edge"), default="chrome")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(install(args.browser, args.uninstall, args.output))
    print("Extension ID:", extension_id())
    print("Extension directory:", PACKAGE / "integrations/chatgpt_web/extension")


if __name__ == "__main__":
    main()
