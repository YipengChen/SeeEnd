# Build on each target OS. Runtime credentials are deliberately excluded.
from pathlib import Path
import sys
import os
import tomllib

root = Path(SPECPATH).parent
sys.path.insert(0, str(root / "packaging"))
from native import NATIVE_MODULES, native_artifacts

native = native_artifacts(os.environ["SEEEND_NATIVE_DIR"])
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
hidden = list(NATIVE_MODULES)
data = [
    (str(root / "LICENSE"), "."),
    (str(root / "THIRD_PARTY_NOTICES.md"), "."),
    (str(root / "LIBRARY_REPLACEMENT.md"), "."),
    (str(root / "aiflame/assets"), "aiflame/assets"),
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
data.append((os.environ["SEEEND_THIRD_PARTY_LICENSES"], "third_party_licenses"))
data.append((os.environ["SEEEND_BUILD_RECEIPT"], "."))
a = Analysis(
    [str(root / "app.py")],
    pathex=[str(root)],
    binaries=[(str(path), str(Path(*module.split(".")[:-1])))
              for module, path in native.items()],
    datas=data,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "esptool",
        "mpremote",
        "serial",
        "readline",
        "zmq",
        "IPython",
        "matplotlib",
        "numpy",
        "pandas",
        "tkinter",
        "pytest",
        "PyQt6",
        "PyQt5",
        "PySide2",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick",
    ],
)
# Analyze original Python imports first so dependencies inside native libraries
# are retained, then discard protected bytecode before constructing the PYZ.
missing = set(NATIVE_MODULES) - {entry[0] for entry in a.pure}
if missing:
    raise RuntimeError("Core dependency analysis is incomplete: " + str(sorted(missing)))
a.pure[:] = [entry for entry in a.pure if entry[0] not in native]
# Directory data collection must not ship local Python caches or OS metadata.
a.datas = [entry for entry in a.datas if "__pycache__" not in Path(entry[0]).parts
           and not entry[0].endswith((".pyc", ".pyo", ".DS_Store"))]
# PDF preview is not used; do not pull QtWebEngine's PDF module into the app.
a.binaries = [entry for entry in a.binaries
              if not any(name in Path(entry[0]).name for name in ("libqpdf", "Qt6Pdf"))]
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
    # The helper has standard streams for browser native messaging.
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
            "NSAppleEventsUsageDescription": "SeeEnd 仅在你返回任务时，读取终端标题和位置，确认唯一匹配后切换对应的 Terminal 标签页或 iTerm2 分屏。",
            "NSLocalNetworkUsageDescription": "SeeEnd 在局域网发现并控制你的 Yeelight 灯具与米家智能插座。",
        },
    )
