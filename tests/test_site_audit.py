"""utils/site_audit.py: the cheap homepage audit that decides whether a
business's existing site is a target, and the owner-facing phrase the cold
email opens with. Pure functions, no network."""
from __future__ import annotations

from datetime import datetime, timezone

from utils import site_audit

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _page(title: str = "Acme Plumbing", viewport: bool = True, year: str = "2026", extra: str = "",
          description: bool = True) -> str:
    meta = '<meta name="viewport" content="width=device-width, initial-scale=1">' if viewport else ""
    desc = '<meta name="description" content="Plumbers in Kent">' if description else ""
    return (
        f"<html><head><title>{title}</title>{meta}{desc}</head><body><h1>Acme</h1>"
        f"<p>Plumbing and heating across Kent.</p>{extra}<footer>© {year} Acme Plumbing</footer></body></html>"
    )


def test_healthy_site_scores_ten_with_nothing_to_say():
    audit = site_audit.audit_html("https://acme.test/", _page(), now=NOW)
    assert (audit.score, audit.defects, audit.keys, audit.owner_phrase()) == (10, [], "", "")


def test_http_and_no_viewport_are_the_visible_defects():
    audit = site_audit.audit_html("http://acme.test/", _page(viewport=False), now=NOW)
    assert audit.defects == ["no_https", "no_viewport"]
    assert audit.score == 4
    assert audit.owner_phrase() == (
        "it doesn't adjust to a phone screen and it isn't showing as secure in the browser"
    )


def test_redirect_to_https_counts_as_secure():
    audit = site_audit.audit_html("https://acme.test/", _page(), now=NOW)
    assert "no_https" not in audit.defects


def test_stale_copyright_carries_its_year_through_the_stored_keys():
    audit = site_audit.audit_html("https://acme.test/", _page(year="2019"), now=NOW)
    assert (audit.defects, audit.copyright_year, audit.score) == (["stale_copyright"], 2019, 8)
    assert audit.keys == "stale_copyright:2019"
    assert site_audit.phrase_from_keys(audit.keys) == "the footer still says 2019"


def test_copyright_range_uses_the_latest_year():
    audit = site_audit.audit_html("https://acme.test/", _page(year="2015-2026"), now=NOW)
    assert "stale_copyright" not in audit.defects and audit.copyright_year == 2026


def test_two_years_old_is_not_stale():
    assert "stale_copyright" not in site_audit.audit_html("https://acme.test/", _page(year="2024"), now=NOW).defects


def test_placeholder_page_can_never_score_as_healthy():
    audit = site_audit.audit_html("https://acme.test/", _page(extra="<p>Website under construction</p>"), now=NOW)
    assert "placeholder" in audit.defects and audit.score <= 4
    assert audit.owner_phrase() == "it's still showing a placeholder page"


def test_a_full_page_that_merely_mentions_coming_soon_is_not_a_placeholder():
    filler = "<p>" + "We fit and service boilers across Kent, seven days a week. " * 20 + "</p>"
    audit = site_audit.audit_html(
        "https://acme.test/", _page(extra=filler + "<p>Winter menu coming soon.</p>"), now=NOW
    )
    assert "placeholder" not in audit.defects


def test_coming_soon_as_the_heading_is_a_placeholder_whatever_the_length():
    filler = "<p>" + "Lorem ipsum dolor sit amet. " * 40 + "</p>"
    audit = site_audit.audit_html(
        "https://acme.test/", _page(extra="<h1>Coming soon</h1>" + filler), now=NOW
    )
    assert "placeholder" in audit.defects
    titled = _page(title="Website coming soon", extra=filler)
    assert "placeholder" in site_audit.audit_html("https://acme.test/", titled, now=NOW).defects


def test_missing_title_and_heavy_html_cost_a_point_each():
    audit = site_audit.audit_html("https://acme.test/", _page(title=""), html_bytes=3 * 1024 * 1024, now=NOW)
    assert audit.defects == ["no_title", "heavy_page"] and audit.score == 8


def test_viewport_meta_is_case_insensitive():
    html = '<html><head><title>x</title><META NAME="Viewport" content="width=device-width"></head><body>© 2026</body></html>'
    assert "no_viewport" not in site_audit.audit_html("https://x.test", html, now=NOW).defects


def test_table_era_markup_is_a_defect_the_owner_can_agree_with():
    old = _page(extra='<table bgcolor="#ffffff"><tr><td><font face="Arial">Call us</font></td></tr></table>')
    audit = site_audit.audit_html("https://acme.test/", old, now=NOW)
    assert audit.defects == ["old_markup"] and audit.score == 8
    assert audit.owner_phrase() == "it looks like it was built a good few years ago"
    modern = _page(extra='<p style="background-color:#fff">Call us</p><div class="text-center">x</div>')
    assert "old_markup" not in site_audit.audit_html("https://acme.test/", modern, now=NOW).defects


def test_missing_meta_description_is_scored_but_never_quoted():
    audit = site_audit.audit_html("https://acme.test/", _page(description=False), now=NOW)
    assert audit.defects == ["no_meta_description"] and audit.score == 9
    assert audit.owner_phrase() == ""


def test_phrase_names_at_most_two_defects_in_owner_order():
    keys = "no_title,stale_copyright:2018,no_https,heavy_page"
    assert site_audit.phrase_from_keys(keys) == (
        "it isn't showing as secure in the browser and the footer still says 2018"
    )


def test_parse_keys_tolerates_null_and_blank():
    assert site_audit.parse_keys(None) == ([], None)
    assert site_audit.phrase_from_keys("") == ""
    assert site_audit.parse_keys("stale_copyright") == (["stale_copyright"], None)
    assert site_audit.phrase_from_keys("stale_copyright") == ""  # no year to quote, so nothing claimed
