"""Preflight (pipeline/preflight.py): the go-live check must judge the same
facts differently depending on ENABLE_LIVE_SEND, never touch the network in
--offline mode, and catch stale cold-email copy. Every network probe is
monkeypatched; nothing here connects anywhere."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

import config
import preflight
import webhook_server
from agents import sales_agent


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A fully valid configuration against a throwaway DB, all network probes
    answering 'fine'. Individual tests break one thing at a time."""
    db_path = tmp_path / "leads.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE leads (id INTEGER PRIMARY KEY, business_name TEXT, location TEXT, contact_email TEXT, status TEXT)")
        conn.execute("CREATE TABLE unsubscribes (id INTEGER PRIMARY KEY, email TEXT)")
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "https://track.example-agency.test")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_live_123")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "whsec_123")
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(config, "SOURCING_ENABLED", False)
    monkeypatch.setattr(config, "SOURCING_REQUIRE_WEBSITE", True)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY", 10)
    monkeypatch.setattr(config, "EMAIL_MAX_PER_DAY_PER_ACCOUNT", 10)
    monkeypatch.setattr(config, "EMAIL_USER", "casey@example-agency.test")
    monkeypatch.setattr(config, "SENDING_DOMAIN", "example-agency.test")
    monkeypatch.setattr(config, "PHYSICAL_ADDRESS", "1 Test Street, Maidstone, Kent, UK")
    monkeypatch.setattr(config, "physical_address_problem", lambda: None)
    monkeypatch.setattr(config, "validate", lambda: None)
    monkeypatch.setattr(config, "WEBSITE_OFFER_PRICE", 589)
    monkeypatch.setattr(config, "SUBSCRIPTION_ENABLED", True)
    monkeypatch.setattr(config, "SUBSCRIPTION_MONTHLY_PRICE", 39)
    monkeypatch.setattr(config, "GUARANTEE_DAYS", 14)
    monkeypatch.setattr(config, "CURRENCY_SYMBOL", "£")
    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "")  # console-only alerts: a deliberate, valid choice
    monkeypatch.setattr(preflight, "_probe_public_url", lambda url: (True, "up"))
    monkeypatch.setattr(preflight, "_mailbox_problem", lambda account: None)
    monkeypatch.setattr(preflight, "_github_token_state", lambda: (True, True, "casey: [repo, delete_repo]"))
    monkeypatch.setattr(preflight, "_renders_state", lambda: (True, "fresh"))
    monkeypatch.setattr(preflight, "_generated_test_artifacts", lambda: [])
    monkeypatch.setattr(preflight, "_test_leads", lambda: [])
    return db_path


def _by_name(checks: list[preflight.Check]) -> dict[str, preflight.Check]:
    return {check.name: check for check in checks}


def _add_test_lead(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO leads (business_name, location, status) VALUES ('Test Business', 'Testville', 'designed')")


def test_clean_dry_run_configuration_is_ready(env, capsys):
    checks = _by_name(preflight.collect_checks())
    assert all(c.ok for c in checks.values()), [c.name for c in checks.values() if not c.ok]
    assert checks["live email send gate"].level == preflight.INFO
    assert "dry run" in checks["live email send gate"].detail
    assert preflight.main([]) == 0
    assert "READY (dry run)" in capsys.readouterr().out


def test_problems_are_warnings_in_a_dry_run_and_blockers_once_armed(env, monkeypatch):
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "sk_test_123")
    monkeypatch.setattr(preflight, "_probe_public_url", lambda url: (False, "HTTP 404"))
    monkeypatch.setattr(preflight, "_mailbox_problem", lambda account: "SMTP AuthenticationError")
    monkeypatch.setattr(preflight, "_test_leads", lambda: [{"id": 1}, {"id": 2}])
    escalating = ("Stripe key mode", "public URL reachable", "mailbox login", "test leads in active DB")

    dry = _by_name(preflight.collect_checks())
    for name in escalating:
        assert not dry[name].ok and dry[name].level == preflight.WARN, name
    assert preflight.main([]) == 0

    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    armed = _by_name(preflight.collect_checks())
    for name in escalating:
        assert not armed[name].ok and armed[name].level == preflight.BLOCKER, name
    assert "ARMED" in armed["live email send gate"].detail
    assert "2 throwaway" in armed["test leads in active DB"].detail
    assert preflight.main([]) == 1


def test_armed_and_clean_says_go(env, monkeypatch, capsys):
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    assert preflight.main([]) == 0
    assert "GO (ARMED)" in capsys.readouterr().out


def test_unattended_sourcing_while_armed_is_flagged(env, monkeypatch):
    monkeypatch.setattr(config, "SOURCING_ENABLED", True)
    assert _by_name(preflight.collect_checks())["lead sourcing gate"].ok  # dry run: just state
    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    gate = _by_name(preflight.collect_checks())["lead sourcing gate"]
    assert not gate.ok and gate.level == preflight.WARN


