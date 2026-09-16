"""The deploy step must return a URL a lead can actually open.

Regression cover for the fault that silently failed 7 of 10 previews:
deploy_files read Vercel's real deployment URL into a local variable and
then returned a *guessed* `https://<project>.vercel.app` instead. Vercel
only assigns that clean alias to short project names, so every longer
business name produced a 404 link, design_agent refused it, and the lead
sat at 'researched' being redeployed on every cycle.
"""
from __future__ import annotations

import pytest

from utils import db, vercel_api
from agents import design_agent


# Real project names from the live database at the time of the fix: every
# one of these up to 35 characters resolved, every one from 36 up gave 404.
SHORT_NAME = "pk-plumbing-services-preview-6"           # 30 chars, alias existed
LONG_NAME = "waterboy-heating-and-cooling-ltd-preview-7"  # 42 chars, alias 404'd


def test_prefers_the_clean_alias_when_it_resolves(monkeypatch):
    monkeypatch.setattr(vercel_api, "_wait_until_publicly_accessible", lambda url, **kw: True)
    assert vercel_api._public_url(SHORT_NAME, "https://dep-abc123.vercel.app") == \
        f"https://{SHORT_NAME}.vercel.app"


def test_falls_back_to_the_deployment_url_when_the_alias_404s(monkeypatch):
    reachable = {f"https://dep-abc123.vercel.app"}
    monkeypatch.setattr(vercel_api, "_wait_until_publicly_accessible",
                        lambda url, **kw: url in reachable)
    assert vercel_api._public_url(LONG_NAME, "https://dep-abc123.vercel.app") == \
        "https://dep-abc123.vercel.app"


def test_returns_an_unreachable_url_rather_than_raising(monkeypatch):
    """design_agent._validate_deployment_url is the gate that stops a bad
    link being emailed -- deploy must hand it something to reject, not
    blow up the whole batch."""
    monkeypatch.setattr(vercel_api, "_wait_until_publicly_accessible", lambda url, **kw: False)
    assert vercel_api._public_url(LONG_NAME, "https://dep-abc123.vercel.app") == \
        "https://dep-abc123.vercel.app"


def test_survives_vercel_omitting_the_deployment_url(monkeypatch):
    monkeypatch.setattr(vercel_api, "_wait_until_publicly_accessible", lambda url, **kw: False)
    assert vercel_api._public_url(LONG_NAME, "") == f"https://{LONG_NAME}.vercel.app"


def test_deploy_files_uses_the_returned_url_and_probes_only_a_ready_build(monkeypatch):
    """Two guarantees in one: the scheme-less hostname Vercel returns is
    used (not discarded), and reachability is probed *after* the build is
    READY -- probing earlier burnt the whole timeout on an unserved build.
    """
    calls: list[str] = []

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"id": "dpl_test", "url": "dep-abc123.vercel.app"}

    monkeypatch.setattr(vercel_api.requests, "post", lambda *a, **kw: _Resp())
    monkeypatch.setattr(vercel_api, "_headers", lambda: {})
    monkeypatch.setattr(vercel_api, "_disable_deployment_protection", lambda name: None)
    monkeypatch.setattr(vercel_api, "_poll_until_ready",
                        lambda dep_id: calls.append("poll") or "READY")

    def _probe(url, **kw):
        calls.append(f"probe:{url}")
        return url == "https://dep-abc123.vercel.app"

    monkeypatch.setattr(vercel_api, "_wait_until_publicly_accessible", _probe)

    result = vercel_api.deploy_files(LONG_NAME, {"index.html": "<h1>hi</h1>"})

    assert result["url"] == "https://dep-abc123.vercel.app"
    assert result["ready_state"] == "READY"
    assert calls[0] == "poll", f"probed before the build was READY: {calls}"
    assert any(c.startswith("probe:") for c in calls)


def test_rebuild_writes_a_changed_preview_url_back_to_the_database(monkeypatch):
    """A redeploy can land on a different hostname than the first build.
    If the row is not updated the emailed link serves the pre-rebuild site
    forever -- including after a prospect sends their own photos."""
    db.init_db()
    lead_id = db.insert_lead("Waterboy Heating And Cooling Ltd", "plumber", "Horsham",
                             status="negotiating")
    db.insert_website(lead_id=lead_id, template_niche="plumber",
                      repo_url="https://github.com/x/waterboy", repo_full_name="x/waterboy",
                      preview_url=f"https://{LONG_NAME}.vercel.app", vercel_project_id="dpl_1")

    monkeypatch.setattr(design_agent.github_api, "get_repo", lambda name: name)
    monkeypatch.setattr(design_agent.github_api, "push_files",
                        lambda repo, files, commit_message="": None)
    monkeypatch.setattr(design_agent.vercel_api, "deploy_files", lambda name, files: {
        "url": "https://dep-xyz789.vercel.app", "ready_state": "READY", "deployment_id": "dpl_2"})
    monkeypatch.setattr(design_agent, "_validate_deployment_url", lambda d: d["url"])
    monkeypatch.setattr(design_agent, "_capture_and_publish_screenshot", lambda lead_id, url: ("", ""))

    website = design_agent.rebuild_preview(db.get_lead(lead_id))

    assert website["preview_url"] == "https://dep-xyz789.vercel.app"
    assert db.get_website_by_lead(lead_id)["preview_url"] == "https://dep-xyz789.vercel.app"
