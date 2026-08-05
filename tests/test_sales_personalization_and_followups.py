"""Tests for the audit-driven email personalization and the follow-up
sequence -- all offline (no HF, no SMTP, no network)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from agents import sales_agent
from utils import email_utils


def _lead(**extra) -> dict:
    lead = {
        "id": 42, "business_name": "Example Co", "niche": "plumber",
        "location": "Leeds", "contact_email": "owner@example.com",
        "status": "emailed", "pain_point": "", "site_audit": "",
    }
    lead.update(extra)
    return lead


# --- Drafting fallback ----------------------------------------------------

def test_draft_cold_email_is_deterministic_without_hf_token(monkeypatch):
    monkeypatch.setattr(sales_agent.config, "HF_API_TOKEN", "")
    subject, intro = sales_agent.draft_cold_email(_lead())
    assert subject == "I built a website for Example Co"
    assert "Example Co" in intro


def test_intro_line_uses_audit_finding():
    audit = {"pain_points": ["the site takes 6.0s to respond -- most visitors on a phone give up"]}
    lead = _lead(site_audit=json.dumps(audit))
    intro = sales_agent._intro_line(lead)
    assert "6.0s" in intro
    # The old copy claimed they had no website; that claim must be gone.
    assert "only find your Google" not in intro


def test_intro_line_stays_honest_without_audit():
    intro = sales_agent._intro_line(_lead(website_url="http://example.com"))
    assert "only find your Google" not in intro
    assert "Example Co" in intro


# --- Audit-driven checklist ---------------------------------------------------

def test_checklist_leads_with_audit_improvements():
    audit = {"improvements": [
        "Secure HTTPS connection (your current site shows 'Not secure')",
        "Faster page speed (your site took 5.0s to load)",
    ]}
    lead = _lead(site_audit=json.dumps(audit))
    items = sales_agent._checklist_items("Leeds", lead)
    assert items[0].startswith("Secure HTTPS")
    assert items[1].startswith("Faster page speed")
    assert len(items) == 5
    # The generic speed item must not appear alongside the specific one.
    assert "Faster page speed" not in items[2:]


def test_checklist_falls_back_to_standard_items_without_audit():
    items = sales_agent._checklist_items("Leeds", _lead())
    assert "Mobile-friendly design" in items
    assert "Local SEO for Leeds" in items


# --- Follow-up scheduling -------------------------------------------------------

def _outbound(ts: datetime, subject: str = "I built a website for Example Co") -> dict:
    return {
        "direction": "outbound", "subject": subject, "body": "x",
        "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
    }


def test_followup_due_after_gap(monkeypatch):
    four_days_ago = datetime.now(timezone.utc) - timedelta(days=4)
    monkeypatch.setattr(sales_agent.db, "get_email_threads", Mock(return_value=[_outbound(four_days_ago)]))
    assert sales_agent._followup_due_index(_lead()) == 0


def test_followup_not_due_too_early(monkeypatch):
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    monkeypatch.setattr(sales_agent.db, "get_email_threads", Mock(return_value=[_outbound(yesterday)]))
    assert sales_agent._followup_due_index(_lead()) is None


def test_no_followup_after_schedule_exhausted(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    threads = [_outbound(old), _outbound(old + timedelta(days=3)), _outbound(old + timedelta(days=7))]
    monkeypatch.setattr(sales_agent.db, "get_email_threads", Mock(return_value=threads))
    assert sales_agent._followup_due_index(_lead()) is None


def test_followup_copy_replies_to_original_thread(monkeypatch):
    ts = datetime.now(timezone.utc) - timedelta(days=4)
    monkeypatch.setattr(sales_agent.db, "get_email_threads", Mock(return_value=[_outbound(ts)]))
    subject, body = sales_agent._followup_copy(_lead(), 0, "https://x.vercel.app")
    assert subject == "Re: I built a website for Example Co"
    assert "https://x.vercel.app" in body


def test_followup_skips_unsendable_preview(monkeypatch):
    monkeypatch.setattr(sales_agent.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(sales_agent.db, "get_website_by_lead", Mock(return_value={"preview_url": ""}))
    log = Mock()
    monkeypatch.setattr(sales_agent.db, "log_state_history", log)
    send = Mock()
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport", send)

    assert sales_agent._send_followup_impl(_lead(), 0) is False
    send.assert_not_called()
    log.assert_called_once()


# --- Header safety ------------------------------------------------------------

def test_subject_header_injection_is_neutralized():
    hostile = "Joe's Cafe\r\nBcc: everyone@example.com\x00"
    assert email_utils._sanitize_header(hostile) == "Joe's Cafe Bcc: everyone@example.com"
