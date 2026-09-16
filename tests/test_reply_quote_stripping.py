"""A reply is classified on the prospect's own words, never on the message
they quoted underneath it.

Every email we send ends "Unsubscribe: <link>", and every mail client pastes
the message being answered under the reply. Before this, a Gmail "Yes please"
with the cold email quoted below it was read as an opt-out: marked lost, sent
a goodbye, and suppressed for life -- and the quoted "£39/month" line routed
every other positive reply to the monthly-plan hand-off instead of negotiation.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import config
from agents import sales_agent
from utils import db, email_utils

COLD_EMAIL_QUOTED = (
    "On Tue, 9 Sep 2026 at 18:07, Casey <casey@caseywebsites.com> wrote:\n"
    "> Hi, I put together a draft website for Acme Plumbing.\n"
    "> It's £589 one-off or £39/month with hosting included.\n"
    ">\n"
    "> --\n"
    "> Unit 1, PO Box 1, Poole\n"
    "> You can opt out anytime -- no hard feelings.\n"
    "> Unsubscribe: https://example.ngrok-free.dev/unsubscribe/abc123\n"
)


# --- strip_quoted_text ----------------------------------------------------------

def test_gmail_quote_is_stripped():
    body = "Yes please, how do I pay?\n\n" + COLD_EMAIL_QUOTED
    assert email_utils.strip_quoted_text(body) == "Yes please, how do I pay?"


def test_wrapped_gmail_header_outlook_block_and_zoho_rule_are_stripped():
    wrapped = ("Sounds good\n\nOn Tue, 9 Sep 2026 at 18:07, Casey <casey@caseywebsites.com>\nwrote:\n"
               "> Unsubscribe: https://x/unsubscribe/1\n")
    assert email_utils.strip_quoted_text(wrapped) == "Sounds good"

    outlook = ("Not sure yet\r\n\r\nFrom: Casey <casey@caseywebsites.com>\r\nSent: 09 September 2026 18:07\r\n"
               "To: owner@acme.test\r\nSubject: a draft website\r\n\r\nUnsubscribe: https://x/unsubscribe/1\r\n")
    assert email_utils.strip_quoted_text(outlook) == "Not sure yet"

    zoho = ("Ok\n---- On Tue, 09 Sep 2026 18:07:00 +0100 Casey <casey@caseywebsites.com> wrote ----\n"
            " Unsubscribe: https://x/unsubscribe/1")
    assert email_utils.strip_quoted_text(zoho) == "Ok"


def test_unquoted_footer_copy_is_cut_and_plain_replies_are_untouched():
    unquoted = "Interested.\n\nYou can opt out anytime -- no hard feelings.\nUnsubscribe: https://x/u/1"
    assert email_utils.strip_quoted_text(unquoted) == "Interested."
    assert email_utils.strip_quoted_text("  How much is it?  ") == "How much is it?"
    # A sentence that merely starts with "On" is not a quote header.
    assert email_utils.strip_quoted_text("On Monday I wrote: the site looks good") == "On Monday I wrote: the site looks good"


def test_a_quote_only_reply_falls_back_to_the_full_text():
    assert email_utils.strip_quoted_text(COLD_EMAIL_QUOTED) == COLD_EMAIL_QUOTED.strip()
    assert email_utils.strip_quoted_text("") == ""


# --- classify_reply -------------------------------------------------------------

def test_quoted_footer_no_longer_reads_as_an_opt_out():
    assert sales_agent.classify_reply("Re: a draft website", "Yes please, how do I pay?\n\n" + COLD_EMAIL_QUOTED) == "positive"
    assert sales_agent.classify_reply("Re: a draft website", "Please unsubscribe me.\n\n" + COLD_EMAIL_QUOTED) == "negative"


def test_a_bare_stop_is_not_an_opt_out():
    assert sales_agent.classify_reply("Re:", "Yes please, can you stop by the shop on Friday?") == "positive"
    assert sales_agent.classify_reply("Re:", "Please stop emailing me") == "negative"
    assert sales_agent.classify_reply("Re:", "please stop") == "negative"


# --- _handle_inbound_impl -------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "SENDGRID_API_KEY", "")
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", True)
    db.init_db()


def _inbound(body: str) -> email_utils.InboundEmail:
    return email_utils.InboundEmail(
        message_id="<m@t>", in_reply_to="", subject="Re: a draft website", from_addr="owner@acme.test",
        to_addr="casey@caseywebsites.com", body=body, content_type="text/plain", date="",
    )


def _lead(status: str) -> dict:
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Maidstone, Kent", status=status)
    db.update_lead_fields(lead_id, contact_email="owner@acme.test")
    return db.get_lead(lead_id)


def test_a_quoted_yes_goes_to_negotiation_not_the_bin(env, monkeypatch):
    rounds: list[str] = []
    monkeypatch.setattr(sales_agent, "_run_negotiation_round",
                        lambda lead, body, github_captured=False: rounds.append(body))
    monkeypatch.setattr(sales_agent, "_send_goodbye", Mock(side_effect=AssertionError("no goodbye")))
    monkeypatch.setattr(sales_agent, "alert_needs_human",
                        Mock(side_effect=AssertionError("not the monthly-plan hand-off either")))
    lead = _lead("emailed")

    sales_agent._handle_inbound_impl(lead, _inbound("Yes please, how do I pay?\n\n" + COLD_EMAIL_QUOTED))

    assert db.get_lead(lead["id"])["status"] == "negotiating"
    assert rounds == ["Yes please, how do I pay?"]   # the model sees their words, not our email
    assert not db.is_unsubscribed("owner@acme.test")
    threads = db.get_email_threads(lead["id"])
    assert "Unsubscribe:" in threads[-1]["body"]      # the full message is still what gets logged


def test_a_customer_with_a_checkout_link_is_never_demoted_by_a_keyword(env, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(sales_agent, "alert_needs_human", lambda lead, reason: alerts.append(reason))
    monkeypatch.setattr(sales_agent, "_send_goodbye", Mock(side_effect=AssertionError("no goodbye")))
    lead = _lead("payment_sent")

    sales_agent._handle_inbound_impl(lead, _inbound("Not interested at that price, can you do 500?"))

    assert db.get_lead(lead["id"])["status"] == "payment_sent"
    assert not db.is_unsubscribed("owner@acme.test")
    assert alerts and "checkout link" in alerts[0]


def test_a_real_decline_is_still_honoured_and_a_person_is_told(env, monkeypatch):
    monkeypatch.setattr(sales_agent, "_send_goodbye", Mock())
    posted: list[str] = []
    monkeypatch.setattr(sales_agent, "_slack_notify", lambda text: posted.append(text) or True)
    lead = _lead("emailed")

    sales_agent._handle_inbound_impl(lead, _inbound("No thanks, take me off your list.\n\n" + COLD_EMAIL_QUOTED))

    assert db.get_lead(lead["id"])["status"] == "lost"
    assert db.is_unsubscribed("owner@acme.test")
    sales_agent._send_goodbye.assert_called_once()
    assert any("DECLINED" in text and "take me off" in text for text in posted)
