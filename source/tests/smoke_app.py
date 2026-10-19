"""Launch the actual signed app after installation, without account access."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def main():
    executable = Path(sys.argv[1]).resolve() / 'Contents/MacOS/TelegramFocus'
    with tempfile.TemporaryDirectory(prefix='tgfocus-app-startup-') as directory:
        environment = dict(os.environ, TELEGRAM_FOCUS_DATA_DIR=directory)
        process = subprocess.Popen([str(executable), '--startup-smoke-test'],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        try:
            output, errors = process.communicate(timeout=30)
            if process.returncode != 0:
                raise RuntimeError(f'Signed app exited with {process.returncode}:\n{output}\n{errors}')
            if 'Signed app startup: worker ready, settings opened and reopened OK' not in output:
                raise RuntimeError(f'App did not confirm readiness:\n{output}\n{errors}')
            print(output.strip())
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = str(error).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print(f'::error title=Signed app startup failed::{message}', flush=True)
        raise
