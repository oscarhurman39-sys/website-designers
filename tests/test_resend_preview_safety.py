"""pipeline/resend_preview.py: the one-off correction email must never go to a
lead it shouldn't (unsubscribed, already replied, already resent, unsendable
preview, stale screenshot) and must never log a resend it didn't send. The
database, transport and URL check are all mocked; nothing connects anywhere."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from pipeline import resend_preview


def _lead(**overrides):
    lead = {
        "id": 7,
        "business_name": "Example Gardeners",
        "contact_email": "owner@example.test",
        "status": "emailed",
    }
    lead.update(overrides)
    return lead


def test_fresh_screenshot_rejects_stale_and_non_png(tmp_path, monkeypatch):
    shot = tmp_path / "7.png"
    shot.write_bytes(b"login wall")
    monkeypatch.setattr(resend_preview.screenshot, "get_cached_screenshot", lambda _lead_id: shot)
    assert resend_preview._fresh_screenshot(7) is None

    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 24)
    old = (datetime.now(timezone.utc) - timedelta(hours=13)).timestamp()
    os.utime(shot, (old, old))
    assert resend_preview._fresh_screenshot(7) is None

    future = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
    os.utime(shot, (future, future))
    assert resend_preview._fresh_screenshot(7) is None


def test_fresh_screenshot_accepts_recent_png(tmp_path, monkeypatch):
    shot = tmp_path / "7.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 24)
    monkeypatch.setattr(resend_preview.screenshot, "get_cached_screenshot", lambda _lead_id: shot)
    assert resend_preview._fresh_screenshot(7) == shot


def _stub_copy(monkeypatch):
    monkeypatch.setattr(resend_preview.sa, "_draft_note", lambda: "Draft note")
    monkeypatch.setattr(resend_preview.sa, "_pricing_line", lambda: "£589 one-off")
    monkeypatch.setattr(resend_preview.sa, "_guarantee_line", lambda: "Pay after seeing it")
    monkeypatch.setattr(resend_preview.sa, "_SENDER_NAME", "Casey")


def test_body_text_contains_correction_and_validated_link(monkeypatch):
    _stub_copy(monkeypatch)

    body = resend_preview.body_text("https://preview.example.test/site")

    assert "picture in my last email was wrong" in body
    assert "It's live here: https://preview.example.test/site" in body
    assert "£589 one-off" in body
    assert "Casey" in body


def test_body_html_escapes_link_in_href_and_visible_content(monkeypatch):
    _stub_copy(monkeypatch)
    link = 'https://preview.example.test/?x=1&y="quoted"'

    body = resend_preview.body_html(link)

    assert 'href="https://preview.example.test/?x=1&amp;y=&quot;quoted&quot;"' in body
    assert link not in body
    assert "cid:" + resend_preview.sa._SCREENSHOT_CID in body


def _ready_prepare(monkeypatch, lead=None):
    monkeypatch.setattr(resend_preview.db, "get_lead", Mock(return_value=lead or _lead()))
    monkeypatch.setattr(resend_preview.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(resend_preview, "_already_resent", Mock(return_value=False))
    monkeypatch.setattr(
        resend_preview.db, "get_website_by_lead", Mock(return_value={"preview_url": "https://preview.example.test/7"})
    )
    monkeypatch.setattr(
        resend_preview.sa, "_validate_preview_link_for_send", Mock(return_value="https://preview.example.test/7")
    )
    shot = Path("fresh-preview.png")
    monkeypatch.setattr(resend_preview, "_fresh_screenshot", Mock(return_value=shot))
    return shot


def test_prepare_blocks_unsubscribed_lead_before_lookup(monkeypatch):
    website_lookup = Mock()
    monkeypatch.setattr(resend_preview.db, "get_lead", Mock(return_value=_lead()))
    monkeypatch.setattr(resend_preview.db, "is_unsubscribed", Mock(return_value=True))
    monkeypatch.setattr(resend_preview.db, "get_website_by_lead", website_lookup)

    assert resend_preview._prepare(7) is None
    website_lookup.assert_not_called()


@pytest.mark.parametrize("status", ["replied", "negotiating", "bounced", "won"])
def test_prepare_skips_leads_no_longer_in_emailed_state(monkeypatch, status):
    monkeypatch.setattr(resend_preview.db, "get_lead", Mock(return_value=_lead(status=status)))
    monkeypatch.setattr(resend_preview.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(resend_preview, "_already_resent", Mock())

    assert resend_preview._prepare(7) is None
    resend_preview._already_resent.assert_not_called()


def test_prepare_blocks_duplicate_resend_before_preview_work(monkeypatch):
    website_lookup = Mock()
    monkeypatch.setattr(resend_preview.db, "get_lead", Mock(return_value=_lead()))
    monkeypatch.setattr(resend_preview.db, "is_unsubscribed", Mock(return_value=False))
    monkeypatch.setattr(resend_preview, "_already_resent", Mock(return_value=True))
    monkeypatch.setattr(resend_preview.db, "get_website_by_lead", website_lookup)

    assert resend_preview._prepare(7) is None
    website_lookup.assert_not_called()


def test_prepare_blocks_unsendable_preview(monkeypatch):
    _ready_prepare(monkeypatch)
    validator = Mock(side_effect=RuntimeError("preview URL is not public"))
    monkeypatch.setattr(resend_preview.sa, "_validate_preview_link_for_send", validator)

    assert resend_preview._prepare(7) is None
    validator.assert_called_once_with("https://preview.example.test/7")
    resend_preview._fresh_screenshot.assert_not_called()


def test_prepare_permits_one_valid_resend(monkeypatch):
    shot = _ready_prepare(monkeypatch)

    assert resend_preview._prepare(7) == (_lead(), "https://preview.example.test/7", shot)
    resend_preview._already_resent.assert_called_once_with(7)
    resend_preview._fresh_screenshot.assert_called_once_with(7)


def test_send_is_hard_gated_when_live_send_is_off(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(resend_preview.config, "ENABLE_LIVE_SEND", False)
    transport, log_thread = Mock(), Mock()
    monkeypatch.setattr(resend_preview.sa, "_send_via_configured_transport", transport)
    monkeypatch.setattr(resend_preview.db, "insert_email_thread", log_thread)
    monkeypatch.setattr(resend_preview, "_wait_for_slot", Mock(side_effect=AssertionError("must not wait for a slot")))

    assert resend_preview._send(_lead(), "https://preview.example.test/7", tmp_path / "shot.png") is False

    transport.assert_not_called()
    log_thread.assert_not_called()  # a logged row would make _already_resent skip this lead forever
    assert "ENABLE_LIVE_SEND is off" in capsys.readouterr().out


def test_send_calls_transport_and_logs_the_resend_when_live_send_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(resend_preview.config, "ENABLE_LIVE_SEND", True)
    monkeypatch.setattr(resend_preview.config, "EMAIL_MIN_DELAY_SECONDS", 0)
    monkeypatch.setattr(resend_preview.config, "EMAIL_MAX_DELAY_SECONDS", 0)
    monkeypatch.setattr(resend_preview, "_wait_for_slot", Mock(return_value=True))
    monkeypatch.setattr(resend_preview.sa, "_sender_for", Mock(return_value=Mock(user="casey@example.test")))
    monkeypatch.setattr(resend_preview.sa, "_pin_sender", Mock())
    monkeypatch.setattr(resend_preview.sa, "_next_send_allowed_at", resend_preview.sa._next_send_allowed_at)
    transport, log_thread = Mock(return_value="message-123"), Mock()
    monkeypatch.setattr(resend_preview.sa, "_send_via_configured_transport", transport)
    monkeypatch.setattr(resend_preview.db, "insert_email_thread", log_thread)
    monkeypatch.setattr(resend_preview.db, "log_state_history", Mock())

    assert resend_preview._send(_lead(), "https://preview.example.test/7", tmp_path / "shot.png") is True

    transport.assert_called_once()
    assert transport.call_args.kwargs["to_addr"] == "owner@example.test"
    assert log_thread.call_args.kwargs["classification"] == "resend_preview"
    assert log_thread.call_args.kwargs["message_id"] == "message-123"


def test_main_refuses_send_while_live_send_is_off(monkeypatch):
    monkeypatch.setattr(resend_preview.config, "ENABLE_LIVE_SEND", False)
    monkeypatch.setattr(resend_preview, "_prepare", Mock(side_effect=AssertionError("must not touch any lead")))

    with pytest.raises(SystemExit):
        resend_preview.main(["--send"])
    resend_preview._prepare.assert_not_called()
