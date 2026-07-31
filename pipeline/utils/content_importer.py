"""ContentImporter: extracts structured content -- logo, photos, opening
hours, services, and reviews -- from a lead's own website.

Runs as a pure function over an already-fetched BeautifulSoup page (the
same page lead_agent.py already fetched for email/phone/testimonial
extraction), so importing content costs no extra network requests. Every
field is independently best-effort: a thin small-business site should
still research successfully with whatever subset was actually found,
exactly like the rest of lead_agent.py.

Persisted on the lead as: logo_url, photos (JSON list), hours (JSON list
of human-readable lines), scraped_services (JSON list), reviews (JSON
list), brand_colors (JSON list of hex strings). design_agent.py's
build_context() prefers these real values over the generic niche
fallbacks/stock photos/fabricated testimonial whenever they were found --
a real photo of their own shop reads as authentic in a way a stock photo
never can, and their own service names/reviews sell harder than generic
copy.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

MAX_PHOTOS = 8
MAX_REVIEWS = 5
MAX_SERVICES = 8
MAX_BRAND_COLORS = 3
MAX_HOURS_LINES = 7

# img alt/class/src hints that mean "this is the business's own logo", not
# a generic photo.
_LOGO_HINTS = ("logo",)
# img alt/class/src hints that mean "this is decorative chrome, not a real
# business photo" -- icons, spacers, tracking pixels, avatars.
_SKIP_IMAGE_HINTS = ("icon", "spacer", "pixel", "sprite", "avatar", "logo")
# Only enforced when the tag actually declares width/height -- filters out
# tiny inline icons without penalizing photos that simply omit the attrs.
_MIN_PHOTO_DIMENSION = 80

_REVIEW_HINTS = ("testimonial", "review", "quote", "client-says", "feedback")
_SERVICE_SECTION_HINTS = ("service", "menu", "what we do", "what we offer", "products")
_HOURS_HINTS = ("hours", "opening", "open")

_HOURS_LINE_RE = re.compile(
    r"\b(mon|tue|tues|wed|thu|thurs|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r".{0,40}?\d{1,2}([:.]\d{2})?\s*(am|pm)?\s*[-–—to]{1,3}\s*\d{1,2}([:.]\d{2})?\s*(am|pm)?",
    re.IGNORECASE,
)
_DAY_URI_RE = re.compile(r"(?:schema\.org/)?(\w+day)$", re.IGNORECASE)

_HEX_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}){1,2}\b")
# Colors so close to black/white/gray that they read as "no real brand
# color" -- filtered out before ranking by frequency (every site's CSS is
# full of these for borders/text/backgrounds).
_NEUTRAL_HEX = {
    "#fff", "#ffffff", "#000", "#000000", "#fafafa", "#f5f5f5", "#eee", "#eeeeee",
    "#ddd", "#dddddd", "#ccc", "#cccccc", "#bbb", "#bbbbbb", "#aaa", "#aaaaaa",
    "#999", "#999999", "#888", "#888888", "#777", "#777777", "#666", "#666666",
    "#555", "#555555", "#444", "#444444", "#333", "#333333", "#222", "#222222",
    "#111", "#111111",
}


def _int_attr(value: Any) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else None


def extract_logo_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """The business's own logo image, or (failing that) its favicon/
    apple-touch-icon -- a real brand mark beats a generic placeholder."""
    for img in soup.find_all("img"):
        alt = (img.get("alt") or "").lower()
        cls = " ".join(img.get("class") or []).lower()
        src = img.get("src") or img.get("data-src") or ""
        if not src:
            continue
        if any(hint in alt or hint in cls or hint in src.lower() for hint in _LOGO_HINTS):
            return urljoin(base_url, src)
    for link in soup.find_all("link"):
        rel = link.get("rel") or []
        rel_str = " ".join(rel).lower() if isinstance(rel, list) else str(rel).lower()
        if any(r in rel_str for r in ("apple-touch-icon", "icon")) and link.get("href"):
            return urljoin(base_url, link["href"])
    return None


def extract_photos(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Real photo URLs from the page -- excludes icons/logos/data-URI
    images and (when declared) anything smaller than a thumbnail."""
    photos: list[str] = []
    seen: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src.startswith("data:"):
            continue
        alt = (img.get("alt") or "").lower()
        cls = " ".join(img.get("class") or []).lower()
        if any(hint in alt or hint in cls or hint in src.lower() for hint in _SKIP_IMAGE_HINTS):
            continue
        width = _int_attr(img.get("width"))
        height = _int_attr(img.get("height"))
        if (width is not None and width < _MIN_PHOTO_DIMENSION) or (
            height is not None and height < _MIN_PHOTO_DIMENSION
        ):
            continue
        url = urljoin(base_url, src)
        if url in seen:
            continue
        photos.append(url)
        seen.add(url)
        if len(photos) >= MAX_PHOTOS:
            break
    return photos


