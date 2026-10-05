"""The offline proof commands (run.py preflight / offline-readiness and
tools/ops.py run-now-proof) must stay local: they run the test suite and the
offline preflight, write a report, and never start the pipeline. subprocess is
faked throughout, so the suite never runs inside itself."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# run.py and tools/ live at the repo root; conftest only puts pipeline/ on the path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run  # noqa: E402
from tools import ops  # noqa: E402


def test_run_preflight_forwards_offline_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(run.subprocess, "call", lambda cmd, **kwargs: calls.append(cmd) or 0)

    assert run.run_preflight(["--offline"]) == 0

    assert Path(calls[0][1]).name == "preflight.py"
    assert calls[0][2:] == ["--offline"]


def _fake_readiness(monkeypatch, tmp_path, returncodes):
    monkeypatch.setattr(run, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run, "_local_python", lambda: "venv-python")
    codes = iter(returncodes)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]))
        return subprocess.CompletedProcess(cmd, next(codes), stdout="output\n", stderr="")

    monkeypatch.setattr(run.subprocess, "run", fake_run)
    return calls


def _report(tmp_path) -> str:
    return next((tmp_path / "reports").glob("offline-readiness-*.md")).read_text(encoding="utf-8")


def test_offline_readiness_runs_suite_then_offline_preflight_and_reports_pass(monkeypatch, tmp_path):
    calls = _fake_readiness(monkeypatch, tmp_path, (0, 0))

    assert run.run_offline_readiness([]) == 0

    (suite, suite_env), (preflight_cmd, _) = calls
    assert suite == ["venv-python", "-m", "pytest", "-q", "--basetemp=.pytest-tmp/offline-readiness", "tests"]
    assert Path(preflight_cmd[1]).name == "run.py" and preflight_cmd[2:] == ["preflight", "--offline"]
    assert suite_env["TMP"] == suite_env["TEMP"] == str(tmp_path / ".pytest-tmp")
    assert "Status: PASS" in _report(tmp_path)


@pytest.mark.parametrize("returncodes", [(1, 0), (0, 1)])
def test_offline_readiness_fails_if_either_step_fails(monkeypatch, tmp_path, returncodes):
    _fake_readiness(monkeypatch, tmp_path, returncodes)

    assert run.run_offline_readiness([]) == 1
    assert "Status: FAIL" in _report(tmp_path)


def test_offline_readiness_rejects_extra_arguments(monkeypatch, tmp_path):
    calls = _fake_readiness(monkeypatch, tmp_path, ())

    assert run.run_offline_readiness(["x"]) == 1
    assert calls == []


def test_run_now_proof_refuses_without_offline_and_spawns_nothing(monkeypatch, tmp_path):
    import config

    def boom(*args, **kwargs):
        raise AssertionError("run-now-proof started a process")

    monkeypatch.setattr(ops.subprocess, "run", boom)
    monkeypatch.setattr(ops.subprocess, "Popen", boom)
    monkeypatch.setattr(ops, "REPO", tmp_path)
    monkeypatch.setattr(ops, "PROOF_DIR", tmp_path / "proofs")
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "SOURCING_ENABLED", False)

    assert ops.run_now_proof(False) == 2
    assert not (tmp_path / "proofs").exists()

    assert ops.run_now_proof(True) == 0
    text = next((tmp_path / "proofs").glob("run-now-proof-*.md")).read_text(encoding="utf-8")
    assert "Mode: offline proof only" in text
    assert "This command did not source leads, send emails, publish previews" in text
    assert "`ENABLE_LIVE_SEND=false`" in text
    assert "`SOURCING_ENABLED=false`" in text
