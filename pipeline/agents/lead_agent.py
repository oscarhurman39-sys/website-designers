"""LeadAgent: turns a CSV of (business_name, niche, location) rows into
enriched leads with business details (phone, address, hours, categories,
rating, reviews) from Google Places, plus a contact email.

Business data comes from the Google Places API (New), not from scraping
Google Maps directly: Google Maps Platform's Terms of Service explicitly
prohibit scraping Maps/Places content, and Maps listing pages are a
JS-rendered SPA that plain requests+BeautifulSoup can't meaningfully parse
anyway (see utils/places_api.py). Places has no email field at all, though
-- when it gives us the business's own website, we reuse the same
robots.txt-respecting scraper this module always used (find_business_website
/ _fetch / _extract_email, all unchanged) just to find a contact email
there.

This is intentionally best-effort: a Places lookup can fail to match, and
a business's own website (if it has one) can still lack a discoverable
email. A lead is marked 'researched' once we have at least a name and
phone number from Places, 'lost' otherwise -- contact_email may still be
blank on a 'researched' lead; sales_agent.py already handles that
gracefully at send time.

Two shortcuts keep leads that are already actionable from ever being
marked 'lost' here:
  * If the lead already has a contact_email (from CSV import or manual
    entry), the website email-scrape is skipped entirely -- we only ever
    scraped a site to *find* an email, and we already have one -- and the
    lead goes straight to 'researched' regardless of what Places returns.
  * The Google Places lookup is optional. With no GOOGLE_PLACES_API_KEY
    configured we skip it and just use whatever data is already on the
    lead, so the rest of the pipeline can run without a Places key.
"""
from __future__ import annotations

import csv
import json
import re
import urllib.robotparser
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config
from utils import db, places_api, tracer

USER_AGENT = "ColdEmailSalesPipelineBot/1.0 (+mailto:contact@example.com)"
REQUEST_TIMEOUT = 10
REQUEST_HEADERS = {"User-Agent": USER_AGENT}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Common filler addresses we never want to treat as a real contact.
_EMAIL_BLOCKLIST_SUBSTR = ("example.com", "sentry.io", "wixpress.com", "godaddy.com", "yourdomain")

_TESTIMONIAL_HINTS = ("testimonial", "review", "quote", "client-says")
_PAIN_POINT_HINTS = ("blog", "news", "about", "why-", "services")

# Established-business targeting gate: a Google Places listing must have at
# least this many ratings AND at least one photo to be worth pursuing.
# Listings below the bar are set aside as 'filtered' rather than emailed.
_MIN_REVIEWS = 5


@dataclass
class CsvRow:
    business_name: str
    niche: str
    location: str


def read_csv(csv_path: str) -> list[CsvRow]:
    """Parse the input CSV. Required columns: business_name, niche, location."""
    rows: list[CsvRow] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"business_name", "niche", "location"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required column(s): {missing}")
        for row in reader:
            rows.append(
                CsvRow(
                    business_name=row["business_name"].strip(),
                    niche=row["niche"].strip().lower(),
                    location=row["location"].strip(),
                )
            )
    return rows


def ingest_csv(csv_path: str) -> list[int]:
    """Insert every row as a 'new' lead. Returns the list of new lead ids."""
    lead_ids = []
    for row in read_csv(csv_path):
        lead_id = db.insert_lead(row.business_name, row.niche, row.location, status="new")
        lead_ids.append(lead_id)
    return lead_ids


