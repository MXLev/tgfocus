#!/usr/bin/env bash
# Test the actual disk image and a copy of the app installed from it.
set -euo pipefail
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo "DMG smoke tests require an Apple Silicon Mac." >&2
  exit 1
fi
repo_root="$(cd "$(dirname "$0")/../.." && pwd)"
test_root="$(mktemp -d "${TMPDIR:-/tmp}/tgfocus-install-test.XXXXXX")"
mount_point="$test_root/mounted"
mounted=false
cleanup() {
  if [[ "$mounted" == true ]]; then
    if ! hdiutil detach "$mount_point"; then
      echo "Could not detach test image; preserved $test_root" >&2
      return 1
    fi
  fi
  rm -rf "$test_root"
}
trap cleanup EXIT
mkdir "$mount_point"
(cd "$repo_root/dist" && shasum -a 256 -c TGFocus-unsigned-arm64.dmg.sha256)
hdiutil attach -readonly -nobrowse -mountpoint "$mount_point" \
  "$repo_root/dist/TGFocus-unsigned-arm64.dmg"
mounted=true
test "$(readlink "$mount_point/Applications")" = /Applications
test -s "$mount_point/Установка.txt"
test -s "$mount_point/.DS_Store"
test -s "$mount_point/.background.png"
# Validate the distributed bundle before copying, as well as the installed copy.
codesign --verify --deep --strict "$mount_point/Telegram Focus.app"
ditto "$mount_point/Telegram Focus.app" "$test_root/Telegram Focus.app"
# Detach before testing to ensure the installed app is self-contained.
hdiutil detach "$mount_point"
mounted=false
app="$test_root/Telegram Focus.app"
codesign --verify --deep --strict "$app"
test -x "$app/Contents/MacOS/TelegramFocus"
python3 "$repo_root/source/tests/smoke_worker.py" \
  "$app/Contents/Resources/helper/focus-worker"
echo "DMG checksum, contents, app copy, signature, and installed worker OK"
