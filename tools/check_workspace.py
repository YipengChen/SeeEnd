#!/usr/bin/env python3
"""Check the SeeEnd monorepo offline without starting services or reading user config.

Defaults to source versions, licenses, policies and the actual App/Skill contract.
Use --release for legacy published/prepared metadata and artifact verification.
--skip-artifacts is only meaningful with --release.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tomllib
import zipfile


ASSET_ROLES = {"app", "third-party-sources", "extension", "store-extension"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version(value, label):
    require(isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value),
            f"{label}: expected a three-part version, got {value!r}")
    return value


def read_json(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def app_version(code):
    metadata = tomllib.loads((code / "pyproject.toml").read_text(encoding="utf-8"))
    project = version(metadata["project"]["version"], "pyproject version")
    tree = ast.parse((code / "aiflame/__init__.py").read_text(encoding="utf-8"))
    values = []
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            values.append(ast.literal_eval(node.value))
    require(values == [project], f"pyproject version {project!r} != aiflame.__version__ {values!r}")
    return project


def extension_version(code):
    root = code / "aiflame/integrations/chatgpt_web/extension"
    manifest = version(read_json(root / "manifest.json")["version"], "extension manifest version")
    provider = re.findall(r"\bconst\s+VERSION\s*=\s*(['\"])(\d+\.\d+\.\d+)\1\s*;",
                          (root / "providers.js").read_text(encoding="utf-8"))
    require([value for _, value in provider] == [manifest],
            f"extension manifest {manifest!r} != providers VERSION {provider!r}")
    return manifest


def skill_version(skill):
    lines = (skill / "seeend/SKILL.md").read_text(encoding="utf-8").splitlines()
    require(lines and lines[0] == "---", "SKILL.md: missing frontmatter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ValueError("SKILL.md: unterminated frontmatter") from None
    values = []
    in_metadata = False
    for line in lines[1:end]:
        if line and not line[0].isspace():
            in_metadata = line.strip() == "metadata:"
        elif in_metadata:
            match = re.fullmatch(r"\s+version:\s*(.*?)\s*", line)
            if match:
                value = match[1]
                values.append(ast.literal_eval(value) if value.startswith(("'", '"')) else value)
    require(len(values) == 1, f"Skill metadata.version must occur once, got {values!r}")
    return version(values[0], "Skill metadata.version")


def identical(left, right):
    require(left.read_bytes() == right.read_bytes(), f"different bytes: {left} / {right}")


def actual_contract(code, skill):
    # An isolated interpreter prevents an already-imported or installed App from
    # masking the selected checkout. -B prevents bytecode writes in either repo.
    program = """
import importlib.util
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from aiflame.integrations.registry import integration_contract
contract = integration_contract()
sys.path.insert(0, str(Path(sys.argv[2]) / 'seeend/scripts'))
spec = importlib.util.spec_from_file_location('seeend_workspace_client', Path(sys.argv[2]) / 'seeend/scripts/seeend_client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
client.contract_summary(contract, contract['app_version'])
print(json.dumps(contract))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(code), str(skill)],
                            cwd=code, capture_output=True, text=True, timeout=15, check=False)
    require(result.returncode == 0,
            "actual App import / Skill contract validation failed: " + (result.stderr.strip() or result.stdout.strip()))
    actual = json.loads(result.stdout)
    expected = read_json(skill / "tests/fixtures/integration-contract.json")
    for field in ("contract_version", "app_name", "schema_version"):
        require(type(actual.get(field)) is type(expected.get(field)) and actual.get(field) == expected.get(field),
                f"contract fixture mismatch: {field}")
    for field in ("required_fields", "optional_fields", "states", "reasons"):
        require(set(actual[field]) == set(expected[field]), f"contract fixture mismatch: {field}")
    capabilities = actual["capabilities"]
    for field, value in expected["capabilities"].items():
        got = capabilities.get(field)
        match = set(got) == set(value) if field == "native_sources" and isinstance(got, list) else type(got) is type(value) and got == value
        require(match, f"contract fixture mismatch: capabilities.{field}")
    sources = {source["id"]: source for source in actual["sources"]}
    for source in expected["sources"]:
        require(source["id"] in sources, f"missing fixture source: {source['id']}")
        for field, value in source.items():
            got = sources[source["id"]].get(field)
            require(type(got) is type(value) and got == value,
                    f"contract fixture mismatch: sources.{source['id']}.{field}")
    return actual


