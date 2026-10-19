#!/usr/bin/env python3
"""Build a native Apple Silicon .app with a self-contained Telegram worker."""
import argparse
import importlib.metadata
import os
import plistlib
import shutil
import subprocess
import struct
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
PRODUCT = SOURCE.parent
BUILD = SOURCE / ".build"


def run(*args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    global BUILD
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, help="Existing build venv, otherwise create one")
    parser.add_argument("--swift-binary", type=Path, help="Reuse a previously compiled Swift executable")
    parser.add_argument("--helper-dir", type=Path, help="Reuse a previously packaged worker")
    parser.add_argument("--build-dir", type=Path, help="Directory for intermediate files")
    args = parser.parse_args()
    if args.build_dir: BUILD = args.build_dir.resolve()
    BUILD.mkdir(parents=True, exist_ok=True)
    venv = args.env.resolve() if args.env else BUILD / "venv"
    python = venv / "bin/python"
    if not python.exists():
        run(sys.executable, "-m", "venv", venv)
    # Reused environments must pick up new and changed build requirements too.
    run(python, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "-r", SOURCE / "requirements-build.txt")
    run(python, "-m", "unittest", "discover", "-s", SOURCE / "tests", "-p", "test_*.py")
    swift = args.swift_binary.resolve() if args.swift_binary else BUILD / "TelegramFocus"
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(BUILD / "pyinstaller-cache"))
    if not args.swift_binary:
        run("swiftc", "-swift-version", "5", "-target", "arm64-apple-macosx14.0", "-O",
            "-module-cache-path", BUILD / "swift-cache", "-framework", "AppKit", "-framework", "SwiftUI",
            "-framework", "UserNotifications", "-framework", "Security", "-framework", "CoreImage",
            SOURCE / "App/Models.swift", SOURCE / "App/Views.swift", SOURCE / "App/TelegramFocus.swift", "-o", swift)
    run("swiftc", "-swift-version", "5", "-module-cache-path", BUILD / "swift-cache",
        "-framework", "AppKit", "-framework", "SwiftUI", "-framework", "UserNotifications", "-framework", "Security", "-framework", "CoreImage",
        SOURCE / "App/Models.swift", SOURCE / "tests/NativeTests.swift", "-o", BUILD / "native-tests")
    run(BUILD / "native-tests")
    helper = args.helper_dir.resolve() if args.helper_dir else BUILD / "dist/focus-worker"
    if not args.helper_dir:
        run(python, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "focus-worker",
            "--distpath", BUILD / "dist", "--workpath", BUILD / "pyinstaller-work", "--specpath", BUILD,
            "--paths", SOURCE / "Helper", SOURCE / "Helper/worker.py", env=env)
    run(python, SOURCE / "tests/smoke_worker.py", helper / "focus-worker")
    app = PRODUCT / "Telegram Focus.app"
    # Build in a separate location and swap only after all checks pass.
    staged = BUILD / "Telegram Focus.app"
    if staged.exists(): shutil.rmtree(staged)
    contents = staged / "Contents"
    (contents / "MacOS").mkdir(parents=True)
    resources = contents / "Resources"
    resources.mkdir()
    shutil.copy2(swift, contents / "MacOS/TelegramFocus")
    shutil.copytree(helper, resources / "helper", symlinks=True)
    plist = dict(CFBundleExecutable="TelegramFocus", CFBundleIdentifier="local.mxlev.TelegramFocus",
                 CFBundleName="Telegram Focus", CFBundleDisplayName="Telegram Focus", CFBundlePackageType="APPL",
                 CFBundleShortVersionString="1.3.0", CFBundleVersion="4", CFBundleIconFile="AppIcon", LSUIElement=True,
                 LSMinimumSystemVersion="14.0", NSHighResolutionCapable=True,
                 NSSupportsAutomaticTermination=False, NSPrincipalClass="NSApplication")
    with (contents / "Info.plist").open("wb") as stream: plistlib.dump(plist, stream)
    iconset = BUILD / "AppIcon.iconset"
    run("swiftc", "-module-cache-path", BUILD / "swift-cache", SOURCE / "App/Icon.swift", "-o", BUILD / "make-icon")
    run(BUILD / "make-icon", iconset)
    # Standard PNG-backed ICNS chunks; avoids iconutil's dependence on GUI services.
    icon_chunks = []
    for code, filename in [(b"icp4", "icon_16x16.png"), (b"icp5", "icon_32x32.png"),
                           (b"ic07", "icon_128x128.png"), (b"ic08", "icon_256x256.png"),
                           (b"ic09", "icon_512x512.png"), (b"ic10", "icon_512x512@2x.png"),
                           (b"ic11", "icon_16x16@2x.png"), (b"ic12", "icon_32x32@2x.png"),
                           (b"ic13", "icon_128x128@2x.png"), (b"ic14", "icon_256x256@2x.png")]:
        png = (iconset / filename).read_bytes()
        icon_chunks.append(code + struct.pack(">I", len(png) + 8) + png)
    payload = b"".join(icon_chunks)
    (resources / "AppIcon.icns").write_bytes(b"icns" + struct.pack(">I", len(payload) + 8) + payload)
    shutil.copy2(PRODUCT / "README.md", resources / "README.md")
    run(python, SOURCE / "collect_licenses.py", resources / "Licenses")
    run("codesign", "--force", "--deep", "--sign", "-", "--identifier", "local.mxlev.TelegramFocus", staged)
    run("codesign", "--verify", "--deep", "--strict", staged)
    if app.exists(): shutil.rmtree(app)
    shutil.copytree(staged, app, symlinks=True)
    shutil.rmtree(staged)
    print(f"\nReady: {app}")


if __name__ == "__main__": main()