def _robots_allows(url: str) -> bool:
    """Check robots.txt for `url` before scraping it. Fails open only when
    robots.txt itself can't be fetched (i.e. no explicit disallow found)."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        parser.read()
    except Exception:
        return True
    return parser.can_fetch(USER_AGENT, url)


def find_business_website(business_name: str, location: str) -> Optional[str]:
    """Best-effort discovery of a business's website via DuckDuckGo's
    no-JS HTML endpoint (no API key required). Returns the first plausible
    result URL, or None.
    """
    query = f"{business_name} {location}"
    search_url = "https://html.duckduckgo.com/html/"
    if not _robots_allows(search_url):
        return None
    try:
        resp = requests.get(
            search_url, params={"q": query}, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for link in soup.select("a.result__a"):
        href = link.get("href", "")
        if href and "duckduckgo.com" not in href:
            return href
    return None


def _fetch(url: str) -> Optional[BeautifulSoup]:
    if not _robots_allows(url):
        return None
    try:
        resp = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _extract_email(soup: BeautifulSoup) -> Optional[str]:
    for a in soup.select("a[href^=mailto]"):
        addr = a["href"].split("mailto:")[-1].split("?")[0].strip()
        if addr and not any(bad in addr.lower() for bad in _EMAIL_BLOCKLIST_SUBSTR):
            return addr
    text = soup.get_text(" ")
    for match in _EMAIL_RE.findall(text):
        if not any(bad in match.lower() for bad in _EMAIL_BLOCKLIST_SUBSTR):
            return match
    return None


def _extract_testimonial(soup: BeautifulSoup) -> Optional[str]:
    for hint in _TESTIMONIAL_HINTS:
        for el in soup.find_all(attrs={"class": re.compile(hint, re.I)}):
            text = el.get_text(" ", strip=True)
            if 20 <= len(text) <= 400:
                return text
    return None


def _extract_pain_point(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """Grab a snippet (first substantial paragraph) that hints at what the
    business struggles with or emphasizes -- used to personalize the cold
    email. Falls back to the homepage's meta description."""
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        if 40 <= len(text) <= 300:
            return text
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    return None


def _find_subpage(soup: BeautifulSoup, base_url: str, hints: tuple[str, ...]) -> Optional[str]:
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(h in href for h in hints):
            return urljoin(base_url, a["href"])
    return None


def research_lead(lead: dict) -> None:
    """Run the full research step for a single lead dict (as returned by
    db.get_lead) and persist results directly to the DB. Wrapped in a
    trace -> agent -> tool span (see utils/tracer.py); the actual research
    logic lives untouched in _research_lead_impl."""
    tracer.run_traced(
        agent_id="lead-agent",
        agent_name="LeadResearcher",
        tool_name="google_places_lookup",
        input_data={"lead_id": lead["id"], "business_name": lead["business_name"], "niche": lead["niche"]},
        fn=lambda: _research_lead_impl(lead),
    )


