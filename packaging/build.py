"""Run on the target OS: python packaging/build.py."""

import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import argparse
from datetime import datetime, timezone

from native import build_native
from verify_native import verify_native
from release import RECEIPT, sha256_file, source_identity, write_receipt

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Build SeeEnd for the current OS")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
parser.add_argument("--verify-native", action="store_true",
                    help="Run the full regression suite against the compiled core before freezing")
parser.add_argument("--download-sources", action="store_true",
                    help="Fetch and verify missing third-party source materials for a clean build")
args = parser.parse_args()
destination = args.dist_dir.resolve()
with tempfile.TemporaryDirectory(prefix="aiflame-build-") as work:
    env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(Path(work) / "cache")}
    # Check redistribution materials before spending time compiling the core.
    prepare = [sys.executable, str(root / "packaging/prepare_licenses.py")]
    if args.download_sources:
        prepare.append("--download")
    subprocess.run(prepare, check=True)
    identity = source_identity(root)
    identity.update({
        "built_at": datetime.now(timezone.utc).isoformat(),
        "third_party_sources_sha256": sha256_file(root / "third_party_sources/MANIFEST.json"),
        "third_party_licenses_sha256": sha256_file(root / "packaging/generated_licenses/MANIFEST.json"),
    })
    receipt = Path(work) / RECEIPT
    write_receipt(receipt, identity)
    env["SEEEND_BUILD_RECEIPT"] = str(receipt)
    native = build_native(root, Path(work) / "native")
    verify_native(root, native, Path(work) / "regression", regression=args.verify_native)
    env["SEEEND_NATIVE_DIR"] = str(native)
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
    if result == 0:
        if source_identity(root)["source_sha256"] != identity["source_sha256"]:
            raise RuntimeError("Source changed during the build; rebuild before distributing")
        write_receipt(destination / RECEIPT, identity)
        print("Build identity:", identity["app_version"], identity["git_revision"], identity["source_sha256"])
raise SystemExit(result)
