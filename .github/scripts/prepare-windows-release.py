"""Validate CI provenance and stage unchanged, versioned unsigned release assets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "source/Windows"))
from version import VERSION


def prepare(dist, reports, output, *, tag, commit, run_url):
    if tag != f"windows-v{VERSION}":
        raise ValueError("Windows tag must match the product version")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full source commit")
    if not re.fullmatch(r"https://github.com/MXLev/tgfocus/actions/runs/[0-9]+", run_url):
        raise ValueError("Expected this repository's CI run URL")
    archive = dist / "TGFocus-Windows-x64.zip"
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if archive.with_suffix(".zip.sha256").read_text().strip() != f"{digest}  {archive.name}":
        raise ValueError("Archive checksum mismatch")
    report = json.loads((reports / "report.json").read_text(encoding="utf-8-sig"))
    if (report.get("status") != "no_threats_reported" or report.get("scanExitCode") != 0 or
            report.get("signatureUpdate") != "successful" or report.get("archiveSha256") != digest or
            str(report.get("sourceRun")) != run_url.rsplit("/", 1)[1]):
        raise ValueError("Missing or mismatched successful Defender evidence")
    inventory = json.loads((reports / "files.json").read_text(encoding="utf-8-sig"))
    if not isinstance(inventory, list) or len(inventory) != report.get("fileCount"):
        raise ValueError("File manifest does not match the scan report")
    paths = {entry["path"].replace("\\", "/") for entry in inventory}
    if not {"TGFocus-Windows-x64/TGFocus.exe", "TGFocus-Windows-x64/helper/focus-worker.exe"} <= paths:
        raise ValueError("Manifest is missing first-party executables")
    output.mkdir(parents=True, exist_ok=False)  # Never silently replace an existing staged release.
    prefix = f"TGFocus-Windows-v{VERSION}"
    filename = f"{prefix}-x64.zip"
    shutil.copyfile(archive, output / filename)  # Preserve exactly the scanned ZIP bytes.
    (output / f"{filename}.sha256").write_text(f"{digest}  {filename}\n", encoding="ascii")
    shutil.copyfile(reports / "report.json", output / f"{prefix}-security-report.json")
    shutil.copyfile(reports / "files.json", output / f"{prefix}-manifest.json")
    provenance = dict(version=VERSION, tag=tag, sourceCommit=commit, buildRun=run_url,
                      archiveSha256=digest, signingStatus="unsigned", channel="prerelease")
    (output / f"{prefix}-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    body = (ROOT / "WINDOWS-RELEASE-NOTES.md").read_text()
    body += (f"\n\nИсходный коммит: `{commit}`. [Сборка и проверки]({run_url}).\n\n"
             f"SHA-256 `{filename}`: `{digest}`.\n\n"
             "Статус подписи: **unsigned**. Версия и метаданные обоих EXE проверены CI.\n")
    (output.parent / "windows-release-body.md").write_text(body, encoding="utf-8")
    return provenance


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--run-url", required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(ROOT / "dist", ROOT / "security-report", ROOT / "release-assets", **vars(args))))
