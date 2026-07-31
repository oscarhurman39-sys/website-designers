"""Regression tests for the shared HMAC-token pattern used by
utils/compliance.py (unsubscribe links), utils/tracker.py (click
tracking), and utils/editor_auth.py (client editor magic links).

All three used to concatenate payload + a literal b"." separator + the
raw mac bytes, then split the decoded blob with rsplit(b".", 1). Since mac
is effectively random bytes, it can itself contain 0x2e, corrupting the
split -- empirically this made ~12% of otherwise-valid tokens fail to
verify (a real CAN-SPAM risk for the unsubscribe link specifically). The
fix slices by mac's fixed 32-byte (sha256) length instead of splitting on
a separator. These tests run enough iterations to reliably reproduce the
old failure rate if it ever regresses -- a single round-trip wouldn't
reliably catch a ~1-in-8 bug.
"""
from __future__ import annotations

from utils import compliance, editor_auth, tracker

ITERATIONS = 500


def test_unsubscribe_token_always_round_trips():
    for lead_id in range(1, ITERATIONS + 1):
        token = compliance.generate_unsubscribe_token(lead_id)
        assert compliance.verify_unsubscribe_token(token) == lead_id


def test_click_token_always_round_trips():
    for lead_id in range(1, ITERATIONS + 1):
        token = tracker._sign(lead_id)
        assert tracker.verify_click_token(token) == lead_id


def test_editor_token_always_round_trips():
    for lead_id in range(1, ITERATIONS + 1):
        token = editor_auth.generate_editor_token(lead_id)
        assert editor_auth.verify_editor_token(token) == lead_id


def _flip_first_char(token: str) -> str:
    # Flipping the LAST base64 character can be a no-op: unused padding
    # bits in the final character group are ignored on decode. Flipping
    # the first character always changes a real decoded byte.
    return ("A" if token[0] != "A" else "B") + token[1:]


def test_unsubscribe_token_rejects_tampering():
    token = compliance.generate_unsubscribe_token(42)
    assert compliance.verify_unsubscribe_token(_flip_first_char(token)) is None


def test_click_token_rejects_tampering():
    token = tracker._sign(42)
    assert tracker.verify_click_token(_flip_first_char(token)) is None


def test_tokens_are_not_interchangeable_across_purposes():
    """A valid unsubscribe token must not verify as a click token or
    editor token, even for the same lead_id -- _TOKEN_PURPOSE is mixed
    into the mac specifically to prevent this."""
    lead_id = 7
    unsub_token = compliance.generate_unsubscribe_token(lead_id)
    assert tracker.verify_click_token(unsub_token) is None
    assert editor_auth.verify_editor_token(unsub_token) is None
