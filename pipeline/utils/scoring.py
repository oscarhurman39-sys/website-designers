"""Lead scoring: who gets a preview and an email first.

One number, `lead_score` 0-10, ordered need-first then reachability, then two
small "can pay" signals. The send queue and the design queue both sort by it
(NULL last), so a scored lead always goes ahead of an unscored one and a
business with no site always goes ahead of one with a tired site.

    website_status   site_score   base
    none             -            10 (contact known) / 8 (unknown)
    platform_only    -             9 / 7
    owned_site       0-3           8
    owned_site       4-5           6
    owned_site       6-7           4
    owned_site       >= good_at    1   (not a target; the design gate parks it)
    owned_site       NULL         NULL (not inspected yet -- see rescore.py)

Modifiers on owned sites: -2 if no contact channel is known, +1 for 20+
Google reviews (a trading business with customers), +1 if the name reads as
a limited company (Ltd/Limited/LLP/PLC: budget, and a corporate subscriber
under PECR rather than a sole trader). Capped to 0-10.

`config.SITE_SCORE_GOOD_AT` (default 8) is the line above which an existing
site is left alone. 0 turns the gate off.
"""
from __future__ import annotations

import re
from typing import Optional

import config

WEBSITE_STATUS_NONE = "none"
WEBSITE_STATUS_PLATFORM_ONLY = "platform_only"
WEBSITE_STATUS_OWNED = "owned_site"

CONTACT_CHANNEL_UNKNOWN = "unknown"

REVIEW_COUNT_BONUS_AT = 20

_LIMITED_COMPANY_RE = re.compile(r"\b(ltd\.?|limited|llp|plc)\b", re.IGNORECASE)


def good_site_threshold() -> int:
    return max(0, int(getattr(config, "SITE_SCORE_GOOD_AT", 8) or 0))


def is_good_site(website_status: Optional[str], site_score: Optional[int]) -> bool:
    """True when the business already has a site healthy enough that we do
    not pitch a rebuild. Unscored owned sites are never 'good' -- they are
    unknown, and the queue treats them as lowest priority instead."""
    threshold = good_site_threshold()
    if threshold <= 0 or website_status != WEBSITE_STATUS_OWNED or site_score is None:
        return False
    return int(site_score) >= threshold


def looks_like_limited_company(business_name: Optional[str]) -> bool:
    return bool(_LIMITED_COMPANY_RE.search(business_name or ""))


def lead_score(
    website_status: Optional[str],
    site_score: Optional[int],
    contact_channel: Optional[str],
    review_count: Optional[int] = None,
    business_name: Optional[str] = None,
) -> Optional[int]:
    contact_known = bool(contact_channel) and contact_channel != CONTACT_CHANNEL_UNKNOWN
    if website_status == WEBSITE_STATUS_NONE:
        return 10 if contact_known else 8
    if website_status == WEBSITE_STATUS_PLATFORM_ONLY:
        return 9 if contact_known else 7
    if website_status != WEBSITE_STATUS_OWNED or site_score is None:
        return None

    score = int(site_score)
    if is_good_site(website_status, score):
        base = 1
    elif score <= 3:
        base = 8
    elif score <= 5:
        base = 6
    else:
        base = 4

    if not contact_known:
        base -= 2
    if (review_count or 0) >= REVIEW_COUNT_BONUS_AT:
        base += 1
    if looks_like_limited_company(business_name):
        base += 1
    return max(0, min(10, base))


def lead_score_for_row(lead: dict) -> Optional[int]:
    """Score straight from a leads row (as returned by db.get_lead)."""
    channel = lead.get("contact_channel") or ("email" if (lead.get("contact_email") or "").strip() else None)
    return lead_score(
        lead.get("website_status"),
        lead.get("site_score"),
        channel,
        review_count=lead.get("google_reviews_count"),
        business_name=lead.get("business_name"),
    )
