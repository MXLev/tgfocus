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
        # Windowed PyInstaller applications may have no stdout/stderr at all.
        if result.returncode != 0:
            raise RuntimeError(f"Frozen Windows startup failed (exit {result.returncode})")
        if not result_file.is_file():
            raise RuntimeError("Frozen Windows app exited without a successful smoke receipt")
        receipt = json.loads(result_file.read_text(encoding="utf-8"))
        if receipt.get("ok") is not True:
            raise RuntimeError("Frozen Windows app did not complete its smoke checks")
        # The shared worker must be part of the exact tested folder, not a system Python.
        worker = executable.parent / "helper/focus-worker.exe"
        if not worker.is_file():
            raise RuntimeError("Portable package is missing its bundled worker")
        print("Windows frozen app: worker ready, settings reopen, isolated native integration smoke OK")


if __name__ == "__main__":
    main()