def local_path(base, value):
    require(isinstance(value, str) and value and not Path(value).is_absolute()
            and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value) and "\\" not in value,
            f"expected a relative local path, got {value!r}")
    return (base / value).resolve()


def current_identity(code):
    program = """
import json
import sys
sys.path.insert(0, sys.argv[1] + '/packaging')
from release import source_identity
print(json.dumps(source_identity(sys.argv[1])))
"""
    result = subprocess.run([sys.executable, "-I", "-B", "-c", program, str(code)],
                            cwd=code, capture_output=True, text=True, timeout=30, check=False)
    require(result.returncode == 0, "source identity failed: " + result.stderr.strip())
    return json.loads(result.stdout)


def prepared_identity(path, manifest, current):
    # Published history is immutable; only the selected local prepared release
    # claims to describe the current working source.
    if manifest["status"] != "prepared":
        return
    for field in ("app_version", "extension_version", "source_sha256"):
        require(manifest.get(field) == current[field],
                f"prepared release {field} differs from current source; rebuild and refresh the manifest")
    receipt = read_json(local_path(path.parent, manifest.get("build_receipt")))
    receipt_matches(receipt, manifest)


def receipt_matches(receipt, manifest):
    for field in ("app_version", "extension_version", "source_sha256"):
        require(receipt.get(field) == manifest.get(field), f"build receipt {field} != release manifest")


def archived_receipts(target, role, manifest):
    if manifest["status"] != "prepared" or role not in {"app", "third-party-sources"}:
        return
    if role == "third-party-sources":
        names = ["third-party-sources/SeeEnd-build.json"]
    else:
        platform = target.name.split("-")[-2]
        bundled = ("SeeEnd.app/Contents/Resources/SeeEnd-build.json" if platform == "Darwin"
                   else "SeeEnd/_internal/SeeEnd-build.json")
        names = ["SeeEnd-build.json", bundled]
    if target.suffix == ".zip":
        with zipfile.ZipFile(target) as archive:
            receipts = [json.loads(archive.read(name)) for name in names]
    else:
        with tarfile.open(target) as archive:
            receipts = [json.load(archive.extractfile(name)) for name in names]
    for receipt in receipts:
        receipt_matches(receipt, manifest)
    require(all(receipt == receipts[0] for receipt in receipts), "archive build receipts differ")


def release_metadata(downloads, app, extension, skill, contract):
    status = read_json(downloads / "release-status.json")
    published, prepared = status["published"], status.get("prepared")
    require(isinstance(published, dict) and (prepared is None or isinstance(prepared, dict)),
            "invalid published/prepared records")
    published_version = version(published["app_version"], "published app_version")
    record, state = (prepared, "prepared") if prepared is not None else (published, "published")
    require(record["app_version"] == app, f"release-status {state} app_version != current App")
    path = local_path(downloads, record["manifest"])
    require(path.is_relative_to(downloads), "release manifest must be within the downloads checkout")
    manifest = read_json(path)
    require(manifest.get("status") == state, f"release manifest status must be {state!r}")
    if state == "published":
        require(manifest.get("external_publication_verified") is True,
                "published release must have verified external publication")
    for field, expected in (("app_version", app), ("extension_version", extension), ("skill_version", skill),
                            ("contract_version", contract["contract_version"]), ("schema_version", contract["schema_version"])):
        require(type(manifest.get(field)) is type(expected) and manifest.get(field) == expected,
                f"release manifest {field} != current checkout")
    require(isinstance(manifest.get("assets"), list), "release manifest must contain an assets list")
    print(f"       published={published_version}; current={app} ({state})")
    return path, manifest


def asset_name(name, role, manifest):
    app, extension = manifest["app_version"], manifest["extension_version"]
    if role == "app":
        valid = re.fullmatch(r"SeeEnd-" + re.escape(app) + r"-[A-Za-z0-9]+-[A-Za-z0-9_-]+\.(?:zip|tar\.gz)", name)
    else:
        expected = {
            "third-party-sources": f"SeeEnd-third-party-sources-{app}.zip",
            "extension": f"SeeEnd-Web-extension-{extension}.zip",
            "store-extension": f"SeeEnd-Chrome-Web-Store-extension-{extension}.zip",
        }
        valid = name == expected.get(role)
    require(valid, f"asset filename does not match {role} version: {name}")


