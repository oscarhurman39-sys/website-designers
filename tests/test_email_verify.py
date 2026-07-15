from __future__ import annotations

import pytest

from utils import email_verify


@pytest.mark.parametrize(
    "addr",
    [
        "owner@example.com",
        "first.last@example.co.uk",
        "user+tag@sub.domain.org",
        "USER_99%x@example.io",
    ],
)
def test_verify_email_accepts_real_addresses(addr):
    assert email_verify.verify_email(addr) is True


@pytest.mark.parametrize(
    "addr",
    [
        "",
        "info@",
        "@example.com",
        "user@localhost",
        "not-an-email",
        "user@@example.com",
        "user@ example.com",
        ".user@example.com",
        "user.@example.com",
        "us..er@example.com",
        "user@.example.com",
        "user@-example.com",
        "user@exa..mple.com",
        "a" * 65 + "@example.com",
        "user@" + "a" * 250 + ".com",
    ],
)
def test_verify_email_rejects_malformed_addresses(addr):
    assert email_verify.verify_email(addr) is False


def test_send_cold_email_blocks_invalid_contact_email(monkeypatch):
    from unittest.mock import Mock

    from agents import sales_agent

    lead = {"id": 7, "business_name": "Example Co", "contact_email": "info@", "location": "Leeds"}

    monkeypatch.setattr(sales_agent.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(sales_agent.db, "update_lead_status", Mock())
    monkeypatch.setattr(sales_agent, "_send_via_configured_transport", Mock())

    assert sales_agent._send_cold_email_impl(lead) is False

    sales_agent._send_via_configured_transport.assert_not_called()
    assert sales_agent.db.update_lead_status.call_args.args[1] == "lost"
