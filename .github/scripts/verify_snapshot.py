"""CI-only canonical proof for the exact local 0.8.68 source snapshot."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packaging"))
from release import sha256_file, source_identity

EXPECTED = "f845c7dc18f50bcafe95b97da73d9da8971b1c0d2d078ae94e0609498cf18dae"
files = []
for directory in ("aiflame", "packaging"):
    files.extend(path for path in (ROOT / directory).rglob("*")
                 if path.is_file() and not path.is_symlink()
                 and not {"__pycache__", "generated_licenses"}.intersection(path.parts)
                 and path.suffix not in {".pyc", ".pyo", ".so", ".pyd"}
                 and path.name != ".DS_Store")
for name in ("app.py", "pyproject.toml", "LICENSE", "THIRD_PARTY_NOTICES.md",
             "LIBRARY_REPLACEMENT.md", "docs/legal/LEGACY_GPL_3_0.txt"):
    if (ROOT / name).is_file():
        files.append(ROOT / name)
digest = hashlib.sha256()
manifest = []
for path in sorted(files, key=lambda path: path.relative_to(ROOT).as_posix()):
    relative, sha = path.relative_to(ROOT).as_posix(), sha256_file(path)
    digest.update(relative.encode() + b"\0")
    digest.update(bytes.fromhex(sha))
    manifest.append({"path": relative, "sha256": sha})
canonical = digest.hexdigest()
assert canonical == EXPECTED, (canonical, EXPECTED)
identity = source_identity(ROOT)
assert identity["app_version"] == "0.8.68"
output = ROOT / "dist/SeeEnd-Windows-snapshot.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({"canonical_source_sha256": canonical,
                             "identity": identity, "files": manifest}, indent=2) + "\n", encoding="utf-8")
print("Canonical exact snapshot verified:", canonical)
print("Platform receipt fingerprint:", identity["source_sha256"])
print("Verified source files:", len(manifest))
