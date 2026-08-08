"""Crash-recovery process supervisor for main.py -- NOT required for normal
operation.

main.py already contains its own infinite work loop (60s cadence) and
handles the actual pipeline logic; this script only exists as a safety net
for hosts that don't have a real process manager (systemd, supervisord,
pm2, etc.) to restart main.py if the Python process itself dies (an
unhandled exception outside main.py's own try/except, an OOM kill, a host
reboot, etc.).

Two modes:

    python scheduler.py
        Foreground supervisor: starts main.py, waits for it to exit, and
        restarts it (with a short backoff) if it ever does, forever, until
        you Ctrl-C. Run this inside tmux/screen exactly like you'd run
        main.py directly -- it's a thin wrapper, not a replacement for
        tmux/screen (see README.md "Deployment").

    python scheduler.py --check
        One-shot, cron-friendly mode: if main.py is already running
        (tracked via a PID file), do nothing and exit. If it isn't, start
        it detached in the background and exit. Designed to be invoked
        every few minutes by cron -- see crontab.example.

IMPORTANT LIMITATION: main.py reads operator commands (`takeover`,
`payment ready`, `transfer`, `pause`/`resume`) from stdin. A main.py
started via `--check` mode has no attached terminal, so those commands are
not usable against that instance -- you would need to attach a real
terminal (tmux/screen) to run them, which is exactly what the README's
"Deployment" section recommends as the primary way to run this pipeline.
Treat `--check`/cron as a fallback that keeps automation (research,
design, sending, inbox polling) alive if the process crashes, not as a
substitute for an interactive session when you need to act on a lead.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

PIPELINE_DIR = Path(__file__).resolve().parent
MAIN_SCRIPT = PIPELINE_DIR / "main.py"
PID_FILE = PIPELINE_DIR / ".scheduler.pid"
LOG_FILE = PIPELINE_DIR / "scheduler.log"

RESTART_BACKOFF_SECONDS = 10


def _pid_is_alive(pid: int) -> bool:
    """Best-effort liveness check. A bare `os.kill(pid, 0)` isn't reliable
    enough on its own -- confirmed by testing this against a just-killed
    main.py, which it reported as still alive: the PID briefly still
    resolves to an unreaped zombie (or, more rarely, gets recycled by an
    unrelated process) right after being killed. On Linux, cross-check
    /proc for a cmdline that actually references main.py and a non-zombie
    process state; fall back to the plain check if /proc isn't available
    (non-Linux) or the process is owned by another user."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else -- treat as alive

    status_path = Path(f"/proc/{pid}/status")
    cmdline_path = Path(f"/proc/{pid}/cmdline")
    if status_path.exists() and cmdline_path.exists():
        try:
            status_text = status_path.read_text(errors="replace")
            cmdline_text = cmdline_path.read_bytes().decode(errors="replace")
        except OSError:
            return True  # couldn't verify further; assume alive rather than risk a double-spawn
        is_zombie = "State:\tZ" in status_text or "State: Z" in status_text
        references_main = "main.py" in cmdline_text
        return references_main and not is_zombie

    return True  # no /proc (non-Linux) -- fall back to the plain liveness check above


def _read_pid() -> Optional[int]:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except ValueError:
        return None


def current_pid() -> Optional[int]:
    """Pid of a live main.py started via this scheduler, or None.

    Public liveness check for other processes (the dashboard's start/stop
    controls) so they don't reimplement the PID-file + /proc logic above.
    """
    pid = _read_pid()
    if pid is not None and _pid_is_alive(pid):
        return pid
    return None


def stop_main(timeout_seconds: float = 15.0) -> bool:
    """Stop a running main.py: SIGTERM, then SIGKILL if still alive at timeout.

    Returns True iff main.py is no longer running on return. main.py installs
    no SIGTERM handler, so SIGTERM normally ends it immediately; that is the
    same crash-safety contract cron/--check mode already relies on (each cycle
    is try/except-wrapped and the DB is SQLite).

    Only meaningful against a background main.py (started by --check mode or
    the dashboard). If main.py is running under the foreground supervisor
    (`python scheduler.py`), the supervisor will restart it ~10s after this
    kills it -- stop the supervisor from its own terminal instead.
    """
    pid = current_pid()
    if pid is None:
        PID_FILE.unlink(missing_ok=True)
        return True
    for sig, wait_seconds in ((signal.SIGTERM, timeout_seconds), (signal.SIGKILL, 3.0)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            break
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if not _pid_is_alive(pid):
                break
            time.sleep(0.5)
        if not _pid_is_alive(pid):
            break
    if _pid_is_alive(pid):
        return False
    PID_FILE.unlink(missing_ok=True)
    return True


def run_supervisor() -> None:
    """Foreground mode: keep main.py running forever, restarting on crash."""
    print(f"[scheduler] Supervising {MAIN_SCRIPT} -- Ctrl-C to stop.")
    while True:
        proc = subprocess.Popen([sys.executable, str(MAIN_SCRIPT)], cwd=str(PIPELINE_DIR))
        PID_FILE.write_text(str(proc.pid))
        try:
            returncode = proc.wait()
        except KeyboardInterrupt:
            print("\n[scheduler] Ctrl-C received, stopping main.py...")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            PID_FILE.unlink(missing_ok=True)
            return
        PID_FILE.unlink(missing_ok=True)
        print(f"[scheduler] main.py exited with code {returncode}; restarting in {RESTART_BACKOFF_SECONDS}s...")
        time.sleep(RESTART_BACKOFF_SECONDS)


def run_check_once() -> None:
    """Cron-friendly mode: start main.py in the background iff it isn't
    already running (PID-file guarded), then exit immediately."""
    pid = _read_pid()
    if pid is not None and _pid_is_alive(pid):
        print(f"[scheduler] main.py already running (pid {pid}); nothing to do.")
        return

    print("[scheduler] main.py not running; starting it in the background.")
    with open(LOG_FILE, "a") as log:
        proc = subprocess.Popen(
            [sys.executable, str(MAIN_SCRIPT)],
            cwd=str(PIPELINE_DIR),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from cron's process group
        )
    PID_FILE.write_text(str(proc.pid))
    print(f"[scheduler] Started main.py in background, pid {proc.pid}. Logs: {LOG_FILE}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check", action="store_true",
        help="One-shot: start main.py in the background if it isn't already running (for cron).",
    )
    args = parser.parse_args()

    if args.check:
        run_check_once()
    else:
        run_supervisor()


if __name__ == "__main__":
    main()