def test_always_blockers_do_not_depend_on_the_mode(env, monkeypatch):
    monkeypatch.setattr(config, "PUBLIC_BASE_URL", "http://localhost:5000")
    monkeypatch.setattr(config, "STRIPE_WEBHOOK_SECRET", "")
    monkeypatch.setattr(config, "SENDING_DOMAIN", "other-domain.test")
    monkeypatch.setattr(config, "STRIPE_SECRET_KEY", "")
    checks = _by_name(preflight.collect_checks())
    for name in ("PUBLIC_BASE_URL", "Stripe webhook secret", "sending identity", "Stripe key mode"):
        assert not checks[name].ok and checks[name].level == preflight.BLOCKER, name
    assert checks["public URL reachable"].level == preflight.INFO  # local URL is never probed
    assert preflight.main([]) == 1


def test_offline_skips_every_network_probe(env, monkeypatch):
    def boom(*args):
        raise AssertionError("network probe ran in --offline mode")

    monkeypatch.setattr(preflight, "_probe_public_url", boom)
    monkeypatch.setattr(preflight, "_mailbox_problem", boom)
    monkeypatch.setattr(preflight, "_github_token_state", boom)
    checks = _by_name(preflight.collect_checks(offline=True))
    assert checks["public URL reachable"].ok and "skipped" in checks["public URL reachable"].detail
    assert checks["mailbox login"].ok and "skipped" in checks["mailbox login"].detail
    assert checks["GitHub token"].ok and "skipped" in checks["GitHub token"].detail


def test_cold_email_content_check_catches_stale_copy(env, monkeypatch):
    ok, detail = preflight._email_content_state()
    assert ok, detail
    assert "£589 one-off" in detail and "£39/month" in detail

    monkeypatch.setattr(sales_agent, "_pricing_line", lambda: "Standard package: £2,000. This completed draft: £750.")
    ok, detail = preflight._email_content_state()
    assert not ok
    assert "price £589 one-off" in detail and "monthly option £39/month" in detail
    assert _by_name(preflight.collect_checks())["cold email content"].level == preflight.BLOCKER


def test_cold_email_content_check_requires_unsubscribe_link_on_public_url(env, monkeypatch):
    monkeypatch.setattr(preflight.config, "PUBLIC_BASE_URL", "https://track.example-agency.test")
    monkeypatch.setattr(
        "utils.compliance.create_unsubscribe_link", lambda lead_id: "https://stale-host.test/unsubscribe/x"
    )
    ok, detail = preflight._email_content_state()
    assert not ok and "unsubscribe link on PUBLIC_BASE_URL" in detail


def test_public_url_probe_classifies_the_responses(monkeypatch):
    import requests

    def fake_get(status, body=None, exc=None):
        def _get(url, timeout):
            if exc:
                raise exc
            resp = Mock(status_code=status)
            resp.json = Mock(return_value=body) if body is not None else Mock(side_effect=ValueError)
            return resp
        return _get

    monkeypatch.setattr(requests, "get", fake_get(200, {"ok": True}))
    assert preflight._probe_public_url("https://tunnel.test")[0]
    monkeypatch.setattr(requests, "get", fake_get(404))
    ok, detail = preflight._probe_public_url("https://tunnel.test")
    assert not ok and "webhook_server.py is not running" in detail
    monkeypatch.setattr(requests, "get", fake_get(200, "<html>ngrok</html>"))
    assert not preflight._probe_public_url("https://tunnel.test")[0]
    monkeypatch.setattr(requests, "get", fake_get(0, exc=requests.ConnectionError("no route")))
    ok, detail = preflight._probe_public_url("https://tunnel.test")
    assert not ok and "unreachable" in detail


def test_renders_are_stale_when_a_template_is_newer_than_the_newest_png(tmp_path, monkeypatch):
    out = tmp_path / "out"
    templates = tmp_path / "templates"
    out.mkdir()
    templates.mkdir()
    monkeypatch.setattr(preflight, "OUT_DIR", out)
    monkeypatch.setattr(preflight, "RENDER_SOURCES", (templates,))
    assert not preflight._renders_state()[0]  # nothing rendered yet

    png = out / "plumber.png"
    png.write_bytes(b"png")
    css = templates / "style.css"
    css.write_text("body{}")
    old, new = time.time() - 7200, time.time() - 3600
    import os

    os.utime(css, (old, old))
    os.utime(png, (new, new))
    ok, detail = preflight._renders_state()
    assert ok and "1 render(s)" in detail

    os.utime(css, (time.time(), time.time()))
    ok, detail = preflight._renders_state()
    assert not ok and "stale" in detail


def test_test_lead_detection_uses_the_cleanup_rules(env, monkeypatch):
    monkeypatch.undo()  # drop the fixture's _test_leads stub, keep its DB path via a fresh setattr below
    db_path = env
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP TABLE leads")
    from utils import db

    db.init_db()
    _add_real_and_test_leads(db)
    leads = preflight._test_leads()
    assert [lead["business_name"] for lead in leads] == ["Test Business"]


