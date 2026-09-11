"""Sourcing must spread a run across towns and trades.

A niche-major walk with no per-search cap filled the whole daily limit from
the first "plumber in Crawley" search -- 20 identical leads, one town's
cooldown, one email a day. The walk is now town-major, round-robin across
every niche x town bucket, capped per bucket, and starts from a different
bucket each day.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
from agents import sourcing_agent


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Exercise iter_candidates' walk alone: no network, no DB, no geo."""
    monkeypatch.setattr(sourcing_agent, "_skip_reason", lambda place: None)
    monkeypatch.setattr(sourcing_agent, "_within_exclusion_zone", lambda c: False)
    monkeypatch.setattr(sourcing_agent, "_already_in_db", lambda c: False)
    monkeypatch.setattr(sourcing_agent, "_to_candidate",
                        lambda place, niche, location: SimpleNamespace(
                            business_name=place["name"], niche=niche, location=location))
    monkeypatch.setattr(sourcing_agent, "_rotation_offset", lambda: 0)
    monkeypatch.setattr(config, "SOURCING_PER_BUCKET", 2)


def _api(monkeypatch, pages_by_query: dict, calls: list | None = None):
    def fake_iter_places(query):
        if calls is not None:
            calls.append(query)
        for i, name in enumerate(pages_by_query.get(query, [])):
            yield {"id": f"{query}#{i}", "name": name}
    monkeypatch.setattr(sourcing_agent, "_iter_places", fake_iter_places)


def test_a_single_search_yields_at_most_per_bucket(monkeypatch):
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Crawley"])
    _api(monkeypatch, {"plumber in Crawley": [f"Plumber {i}" for i in range(20)]})
    got = list(sourcing_agent.iter_candidates())
    assert [c.business_name for c in got] == ["Plumber 0", "Plumber 1"]


def test_a_run_spreads_across_towns_and_trades(monkeypatch):
    monkeypatch.setattr(config, "SOURCING_PER_BUCKET", 1)
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber", "cafe"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Crawley", "Leeds"])
    _api(monkeypatch, {
        "plumber in Crawley": ["CP1", "CP2", "CP3"], "cafe in Crawley": ["CC1", "CC2"],
        "plumber in Leeds": ["LP1", "LP2"], "cafe in Leeds": ["LC1"],
    })
    got = list(sourcing_agent.iter_candidates(limit=4))
    assert {(c.niche, c.location) for c in got} == {
        ("plumber", "Crawley"), ("cafe", "Crawley"), ("plumber", "Leeds"), ("cafe", "Leeds")}
    # Town-major: both Crawley trades before either Leeds one.
    assert [c.location for c in got] == ["Crawley", "Crawley", "Leeds", "Leeds"]


def test_the_starting_bucket_rotates(monkeypatch):
    monkeypatch.setattr(config, "SOURCING_PER_BUCKET", 1)
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Crawley", "Leeds", "York"])
    _api(monkeypatch, {"plumber in Crawley": ["C"], "plumber in Leeds": ["L"], "plumber in York": ["Y"]})
    monkeypatch.setattr(sourcing_agent, "_rotation_offset", lambda: 4)  # 4 % 3 == 1
    assert [c.business_name for c in sourcing_agent.iter_candidates()] == ["L", "Y", "C"]


def test_capped_buckets_do_not_fetch_further_pages(monkeypatch):
    """The per-bucket break must stop consuming the lazy page iterator, or
    the cap saves nothing on the Places bill."""
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Crawley"])
    pulled = []
    def fake_iter_places(query):
        for i in range(50):
            pulled.append(i)
            yield {"id": f"p{i}", "name": f"P{i}"}
    monkeypatch.setattr(sourcing_agent, "_iter_places", fake_iter_places)
    assert len(list(sourcing_agent.iter_candidates())) == 2
    assert len(pulled) == 2


def test_zero_per_bucket_means_uncapped(monkeypatch):
    monkeypatch.setattr(config, "SOURCING_PER_BUCKET", 0)
    monkeypatch.setattr(config, "SOURCING_NICHES", ["plumber"])
    monkeypatch.setattr(config, "SOURCING_LOCATIONS", ["Crawley"])
    _api(monkeypatch, {"plumber in Crawley": [f"P{i}" for i in range(7)]})
    assert len(list(sourcing_agent.iter_candidates())) == 7
