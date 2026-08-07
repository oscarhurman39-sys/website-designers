"""Business discovery via the Google Places API (New, v1) Text Search.

One POST per page, up to 20 results a page, paginated with nextPageToken --
no per-place Details calls needed because the field mask asks for
everything the pipeline wants (website, phone, rating, review count,
types) up front. Free monthly credit comfortably covers thousands of
searches; see DEPLOY.md.

Targets the *new* Places API (places.googleapis.com/v1), not the legacy
Maps Places endpoints -- enable "Places API (New)" on the key's project.
"""
from __future__ import annotations

import time
from typing import Any

import requests

import config

_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.websiteUri",
        "places.nationalPhoneNumber",
        "places.rating",
        "places.userRatingCount",
        "places.types",
        "nextPageToken",
    )
)
_PAGE_SIZE = 20
_REQUEST_TIMEOUT = 15
# Google explicitly documents a short delay before a nextPageToken becomes
# valid; without it the next page intermittently returns INVALID_ARGUMENT.
_PAGE_TOKEN_DELAY_SECONDS = 2


def _to_business(place: dict[str, Any]) -> dict[str, Any]:
    return {
        "place_id": place.get("id") or "",
        "name": (place.get("displayName") or {}).get("text") or "",
        "address": place.get("formattedAddress") or "",
        "website": place.get("websiteUri") or "",
        "phone": place.get("nationalPhoneNumber") or "",
        "rating": place.get("rating"),
        "reviews_count": place.get("userRatingCount") or 0,
        "types": place.get("types") or [],
    }


def search_businesses(query: str, limit: int = 60) -> list[dict[str, Any]]:
    """Text-search businesses (e.g. "plumber in Leeds, UK"). Returns up to
    `limit` normalized business dicts. Raises RuntimeError if the API key
    is missing or Google returns an error -- callers decide whether that
    stops the run (CLI) or is logged and skipped (main loop top-up)."""
    if not config.GOOGLE_PLACES_API_KEY:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not configured.")

    headers = {
        "X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": _FIELD_MASK,
        "Content-Type": "application/json",
    }
    results: list[dict[str, Any]] = []
    page_token: str | None = None

    while len(results) < limit:
        body: dict[str, Any] = {"textQuery": query, "pageSize": _PAGE_SIZE}
        if page_token:
            body["pageToken"] = page_token
        resp = requests.post(_SEARCH_URL, json=body, headers=headers, timeout=_REQUEST_TIMEOUT)
        if resp.status_code != 200:
            raise RuntimeError(f"Places search failed (HTTP {resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        for place in data.get("places", []):
            business = _to_business(place)
            if business["name"]:
                results.append(business)
            if len(results) >= limit:
                break
        page_token = data.get("nextPageToken")
        if not page_token or len(results) >= limit:
            break
        time.sleep(_PAGE_TOKEN_DELAY_SECONDS)

    return results[:limit]
