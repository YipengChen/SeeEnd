"""Identify a local build and select only its verified third-party materials."""

import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys
import tomllib


RECEIPT = "SeeEnd-build.json"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_identity(root):
    root = Path(root)
    paths = []
    for directory in ("aiflame", "packaging"):
        paths.extend(path for path in (root / directory).rglob("*")
                     if path.is_file() and not path.is_symlink()
                     and not {"__pycache__", "generated_licenses"}.intersection(path.parts)
                     and path.suffix not in {".pyc", ".pyo", ".so", ".pyd"}
                     and path.name != ".DS_Store")
    for name in ("app.py", "pyproject.toml", "LICENSE", "THIRD_PARTY_NOTICES.md",
                 "LIBRARY_REPLACEMENT.md", "docs/legal/LEGACY_GPL_3_0.txt"):
        path = root / name
        if path.is_file():
            paths.append(path)
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                  capture_output=True, text=True, check=False)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                                capture_output=True, text=True, check=False)
        commit = revision.stdout.strip() if revision.returncode == 0 else None
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    except OSError:
        # An sdist can be built without a Git executable or checkout metadata.
        commit = dirty = None
    extension = root / "aiflame/integrations/chatgpt_web/extension/manifest.json"
    return {
        "format_version": 1,
        "app_version": tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"],
        "extension_version": json.loads(extension.read_text(encoding="utf-8"))["version"],
        "git_revision": commit,
        "git_dirty": dirty,
        "source_sha256": digest.hexdigest(),
        "platform": platform.system(),
        "architecture": platform.machine(),
        "python": platform.python_version(),
    }


def write_receipt(path, identity):
    Path(path).write_text(json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_build(root, dist):
    dist = Path(dist)
    identity = json.loads((dist / RECEIPT).read_text(encoding="utf-8"))
    current = source_identity(root)
    for key in ("app_version", "extension_version", "source_sha256", "platform", "architecture"):
        if identity.get(key) != current[key]:
            raise RuntimeError(f"Build {key} differs from this checkout; rebuild before archiving")
    resources = (dist / "SeeEnd.app/Contents/Resources" if sys.platform == "darwin"
                 else dist / "SeeEnd/_internal")
    bundled = json.loads((resources / RECEIPT).read_text(encoding="utf-8"))
    if bundled != identity:
        raise RuntimeError("Build receipt does not match the application bundle")
    for directory, key in (("third_party_sources", "third_party_sources_sha256"),
                           ("packaging/generated_licenses", "third_party_licenses_sha256")):
        if identity.get(key) != sha256_file(Path(root) / directory / "MANIFEST.json"):
            raise RuntimeError("Third-party materials changed after the application build")
    return identity


def third_party_files(materials):
    """Use the prepared manifest, not every file left in a local cache directory."""
    materials = Path(materials)
    manifest = materials / "MANIFEST.json"
    paths = {manifest}
    for record in json.loads(manifest.read_text(encoding="utf-8")):
        sources = record.get("sources", [])
        if record.get("archive"):
            sources = [*sources, record]
        for source in sources:
            name = source["archive"]
            if Path(name).name != name or name in {".", ".."}:
                raise RuntimeError("Invalid third-party source archive name")
            path = materials / name
            if path.is_symlink() or sha256_file(path) != source["sha256"]:
                raise RuntimeError("Unverified third-party source: " + name)
            paths.add(path)
        if record.get("sources"):
            recipe = materials / (record["name"] + "-" + record["version"] + "-recipe")
            if recipe.is_symlink() or not recipe.is_dir():
                raise RuntimeError("Missing third-party build recipe: " + recipe.name)
            paths.update(path for path in recipe.rglob("*") if path.is_file())
    if any(path.is_symlink() or not path.resolve().is_relative_to(materials.resolve()) for path in paths):
        raise RuntimeError("Third-party materials must not reference files outside their directory")
    return sorted(paths)
