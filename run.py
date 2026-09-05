"""Single entrypoint dispatching to the pipeline's run modes:

    python run.py quick-test           # one-shot: pipeline/quick_run.py
    python run.py loop                 # main.py's always-on orchestrator + console
    python run.py dashboard            # streamlit run dashboard.py
    python run.py test-email <email>   # one-shot: pipeline/test_email.py
    python run.py cleanup-tests [--dry-run]  # delete test leads + their repos/projects

Each mode runs as its own subprocess, not imported in-process -- this is a
thin dispatcher, not a reimplementation. That matters because `loop` reads
optional operator commands from stdin (transfer/status/pause/resume) and
`dashboard` is a streamlit server process; both need to run exactly as if
invoked directly, not nested inside another Python process's event/import
state.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PIPELINE_DIR = REPO_ROOT / "pipeline"


def run_quick_test(extra_args: list[str]) -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "quick_run.py")], cwd=str(PIPELINE_DIR))


def run_loop(extra_args: list[str]) -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "main.py")], cwd=str(PIPELINE_DIR))


def run_dashboard(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "dashboard.py")], cwd=str(REPO_ROOT)
    )


def run_test_email(extra_args: list[str]) -> int:
    if len(extra_args) != 1:
        print("Usage: python run.py test-email <email-address>")
        return 1
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "test_email.py"), extra_args[0]], cwd=str(PIPELINE_DIR)
    )


def run_cleanup_tests(extra_args: list[str]) -> int:
    # extra_args passes --dry-run straight through to cleanup_tests.py's argparse.
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "cleanup_tests.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


_MODES = {
    "quick-test": run_quick_test,
    "loop": run_loop,
    "dashboard": run_dashboard,
    "test-email": run_test_email,
    "cleanup-tests": run_cleanup_tests,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrypoint for the cold-email sales pipeline.")
    parser.add_argument("mode", choices=sorted(_MODES), help="Which mode to run")
    parser.add_argument("mode_args", nargs=argparse.REMAINDER, help="Extra arguments for the chosen mode")
    args = parser.parse_args()
    raise SystemExit(_MODES[args.mode](args.mode_args))


if __name__ == "__main__":
    main()
