"""Lightweight, dependency-free audit of a website.

Two distinct entry points, for two distinct audiences:

  - audit_html()/audit_url() audit a LEAD'S OLD site. Turns "their site is
    probably bad" into specific, verifiable findings ("no mobile viewport
    tag", "took 6.2s to respond", "no meta description") that feed three
    places: lead_agent.py stores the whole audit as JSON on the lead
    (site_audit), sales_agent.py turns findings into the per-lead "what we
    improved" checklist in the cold email, and it's the pain_point
    fallback when nothing better was scraped. `score` here is an additive
    BADNESS rubric -- higher means their old site is weaker (a hotter
    prospect).

  - audit_readiness() audits OUR OWN generated preview / live client site
    as a pre-publish QA gate -- design_agent.py runs it right after a
    deploy succeeds. `readiness_pct` here is the opposite framing:
    percentage of checks PASSED (100 = fully ready to publish).

This is deliberately a small requests+BeautifulSoup(+HEAD-request) pass,
not a real Lighthouse run -- it checks only things that are cheap, robust,
and explainable in one line to a non-technical business owner. Every check
either observes something concrete or stays silent; no speculation.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# Response slower than this reads as "slow" to a visitor on a phone.
SLOW_RESPONSE_SECONDS = 3.0
# A homepage heavier than this is flagged (HTML payload only -- images and
# scripts would add more, so this is a conservative lower bound).
HEAVY_PAGE_BYTES = 1_500_000
# Copyright years this far in the past read as an abandoned site.
STALE_COPYRIGHT_YEARS = 2

_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(\d{4})", re.IGNORECASE)

_SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "tiktok.com")


def audit_html(html: str, *, final_url: str = "", response_seconds: float = 0.0,
               page_bytes: int = 0) -> dict[str, Any]:
    """Audit an already-fetched homepage. Pure function of its inputs, so
    it's easy to test and can't fail on network. Returns:

        {"checks": {...}, "pain_points": [str, ...], "improvements": [str, ...], "score": int}

    `pain_points` are phrased about THEIR current site (for email
    personalization); `improvements` are phrased about OUR preview (for the
    "what we improved" checklist). `score` is an additive badness rubric --
    higher means their current site is weaker, i.e. a hotter prospect.
    """
    soup = BeautifulSoup(html, "html.parser")
    checks: dict[str, Any] = {}
    pain_points: list[str] = []
    improvements: list[str] = []
    score = 0

    # --- HTTPS -----------------------------------------------------------
    is_https = final_url.startswith("https://")
    checks["https"] = is_https
    if final_url and not is_https:
        pain_points.append("the site isn't served over a secure (https) connection, "
                           "so browsers mark it 'Not secure'")
        improvements.append("Secure HTTPS connection (your current site shows 'Not secure')")
        score += 20

    # --- Mobile viewport ---------------------------------------------------
    viewport = soup.find("meta", attrs={"name": "viewport"})
    checks["mobile_viewport"] = viewport is not None
    if viewport is None:
        pain_points.append("the site has no mobile viewport set, so it renders as a shrunken "
                           "desktop page on phones")
        improvements.append("Mobile-friendly design (your current site isn't mobile-optimised)")
        score += 25

    # --- Title & meta description ------------------------------------------
    title = (soup.title.get_text(strip=True) if soup.title else "") or ""
    checks["title"] = title
    if not title:
        pain_points.append("the homepage has no page title, which hurts how it appears in Google")
        improvements.append("A proper page title so you show up better in Google")
        score += 10

    meta_desc = soup.find("meta", attrs={"name": "description"})
    desc_content = (meta_desc.get("content") or "").strip() if meta_desc else ""
    checks["meta_description"] = bool(desc_content)
    if not desc_content:
        pain_points.append("there's no meta description, so Google picks random page text "
                           "for the search result snippet")
        improvements.append("A search-friendly description (missing on your current site)")
        score += 10

    # --- H1 ---------------------------------------------------------------
    h1_count = len(soup.find_all("h1"))
    checks["h1_count"] = h1_count
    if h1_count == 0:
        pain_points.append("the page has no main headline (h1), which search engines use to "
                           "understand what the business does")
        score += 5

    # --- Image alt text ------------------------------------------------------
    imgs = soup.find_all("img")
    imgs_missing_alt = sum(1 for img in imgs if not (img.get("alt") or "").strip())
    checks["images"] = len(imgs)
    checks["images_missing_alt"] = imgs_missing_alt
    if imgs and imgs_missing_alt > len(imgs) / 2:
        pain_points.append(f"{imgs_missing_alt} of {len(imgs)} images have no alt text, which "
                           "hurts both accessibility and image search")
        improvements.append("Accessible images with proper descriptions")
        score += 5

    # --- Speed & weight -------------------------------------------------------
    checks["response_seconds"] = round(response_seconds, 2)
    if response_seconds > SLOW_RESPONSE_SECONDS:
        pain_points.append(f"the homepage took {response_seconds:.1f}s to respond -- most "
                           "visitors on a phone give up after about 3 seconds")
        improvements.append(f"Faster page speed (your site took {response_seconds:.1f}s to load)")
        score += 20
    checks["page_bytes"] = page_bytes
    if page_bytes > HEAVY_PAGE_BYTES:
        pain_points.append("the homepage HTML alone is unusually heavy, which slows every visit")
        score += 5

    # --- Stale copyright year ---------------------------------------------------
    text = soup.get_text(" ")
    year_match = _COPYRIGHT_RE.search(text)
    current_year = datetime.now(timezone.utc).year
    copyright_year = int(year_match.group(1)) if year_match else None
    checks["copyright_year"] = copyright_year
    if copyright_year and current_year - copyright_year >= STALE_COPYRIGHT_YEARS:
        pain_points.append(f"the footer still says {copyright_year}, which makes the business "
                           "look closed or the site abandoned")
        improvements.append("Up-to-date content (your site's footer is dated "
                            f"{copyright_year})")
        score += 10

    # --- Social links (a signal, not a pain point) ------------------------------
    socials = sorted({
        domain
        for a in soup.find_all("a", href=True)
        for domain in _SOCIAL_DOMAINS
        if domain in a["href"].lower()
    })
    checks["social_links"] = socials

    return {
        "checks": checks,
        "pain_points": pain_points,
        "improvements": improvements,
        "score": min(score, 100),
    }


def audit_url(url: str, *, timeout: int = 15, user_agent: str = "SiteAuditBot/1.0") -> Optional[dict[str, Any]]:
    """Fetch `url` and audit it. Returns None on any fetch failure -- an
    audit is an enrichment, never a blocker. robots.txt is the caller's
    responsibility (lead_agent checks it before ever handing a URL here,
    and passes its own bot user agent for consistency)."""
    import requests  # local import keeps this module importable without network use

    try:
        start = time.monotonic()
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": user_agent})
        elapsed = time.monotonic() - start
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return audit_html(
        resp.text,
        final_url=resp.url or url,
        response_seconds=elapsed,
        page_bytes=len(resp.content),
    )


# --- QA/readiness scanner for OUR OWN generated preview / live site -----------
#
# Everything below audits a site WE built, as a pre-publish gate -- see the
# module docstring. Reuses audit_html()'s checks for the shared basics
# (https/viewport/title/meta description/h1/alt text) and adds two checks
# audit_html() deliberately doesn't do (broken links, color contrast),
# since those require extra network calls / different reasoning that would
# slow down and complicate the read-only prospect audit above.

# WCAG 2.1 AA minimum contrast ratio for normal-size text.
MIN_CONTRAST_RATIO = 4.5
# A page with hundreds of images shouldn't turn a QA pass into a multi-
# minute crawl -- check at most this many distinct links/images.
MAX_LINK_CHECKS = 15

_INLINE_FG_COLOR_RE = re.compile(r"(?<![-a-zA-Z])color\s*:\s*(#[0-9a-fA-F]{3,6})")
_INLINE_BG_COLOR_RE = re.compile(r"background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,6})", re.IGNORECASE)

READINESS_CHECKS = (
    "https", "mobile_viewport", "title", "meta_description", "h1_present",
    "images_alt_text", "no_broken_links", "sufficient_contrast", "fresh_copyright",
)


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance (0.0-1.0) of a #rgb/#rrggbb color."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = channel(r), channel(g), channel(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """WCAG contrast ratio between two colors, from 1.0 (identical) to
    21.0 (black on white). >= MIN_CONTRAST_RATIO passes AA for normal
    text. Raises ValueError on a malformed hex string, same as int()."""
    lum_a, lum_b = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return round((lighter + 0.05) / (darker + 0.05), 2)


def check_contrast_pairs(html: str) -> list[dict[str, Any]]:
    """Elements whose inline style declares BOTH a text color and a
    background color that together fail WCAG AA (4.5:1). Only catches
    inline-style pairs -- the common case for templated pages and for
    small-business sites with hand-set colors -- it cannot see rules
    defined in a separate stylesheet."""
    soup = BeautifulSoup(html, "html.parser")
    failing: list[dict[str, Any]] = []
    for el in soup.find_all(style=True):
        style = el["style"]
        fg_match = _INLINE_FG_COLOR_RE.search(style)
        bg_match = _INLINE_BG_COLOR_RE.search(style)
        if not fg_match or not bg_match:
            continue
        try:
            ratio = contrast_ratio(fg_match.group(1), bg_match.group(1))
        except ValueError:
            continue
        if ratio < MIN_CONTRAST_RATIO:
            failing.append({"foreground": fg_match.group(1), "background": bg_match.group(1), "ratio": ratio})
    return failing


def check_broken_links(html: str, base_url: str, *, timeout: int = 8,
                       max_checks: int = MAX_LINK_CHECKS) -> list[str]:
    """HEAD- (falling back to GET- for servers that reject HEAD) request
    every distinct http(s) link/image on the page, resolved against
    base_url. Returns the URLs that errored or 4xx/5xx'd. Skips
    mailto:/tel:/#anchor/javascript:/data: -- there's nothing to check."""
    import requests  # local import keeps this module importable without network use

    soup = BeautifulSoup(html, "html.parser")
    urls: list[str] = []
    seen: set[str] = set()
    for tag_name, attr in (("a", "href"), ("img", "src")):
        for el in soup.find_all(tag_name):
            raw = (el.get(attr) or "").strip()
            if not raw or raw.startswith(("mailto:", "tel:", "#", "javascript:", "data:")):
                continue
            url = urljoin(base_url, raw)
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            urls.append(url)
            seen.add(url)
            if len(urls) >= max_checks:
                break
        if len(urls) >= max_checks:
            break

    broken: list[str] = []
    for url in urls:
        try:
            resp = requests.head(url, timeout=timeout, allow_redirects=True)
            if resp.status_code in (404, 405) or resp.status_code >= 500:
                # Some servers reject/misreport HEAD -- retry with GET
                # before calling a link broken.
                resp = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)
            if resp.status_code >= 400:
                broken.append(url)
        except requests.RequestException:
            broken.append(url)
    return broken


