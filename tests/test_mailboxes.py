"""Multi-mailbox sending: EMAIL_ACCOUNTS parsing, mailbox selection and
per-lead stickiness (config.py, utils/mailboxes.py, utils/email_utils.py and
the agents/sales_agent.py wiring).

Runs against a throwaway SQLite DB with SMTP/IMAP stubbed out -- any attempt
to open a real connection fails the test. No network, no credentials.
"""
from __future__ import annotations

import imaplib
import smtplib
import uuid
from datetime import datetime, timezone

import pytest

import config
from agents import sales_agent
from utils import db, email_utils, mailboxes

PRIMARY = config.EmailAccount(
    user="casey@primary.test", password="pw0", smtp_host="smtp.primary.test",
    smtp_port=587, imap_host="imap.primary.test", display_name="Casey",
)
SECOND = config.EmailAccount(
    user="casey@two.test", password="pw1", smtp_host="smtp.two.test",
    smtp_port=587, imap_host="imap.two.test", display_name="Casey",
)
THIRD = config.EmailAccount(
    user="casey@three.test", password="pw2", smtp_host="smtp.three.test",
    smtp_port=587, imap_host="imap.three.test", display_name="Casey",
)
ACCOUNTS = [PRIMARY, SECOND, THIRD]


# --- EMAIL_ACCOUNTS parsing ----------------------------------------------------

def test_parse_no_extra_accounts_is_just_the_primary():
    assert config.parse_email_accounts("", PRIMARY) == [PRIMARY]
    assert config.parse_email_accounts(" ; ;", PRIMARY) == [PRIMARY]


def test_parse_one_two_field_entry_inherits_primary_hosts():
    accounts = config.parse_email_accounts("b@two.test:pw1", PRIMARY)
    assert [a.user for a in accounts] == [PRIMARY.user, "b@two.test"]
    b = accounts[1]
    assert (b.password, b.smtp_host, b.smtp_port, b.imap_host, b.display_name) == (
        "pw1", "smtp.primary.test", 587, "imap.primary.test", "Casey",
    )


def test_parse_three_entries_keep_order_with_primary_first():
    raw = "b@two.test:pw1; c@three.test:pw2 ;d@four.test:pw3"
    users = [a.user for a in config.parse_email_accounts(raw, PRIMARY)]
    assert users == [PRIMARY.user, "b@two.test", "c@three.test", "d@four.test"]


def test_parse_four_field_entry_allows_colon_in_password():
    b = config.parse_email_accounts("b@two.test:pa:ss:smtp.two.test:imap.two.test", PRIMARY)[1]
    assert (b.password, b.smtp_host, b.imap_host) == ("pa:ss", "smtp.two.test", "imap.two.test")


def test_parse_four_field_blank_hosts_fall_back_to_primary():
    b = config.parse_email_accounts("b@two.test:pw1::", PRIMARY)[1]
    assert (b.smtp_host, b.imap_host) == ("smtp.primary.test", "imap.primary.test")


@pytest.mark.parametrize("raw", ["b@two.test:pa:ss", "b@two.test", "b@two.test:", ":pw"])
def test_parse_rejects_malformed_entries(raw):
    with pytest.raises(ValueError):
        config.parse_email_accounts(raw, PRIMARY)


def test_parse_drops_duplicate_of_primary():
    accounts = config.parse_email_accounts("CASEY@primary.test:other;b@two.test:pw1", PRIMARY)
    assert [a.user for a in accounts] == [PRIMARY.user, "b@two.test"]


# --- Selection / stickiness against a temp DB -----------------------------------

