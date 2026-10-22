"""Launch the frozen GUI with isolated state, no real account and no actual toasts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit("Missing frozen Windows app")
    with tempfile.TemporaryDirectory(prefix="tgfocus-windows-smoke-") as temporary:
        directory = Path(temporary)
        result_file = directory / "smoke-result.json"
        environment = dict(os.environ, TELEGRAM_FOCUS_DATA_DIR=str(directory / "data"),
                           TELEGRAM_FOCUS_SMOKE_RESULT=str(result_file), QT_QPA_PLATFORM="offscreen",
                           PYTHONUTF8="1")
        result = subprocess.run([str(executable), "--startup-smoke-test"], env=environment,
                                capture_output=True, timeout=60)
        receipt = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else {}
        # Windowed PyInstaller applications may have no stdout/stderr at all.
        if result.returncode != 0:
            raise RuntimeError(f"Frozen Windows startup failed (exit {result.returncode}): "
                               + str(receipt.get("error", "no smoke receipt")))
        if not result_file.is_file():
            raise RuntimeError("Frozen Windows app exited without a successful smoke receipt")
        if receipt.get("ok") is not True:
            raise RuntimeError("Frozen Windows app did not complete its smoke checks")
        # The shared worker must be part of the exact tested folder, not a system Python.
        worker = executable.parent / "helper/focus-worker.exe"
        if not worker.is_file():
            raise RuntimeError("Portable package is missing its bundled worker")
        checks = receipt.get("checks", {})
        print("::notice title=Windows WNF startup smoke::"
              f"Subscription={checks.get('wnf_subscribed')}; DND={checks.get('focus')}. "
              "Read-only native startup/cleanup checked; desktop DND transitions not tested.")
        if checks.get("shortcut_identity") != "OK":
            raise RuntimeError("Native shortcut/AUMID round trip was not checked")
        print("::notice title=Windows notification identity smoke::"
              "Native shortcut/AUMID created and read back successfully in isolated storage. "
              "Interactive Settings discovery/priority banners not tested.")
        if checks.get("notification_construction") != "OK" or checks.get("cases") != 3:
            raise RuntimeError("Real WinRT notification XML/data/audio/callback construction was not checked")
        print("::notice title=Windows notification construction smoke::"
              "Real WinRT XML/data/events/audio/protocol construction passed for 3 toasts; native Show intercepted.")
        print("Windows frozen app: worker ready, settings reopen, isolated native integration smoke OK")


if __name__ == "__main__":
    main()