def audit_readiness(html: str, base_url: str, *, check_links: bool = True,
                    link_timeout: int = 8, max_link_checks: int = MAX_LINK_CHECKS) -> dict[str, Any]:
    """Pre-publish QA gate for a site WE generated (as opposed to
    audit_html/audit_url, which audit a PROSPECT's old site). Returns:

        {"checks": {...}, "issues": [str, ...], "readiness_pct": int,
         "broken_links": [str, ...], "contrast_failures": [...]}

    `readiness_pct` is the percentage of READINESS_CHECKS that passed (100
    = every check passed). `check_links=False` skips the network-bound
    broken-link crawl (useful for a fast, offline pre-deploy check on the
    rendered HTML alone, before there's a live URL to crawl)."""
    base = audit_html(html, final_url=base_url)
    checks: dict[str, Any] = {}
    issues: list[str] = []

    checks["https"] = base["checks"]["https"]
    if not checks["https"]:
        issues.append("Not served over HTTPS")

    checks["mobile_viewport"] = base["checks"]["mobile_viewport"]
    if not checks["mobile_viewport"]:
        issues.append("Missing mobile viewport meta tag")

    checks["title"] = bool(base["checks"]["title"])
    if not checks["title"]:
        issues.append("Missing page title")

    checks["meta_description"] = base["checks"]["meta_description"]
    if not checks["meta_description"]:
        issues.append("Missing meta description")

    checks["h1_present"] = base["checks"]["h1_count"] > 0
    if not checks["h1_present"]:
        issues.append("No <h1> heading found")

    images, missing_alt = base["checks"]["images"], base["checks"]["images_missing_alt"]
    checks["images_alt_text"] = images == 0 or missing_alt <= images / 2
    if not checks["images_alt_text"]:
        issues.append(f"{missing_alt} of {images} images missing alt text")

    broken = check_broken_links(html, base_url, timeout=link_timeout, max_checks=max_link_checks) if check_links else []
    checks["no_broken_links"] = not broken
    if broken:
        shown = ", ".join(broken[:3]) + ("..." if len(broken) > 3 else "")
        issues.append(f"{len(broken)} broken link(s)/image(s): {shown}")

    contrast_failures = check_contrast_pairs(html)
    checks["sufficient_contrast"] = not contrast_failures
    if contrast_failures:
        issues.append(f"{len(contrast_failures)} text/background color pair(s) fail WCAG AA contrast (4.5:1)")

    copyright_year = base["checks"]["copyright_year"]
    checks["fresh_copyright"] = (
        copyright_year is None or datetime.now(timezone.utc).year - copyright_year < STALE_COPYRIGHT_YEARS
    )
    if not checks["fresh_copyright"]:
        issues.append(f"Footer copyright year ({copyright_year}) looks stale")

    passed = sum(1 for name in READINESS_CHECKS if checks.get(name))
    readiness_pct = round(100 * passed / len(READINESS_CHECKS))

    return {
        "checks": checks,
        "issues": issues,
        "readiness_pct": readiness_pct,
        "broken_links": broken,
        "contrast_failures": contrast_failures,
    }
