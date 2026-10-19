#!/usr/bin/env bash
# Package the app produced by build.py; no Developer ID or notarization required.
set -euo pipefail

if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "DMG packaging requires an Apple Silicon Mac." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
app="$repo_root/Telegram Focus.app"
output="$repo_root/dist/TGFocus-unsigned-arm64.dmg"
if [[ ! -d "$app" ]]; then
  echo "Build the app first: python3.13 source/build.py" >&2
  exit 1
fi
codesign --verify --deep --strict "$app"
mkdir -p "$repo_root/dist"
build="$repo_root/source/.build"
python="$build/venv/bin/python"
if [[ ! -x "$python" ]] || ! "$python" -c 'import dmgbuild' >/dev/null 2>&1; then
  echo "Install packaging dependencies in the build environment:" >&2
  echo "  source/.build/venv/bin/python -m pip install -r source/requirements-build.txt" >&2
  exit 1
fi
swiftc -module-cache-path "$build/swift-cache" \
  "$repo_root/source/DmgBackground.swift" -o "$build/make-dmg-background"
"$build/make-dmg-background" "$build/dmg-background.png"
"$python" -m dmgbuild -s "$repo_root/source/dmg_settings.py" \
  -D "app=$app" -D "guide=$repo_root/INSTALL.ru.md" \
  -D "background=$build/dmg-background.png" "Telegram Focus" "$output"
hdiutil verify "$output"
(cd "$repo_root/dist" && shasum -a 256 TGFocus-unsigned-arm64.dmg > TGFocus-unsigned-arm64.dmg.sha256)
echo "Ready: $output"
