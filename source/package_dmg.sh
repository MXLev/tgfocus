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
# dmgbuild invokes `sync --file-system`, which the macOS /bin/sync lacks.
# Homebrew keeps GNU sync named gsync; expose it only to the packaging process.
gnu_sync="$(command -v gsync || true)"
if [[ -z "$gnu_sync" ]] && command -v brew >/dev/null 2>&1; then
  coreutils_prefix="$(brew --prefix coreutils 2>/dev/null || true)"
  if [[ -x "$coreutils_prefix/bin/gsync" ]]; then
    gnu_sync="$coreutils_prefix/bin/gsync"
  fi
fi
if [[ -z "$gnu_sync" ]]; then
  echo "DMG packaging requires GNU coreutils. Install Homebrew, then run: brew install coreutils" >&2
  exit 1
fi
packaging_tools="$(mktemp -d "${TMPDIR:-/tmp}/tgfocus-dmg-tools.XXXXXX")"
trap 'rm -rf "$packaging_tools"' EXIT
ln -s "$gnu_sync" "$packaging_tools/sync"
swiftc -module-cache-path "$build/swift-cache" \
  "$repo_root/source/DmgBackground.swift" -o "$build/make-dmg-background"
"$build/make-dmg-background" "$build/dmg-background.png"
PATH="$packaging_tools:$PATH" "$python" -m dmgbuild -s "$repo_root/source/dmg_settings.py" \
  -D "app=$app" -D "guide=$repo_root/INSTALL.ru.md" \
  -D "background=$build/dmg-background.png" "Telegram Focus" "$output"
hdiutil verify "$output"
(cd "$repo_root/dist" && shasum -a 256 TGFocus-unsigned-arm64.dmg > TGFocus-unsigned-arm64.dmg.sha256)
echo "Ready: $output"