def extract_reviews(soup: BeautifulSoup) -> list[str]:
    """Up to MAX_REVIEWS distinct testimonial/review snippets. Generalizes
    what used to be lead_agent._extract_testimonial's single-result search
    -- same hints, same length bounds (20-400 chars), just not stopping at
    the first match."""
    reviews: list[str] = []
    seen: set[str] = set()
    for hint in _REVIEW_HINTS:
        for el in soup.find_all(attrs={"class": re.compile(hint, re.I)}):
            text = el.get_text(" ", strip=True)
            if 20 <= len(text) <= 400 and text.lower() not in seen:
                reviews.append(text)
                seen.add(text.lower())
            if len(reviews) >= MAX_REVIEWS:
                return reviews
    return reviews


def extract_services(soup: BeautifulSoup) -> list[str]:
    """Real service/menu item names pulled from a list under a heading
    that reads as a services/menu section (e.g. <h2>Our Services</h2>
    followed by a <ul>). Best-effort: a page with no such structure yields
    an empty list, and design_agent.py falls back to its curated
    per-niche list exactly as it did before."""
    services: list[str] = []
    seen: set[str] = set()
    for heading in soup.find_all(re.compile(r"^h[1-4]$")):
        heading_text = heading.get_text(" ", strip=True).lower()
        if not any(hint in heading_text for hint in _SERVICE_SECTION_HINTS):
            continue
        container = heading.find_next(["ul", "ol"])
        if container is None:
            continue
        for item in container.find_all("li"):
            text = item.get_text(" ", strip=True)
            if text and 2 <= len(text) <= 60 and text.lower() not in seen:
                services.append(text)
                seen.add(text.lower())
        if services:
            break
    return services[:MAX_SERVICES]


def _short_day(value: str) -> str:
    match = _DAY_URI_RE.search(value.strip())
    day = match.group(1) if match else value.strip()
    return day[:3].title()


