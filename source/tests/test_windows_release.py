"""Release staging must bind unchanged package bytes to the current CI evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source/Windows"))
from version import VERSION

spec = importlib.util.spec_from_file_location("windows_release", ROOT / ".github/scripts/prepare-windows-release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class WindowsReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.dist, self.reports, self.output = root / "dist", root / "reports", root / "assets"
        self.dist.mkdir()
        self.reports.mkdir()
        self.archive = self.dist / "TGFocus-Windows-x64.zip"
        self.archive.write_bytes(b"synthetic package bytes; never executed")
        self.digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.archive.with_suffix(".zip.sha256").write_text(f"{self.digest}  {self.archive.name}\n")
        self.report = dict(status="no_threats_reported", scanExitCode=0, signatureUpdate="successful",
                           archiveSha256=self.digest, sourceRun="1234", fileCount=2)
        self.inventory = [dict(path="TGFocus-Windows-x64\\TGFocus.exe", sha256="0" * 64),
                          dict(path="TGFocus-Windows-x64\\helper\\focus-worker.exe", sha256="1" * 64)]
        self.write_reports()

    def write_reports(self):
        (self.reports / "report.json").write_text(json.dumps(self.report), encoding="utf-8-sig")
        (self.reports / "files.json").write_text(json.dumps(self.inventory), encoding="utf-8-sig")

    def prepare(self, **changes):
        args = dict(tag=f"windows-v{VERSION}", commit="a" * 40,
                    run_url="https://github.com/MXLev/tgfocus/actions/runs/1234")
        args.update(changes)
        return release.prepare(self.dist, self.reports, self.output, **args)

    def test_staging_preserves_scanned_bytes_and_describes_unsigned_provenance(self):
        self.prepare()
        filename = f"TGFocus-Windows-v{VERSION}-x64.zip"
        self.assertEqual((self.output / filename).read_bytes(), self.archive.read_bytes())
        self.assertEqual((self.output / f"{filename}.sha256").read_text(), f"{self.digest}  {filename}\n")
        provenance = json.loads((self.output / f"TGFocus-Windows-v{VERSION}-provenance.json").read_text())
        self.assertEqual(provenance["signingStatus"], "unsigned")
        self.assertEqual(provenance["sourceCommit"], "a" * 40)
        self.assertIn("1234", provenance["buildRun"])

    def test_mismatched_tag_or_untrusted_provenance_is_rejected(self):
        for changes in [dict(tag="windows-v9.9.9"), dict(commit="short"),
                        dict(run_url="https://example.com/actions/runs/1234")]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(**changes)
        self.assertFalse(self.output.exists())

    def test_changed_archive_is_rejected(self):
        self.archive.write_bytes(b"different bytes")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_failed_stale_or_other_artifact_scan_is_rejected(self):
        for changes in [dict(status="scanner_unavailable"), dict(scanExitCode=2),
                        dict(signatureUpdate="incomplete"), dict(archiveSha256="0" * 64),
                        dict(sourceRun="9999")]:
            original = self.report.copy()
            self.report.update(changes)
            self.write_reports()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare()
            self.report = original
        self.assertFalse(self.output.exists())

    def test_missing_worker_or_wrong_manifest_count_is_rejected(self):
        self.inventory.pop()
        self.write_reports()
        with self.assertRaises(ValueError):
            self.prepare()
        self.report["fileCount"] = 1
        self.write_reports()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_existing_staged_assets_are_never_replaced(self):
        self.output.mkdir()
        marker = self.output / "existing.zip"
        marker.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            self.prepare()
        self.assertEqual(marker.read_bytes(), b"preserve")


if __name__ == "__main__":
    unittest.main()
