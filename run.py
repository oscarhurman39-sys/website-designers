"""Single entrypoint dispatching to the pipeline's run modes:

    python run.py quick-test           # one-shot: pipeline/quick_run.py
    python run.py loop                 # main.py's always-on orchestrator + console
    python run.py dashboard            # streamlit run dashboard.py
    python run.py doctor               # local setup/safety health check
    python run.py test                 # offline pytest suite
    python run.py retry-lead <id>      # reset one failed lead for another pass
    python run.py test-email <email>   # one-shot: pipeline/test_email.py

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


def run_quick_test(extra_args: list[str]) -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "quick_run.py")], cwd=str(PIPELINE_DIR))


def run_loop(extra_args: list[str]) -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "main.py")], cwd=str(PIPELINE_DIR))


def run_dashboard(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, "-m", "streamlit", "run", str(REPO_ROOT / "dashboard.py")], cwd=str(REPO_ROOT)
    )


def run_doctor(extra_args: list[str]) -> int:
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "doctor.py")], cwd=str(PIPELINE_DIR))


def run_tests(extra_args: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", "pytest", *extra_args], cwd=str(REPO_ROOT))


def run_retry_lead(extra_args: list[str]) -> int:
    if len(extra_args) != 1 or not extra_args[0].isdigit():
        print("Usage: python run.py retry-lead <lead_id>")
        return 1

    sys.path.insert(0, str(PIPELINE_DIR))
    import config  # noqa: PLC0415
    from utils import db  # noqa: PLC0415

    db.init_db()
    lead_id = int(extra_args[0])
    lead = db.get_lead(lead_id)
    if lead is None:
        print(f"No such lead {lead_id}")
        return 1
    target_status = "researched" if lead.get("contact_email") else "new"
    db.refresh_lead_score(lead_id)
    db.update_lead_status(lead_id, target_status, notes="Manually retried via run.py")
    mode = "live send enabled" if config.ENABLE_LIVE_SEND else "dry-run email mode"
    print(f"Lead {lead_id} reset to '{target_status}' ({mode}).")
    return 0


def run_test_email(extra_args: list[str]) -> int:
    if len(extra_args) != 1:
        print("Usage: python run.py test-email <email-address>")
        return 1
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "test_email.py"), extra_args[0]], cwd=str(PIPELINE_DIR)
    )


_MODES = {
    "quick-test": run_quick_test,
    "loop": run_loop,
    "dashboard": run_dashboard,
    "doctor": run_doctor,
    "test": run_tests,
    "retry-lead": run_retry_lead,
    "test-email": run_test_email,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrypoint for the cold-email sales pipeline.")
    parser.add_argument("mode", choices=sorted(_MODES), help="Which mode to run")
    parser.add_argument("mode_args", nargs=argparse.REMAINDER, help="Extra arguments for the chosen mode")
    args = parser.parse_args()
    raise SystemExit(_MODES[args.mode](args.mode_args))


if __name__ == "__main__":
    main()
