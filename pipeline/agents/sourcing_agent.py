"""SourcingAgent: discovers new leads automatically via the Google Places
API (New) Text Search, so the pipeline isn't limited to hand-typed leads
and CSV drops.

Why Places rather than a generic web search: the pipeline sells a rebuild
of a *poor* web presence. A normal website can be scraped for an email;
a Facebook/directory-only business can still be worth a preview and a
phone-first follow-up; a business with no listed web presence is kept out
unless SOURCING_REQUIRE_WEBSITE is explicitly disabled.

Everything here is best-effort and must never take the main loop down:
API/network failures are printed and skipped, and source_leads() always
returns the number of leads it actually managed to insert.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from urllib.parse import urlparse

import requests

import config
from utils import db, tracer

PLACES_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Field masks are mandatory on Places API (New) and also drive billing, so
# ask only for what we store. `nextPageToken` has to be in the mask too or
# the API never returns one and paging silently stops after page one.
PLACES_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.businessStatus",
        "places.types",
        "places.rating",
        "places.userRatingCount",
        "places.location",
        "nextPageToken",
    ]
)
PAGE_SIZE = 20  # the API's per-page maximum
MAX_PAGES_PER_QUERY = 3  # Text Search caps out at 60 results per query
REQUEST_TIMEOUT = 15


class PlacesAuthError(RuntimeError):
    """The API key is invalid, or Places API (New) is disabled/unbilled on its
    project. Distinguished from transient failures so one run stops after the
    first such response rather than repeating it for every niche x location."""

# Hosts that Google lists as a business's "website" but which are really a
# social profile or directory entry. These are high-intent leads because the
# business has some online presence but not a proper owned site.
PLATFORM_HOSTS: frozenset[str] = frozenset(
    {"facebook.com", "instagram.com", "linktr.ee", "yell.com", "checkatrade.com", "google.com"}
)

WEBSITE_STATUS_NONE = "none"
WEBSITE_STATUS_PLATFORM_ONLY = "platform_only"
WEBSITE_STATUS_OWNED = "owned_site"
WEBSITE_STATUS_UNSCORED = "unscored"

CONTACT_CHANNEL_EMAIL = "email"
CONTACT_CHANNEL_PHONE = "phone"
CONTACT_CHANNEL_UNKNOWN = "unknown"

# `niche` must name a folder here, otherwise DesignAgent has no template to
# build the preview from. Checked at sourcing time so a typo in
# SOURCING_NICHES shows up in the log rather than as broken previews.
TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"


@dataclass
class Candidate:
    """A Places result that passed every filter and is ready to insert."""

    business_name: str
    niche: str
    location: str
    place_id: str
    website_url: str
    phone: str
    address: str
    rating: Optional[float]
    review_count: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    website_status: str = "unscored"
    contact_channel: str = "unknown"

    def summary(self) -> str:
        """Short JSON blob stored in leads.notes (not scraped_info, which
        LeadAgent overwrites with homepage text) so the rating and review
        count survive research and are visible on the dashboard."""
        return json.dumps(
            {
                "source": "google_places",
                "rating": self.rating,
                "review_count": self.review_count,
                "address": self.address,
                "sourced_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    def describe(self) -> str:
        rating = f"{self.rating} ({self.review_count} reviews)" if self.rating is not None else "unrated"
        return (
            f"{self.business_name} | {self.niche} | {self.location} | "
            f"{self.website_url or '-'} | {self.phone or '-'} | {rating}"
        )


def _host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_platform_host(url: str) -> bool:
    """True if `url` lives on one of PLATFORM_HOSTS or any subdomain of one
    (m.facebook.com, business.google.com, ...)."""
    host = _host_of(url)
    return any(host == platform or host.endswith("." + platform) for platform in PLATFORM_HOSTS)


def website_status_for_url(url: str) -> str:
    """Classify the business's listed web presence from Google Places."""
    url = (url or "").strip()
    if not url:
        return WEBSITE_STATUS_NONE
    if is_platform_host(url):
        return WEBSITE_STATUS_PLATFORM_ONLY
    return WEBSITE_STATUS_OWNED


def contact_channel_for_place(place: dict[str, Any]) -> str:
    """Best known follow-up channel before LeadAgent tries website scraping."""
    if (place.get("websiteUri") or "").strip() and not is_platform_host(place.get("websiteUri") or ""):
        return CONTACT_CHANNEL_EMAIL
    if (place.get("nationalPhoneNumber") or "").strip():
        return CONTACT_CHANNEL_PHONE
    return CONTACT_CHANNEL_UNKNOWN


def site_score_for_status(website_status: str) -> Optional[int]:
    """Lower = weaker current web presence. None means not inspected yet."""
    if website_status in (WEBSITE_STATUS_NONE, WEBSITE_STATUS_PLATFORM_ONLY):
        return 0
    return None


