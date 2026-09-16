"""The daily sourcing limit counts leads inserted, not Places requests. Once
the niche x town grid is mostly sourced, a run finds nothing new and used to
walk every bucket anyway: hundreds of billed Text Search requests an hour for
zero leads. SOURCING_MAX_REQUESTS_PER_RUN caps a run, and a 429 ends it.
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import config
from agents import sourcing_agent


@pytest.fixture
def grid(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber", "electrician"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", [f"Town {i}" for i in range(40)])
    monkeypatch.setattr(config, "SOURCING_PER_BUCKET", 2)
    monkeypatch.setattr(config, "SOURCING_MAX_REQUESTS_PER_RUN", 5)
    monkeypatch.setattr(sourcing_agent, "_rotation_offset", lambda: 0)
    monkeypatch.setattr(sourcing_agent, "_skip_reason", lambda place: None)
    monkeypatch.setattr(sourcing_agent, "_within_exclusion_zone", lambda candidate: False)
    # A saturated grid: every business the API returns is already a lead.
    monkeypatch.setattr(sourcing_agent, "_already_in_db", lambda candidate: True)


def _fake_post(calls: list[str]):
    def post(url, json=None, headers=None, timeout=None):
        calls.append(json["textQuery"])
        n = len(calls)
        resp = Mock()
        resp.status_code = 200
        resp.json = lambda: {
            "places": [{"id": f"p{n}", "displayName": {"text": f"Biz {n}"},
                        "businessStatus": "OPERATIONAL", "websiteUri": "https://biz.example"}],
            "nextPageToken": None,
        }
        return resp
    return post


def test_a_saturated_run_stops_at_the_request_cap(grid, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(sourcing_agent.requests, "post", _fake_post(calls))

    assert list(sourcing_agent.iter_candidates(12)) == []

    assert len(calls) == 5   # 80 buckets were available; only the cap's worth were searched


def test_the_cap_does_not_cut_a_normal_run_short(grid, monkeypatch):
    monkeypatch.setattr(sourcing_agent, "_already_in_db", lambda candidate: False)
    calls: list[str] = []
    monkeypatch.setattr(sourcing_agent.requests, "post", _fake_post(calls))

    found = list(sourcing_agent.iter_candidates(4))

    assert len(found) == 4
    assert len(calls) <= 5


def test_a_quota_error_ends_the_run_instead_of_walking_the_grid(grid, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_MAX_REQUESTS_PER_RUN", 0)   # uncapped: only the 429 stops it
    calls: list[str] = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append(json["textQuery"])
        resp = Mock()
        resp.status_code = 429
        resp.json = lambda: {"error": {"message": "Quota exceeded for quota metric 'Text Search'"}}
        return resp
    monkeypatch.setattr(sourcing_agent.requests, "post", post)

    assert list(sourcing_agent.iter_candidates(12)) == []
    assert len(calls) == 1
