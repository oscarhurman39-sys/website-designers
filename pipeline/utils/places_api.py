"""Google Places API (New) client for business research.

Used instead of scraping Google Maps directly: Google Maps Platform's
Terms of Service explicitly prohibit it ("Customer will not export,
extract, or otherwise scrape Google Maps Content for use outside the
Services" -- Maps Platform ToS 3.2.3, and separately: "any automated means
(such as harvesting bots, robots, spiders, or scrapers)" is disallowed
outright). Maps listing pages are also a JS-rendered SPA that plain
requests+BeautifulSoup can't meaningfully parse -- the useful content
isn't in the initial server response. This module talks to the real,
documented, ToS-compliant Places API (New) instead.

Requires config.GOOGLE_PLACES_API_KEY. Get one at
https://console.cloud.google.com/google/maps-apis -- note opening hours
are billed at the "Enterprise" SKU tier, higher than the base tier; check
current pricing before high-volume use.

NOTE: the exact request/response shape here was verified against Google's
published Places API (New) reference docs, not against a live call --
this environment had no route to places.googleapis.com to test against.
Confirm it against a real API key before relying on it in production
(tests/test_pipeline_real.py's LeadAgent step is a natural place to add
that coverage).
"""
from __future__ import annotations

from typing import Any, Optional

import requests

import config

_API_BASE = "https://places.googleapis.com/v1"
_REQUEST_TIMEOUT = 15

# Field masks: request only the fields we actually use, since Places API
# bills per requested field group, not a flat per-call rate.
_SEARCH_FIELD_MASK = "places.id,places.displayName"
_DETAILS_FIELD_MASK = ",".join(
    [
        "id",
        "displayName",
        "nationalPhoneNumber",
        "formattedAddress",
        "websiteUri",
        "types",
        "rating",
        "userRatingCount",
        "regularOpeningHours.weekdayDescriptions",
        "reviews.text",
        "reviews.rating",
        "editorialSummary",
        "photos.name",
    ]
)


def _headers(field_mask: str) -> dict[str, str]:
    if not config.GOOGLE_PLACES_API_KEY:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not configured.")
    return {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": field_mask,
    }


def _find_place_id(business_name: str, location: str) -> Optional[str]:
    """Text Search (New): resolve a business name + location to a place id."""
    query = f"{business_name} {location}".strip()
    resp = requests.post(
        f"{_API_BASE}/places:searchText",
        headers=_headers(_SEARCH_FIELD_MASK),
        json={"textQuery": query},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    places = resp.json().get("places", [])
    return places[0]["id"] if places else None


def _get_place_details(place_id: str) -> dict[str, Any]:
    """Place Details (New): full listing data for an already-resolved place id."""
    resp = requests.get(
        f"{_API_BASE}/places/{place_id}",
        headers=_headers(_DETAILS_FIELD_MASK),
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _normalize(details: dict[str, Any]) -> dict[str, Any]:
    """Reshape a raw Place Details (New) response into the flat fields
    lead_agent.py actually stores in the leads table."""
    name = (details.get("displayName") or {}).get("text", "")
    opening_hours = (details.get("regularOpeningHours") or {}).get("weekdayDescriptions", [])
    reviews = [
        (r.get("text") or {}).get("text", "")
        for r in details.get("reviews", [])
        if (r.get("text") or {}).get("text")
    ]
    editorial = (details.get("editorialSummary") or {}).get("text", "")
    photo_refs = [p.get("name", "") for p in details.get("photos", []) if p.get("name")]

    return {
        "name": name,
        "phone": details.get("nationalPhoneNumber", ""),
        "address": details.get("formattedAddress", ""),
        "website_uri": details.get("websiteUri", ""),
        "categories": details.get("types", []),
        "rating": details.get("rating"),
        "review_count": details.get("userRatingCount"),
        "opening_hours": opening_hours,
        "reviews": reviews[:3],
        "editorial_summary": editorial,
        "photo_references": photo_refs,
    }


def find_business(business_name: str, location: str) -> Optional[dict[str, Any]]:
    """Look up a business on Google Places by name + location.

    Returns a normalized dict (name, phone, address, website_uri,
    categories, rating, review_count, opening_hours, reviews,
    editorial_summary, photo_references) or None if no matching place was
    found. Raises on API errors (bad key, quota exceeded, network) --
    callers decide how to handle that (lead_agent.py catches broadly and
    marks the lead 'lost' rather than crashing the whole batch).
    """
    place_id = _find_place_id(business_name, location)
    if place_id is None:
        return None
    details = _get_place_details(place_id)
    return _normalize(details)