def lead_score_for_candidate(website_status: str, contact_channel: str) -> Optional[int]:
    """Deterministic first-pass acquisition score.

    The score favours obvious need first, then reachability. It intentionally
    ignores rating/review count for now because those need a wider rubric and
    tests before they influence who gets contacted.
    """
    if website_status == WEBSITE_STATUS_NONE:
        return 10 if contact_channel != CONTACT_CHANNEL_UNKNOWN else 8
    if website_status == WEBSITE_STATUS_PLATFORM_ONLY:
        return 9 if contact_channel != CONTACT_CHANNEL_UNKNOWN else 7
    return None


def _skip_reason(place: dict[str, Any]) -> Optional[str]:
    """Why this raw Places result shouldn't become a lead, or None if it should."""
    if place.get("businessStatus") != "OPERATIONAL":
        return f"business status {place.get('businessStatus') or 'unknown'}"
    if not place.get("id"):
        return "no place id"
    if not (place.get("displayName") or {}).get("text", "").strip():
        return "no display name"
    website = (place.get("websiteUri") or "").strip()
    if not website and config.SOURCING_REQUIRE_WEBSITE:
        return "no website"
    return None


def _search_page(text_query: str, page_token: Optional[str] = None) -> dict[str, Any]:
    """One Text Search request. Raises on transport errors or a non-200
    reply, surfacing Google's own error message rather than the raw body."""
    body: dict[str, Any] = {"textQuery": text_query, "pageSize": PAGE_SIZE}
    if page_token:
        body["pageToken"] = page_token
    resp = requests.post(
        PLACES_TEXT_SEARCH_URL,
        json=body,
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
            "X-Goog-FieldMask": PLACES_FIELD_MASK,
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code != 200:
        try:
            message = resp.json().get("error", {}).get("message", "")
        except ValueError:
            message = (resp.text or "")[:200]
        if resp.status_code in (401, 403):
            # Key/project problem: every remaining query will fail the same
            # way, so abort the whole run instead of logging 40 identical lines.
            raise PlacesAuthError(f"Places API HTTP {resp.status_code}: {message}")
        raise RuntimeError(f"Places API HTTP {resp.status_code}: {message}")
    return resp.json()


def _iter_places(text_query: str) -> Iterator[dict[str, Any]]:
    """Yield raw place dicts for `text_query`, following nextPageToken up to
    MAX_PAGES_PER_QUERY pages."""
    page_token: Optional[str] = None
    for _ in range(MAX_PAGES_PER_QUERY):
        data = _search_page(text_query, page_token)
        yield from data.get("places", [])
        page_token = data.get("nextPageToken")
        if not page_token:
            return


def _miles_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles (haversine)."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _within_exclusion_zone(candidate: "Candidate") -> bool:
    """True if the business sits inside SOURCING_EXCLUDE_RADIUS_MILES of
    SOURCING_EXCLUDE_CENTER (your home patch, where you'd rather not cold
    email people you might bump into). A candidate with no coordinates is
    NOT excluded -- the search location itself is already far enough away."""
    if not config.SOURCING_EXCLUDE_CENTER or config.SOURCING_EXCLUDE_RADIUS_MILES <= 0:
        return False
    if candidate.latitude is None or candidate.longitude is None:
        return False
    lat, lon = config.SOURCING_EXCLUDE_CENTER
    return _miles_between(lat, lon, candidate.latitude, candidate.longitude) < config.SOURCING_EXCLUDE_RADIUS_MILES


def _to_candidate(place: dict[str, Any], niche: str, location: str) -> Candidate:
    website_url = (place.get("websiteUri") or "").strip()
    website_status = website_status_for_url(website_url)
    contact_channel = contact_channel_for_place(place)
    return Candidate(
        business_name=place["displayName"]["text"].strip(),
        niche=niche,
        location=location,
        place_id=place["id"],
        website_url=website_url,
        phone=(place.get("nationalPhoneNumber") or "").strip(),
        address=place.get("formattedAddress") or "",
        rating=place.get("rating"),
        review_count=int(place.get("userRatingCount") or 0),
        latitude=(place.get("location") or {}).get("latitude"),
        longitude=(place.get("location") or {}).get("longitude"),
        website_status=website_status,
        contact_channel=contact_channel,
    )


def _already_in_db(candidate: Candidate) -> bool:
    """Dedupe on the Google place id first; fall back to a case-insensitive
    name+location match so a lead someone typed in by hand (no place_id)
    doesn't get a second copy."""
    return db.place_id_exists(candidate.place_id) or db.lead_exists_by_name_and_location(
        candidate.business_name, candidate.location
    )


def iter_candidates(limit: Optional[int] = None) -> Iterator[Candidate]:
    """Lazily yield insert-ready candidates across every niche x location
    query, stopping after `limit` (None = no limit).

    Read-only: nothing is written here, which is what lets the CLI's
    --dry-run share this exact code path with the real run. A failing
    query is printed and skipped so one bad search doesn't waste the rest.
    """
    if limit is not None and limit <= 0:
        return
    yielded = 0
    skipped: dict[str, int] = {}
    seen_place_ids: set[str] = set()  # the same business shows up for neighbouring towns

    for niche in config.SOURCING_NICHES:
        if not (TEMPLATES_DIR / niche).is_dir():
            print(f"[sourcing] WARNING: no templates/{niche}/ folder; DesignAgent will fall back for these leads")
        for location in config.SOURCING_LOCATIONS:
            query = f"{niche} in {location}"
            try:
                for place in _iter_places(query):
                    reason = _skip_reason(place)
                    if reason:
                        skipped[reason] = skipped.get(reason, 0) + 1
                        continue
                    if place["id"] in seen_place_ids:
                        continue
                    seen_place_ids.add(place["id"])
                    candidate = _to_candidate(place, niche, location)
                    if _within_exclusion_zone(candidate):
                        key = f"within {config.SOURCING_EXCLUDE_RADIUS_MILES:g} miles of home"
                        skipped[key] = skipped.get(key, 0) + 1
                        continue
                    if _already_in_db(candidate):
                        skipped["already in DB"] = skipped.get("already in DB", 0) + 1
                        continue
                    yield candidate
                    yielded += 1
                    if limit is not None and yielded >= limit:
                        _print_skip_summary(skipped)
                        return
            except PlacesAuthError as exc:
                print(f"[sourcing] Aborting run -- {exc}")
                _print_skip_summary(skipped)
                return
            except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
                print(f"[sourcing] Query {query!r} failed: {exc}")
    _print_skip_summary(skipped)


def _print_skip_summary(skipped: dict[str, int]) -> None:
    if skipped:
        detail = ", ".join(f"{reason}: {n}" for reason, n in sorted(skipped.items()))
        print(f"[sourcing] Skipped -- {detail}")


def remaining_today(limit: Optional[int] = None) -> int:
    """How many more leads sourcing may insert right now: the daily cap
    minus what's already been sourced today (counted from the DB, so a
    restart mid-day can't blow through it), further bounded by `limit`."""
    remaining = max(0, config.SOURCING_DAILY_LIMIT - db.count_sourced_leads_today())
    if limit is not None:
        remaining = min(remaining, max(0, limit))
    return remaining


def _insert(candidate: Candidate) -> int:
    lead_id = db.insert_lead(candidate.business_name, candidate.niche, candidate.location, status="new")
    db.update_lead_fields(
        lead_id,
        place_id=candidate.place_id,
        website_url=candidate.website_url or None,
        website_status=candidate.website_status,
        site_score=site_score_for_status(candidate.website_status),
        lead_score=lead_score_for_candidate(candidate.website_status, candidate.contact_channel),
        contact_channel=candidate.contact_channel,
        phone=candidate.phone or None,
        address=candidate.address or None,
        google_rating=candidate.rating,
        google_reviews_count=candidate.review_count or None,
        notes=candidate.summary(),
    )
    return lead_id


def source_leads(limit: Optional[int] = None) -> int:
    """Discover and insert new 'new'-status leads. Returns how many were
    inserted. Wrapped in a trace span like the other agents so sourcing
    runs show up on the dashboard; the logic lives in _source_leads_impl."""
    return tracer.run_traced(
        agent_id="sourcing-agent",
        agent_name="LeadSourcer",
        tool_name="google_places_text_search",
        input_data={
            "limit": limit,
            "niches": list(config.SOURCING_NICHES),
            "locations": list(config.SOURCING_LOCATIONS),
        },
        fn=lambda: _source_leads_impl(limit),
    )


def _source_leads_impl(limit: Optional[int] = None) -> int:
    if not config.GOOGLE_PLACES_API_KEY:
        print("[sourcing] GOOGLE_PLACES_API_KEY is not set; skipping lead sourcing.")
        return 0

    budget = remaining_today(limit)
    if budget == 0:
        print(f"[sourcing] Daily sourcing limit ({config.SOURCING_DAILY_LIMIT}) already reached; nothing to do.")
        return 0

    inserted = 0
    try:
        for candidate in iter_candidates(budget):
            lead_id = _insert(candidate)
            inserted += 1
            print(f"[sourcing] Added lead {lead_id}: {candidate.describe()}")
    except Exception as exc:  # noqa: BLE001 - sourcing is optional; never take the loop down
        print(f"[sourcing] Aborted after {inserted} insert(s): {exc}")
    print(f"[sourcing] Inserted {inserted} new lead(s) (budget was {budget}).")
    return inserted
