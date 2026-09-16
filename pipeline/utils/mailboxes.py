"""Which mailbox emails which lead: multi-mailbox rotation plus stickiness.

Why this exists: one cold-outreach mailbox tops out around 25 emails a day
before inbox placement slides, so real volume comes from several mailboxes
(config.EMAIL_ACCOUNTS), ideally across several domains. Two rules stop
that from confusing prospects or mail filters:

  1. Stickiness -- every email to a lead leaves from the mailbox that sent
     the first one, so the whole thread lives in one inbox and the From
     address never changes mid-conversation (a sender that changes reads as
     spoofing to humans and filters alike).
  2. Spreading -- a brand-new lead goes to the mailbox with the fewest sends
     in the last 24h that is still under config.EMAIL_MAX_PER_DAY_PER_ACCOUNT,
     ties rotating so day one doesn't pile everything onto account 0.

This module only *decides*; sending stays in utils/email_utils.py and
persisting the decision (leads.sender_account) in agents/sales_agent.py.
"""
from __future__ import annotations

from typing import Optional

import config
from utils import db

# Index the next tie-break scan starts from. Process-local on purpose: it
# only settles ties, and the DB send counts (which survive restarts) do the
# real balancing.
_round_robin_cursor = 0


def account_by_user(user: str) -> Optional[config.EmailAccount]:
    """The configured mailbox whose user matches `user` (case-insensitive),
    or None if it isn't (or is no longer) in config.EMAIL_ACCOUNTS."""
    wanted = (user or "").strip().lower()
    if not wanted:
        return None
    for account in config.EMAIL_ACCOUNTS:
        if account.user.strip().lower() == wanted:
            return account
    return None


def _sends_today_by_index() -> dict[int, int]:
    return {
        index: db.emails_sent_today_by_account(account.user)
        for index, account in enumerate(config.EMAIL_ACCOUNTS)
    }


def any_account_under_cap() -> bool:
    """Side-effect-free probe for the rate limiter: is there any mailbox a
    new cold email could leave from right now?"""
    cap = config.EMAIL_MAX_PER_DAY_PER_ACCOUNT
    return any(n < cap for n in _sends_today_by_index().values())


def least_loaded_account() -> Optional[config.EmailAccount]:
    """Mailbox for a brand-new thread: fewest outbound emails in the last
    24h among those under the per-mailbox cap, ties rotating. None when
    every mailbox is at cap -- the caller must then skip the send, exactly
    as it does when the global EMAIL_MAX_PER_DAY is hit."""
    global _round_robin_cursor
    accounts = config.EMAIL_ACCOUNTS
    cap = config.EMAIL_MAX_PER_DAY_PER_ACCOUNT
    counts = _sends_today_by_index()
    eligible = [index for index, n in counts.items() if n < cap]
    if not eligible:
        return None
    fewest = min(counts[index] for index in eligible)
    tied = [index for index in eligible if counts[index] == fewest]
    # Round-robin among the tied: first tied mailbox at or after the cursor,
    # wrapping to the first tied one.
    chosen = next((index for index in tied if index >= _round_robin_cursor), tied[0])
    _round_robin_cursor = (chosen + 1) % len(accounts)
    return accounts[chosen]


def account_for_lead(lead: dict) -> Optional[config.EmailAccount]:
    """The mailbox every email to this lead must leave from.

    Resolution order: the mailbox pinned on the lead (`leads.sender_account`);
    else the mailbox that last emailed them per the thread log (leads emailed
    before that column existed -- their thread lives wherever it started);
    else, for a genuinely new thread, least_loaded_account(), which is None
    when every mailbox is at its daily cap.

    A pinned/logged mailbox is returned even when it is at cap: the caps
    protect the reputation of *cold* sends, and a reply to someone already
    talking to us must never be held back or moved to a different address.
    If the pinned mailbox has been removed from config it is reported and
    the lead falls through to a fresh pick -- the only alternative would be
    never emailing them again.
    """
    pinned = (lead.get("sender_account") or "").strip()
    if pinned:
        account = account_by_user(pinned)
        if account is not None:
            return account
        print(
            f"[mailboxes] Lead {lead.get('id')} is pinned to {pinned!r}, which is no longer in "
            "EMAIL_ACCOUNTS; choosing a new mailbox for it."
        )
    lead_id = lead.get("id")
    if lead_id is not None:
        for thread in reversed(db.get_email_threads(lead_id)):
            if thread["direction"] == "outbound" and thread.get("from_addr"):
                account = account_by_user(thread["from_addr"])
                if account is not None:
                    return account
                break  # the last sender is gone from config; treat as a fresh pick
    return least_loaded_account()