def _research_lead_impl(lead: dict) -> None:
    lead_id = lead["id"]
    # A lead that already has an email (CSV/manual) is workable no matter
    # what research turns up -- we track that here so none of the paths
    # below can mark it 'lost'.
    existing_email = (lead.get("contact_email") or "").strip()

    # Google Places research is optional: without an API key we skip the
    # lookup entirely and rely on whatever data is already on the lead.
    place = None
    if config.GOOGLE_PLACES_API_KEY:
        try:
            place = places_api.find_business(lead["business_name"], lead["location"] or "")
        except Exception as exc:  # noqa: BLE001 - API/auth/quota errors must not crash the batch
            if existing_email:
                db.update_lead_status(
                    lead_id, "researched", notes=f"Kept existing contact email; Places lookup failed: {exc}"
                )
                return
            db.update_lead_status(lead_id, "lost", notes=f"Google Places lookup failed: {exc}")
            return

    if place is None:
        # No Places match, or no API key configured. If we already have an
        # email the lead is still actionable; otherwise there's nothing to
        # work with.
        if existing_email:
            note = (
                "No matching Google Places listing; kept existing contact email"
                if config.GOOGLE_PLACES_API_KEY
                else "No Places API key configured; using existing lead data"
            )
            db.update_lead_status(lead_id, "researched", notes=note)
            return
        note = (
            "No matching Google Places listing found"
            if config.GOOGLE_PLACES_API_KEY
            else "No Places API key configured and no existing contact email"
        )
        db.update_lead_status(lead_id, "lost", notes=note)
        return

    # Established-business targeting gate: only pursue listings with enough
    # ratings to be real and at least one photo. This runs on the Places
    # result regardless of any pre-existing email -- a weak listing is set
    # aside as 'filtered' (skipped by every downstream queue), not emailed.
    review_count = place["review_count"] or 0
    photo_count = len(place["photo_references"])
    if review_count < _MIN_REVIEWS or photo_count == 0:
        db.update_lead_status(
            lead_id,
            "filtered",
            notes=f"Below targeting threshold (ratings={review_count}, photos={photo_count})",
        )
        return

    if not place["name"] or not place["phone"]:
        db.update_lead_fields(
            lead_id,
            address=place["address"] or "",
            categories=", ".join(place["categories"]),
            rating=place["rating"],
            review_count=place["review_count"],
            opening_hours=json.dumps(place["opening_hours"]),
            reviews=json.dumps(place["reviews"]),
            scraped_info=place["editorial_summary"] or "",
        )
        if existing_email:
            db.update_lead_status(
                lead_id, "researched", notes="Places listing missing name/phone; kept existing contact email"
            )
            return
        db.update_lead_status(lead_id, "lost", notes="Places listing found but missing name and/or phone")
        return

    # Places has no email field at all. Only when we DON'T already have an
    # email do we reuse the same robots.txt-respecting scraper this module
    # always used (find_business_website / _fetch / _extract_email /
    # _find_subpage, all unchanged) to find one on the business's own site,
    # falling back to a DuckDuckGo search for the site if Places had no URL.
    email_addr: Optional[str] = existing_email or None
    website_url = place["website_uri"] or ""
    # When we fetch the business's own homepage (only when we don't already
    # have an email), harvest a pain_point from it too -- the first
    # substantial paragraph / meta description of their current site. This
    # restores the pre-Places personalization hook (sales_agent uses it as
    # "something noticed about their current site", design_agent works it
    # into the hero copy). Default to any existing value so we never clobber
    # a pain_point with an empty string when we don't scrape.
    pain_point = lead.get("pain_point") or ""
    if not existing_email:
        website_url = website_url or find_business_website(lead["business_name"], lead["location"] or "")
        if website_url:
            homepage = _fetch(website_url)
            if homepage is not None:
                email_addr = _extract_email(homepage)
                extracted_pain = _extract_pain_point(homepage, website_url)
                if extracted_pain:
                    pain_point = extracted_pain
                if not email_addr:
                    subpage_url = _find_subpage(homepage, website_url, ("contact", "about"))
                    if subpage_url:
                        subpage = _fetch(subpage_url)
                        if subpage is not None:
                            email_addr = _extract_email(subpage)

    db.update_lead_fields(
        lead_id,
        contact_email=email_addr or "",
        website_url=website_url or "",
        phone=place["phone"],
        address=place["address"] or "",
        categories=", ".join(place["categories"]),
        rating=place["rating"],
        review_count=place["review_count"],
        opening_hours=json.dumps(place["opening_hours"]),
        # Review text stays in the DB for the operator's own context only --
        # design_agent renders placeholders, never this content (Google
        # Maps Platform display terms). Deliberately NOT copied into
        # `testimonial` anymore for the same reason.
        reviews=json.dumps(place["reviews"]),
        pain_point=pain_point,
        scraped_info=place["editorial_summary"] or "",
    )
    db.update_lead_status(lead_id, "researched", notes="Research complete via Google Places API")


def run(csv_path: Optional[str] = None) -> None:
    """Main entrypoint called by main.py: ingest a fresh CSV if given, then
    research every lead currently in 'new' status."""
    if csv_path:
        ingest_csv(csv_path)
    for lead in db.list_leads_by_status("new"):
        research_lead(lead)


def _main() -> None:
    """CLI entrypoint: `python -m agents.lead_agent path/to/leads.csv`
    (run from inside the `pipeline/` directory so the flat `utils`/`config`
    imports resolve, same as main.py and webhook_server.py)."""
    import argparse

    parser = argparse.ArgumentParser(description="Ingest and research leads from a CSV file.")
    parser.add_argument("csv_path", help="CSV with columns: business_name, niche, location")
    args = parser.parse_args()

    db.init_db()
    run(args.csv_path)


if __name__ == "__main__":
    _main()
