# Contributing to TGFocus

Bug reports, documentation fixes, and pull requests are welcome. Russian and English are both fine. The project is licensed under MIT; contributions are submitted under the same license.

## Development

Use a branch in your clone or fork and open a pull request to `main`. Describe the user-visible problem, the change, and how you tested it. Discuss large changes in an issue first.

Python worker development works on Linux or macOS with Python 3.13:

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install Telethon==1.45.0
.venv/bin/python -m unittest discover -s source/tests -p 'test_*.py' -v
```

Building the native app requires an Apple Silicon Mac, macOS 14+, Python 3.13, and full Xcode (including appintentsmetadataprocessor). To package a DMG, also install [Homebrew](https://brew.sh/) and GNU coreutils. These are build tools, not requirements for users installing the DMG:

```sh
python3.13 source/build.py
brew install coreutils
bash source/package_dmg.sh
bash source/tests/smoke_dmg.sh
```

The build runs Python tests, native Swift tests, and packaged-worker checks. The DMG smoke test verifies the checksum, mounts the image, checks its contents, copies the app to a temporary directory, detaches the image, and exercises the copied worker. It does not modify `/Applications` or connect to a Telegram account.

Run relevant checks before pushing. Pull-request CI repeats Python tests and builds/tests the macOS DMG before merge. Hosted CI requires pushing a branch; a public release is a separate step. CI artifacts are test builds, not published releases.

## Before publishing a release

### Get a test installer without installing developer tools

The workflow files must first be committed and pushed to a development branch on GitHub. Open a pull request to `main`; no tag or release is needed. In the PR, open the **macOS app and DMG** check, or go to **Actions → CI** and select that run. After it succeeds, download **TGFocus-test-DMG** from the run's **Artifacts** section (GitHub sign-in is required). Unzip the artifact to find `TGFocus-unsigned-arm64.dmg`.

Open that DMG on an Apple Silicon Mac running macOS 14+. You should see the app on the left, an arrow, and Applications on the right. Drag the app to Applications, eject the disk image, then launch the installed app. Python and Xcode are not needed for this test. Follow [INSTALL.ru.md](INSTALL.ru.md) if Gatekeeper blocks the first launch.

Check that the menu-bar icon appears, settings open, and rule simulation works without logging in. To verify Telegram login and actual notifications, connect your own account and grant notification permission. Record the macOS version and results in the PR. Quit an existing installed copy before testing; copying over it replaces that app, so keep a copy first if you need to retain it.

The CI job validates the DMG automatically, but only this test on your Mac validates the visible installer, downloaded-app security prompts, and notification permissions. Uploading a CI artifact does not publish a GitHub Release.

### Launch, update, and notification regression checks

- Open settings from the menu bar, close them, and open them again. Closing settings must not stop filtering.
- Try launching the app directly from the read-only DMG: it should explain installation and exit before starting a worker or loading credentials.
- With an installed copy running, launch a second copy from a different writable folder. It should explain how to quit the first copy and exit without starting another worker.
- The free ad-hoc build must launch without the restricted time-sensitive entitlement. CI verifies the installed app signature, starts its actual signed executable and worker, and opens/reopens settings on macOS 15 and 26 using the same DMG. This test uses an isolated data directory and does not access credentials or request notification permissions.
- The free ad-hoc app must detect the missing signing Team ID via Security.framework and report native Focus Filters as unsupported. Preserve a saved automatic preference as paused until the user confirms manual mode; show settings on startup to explain. The guide must not direct ad-hoc users to the disabled Add button. Verify the confirmation works from the manual-mode button.
- `SetFocusFilterIntent.current` remains available in code for builds with a signing team. Team ID is a prerequisite, not proof of integration: creating a filter in System Settings and real off/on transitions require a correctly signed interactive test. Do not count `.notFound` from an unregistered ad-hoc app as validation of automatic delivery.
- Test the master switch from both settings and the menu: off must stop delivery, both labels must update, and the template menu icon must change between outlined and filled. An enabled master switch waiting for Focus must say so rather than appear broken. Manual mode still requires a duplicate-notification warning; confirm persists after restart.
- CI validates generated App Intents metadata and verifies that the actual ad-hoc app reports unsupported signing on macOS 15 and 26. This is a fallback test; it does not validate creating or running a native Focus Filter.
- Check mention-only and reply-only switches independently, including a reply that also explicitly mentions you. Check inherited chat/category mute settings, a muted forum topic in an unmuted group, an explicitly unmuted topic, and the General topic.
- While the app is running, a source build must refuse to replace it. Run two source builds concurrently: the second must stop before touching shared output. The runtime lock covers the entire first build through installation. Python tests exercise copy/validation failures, rollback, and lock contention throughout the build.

### Release checklist

1. Run the full macOS build and DMG smoke test above, or download the successful CI run's DMG artifact.
2. On a Mac, test the downloaded DMG: drag to Applications, eject the image, and launch the installed app. Check the expected Gatekeeper prompt and the documented first-launch steps. Automated tests do not validate Gatekeeper or the graphical interface.
3. Test notifications, settings, and (with your own credentials) Telegram login. Never share account credentials, session strings, login codes, or private messages in issues or logs.
4. Keep the version in `source/build.py` consistent with the release tag. Push a version tag to create a draft release, then review the artifacts and notes before publishing it.

For security-sensitive reports, read [SECURITY.md](SECURITY.md). Keep discussion respectful and focused on the work.

### Idle resource diagnostic

The installed-app smoke test closes settings, disables the master filter, and keeps the app and worker alive for 25 seconds without a Telegram account. It reports summed resident memory, memory change, and average CPU time for the app process tree after startup warmup as a GitHub Actions notice. This short disconnected sample does not prove the absence of long-term leaks or represent an active Telegram account.

With the master switch off, Focus polling stops and the worker skips message/entity/mute processing and IPC events. The Telegram connection stays available for fast resumption; its keepalive/reconnect traffic remains. In a build with a signing team, when automatic mode waits for Focus the worker skips message processing while the app checks the native Focus Filter every two seconds. An ad-hoc app does not poll the unsupported API. Decision history and worker caches are bounded; the IPC decoder drops frames over 1 MiB and recovers at the next newline.
