"""SourcingAgent tests. The Places API call is always monkeypatched -- no
test here makes a network request -- and every test gets a fresh
throwaway SQLite file so runs can't see each other's leads."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from unittest.mock import Mock

import pytest
import requests

import config
from agents import sourcing_agent
from utils import db


def _place(
    place_id: str,
    name: str,
    website: str | None = "https://example.com",
    status: str = "OPERATIONAL",
    rating: float = 3.9,
    reviews: int = 12,
    phone: str = "01883 000000",
) -> dict[str, Any]:
    place: dict[str, Any] = {
        "id": place_id,
        "displayName": {"text": name},
        "formattedAddress": f"1 High St, {name}",
        "businessStatus": status,
        "rating": rating,
        "userRatingCount": reviews,
        "nationalPhoneNumber": phone,
    }
    if website is not None:
        place["websiteUri"] = website
    return place


def _fake_post(pages_by_query: dict[str, list[list[dict[str, Any]]]]) -> Callable[..., Mock]:
    """Stand-in for requests.post: serves `pages_by_query[textQuery]` one
    page per call, using the page index as the nextPageToken."""
    calls: list[dict[str, Any]] = []

    def post(url: str, json: dict[str, Any], headers: dict[str, str], timeout: int) -> Mock:
        calls.append({"url": url, "json": json, "headers": headers})
        pages = pages_by_query.get(json["textQuery"], [[]])
        index = int(json.get("pageToken") or 0)
        body: dict[str, Any] = {"places": pages[index]}
        if index + 1 < len(pages):
            body["nextPageToken"] = str(index + 1)
        resp = Mock()
        resp.status_code = 200
        resp.json = Mock(return_value=body)
        return resp

    post.calls = calls  # type: ignore[attr-defined]
    return post


# Kept next to the tests rather than under pytest's tmp_path: the repo
# already uses gitignored throwaway *.db files (see conftest.py), and the
# system temp dir isn't writable in every environment this runs in.
_TEST_DB = Path(__file__).resolve().parent / "leads.sourcing-test.db"


@pytest.fixture
def sourcing_env(monkeypatch):
    _TEST_DB.unlink(missing_ok=True)
    monkeypatch.setattr(config, "DB_PATH", str(_TEST_DB))
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Oxted, Surrey"])
    monkeypatch.setattr(config, "SOURCING_DAILY_LIMIT", 30)
    monkeypatch.setattr(config, "SOURCING_REQUIRE_WEBSITE", True)
    # Bypass the trace span so tests don't write a traces.json file.
    monkeypatch.setattr(sourcing_agent.tracer, "run_traced", lambda **kwargs: kwargs["fn"]())
    db.init_db()
    yield
    _TEST_DB.unlink(missing_ok=True)


def _use_api(monkeypatch, pages_by_query):
    post = _fake_post(pages_by_query)
    monkeypatch.setattr(sourcing_agent.requests, "post", post)
    return post


def test_source_leads_inserts_operational_businesses_with_websites(sourcing_env, monkeypatch):
    post = _use_api(
        monkeypatch,
        {
            "plumber in Oxted, Surrey": [
                [
                    _place("p1", "Oxted Plumbing", website="https://oxtedplumbing.co.uk", rating=4.2, reviews=31),
                    _place("p2", "Closed Plumbers", status="CLOSED_PERMANENTLY"),
                ]
            ]
        },
    )

    assert sourcing_agent.source_leads() == 1

    leads = db.list_leads_by_status("new")
    assert [lead["business_name"] for lead in leads] == ["Oxted Plumbing"]
    lead = leads[0]
    assert lead["niche"] == "plumber"
    assert lead["location"] == "Oxted, Surrey"
    assert lead["website_url"] == "https://oxtedplumbing.co.uk"
    assert lead["phone"] == "01883 000000"
    assert lead["place_id"] == "p1"
    assert '"rating": 4.2' in lead["notes"] and '"review_count": 31' in lead["notes"]

    request = post.calls[0]
    assert request["url"] == sourcing_agent.PLACES_TEXT_SEARCH_URL
    assert request["headers"]["X-Goog-Api-Key"] == "test-key"
    assert "places.websiteUri" in request["headers"]["X-Goog-FieldMask"]
    assert "nextPageToken" in request["headers"]["X-Goog-FieldMask"]
    assert request["json"] == {"textQuery": "plumber in Oxted, Surrey", "pageSize": 20}


def test_website_filter_respects_sourcing_require_website(sourcing_env, monkeypatch):
    pages = {"plumber in Oxted, Surrey": [[_place("p1", "No Site Plumbing", website=None)]]}
    _use_api(monkeypatch, pages)

    assert sourcing_agent.source_leads() == 0
    assert db.list_all_leads() == []

    monkeypatch.setattr(config, "SOURCING_REQUIRE_WEBSITE", False)
    assert sourcing_agent.source_leads() == 1
    assert db.list_all_leads()[0]["website_url"] is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/oxtedplumbing",
        "https://m.facebook.com/oxtedplumbing",
        "https://instagram.com/oxtedplumbing",
        "https://linktr.ee/oxtedplumbing",
        "https://www.yell.com/biz/oxted-plumbing",
        "https://www.checkatrade.com/trades/oxtedplumbing",
        "https://business.google.com/site/oxted",
    ],
)
def test_is_platform_host_matches_profile_hosts_and_subdomains(url):
    assert sourcing_agent.is_platform_host(url)


@pytest.mark.parametrize("url", ["https://oxtedplumbing.co.uk", "https://notfacebook.com", "https://facebook.com.example.org"])
def test_is_platform_host_ignores_real_websites(url):
    assert not sourcing_agent.is_platform_host(url)


def test_platform_profile_websites_are_skipped(sourcing_env, monkeypatch):
    _use_api(
        monkeypatch,
        {
            "plumber in Oxted, Surrey": [
                [
                    _place("p1", "Facebook Only", website="https://www.facebook.com/fbonly"),
                    _place("p2", "Real Site", website="https://realsite.co.uk"),
                ]
            ]
        },
    )

    assert sourcing_agent.source_leads() == 1
    assert [lead["business_name"] for lead in db.list_all_leads()] == ["Real Site"]


def test_dedupes_on_place_id_and_on_name_plus_location(sourcing_env, monkeypatch):
    # A lead typed in by hand has no place_id: the name+location fallback must catch it.
    db.insert_lead("manual plumbing", "plumber", "oxted, surrey")

    pages = {
        "plumber in Oxted, Surrey": [
            [
                _place("p1", "Oxted Plumbing"),
                _place("p2", "Manual Plumbing", website="https://manual.co.uk"),
            ]
        ]
    }
    _use_api(monkeypatch, pages)

    assert sourcing_agent.source_leads() == 1
    # Running again must not re-insert the place we already stored.
    assert sourcing_agent.source_leads() == 0
    assert sorted(lead["business_name"] for lead in db.list_all_leads()) == ["Oxted Plumbing", "manual plumbing"]


def test_same_place_across_locations_is_inserted_once(sourcing_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Oxted, Surrey", "Caterham, Surrey"])
    shared = _place("p1", "Border Plumbing")
    _use_api(monkeypatch, {"plumber in Oxted, Surrey": [[shared]], "plumber in Caterham, Surrey": [[shared]]})

    assert sourcing_agent.source_leads() == 1


def test_daily_limit_caps_inserts_and_survives_restarts(sourcing_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_DAILY_LIMIT", 2)
    # Manually added leads (no place_id) must not count against the sourcing cap.
    db.insert_lead("Hand Typed", "plumber", "Somewhere")
    pages = {"plumber in Oxted, Surrey": [[_place(f"p{i}", f"Plumber {i}") for i in range(5)]]}
    _use_api(monkeypatch, pages)

    assert sourcing_agent.source_leads() == 2
    assert db.count_sourced_leads_today() == 2
    # A fresh call (as after a process restart) reads the count back from the DB.
    assert sourcing_agent.source_leads() == 0
    assert len(db.list_all_leads()) == 3


def test_explicit_limit_is_bounded_by_daily_limit(sourcing_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_DAILY_LIMIT", 3)
    pages = {"plumber in Oxted, Surrey": [[_place(f"p{i}", f"Plumber {i}") for i in range(5)]]}
    _use_api(monkeypatch, pages)

    assert sourcing_agent.source_leads(limit=1) == 1
    assert sourcing_agent.source_leads(limit=10) == 2


def test_follows_next_page_token(sourcing_env, monkeypatch):
    post = _use_api(
        monkeypatch,
        {"plumber in Oxted, Surrey": [[_place("p1", "Page One")], [_place("p2", "Page Two")]]},
    )

    assert sourcing_agent.source_leads() == 2
    assert post.calls[1]["json"]["pageToken"] == "1"


def test_api_errors_never_raise(sourcing_env, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Oxted, Surrey", "Reigate, Surrey"])
    good = _fake_post({"plumber in Reigate, Surrey": [[_place("p1", "Reigate Plumbing")]]})

    def flaky_post(url, json, headers, timeout):
        if json["textQuery"].endswith("Oxted, Surrey"):
            raise requests.ConnectionError("boom")
        return good(url, json=json, headers=headers, timeout=timeout)

    monkeypatch.setattr(sourcing_agent.requests, "post", flaky_post)

    # The failed Oxted query is skipped; the Reigate one still yields a lead.
    assert sourcing_agent.source_leads() == 1


def test_non_200_response_is_reported_not_raised(sourcing_env, monkeypatch):
    resp = Mock()
    resp.status_code = 403
    resp.json = Mock(return_value={"error": {"message": "API key not valid"}})
    monkeypatch.setattr(sourcing_agent.requests, "post", Mock(return_value=resp))

    assert sourcing_agent.source_leads() == 0
    assert db.list_all_leads() == []


def test_missing_api_key_skips_without_calling_api(sourcing_env, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "")
    post = Mock()
    monkeypatch.setattr(sourcing_agent.requests, "post", post)

    assert sourcing_agent.source_leads() == 0
    post.assert_not_called()


def test_iter_candidates_is_read_only(sourcing_env, monkeypatch):
    _use_api(monkeypatch, {"plumber in Oxted, Surrey": [[_place("p1", "Dry Run Plumbing")]]})

    candidates = list(sourcing_agent.iter_candidates())

    assert [c.business_name for c in candidates] == ["Dry Run Plumbing"]
    assert db.list_all_leads() == []


def test_main_loop_runs_sourcing_at_most_once_per_hour(monkeypatch):
    import main

    calls: list[int] = []
    monkeypatch.setattr(config, "SOURCING_ENABLED", True)
    monkeypatch.setattr(main.sourcing_agent, "source_leads", lambda: calls.append(1) or 0)
    monkeypatch.setattr(main, "_last_sourcing_at", None)
    now = [1000.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])

    main._maybe_source_leads()
    main._maybe_source_leads()
    assert len(calls) == 1

    now[0] += config.SOURCING_INTERVAL_SECONDS + 1
    main._maybe_source_leads()
    assert len(calls) == 2

    monkeypatch.setattr(config, "SOURCING_ENABLED", False)
    now[0] += config.SOURCING_INTERVAL_SECONDS + 1
    main._maybe_source_leads()
    assert len(calls) == 2
