"""Single entrypoint dispatching to the pipeline's run modes:

    python run.py quick-test           # one-shot: pipeline/quick_run.py
    python run.py loop                 # main.py's always-on orchestrator + console
    python run.py dashboard            # streamlit run dashboard.py
    python run.py test-email <email>   # one-shot: pipeline/test_email.py
    python run.py cleanup-tests [--dry-run]  # delete test leads + their repos/projects
    python run.py report                     # per-niche funnel + revenue
    python run.py control [--json] [--all]    # read-only agent ownership + stalled-work board
    python run.py rebuild <lead_id>          # one-shot: pipeline/rebuild_preview.py
    python run.py ops start|stop|restart|status [--json]|logs [name]   # headless services, no windows
    python run.py source [--limit N] [--dry-run]   # one-shot: pipeline/source_leads.py
    python run.py preflight [--offline]     # go-live readiness check; prints GO / READY / NO-GO
    python run.py preview-email [--lead ID] # print the exact cold email; nothing sent or written
    python run.py preview-audit [--limit 10] # offline audit of active previews; nothing sent
    python run.py test-alert                # post one test alert to the Slack channel
    python run.py serve-public [--status]   # bring PUBLIC_BASE_URL up, or report why it is not
    python run.py reviewed-batch --prepare --lead ID [--lead ID...]
    python run.py offline-readiness         # tests + offline preflight with a timestamped proof report

Each mode runs as its own subprocess, not imported in-process -- this is a
thin dispatcher, not a reimplementation. That matters because `loop` reads
optional operator commands from stdin (transfer/status/pause/resume) and
`dashboard` is a streamlit server process; both need to run exactly as if
invoked directly, not nested inside another Python process's event/import
state.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
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


def run_report(extra_args: list[str]) -> int:
    # Per-niche funnel + revenue, to decide which niches to focus on.
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "report.py"), *extra_args], cwd=str(PIPELINE_DIR))


def run_control(extra_args: list[str]) -> int:
    # Read-only operating board: owner, next action, stage age and exceptions.
    return subprocess.call([sys.executable, str(PIPELINE_DIR / "control.py"), *extra_args], cwd=str(PIPELINE_DIR))


def run_rebuild(extra_args: list[str]) -> int:
    # Re-render + redeploy one lead's preview in place (after new photos/logo or a template change).
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "rebuild_preview.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_source(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "source_leads.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_preflight(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "preflight.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_preview_email(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "preview_email.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_preview_audit(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "preview_audit.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_test_alert(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "test_alert.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_serve_public(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "serve_public.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def run_ops(extra_args: list[str]) -> int:
    # Headless service manager (tools/ops.py): start|stop|restart|status [--json]|logs. No windows.
    return subprocess.call([sys.executable, str(REPO_ROOT / "tools" / "ops.py"), *extra_args], cwd=str(REPO_ROOT))


def run_reviewed_batch(extra_args: list[str]) -> int:
    return subprocess.call(
        [sys.executable, str(PIPELINE_DIR / "reviewed_batch.py"), *extra_args], cwd=str(PIPELINE_DIR)
    )


def _local_python() -> str:
    """Prefer the repo venv on Windows, then fall back to this interpreter."""
    venv_python = REPO_ROOT / "venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def _append_command_report(report_lines: list[str], title: str, cmd: list[str], result: subprocess.CompletedProcess[str]) -> None:
    report_lines.append(f"## {title}")
    report_lines.append("")
    report_lines.append("Command:")
    report_lines.append("```text")
    report_lines.append(" ".join(cmd))
    report_lines.append("```")
    report_lines.append("")
    report_lines.append(f"Exit code: {result.returncode}")
    report_lines.append("")
    report_lines.append("Output:")
    report_lines.append("```text")
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    report_lines.append(output if output else "<no output>")
    report_lines.append("```")
    report_lines.append("")


def run_offline_readiness(extra_args: list[str]) -> int:
    """Run the Windows-safe offline readiness proof and save the evidence."""
    if extra_args:
        print("Usage: python run.py offline-readiness")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    report_path = reports_dir / f"offline-readiness-{timestamp}.md"

    tmp_dir = REPO_ROOT / ".pytest-tmp"
    tmp_dir.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["TMP"] = str(tmp_dir)
    env["TEMP"] = str(tmp_dir)

    python_exe = _local_python()
    checks: list[tuple[str, list[str]]] = [
        ("Full pytest suite", [python_exe, "-m", "pytest", "-q"]),
        ("Offline preflight", [python_exe, str(REPO_ROOT / "run.py"), "preflight", "--offline"]),
    ]

    report_lines = [
        "# Offline readiness proof",
        "",
        f"Created: {datetime.now().isoformat(timespec='seconds')}",
        f"Repo: {REPO_ROOT}",
        f"Python: {python_exe}",
        f"TMP/TEMP: {tmp_dir}",
        "",
        "This local proof runs the Windows-safe test command, then the offline preflight.",
        "No sourcing, email, payment, publishing, or prospect contact is triggered.",
        "",
    ]

    failures = 0
    for title, cmd in checks:
        print(f"Running {title}...")
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        _append_command_report(report_lines, title, cmd, result)
        if result.returncode != 0:
            failures += 1

    status = "PASS" if failures == 0 else "FAIL"
    report_lines.insert(2, f"Status: {status}")
    report_lines.insert(3, "")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"Offline readiness report written to {report_path}")
    return 0 if failures == 0 else 1


_MODES = {
    "quick-test": run_quick_test,
    "loop": run_loop,
    "dashboard": run_dashboard,
    "test-email": run_test_email,
    "cleanup-tests": run_cleanup_tests,
    "source": run_source,
    "rebuild": run_rebuild,
    "report": run_report,
    "control": run_control,
    "preflight": run_preflight,
    "preview-email": run_preview_email,
    "preview-audit": run_preview_audit,
    "test-alert": run_test_alert,
    "serve-public": run_serve_public,
    "reviewed-batch": run_reviewed_batch,
    "offline-readiness": run_offline_readiness,
    "ops": run_ops,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Entrypoint for the cold-email sales pipeline.")
    parser.add_argument("mode", choices=sorted(_MODES), help="Which mode to run")
    parser.add_argument("mode_args", nargs=argparse.REMAINDER, help="Extra arguments for the chosen mode")
    args = parser.parse_args()
    raise SystemExit(_MODES[args.mode](args.mode_args))


if __name__ == "__main__":
    main()