@pytest.fixture
def mailbox_env(tmp_path, monkeypatch):
    """Three configured mailboxes capped at 2/day each, a throwaway DB, dry-run
    sending, and hard failure on any real SMTP/IMAP connection."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "TRACES_PATH", str(tmp_path / "traces.json"))
    monkeypatch.setattr(config, "EMAIL_ACCOUNTS", list(ACCOUNTS))
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY_PER_ACCOUNT", 2)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 100)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_HOUR", 100)
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "SENDGRID_API_KEY", "")
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")
    monkeypatch.setattr(config, "PHYSICAL_ADDRESS", "1 Real Road, Leeds LS1 1AA, UK")
    monkeypatch.setattr(config, "SENDING_DOMAIN", "primary.test")
    monkeypatch.setattr(config, "ADMIN_EMAIL", "admin@primary.test")
    monkeypatch.setattr(email_utils, "_DRY_RUN_LOG_PATH", tmp_path / "dry_run.log")
    monkeypatch.setattr(mailboxes, "_round_robin_cursor", 0)
    monkeypatch.setattr(sales_agent, "_next_send_allowed_at", datetime.min.replace(tzinfo=timezone.utc))

    def _no_network(*args, **kwargs):
        raise AssertionError(f"real network connection attempted: {args}")

    monkeypatch.setattr(smtplib, "SMTP", _no_network)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _no_network)
    db.init_db()
    return tmp_path


def _log_outbound(lead_id: int, user: str, count: int = 1) -> None:
    for _ in range(count):
        db.insert_email_thread(
            lead_id=lead_id, direction="outbound", subject="s", body="b",
            from_addr=user, to_addr="someone@example.test", message_id=f"<{uuid.uuid4()}@t>",
        )


def _new_lead(email: str = "owner@acme.test", status: str = "designed") -> int:
    lead_id = db.insert_lead("Acme Plumbing", "plumber", "Leeds", status=status)
    db.update_lead_fields(lead_id, contact_email=email)
    return lead_id


def _outbound_senders(lead_id: int) -> list[str]:
    return [t["from_addr"] for t in db.get_email_threads(lead_id) if t["direction"] == "outbound"]


def test_migration_adds_sender_account_column(mailbox_env):
    assert db.get_lead(_new_lead())["sender_account"] is None


def test_emails_sent_today_by_account_counts_case_insensitively(mailbox_env):
    lead_id = _new_lead()
    _log_outbound(lead_id, SECOND.user)
    _log_outbound(lead_id, SECOND.user.upper())
    assert db.emails_sent_today_by_account(SECOND.user) == 2
    assert db.emails_sent_today_by_account(THIRD.user) == 0
    assert db.emails_sent_today() == 2  # global counter unchanged in meaning


def test_least_loaded_prefers_fewest_sends_under_cap(mailbox_env):
    lead_id = _new_lead()
    _log_outbound(lead_id, PRIMARY.user, count=2)  # at cap
    _log_outbound(lead_id, SECOND.user)
    assert mailboxes.least_loaded_account() is THIRD


def test_ties_rotate_round_robin(mailbox_env):
    picks = [mailboxes.least_loaded_account().user for _ in range(4)]
    assert picks == [PRIMARY.user, SECOND.user, THIRD.user, PRIMARY.user]


def test_every_mailbox_at_cap_returns_none_and_blocks_cold_sends(mailbox_env):
    # The cap-filling sends belong to OTHER leads: a lead with outbound rows of
    # its own is an existing thread and would (rightly) stick to its sender.
    filler = _new_lead("filler@example.test")
    for account in ACCOUNTS:
        _log_outbound(filler, account.user, count=2)
    lead_id = _new_lead()
    assert mailboxes.least_loaded_account() is None
    assert mailboxes.any_account_under_cap() is False
    assert sales_agent._can_send_now() is False
    assert sales_agent._send_cold_email_impl(db.get_lead(lead_id)) is False
    lead = db.get_lead(lead_id)
    assert lead["status"] == "designed" and lead["sender_account"] is None


def test_pinned_lead_keeps_its_mailbox_even_at_cap(mailbox_env):
    lead_id = _new_lead()
    db.update_lead_fields(lead_id, sender_account=SECOND.user)
    _log_outbound(lead_id, SECOND.user, count=5)
    assert mailboxes.account_for_lead(db.get_lead(lead_id)) is SECOND


def test_legacy_lead_follows_its_thread_log(mailbox_env):
    """A lead emailed before sender_account existed stays in the mailbox
    that actually sent to it, per email_threads.from_addr."""
    lead_id = _new_lead()
    _log_outbound(lead_id, THIRD.user)
    assert mailboxes.account_for_lead(db.get_lead(lead_id)) is THIRD


def test_pinned_mailbox_removed_from_config_falls_back_to_a_fresh_pick(mailbox_env, monkeypatch):
    monkeypatch.setattr(config, "EMAIL_ACCOUNTS", [PRIMARY, SECOND])
    lead_id = _new_lead()
    db.update_lead_fields(lead_id, sender_account=THIRD.user)
    assert mailboxes.account_for_lead(db.get_lead(lead_id)) is PRIMARY


def test_cold_email_pins_sender_and_every_later_email_sticks_to_it(mailbox_env):
    filler = _new_lead("filler@example.test")
    _log_outbound(filler, PRIMARY.user, count=2)  # primary at cap -> SECOND is least loaded
    lead_id = _new_lead()

    lead = db.get_lead(lead_id)
    assert sales_agent._send_cold_email_impl(lead) is True
    lead = db.get_lead(lead_id)
    assert lead["status"] == "emailed"
    assert lead["sender_account"] == SECOND.user
    assert _outbound_senders(lead_id) == [SECOND.user]

    # Push SECOND well past its cap: replies to an existing thread must still
    # leave from it, never from a different address mid-conversation.
    _log_outbound(filler, SECOND.user, count=5)
    assert sales_agent._send_thread_reply(lead, "Happy to walk you through it.") is True
    assert sales_agent.send_github_username_request(lead) is True
    assert sales_agent.send_handover_confirmation(lead, "org/acme-site") is True
    sales_agent._send_goodbye(lead)
    senders = _outbound_senders(lead_id)
    assert len(senders) == 5 and set(senders) == {SECOND.user}

    dry_run_log = (mailbox_env / "dry_run.log").read_text(encoding="utf-8")
    assert dry_run_log.count(f"From: {SECOND.user}") == 5
    assert f"From: {PRIMARY.user}" not in dry_run_log


def test_send_email_without_account_uses_the_primary_mailbox(mailbox_env):
    lead_id = _new_lead()
    email_utils.send_email("owner@acme.test", "Hi", "Body", lead_id)
    assert f"From: {PRIMARY.user}" in (mailbox_env / "dry_run.log").read_text(encoding="utf-8")


class _FakeIMAP:
    """Stand-in for imaplib.IMAP4_SSL: SECOND's login fails, THIRD holds one
    out-of-office reply from the lead, PRIMARY is empty."""

    def __init__(self, host: str):
        self.host = host
        self.user = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user: str, password: str):
        if user == SECOND.user:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        self.user = user

    def select(self, mailbox: str):
        return "OK", [b"1"]

    def search(self, charset, criterion):
        return ("OK", [b"1"]) if self.user == THIRD.user else ("OK", [b""])

    def fetch(self, num, what):
        raw = (
            f"From: Owner <owner@acme.test>\r\nTo: {self.user}\r\n"
            f"Subject: Automatic reply\r\nMessage-ID: <reply-{self.user}@t>\r\n"
            "Content-Type: text/plain\r\n\r\nI am out of the office until Monday.\r\n"
        ).encode()
        return "OK", [(b"1 (RFC822 {%d})" % len(raw), raw)]


def test_polling_covers_every_mailbox_and_survives_a_failed_login(mailbox_env, monkeypatch, capsys):
    monkeypatch.setattr(imaplib, "IMAP4_SSL", _FakeIMAP)
    lead_id = _new_lead(status="emailed")

    messages = email_utils.fetch_unseen_emails()
    assert [m.account_user for m in messages] == [THIRD.user]
    assert messages[0].to_addr == THIRD.user
    assert f"IMAP poll failed for {SECOND.user}" in capsys.readouterr().out

    # A reply landing in THIRD's inbox pins an unpinned lead there, so our
    # next email goes back out from the address the prospect wrote to.
    assert sales_agent.check_inbox() == 1
    assert db.get_lead(lead_id)["sender_account"] == THIRD.user
    assert mailboxes.account_for_lead(db.get_lead(lead_id)) is THIRD
