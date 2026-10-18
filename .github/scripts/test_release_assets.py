"""Regression checks for the CI release boundary, using tiny local fixtures."""

import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "packaging"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_assets as assets
import release as build_release
import update_compact_draft as draft


class SourceIdentityOrderTests(unittest.TestCase):
    def test_source_fingerprint_uses_the_same_case_sensitive_order_on_windows(self):
        # Insertion order is the independently specified POSIX lexical order.
        # Upper/lowercase names exist on every OS; no case-only duplicates are needed.
        files = {
            "LICENSE": "GPL fixture\n",
            "aiflame/Zebra.py": "UPPER = True\n",
            "aiflame/apple.py": "lower = True\n",
            "aiflame/integrations/chatgpt_web/extension/manifest.json": '{"version":"0.2.14"}\n',
            "packaging/Zebra.py": "UPPER_BUILD = True\n",
            "packaging/apple.py": "lower_build = True\n",
            "pyproject.toml": '[project]\nversion = "0.8.69"\n',
        }
        expected = "90d26cbc098d4363be33e951866b24674682e3f1be3d7259845c08cfce2c669a"
        legacy_windows = hashlib.sha256()
        for name in sorted(files, key=PureWindowsPath):
            legacy_windows.update(name.encode() + b"\0")
            legacy_windows.update(hashlib.sha256(files[name].encode()).digest())
        self.assertNotEqual(legacy_windows.hexdigest(), expected)

        class WindowsOrderPath(type(Path())):
            # Keep real local I/O while reproducing WindowsPath's old ordering.
            def __lt__(self, other):
                return PureWindowsPath(self.as_posix()) < PureWindowsPath(other.as_posix())

        with tempfile.TemporaryDirectory(prefix="seeend-source-order-") as work:
            root = Path(work)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode())
            self.assertEqual(build_release.source_identity(root)["source_sha256"], expected)
            with patch.object(build_release, "Path", WindowsOrderPath):
                self.assertEqual(build_release.source_identity(root)["source_sha256"], expected)


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

    def test_requires_matching_tag_and_all_supported_platforms_source_and_skill(self):
        directory = self.aggregate()
        names = assets.finalize(self.root, directory, "v0.8.69")
        self.assertEqual(len(names), 15)
        self.assertEqual(len((directory / "SHA256SUMS").read_text().splitlines()), 15)
        self.assertIn("SeeEnd-source-0.8.69.zip", names)
        self.assertIn("SeeEnd-skill-1.0.6.zip", names)
        with self.assertRaisesRegex(RuntimeError, "must equal"):
            assets.finalize(self.root, directory, "v0.8.68")
        (directory / "SeeEnd-0.8.69-Linux-x86_64.tar.gz").unlink()
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

    def test_compact_downloads_preserve_installers_sources_and_original_checksums(self):
        directory = self.aggregate()
        destination = self.root / "public"
        names = assets.present(self.root, directory, destination, "v0.8.69")
        self.assertEqual(len(names), 4)
        self.assertEqual({p.name for p in destination.iterdir()}, names | {"SHA256SUMS"})
        bundle = destination / "SeeEnd-developer-materials-0.8.69.zip"
        with zipfile.ZipFile(bundle) as archive:
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
            originals = assets.finalize(self.root, directory, "v0.8.69")
            installers = names - {bundle.name}
            self.assertEqual(set(archive.namelist()), originals - installers | {"SHA256SUMS", "README.txt"})
            for name in originals - installers | {"SHA256SUMS"}:
                self.assertEqual(archive.read(name), (directory / name).read_bytes())
        for name in installers:
            self.assertEqual((destination / name).read_bytes(), (directory / name).read_bytes())
        for line in (destination / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ")
            self.assertEqual(digest, assets.sha256_file(destination / name))
        repeat = self.root / "repeat"
        assets.present(self.root, directory, repeat, "v0.8.69")
        self.assertEqual(bundle.read_bytes(), (repeat / bundle.name).read_bytes())

    def test_compact_downloads_reject_tampering_and_nonempty_output(self):
        directory = self.aggregate()
        destination = self.root / "public"
        destination.mkdir()
        (destination / "old.zip").write_bytes(b"keep")
        with self.assertRaisesRegex(RuntimeError, "must be empty"):
            assets.present(self.root, directory, destination, "v0.8.69")
        (directory / "SeeEnd-source-0.8.69.zip").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
            assets.present(self.root, directory, self.root / "new", "v0.8.69")

    def remote_fixture(self):
        directory = self.aggregate()
        public = self.root / "public"
        assets.present(self.root, directory, public, "v0.8.69")
        old = draft.manifest(directory)
        value = {"isDraft": True, "tagName": "v0.8.69", "body": "Existing notes", "url": "https://example.invalid/draft"}
        remote = {name: {"name": name, "state": "uploaded", "digest": digest, "url": "https://example.invalid/" + name}
                  for name, digest in old.items()}
        calls = []

        def fake_gh(*arguments):
            calls.append(arguments)
            if arguments[:2] == ("release", "view"):
                return json.dumps({**value, "assets": list(remote.values())})
            if arguments[:2] == ("release", "upload"):
                path = Path(arguments[3])
                remote[path.name] = {"name": path.name, "state": "uploaded", "digest": "sha256:" + assets.sha256_file(path),
                                     "url": "https://example.invalid/" + path.name}
            elif arguments[:2] == ("release", "delete-asset"):
                del remote[arguments[3]]
            elif arguments[:2] == ("release", "edit"):
                value["body"] = Path(arguments[4]).read_text(encoding="utf-8")
            return ""

        return directory, public, value, remote, calls, fake_gh

    def test_draft_update_verifies_replacements_before_cleanup_and_preserves_installers(self):
        directory, public, value, remote, calls, fake = self.remote_fixture()
        originals = {name: item for name, item in remote.items() if name in draft.manifest(public) and name != "SHA256SUMS"}
        with patch.object(draft, "gh", side_effect=fake):
            draft.update("v0.8.69", directory, public)
            first_upload_count = sum(call[:2] == ("release", "upload") for call in calls)
            draft.update("v0.8.69", directory, public)  # Completed updates can safely run again.
        self.assertEqual(first_upload_count, 2)
        self.assertEqual(sum(call[:2] == ("release", "upload") for call in calls), 2)
        self.assertEqual(set(remote), set(draft.manifest(public)))
        self.assertTrue(all(remote[name] is item for name, item in originals.items()))
        self.assertIn("macOS（Apple 芯片）", value["body"])
        self.assertNotIn("https://example.invalid/", value["body"])
        first_delete = next(i for i, call in enumerate(calls) if call[:2] == ("release", "delete-asset"))
        self.assertGreater(first_delete, max(i for i, call in enumerate(calls) if call[:2] == ("release", "upload")))
        self.assertTrue(any(call[:2] == ("release", "view") for call in calls[3:first_delete]))

    def test_draft_update_rejects_published_release_and_mismatched_remote_before_upload(self):
        directory, public, value, remote, calls, fake = self.remote_fixture()
        with patch.object(draft, "gh", side_effect=fake):
            value["isDraft"] = False
            with self.assertRaisesRegex(RuntimeError, "Only the matching draft"):
                draft.update("v0.8.69", directory, public)
            value["isDraft"] = True
            next(iter(remote.values()))["digest"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(RuntimeError, "differs from verified"):
                draft.update("v0.8.69", directory, public)
        self.assertTrue(all(call[:2] == ("release", "view") for call in calls))

    def test_draft_update_never_deletes_when_replacement_verification_fails(self):
        directory, public, value, remote, calls, fake = self.remote_fixture()

        def corrupt_upload(*arguments):
            result = fake(*arguments)
            if arguments[:2] == ("release", "upload"):
                remote[Path(arguments[3]).name]["digest"] = "sha256:" + "0" * 64
            return result

        with patch.object(draft, "gh", side_effect=corrupt_upload):
            with self.assertRaisesRegex(RuntimeError, "failed verification"):
                draft.update("v0.8.69", directory, public)
        self.assertFalse(any(call[:2] == ("release", "delete-asset") for call in calls))

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
