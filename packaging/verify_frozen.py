"""Exercise the built application's native CLI helper on the target OS."""

import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    if sys.platform == "darwin":
        helper = root / "dist/SeeEnd.app/Contents/MacOS/SeeEnd"
    elif sys.platform == "win32":
        helper = root / "dist/SeeEnd/SeeEnd Helper.exe"
    else:
        helper = root / "dist/SeeEnd/SeeEnd"
    if not helper.is_file():
        raise FileNotFoundError(helper)
    # The GPL utility is a separate executable, with no SeeEnd module archive.
    from PyInstaller.archive.readers import CArchiveReader
    app_archive = CArchiveReader(str(helper))
    pyz = next(name for name in app_archive.toc if name.endswith(".pyz"))
    modules = app_archive.open_embedded_archive(pyz).toc
    if any(name == "esptool" or name.startswith("esptool.") for name in modules):
        raise RuntimeError("GPL esptool was bundled into the desktop module archive")
    if "readline" in modules:
        raise RuntimeError("GPL readline was bundled into the desktop module archive")
    if sys.platform == "darwin":
        contents = helper.parent.parent
        tool = contents / "Resources/third_party/esptool/esptool"
        tool_archive = CArchiveReader(str(tool))
        tool_pyz = next(name for name in tool_archive.toc if name.endswith(".pyz"))
        tool_modules = tool_archive.open_embedded_archive(tool_pyz).toc
        if any(name == "aiflame" or name.startswith("aiflame.") for name in tool_modules):
            raise RuntimeError("Independent tool contains SeeEnd application modules")
        if any(contents.rglob("*readline*.dylib")):
            raise RuntimeError("GPL readline dynamic library was bundled")
    result = subprocess.run([str(helper), "--esptool", "version"],
                            capture_output=True, text=True, timeout=20)
    if result.returncode or "5.4.0" not in result.stdout:
        raise RuntimeError("Independent esptool did not run: " + result.stdout + result.stderr)
    print("Independent esptool 5.4.0 verified; desktop archive contains no esptool/readline.")
    environment = {**os.environ, "AIFLAME_TEST_FROZEN_HELPER": str(helper)}
    return subprocess.call(
        [sys.executable, "-m", "unittest",
         "test_cli_http.CliHttpTests.test_frozen_helpers_deliver_real_lifecycle", "-v"],
        cwd=root / "tests",
        env={**environment, "PYTHONPATH": str(root)},
    )


if __name__ == "__main__":
    raise SystemExit(main())
