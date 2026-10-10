"""Stage versioned browser packages without replacing historical store uploads."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

from release import source_identity, write_receipt


def zip_entry(archive, name, data):
    # Fixed timestamps and permissions make identical input bytes reproducible.
    entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = 0o100644 << 16
    archive.writestr(entry, data)


def stage_extension(source, destination, identity):
    source, destination = Path(source), Path(destination)
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    version = manifest["version"]
    provider = (source / "providers.js").read_text(encoding="utf-8")
    if re.search(r"const VERSION = ['\"]" + re.escape(version) + r"['\"]", provider) is None:
        raise RuntimeError("Browser provider and manifest versions do not match")
    directory = f"SeeEnd-Web-extension-{version}"
    names = [path.name for path in sorted(source.iterdir()) if path.is_file() and not path.is_symlink()
             and path.suffix in {".js", ".html", ".json", ".png", ".md"}]
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="seeend-extension-", dir=destination) as work_dir:
        work = Path(work_dir)
        staged = work / directory
        staged.mkdir()
        for name in names:
            shutil.copy2(source / name, staged / name)
        development = work / (directory + ".zip")
        store = work / f"SeeEnd-Chrome-Web-Store-extension-{version}.zip"
        store_manifest = {key:value for key,value in manifest.items() if key != "key"}
        with zipfile.ZipFile(development, "w") as archive:
            for name in names:
                zip_entry(archive, directory + "/" + name, (source / name).read_bytes())
        with zipfile.ZipFile(store, "w") as archive:
            for name in names:
                if name == "README.md":
                    continue
                data = (json.dumps(store_manifest, ensure_ascii=False, indent=2) + "\n").encode() if name == "manifest.json" else (source / name).read_bytes()
                zip_entry(archive, name, data)
        receipt = work / f"SeeEnd-extension-{version}.json"
        write_receipt(receipt, {
            **identity,
            "extension_version": version,
            "files_sha256": {name:hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names},
            "packages_sha256": {path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in (development, store)},
        })
        # Only this version's staging directory can be replaced. In particular,
        # dist/SeeEnd-Web-extension and docs/chrome-web-store stay untouched.
        target = destination / directory
        if target.exists():
            shutil.rmtree(target)
        staged.replace(target)
        for path in (development, store, receipt):
            path.replace(destination / path.name)
    return destination / directory, destination / development.name, destination / store.name


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=root / "dist")
    args = parser.parse_args()
    source = root / "aiflame/integrations/chatgpt_web/extension"
    for path in stage_extension(source, args.dist_dir.resolve(), source_identity(root)):
        print(path)


if __name__ == "__main__":
    main()
