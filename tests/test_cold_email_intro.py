"""The cold email's first line must be true for the business reading it.
Until 2026-09-20 every lead was told "people only find your Google
listing" -- and all 164 had a website."""
from __future__ import annotations

from agents import sales_agent

LINK = "https://preview.test"
LISTING_LINE = "only find your Google listing"


def test_a_lead_with_no_known_site_keeps_the_listing_line():
    body = sales_agent._plain_text_body("Sample Plumbing", LINK, "Maidstone, Kent")
    assert body.startswith(f"I noticed people searching for Sample Plumbing {LISTING_LINE}")
    assert sales_agent._intro_line("Acme", {"website_status": "none"}).startswith("I noticed people searching for Acme only find your Google listing")


def test_owned_site_with_defects_names_the_defect_not_the_listing():
    lead = {"business_name": "Acme Plumbing", "website_status": "owned_site",
            "site_defects": "no_viewport,stale_copyright:2019"}
    body = sales_agent._plain_text_body("Acme Plumbing", LINK, "Crawley", lead=lead)
    assert body.split("\n\n")[0] == (
        "I had a look at the website for Acme Plumbing and noticed it doesn't adjust to a phone screen "
        "and the footer still says 2019, so I put together a draft of what a fresh one could look like for you."
    )
    assert LISTING_LINE not in body


def test_owned_site_without_recorded_defects_makes_no_claim_at_all():
    lead = {"business_name": "Acme", "website_url": "https://acme.test", "site_defects": ""}
    assert sales_agent._intro_line("Acme", lead) == (
        "I had a look at the website for Acme and put together a draft of what a fresh one could look like for you."
    )


def test_intro_reads_right_for_awkward_business_names():
    lead = {"website_status": "owned_site", "site_defects": "old_markup"}
    assert sales_agent._intro_line("The Refuge", lead).startswith("I had a look at the website for The Refuge and noticed")
    assert sales_agent._intro_line("Ecotec - emergency plumber crawley.", lead).startswith(
        "I had a look at the website for Ecotec - emergency plumber crawley and noticed"
    )


def test_platform_only_lead_names_the_directory_page():
    assert "only find a directory or social-media page" in sales_agent._intro_line(
        "Acme", {"website_status": "platform_only"}
    )


def test_html_body_uses_the_same_intro():
    lead = {"business_name": "Acme Plumbing", "website_status": "owned_site", "site_defects": "no_https"}
    html = sales_agent._build_html_body("Acme Plumbing", LINK, "Crawley", lead=lead)
    assert "noticed it isn&#x27;t showing as secure in the browser" in html
    assert LISTING_LINE not in html


def test_the_longest_intro_does_not_bloat_the_email():
    old = sales_agent._intro_line("Acme Plumbing")
    new = sales_agent._intro_line("Acme Plumbing", {"website_status": "owned_site", "site_defects": "placeholder,no_viewport"})
    assert len(new.split()) - len(old.split()) <= 20
