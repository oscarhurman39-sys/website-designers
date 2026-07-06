"""Single entrypoint dispatching to the pipeline's run modes:

    python run.py quick-test              # one-shot: pipeline/quick_run.py
    python run.py loop                    # main.py's always-on orchestrator + console
    python run.py dashboard               # streamlit run dashboard.py
    python run.py test-email you@x.com    # end-to-end test email to YOURSELF

Each mode runs as its own subprocess, not imported in-process -- this is a
thin dispatcher, not a reimplementation. That matters because `loop` reads
operator commands from stdin (takeover/payment/transfer) and `dashboard`
is a streamlit server process; both need to run exactly as if invoked
directly, not nested inside another Python process's event/import state.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = REPO_ROOT / "pipeline"


def run_quick_test() -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "quick_run.py")], cwd=str(PIPELINE_DIR))


def run_loop() -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "main.py")], cwd=str(PIPELINE_DIR))


def run_dashboard() -> int:
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "dashboard.py")], cwd=str(REPO_ROOT)
    )


def run_test_email(email: str) -> int:
    """The "am I ready?" button: pipeline/test_email.py sends one end-to-end
    test email (dummy lead -> real design/deploy -> send) to `email`."""
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "test_email.py"), email], cwd=str(PIPELINE_DIR)
    )


_MODES = {
    "quick-test": run_quick_test,
    "loop": run_loop,
    "dashboard": run_dashboard,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrypoint for the cold-email sales pipeline.")
    parser.add_argument("mode", choices=sorted([*_MODES, "test-email"]), help="Which mode to run")
    parser.add_argument("email", nargs="?", help="Recipient for test-email mode (your own address)")
    args = parser.parse_args()

    if args.mode == "test-email":
        if not args.email:
            parser.error("test-email mode needs an address: python run.py test-email you@example.com")
        raise SystemExit(run_test_email(args.email))
    raise SystemExit(_MODES[args.mode]())


if __name__ == "__main__":
    main()
