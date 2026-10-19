"""Launch the actual signed app after installation, without account access."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def process_tree_sample(root_pid):
    """Sum resident memory and CPU time for the app and its worker descendants."""
    output = subprocess.check_output(['ps', '-axo', 'pid=,ppid=,rss=,time='], text=True)
    rows = {}
    for line in output.splitlines():
        pid, parent, rss, cpu = line.split()
        seconds = 0.0
        for part in cpu.split(':'):
            seconds = seconds * 60 + float(part)
        rows[int(pid)] = (int(parent), int(rss), seconds)
    descendants = {root_pid}
    while True:
        expanded = descendants | {pid for pid, row in rows.items() if row[0] in descendants}
        if expanded == descendants:
            break
        descendants = expanded
    live = [rows[pid] for pid in descendants if pid in rows]
    return time.monotonic(), sum(row[1] for row in live) / 1024, sum(row[2] for row in live)


def main():
    executable = Path(sys.argv[1]).resolve() / 'Contents/MacOS/TelegramFocus'
    with tempfile.TemporaryDirectory(prefix='tgfocus-app-startup-') as directory:
        environment = dict(os.environ, TELEGRAM_FOCUS_DATA_DIR=directory)
        process = subprocess.Popen([str(executable), '--startup-smoke-test', '--measure-idle'],
            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True)
        try:
            samples = []
            started = time.monotonic()
            while process.poll() is None and time.monotonic() - started < 50:
                # Ignore startup/rendering warmup; sample the app with filtering off.
                if time.monotonic() - started >= 7:
                    samples.append(process_tree_sample(process.pid))
                time.sleep(1)
            output, errors = process.communicate(timeout=5)
            if process.returncode != 0:
                raise RuntimeError(f'Signed app exited with {process.returncode}:\n{output}\n{errors}')
            if 'Signed app startup: worker ready, settings opened and reopened OK' not in output:
                raise RuntimeError(f'App did not confirm readiness:\n{output}\n{errors}')
            if len(samples) < 5 or 'Idle resource measurement ready' not in output:
                raise RuntimeError('App did not stay alive for the idle resource measurement')
            first, last = samples[0], samples[-1]
            cpu_percent = max(0, 100 * (last[2] - first[2]) / (last[0] - first[0]))
            peak_rss = max(sample[1] for sample in samples)
            rss_change = last[1] - first[1]
            print(f'::notice title=Idle app and worker resources::Filter off, no account, '
                  f'{last[0] - first[0]:.0f}s sample: average CPU {cpu_percent:.2f}% of one core, '
                  f'peak summed RSS {peak_rss:.1f} MiB, RSS change {rss_change:+.1f} MiB. '
                  'Short diagnostic, not a long-term leak test.', flush=True)
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
