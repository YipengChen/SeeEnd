"""Archive the native bundle without flattening framework symlinks."""

import platform
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
import zipfile

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Archive SeeEnd for the current OS")
parser.add_argument("--dist-dir", type=Path, default=root / "dist")
args = parser.parse_args()
dist = args.dist_dir.resolve()
version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
name = f"SeeEnd-{version}-{platform.system()}-{platform.machine()}"
# Include an offline, self-contained illustrated installation guide.
sys.path.insert(0, str(root))
from aiflame.integrations.chatgpt_web.guide import offline_html
guide = dist / "SeeEnd-browser-extension-install.html"
guide.write_text(offline_html(), encoding="utf-8")
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
# Ship actual third-party sources alongside the app, without project source.
source_zip = dist / f"SeeEnd-third-party-sources-{version}.zip"
materials = root / "third_party_sources"
if not (materials / "MANIFEST.json").is_file():
    raise RuntimeError("Third-party source materials are not prepared")
with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
    for item in sorted(materials.rglob("*")):
        if item.is_file():
            archive.write(item, "third-party-sources/" + str(item.relative_to(materials)))
    archive.write(root / "THIRD_PARTY_NOTICES.md", "third-party-sources/README.md")
    archive.write(root / "LIBRARY_REPLACEMENT.md", "third-party-sources/LIBRARY_REPLACEMENT.md")
    for item in sorted((root / "packaging/generated_licenses").rglob("*")):
        if item.is_file():
            archive.write(item, "third-party-sources/licenses/" + str(item.relative_to(root / "packaging/generated_licenses")))
if sys.platform in ("darwin", "win32"):
    with zipfile.ZipFile(dist / (name + ".zip"), "a", zipfile.ZIP_DEFLATED) as archive:
        archive.write(source_zip, source_zip.name)
        archive.write(guide, guide.name)
        for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md", "LIBRARY_REPLACEMENT.md"):
            archive.write(root / filename, filename)
else:
    import tarfile
    with tarfile.open(dist / (name + ".tar"), "w") as archive:
        archive.add(dist / "SeeEnd", arcname="SeeEnd")
        archive.add(source_zip, arcname=source_zip.name)
        archive.add(guide, arcname=guide.name)
        for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md", "LIBRARY_REPLACEMENT.md"):
            archive.add(root / filename, arcname=filename)
    with open(dist / (name + ".tar"), "rb") as src:
        import gzip
        with gzip.open(dist / (name + ".tar.gz"), "wb") as out:
            shutil.copyfileobj(src, out)
    (dist / (name + ".tar")).unlink()
print("Archive:", name)
print("Third-party sources:", source_zip.name)
