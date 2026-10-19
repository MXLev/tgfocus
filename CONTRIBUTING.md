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

Building the native app requires an Apple Silicon Mac, macOS 14+, Python 3.13, and Xcode Command Line Tools. To package a DMG, also install [Homebrew](https://brew.sh/) and GNU coreutils. These are build tools, not requirements for users installing the DMG:

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
- Confirm the default automatic Focus option requests status access once. Keep TGFocus OUT of Focus's Allowed Apps, allow its time-sensitive notifications in notification settings and in the selected Focus, and mute ordinary Telegram in that Focus. Toggle Focus and verify TGFocus pauses/resumes without Shortcuts. Inspect the delivered banner as well as the rule log: macOS controls final delivery. Denied/unknown status or disabled time-sensitive permission must suppress auto-delivery with an explanation. Adding TGFocus to Allowed Apps must pause auto-delivery rather than be treated as a global Focus signal. CI checks the entitlement and delivery policy; actual OS permission/status availability and bypass delivery in the ad-hoc-signed app require a Mac check.
- Disable automatic mode: expect a duplicate-notification warning and Cancel/Confirm options. Cancel must preserve automatic mode; confirm must persist manual mode after restart.
- Check mention-only and reply-only switches independently, including a reply that also explicitly mentions you. Check inherited chat/category mute settings, a muted forum topic in an unmuted group, an explicitly unmuted topic, and the General topic.
- While the app is running, a source build must refuse to replace it. Run two source builds concurrently: the second must stop before touching shared output. The runtime lock covers the entire first build through installation. Python tests exercise copy/validation failures, rollback, and lock contention throughout the build.

### Release checklist

1. Run the full macOS build and DMG smoke test above, or download the successful CI run's DMG artifact.
2. On a Mac, test the downloaded DMG: drag to Applications, eject the image, and launch the installed app. Check the expected Gatekeeper prompt and the documented first-launch steps. Automated tests do not validate Gatekeeper or the graphical interface.
3. Test notifications, settings, and (with your own credentials) Telegram login. Never share account credentials, session strings, login codes, or private messages in issues or logs.
4. Keep the version in `source/build.py` consistent with the release tag. Push a version tag to create a draft release, then review the artifacts and notes before publishing it.

For security-sensitive reports, read [SECURITY.md](SECURITY.md). Keep discussion respectful and focused on the work.
