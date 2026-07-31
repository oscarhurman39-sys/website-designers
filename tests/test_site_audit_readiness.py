"""Tests for utils/site_audit.py's QA/readiness scanner (audit_readiness
and its helpers) -- the pre-publish gate that audits OUR OWN generated
preview/live site, as opposed to audit_html/audit_url which audit a
PROSPECT's old site. Network calls (check_broken_links) are mocked."""
from __future__ import annotations

import requests as requests_module
from unittest.mock import Mock

from agents import design_agent
from utils import db, site_audit

BASE_URL = "https://example.com"


# --- contrast_ratio / check_contrast_pairs --------------------------------------

def test_contrast_ratio_black_on_white_is_max():
    assert site_audit.contrast_ratio("#000000", "#ffffff") == 21.0


def test_contrast_ratio_identical_colors_is_one():
    assert site_audit.contrast_ratio("#336699", "#336699") == 1.0


def test_contrast_ratio_order_independent():
    assert site_audit.contrast_ratio("#000000", "#ffffff") == site_audit.contrast_ratio("#ffffff", "#000000")


def test_check_contrast_pairs_flags_low_contrast_inline_pair():
    html = '<div style="color: #ffffff; background-color: #ffff00;">low contrast</div>'
    failures = site_audit.check_contrast_pairs(html)
    assert len(failures) == 1
    assert failures[0]["foreground"] == "#ffffff"
    assert failures[0]["background"] == "#ffff00"
    assert failures[0]["ratio"] < site_audit.MIN_CONTRAST_RATIO


def test_check_contrast_pairs_passes_high_contrast_pair():
    html = '<div style="color: #ffffff; background-color: #000000;">fine</div>'
    assert site_audit.check_contrast_pairs(html) == []


def test_check_contrast_pairs_ignores_elements_without_both_colors():
    html = '<div style="background-color: #ff6600;">only a background</div>'
    assert site_audit.check_contrast_pairs(html) == []


def test_check_contrast_pairs_does_not_confuse_background_color_with_color():
    # A lone `background-color:` declaration must not be mistaken for a
    # text `color:` declaration (regression guard for the lookbehind regex).
    html = '<div style="background-color: #ffff00;">no text color set</div>'
    assert site_audit.check_contrast_pairs(html) == []


# --- check_broken_links ----------------------------------------------------------

def test_check_broken_links_flags_4xx_and_skips_non_http_schemes(monkeypatch):
    html = """
    <a href="mailto:owner@example.com">email</a>
    <a href="tel:12345">call</a>
    <a href="#section">anchor</a>
    <a href="/missing-page">broken link</a>
    <img src="/photo.jpg">
    """

    def fake_head(url, timeout, allow_redirects):
        status = 404 if url.endswith("missing-page") else 200
        return Mock(status_code=status)

    monkeypatch.setattr(requests_module, "head", fake_head)
    broken = site_audit.check_broken_links(html, BASE_URL)
    assert broken == [f"{BASE_URL}/missing-page"]


def test_check_broken_links_retries_with_get_when_head_rejected(monkeypatch):
    html = '<a href="/some-page">link</a>'

    monkeypatch.setattr(requests_module, "head", lambda *a, **k: Mock(status_code=405))
    monkeypatch.setattr(requests_module, "get", lambda *a, **k: Mock(status_code=200))
    assert site_audit.check_broken_links(html, BASE_URL) == []


def test_check_broken_links_treats_request_exception_as_broken(monkeypatch):
    html = '<a href="/unreachable">link</a>'
    monkeypatch.setattr(
        requests_module, "head",
        Mock(side_effect=requests_module.exceptions.ConnectionError("boom")),
    )
    assert site_audit.check_broken_links(html, BASE_URL) == [f"{BASE_URL}/unreachable"]


def test_check_broken_links_dedupes_and_caps(monkeypatch):
    html = "".join(f'<a href="/page{i}">l</a>' for i in range(site_audit.MAX_LINK_CHECKS + 10))
    html += '<a href="/page0">duplicate of the first</a>'
    calls = []

    def fake_head(url, timeout, allow_redirects):
        calls.append(url)
        return Mock(status_code=200)

    monkeypatch.setattr(requests_module, "head", fake_head)
    site_audit.check_broken_links(html, BASE_URL)
    assert len(calls) == site_audit.MAX_LINK_CHECKS
    assert len(set(calls)) == len(calls)


# --- audit_readiness --------------------------------------------------------------