def check_artifacts(path, manifest):
    assets = manifest["assets"]
    require(assets, "release assets list is empty")
    roles, paths = set(), set()
    for asset in assets:
        require(isinstance(asset, dict) and asset.get("role") in ASSET_ROLES, f"invalid asset record: {asset!r}")
        role = asset["role"]
        target = local_path(path.parent, asset.get("path"))
        require(target not in paths, f"duplicate asset path: {target}")
        paths.add(target)
        roles.add(role)
        asset_name(target.name, role, manifest)
        expected = asset.get("sha256")
        require(isinstance(expected, str) and re.fullmatch(r"[a-fA-F0-9]{64}", expected),
                f"invalid SHA-256 for {target.name}")
        require(target.is_file(), f"missing local asset: {target}")
        digest = hashlib.sha256()
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        require(digest.hexdigest() == expected.lower(), f"SHA-256 mismatch: {target}")
        archived_receipts(target, role, manifest)
        print(f"       {role}: {target.name}")
    require(roles == ASSET_ROLES, f"missing release asset roles: {sorted(ASSET_ROLES - roles)}")


class Checks:
    def __init__(self):
        self.passed = self.failed = self.skipped = 0

    def run(self, name, action):
        try:
            value = action()
        except Exception as error:
            self.failed += 1
            print(f"FAIL   {name}: {error}")
            return None
        self.passed += 1
        print(f"PASS   {name}")
        return value

    def skip(self, name, reason):
        self.skipped += 1
        print(f"SKIP   {name}: {reason}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--code", type=Path, default=root)
    parser.add_argument("--skill", type=Path, default=root / "skill")
    parser.add_argument("--website", "--downloads", dest="downloads", type=Path, default=root / "website")
    parser.add_argument("--release", action="store_true", help="validate legacy published/prepared releases and artifacts")
    parser.add_argument("--skip-artifacts", action="store_true", help="explicitly skip asset filenames, local files and SHA-256 checks")
    args = parser.parse_args()
    code, skill, downloads = (getattr(args, name).expanduser().resolve() for name in ("code", "skill", "downloads"))
    checks = Checks()
    app = checks.run("App version: pyproject / AST __version__", lambda: app_version(code))
    extension = checks.run("extension version: manifest / providers", lambda: extension_version(code))
    skill_ver = checks.run("Skill frontmatter version", lambda: skill_version(skill))
    checks.run("Skill repository / package LICENSE bytes", lambda: identical(skill / "LICENSE", skill / "seeend/LICENSE"))
    checks.run("App / Skill GPL license bytes", lambda: identical(code / "LICENSE", skill / "LICENSE"))
    for name in ("index.html", "support.html"):
        checks.run("canonical website " + name, lambda name=name: require((downloads / name).is_file(), "missing website file: " + name))
    contract = checks.run("actual App contract / actual Skill validator / fixture", lambda: actual_contract(code, skill))
    if contract is not None and app is not None:
        checks.run("imported contract App version", lambda: require(contract["app_version"] == app, "registry version != AST/pyproject version"))
    if not args.release:
        print(f"\nResult: {checks.passed} PASS, {checks.failed} FAIL; source checks only (no release claim)")
        return 1 if checks.failed else 0
    release = None
    if all(value is not None for value in (app, extension, skill_ver, contract)):
        release = checks.run("release metadata", lambda: release_metadata(downloads, app, extension, skill_ver, contract))
    else:
        checks.skip("release metadata", "version or contract prerequisites failed")
    if release is not None:
        checks.run("prepared source / build receipt identity",
                   lambda: prepared_identity(*release, current_identity(code)))
    if args.skip_artifacts:
        checks.skip("artifact filenames / local files / SHA-256", "--skip-artifacts requested; assets are NOT verified")
    elif release is not None:
        checks.run("artifact filenames / local files / SHA-256", lambda: check_artifacts(*release))
    else:
        checks.skip("artifacts", "release metadata failed")
    print(f"\nResult: {checks.passed} PASS, {checks.failed} FAIL, {checks.skipped} SKIP")
    return 1 if checks.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
