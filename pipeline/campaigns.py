"""Campaign definitions: the offer copy, separated from the sending machine.

Everything in `utils/` (compliance, email_utils, tracker, db) and the
rate-limiting / reply-classification / human-takeover logic in
`agents/sales_agent.py` is offer-agnostic -- it will send and track any
outreach compliantly. What *was* offer-specific was the copy, hardcoded
across a dozen places in sales_agent.py. It lives here now, so a second
business can reuse the whole pipeline by adding a Campaign rather than
forking the agent.

Select the active campaign with the CAMPAIGN env var (default: web_design).

    CAMPAIGN=web_design    python main.py

IMPORTANT -- what a Campaign does and does not give you:
  * It fully controls the *copy*: subject line, intro, checklist, pricing
    lines, sign-off, decline reply. All of it is deterministic template
    text -- no model generates outreach copy in this pipeline.
  * It does NOT supply the asset the copy points at. web_design has one
    (design_agent builds and deploys a preview site, and sets
    requires_preview_link=True so no email can go out without a validated
    live URL). garden_centre does NOT yet -- see its note below. Adding a
    campaign whose asset-building step doesn't exist gets you emails with
    nothing to link to, so keep requires_preview_link=True unless you have
    deliberately built an alternative.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class Campaign:
    """All human-readable copy for one outreach offer."""

    key: str
    sender_name: str

    # --- Subjects ---
    subject_template: str

    # --- Main email body ---
    intro_template: str
    checklist_header: str
    checklist_items: tuple[str, ...]
    link_line_template: str
    urgency_line: str
    reply_to_buy_line: str
    pricing_line_template: str
    anchor_price: int
    offer_price: int
    cta_button_label: str

    # --- Automatic decline acknowledgement ---
    decline_subject: str
    decline_body_template: str

    # Block the send unless a validated, publicly-reachable link exists.
    requires_preview_link: bool = True

    # --- Rendering helpers -------------------------------------------------

    def subject(self, business_name: str) -> str:
        return self.subject_template.format(business_name=business_name)

    def intro_line(self, business_name: str) -> str:
        return self.intro_template.format(business_name=business_name)

    def checklist(self, city: str) -> list[str]:
        return [item.format(city=city) for item in self.checklist_items]

    def link_line(self, link: str) -> str:
        return self.link_line_template.format(link=link)

    def pricing_line(self) -> str:
        # Prices are comma-formatted here so the template stays a plain
        # {placeholder} string with no format spec to get wrong.
        return self.pricing_line_template.format(
            anchor_price=f"{self.anchor_price:,}", offer_price=f"{self.offer_price:,}"
        )

    def decline_body(self, business_name: str) -> str:
        return self.decline_body_template.format(business_name=business_name)


# --- web_design: the original offer, copy preserved verbatim ----------------
# Do not reword these without checking tests/ -- the HTML body assertions
# compare exact strings.

WEB_DESIGN = Campaign(
    key="web_design",
    sender_name="Casey",
    subject_template="I built a website for {business_name}",
    intro_template=(
        "I noticed people searching for {business_name} only find your Google "
        "listing. So I built a site that could help you appear more professional "
        "online."
    ),
    checklist_header="What we improved",
    checklist_items=(
        "Mobile-friendly design",
        "Faster page speed",
        "Clear calls-to-action",
        "Local SEO for {city}",
        "Professional, trust-building look",
    ),
    link_line_template="View the live preview: {link}",
    urgency_line=(
        "This preview is live for 7 days -- after that it'll be repurposed. "
        "No pressure, just didn't want you to miss it."
    ),
    reply_to_buy_line=(
        "If you'd like to own it, reply YES. I'll connect your domain, swap in "
        "your own photos, and make any changes you want."
    ),
    pricing_line_template="Standard package: £{anchor_price}. This completed draft: £{offer_price}.",
    anchor_price=2000,
    offer_price=config.WEBSITE_OFFER_PRICE,
    cta_button_label="View Your Free Website &rarr;",
    decline_subject="No problem",
    decline_body_template=(
        "Hi, totally understood -- I won't reach out again about this. "
        "Wishing {business_name} all the best."
    ),
    requires_preview_link=True,
)


# --- garden_centre: Timber staff-training licence ---------------------------
# NOT RUNNABLE YET. The copy is ready, but nothing in the pipeline builds the
# asset it points at (a Timber deck seeded with the centre's own stock).
# requires_preview_link stays True deliberately: until a garden-centre
# equivalent of design_agent exists, every lead will be blocked before send
# with "preview URL is missing or invalid" -- which is the correct, loud
# failure rather than emailing a broken link.

GARDEN_CENTRE = Campaign(
    key="garden_centre",
    sender_name="Oscar",
    subject_template="a plant-knowledge deck for {business_name}",
    intro_template=(
        "New counter staff at {business_name} usually learn the range by asking "
        "whoever is nearest. So I built a deck they can learn it from instead."
    ),
    checklist_header="What's in the deck",
    checklist_items=(
        "Care, aspect, soil and pruning per plant",
        "Trade price, retail price and margin",
        "Order weeks and bench time",
        "Shrink and return risk flags",
        "Quiz mode, works offline on any phone",
    ),
    link_line_template="Open the demo deck: {link}",
    urgency_line=(
        "The demo stays up for 14 days. No pressure, just didn't want you to "
        "miss it before the spring order goes in."
    ),
    reply_to_buy_line=(
        "If you'd like it loaded with your full range, reply YES and I'll build "
        "it from your stock list."
    ),
    pricing_line_template="Setup with your range: £{anchor_price}. Per-site licence: £{offer_price}/year.",
    anchor_price=750,
    offer_price=480,
    cta_button_label="Open Your Plant Deck &rarr;",
    decline_subject="No problem",
    decline_body_template=(
        "Hi, totally understood -- I won't reach out again about this. "
        "Wishing {business_name} all the best."
    ),
    requires_preview_link=True,
)


CAMPAIGNS: dict[str, Campaign] = {c.key: c for c in (WEB_DESIGN, GARDEN_CENTRE)}

DEFAULT_CAMPAIGN_KEY = WEB_DESIGN.key


def get(key: str) -> Campaign:
    """Return the Campaign for `key`, or raise listing the valid keys."""
    try:
        return CAMPAIGNS[key]
    except KeyError:
        raise RuntimeError(
            f"Unknown CAMPAIGN {key!r}. Valid campaigns: {', '.join(sorted(CAMPAIGNS))}"
        ) from None


def active() -> Campaign:
    """The campaign selected by the CAMPAIGN env var (default: web_design)."""
    return get(os.getenv("CAMPAIGN", "").strip() or DEFAULT_CAMPAIGN_KEY)
