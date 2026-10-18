"""Release identity regressions, using tiny isolated artifacts."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from check_workspace import archived_receipts, prepared_identity


class PreparedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "release.json"
        self.identity = {"app_version": "0.8.66", "extension_version": "0.2.14",
                         "source_sha256": hashlib.sha256(b"current source").hexdigest()}
        self.manifest = {**self.identity, "status": "prepared", "build_receipt": "SeeEnd-build.json"}
        self.receipt = self.root / "SeeEnd-build.json"
        self.receipt.write_text(json.dumps(self.identity))

    def test_current_source_and_receipt_match(self):
        prepared_identity(self.path, self.manifest, self.identity)

    def test_same_version_changed_source_is_rejected(self):
        changed = {**self.identity, "source_sha256": hashlib.sha256(b"later edits").hexdigest()}
        with self.assertRaisesRegex(ValueError, "differs from current source"):
            prepared_identity(self.path, self.manifest, changed)

    def test_stale_or_missing_external_receipt_is_rejected(self):
        self.receipt.write_text(json.dumps({**self.identity, "source_sha256": "0" * 64}))
        with self.assertRaisesRegex(ValueError, "build receipt source_sha256"):
            prepared_identity(self.path, self.manifest, self.identity)
        self.receipt.unlink()
        with self.assertRaises(FileNotFoundError):
            prepared_identity(self.path, self.manifest, self.identity)

    def test_published_history_does_not_claim_current_source(self):
        prepared_identity(self.path, {**self.manifest, "status": "published"},
                          {**self.identity, "source_sha256": "changed"})

    def test_archive_with_current_outer_receipt_but_stale_bundle_is_rejected(self):
        target = self.root / "SeeEnd-0.8.66-Darwin-arm64.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("SeeEnd-build.json", json.dumps(self.identity))
            archive.writestr("SeeEnd.app/Contents/Resources/SeeEnd-build.json",
                             json.dumps({**self.identity, "source_sha256": "0" * 64}))
        with self.assertRaisesRegex(ValueError, "build receipt source_sha256"):
            archived_receipts(target, "app", self.manifest)

    def test_archive_receipts_must_be_identical(self):
        target = self.root / "SeeEnd-0.8.66-Darwin-arm64.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("SeeEnd-build.json", json.dumps(self.identity))
            archive.writestr("SeeEnd.app/Contents/Resources/SeeEnd-build.json",
                             json.dumps({**self.identity, "built_at": "different build"}))
        with self.assertRaisesRegex(ValueError, "archive build receipts differ"):
            archived_receipts(target, "app", self.manifest)

    def test_third_party_archive_receipt_matches_release(self):
        target = self.root / "SeeEnd-third-party-sources-0.8.66.zip"
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("third-party-sources/SeeEnd-build.json", json.dumps(self.identity))
        archived_receipts(target, "third-party-sources", self.manifest)


if __name__ == "__main__":
    unittest.main()
