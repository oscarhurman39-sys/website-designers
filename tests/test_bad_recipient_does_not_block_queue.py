"""One unsendable address must not stall the whole send queue.

On 2026-09-11 lead 55's contact email was the literal string "null" (a site
builder's empty mailto:). It sat first in priority order, Zoho refused it
with 553 on every 60 s cycle, and the exception ended the send stage before
any of the 25 real leads behind it were tried. Nothing went out for hours.
"""
from __future__ import annotations

import smtplib
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup

from agents import lead_agent, sales_agent
from utils import email_verify


@pytest.mark.parametrize("bad", ["null", "undefined", "", "owner@", "@example.com", "not an email", "a@b"])
def test_verify_email_rejects_non_addresses(bad):
    assert email_verify.verify_email(bad) is False


@pytest.mark.parametrize("good", ["owner@example.co.uk", " info@cafe.com ", "first.last+tag@sub.domain.org"])
def test_verify_email_accepts_real_addresses(good):
    assert email_verify.verify_email(good) is True


def test_extract_email_skips_placeholder_mailto():
    soup = BeautifulSoup('<a href="mailto:null">Email us</a>', "html.parser")
    assert lead_agent._extract_email(soup) is None


def test_extract_email_keeps_real_mailto():
    soup = BeautifulSoup('<a href="mailto:hello@cafe.co.uk?subject=hi">Email us</a>', "html.parser")
    assert lead_agent._extract_email(soup) == "hello@cafe.co.uk"


def _lead(email: str, lead_id: int = 55) -> dict:
    return {"id": lead_id, "business_name": "N R Cafe", "contact_email": email, "location": "Manchester", "niche": "cafe"}


def _stub_send_path(monkeypatch) -> Mock:
    """Everything around the transport call, so the send path runs end to
    end with no DB, no network and no real mailbox."""
    monkeypatch.setattr(sales_agent.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(sales_agent.db, "last_cold_email_at", Mock(return_value=None))
    monkeypatch.setattr(sales_agent.db, "get_website_by_lead", Mock(return_value={"preview_url": "https://x.vercel.app"}))
    monkeypatch.setattr(sales_agent, "_validate_preview_link_for_send", lambda link: link)
    monkeypatch.setattr(sales_agent.screenshot, "get_cached_screenshot", Mock(return_value=None))
    monkeypatch.setattr(sales_agent, "_sender_for", Mock(return_value=sales_agent.config.EMAIL_ACCOUNTS[0]))
    monkeypatch.setattr(sales_agent.db, "update_lead_status", Mock())
    monkeypatch.setattr(sales_agent.db, "insert_email_thread", Mock())
    monkeypatch.setattr(sales_agent, "_pin_sender", Mock())
    transport = Mock(return_value="<msg-id@example.com>")
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport", transport)
    return transport


def test_malformed_address_marks_lead_lost_without_sending(monkeypatch):
    transport = _stub_send_path(monkeypatch)

    assert sales_agent._send_cold_email_impl(_lead("null")) is False

    transport.assert_not_called()
    assert sales_agent.db.update_lead_status.call_args.args[:2] == (55, "lost")


def test_recipient_refused_marks_lead_bounced_and_returns_false(monkeypatch):
    transport = _stub_send_path(monkeypatch)
    transport.side_effect = smtplib.SMTPRecipientsRefused({"null": (553, b"Recipient domain not specified.")})

    assert sales_agent._send_cold_email_impl(_lead("owner@example.com")) is False

    assert sales_agent.db.update_lead_status.call_args.args[:2] == (55, "bounced")
    sales_agent.db.insert_email_thread.assert_not_called()


def test_other_smtp_failures_still_raise(monkeypatch):
    """A broken mailbox must stop the stage, not be retried against every
    queued lead in one cycle."""
    transport = _stub_send_path(monkeypatch)
    transport.side_effect = smtplib.SMTPAuthenticationError(535, b"bad password")

    with pytest.raises(smtplib.SMTPAuthenticationError):
        sales_agent._send_cold_email_impl(_lead("owner@example.com"))


def test_queue_moves_past_a_bad_lead(monkeypatch):
    """The real send_next_pending -> send_cold_email -> impl chain: the bad
    lead is marked lost and the good one behind it is actually sent."""
    transport = _stub_send_path(monkeypatch)
    monkeypatch.setattr(sales_agent, "_can_send_now", lambda: True)
    monkeypatch.setattr(sales_agent, "_public_links_will_work", lambda: True)
    bad, good = _lead("null"), _lead("owner@example.com", lead_id=56)
    monkeypatch.setattr(sales_agent.db, "list_sendable_leads_by_priority", Mock(return_value=[bad, good]))

    assert sales_agent.send_next_pending() == 56

    assert transport.call_count == 1
    assert transport.call_args.kwargs["to_addr"] == "owner@example.com"
    statuses = [c.args[:2] for c in sales_agent.db.update_lead_status.call_args_list]
    assert (55, "lost") in statuses
    assert (56, "emailed") in statuses
