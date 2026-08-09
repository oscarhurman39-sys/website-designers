"""Guards against the pipeline making claims it cannot back.

Two regressions this locks down, both of which shipped and would have been
seen by every recipient:

1. The cold email opened by telling the business it had no website. Because
   lead_agent marks a lead 'lost' when it can't find a website, only leads
   that HAVE one ever reach the sending stage -- so that claim was false for
   100% of recipients, in an email built from their own scraped site.
2. design_agent invented a customer testimonial when scraping found no real
   one, and published it on a public page carrying the business's real name.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from agents import design_agent, sales_agent

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Every template that renders a testimonial at all. templates/default is in
# here too, but sits it inside another section rather than a dedicated one --
# which is exactly how it got missed the first time round, so it is covered by
# the generic assertions below and excluded only from the section-specific ones.
TESTIMONIAL_NICHES = sorted(
    p.parent.name
    for p in TEMPLATES_DIR.glob("*/index.html")
    if "{{ testimonial }}" in p.read_text()
)
NICHES_WITH_TESTIMONIAL_SECTION = sorted(
    p.parent.name
    for p in TEMPLATES_DIR.glob("*/index.html")
    if '<section id="testimonial"' in p.read_text()
)


def _render(niche: str, context: dict) -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR / niche)))
    return env.get_template("index.html").render(**context)


def test_intro_line_does_not_claim_the_business_lacks_a_website():
    intro = sales_agent._intro_line("Example Co").lower()

    assert "only find your google listing" not in intro
    for false_claim in ("don't have a website", "no website", "without a website"):
        assert false_claim not in intro


def test_intro_line_names_the_business():
    assert "Example Co" in sales_agent._intro_line("Example Co")


def test_email_copy_makes_no_unmeasured_comparison_to_their_current_site():
    """Nothing in the pipeline inspects the quality of the recipient's existing
    site, so the email must not claim to have beaten it."""
    body = sales_agent._plain_text_body("Example Co", "https://example.vercel.app", "Leeds").lower()

    for comparative in ("what we improved", "faster page speed"):
        assert comparative not in body


def test_build_context_never_invents_a_testimonial():
    lead = {"id": 1, "business_name": "Example Co", "niche": "landscaper", "location": "Leeds"}

    assert design_agent.build_context(lead)["testimonial"] == ""


def test_build_context_keeps_a_real_scraped_testimonial():
    lead = {
        "id": 1,
        "business_name": "Example Co",
        "niche": "landscaper",
        "location": "Leeds",
        "testimonial": "  Genuinely brilliant service.  ",
    }

    assert design_agent.build_context(lead)["testimonial"] == "Genuinely brilliant service."


@pytest.mark.parametrize("niche", TESTIMONIAL_NICHES)
def test_template_leaves_no_empty_quote_when_there_is_no_testimonial(niche):
    html = _render(niche, {"business_name": "Example Co", "testimonial": ""})

    assert "&ldquo;&rdquo;" not in html
    assert "&ldquo; &rdquo;" not in html


@pytest.mark.parametrize("niche", NICHES_WITH_TESTIMONIAL_SECTION)
def test_template_drops_testimonial_section_and_nav_link_when_there_is_none(niche):
    html = _render(niche, {"business_name": "Example Co", "testimonial": ""})

    assert 'id="testimonial"' not in html
    assert 'href="#testimonial"' not in html  # a nav link to a removed section would 404 in-page


@pytest.mark.parametrize("niche", TESTIMONIAL_NICHES)
def test_template_renders_a_real_testimonial(niche):
    html = _render(niche, {"business_name": "Example Co", "testimonial": "Genuinely brilliant service."})

    assert "Genuinely brilliant service." in html
