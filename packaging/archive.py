"""Archive a matching build without flattening macOS framework symlinks."""

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from release import RECEIPT, third_party_files, validate_build


root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Archive SeeEnd for the current OS")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
args = parser.parse_args()
dist = args.dist_dir.resolve()
identity = validate_build(root, dist)
version = identity["app_version"]
name = f"SeeEnd-{version}-{identity['platform']}-{identity['architecture']}"
materials = root / "third_party_sources"
sources = third_party_files(materials)
notices = root / "packaging/generated_licenses"
license_files = sorted(path for path in notices.rglob("*") if path.is_file())
if any(path.is_symlink() for path in license_files):
    raise RuntimeError("License materials must not contain external links")

sys.path.insert(0, str(root))
from aiflame.integrations.chatgpt_web.guide import offline_html  # noqa: E402 — root added for direct script execution

# Construct both archives before replacing named release files. A failed run
# cannot leave a new, incomplete archive looking like a distributable release.
with tempfile.TemporaryDirectory(prefix="seeend-archive-", dir=dist) as work_dir:
    work = Path(work_dir)
    guide = work / "SeeEnd-browser-extension-install.html"
    guide.write_text(offline_html(), encoding="utf-8")
    documents = [guide, dist / RECEIPT,
                 *(root / filename for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md", "LIBRARY_REPLACEMENT.md"))]
    if sys.platform == "darwin":
        app_archive = work / (name + ".zip")
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(dist / "SeeEnd.app"), str(app_archive)], check=True)
    elif sys.platform == "win32":
        app_archive = Path(shutil.make_archive(str(work / name), "zip", root_dir=dist, base_dir="SeeEnd"))
    else:
        app_archive = work / (name + ".tar.gz")
        with tarfile.open(app_archive, "w:gz") as archive:
            archive.add(dist / "SeeEnd", arcname="SeeEnd")
            for document in documents:
                archive.add(document, arcname=document.name)
    if sys.platform in ("darwin", "win32"):
        with zipfile.ZipFile(app_archive, "a", zipfile.ZIP_DEFLATED) as archive:
            for document in documents:
                archive.write(document, document.name)

    source_archive = work / f"SeeEnd-third-party-sources-{version}.zip"
    with zipfile.ZipFile(source_archive, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sources:
            archive.write(item, "third-party-sources/" + item.relative_to(materials).as_posix())
        archive.write(root / "THIRD_PARTY_NOTICES.md", "third-party-sources/README.md")
        archive.write(root / "LIBRARY_REPLACEMENT.md", "third-party-sources/LIBRARY_REPLACEMENT.md")
        archive.write(dist / RECEIPT, "third-party-sources/" + RECEIPT)
        for item in license_files:
            archive.write(item, "third-party-sources/licenses/" + item.relative_to(notices).as_posix())
    app_archive.replace(dist / app_archive.name)
    source_archive.replace(dist / source_archive.name)
    shutil.copy2(guide, dist / guide.name)

print("Archive:", name)
print("Build revision:", identity["git_revision"], "source SHA256:", identity["source_sha256"])
print("Optional third-party sources (not required to run SeeEnd):", source_archive.name)
