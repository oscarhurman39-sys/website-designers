"""Tests for maintenance.py: the live-client-site re-audit pass and the
monthly report generator. Network (requests.get / url_safety) is mocked;
DB is a real tmp_path SQLite file."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import maintenance
import pytest
from utils import db

READY_HTML = """
<html><head>
<meta name="viewport" content="width=device-width">
<title>Joes Cafe</title>
<meta name="description" content="A cafe in Leeds.">
</head><body><h1>Welcome</h1></body></html>
"""


def _setup_won_lead(tmp_path, monkeypatch, preview_url="https://joes-cafe.example"):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("Joes Cafe", "cafe", "Leeds")
    db.update_lead_status(lead_id, "researched")
    db.update_lead_status(lead_id, "designed")
    db.update_lead_status(lead_id, "emailed")
    db.update_lead_status(lead_id, "won")
    db.insert_website(lead_id=lead_id, template_niche="cafe", repo_url="", repo_full_name="", preview_url=preview_url)
    return lead_id


# --- check_site ----------------------------------------------------------------

def test_check_site_persists_audit_when_up(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    lead = db.get_lead(lead_id)
    monkeypatch.setattr(maintenance.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(maintenance.requests, "get", lambda *a, **k: Mock(text=READY_HTML, raise_for_status=Mock()))

    result = maintenance.check_site(lead)

    assert result["readiness_pct"] > 0
    stored = db.get_latest_site_audit(lead_id, "live_client_site")
    assert stored is not None
    assert stored["url"] == "https://joes-cafe.example"


def test_check_site_returns_none_without_a_website(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    lead_id = db.insert_lead("No Site Co", "cafe", "Leeds")
    assert maintenance.check_site(db.get_lead(lead_id)) is None
    assert db.get_latest_site_audit(lead_id, "live_client_site") is None


def test_check_site_alerts_when_down(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    lead = db.get_lead(lead_id)
    monkeypatch.setattr(
        maintenance.url_safety, "validate_public_url",
        Mock(side_effect=RuntimeError("URL returned HTTP 503")),
    )
    alert_mock = Mock()
    monkeypatch.setattr(maintenance, "_alert", alert_mock)

    result = maintenance.check_site(lead)

    assert result["readiness_pct"] == 0
    assert "unreachable" in result["issues"][0].lower()
    alert_mock.assert_called_once()
    assert "DOWN" in alert_mock.call_args.args[1]


def test_check_site_alerts_on_readiness_regression(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    lead = db.get_lead(lead_id)
    # Seed a prior "healthy" audit.
    db.insert_site_audit(lead_id, "live_client_site", {"readiness_pct": 100, "issues": []}, url="https://joes-cafe.example")

    broken_html = "<html><body>no head at all</body></html>"
    monkeypatch.setattr(maintenance.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(maintenance.requests, "get", lambda *a, **k: Mock(text=broken_html, raise_for_status=Mock()))
    alert_mock = Mock()
    monkeypatch.setattr(maintenance, "_alert", alert_mock)

    result = maintenance.check_site(lead)

    assert result["readiness_pct"] < 100
    alert_mock.assert_called_once()
    assert "readiness dropped" in alert_mock.call_args.args[1]


def test_check_site_no_alert_for_small_fluctuation(tmp_path, monkeypatch):
    """A drop smaller than REGRESSION_READINESS_DROP shouldn't page anyone."""
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    lead = db.get_lead(lead_id)
    db.insert_site_audit(lead_id, "live_client_site", {"readiness_pct": 89, "issues": []}, url="https://joes-cafe.example")

    monkeypatch.setattr(maintenance.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(maintenance.requests, "get", lambda *a, **k: Mock(text=READY_HTML, raise_for_status=Mock()))
    alert_mock = Mock()
    monkeypatch.setattr(maintenance, "_alert", alert_mock)

    maintenance.check_site(lead)
    alert_mock.assert_not_called()


# --- run_check: only 'won' leads -------------------------------------------------

def test_run_check_only_processes_won_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    won_id = _setup_won_lead(tmp_path, monkeypatch)
    other_id = db.insert_lead("Not Won Co", "cafe", "Leeds")
    db.insert_website(lead_id=other_id, template_niche="cafe", repo_url="", repo_full_name="", preview_url="https://other.example")

    checked = []
    monkeypatch.setattr(maintenance, "check_site", lambda lead: checked.append(lead["id"]))
    maintenance.run_check()

    assert checked == [won_id]


# --- monthly_report --------------------------------------------------------------

def test_monthly_report_uses_only_real_tracked_data(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    db.insert_site_audit(
        lead_id, "live_client_site",
        {"readiness_pct": 90, "issues": ["1 broken link"], "checks_performed": 9, "checks_total": 9, "checks_skipped": []},
        url="https://joes-cafe.example",
    )
    db.log_click(lead_id)
    db.log_click(lead_id)

    report = maintenance.monthly_report(lead_id)

    assert "Joes Cafe" in report
    assert "Uptime: 100%" in report
    assert "Current basic publishing checks: 90% (9/9 checks performed)" in report
    assert "Not a WCAG compliance certification" in report
    assert "1 broken link" in report
    assert "opened 2 time(s)" in report
    assert "does not integrate real visitor" in report
    # The disclaimer itself is allowed to name "enquiries"/"visitors" (it's
    # explaining what's NOT tracked) -- what must never appear is a
    # fabricated number claimed alongside them, e.g. "14 enquiries".
    assert not re.search(r"\d+\s+enquir", report, re.IGNORECASE)
    assert not re.search(r"\d+\s+visitor", report, re.IGNORECASE)


def test_monthly_report_notes_skipped_checks(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    db.insert_site_audit(
        lead_id, "live_client_site",
        {"readiness_pct": 100, "issues": [], "checks_performed": 8, "checks_total": 9,
         "checks_skipped": ["no_broken_links"]},
        url="https://joes-cafe.example",
    )
    report = maintenance.monthly_report(lead_id)
    assert "Current basic publishing checks: 100% (8/9 checks performed, 1 skipped)" in report


def test_monthly_report_handles_no_audits_yet(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    report = maintenance.monthly_report(lead_id)
    assert "No automated checks have run yet" in report


def test_monthly_report_excludes_audits_outside_the_window(tmp_path, monkeypatch):
    lead_id = _setup_won_lead(tmp_path, monkeypatch)
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO site_audits (lead_id, audited_target, url, score, readiness_pct, result, created_at) "
            "VALUES (?, 'live_client_site', ?, NULL, 50, ?, ?)",
            (lead_id, "https://joes-cafe.example", '{"readiness_pct": 50, "issues": []}',
             (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")),
        )
    report = maintenance.monthly_report(lead_id, window_days=30)
    assert "No automated checks have run yet" in report


def test_monthly_report_raises_for_unknown_lead(tmp_path, monkeypatch):
    monkeypatch.setattr(db.config, "DB_PATH", str(tmp_path / "test.db"))
    db.init_db()
    with pytest.raises(ValueError, match="No lead 999"):
        maintenance.monthly_report(999)
