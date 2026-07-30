"""Lightweight, dependency-free audit of a lead's EXISTING website.

Turns "their site is probably bad" into specific, verifiable findings
("no mobile viewport tag", "took 6.2s to respond", "no meta description")
that feed three places:

  - lead_agent.py stores the whole audit as JSON on the lead (site_audit),
  - sales_agent.py turns findings into the per-lead "what we improved"
    checklist in the cold email (specific beats generic),
  - the pain_point fallback when nothing better was scraped.

This is deliberately a small requests+BeautifulSoup pass, not a real
Lighthouse run -- it checks only things that are cheap, robust, and
explainable in one line to a non-technical business owner. Every check
either observes something concrete or stays silent; no speculation.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

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
