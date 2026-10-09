"""Run on the target OS: python packaging/build.py."""

import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import argparse

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Build SeeEnd for the current OS")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
args = parser.parse_args()
destination = args.dist_dir.resolve()
with tempfile.TemporaryDirectory(prefix="aiflame-build-") as work:
    env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(Path(work) / "cache")}
    subprocess.run([sys.executable, str(root / "packaging/prepare_licenses.py")], check=True)
    env["SEEEND_THIRD_PARTY_LICENSES"] = str(root / "packaging/generated_licenses")
    result = subprocess.call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(destination),
            "--workpath",
            str(Path(work) / "work"),
            str(root / "packaging/SeeEnd.spec"),
        ],
        cwd=root,
        env=env,
    )
    if result == 0 and sys.platform == "darwin":
        # BUNDLE has copied COLLECT; retain only the user-facing .app on macOS.
        shutil.rmtree(destination / "SeeEnd")
raise SystemExit(result)
