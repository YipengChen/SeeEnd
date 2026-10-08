"""Archive the native bundle without flattening framework symlinks."""

import platform
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Archive SeeEnd for the current OS")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
args = parser.parse_args()
dist = args.dist_dir.resolve()
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
name = f"SeeEnd-{version}-{platform.system()}-{platform.machine()}"
if sys.platform == "darwin":
    subprocess.run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(dist / "SeeEnd.app"),
            str(dist / (name + ".zip")),
        ],
        check=True,
    )
elif sys.platform == "win32":
    shutil.make_archive(str(dist / name), "zip", root_dir=dist, base_dir="SeeEnd")
else:
    shutil.make_archive(str(dist / name), "gztar", root_dir=dist, base_dir="SeeEnd")
print("Archive:", name)
