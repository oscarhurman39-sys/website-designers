"""Cheap, deterministic audit of a business's existing website.

The pipeline sells a rebuild of a *poor* web presence, yet every lead sourced
between 2026-09-08 and 2026-09-20 had a working owned site and nothing ever
looked at it: `site_score` stayed NULL, the send queue was id order, and the
cold email opened with "people only find your Google listing" -- untrue for
all of them. This module is the missing look.

It runs on the homepage LeadAgent already fetches for the email scrape, so it
costs no extra request. Every check is something the business owner can see
for themselves and that the draft site visibly fixes:

    no_https             the site does not load over https (no padlock)
    no_viewport          no viewport meta -> shrunk desktop page on a phone
    stale_copyright      footer year is 3+ years old (stored as stale_copyright:YYYY)
    old_markup           <font>/<center>/<frameset>/<marquee>/bgcolor= -- a build
                         from the table-layout era, whatever the footer says
    no_title             empty/missing <title>
    no_meta_description  no meta description for Google to show (scored, never
                         quoted to the owner: they cannot see it)
    heavy_page           homepage HTML over HEAVY_HTML_BYTES
    placeholder          a parked / under-construction page: the marker sits in the
                         title or a heading, or the whole page says little else. A
                         "winter menu coming soon" line on a full page is not one.

Score is 0-10 where 10 is a healthy site. It is a proxy from the HTML alone
(no assets are downloaded, nothing is rendered) and says little about looks:
on the 2026-09-20 queue 43 of 46 reachable sites passed every check. That is
the honest limit of a scrape, and why the email wording only ever names the
concrete defect found, never "your site is bad".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

HEAVY_HTML_BYTES = 2 * 1024 * 1024
STALE_COPYRIGHT_YEARS = 3

_PENALTY = {
    "placeholder": 6,
    "no_https": 3,
    "no_viewport": 3,
    "stale_copyright": 2,
    "old_markup": 2,
    "no_title": 1,
    "no_meta_description": 1,
    "heavy_page": 1,
}

_OLD_MARKUP_RE = re.compile(r"<(?:font|center|frameset|marquee|blink)\b|\sbgcolor\s*=", re.IGNORECASE)

_PLACEHOLDER_MARKERS = (
    "under construction",
    "coming soon",
    "this domain is parked",
    "domain is for sale",
    "default web page",
    "future home of",
)
# A page with less visible text than this is a placeholder if it carries a
# marker anywhere; a fuller page only if the marker is its title or heading.
_PLACEHOLDER_TEXT_MAX_CHARS = 600

_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", re.IGNORECASE)

# Owner-facing phrases for the cold email. Ordered by how obviously the
# owner can confirm each one, because the intro line names at most two.
_OWNER_PHRASES = {
    "placeholder": "it's still showing a placeholder page",
    "no_viewport": "it doesn't adjust to a phone screen",
    "no_https": "it isn't showing as secure in the browser",
    "stale_copyright": "the footer still says {year}",
    "old_markup": "it looks like it was built a good few years ago",
    "no_title": "it has no page title for Google to show",
    "heavy_page": "it's a heavy page to load",
}
_OWNER_PHRASE_ORDER = (
    "placeholder", "no_viewport", "no_https", "stale_copyright", "old_markup", "no_title", "heavy_page",
)


@dataclass
class SiteAudit:
    score: int
    defects: list[str] = field(default_factory=list)
    copyright_year: Optional[int] = None

    @property
    def keys(self) -> str:
        """Comma-separated defect keys, the form stored in leads.site_defects.
        The stale-copyright key carries its year so the email can quote it."""
        return ",".join(
            f"{d}:{self.copyright_year}" if d == "stale_copyright" and self.copyright_year else d
            for d in self.defects
        )

    def owner_phrase(self) -> str:
        """The one or two most visible defects as a phrase the owner would
        agree with, e.g. "it doesn't adjust to a phone screen and the footer
        still says 2019". Empty when nothing was found."""
        return owner_phrase(self.defects, self.copyright_year)


def owner_phrase(defects: list[str], copyright_year: Optional[int] = None) -> str:
    ordered = [d for d in _OWNER_PHRASE_ORDER if d in defects][:2]
    if "stale_copyright" in ordered and not copyright_year:
        ordered.remove("stale_copyright")
    phrases = [_OWNER_PHRASES[d].format(year=copyright_year) for d in ordered]
    return " and ".join(phrases)


def parse_keys(keys: Optional[str]) -> tuple[list[str], Optional[int]]:
    """Inverse of SiteAudit.keys, tolerant of NULL/blank."""
    defects: list[str] = []
    year: Optional[int] = None
    for raw in (keys or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        name, _, suffix = raw.partition(":")
        defects.append(name)
        if name == "stale_copyright" and suffix.isdigit():
            year = int(suffix)
    return defects, year


def phrase_from_keys(keys: Optional[str]) -> str:
    """Owner-facing phrase straight from the stored leads.site_defects value."""
    defects, year = parse_keys(keys)
    return owner_phrase(defects, year)


def audit_html(
    final_url: str,
    html: str,
    html_bytes: Optional[int] = None,
    now: Optional[datetime] = None,
    soup: Optional[BeautifulSoup] = None,
) -> SiteAudit:
    """Audit one fetched homepage. `final_url` is the URL after redirects
    (an http:// site that redirects to https:// is fine). `soup` lets the
    caller reuse a parse it already has."""
    soup = soup or BeautifulSoup(html or "", "html.parser")
    year_now = (now or datetime.now(timezone.utc)).year
    defects: list[str] = []
    copyright_year: Optional[int] = None

    text = soup.get_text(" ", strip=True)
    lowered = text.lower()
    # Title and h1 only: a chain's "Sheffield coming soon!" location card is an
    # h2/h3 on a full page (leads 48 and 113, 2026-09-20), not a placeholder.
    headings = [soup.title] if soup.title else []
    headings += soup.find_all("h1")
    headline = " ".join(h.get_text(" ", strip=True) for h in headings).lower()
    if any(marker in headline for marker in _PLACEHOLDER_MARKERS) or (
        len(text) < _PLACEHOLDER_TEXT_MAX_CHARS and any(marker in lowered for marker in _PLACEHOLDER_MARKERS)
    ):
        defects.append("placeholder")

    if urlparse(final_url or "").scheme.lower() != "https":
        defects.append("no_https")

    if soup.find("meta", attrs={"name": re.compile(r"^viewport$", re.IGNORECASE)}) is None:
        defects.append("no_viewport")

    years = [int(m) for m in _COPYRIGHT_RE.findall(text)]
    years = [y for y in years if 1990 <= y <= year_now + 1]
    if years:
        copyright_year = max(years)
        if year_now - copyright_year >= STALE_COPYRIGHT_YEARS:
            defects.append("stale_copyright")

    if _OLD_MARKUP_RE.search(html or ""):
        defects.append("old_markup")

    title = soup.title.get_text(strip=True) if soup.title else ""
    if not title:
        defects.append("no_title")

    description = soup.find("meta", attrs={"name": re.compile(r"^description$", re.IGNORECASE)})
    if description is None or not (description.get("content") or "").strip():
        defects.append("no_meta_description")

    size = html_bytes if html_bytes is not None else len((html or "").encode("utf-8", errors="ignore"))
    if size > HEAVY_HTML_BYTES:
        defects.append("heavy_page")

    score = max(0, 10 - sum(_PENALTY[d] for d in defects))
    return SiteAudit(score=score, defects=defects, copyright_year=copyright_year)
