"""LeadAgent: turns a CSV of (business_name, niche, location) rows into
enriched leads with a contact email, a pain-point snippet, and (if found) a
testimonial, scraped from the business's own website.

This is intentionally best-effort: small local businesses often have thin,
inconsistent websites, so we fall back gracefully at every step rather than
raising. A lead we can't find a contact email for is marked 'lost' rather
than blocking the pipeline.
"""
from __future__ import annotations

import csv
import re
import urllib.robotparser
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from utils import db, retry, tracer

USER_AGENT = "ColdEmailSalesPipelineBot/1.0 (+mailto:contact@example.com)"
REQUEST_TIMEOUT = 10
REQUEST_HEADERS = {"User-Agent": USER_AGENT}

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
# Common filler addresses we never want to treat as a real contact.
_EMAIL_BLOCKLIST_SUBSTR = ("example.com", "sentry.io", "wixpress.com", "godaddy.com", "yourdomain")

_TESTIMONIAL_HINTS = ("testimonial", "review", "quote", "client-says")
_PAIN_POINT_HINTS = ("blog", "news", "about", "why-", "services")

# Domains that constantly outrank a thin/nonexistent business site in local
# search but aren't a site the business actually owns -- a social profile,
# directory listing, or delivery-platform page. Treating one of these as
# "website_url" would (a) make the cold email's "you only have a Google
# listing" claim false, and (b) send us scraping a JS-heavy platform page
# for contact info that was never going to be there. See find_business_website().
_NON_BUSINESS_DOMAINS = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "pinterest.com", "threads.net", "tiktok.com",
    "yelp.com", "yelp.co.uk", "tripadvisor.com", "tripadvisor.co.uk",
    "yell.com", "thomsonlocal.com", "checkatrade.com", "trustpilot.com",
    "bark.com", "google.com", "maps.app.goo.gl", "business.site",
    "justeat.co.uk", "just-eat.co.uk", "ubereats.com", "deliveroo.co.uk",
    "opentable.com", "booksy.com", "treatwell.co.uk", "fresha.com",
    "wixpress.com", "duckduckgo.com",
)

# Cap on how many non-blocklisted search results find_business_website()
# will actually fetch-and-verify before giving up -- keeps a "no real
# website" lead from triggering a long chain of fetches against unrelated
# same-industry sites DuckDuckGo happened to rank nearby.
_MAX_CANDIDATES_CHECKED = 5


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


def _is_business_site_url(url: str) -> bool:
    """False for known social-media/directory/aggregator domains (see
    _NON_BUSINESS_DOMAINS) and anything with no discernible host at all."""
    netloc = urlparse(url).netloc.lower()
    if not netloc:
        return False
    return not any(netloc == d or netloc.endswith("." + d) for d in _NON_BUSINESS_DOMAINS)


_GENERIC_NAME_WORDS = {"ltd", "limited", "llc", "inc", "co", "company", "the", "and"}


def _mentions_business(soup: BeautifulSoup, business_name: str) -> bool:
    """Loose check that a fetched page is plausibly this business's own
    site, not an unrelated same-industry site DuckDuckGo happened to rank
    nearby -- at least one significant word of the business name (skipping
    generic suffixes like 'ltd') must appear in the page title or text."""
    words = [w.strip(".,&").lower() for w in business_name.split()]
    significant = [w for w in words if w and w not in _GENERIC_NAME_WORDS and len(w) > 2]
    if not significant:
        return True  # nothing meaningful to check against -- don't block on it
    title_text = soup.title.get_text() if soup.title else ""
    haystack = f"{title_text} {soup.get_text(' ')}".lower()
    return any(word in haystack for word in significant)


_ddg_search_retry = retry.with_retries(retriable=(requests.RequestException,), label="lead_agent.ddg_search")


