"""`run.py rescore`: backfill site scores for leads researched before the
audit existed; `--park-good` takes healthy-site leads out of the queues.
The fetch is canned, so no network."""
from __future__ import annotations

import pytest

import config
import rescore
from agents import lead_agent
from utils import db

_DESC = '<meta name="description" content="Acme">'
HOME_POOR = f'<html><head><title>Acme</title>{_DESC}</head><body><p>Acme</p><footer>© 2018 Acme</footer></body></html>'
HOME_GOOD = (f'<html><head><title>Acme</title><meta name="viewport" content="width=device-width">{_DESC}</head>'
             '<body><p>Acme</p><footer>© 2026 Acme</footer></body></html>')


class _Resp:
    def __init__(self, url: str, text: str) -> None:
        self.url, self.text, self.content = url, text, text.encode("utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "SITE_SCORE_GOOD_AT", 8)
    db.init_db()


def _lead(name: str, url: str, status: str = "designed") -> int:
    lead_id = db.insert_lead(name, "plumber", "Town", status=status)
    db.update_lead_fields(lead_id, website_url=url, website_status="owned_site", contact_email="o@x.test")
    return lead_id


def _serve(pages: dict[str, str]):
    return lambda url: _Resp(url, pages[url]) if url in pages else None


def test_dry_run_scores_but_writes_nothing(monkeypatch, capsys):
    poor = _lead("Poor", "http://poor.test/")
    monkeypatch.setattr(lead_agent, "_fetch_response", _serve({"http://poor.test/": HOME_POOR}))
    counts = rescore.rescore(dry_run=True, gap_seconds=0)
    assert counts == {"audited": 1, "unreachable": 0, "good": 0, "written": 0}
    assert db.get_lead(poor)["site_score"] is None
    assert "stale_copyright:2018" in capsys.readouterr().out


def test_rescore_writes_scores_and_park_good_parks_only_healthy_sites(monkeypatch):
    poor = _lead("Poor", "http://poor.test/")
    good = _lead("Good", "https://good.test/")
    gone = _lead("Gone", "https://gone.test/", status="researched")
    monkeypatch.setattr(
        lead_agent, "_fetch_response", _serve({"http://poor.test/": HOME_POOR, "https://good.test/": HOME_GOOD})
    )

    counts = rescore.rescore(gap_seconds=0)
    assert counts == {"audited": 2, "unreachable": 1, "good": 1, "written": 2}
    p, g, x = (db.get_lead(i) for i in (poor, good, gone))
    assert (p["site_score"], p["lead_score"], p["site_defects"]) == (2, 8, "no_https,no_viewport,stale_copyright:2018")
    assert p["pain_point"].startswith("their current website: it doesn't adjust to a phone screen")
    assert (g["site_score"], g["lead_score"]) == (10, 1)
    assert x["site_score"] is None and x["status"] == "researched"

    assert rescore.park_good_site_leads(dry_run=True) == 1
    assert db.get_lead(good)["status"] == "designed"  # dry run parks nothing
    assert rescore.park_good_site_leads() == 1
    assert db.get_lead(good)["status"] == "lost"
    assert db.get_lead(poor)["status"] == "designed"

    # Already-scored leads are not fetched again; only the unreachable one is left.
    assert rescore.rescore(gap_seconds=0) == {"audited": 0, "unreachable": 1, "good": 0, "written": 0}
    # ...unless an audit rule changed and every queued lead needs a fresh score.
    assert rescore.rescore(gap_seconds=0, include_scored=True) == {"audited": 1, "unreachable": 1, "good": 0, "written": 1}
    assert db.get_lead(poor)["site_score"] == 2  # the parked 'good' lead is lost, so no longer queued


def test_cli_dry_run_never_parks(monkeypatch, capsys):
    good = _lead("Good", "https://good.test/")
    monkeypatch.setattr(lead_agent, "_fetch_response", _serve({"https://good.test/": HOME_GOOD}))
    assert rescore.main(["--dry-run", "--park-good"]) == 0
    assert db.get_lead(good)["status"] == "designed" and db.get_lead(good)["site_score"] is None
    assert "nothing written (dry run)" in capsys.readouterr().out
