"""Commander decisions of 2026-09-05 wired into code: two-option pricing,
guarantee wording, one follow-up reminder, monthly-plan escalation, and the
per-niche outcome report."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

import config
from agents import sales_agent
from utils import db


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "SENDGRID_API_KEY", "")
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_MONTHLY_PRICE", 39)
    monkeypatch.setattr(config, "GUARANTEE_DAYS", 14)
    monkeypatch.setattr(config, "FREE_EDITS_DAYS", 30)
    monkeypatch.setattr(config, "FOLLOW_UP_ENABLED", True)
    monkeypatch.setattr(config, "FOLLOW_UP_AFTER_DAYS", 3)
    monkeypatch.setattr(config, "WEBSITE_OFFER_PRICE", 589)
    monkeypatch.setattr(config, "CURRENCY_SYMBOL", "£")
    monkeypatch.setattr(sales_agent, "_next_send_allowed_at", datetime.min.replace(tzinfo=timezone.utc))
    db.init_db()
    return tmp_path


def _lead(status="emailed", email="owner@acme.test") -> int:
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Maidstone, Kent", status=status)
    db.update_lead_fields(lead_id, contact_email=email)
    return lead_id


def _outbound(lead_id: int, days_ago: int) -> None:
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO email_threads (lead_id, direction, subject, body, from_addr, to_addr, message_id, timestamp) "
            "VALUES (?, 'outbound', 's', 'b', 'casey@x.test', 'owner@acme.test', ?, datetime('now', ?))",
            (lead_id, f"<{uuid.uuid4()}@t>", f"-{days_ago} days"),
        )


def test_cold_email_states_both_prices_and_the_guarantee():
    paras = sales_agent._closing_paragraphs("https://preview.test")
    text = "\n".join(paras)
    assert "£589 one-off" in text and "£39/month" in text
    assert "14-day money-back" in text and "first 30 days are free" in text
    assert "attach them to your reply" in text  # photos/logo offer kept


def test_follow_up_goes_only_to_silent_leads_after_the_wait(env, monkeypatch):
    sent = []
    monkeypatch.setattr(sales_agent, "_send_thread_reply", lambda lead, body, classification="": sent.append((lead["id"], classification)) or True)
    monkeypatch.setattr(sales_agent, "_can_send_now", lambda: True)
    fresh = _lead(); _outbound(fresh, 1)                      # too recent
    silent = _lead(email="b@acme.test"); _outbound(silent, 4)  # due
    replied = _lead(email="c@acme.test"); _outbound(replied, 5)
    db.insert_email_thread(lead_id=replied, direction="inbound", subject="re", body="hi", from_addr="c@acme.test",
                           to_addr="casey@x.test", message_id="<in@t>")
    assert [l["id"] for l in db.list_follow_up_due(3)] == [silent]
    assert sales_agent.send_follow_up_if_due() == silent
    assert sent == [(silent, "follow_up")]
    assert db.get_lead(silent)["follow_up_sent_at"] is not None
    assert sales_agent.send_follow_up_if_due() is None  # never twice


def test_follow_up_respects_the_send_gate(env, monkeypatch):
    lead = _lead(); _outbound(lead, 10)
    monkeypatch.setattr(sales_agent, "_can_send_now", lambda: False)
    assert sales_agent.send_follow_up_if_due() is None
    assert db.get_lead(lead)["follow_up_sent_at"] is None


def test_monthly_plan_reply_is_handed_to_a_human_not_negotiated(env, monkeypatch):
    lead_id = _lead(status="emailed")
    replies, alerts = [], []
    monkeypatch.setattr(sales_agent, "_send_thread_reply", lambda lead, body, classification="": replies.append(classification) or True)
    monkeypatch.setattr(sales_agent, "alert_needs_human", lambda lead, msg: alerts.append(msg))
    monkeypatch.setattr(sales_agent, "alert_positive_reply", lambda lead: None)
    monkeypatch.setattr(sales_agent, "_run_negotiation_round", Mock(side_effect=AssertionError("must not negotiate")))
    msg = email_utils_inbound("Yes please, I'd rather pay monthly if that's ok?")
    sales_agent._handle_inbound_impl(db.get_lead(lead_id), msg)
    assert replies == ["subscription_interest"]
    assert alerts and "monthly plan" in alerts[0]
    assert db.get_lead(lead_id)["status"] == "negotiating"


def email_utils_inbound(body: str):
    from utils.email_utils import InboundEmail
    return InboundEmail(message_id=f"<{uuid.uuid4()}@t>", in_reply_to="", subject="Re: I built a website for Acme Plumbing",
                        from_addr="owner@acme.test", to_addr="casey@x.test", body=body, content_type="text/plain",
                        date="now", account_user="casey@x.test")


def test_outcome_report_groups_by_niche_with_revenue(env):
    won = _lead(status="won"); _outbound(won, 2)
    db.update_lead_fields(won, won_amount=589)
    _lead(status="designed", email="d@acme.test")
    rows = {r["niche"]: r for r in db.outcome_report()}
    assert rows["plumber"]["leads"] == 2 and rows["plumber"]["won"] == 1 and rows["plumber"]["revenue"] == 589
