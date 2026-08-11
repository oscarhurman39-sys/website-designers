"""Guards on how inbound replies are classified and acted on.

Three regressions this locks down, all of which shipped:

1. A bare r"\\bstop\\b" in the negative patterns matched "non-stop busy",
   "don't stop" and "can you stop by next week" — marking buying signals as
   rejections, killing the lead and mailing it a goodbye.
2. An opt-out reply ("remove me from your list") ended only that one lead. The
   address was never added to the suppression list, so the same person could
   be emailed again from any other lead carrying it.
3. r"\\b\\bOOO\\b" was matched against lowercased text, so the bare OOO acronym
   never matched and an auto-reply was treated as a positive reply.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agents import sales_agent
from agents.sales_agent import classify_reply
from utils import db


@pytest.fixture()
def emailed_lead():
    """A lead that has been sent its cold email, on the throwaway test DB."""
    db.init_db()
    lead_id = db.insert_lead("Barlow Landscapes", "landscaper", "Leeds")
    db.update_lead_fields(lead_id, contact_email="info@barlow-landscapes.co.uk")
    for status in ("researched", "designed", "emailed"):
        db.update_lead_status(lead_id, status)
    return lead_id


def _inbound(from_addr: str, body: str, subject: str = "Re: your email"):
    return SimpleNamespace(
        message_id=f"<{from_addr}-{hash(body)}@test>", subject=subject, body=body,
        from_addr=from_addr, to_addr="me@test.invalid", content_type="text/plain",
    )


# --- 1. buying signals containing "stop" must not read as rejections ---------

BUYING_SIGNALS_WITH_STOP = [
    "Sorry for the delay, been non-stop busy. Looks great - can you stop by next week?",
    "Yes please, tell me more! Don't stop now.",
    "This is a full-stop yes from me, what's the next step?",
]


def test_buying_signals_containing_stop_are_not_negative():
    for body in BUYING_SIGNALS_WITH_STOP:
        assert classify_reply("Re: your email", body) != "negative", body


def test_buying_signals_containing_stop_are_not_optout():
    for body in BUYING_SIGNALS_WITH_STOP:
        assert classify_reply("Re: your email", body) != "optout", body


# --- 2. explicit opt-outs are their own class, and outrank everything --------

OPT_OUTS = [
    "Please remove me from your list.",
    "Unsubscribe.",
    "Take me off this list please.",
    "Do not contact me again.",
    "Please stop emailing me.",
    "Opt me out.",
]


def test_explicit_optouts_classify_as_optout():
    for body in OPT_OUTS:
        assert classify_reply("Re: your email", body) == "optout", body


def test_optout_beats_out_of_office():
    """An auto-reply that also asks to be removed is still an opt-out."""
    assert classify_reply(
        "Out of office", "I am on annual leave. Also please remove me from your list."
    ) == "optout"


# --- 3. out-of-office detection, including the bare acronym -----------------

def test_bare_ooo_acronym_is_detected():
    assert classify_reply("Re: your email", "Hi, I am OOO till Weds.") == "out_of_office"


def test_out_of_office_phrases_are_detected():
    for body in ["I am out of the office until Monday.",
                 "This is an automatic reply.",
                 "I'm on annual leave right now."]:
        assert classify_reply("Re: your email", body) == "out_of_office", body


# --- soft declines stay 'negative' (lead lost, address NOT suppressed) ------

def test_soft_declines_are_negative_not_optout():
    for body in ["Not interested, thanks.", "No thanks.", "We're all set."]:
        assert classify_reply("Re: your email", body) == "negative", body


# --- ambiguity still defaults to positive so no human reply is dropped ------

def test_ambiguous_reply_defaults_to_positive():
    assert classify_reply("Re: your email", "Who is this?") == "positive"


def test_clear_interest_is_positive():
    for body in ["Interested, how much?", "Sounds good, let's talk."]:
        assert classify_reply("Re: your email", body) == "positive", body


# --- the opt-out must actually suppress the address, end to end -------------

def test_reply_optout_suppresses_the_address(emailed_lead):
    email = "info@barlow-landscapes.co.uk"
    assert not db.is_unsubscribed(email)

    sales_agent._handle_inbound_impl(
        db.get_lead(emailed_lead), _inbound(email, "Please remove me from your list.")
    )

    assert db.is_unsubscribed(email), "an opt-out reply must suppress the address itself"
    assert db.get_lead(emailed_lead)["status"] == "unsubscribed"


def test_reply_optout_sends_no_further_email(emailed_lead):
    """Someone who asked to be left alone gets silence, not one more email."""
    email = "info@barlow-landscapes.co.uk"
    sales_agent._handle_inbound_impl(
        db.get_lead(emailed_lead), _inbound(email, "Unsubscribe.")
    )
    directions = [t["direction"] for t in db.get_email_threads(emailed_lead)]
    assert "outbound" not in directions


# --- a reply from a colleague's mailbox still finds the lead ----------------

def test_reply_from_same_domain_matches_the_lead(emailed_lead):
    """You mail info@; a person answers from their own address at that domain."""
    found = db.get_lead_by_email_domain("jo@barlow-landscapes.co.uk")
    assert found is not None and found["id"] == emailed_lead


def test_domain_match_is_case_insensitive(emailed_lead):
    assert db.get_lead_by_email_domain("Jo@Barlow-Landscapes.CO.UK") is not None


def test_domain_match_ignores_free_mail_providers(emailed_lead):
    """A gmail.com match would attach any stranger to a lead."""
    for addr in ["jo@gmail.com", "jo@outlook.com", "jo@btinternet.com"]:
        assert db.get_lead_by_email_domain(addr) is None, addr


def test_domain_match_ignores_unknown_domains(emailed_lead):
    assert db.get_lead_by_email_domain("jo@somewhere-else.com") is None


def test_domain_match_only_considers_contacted_leads():
    """A lead that was never emailed can't be the source of a reply."""
    db.init_db()
    lead_id = db.insert_lead("Never Emailed Ltd", "cafe", "Leeds")
    db.update_lead_fields(lead_id, contact_email="hi@never-emailed.co.uk")
    assert db.get_lead_by_email_domain("jo@never-emailed.co.uk") is None
