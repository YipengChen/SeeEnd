# Build on each target OS. Runtime credentials are deliberately excluded.
from pathlib import Path
import sys
import tomllib
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH).parent
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
hidden = collect_submodules("mpremote") + collect_submodules("esptool")
data = [
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(root / "aiflame/assets"), "aiflame/assets"),
    (str(root / "aiflame/devices/firmware"), "aiflame/devices/firmware"),
    (str(root / "aiflame/devices/wifi_protocol.py"), "aiflame/devices"),
    (
        str(root / "aiflame/integrations/chatgpt_web/extension"),
        "aiflame/integrations/chatgpt_web/extension",
    ),
    (
        str(root / "aiflame/integrations/cli_tools/deepseek_bundle"),
        "aiflame/integrations/cli_tools/deepseek_bundle",
    ),
]
data.append((str(root / "aiflame/integrations/cli_tools/opencode_bundle"), "aiflame/integrations/cli_tools/opencode_bundle"))
data += collect_data_files("esptool")
a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=[],
    datas=data,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "matplotlib",
        "numpy",
        "pandas",
        "tkinter",
        "IPython",
        "pytest",
        "PyQt6",
        "PyQt5",
        "PySide2",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
    ],
)
# Directory data collection must not ship local Python caches or OS metadata.
a.datas = [entry for entry in a.datas if "__pycache__" not in Path(entry[0]).parts
           and not entry[0].endswith((".pyc", ".pyo", ".DS_Store"))]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SeeEnd",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=sys.platform != "win32",
    disable_windowed_traceback=False,
    icon=str(root / "aiflame/assets/icon.ico") if sys.platform == "win32" else None,
)
executables = [exe]
if sys.platform == "win32":
    # The helper has standard streams for browser native messaging and USB tools.
    helper = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SeeEnd Helper",
        console=True,
        upx=False,
    )
    bridge = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="SeeEnd Bridge",
        console=True,
        upx=False,
    )
    executables += [helper, bridge]
coll = COLLECT(
    *executables, a.binaries, a.datas, strip=False, upx=False, name="SeeEnd"
)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="SeeEnd.app",
        icon=str(root / "aiflame/assets/icon.icns"),
        bundle_identifier="com.aiflame.desktop",
        info_plist={
            "CFBundleShortVersionString": version,
            # Keep helper stdin/stdout while exposing a normal Dock application.
            "LSBackgroundOnly": False,
            "LSUIElement": False,
            "NSHighResolutionCapable": True,
            "NSLocalNetworkUsageDescription": "SeeEnd 在局域网发现并控制你的 AI Flame 和 Yeelight 灯光设备。",
        },
    )
