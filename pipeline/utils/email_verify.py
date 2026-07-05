"""Pre-send email address verification: syntax + DNS MX lookup.

Sending to addresses whose domain can't receive mail is how a young sending
domain earns a bounce rate -- and bounce rate is the fastest way to get
blacklisted. sales_agent.py calls verify_email() immediately before the
first cold email to a lead; a failing address moves the lead to
'bad_email' and is never sent to.

Deliberately conservative about what counts as a failure:
  * bad syntax, NXDOMAIN, or a domain with no MX and no A/AAAA record ->
    invalid (these can never receive mail).
  * DNS timeouts or other resolver errors -> VALID ("inconclusive"): a
    transient DNS blip must not throw away a good lead. The worst case of
    failing open here is one bounce, which bounce handling already covers.

Requires dnspython (see requirements.txt).
"""
from __future__ import annotations

import re

import dns.resolver

# Same shape lead_agent uses to scrape addresses; anchored for a full match.
_SYNTAX_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

_DNS_TIMEOUT_SECONDS = 5.0


def verify_email(addr: str) -> tuple[bool, str]:
    """Return (is_sendable, reason).

    is_sendable is False only for definitive failures (bad syntax, domain
    that provably can't receive mail); resolver timeouts/errors fail open
    with reason "dns inconclusive" so transient DNS never costs a lead.
    """
    addr = (addr or "").strip()
    if not _SYNTAX_RE.match(addr):
        return False, "bad syntax"

    domain = addr.rsplit("@", 1)[1]
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = _DNS_TIMEOUT_SECONDS
        try:
            resolver.resolve(domain, "MX")
            return True, "mx found"
        except dns.resolver.NoAnswer:
            # No MX record: RFC 5321 falls back to the domain's A/AAAA host,
            # so only fail if neither exists either.
            for rtype in ("A", "AAAA"):
                try:
                    resolver.resolve(domain, rtype)
                    return True, f"no mx, {rtype.lower()} fallback"
                except dns.resolver.NoAnswer:
                    continue
            return False, "no mx and no a/aaaa record"
    except dns.resolver.NXDOMAIN:
        return False, "domain does not exist"
    except Exception:  # noqa: BLE001 - timeouts/servfails/etc. fail open
        return True, "dns inconclusive"
