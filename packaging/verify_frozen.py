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
    retired = ("esptool", "mpremote", "serial", "aiflame.devices.firmware",
               "aiflame.transports.usb", "aiflame.transports.wifi", "aiflame.hub.tuning")
    if any(name == prefix or name.startswith(prefix + ".") for name in modules for prefix in retired):
        raise RuntimeError("Removed AI Flame modules were bundled")
    if sys.platform == "darwin" and (helper.parent.parent / "Resources/third_party/esptool").exists():
        raise RuntimeError("Removed esptool executable was bundled")
    print("Desktop archive contains no AI Flame hardware modules or tools.")
    environment = {**os.environ, "AIFLAME_TEST_FROZEN_HELPER": str(helper)}
    return subprocess.call(
        [sys.executable, "-m", "unittest",
         "test_cli_http.CliHttpTests.test_frozen_helpers_deliver_real_lifecycle", "-v"],
        cwd=root / "tests",
        env={**environment, "PYTHONPATH": str(root)},
    )


if __name__ == "__main__":
    raise SystemExit(main())
