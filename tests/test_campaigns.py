"""Tests for the campaign copy layer (pipeline/campaigns.py).

Two jobs:
  1. Regression-guard the web_design copy, which was inlined in sales_agent.py
     before the refactor -- the strings below are the pre-refactor wording.
  2. Prove the sending machine actually follows the selected campaign, and
     that every registered campaign's templates render without a KeyError
     (a typo'd {placeholder} in a Campaign would otherwise only surface at
     real send time, mid-outreach).
"""
from __future__ import annotations

import pytest

import campaigns
from agents import sales_agent

LEAD = {
    "id": 1,
    "business_name": "Example Co",
    "niche": "vehicle-repair",
    "location": "Leeds",
    "pain_point": "",
}
LINK = "https://example.vercel.app"


# --- Registry behaviour -------------------------------------------------------

def test_default_campaign_is_web_design(monkeypatch):
    monkeypatch.delenv("CAMPAIGN", raising=False)
    assert campaigns.active().key == "web_design"


def test_blank_campaign_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("CAMPAIGN", "   ")
    assert campaigns.active().key == "web_design"


def test_unknown_campaign_raises_listing_valid_keys(monkeypatch):
    monkeypatch.setenv("CAMPAIGN", "does_not_exist")
    with pytest.raises(RuntimeError) as exc:
        campaigns.active()
    assert "web_design" in str(exc.value)
    assert "garden_centre" in str(exc.value)


# --- web_design regression guard ---------------------------------------------

def test_web_design_plain_text_matches_pre_refactor_copy():
    body = sales_agent._plain_text_body("Example Co", LINK, "Leeds", campaigns.WEB_DESIGN)

    assert body.startswith(
        "I noticed people searching for Example Co only find your Google listing."
    )
    assert "What we improved:" in body
    assert "✅ Local SEO for Leeds" in body
    assert f"View the live preview: {LINK}" in body
    assert "This preview is live for 7 days" in body
    assert "If you'd like to own it, reply YES." in body
    assert "Standard package: £2,000." in body
    assert body.endswith("\n\nCasey")


def test_web_design_html_body_keeps_the_cta_button():
    html = sales_agent._build_html_body("Example Co", LINK, "Leeds", campaigns.WEB_DESIGN)
    assert "View Your Free Website &rarr;</a>" in html
    # The plain-text link line is deliberately absent from the HTML variant.
    assert f"View the live preview: {LINK}" not in html


def test_web_design_prompt_and_fallback_mention_the_web_offer():
    prompt = sales_agent._build_prompt(LEAD, campaigns.WEB_DESIGN)
    assert "freelance web designer" in prompt
    assert "free, live website preview" in prompt
    # Blank pain_point falls back to the campaign default.
    assert "a slow or outdated website" in prompt

    subject, body = sales_agent._fallback_email(LEAD, campaigns.WEB_DESIGN)
    assert subject == "a free preview site for Example Co"
    assert "free, live website preview for Example Co" in body


# --- Campaign selection actually changes the output --------------------------

def test_selected_campaign_drives_the_copy(monkeypatch):
    monkeypatch.setenv("CAMPAIGN", "garden_centre")

    body = sales_agent._plain_text_body("Knights", LINK, "Surrey")
    assert "What's in the deck:" in body
    assert "Trade price, retail price and margin" in body
    assert body.endswith("\n\nOscar")
    assert "What we improved" not in body

    prompt = sales_agent._build_prompt({**LEAD, "business_name": "Knights"})
    assert "garden centres" in prompt
    assert "freelance web designer" not in prompt


def test_garden_centre_subject_differs_from_web_design():
    assert campaigns.GARDEN_CENTRE.subject("Knights") != campaigns.WEB_DESIGN.subject("Knights")


# --- Every campaign must render cleanly -------------------------------------

@pytest.mark.parametrize("key", sorted(campaigns.CAMPAIGNS))
def test_every_campaign_renders_without_placeholder_errors(key):
    """A bad {placeholder} in any Campaign field fails here, not in production."""
    c = campaigns.get(key)

    assert c.subject("Biz")
    assert c.fallback_subject("Biz")
    assert c.fallback_body("Biz")
    assert c.intro_line("Biz")
    assert c.link_line(LINK) and LINK in c.link_line(LINK)
    assert c.pricing_line() and "{" not in c.pricing_line()
    assert c.decline_body("Biz")
    assert len(c.checklist("Leeds")) == len(c.checklist_items)

    # Rendered copy must not leak an unsubstituted placeholder.
    text = sales_agent._plain_text_body("Biz", LINK, "Leeds", c)
    assert "{" not in text and "}" not in text
    assert "Biz" in text

    html = sales_agent._build_html_body("Biz", LINK, "Leeds", c)
    assert c.cta_button_label in html


@pytest.mark.parametrize("key", sorted(campaigns.CAMPAIGNS))
def test_every_campaign_requires_a_validated_link(key):
    """Guards against adding a campaign that emails a dead/blank link: no
    campaign may opt out of preview validation until it has an asset-building
    step of its own (see campaigns.py module docstring)."""
    assert campaigns.get(key).requires_preview_link is True