def _hours_from_json_ld(soup: BeautifulSoup) -> list[str]:
    """schema.org LocalBusiness opening hours, if the site ships them --
    either the shorthand `openingHours` string form (e.g. "Mo-Fr
    09:00-17:00") or the structured `openingHoursSpecification` list."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (ValueError, TypeError):
            continue
        lines: list[str] = []
        for entry in data if isinstance(data, list) else [data]:
            if not isinstance(entry, dict):
                continue
            spec = entry.get("openingHours") or entry.get("openingHoursSpecification")
            if isinstance(spec, str):
                lines.append(spec)
            elif isinstance(spec, list):
                for item in spec:
                    if isinstance(item, str):
                        lines.append(item)
                    elif isinstance(item, dict):
                        days = item.get("dayOfWeek")
                        if isinstance(days, list):
                            days_label = "/".join(_short_day(d) for d in days)
                        elif isinstance(days, str):
                            days_label = _short_day(days)
                        else:
                            days_label = ""
                        opens, closes = item.get("opens", ""), item.get("closes", "")
                        if days_label and opens and closes:
                            lines.append(f"{days_label}: {opens} - {closes}")
        if lines:
            return lines[:MAX_HOURS_LINES]
    return []


def _hours_from_text(soup: BeautifulSoup) -> list[str]:
    """Fallback when there's no JSON-LD: scan for lines that look like
    "Monday - Friday: 9am - 5pm", preferring a hinted hours/opening-times
    container over the whole page if one exists."""
    blocks = [
        el.get_text(" ", strip=True)
        for hint in _HOURS_HINTS
        for el in soup.find_all(attrs={"class": re.compile(hint, re.I)})
    ]
    blocks.append(soup.get_text("\n"))

    lines: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        for raw_line in re.split(r"[\n\r]+", block):
            line = raw_line.strip()
            if line and len(line) <= 80 and line.lower() not in seen and _HOURS_LINE_RE.search(line):
                lines.append(line)
                seen.add(line.lower())
        if lines:
            break
    return lines[:MAX_HOURS_LINES]


def extract_hours(soup: BeautifulSoup) -> list[str]:
    return _hours_from_json_ld(soup) or _hours_from_text(soup)


def extract_brand_colors(soup: BeautifulSoup) -> list[str]:
    """The site's most-used non-neutral hex colors -- a lightweight,
    dependency-free stand-in for real logo/image color extraction (no
    image-processing library is in this project's dependencies, see
    requirements.txt). Pulled from <style> blocks, inline style="" attrs,
    and a theme-color meta tag; ranked by frequency."""
    sources: list[str] = [tag.get_text() for tag in soup.find_all("style")]
    sources.extend(el["style"] for el in soup.find_all(style=True))
    theme_color = soup.find("meta", attrs={"name": "theme-color"})
    if theme_color and theme_color.get("content"):
        sources.append(theme_color["content"])

    counts: dict[str, int] = {}
    for source in sources:
        for match in _HEX_COLOR_RE.findall(source):
            normalized = match.lower()
            if normalized in _NEUTRAL_HEX:
                continue
            counts[normalized] = counts.get(normalized, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [color for color, _count in ranked[:MAX_BRAND_COLORS]]


def extract_content(soup: BeautifulSoup, base_url: str) -> dict[str, Any]:
    """Everything this module can find on one already-fetched page."""
    return {
        "logo_url": extract_logo_url(soup, base_url),
        "photos": extract_photos(soup, base_url),
        "hours": extract_hours(soup),
        "services": extract_services(soup),
        "reviews": extract_reviews(soup),
        "brand_colors": extract_brand_colors(soup),
    }


def merge_content(primary: dict[str, Any], addition: dict[str, Any]) -> dict[str, Any]:
    """Combine results from the homepage (`primary`) with a subpage
    (`addition`) the same way lead_agent.py merges email/phone/testimonial
    across subpages: keep the first non-empty scalar, extend+dedupe lists
    up to their caps."""
    merged: dict[str, Any] = dict(primary)
    merged["logo_url"] = primary.get("logo_url") or addition.get("logo_url")
    merged["hours"] = primary.get("hours") or addition.get("hours") or []
    for list_key, cap in (
        ("photos", MAX_PHOTOS),
        ("services", MAX_SERVICES),
        ("reviews", MAX_REVIEWS),
        ("brand_colors", MAX_BRAND_COLORS),
    ):
        combined = list(primary.get(list_key) or [])
        seen = {item.lower() for item in combined}
        for item in addition.get(list_key) or []:
            if item.lower() not in seen:
                combined.append(item)
                seen.add(item.lower())
        merged[list_key] = combined[:cap]
    return merged


def serialize_for_db(content: dict[str, Any]) -> dict[str, str]:
    """JSON-encode the list fields for storage as `leads` TEXT columns.
    Empty/missing fields are stored as "" (not "null"/"[]") so
    `load_content` treats "never scraped" and "scraped, found nothing" the
    same way."""
    return {
        "logo_url": content.get("logo_url") or "",
        "photos": json.dumps(content["photos"]) if content.get("photos") else "",
        "hours": json.dumps(content["hours"]) if content.get("hours") else "",
        "scraped_services": json.dumps(content["services"]) if content.get("services") else "",
        "reviews": json.dumps(content["reviews"]) if content.get("reviews") else "",
        "brand_colors": json.dumps(content["brand_colors"]) if content.get("brand_colors") else "",
    }


def _load_list(raw: Optional[str]) -> list:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []


def load_content(lead: dict[str, Any]) -> dict[str, Any]:
    """Safe JSON-decode of a lead's stored scraped-content columns -- same
    defensive pattern as sales_agent._lead_audit for site_audit. Missing or
    corrupt fields decode to their empty default rather than raising, so
    callers never need their own try/except."""
    return {
        "logo_url": (lead.get("logo_url") or "").strip(),
        "photos": _load_list(lead.get("photos")),
        "hours": _load_list(lead.get("hours")),
        "services": _load_list(lead.get("scraped_services")),
        "reviews": _load_list(lead.get("reviews")),
        "brand_colors": _load_list(lead.get("brand_colors")),
    }
