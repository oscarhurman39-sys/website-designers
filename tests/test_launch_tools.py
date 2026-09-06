"""Unit tests for the launch gate (ENABLE_LIVE_SEND), env-driven caps, and
pipeline/launch.py's preflight / render-previews / reset-db. No network."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import config
import launch
from agents import design_agent, sales_agent
from utils import db, email_utils


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "DRY_RUN_DIR", str(tmp_path / "dry_run"))
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "PHYSICAL_ADDRESS", "1 Test St")
    monkeypatch.setattr(config, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(config, "EMAIL_USER", "sender@example.com")
    monkeypatch.setattr(config, "SENDING_DOMAIN", "example.com")
    monkeypatch.setattr(config, "EMAIL_HOST", "smtp.example.com")
    db.init_db()
    return tmp_path


# --- ENABLE_LIVE_SEND transport gate ------------------------------------------------

def test_smtp_dry_run_writes_eml_and_never_connects(isolated, monkeypatch):
    smtp = Mock(side_effect=AssertionError("SMTP must not be used in dry run"))
    monkeypatch.setattr(email_utils.smtplib, "SMTP", smtp)

    message_id = email_utils.send_email("owner@example.com", "Hi", "Body", lead_id=7)

    files = list((isolated / "dry_run").glob("*.eml"))
    assert message_id.startswith("<") and len(files) == 1
    content = files[0].read_text()
    assert "List-Unsubscribe" in content and "owner@example.com" in content and "1 Test St" in content
    smtp.assert_not_called()


def test_smtp_live_send_uses_smtp_and_writes_nothing(isolated, monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    server = Mock()
    smtp = Mock(return_value=Mock(__enter__=Mock(return_value=server), __exit__=Mock(return_value=False)))
    monkeypatch.setattr(email_utils.smtplib, "SMTP", smtp)

    email_utils.send_email("owner@example.com", "Hi", "Body", lead_id=7)

    server.sendmail.assert_called_once()
    assert not (isolated / "dry_run").exists()


def test_sendgrid_dry_run_writes_json_and_never_calls_api(isolated, monkeypatch):
    monkeypatch.setattr(config, "SENDGRID_API_KEY", "SG.fake")
    monkeypatch.setattr(config, "SENDGRID_FROM_EMAIL", "sender@example.com")
    client = Mock(side_effect=AssertionError("SendGrid must not be used in dry run"))
    monkeypatch.setattr(email_utils, "SendGridAPIClient", client)

    email_utils.send_email_sendgrid("owner@example.com", "Hi", "Body", lead_id=7)

    files = list((isolated / "dry_run").glob("*.json"))
    assert len(files) == 1 and "owner@example.com" in files[0].read_text()
    client.assert_not_called()


def test_dry_run_still_refuses_unsubscribed(isolated, monkeypatch):
    monkeypatch.setattr(email_utils.smtplib, "SMTP", Mock())
    lead_id = db.insert_lead("Anyone", "cafe", "Town")
    db.mark_unsubscribed(lead_id, "gone@example.com")
    with pytest.raises(RuntimeError):
        email_utils.send_email("gone@example.com", "Hi", "Body", lead_id=lead_id)
    assert not (isolated / "dry_run").exists()


def test_cold_email_dry_run_marks_lead_with_dry_run_note(isolated, monkeypatch):
    lead_id = db.insert_lead("Example Co", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="owner@example.com")
    lead = db.get_lead(lead_id)
    monkeypatch.setattr(sales_agent.db, "get_website_by_lead", Mock(return_value={"preview_url": "https://x.vercel.app"}))
    monkeypatch.setattr(sales_agent, "_validate_preview_link_for_send", lambda url: url)
    monkeypatch.setattr(sales_agent.screenshot, "get_cached_screenshot", Mock(return_value=None))
    monkeypatch.setattr(email_utils.smtplib, "SMTP", Mock(side_effect=AssertionError("no SMTP")))

    assert sales_agent._send_cold_email_impl(lead) is True

    assert db.get_lead(lead_id)["status"] == "emailed"
    assert db.list_dry_run_emailed_lead_ids() == [lead_id]
    assert len(list((isolated / "dry_run").glob("*.eml"))) == 1


# --- env-driven caps ----------------------------------------------------------------

def test_env_helpers(monkeypatch):
    monkeypatch.setenv("X_BOOL", "TRUE")
    monkeypatch.setenv("X_INT", "7")
    assert config._env_bool("X_BOOL") is True
    assert config._env_bool("X_MISSING", default=False) is False
    assert config._env_int("X_INT", 99) == 7
    assert config._env_int("X_MISSING", 99) == 99


def test_default_daily_cap_is_first_week_safe():
    assert config.FIRST_WEEK_MAX_PER_DAY == 10


# --- preflight --------------------------------------------------------------------

def _levels(checks, label):
    return [level for level, name, _ in checks if name == label]


def test_preflight_flags_test_data_and_dry_run_leads(isolated):
    db.insert_lead("Sunrise Cafe", "cafe", "Austin")
    lead_id = db.insert_lead("Real Plumbing Ltd", "plumber", "Leeds")
    db.update_lead_status(lead_id, "emailed", notes="DRY RUN -- not sent")

    checks = launch.check_database()

    assert _levels(checks, "Test data") == [launch.WARN]
    assert _levels(checks, "Dry-run leads") == [launch.WARN]


def test_preflight_blocks_live_send_on_localhost(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:5000")
    assert _levels(launch.check_env(), "PUBLIC_BASE_URL") == [launch.BLOCK]

    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    assert _levels(launch.check_env(), "PUBLIC_BASE_URL") == [launch.WARN]


def test_preflight_warns_when_caps_exceed_first_week(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 50)
    assert _levels(launch.check_send_gates(), "Email caps") == [launch.WARN]
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 10)
    assert _levels(launch.check_send_gates(), "Email caps") == [launch.OK]


def test_preflight_templates_all_render():
    checks = launch.check_templates()
    assert _levels(checks, "Templates") == [launch.OK]


def test_preflight_exit_code_reflects_blocks(isolated, monkeypatch, capsys):
    monkeypatch.setattr(launch, "check_env", lambda: [launch._check(launch.BLOCK, "Required env", "missing: X")])
    assert launch.run_preflight() == 1
    monkeypatch.setattr(launch, "check_env", lambda: [launch._check(launch.OK, "Required env", "")])
    assert launch.run_preflight() == 0
    assert "Launch preflight" in capsys.readouterr().out


# --- render-previews ---------------------------------------------------------------

def test_render_previews_writes_every_template(isolated, tmp_path):
    out = tmp_path / "previews"
    assert launch.render_previews(out_dir=out) == 0
    for niche in design_agent.available_niches():
        html = (out / niche / "index.html").read_text(encoding="utf-8")
        assert html.strip() and "{{" not in html and (out / niche / "style.css").exists()
    assert (out / "index.html").exists()


def test_render_previews_unknown_niche(isolated, tmp_path):
    assert launch.render_previews(niche="does-not-exist", out_dir=tmp_path) == 1


# --- reset-db ----------------------------------------------------------------------

def test_reset_db_requires_yes(isolated):
    db.insert_lead("Sunrise Cafe", "cafe", "Austin")
    assert launch.reset_db(yes=False) == 1
    assert len(db.list_all_leads()) == 1


def test_reset_db_backs_up_and_keeps_unsubscribes(isolated):
    lead_id = db.insert_lead("Sunrise Cafe", "cafe", "Austin")
    db.mark_unsubscribed(lead_id, "stop@example.com")

    assert launch.reset_db(yes=True) == 0

    assert db.list_all_leads() == []
    assert db.is_unsubscribed("stop@example.com")
    assert len(list(isolated.glob("leads.db.bak-*"))) == 1


def test_reset_db_can_drop_unsubscribes(isolated):
    lead_id = db.insert_lead("Sunrise Cafe", "cafe", "Austin")
    db.mark_unsubscribed(lead_id, "stop@example.com")
    assert launch.reset_db(yes=True, drop_unsubscribes=True) == 0
    assert not db.is_unsubscribed("stop@example.com")
