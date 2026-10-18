"""Replace only a verified draft's supporting assets, preserving its installers."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging"))
from release import sha256_file
from release_assets import require


def gh(*arguments):
    return subprocess.check_output(["gh", *arguments], text=True)


def manifest(directory):
    result = {}
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (SeeEnd-[A-Za-z0-9_.-]+)", line)
        require(match is not None, "Invalid release checksum entry")
        digest, name = match.groups()
        require(name not in result, "Duplicate release checksum entry")
        require(sha256_file(directory / name) == digest, f"Checksum mismatch: {name}")
        result[name] = "sha256:" + digest
    result["SHA256SUMS"] = "sha256:" + sha256_file(directory / "SHA256SUMS")
    return result


def release(tag):
    value = json.loads(gh("release", "view", tag, "--json", "isDraft,tagName,assets,body,url"))
    require(value["isDraft"] and value["tagName"] == tag, "Only the matching draft release may be changed")
    return value, {asset["name"]: asset for asset in value["assets"]}


def update(tag, original, public):
    old, new = manifest(original), manifest(public)
    require(len(old) == 16 and len(new) == 5, "Unexpected release asset set")
    installers = set(new) - {"SHA256SUMS"} - {name for name in new if "-developer-materials-" in name}
    require(len(installers) == 3 and installers <= old.keys(), "Missing original installers")
    value, remote = release(tag)
    require(installers <= remote.keys(), "Original installers must remain available")
    require(set(remote) <= old.keys() | new.keys(), "Unexpected remote assets; refusing cleanup")
    for name, asset in remote.items():
        require(asset["state"] == "uploaded" and asset.get("digest") in {old.get(name), new.get(name)},
                f"Remote asset differs from verified CI material: {name}")
    # Installers stay on GitHub unchanged. Upload and verify replacements before deleting anything.
    for name in sorted(set(new) - installers):
        if name not in remote or remote[name].get("digest") != new[name]:
            gh("release", "upload", tag, str(public / name), "--clobber")
    value, remote = release(tag)
    for name, digest in new.items():
        require(name in remote and remote[name].get("digest") == digest,
                f"New public download failed verification: {name}")
    labels = (("Darwin-arm64.zip", "macOS（Apple 芯片）"),
              ("Windows-AMD64.zip", "Windows x64"), ("Linux-x86_64.tar.gz", "Linux x64"))
    # GitHub changes temporary draft URLs when notes are edited; use filenames that
    # remain valid in both the draft and the eventual published release.
    lines = ["<!-- seeend-downloads:start -->", "## 下载", "", "在页面下方 Assets 中选择对应系统的安装包：", ""]
    for suffix, label in labels:
        name = next(name for name in installers if name.endswith(suffix))
        lines.append(f"- **{label}**：`{name}`")
    bundle = next(name for name in new if "-developer-materials-" in name)
    lines.extend(["", f"开发资料：`{bundle}`；文件校验：`SHA256SUMS`。",
                  "", "开发资料包包含完整项目源码、Skill、浏览器扩展、三平台第三方依赖源码和构建记录。",
                  "<!-- seeend-downloads:end -->"])
    previous = re.sub(r"<!-- seeend-downloads:start -->.*?<!-- seeend-downloads:end -->\s*", "",
                      value.get("body") or "", flags=re.DOTALL).strip()
    notes = "\n".join(lines) + ("\n\n" + previous if previous else "") + "\n"
    with tempfile.TemporaryDirectory(prefix="seeend-release-notes-") as directory:
        path = Path(directory) / "notes.md"
        path.write_text(notes, encoding="utf-8")
        gh("release", "edit", tag, "--notes-file", str(path))
    for name in sorted(set(remote) - new.keys()):
        gh("release", "delete-asset", tag, name, "--yes")
    _, final = release(tag)
    require(set(final) == new.keys(), "Draft asset cleanup did not finish")
    require(all(final[name].get("digest") == digest for name, digest in new.items()),
            "Final draft checksums differ")
    print(f"Verified compact draft: {value['url']} (5 uploaded assets; installers unchanged)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--public-dir", type=Path, required=True)
    arguments = parser.parse_args()
    update(arguments.tag, arguments.assets_dir, arguments.public_dir)
