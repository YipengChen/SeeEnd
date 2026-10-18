"""Stage and verify the exact CI assets before creating a draft GitHub Release."""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess

from release import sha256_file, source_identity


DESKTOPS = (("Darwin", "arm64"),
            ("Windows", "AMD64"), ("Linux", "x86_64"))
IDENTITY_FIELDS = ("app_version", "extension_version", "git_revision", "source_sha256")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def check_tag(root, tag):
    version = source_identity(root)["app_version"]
    require(tag == "v" + version, f"Release tag {tag!r} must equal v{version}")


def desktop_names(identity):
    suffix = f"{identity['app_version']}-{identity['platform']}-{identity['architecture']}"
    extension = ".tar.gz" if identity["platform"] == "Linux" else ".zip"
    return (f"SeeEnd-{suffix}{extension}", f"SeeEnd-third-party-sources-{suffix}.zip",
            f"SeeEnd-{suffix}-build.json")


def extension_names(identity):
    version = identity["extension_version"]
    return (f"SeeEnd-Web-extension-{version}.zip", f"SeeEnd-Chrome-Web-Store-extension-{version}.zip",
            f"SeeEnd-extension-{version}.json")


def source_names(root, identity):
    skill = (Path(root) / "skill/seeend/SKILL.md").read_text(encoding="utf-8")
    versions = re.findall(r"^  version: ['\"]?(\d+\.\d+\.\d+)['\"]?\s*$", skill, re.MULTILINE)
    require(len(versions) == 1, "Skill metadata must declare exactly one version")
    return (f"SeeEnd-source-{identity['app_version']}.zip", f"SeeEnd-skill-{versions[0]}.zip",
            f"SeeEnd-source-{identity['app_version']}.json")


def source_archives(root, dist, destination):
    identity = source_identity(root)
    require(identity.get("git_dirty") is False, "Source archives require a clean Git checkout")
    names = source_names(root, identity)
    dist.mkdir(parents=True, exist_ok=True)
    for name, tree in ((names[0], "HEAD"), (names[1], "HEAD:skill")):
        subprocess.run(["git", "archive", "--format=zip", "--prefix=" + name[:-4] + "/",
                        "--output=" + str((dist / name).resolve()), tree], cwd=root, check=True)
    (dist / names[2]).write_text(json.dumps({**identity, "packages_sha256": {
        name: sha256_file(dist / name) for name in names[:2]}}, indent=2) + "\n", encoding="utf-8")
    stage(root, dist, destination, "source")


def read_identity(path, expected):
    require(path.is_file() and not path.is_symlink(), f"Missing regular receipt: {path}")
    identity = json.loads(path.read_text(encoding="utf-8"))
    for field in IDENTITY_FIELDS:
        require(identity.get(field) == expected.get(field), f"Receipt {path.name}: mismatched {field}")
    require(identity.get("git_dirty") is False, f"Receipt {path.name}: release source must be a clean Git checkout")
    return identity


def write_checksums(directory, names, filename):
    lines = []
    for name in sorted(names):
        path = directory / name
        require(path.is_file() and not path.is_symlink(), f"Missing regular release asset: {name}")
        lines.append(f"{sha256_file(path)}  {name}\n")
    (directory / filename).write_text("".join(lines), encoding="utf-8")


def stage(root, dist, destination, kind):
    expected = source_identity(root)
    require(not destination.exists() or not any(destination.iterdir()), "Staging directory must be empty")
    if kind == "desktop":
        identity = read_identity(dist / "SeeEnd-build.json", expected)
        require((identity["platform"], identity["architecture"]) in DESKTOPS,
                "Unexpected desktop platform or architecture")
        names = desktop_names(identity)
        require(json.loads((dist / names[2]).read_text(encoding="utf-8")) == identity,
                "Platform receipt differs from the verified build receipt")
        checksum = f"SHA256SUMS-{identity['platform']}-{identity['architecture']}.txt"
    else:
        names = extension_names(expected) if kind == "extension" else source_names(root, expected)
        identity = read_identity(dist / names[2], expected)
        for name in names[:2]:
            require(identity.get("packages_sha256", {}).get(name) == sha256_file(dist / name),
                    f"Package checksum mismatch: {name}")
        checksum = f"SHA256SUMS-{kind}.txt"
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = dist / name
        require(path.is_file() and not path.is_symlink(), f"Missing regular release asset: {name}")
        shutil.copy2(path, destination / name)
    write_checksums(destination, names, checksum)
    verify_checksums(destination)


