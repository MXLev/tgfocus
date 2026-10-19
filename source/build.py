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
from build_support import app_update_lock, ensure_app_stopped, replace_app_under_lock, runtime_directory

SOURCE = Path(__file__).resolve().parent
PRODUCT = SOURCE.parent
BUILD = SOURCE / ".build"


def run(*args, **kwargs):
    command = [str(a) for a in args]
    if command[0] == "swiftc" or (command[0] == "xcrun" and "appintentsmetadataprocessor" in command):
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kwargs)
        print(result.stdout, end="", flush=True)
        if result.returncode:
            message = result.stdout[-10000:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Native build failed::{message}", flush=True)
            raise subprocess.CalledProcessError(result.returncode, command)
    else:
        subprocess.run(command, check=True, **kwargs)


def main():
    global BUILD
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, help="Existing build venv, otherwise create one")
    parser.add_argument("--swift-binary", type=Path, help="Reuse a previously compiled Swift executable")
    parser.add_argument("--helper-dir", type=Path, help="Reuse a previously packaged worker")
    parser.add_argument("--build-dir", type=Path, help="Directory for intermediate files")
    args = parser.parse_args()
    if args.build_dir: BUILD = args.build_dir.resolve()
    # One lock protects all shared build outputs and the final installation.
    with app_update_lock(runtime_directory()):
        build(args)


def build(args):
    app = PRODUCT / "Telegram Focus.app"
    ensure_app_stopped(app)
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
    sources = [SOURCE / f"App/{name}.swift" for name in
               ("Models", "Views", "SettingsWindowController", "FocusAutomation", "TelegramFocus")]
    # Focus Filters need extracted App Intents metadata, not only linked Swift code.
    protocols = BUILD / "intent-protocols.json"
    protocols.write_text('["AppIntent", "SetFocusFilterIntent"]')
    const_values = BUILD / "TelegramFocus.swiftconstvalues"
    if not args.swift_binary:
        run("swiftc", "-module-name", "TelegramFocus", "-whole-module-optimization",
            "-emit-const-values-path", const_values,
            "-Xfrontend", "-const-gather-protocols-file", "-Xfrontend", protocols, "-swift-version", "5", "-target", "arm64-apple-macosx14.0", "-O",
            "-module-cache-path", BUILD / "swift-cache", "-framework", "AppKit", "-framework", "SwiftUI",
            "-framework", "UserNotifications", "-framework", "Security", "-framework", "CoreImage", "-framework", "AppIntents",
            *sources, "-o", swift)
    run("swiftc", "-swift-version", "5", "-module-cache-path", BUILD / "swift-cache",
        "-framework", "AppKit", "-framework", "SwiftUI", "-framework", "UserNotifications", "-framework", "Security", "-framework", "CoreImage", "-framework", "AppIntents",
        SOURCE / "App/Models.swift", SOURCE / "App/SettingsWindowController.swift", SOURCE / "App/FocusAutomation.swift", SOURCE / "tests/NativeTests.swift", "-o", BUILD / "native-tests")
    run(BUILD / "native-tests")
    helper = args.helper_dir.resolve() if args.helper_dir else BUILD / "dist/focus-worker"
    if not args.helper_dir:
        run(python, "-m", "PyInstaller", "--noconfirm", "--onedir", "--name", "focus-worker",
            "--distpath", BUILD / "dist", "--workpath", BUILD / "pyinstaller-work", "--specpath", BUILD,
            "--paths", SOURCE / "Helper", SOURCE / "Helper/worker.py", env=env)
    run(python, SOURCE / "tests/smoke_worker.py", helper / "focus-worker")
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
                 CFBundleShortVersionString="1.4.0", CFBundleVersion="6", CFBundleIconFile="AppIcon", LSUIElement=True,
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
    source_list = BUILD / "intent-sources.list"
    source_list.write_text("\n".join(map(str, sources)) + "\n")
    values_list = BUILD / "intent-values.list"
    values_list.write_text(str(const_values) + "\n")
    empty_list = BUILD / "intent-empty.list"
    empty_list.write_text("")
    developer = subprocess.check_output(["xcode-select", "-p"], text=True).strip()
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    xcode_version = subprocess.check_output(["xcodebuild", "-version"], text=True).splitlines()[-1].split()[-1]
    run("xcrun", "appintentsmetadataprocessor",
        "--toolchain-dir", Path(developer) / "Toolchains/XcodeDefault.xctoolchain",
        "--module-name", "TelegramFocus", "--sdk-root", sdk, "--xcode-version", xcode_version,
        "--platform-family", "macOS", "--deployment-target", "14.0",
        "--bundle-identifier", "local.mxlev.TelegramFocus", "--output", resources,
        "--target-triple", "arm64-apple-macos14.0", "--binary-file", contents / "MacOS/TelegramFocus",
        "--source-file-list", source_list, "--swift-const-vals-list", values_list,
        "--metadata-file-list", empty_list, "--static-metadata-file-list", empty_list,
        "--compile-time-extraction", "--deployment-aware-processing", "--no-app-shortcuts-localization")
    metadata = resources / "Metadata.appintents/extract.actionsdata"
    if not metadata.is_file() or b"TGFocusFilter" not in metadata.read_bytes():
        raise RuntimeError("Focus Filter metadata was not generated; refusing to distribute a broken auto mode")
    run("codesign", "--force", "--deep", "--sign", "-", "--identifier", "local.mxlev.TelegramFocus", staged)
    run("codesign", "--verify", "--deep", "--strict", staged)
    replace_app_under_lock(staged, app, lambda candidate: run("codesign", "--verify", "--deep", "--strict", candidate))
    shutil.rmtree(staged)
    print(f"\nReady: {app}")


if __name__ == "__main__": main()