_READY_HTML = """
<html><head>
<meta name="viewport" content="width=device-width">
<title>Test Co</title>
<meta name="description" content="A test description.">
</head><body>
<h1>Welcome</h1>
<img src="/a.jpg" alt="a">
<div style="color: #ffffff; background-color: #ffff00;">Bad contrast text</div>
</body></html>
"""


def test_audit_readiness_computes_percentage_and_issues_without_link_check():
    result = site_audit.audit_readiness(_READY_HTML, BASE_URL, check_links=False)
    assert result["checks"]["https"] is True
    assert result["checks"]["sufficient_contrast"] is False
    assert result["checks"]["no_broken_links"] is True  # skipped, not failed
    assert result["readiness_pct"] == 89  # 8/9 checks passed
    assert len(result["issues"]) == 1
    assert "contrast" in result["issues"][0].lower()
    assert result["broken_links"] == []
    assert len(result["contrast_failures"]) == 1


def test_audit_readiness_is_100_when_everything_passes():
    html = _READY_HTML.replace(
        '<div style="color: #ffffff; background-color: #ffff00;">Bad contrast text</div>', ""
    )
    result = site_audit.audit_readiness(html, BASE_URL, check_links=False)
    assert result["readiness_pct"] == 100
    assert result["issues"] == []


def test_audit_readiness_flags_broken_links_when_check_links_true(monkeypatch):
    html = _READY_HTML.replace("</body>", '<img src="/broken.jpg"></body>')
    monkeypatch.setattr(requests_module, "head", lambda *a, **k: Mock(status_code=404))
    result = site_audit.audit_readiness(html, BASE_URL, check_links=True)
    assert result["checks"]["no_broken_links"] is False
    assert f"{BASE_URL}/broken.jpg" in result["broken_links"]
    assert any("broken link" in issue for issue in result["issues"])


# --- design_agent integration: the audit actually gets persisted ---------------

def test_process_lead_persists_readiness_audit(monkeypatch, tmp_path):
    """End-to-end: _process_lead_impl runs the readiness audit against the
    just-deployed preview and persists it via db.insert_site_audit -- not
    just a unit test of audit_readiness() in isolation."""
    monkeypatch.setattr(design_agent.db.config, "DB_PATH", str(tmp_path / "test.db"))
    design_agent.db.init_db()
    lead_id = design_agent.db.insert_lead("Joes Cafe", "cafe", "Leeds")
    lead = design_agent.db.get_lead(lead_id)

    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(
        design_agent.vercel_api, "deploy_files",
        Mock(return_value={
            "deployment_id": "dpl_1", "url": "https://joes-cafe.example", "ready_state": "READY",
        }),
    )
    monkeypatch.setattr(design_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(design_agent, "_capture_and_publish_screenshot", Mock(return_value=("", "")))
    # No real network for the broken-link crawl in this test.
    monkeypatch.setattr(design_agent.site_audit, "check_broken_links", Mock(return_value=[]))

    website = design_agent._process_lead_impl(lead)
    assert website is not None

    audit = db.get_latest_site_audit(lead_id, "generated_preview")
    assert audit is not None
    assert audit["url"] == "https://joes-cafe.example"
    assert audit["readiness_pct"] is not None
    assert 0 <= audit["readiness_pct"] <= 100


def test_process_lead_readiness_audit_failure_never_blocks_a_successful_deploy(monkeypatch, tmp_path):
    """Exactly like screenshot capture -- a readiness-audit crash must not
    lose an otherwise-successful deploy."""
    monkeypatch.setattr(design_agent.db.config, "DB_PATH", str(tmp_path / "test.db"))
    design_agent.db.init_db()
    lead_id = design_agent.db.insert_lead("Joes Cafe", "cafe", "Leeds")
    lead = design_agent.db.get_lead(lead_id)

    monkeypatch.setattr(design_agent, "RENDERED_SITES_DIR", tmp_path)
    monkeypatch.setattr(
        design_agent.vercel_api, "deploy_files",
        Mock(return_value={
            "deployment_id": "dpl_1", "url": "https://joes-cafe.example", "ready_state": "READY",
        }),
    )
    monkeypatch.setattr(design_agent.url_safety, "validate_public_url", lambda url, **kw: url)
    monkeypatch.setattr(design_agent, "_capture_and_publish_screenshot", Mock(return_value=("", "")))
    monkeypatch.setattr(
        design_agent.site_audit, "audit_readiness", Mock(side_effect=RuntimeError("boom"))
    )

    website = design_agent._process_lead_impl(lead)
    assert website is not None
    assert db.get_lead(lead_id)["status"] == "designed"
    assert db.get_latest_site_audit(lead_id, "generated_preview") is None
