"""Regression checks for the CI release boundary, using tiny local fixtures."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging"))
import release_assets as assets


class ReleaseAssetTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        skill = self.root / "skill/seeend"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text('---\nmetadata:\n  version: "1.0.6"\n---\n')
        self.identity = {"app_version": "0.8.69", "extension_version": "0.2.14",
                         "git_revision": "a" * 40, "git_dirty": False, "source_sha256": "b" * 64}
        self.identity_mock = patch.object(assets, "source_identity", return_value=self.identity)
        self.identity_mock.start()
        self.addCleanup(self.identity_mock.stop)

    def fixture(self, kind, system="Darwin", architecture="arm64"):
        dist = self.root / (kind + system + architecture)
        dist.mkdir()
        identity = {**self.identity, "platform": system, "architecture": architecture}
        names = (assets.desktop_names(identity) if kind == "desktop" else
                 assets.extension_names(identity) if kind == "extension" else assets.source_names(self.root, identity))
        for name in names[:2]:
            (dist / name).write_bytes(name.encode())
        if kind != "desktop":
            identity["packages_sha256"] = {name: assets.sha256_file(dist / name) for name in names[:2]}
        (dist / names[2]).write_text(json.dumps(identity))
        if kind == "desktop":
            (dist / "SeeEnd-build.json").write_text(json.dumps(identity))
        (dist / "do-not-release").mkdir()
        destination = dist / "staged"
        assets.stage(self.root, dist, destination, kind)
        self.assertNotIn("do-not-release", {p.name for p in destination.iterdir()})
        return destination, names

    def aggregate(self):
        destination = self.root / "release"
        destination.mkdir()
        for system, architecture in assets.DESKTOPS:
            stage, _ = self.fixture("desktop", system, architecture)
            for path in stage.iterdir():
                shutil.copy2(path, destination / path.name)
        for kind in ("extension", "source"):
            stage, _ = self.fixture(kind)
            for path in stage.iterdir():
                shutil.copy2(path, destination / path.name)
        return destination

    def test_requires_matching_tag_and_all_four_platforms_source_and_skill(self):
        directory = self.aggregate()
        names = assets.finalize(self.root, directory, "v0.8.69")
        self.assertEqual(len(names), 18)
        self.assertEqual(len((directory / "SHA256SUMS").read_text().splitlines()), 18)
        self.assertIn("SeeEnd-source-0.8.69.zip", names)
        self.assertIn("SeeEnd-skill-1.0.6.zip", names)
        with self.assertRaisesRegex(RuntimeError, "must equal"):
            assets.finalize(self.root, directory, "v0.8.68")
        (directory / "SeeEnd-0.8.69-Darwin-x86_64.zip").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing regular"):
            assets.finalize(self.root, directory, "v0.8.69")

    def test_rejects_tampered_archives_unlisted_directories_and_duplicate_entries(self):
        directory, names = self.fixture("extension")
        package = directory / names[0]
        original = package.read_bytes()
        package.write_bytes(b"corrupt download")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            assets.verify_checksums(directory)
        package.write_bytes(original)
        (directory / "unpacked-extension").mkdir()
        with self.assertRaisesRegex(RuntimeError, "Unexpected files"):
            assets.verify_checksums(directory)
        (directory / "unpacked-extension").rmdir()
        shutil.copy2(directory / "SHA256SUMS-extension.txt", directory / "SHA256SUMS-duplicate.txt")
        with self.assertRaisesRegex(RuntimeError, "Duplicate release asset"):
            assets.verify_checksums(directory)

    def test_rejects_mismatched_or_dirty_receipts_and_unsafe_checksum_paths(self):
        directory = self.aggregate()
        receipt = directory / "SeeEnd-0.8.69-Darwin-arm64-build.json"
        identity = json.loads(receipt.read_text())
        identity["git_revision"] = "c" * 40
        receipt.write_text(json.dumps(identity))
        names = assets.desktop_names(identity)
        assets.write_checksums(directory, names, "SHA256SUMS-Darwin-arm64.txt")
        with self.assertRaisesRegex(RuntimeError, "mismatched git_revision"):
            assets.finalize(self.root, directory, "v0.8.69")
        identity.update(git_revision=self.identity["git_revision"], git_dirty=True)
        receipt.write_text(json.dumps(identity))
        with self.assertRaisesRegex(RuntimeError, "clean Git checkout"):
            assets.read_identity(receipt, self.identity)
        assets.write_checksums(directory, names, "SHA256SUMS-Darwin-arm64.txt")
        (directory / "SHA256SUMS-source.txt").write_text("0" * 64 + "  ../outside.zip\n")
        with self.assertRaisesRegex(RuntimeError, "Invalid checksum"):
            assets.verify_checksums(directory)

    def test_source_archives_use_the_exact_commit_and_include_standalone_skill_license(self):
        (self.root / "LICENSE").write_text("GPL fixture")
        (self.root / "skill/LICENSE").write_text("GPL fixture")
        (self.root / ".gitignore").write_text("dist/\n")
        for arguments in (["init", "-q"], ["add", "."],
                          ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                           "commit", "-qm", "fixture"]):
            subprocess.run(["git", *arguments], cwd=self.root, check=True, capture_output=True)
        dist = self.root / "dist"
        assets.source_archives(self.root, dist, dist / "staged")
        with zipfile.ZipFile(dist / "SeeEnd-source-0.8.69.zip") as archive:
            self.assertIn("SeeEnd-source-0.8.69/LICENSE", archive.namelist())
            self.assertIn("SeeEnd-source-0.8.69/skill/seeend/SKILL.md", archive.namelist())
        with zipfile.ZipFile(dist / "SeeEnd-skill-1.0.6.zip") as archive:
            self.assertIn("SeeEnd-skill-1.0.6/LICENSE", archive.namelist())
            self.assertIn("SeeEnd-skill-1.0.6/seeend/SKILL.md", archive.namelist())


if __name__ == "__main__":
    unittest.main()