def verify_checksums(directory):
    manifests = sorted(directory.glob("SHA256SUMS-*.txt"))
    require(bool(manifests), "No per-job SHA256SUMS manifests found")
    listed = set()
    for manifest in manifests:
        require(not manifest.is_symlink(), "Checksum manifests must not be symlinks")
        lines = manifest.read_text(encoding="utf-8").splitlines()
        require(bool(lines), f"Empty checksum manifest: {manifest.name}")
        for line in lines:
            match = re.fullmatch(r"([0-9a-f]{64})  (SeeEnd-[A-Za-z0-9_.-]+)", line)
            require(match is not None, f"Invalid checksum entry in {manifest.name}")
            digest, name = match.groups()
            require(name not in listed, f"Duplicate release asset: {name}")
            path = directory / name
            require(path.is_file() and not path.is_symlink(), f"Missing regular release asset: {name}")
            require(sha256_file(path) == digest, f"Release asset checksum mismatch: {name}")
            listed.add(name)
    actual = {path.name for path in directory.iterdir() if path.name != "SHA256SUMS"}
    require(actual == listed | {path.name for path in manifests}, "Unexpected files or directories among release assets")
    return listed


def finalize(root, directory, tag):
    check_tag(root, tag)
    expected = source_identity(root)
    listed = verify_checksums(directory)
    names = set(extension_names(expected)) | set(source_names(root, expected))
    for system, architecture in DESKTOPS:
        identity = {**expected, "platform": system, "architecture": architecture}
        desktop = desktop_names(identity)
        receipt = read_identity(directory / desktop[2], expected)
        require((receipt.get("platform"), receipt.get("architecture")) == (system, architecture),
                f"Incorrect platform receipt: {desktop[2]}")
        names.update(desktop)
    for package_names in (extension_names(expected), source_names(root, expected)):
        receipt = read_identity(directory / package_names[2], expected)
        for name in package_names[:2]:
            require(receipt.get("packages_sha256", {}).get(name) == sha256_file(directory / name),
                    f"Package checksum mismatch: {name}")
    require(listed == names, "Release requires all three desktop builds, extensions, application source and Skill")
    write_checksums(directory, names, "SHA256SUMS")
    return names


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    tag = subparsers.add_parser("check-tag")
    tag.add_argument("tag")
    staging = subparsers.add_parser("stage")
    staging.add_argument("--kind", choices=("desktop", "extension"), required=True)
    staging.add_argument("--dist-dir", type=Path, default=root / "dist")
    staging.add_argument("--output-dir", type=Path, default=root / "release-assets")
    sources = subparsers.add_parser("source")
    sources.add_argument("--dist-dir", type=Path, default=root / "dist")
    sources.add_argument("--output-dir", type=Path, default=root / "dist/source-assets")
    final = subparsers.add_parser("finalize")
    final.add_argument("--assets-dir", type=Path, required=True)
    final.add_argument("--tag", required=True)
    args = parser.parse_args()
    if args.command == "check-tag":
        check_tag(root, args.tag)
    elif args.command == "stage":
        stage(root, args.dist_dir, args.output_dir, args.kind)
    elif args.command == "source":
        source_archives(root, args.dist_dir, args.output_dir)
    else:
        finalize(root, args.assets_dir, args.tag)
    print("Release asset checks passed.")


if __name__ == "__main__":
    main()
