"""Build a portable x64 Windows tray app and a separate self-contained worker."""
import argparse
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

WINDOWS = Path(__file__).resolve().parent
SOURCE = WINDOWS.parent
ROOT = SOURCE.parent
BUILD = WINDOWS / ".build"


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, cwd=ROOT, env=dict(os.environ, PYTHONUTF8="1"))


def collect_licenses(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "dependency").replace("/", "_").replace("\\", "_")
        target = destination / name
        target.mkdir(exist_ok=True)
        metadata = distribution.read_text("METADATA") or distribution.read_text("PKG-INFO") or name
        (target / "METADATA.txt").write_text(metadata, encoding="utf-8")
        for index, item in enumerate(distribution.files or []):
            if any(marker in item.name.upper() for marker in ("LICENSE", "COPYING", "COPYRIGHT")):
                file = Path(distribution.locate_file(item))
                if file.is_file():
                    # Different bundled components may each call their notice LICENSE.
                    shutil.copy2(file, target / f"{index}-{item.name}")
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"):
        if candidate.is_file():
            shutil.copy2(candidate, destination / "Python-LICENSE.txt")
            break


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, help="Reuse a Windows Python build venv")
    parser.add_argument("--collect-licenses", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.collect_licenses:
        collect_licenses(args.collect_licenses)
        return
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        parser.error("Build this prototype on x64 Windows with Python 3.13 x64; it cannot be cross-compiled on macOS/Linux.")
    if sys.version_info[:2] != (3, 13):
        parser.error("Use Python 3.13 x64 for the pinned Windows build environment.")

    BUILD.mkdir(parents=True, exist_ok=True)
    venv = args.env.resolve() if args.env else BUILD / "venv"
    python = venv / "Scripts/python.exe"
    if not python.is_file():
        run(sys.executable, "-m", "venv", venv)
    run(python, "-c", "import struct, sys; assert sys.version_info[:2] == (3, 13) and struct.calcsize('P') == 8")
    run(python, "-m", "pip", "install", "--disable-pip-version-check", "-r", WINDOWS / "requirements.txt")
    # macOS app replacement tests use fcntl/ps and run in existing Linux/mac CI.
    for pattern in ("test_filter.py", "test_notification_settings.py", "test_ai_filter.py", "test_worker_ipc.py", "test_windows*.py"):
        run(python, "-m", "unittest", "discover", "-s", SOURCE / "tests", "-p", pattern, "-v")

    worker_dist = BUILD / "worker-dist"
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "focus-worker", "--distpath", worker_dist, "--workpath", BUILD / "worker-work",
        "--specpath", BUILD, "--paths", SOURCE / "Helper", SOURCE / "Helper/worker.py")
    worker = worker_dist / "focus-worker"
    run(python, SOURCE / "tests/smoke_worker.py", worker / "focus-worker.exe")

    app_dist = BUILD / "app-dist"
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "TGFocus", "--distpath", app_dist, "--workpath", BUILD / "app-work",
        "--specpath", BUILD, "--paths", WINDOWS,
        "--additional-hooks-dir", WINDOWS / "hooks",
        "--collect-all", "windows_toasts", "--collect-all", "winrt",
        "--hidden-import", "winrt.windows.ui.shell", "--hidden-import", "winrt.windows.ui.notifications",
        "--hidden-import", "winrt.windows.foundation", "--hidden-import", "winrt.windows.foundation.collections",
        "--hidden-import", "winrt.windows.data.xml.dom",
        "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtQuick", "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtPdf", "--exclude-module", "PySide6.QtPdfWidgets",
        WINDOWS / "app.py")
    package = BUILD / "package/TGFocus-Windows-x64"
    if package.exists():
        shutil.rmtree(package)
    shutil.copytree(app_dist / "TGFocus", package)
    shutil.copytree(worker, package / "helper")
    for file in ("WINDOWS.ru.md", "AI.ru.md", "LICENSE"):
        shutil.copy2(ROOT / file, package / file)
    run(python, __file__, "--collect-licenses", package / "Licenses")
    shutil.copytree(WINDOWS / "licenses", package / "Licenses/Qt-notices")
    # LGPL replacement/rebuild instructions include our exact corresponding app
    # source; upstream Qt/PySide source is linked separately in Qt-notices.
    source_copy = package / "Source"
    for folder in (WINDOWS, SOURCE / "Helper", SOURCE / "tests"):
        for file in folder.glob("*.py"):
            target = source_copy / "source" / folder.name / file.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
    shutil.copy2(WINDOWS / "requirements.txt", source_copy / "source/Windows/requirements.txt")
    shutil.copytree(WINDOWS / "hooks", source_copy / "source/Windows/hooks",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(WINDOWS / "licenses", source_copy / "source/Windows/licenses")
    for file in ("WINDOWS.ru.md", "AI.ru.md", "LICENSE"):
        shutil.copy2(ROOT / file, source_copy / file)
    # Catch accidental Qt optional-module collection instead of silently shipping
    # GPL-only plugins alongside an LGPL-component notice set.
    for library in package.rglob("Qt6*.dll"):
        if library.name.lower() not in {"qt6core.dll", "qt6gui.dll", "qt6widgets.dll", "qt6network.dll"}:
            raise RuntimeError(f"Unexpected Qt module in portable package: {library.name}")
    # Exercise exactly the folder later placed in the ZIP; no user account/API key.
    run(python, SOURCE / "tests/smoke_windows.py", package / "TGFocus.exe")

    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(output / package.name), "zip", package.parent, package.name))
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(f"Ready: {archive}")


if __name__ == "__main__":
    main()