@_ddg_search_retry
def _ddg_search(query: str) -> requests.Response:
    resp = requests.get(
        "https://html.duckduckgo.com/html/", params={"q": query}, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp


def find_business_website(business_name: str, location: str) -> Optional[str]:
    """Best-effort discovery of a business's website via DuckDuckGo's
    no-JS HTML endpoint (no API key required).

    Skips social-media/directory/aggregator results outright (see
    _is_business_site_url) rather than returning the first non-DuckDuckGo
    link blindly -- those show up constantly for small local businesses and
    aren't a site they own. Of the remaining candidates (checked up to
    _MAX_CANDIDATES_CHECKED), only returns one whose fetched homepage
    plausibly mentions the business (see _mentions_business), so a
    same-industry site that just happens to rank nearby doesn't get mistaken
    for this business's own site. Returns None if nothing found or nothing
    validates -- which downstream is exactly "no website", the same claim
    the cold email's opening line makes.
    """
    query = f"{business_name} {location}"
    if not _robots_allows("https://html.duckduckgo.com/html/"):
        return None
    try:
        resp = _ddg_search(query)
    except requests.RequestException:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    checked = 0
    for link in soup.select("a.result__a"):
        href = link.get("href", "")
        if not href or not _is_business_site_url(href):
            continue
        if checked >= _MAX_CANDIDATES_CHECKED:
            break
        checked += 1
        candidate = _fetch(href)
        if candidate is not None and _mentions_business(candidate, business_name):
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


def _decode_cloudflare_email(cfemail_hex: str) -> Optional[str]:
    """Decode Cloudflare's automatic email-obfuscation encoding
    (the `data-cfemail` attribute Cloudflare's free tier injects on every
    mailto: link and visible email address). Extremely common on small
    business sites -- without this, _extract_email() silently finds
    nothing on any Cloudflare-protected page, even when a real address is
    right there on the screen. XOR cipher, first hex byte is the key."""
    try:
        key = int(cfemail_hex[:2], 16)
        return "".join(
            chr(int(cfemail_hex[i:i + 2], 16) ^ key)
            for i in range(2, len(cfemail_hex), 2)
        )
    except (ValueError, IndexError):
        return None


def _extract_email(soup: BeautifulSoup) -> Optional[str]:
    for el in soup.select("[data-cfemail]"):
        decoded = _decode_cloudflare_email(el["data-cfemail"])
        if decoded and not any(bad in decoded.lower() for bad in _EMAIL_BLOCKLIST_SUBSTR):
            return decoded
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
        tool_name="scrape_business_website",
        input_data={"lead_id": lead["id"], "business_name": lead["business_name"], "niche": lead["niche"]},
        fn=lambda: _research_lead_impl(lead),
    )


def _research_lead_impl(lead: dict) -> None:
    """Best-effort research: a lead always has at least a business_name/
    niche/location (required at insert time), so there is never a case
    where "absolutely nothing" is known about it. Research failures
    (no website found, fetch blocked, no contact email discovered) push the
    lead forward to 'researched' with whatever partial data is available,
    rather than marking it 'lost' -- DesignAgent can build a site from
    name/niche/location alone, and SalesAgent (the actual point where a
    missing contact_email becomes fatal) is the correct place to mark a
    lead 'lost', not here."""
    lead_id = lead["id"]
    website_url = find_business_website(lead["business_name"], lead["location"] or "")

    if not website_url:
        db.update_lead_status(lead_id, "researched", notes="No website found during research; proceeding with name/niche/location only")
        return

    homepage = _fetch(website_url)
    if homepage is None:
        db.update_lead_fields(lead_id, website_url=website_url)
        db.update_lead_status(
            lead_id, "researched",
            notes=f"Could not fetch website (robots.txt or network): {website_url}; proceeding without scraped data",
        )
        return

    email_addr = _extract_email(homepage)
    testimonial = _extract_testimonial(homepage)
    pain_point = _extract_pain_point(homepage, website_url)

    # If nothing useful on the homepage, try a likely subpage (about/contact/blog).
    if not email_addr or not testimonial or not pain_point:
        subpage_url = _find_subpage(homepage, website_url, ("contact", "about", "blog", "review"))
        if subpage_url:
            subpage = _fetch(subpage_url)
            if subpage is not None:
                email_addr = email_addr or _extract_email(subpage)
                testimonial = testimonial or _extract_testimonial(subpage)
                pain_point = pain_point or _extract_pain_point(subpage, subpage_url)

    db.update_lead_fields(
        lead_id,
        website_url=website_url,
        contact_email=email_addr or "",
        pain_point=pain_point or "",
        testimonial=testimonial or "",
        scraped_info=homepage.get_text(" ", strip=True)[:2000],
    )
    notes = "Research complete" if email_addr else "Website found but no contact email discovered; proceeding without one"
    db.update_lead_status(lead_id, "researched", notes=notes)


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
