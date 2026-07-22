"""Fast, offline unit tests for the pipeline's pure logic: reply
classification, template rendering for every niche, shared URL-safety
checks, token round-trips, pricing/currency coherence, and the
redesign/retry plumbing. No network, no real DB files outside tmp_path.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from github import GithubException

import config
from agents import design_agent, sales_agent
from utils import compliance, db, github_api, stripe_utils, tracker, url_safety


# --- Reply classification ----------------------------------------------------

@pytest.mark.parametrize(
    "subject,body,expected",
    [
        ("Out of Office", "I am away until Monday", "out_of_office"),
        ("Automatic reply: your email", "", "out_of_office"),
        ("Re: I built a website", "OOO til the 3rd", "out_of_office"),
        ("Re: website", "Please remove me from your list", "negative"),
        ("Re: website", "not interested, thanks", "negative"),
        ("Re: website", "How much does it cost?", "positive"),
        ("Re: website", "this looks great, let's talk", "positive"),
        ("Re: website", "hmm, who is this?", "positive"),  # ambiguous defaults to positive
    ],
)
def test_classify_reply(subject, body, expected):
    assert sales_agent.classify_reply(subject, body) == expected


# --- Template rendering (every niche must render cleanly) --------------------

def _dummy_lead(niche: str) -> dict:
    return {
        "id": 1,
        "business_name": "Acme & Sons",
        "niche": niche,
        "location": "Testville",
        "phone": "01234 567890",
        "pain_point": "slow website",
        "testimonial": "Great service!",
    }


def test_every_template_renders_without_leftover_placeholders():
    niches = design_agent.available_niches()
    assert niches, "templates/ directory should not be empty"
    for niche in sorted(niches):
        context = design_agent.build_context(_dummy_lead(niche))
        rendered = design_agent.render_template_files(niche, context)
        for filename, content in rendered.items():
            assert "{{" not in content, f"{niche}/{filename} has unrendered Jinja placeholders"
        assert "Acme" in rendered["index.html"], f"{niche}/index.html ignored business_name"


def test_unknown_niche_falls_back_to_default_template():
    context = design_agent.build_context(_dummy_lead("vehicle-repair"))
    rendered = design_agent.render_template_files("vehicle-repair", context)
    assert "Acme" in rendered["index.html"]


# --- Shared URL-safety checks ------------------------------------------------

def _response(url: str, text: str = "<html><body>site</body></html>", status_code: int = 200) -> Mock:
    resp = Mock()
    resp.url = url
    resp.text = text
    resp.status_code = status_code
    return resp


def test_validate_public_page_accepts_public_site(monkeypatch):
    monkeypatch.setattr(
        url_safety.requests, "get", Mock(return_value=_response("https://example.vercel.app"))
    )
    assert (
        url_safety.validate_public_page("https://example.vercel.app", 5)
        == "https://example.vercel.app"
    )


@pytest.mark.parametrize("bad_url", ["", "not-a-url", "ftp://example.com/x", "https://x.dev/login"])
def test_validate_public_page_rejects_without_fetching(bad_url):
    with pytest.raises(RuntimeError):
        url_safety.validate_public_page(bad_url, 5)


def test_validate_public_page_rejects_http_error(monkeypatch):
    monkeypatch.setattr(
        url_safety.requests, "get",
        Mock(return_value=_response("https://example.vercel.app", status_code=404)),
    )
    with pytest.raises(RuntimeError, match="HTTP 404"):
        url_safety.validate_public_page("https://example.vercel.app", 5)


def test_validate_public_page_rejects_auth_interstitial(monkeypatch):
    monkeypatch.setattr(
        url_safety.requests, "get",
        Mock(return_value=_response("https://example.vercel.app", "Vercel Authentication required")),
    )
    with pytest.raises(RuntimeError, match="authentication page"):
        url_safety.validate_public_page("https://example.vercel.app", 5)


def test_validate_public_page_rejects_redirect_to_auth(monkeypatch):
    monkeypatch.setattr(
        url_safety.requests, "get",
        Mock(return_value=_response("https://vercel.com/signin")),
    )
    with pytest.raises(RuntimeError, match="redirects to an authentication path"):
        url_safety.validate_public_page("https://example.vercel.app", 5)


# --- Signed token round-trips ------------------------------------------------

def test_unsubscribe_token_round_trip():
    token = compliance.generate_unsubscribe_token(42)
    assert compliance.verify_unsubscribe_token(token) == 42


def test_unsubscribe_token_rejects_tampering():
    token = compliance.generate_unsubscribe_token(42)
    tampered = ("A" if token[0] != "A" else "B") + token[1:]
    assert compliance.verify_unsubscribe_token(tampered) is None


def test_click_and_unsubscribe_tokens_are_not_interchangeable():
    click_link = tracker.create_click_link(42)
    click_token = click_link.split("token=")[-1]
    assert tracker.verify_click_token(click_token) == 42
    assert compliance.verify_unsubscribe_token(click_token) is None


# --- Pricing / currency coherence --------------------------------------------

def test_checkout_charges_the_currency_quoted_in_emails(monkeypatch):
    created = {}

    def fake_create(**kwargs):
        created.update(kwargs)
        return Mock(url="https://checkout.stripe.example/session")

    monkeypatch.setattr(stripe_utils.stripe.checkout.Session, "create", fake_create)
    url = stripe_utils.create_checkout_session(7, "Acme", "owner@example.com")

    assert url == "https://checkout.stripe.example/session"
    assert created["line_items"][0]["price_data"]["currency"] == config.PAYMENT_CURRENCY
    assert created["line_items"][0]["price_data"]["unit_amount"] == config.WEBSITE_PRICE * 100


def test_email_copy_quotes_configured_currency_symbol():
    paragraphs = sales_agent._closing_paragraphs("https://example.vercel.app")
    price_line = next(p for p in paragraphs if "Standard package" in p)
    assert config.CURRENCY_SYMBOL in price_line
    assert f"{config.WEBSITE_OFFER_PRICE:,}" in price_line


# --- Redesign / retry plumbing -----------------------------------------------

def test_push_files_updates_existing_files(monkeypatch):
    repo = Mock()
    repo.create_file.side_effect = GithubException(422, {"message": "sha required"}, None)
    repo.get_contents.return_value = Mock(sha="abc123")

    github_api.push_files(repo, {"index.html": "<html></html>"})

    repo.update_file.assert_called_once()
    assert repo.update_file.call_args.kwargs["sha"] == "abc123"


def test_push_files_raises_on_other_github_errors():
    repo = Mock()
    repo.create_file.side_effect = GithubException(403, {"message": "forbidden"}, None)
    with pytest.raises(GithubException):
        github_api.push_files(repo, {"index.html": "<html></html>"})


def test_upsert_website_never_duplicates_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    lead_id = db.insert_lead("Acme", "cafe", "Testville")

    first = db.upsert_website(lead_id, "cafe", "u1", "o/r1", "https://one.example")
    second = db.upsert_website(lead_id, "cafe", "u2", "o/r2", "https://two.example")

    assert first == second
    website = db.get_website_by_lead(lead_id)
    assert website["preview_url"] == "https://two.example"
    with db.get_connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM websites WHERE lead_id = ?", (lead_id,)
        ).fetchone()["n"]
    assert count == 1


def test_design_run_gives_up_after_max_attempts(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    lead_id = db.insert_lead("Acme", "cafe", "Testville")
    db.update_lead_status(lead_id, "researched")
    for _ in range(design_agent.MAX_DESIGN_ATTEMPTS):
        db.log_state_history(lead_id, "researched", "researched", notes="Design failed: boom")

    process = Mock()
    monkeypatch.setattr(design_agent, "process_lead", process)
    design_agent.run()

    process.assert_not_called()
    assert db.get_lead(lead_id)["status"] == "lost"


def test_email_blocked_notes_count_toward_retry_budget(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    db.init_db()
    lead_id = db.insert_lead("Acme", "cafe", "Testville")
    db.log_state_history(lead_id, "designed", "researched", notes="Email blocked: preview URL is not publicly sendable")
    db.log_state_history(lead_id, "researched", "researched", notes="Design failed: boom")

    assert design_agent._design_failures(lead_id) == 2