def _add_real_and_test_leads(db) -> None:
    db.insert_lead("Test Business", "vehicle-repair", "Testville", status="designed")
    db.insert_lead("Real Plumber", "plumber", "Maidstone, Kent", status="designed")


def test_health_route_answers_with_the_expected_body():
    client = webhook_server.app.test_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "service": "website-designers-webhook"}


def test_github_token_check_separates_unusable_from_merely_untidy(env, monkeypatch):
    """Missing `repo` stops previews being built at all; missing `delete_repo`
    only leaks repos, so it must never block a send."""
    monkeypatch.setattr(preflight, "_github_token_state", lambda: (True, True, "casey: [repo, delete_repo]"))
    checks = _by_name(preflight.collect_checks())
    assert checks["GitHub token"].ok
    assert "GitHub repo cleanup" not in checks

    monkeypatch.setattr(preflight, "_github_token_state", lambda: (True, False, "casey: [repo]"))
    checks = _by_name(preflight.collect_checks())
    assert checks["GitHub token"].ok  # usable: previews still build and hand over
    cleanup = checks["GitHub repo cleanup"]
    assert not cleanup.ok and cleanup.level == preflight.WARN and "delete_repo" in cleanup.detail
    assert preflight.main([]) == 0  # a warning, never a stop sign

    monkeypatch.setattr(config, "ENABLE_LIVE_SEND", True)
    assert preflight.main([]) == 0  # still only a warning once armed

    monkeypatch.setattr(preflight, "_github_token_state", lambda: (False, False, "token rejected (401)"))
    checks = _by_name(preflight.collect_checks())
    assert not checks["GitHub token"].ok and checks["GitHub token"].level == preflight.BLOCKER
    assert preflight.main([]) == 1


def test_github_scope_probe_reads_the_header(monkeypatch):
    import requests

    def responder(status, scopes=None, exc=None):
        def _get(url, headers, timeout):
            if exc:
                raise exc
            resp = Mock(status_code=status, headers={} if scopes is None else {"X-OAuth-Scopes": scopes})
            resp.json = Mock(return_value={"login": "casey"})
            return resp
        return _get

    monkeypatch.setattr(config, "GITHUB_TOKEN", "ghp_x")
    monkeypatch.setattr(requests, "get", responder(200, "repo, delete_repo"))
    assert preflight._github_token_state()[:2] == (True, True)

    monkeypatch.setattr(requests, "get", responder(200, "repo"))
    usable, can_delete, detail = preflight._github_token_state()
    assert (usable, can_delete) == (True, False) and detail == "casey: [repo]"

    monkeypatch.setattr(requests, "get", responder(200, "gist"))
    usable, can_delete, detail = preflight._github_token_state()
    assert (usable, can_delete) == (False, False) and "cannot build previews" in detail

    monkeypatch.setattr(requests, "get", responder(401))
    assert preflight._github_token_state()[0] is False

    # A fine-grained token reports no scopes: say so rather than guess.
    monkeypatch.setattr(requests, "get", responder(200, ""))
    assert "fine-grained" in preflight._github_token_state()[2]

    # An unreachable API must not be read as a bad token.
    monkeypatch.setattr(requests, "get", responder(0, exc=requests.ConnectionError("offline")))
    usable, can_delete, detail = preflight._github_token_state()
    assert (usable, can_delete) == (True, True) and "unverified" in detail

    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    assert preflight._github_token_state() == (False, False, "GITHUB_TOKEN is empty")


def test_slack_placeholder_token_is_a_warning_not_a_silent_failure(env, monkeypatch):
    import requests

    def no_network(*args, **kwargs):
        raise AssertionError("Slack must not be called for a placeholder or in --offline mode")

    monkeypatch.setattr(requests, "post", no_network)

    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-placeholder-for-your-slack-bot-token")
    check = _by_name(preflight.collect_checks())["Slack alerts"]
    assert not check.ok and check.level == preflight.WARN and "placeholder" in check.detail
    assert preflight.main([]) == 0  # alerts never block a send

    monkeypatch.setattr(config, "SLACK_BOT_TOKEN", "xoxb-test-fixture-not-a-real-token")
    check = _by_name(preflight.collect_checks(offline=True))["Slack alerts"]
    assert check.ok and check.level == preflight.INFO and "not verified" in check.detail

    responses = iter([{"ok": True, "team": "Casey Websites"}, {"ok": False, "error": "invalid_auth"}])
    monkeypatch.setattr(requests, "post", lambda *a, **k: Mock(json=lambda: next(responses)))
    check = _by_name(preflight.collect_checks())["Slack alerts"]
    assert check.ok and "Casey Websites" in check.detail
    check = _by_name(preflight.collect_checks())["Slack alerts"]
    assert not check.ok and "invalid_auth" in check.detail
