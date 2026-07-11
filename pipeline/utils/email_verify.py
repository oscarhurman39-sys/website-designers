"""Pre-send email address verification: syntax + DNS deliverability.

Sending to addresses whose domain can't receive mail is how a young sending
domain earns a bounce rate -- and bounce rate is the fastest way to get
blacklisted. sales_agent.py calls verify_email() immediately before the
first cold email to a lead; a failing address moves the lead to
'bad_email' and is never sent to.

This delegates to the `email-validator` library (JoshData/python-email-
validator), which is far more thorough than a hand-rolled regex + MX query:
full RFC-compliant syntax parsing, internationalized domains, RFC 7505
null-MX records, A/AAAA fallback restricted to *globally reachable*
addresses, and SPF reject-all detection.

Deliberately conservative about what counts as a failure -- matching the
library's own stance:
  * bad syntax, NXDOMAIN, null MX, or a domain with no MX and no global
    A/AAAA record -> invalid (these can never receive mail). The library
    raises EmailNotValidError for all of these.
  * DNS timeouts / dead nameservers -> VALID (fail open): the library
    returns normally (marking deliverability "unknown") rather than
    raising, so a transient DNS blip never throws away a good lead. Worst
    case is one bounce, which bounce handling already covers.

Requires `email-validator` (see requirements.txt); it pulls in dnspython.
"""
from __future__ import annotations

from email_validator import EmailNotValidError, validate_email

_DNS_TIMEOUT_SECONDS = 5.0


def verify_email(addr: str) -> tuple[bool, str]:
    """Return (is_sendable, reason).

    is_sendable is False only for definitive failures (bad syntax, or a
    domain that provably can't receive mail); resolver timeouts / dead
    nameservers fail open with the address treated as sendable, so
    transient DNS never costs a lead. `reason` is a short human-readable
    explanation (the library's own message on failure).
    """
    addr = (addr or "").strip()
    if not addr:
        return False, "empty address"
    try:
        # check_deliverability=True performs the MX / A-AAAA / null-MX / SPF
        # checks described above. timeout bounds the DNS lookups; on timeout
        # the library returns normally (does not raise), which we treat as
        # sendable below.
        validate_email(addr, check_deliverability=True, timeout=_DNS_TIMEOUT_SECONDS)
    except EmailNotValidError as exc:
        # Covers both syntax (EmailSyntaxError) and deliverability
        # (EmailUndeliverableError) failures -- both subclass this.
        return False, str(exc)
    return True, "valid"
