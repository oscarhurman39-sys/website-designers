# The Companies House screen: proving a lead may be cold-emailed

**Status: design proposal, not built, not legal advice.** Written 2026-09-20 against the code as it
stands today. It builds on `docs/research/pecr-and-companies-house.md` (same date) and re-verifies
the Companies House API facts directly (see Sources). Nothing in this document has been implemented;
no source file was changed to write it.

---

## 1. The rule, in one paragraph

UK PECR regulation 22 forbids unsolicited direct-marketing email only to **individual subscribers**,
and the ICO defines those as "sole traders; certain types of partnerships (eg non-limited liability
partnerships or other types of English, Welsh and Northern Irish partnerships); and other
unincorporated bodies of individuals" [S1]. A **corporate subscriber** — "companies; corporation
soles; limited liability partnerships; Scottish partnerships; some government bodies; and any other
body corporate or entity that is a legal person distinct from its members" — may be cold-emailed
with no consent at all: "you can send B2B direct marketing emails or texts to any corporate body.
You do not need their consent under PECR to send such messages" [S1]. There is no first-email
exception for sole traders; the soft opt-in needs a prior sale or negotiation with that same person
[S2], which a Google Places listing is not. So the pipeline's legal position rests entirely on one
fact it does not currently establish: **is the business behind this trading name an incorporated
body?** Since 5 February 2026 getting that wrong is worth up to £17.5m or 4% of turnover [S3].
Therefore, before `sales_agent` sends anything to an address it scraped, the pipeline must hold
positive, stored, auditable evidence that the business is a live registered company, LLP or PLC —
and the absence of evidence must mean "do not send", never "probably fine". Everything else the
pipeline already does right (identity in the footer, working unsubscribe, physical address,
suppression) stays mandatory for corporate recipients too [S1][S4]; this screen only decides who is
allowed to receive the message in the first place.

`scoring.looks_like_limited_company()` is not this control and must not be mistaken for it. It
regex-matches `Ltd|Limited|LLP|PLC` in the *trading name* and adds +1 to `lead_score`. On the live
database it fires for 25 of 164 leads. It has both error directions: a genuinely incorporated
plumber trading as "Copthorne Plumbing and Heating" scores 0, and anyone may style themselves
"... Ltd" on a shopfront without ever having incorporated. It is a budget signal. Leave it exactly
where it is, in the score. It never gates a send.

---

## 2. The Companies House API

### Which endpoint answers the question

Two search endpoints can, and the design uses the second:

| Endpoint | Path | Why / why not |
|---|---|---|
| Company search | `GET https://api.company-information.service.gov.uk/search/companies` | Params `q`, `items_per_page`, `start_index`, `restrictions` [S5]. Free-text, relevance-ranked, no server-side filter on status/type/location — every filter has to be applied client-side over whatever it chose to return. |
| **Advanced company search** | `GET https://api.company-information.service.gov.uk/advanced-search/companies` | Params include `company_name_includes`, `company_name_excludes`, `company_status` (list), `company_type` (list), `company_subtype`, `sic_codes` (list), `location`, `incorporated_from`/`_to`, `dissolved_from`/`_to`, `size` (max **5000**), `start_index` [S6]. **Use this one.** `company_status=active` and `company_type=ltd,plc,llp` are applied by the register, so the candidate set that comes back is already restricted to entities that make the lead a corporate subscriber, and `location` narrows by registered-office town in the same request. |

Both return `CompanySearch` items carrying everything the decision needs [S7]:
`company_number` ("the company registration / incorporation number"), `company_status` (enumerated
`active`, `dissolved`, `liquidation`, `receivership`, `administration`, `voluntary-arrangement`,
`converted-closed`, `insolvency-proceedings`, `registered`, `removed`), `company_type` (30+ values
including `ltd`, `plc`, `llp`), `title`, `date_of_creation`, `address` with `locality` and
`postal_code` subfields, `address_snippet`, and `matches` (character offsets of the matched
substrings). `total_results` on the envelope is what makes "how ambiguous is this name?" answerable
in one call.

`GET /company/{companyNumber}` is **not** needed. Advanced search already returns status, type and
registered address, so a second call per lead would double the request count for nothing. (If it is
ever added, note that dissolved companies still return HTTP 200 — the status field is the check, not
the status code.)

### Cost, rate limit, authentication

- **Cost: free.** No fee appears anywhere in the authorisation or application guides [S8][S9]. The
  register's bulk equivalent, the Free Company Data Product, is also free [S10].
- **Rate limit: 600 requests per five minutes, per application.** "You can make up to 600 requests
  within a five-minute period"; over it, "you will receive a `429 Too Many Requests` HTTP status
  code for each request made within the remainder of the five-minute window". A higher limit is
  available: "If you have an application that requires a higher rate limit than the default applied,
  then please contact us" [S11].
- **Authentication: an API key, sent as HTTP Basic auth with the key as the username and the
  password ignored** [S8]:
  `curl -XGET -u my_api_key: https://api.company-information.service.gov.uk/company/00000006`.
  OAuth 2.0 exists but is for end-user actions and filing, not the public data API [S8]. The key
  comes from a free developer account: register, create a **live** application, "Create new key"
  [S9]. **So yes — a key is required, and Oscar must create one.** It goes in `.env` as
  `COMPANIES_HOUSE_API_KEY` and nowhere else.

The pipeline's actual demand is trivial against that ceiling: `SOURCING_DAILY_LIMIT` is 30, one
advanced-search request screens one lead, and results are cached. A 600-per-5-minute budget is
roughly 170,000 lookups a day. The rate limit only becomes real during a one-off backfill of the
whole database, which is 164 requests — a quarter of a single window.

### How confident a name match can be, honestly

Not very, and this is the weakest joint in the whole design. Companies House indexes **registered
names**. Google Places shows **trading names**. They coincide often enough to be useful and diverge
often enough that name alone can never be the answer:

- **Trading names are not on the register at all.** "Dave's Plumbing" may be a sole trader while an
  unrelated `DAVES PLUMBING LTD` exists two counties away. A name-only hit is a genuine
  false-positive risk, and a false positive here is the expensive kind: a sole trader in the email
  bucket.
- **Registered offices are usually the accountant's**, not the workshop. So the postcode corroborator
  produces *false negatives* — a real Ltd screened out because its registered office is in a
  different town. That is the cheap direction to be wrong in, and the design accepts it.
- **Generic trade names are dense.** `company_name_includes=plumbing` plus `location=Manchester`
  returns many live companies; picking one is guesswork dressed up as evidence.
- **Ordinary partnerships and Scottish general partnerships are not on the register**, so absence of
  a match genuinely cannot distinguish "sole trader" from "partnership" from "company we failed to
  find" [S1].

How often will it be ambiguous? On this database the shape of the problem is visible without calling
the API: of the 81 leads that have ever been in the send path, 15 carry a corporate suffix in the
trading name (near-certain matches, since the suffix plus the town is usually unique) and 66 do not.
Names like "The Refuge", "Mai Salon" and "Ecotec - emergency plumber crawley." will each return
either nothing or a crowd. A defensible planning assumption is that **somewhere between a third and
two-thirds of no-suffix leads come back `no_match` or `ambiguous`** — but that is an estimate, not a
measurement, and the design deliberately makes the measurement cheap: `python run.py screen --dry-run`
prints the verdict for every queued lead and writes nothing. Run that before deciding anything else.
The exact number is not knowable from this repo, and this document does not pretend otherwise.

The matching rule below is built so that every failure mode lands on "do not send".

---

## 3. The design

### 3.1 The new lead field

One field decides sends; four more make the decision auditable, which is the point of §7 of the PECR
notes ("log the `company_number`, `status`, `type` and match evidence ... if the ICO asks"). All five
go in one migration block, exactly as `website_status` / `site_score` / `lead_score` /
`contact_channel` did.

| Column | Type | Meaning |
|---|---|---|
| **`companies_house_status`** | TEXT | **The gate.** `NULL` = never screened. `matched`, `no_match`, `ambiguous`, `unavailable`. Only `matched` unlocks a send. |
| `company_number` | TEXT | The CRN when matched. The audit trail's primary key. |
| `company_type` | TEXT | `ltd` / `plc` / `llp` as the register reports it. |
| `companies_house_checked_at` | TEXT | UTC `YYYY-MM-DD HH:MM:SS`, so staleness is computable. |
| `companies_house_evidence` | TEXT | Small JSON: normalised query, matched title, corroborator used, candidate count. What gets pasted into a regulator's email. |

`NULL` is the critical value. Every lead in the database today is `NULL`, and `NULL` means *not
corporate*, because it means *nothing is known*. There is no code path anywhere in this design where
`NULL` or a blank string is read as permission.

### 3.2 Where the lookup belongs

**Lookup at research time** (`lead_agent._research_lead_impl`), **gate at design time and again at
send time.**

- *Not at sourcing.* `sourcing_agent` has its own request budget and runs hourly across a 352-bucket
  grid; 73 of the 164 leads sourced so far died at "no contact email" and never reached a send. A CH
  lookup per candidate spends quota on leads that die two stages later. It also happens before the
  address is stored, though in practice Places supplies `formattedAddress` at the same moment.
- **At research.** `_research_lead_impl` already makes several network calls per lead, already writes
  `website_status` / `site_score` / `contact_channel` / `lead_score` in one `update_lead_fields`
  call, and by then `business_name`, `address` and `location` are all populated. One more field group
  in the same write. Both exit branches of that function need it (the no-website branch at L359 and
  the researched branch at L416), because both produce a lead that the design queue will pick up.
- **Gate #1 in `design_agent.park_reason`** — refuses a lead *before* a GitHub repo and a Vercel
  project are created. Cheapest place to say no; it is already where the healthy-site gate lives.
- **Gate #2 in `sales_agent._send_cold_email_impl` and `send_follow_up_if_due`** — the compliance
  control proper. Gate #1 is a money-saver and can be bypassed: a lead can be set to `designed` by
  hand, `reviewed-batch` can stage one, and every lead designed before this screen existed is already
  sitting in the queue. Gate #2 is the one that must be impossible to route around, so it re-reads
  the field at the moment of sending rather than trusting the status. A follow-up is a second
  unsolicited marketing message and is gated identically.

Ordering inside `park_reason` matters: the existing `"No contact email found"` branch stays **first**,
because `tests/test_lead_quality_gate.py::test_no_email_is_still_the_first_reason_to_park` asserts
that string. The screen becomes the second check, the healthy-site gate third.

### 3.3 What happens to a lead that cannot be matched

It never becomes sendable, and the two "cannot" cases are treated differently, because one is a fact
about the business and the other is a fact about the weather:

| `companies_house_status` | Design queue | Send queue | Why |
|---|---|---|---|
| `matched` | built | sent | The only permission there is. |
| `no_match` | parked `lost`, note `"Not verified as a registered company (Companies House: no match on 'X' near CT1); not cold-emailable under PECR"` | unreachable | A decision about the lead. Mirrors today's park behaviour exactly. |
| `ambiguous` | parked `lost`, note names the candidate count | unreachable | Two live companies fit; guessing is how a sole trader gets emailed. |
| `unavailable` | **not parked** — left `researched`, retried next cycle | held, no status change | An outage must never destroy leads. |
| `NULL` | **not parked** — left `researched`, retried next cycle | held, no status change | Never screened ≠ screened and failed. |

The `unavailable`/`NULL` rows are the safety property in one line: **an API failure costs sends, never
fines, and never data.** They follow the contract `_cooldown_blocked_until` already uses in
`_send_cold_email_impl` — return `False`, no status change, no sender pinned, lead comes back round
next cycle.

Parking a `no_match` lead as `lost` is honest about today's code but throws the lead away. If the
call/letter channel in `docs/research/no-site-leads-plan.md` is ever built, `no_match` becomes a
*routing* verdict rather than a death sentence: letters are outside PECR entirely ("direct marketing
by post is not covered by PECR"), so an unmatched lead is a perfectly good letter lead. That is §6's
decision.

### 3.4 The matching rule

```
normalise(name):  uppercase; "&" -> " AND "; strip Ltd/Limited/LLP/PLC/Company/Co/UK;
                  strip punctuation; drop leading "THE"; collapse whitespace
outward(address): the outward half of the first UK postcode ("CT1 2AB" -> "CT1")

GET /advanced-search/companies
    company_name_includes = normalise(business_name)
    company_status        = active
    company_type          = ltd,plc,llp
    location              = <lead town>            (omitted on a retry, see below)
    size                  = 20

candidates = [ item for item in items
               if normalise(item.title) == normalise(business_name) ]      # exact, post-normalisation

corroborated = [ c for c in candidates
                 if outward(c.address.postal_code) == outward(lead.address)
                 or  upper(c.address.locality)     == upper(lead town) ]

exactly one corroborated                       -> matched
zero corroborated, >= 1 candidate              -> ambiguous   (name fits, location does not)
zero candidates                                -> no_match
more than one corroborated                     -> ambiguous
```

Two details worth stating: `location` is sent on the first request because it cuts the candidate
crowd sharply, and the "name fits but location does not" case becomes `ambiguous` rather than
`matched` precisely because registered offices are so often the accountant's — the pipeline does not
know whether that is a different company or the same one filed elsewhere, and "does not know" means
"does not send". Substring matching on the title is deliberately **not** used: `company_name_includes`
already does the fuzzy part, and accepting `ACME PLUMBING AND ROOFING LTD` for a lead called `ACME
PLUMBING` is how a false positive happens.

Every one of the 164 leads in the database has a UK postcode in `leads.address` (Google Places
`formattedAddress`), so the corroborator is available for 100% of them. That was checked, not assumed.

One thing that would have been a better corroborator is not available: a company registration number
in the lead's own website footer. `leads.scraped_info` holds the homepage text but is truncated to
2000 characters and the footer falls off the end — **0 of the 164 rows contain a registration number
or "registered in England"**. Widening that capture is a cheap future improvement and is out of scope
here.

### 3.5 Caching

Reuse the `pipeline_state` key/value table added 2026-09-20. No new table, same backup, same WAL
locking.

- Key: `ch.<sha1(normalised_name + "|" + outward_code)[:16]>` — the cache is keyed on the *query*,
  not the lead, so two leads that are the same business in two towns' search results (which the
  sourcing dedupe already shows happens) cost one request, not two.
- Value: the JSON `Screen` record plus `checked_at`.
- TTL: `COMPANIES_HOUSE_RECHECK_DAYS` (default 90). Matches go stale because companies dissolve;
  non-matches go stale because sole traders incorporate. One knob, one meaning.
- **`unavailable` is never cached.** Caching an outage would turn five bad minutes into a
  `COMPANIES_HOUSE_RECHECK_DAYS`-long blackout.
- `companies_house_checked_at` on the lead is the per-lead copy of the same timestamp, so the audit
  trail survives a cache eviction.

### 3.6 Degrading safely under a rate limit or an outage

- A `429` raises `CompaniesHouseRateLimited`, which is deliberately **not** in the retry decorator's
  `retriable` tuple. Retrying a 429 after 2 and 4 seconds cannot succeed — the window is five minutes
  — and each attempt is another request against the same ceiling. It propagates immediately, the
  screen returns `unavailable`, and the run stops screening for this cycle, the same shape as
  `PlacesQuotaError` in `sourcing_agent`.
- A per-run request budget (`COMPANIES_HOUSE_MAX_REQUESTS_PER_RUN`, default 120) mirrors
  `sourcing_agent._RequestBudget`, so a pathological loop cannot walk the whole register.
- Connection errors and 5xx go through the existing `retry.with_retries` (2s/4s, three attempts) and
  then return `unavailable`.
- A blank `COMPANIES_HOUSE_API_KEY` returns `unavailable` without making a request, and preflight
  says so loudly.
- Net effect at every failure point: **fewer sends, no wrong sends, no lost leads.**

---

## 4. The exact changes, file by file

### 4.1 New: `pipeline/utils/companies_house.py`

```python
"""Companies House screen: the evidence that a lead may be cold-emailed.

PECR reg 22 bars unsolicited marketing email only to *individual*
subscribers -- sole traders and ordinary partnerships. A registered
company, LLP or PLC may be cold-emailed with no consent at all. The
pipeline therefore has to decide which of the two a Google Places listing
is, and the only free authoritative source is the Companies House public
data register.

`screen(business_name, address, town)` returns a Screen whose `status` is
one of four values, and exactly one of them permits a send:

    matched      one active ltd/plc/llp whose name normalises to this
                 lead's AND whose registered office corroborates it
    no_match     the register has nothing that fits -> treat as a sole
                 trader; not cold-emailable
    ambiguous    more than one active company fits, or one fits on name
                 with no location corroboration -> a person decides, not
                 this module
    unavailable  429/5xx/no key -> nothing is known, so nothing is sent

`unavailable` is the whole safety story: an outage must cost sends, never
fines and never leads. It is never cached and never parks a lead.

Registered names are not trading names. A match is evidence, not proof;
a non-match is not proof of anything at all. Both directions of error are
possible, and the rules below are tuned so the likely one (a real company
screened out because its registered office is its accountant's) is the
cheap one.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests

import config
from utils import db, retry

ADVANCED_SEARCH_URL = "https://api.company-information.service.gov.uk/advanced-search/companies"
REQUEST_TIMEOUT = 10
SEARCH_SIZE = 20

# The three register types that make a business a corporate subscriber under
# PECR. Scottish partnerships also qualify but are not on this register, and
# `private-unlimited` / `limited-partnership` deliberately are not here: a
# limited partnership's general partner can be an individual.
CORPORATE_TYPES = ("ltd", "plc", "llp")

STATUS_MATCHED = "matched"
STATUS_NO_MATCH = "no_match"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNAVAILABLE = "unavailable"

_SUFFIX_RE = re.compile(r"\b(limited|ltd|llp|plc|company|co|uk)\b\.?", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^A-Z0-9 ]+")
_POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b", re.IGNORECASE)
_CACHE_PREFIX = "ch."


class CompaniesHouseRateLimited(RuntimeError):
    """HTTP 429. The register allows 600 requests per five minutes; once
    that is spent every request in the rest of the window fails the same
    way. Deliberately NOT retriable: a 2s/4s backoff cannot outlast a
    five-minute window and each attempt spends another request."""


def normalise_name(name: Optional[str]) -> str:
    """Trading name -> comparison key. 'Kang Heating & Plumbing Ltd' and
    'KANG HEATING AND PLUMBING LIMITED' both become 'KANG HEATING AND
    PLUMBING'."""
    text = (name or "").upper().replace("&", " AND ")
    text = _SUFFIX_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    words = [w for w in text.split() if w and w != "THE"]
    return " ".join(words)


def outward_code(address: Optional[str]) -> str:
    """The outward half of the first UK postcode in `address` ('CT1 2AB' ->
    'CT1'), or '' when there isn't one. Outward rather than the full code
    because a registered office is usually the accountant's office in the
    same town, not the same building."""
    match = _POSTCODE_RE.search(address or "")
    return match.group(1).upper() if match else ""


@dataclass(frozen=True)
class Screen:
    status: str
    company_number: str = ""
    company_type: str = ""
    title: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def sendable(self) -> bool:
        """The single place that decides a lead is a corporate subscriber.
        Every other status -- including no status at all -- is a no."""
        return self.status == STATUS_MATCHED

    def as_lead_fields(self) -> dict[str, Any]:
        """The db.update_lead_fields payload. `unavailable` writes only the
        status: it is not a finding about the business and must not
        overwrite an earlier real one."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        if self.status == STATUS_UNAVAILABLE:
            return {"companies_house_status": STATUS_UNAVAILABLE}
        return {
            "companies_house_status": self.status,
            "company_number": self.company_number or None,
            "company_type": self.company_type or None,
            "companies_house_checked_at": now,
            "companies_house_evidence": json.dumps(self.evidence) if self.evidence else None,
        }


def status_of(lead: dict) -> str:
    """The stored verdict for a lead row, with NULL read as 'never
    screened'. Never returns '' -- callers compare against STATUS_* only."""
    return (lead.get("companies_house_status") or "").strip() or ""


def is_sendable(lead: dict) -> bool:
    """Whether PECR permits an unsolicited marketing email to this lead.
    The only True is a stored 'matched': NULL, blank, no_match, ambiguous
    and unavailable are all False, by design and by default."""
    return status_of(lead) == STATUS_MATCHED


_ch_retry = retry.with_retries(
    retriable=(requests.RequestException,),
    transient=retry.is_transient,
    label="companies_house.advanced_search",
)


class _RequestBudget:
    """Hard cap on register requests for one run, mirroring
    sourcing_agent._RequestBudget. The daily lead limit counts leads, not
    calls; this bounds the worst case."""

    def __init__(self, limit: int) -> None:
        self.limit, self.spent = limit, 0

    def spend(self) -> None:
        if self.limit > 0 and self.spent >= self.limit:
            raise CompaniesHouseRateLimited(
                f"request budget of {self.limit} per run spent (COMPANIES_HOUSE_MAX_REQUESTS_PER_RUN)"
            )
        self.spent += 1


_run_budget: Optional[_RequestBudget] = None


def start_run(limit: Optional[int] = None) -> None:
    """Begin a fresh per-run request budget. Called by rescore/screen and
    by main.py at the top of a cycle."""
    global _run_budget
    _run_budget = _RequestBudget(config.COMPANIES_HOUSE_MAX_REQUESTS_PER_RUN if limit is None else limit)


@_ch_retry
def _advanced_search(name_key: str, town: str) -> dict[str, Any]:
    """One advanced-search request. HTTP Basic with the key as the username
    and an empty password, which is how this API takes its key."""
    if _run_budget is not None:
        _run_budget.spend()
    params = {
        "company_name_includes": name_key,
        "company_status": "active",
        "company_type": ",".join(CORPORATE_TYPES),
        "size": SEARCH_SIZE,
    }
    if town:
        params["location"] = town
    resp = requests.get(
        ADVANCED_SEARCH_URL,
        params=params,
        auth=(config.COMPANIES_HOUSE_API_KEY, ""),
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise CompaniesHouseRateLimited(
            "Companies House HTTP 429: 600 requests per five minutes spent; screening stops this run"
        )
    resp.raise_for_status()
    return resp.json()


def _cache_key(name_key: str, outward: str) -> str:
    digest = hashlib.sha1(f"{name_key}|{outward}".encode("utf-8")).hexdigest()[:16]
    return f"{_CACHE_PREFIX}{digest}"


def _cached(key: str) -> Optional[Screen]:
    try:
        raw = db.get_state(key)
    except Exception:  # noqa: BLE001 - a cache miss is never a reason to fail a screen
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        checked = datetime.strptime(payload["checked_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None
    if datetime.now(timezone.utc) - checked > timedelta(days=config.COMPANIES_HOUSE_RECHECK_DAYS):
        return None
    return Screen(
        status=payload["status"],
        company_number=payload.get("company_number", ""),
        company_type=payload.get("company_type", ""),
        title=payload.get("title", ""),
        evidence=payload.get("evidence", {}),
    )


def _store(key: str, screen: Screen) -> None:
    """Cache a finding. An outage is not a finding, so it is never stored:
    five bad minutes must not become a 90-day blackout."""
    if screen.status == STATUS_UNAVAILABLE:
        return
    try:
        db.set_state(key, json.dumps({
            "status": screen.status,
            "company_number": screen.company_number,
            "company_type": screen.company_type,
            "title": screen.title,
            "evidence": screen.evidence,
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        }))
    except Exception as exc:  # noqa: BLE001 - failing to cache is not failing to screen
        print(f"[companies_house] could not cache {key}: {exc}")


def decide(business_name: str, address: str, town: str, items: list[dict[str, Any]]) -> Screen:
    """The matching rule, with no network in it so it can be tested
    exhaustively. Exact match on the normalised name, then a registered-
    office corroborator (postcode outward code or locality). One
    corroborated candidate is the only path to `matched`."""
    name_key = normalise_name(business_name)
    lead_outward = outward_code(address)
    lead_town = (town or "").strip().upper()

    candidates = [i for i in items if normalise_name(i.get("title")) == name_key]
    corroborated = []
    for item in candidates:
        addr = item.get("registered_office_address") or item.get("address") or {}
        same_postcode = bool(lead_outward) and outward_code(addr.get("postal_code")) == lead_outward
        same_town = bool(lead_town) and (addr.get("locality") or "").strip().upper() == lead_town
        if same_postcode or same_town:
            corroborated.append((item, "postcode" if same_postcode else "locality"))

    evidence = {
        "query": name_key,
        "lead_outward": lead_outward,
        "candidates": len(candidates),
        "corroborated": len(corroborated),
    }
    if len(corroborated) == 1:
        item, how = corroborated[0]
        evidence["corroborator"] = how
        evidence["title"] = item.get("title", "")
        return Screen(
            status=STATUS_MATCHED,
            company_number=str(item.get("company_number") or ""),
            company_type=str(item.get("company_type") or ""),
            title=str(item.get("title") or ""),
            evidence=evidence,
        )
    if not candidates:
        return Screen(status=STATUS_NO_MATCH, evidence=evidence)
    return Screen(status=STATUS_AMBIGUOUS, evidence=evidence)


def screen(business_name: str, address: str = "", town: str = "") -> Screen:
    """Cached, network-backed screen for one business. Returns
    `unavailable` -- never raises -- for a missing key, a 429, or any
    transport failure, because a caller that has to handle an exception to
    stay safe will eventually not."""
    if not config.COMPANIES_HOUSE_SCREEN_ENABLED:
        return Screen(status=STATUS_UNAVAILABLE, evidence={"reason": "screen disabled"})
    if not config.COMPANIES_HOUSE_API_KEY:
        return Screen(status=STATUS_UNAVAILABLE, evidence={"reason": "COMPANIES_HOUSE_API_KEY is not set"})

    name_key = normalise_name(business_name)
    if not name_key:
        return Screen(status=STATUS_NO_MATCH, evidence={"reason": "business name normalises to nothing"})

    key = _cache_key(name_key, outward_code(address))
    hit = _cached(key)
    if hit is not None:
        return hit
    try:
        payload = _advanced_search(name_key, town)
    except CompaniesHouseRateLimited as exc:
        print(f"[companies_house] {exc}")
        return Screen(status=STATUS_UNAVAILABLE, evidence={"reason": "rate limited"})
    except (requests.RequestException, ValueError) as exc:
        print(f"[companies_house] lookup failed for {business_name!r}: {exc}")
        return Screen(status=STATUS_UNAVAILABLE, evidence={"reason": type(exc).__name__})

    result = decide(business_name, address, town, payload.get("items") or [])
    _store(key, result)
    return result


def screen_lead(lead: dict) -> Screen:
    """Screen a leads row and persist the verdict. Returns the Screen so a
    caller can act on it without re-reading the row."""
    result = screen(lead.get("business_name") or "", lead.get("address") or "", lead.get("location") or "")
    db.update_lead_fields(lead["id"], **result.as_lead_fields())
    return result


def park_note(lead: dict) -> str:
    """The state_history note for a lead parked by this screen. Written out
    in full because it is the sentence an operator (or a regulator) reads."""
    status = status_of(lead) or "never screened"
    return (
        f"Not verified as a registered company (Companies House: {status}"
        f" for {normalise_name(lead.get('business_name'))!r}"
        f" near {outward_code(lead.get('address')) or lead.get('location') or '?'});"
        " not cold-emailable under PECR"
    )
```

### 4.2 `pipeline/config.py` — four names, after the sourcing block (~L237)

```diff
+# --- Companies House screen (utils/companies_house.py) -------------------------
+# PECR reg 22 permits unsolicited marketing email to corporate subscribers only.
+# The screen is what proves a lead is one. It is a compliance control, not a
+# feature: it defaults ON, and turning it off belongs in AGENTS.md's hard-gate
+# list alongside ENABLE_LIVE_SEND. A missing key does not disable it -- it makes
+# every screen 'unavailable', which blocks sends rather than allowing them.
+COMPANIES_HOUSE_API_KEY: str = os.getenv("COMPANIES_HOUSE_API_KEY", "").strip()
+COMPANIES_HOUSE_SCREEN_ENABLED: bool = _env_bool("COMPANIES_HOUSE_SCREEN_ENABLED", True)
+# A match goes stale because companies dissolve; a non-match goes stale because
+# sole traders incorporate. One window, both directions.
+COMPANIES_HOUSE_RECHECK_DAYS: int = int(os.getenv("COMPANIES_HOUSE_RECHECK_DAYS", "90") or 90)
+# Register requests per run (0 = uncapped). The register allows 600 per five
+# minutes per application; a normal cycle needs a handful, a full backfill 164.
+COMPANIES_HOUSE_MAX_REQUESTS_PER_RUN: int = int(
+    os.getenv("COMPANIES_HOUSE_MAX_REQUESTS_PER_RUN", "120") or 120
+)
```

`.env.example` gains `COMPANIES_HOUSE_API_KEY=` (blank) and the three tunables, placeholders only.

### 4.3 `pipeline/utils/db.py` — schema and migration

In `_SCHEMA`'s `leads` table (after `site_defects TEXT,`, L58):

```diff
     site_defects    TEXT,
+    companies_house_status      TEXT,
+    company_number              TEXT,
+    company_type                TEXT,
+    companies_house_checked_at  TEXT,
+    companies_house_evidence    TEXT,
     created_at      TEXT NOT NULL DEFAULT (datetime('now'))
```

In `init_db()`, after the `site_defects` migration (L174):

```diff
         _migrate_add_column(conn, "leads", "site_defects", "TEXT")
+        # The Companies House screen (utils/companies_house.py). PECR reg 22
+        # permits unsolicited marketing email to corporate subscribers only, so
+        # a send needs positive evidence that this business is one.
+        # `companies_house_status` is the gate and NULL means NOT corporate:
+        # never screened is not the same as screened and cleared. The other
+        # four columns are the audit trail the ICO would ask for.
+        _migrate_add_column(conn, "leads", "companies_house_status", "TEXT")
+        _migrate_add_column(conn, "leads", "company_number", "TEXT")
+        _migrate_add_column(conn, "leads", "company_type", "TEXT")
+        _migrate_add_column(conn, "leads", "companies_house_checked_at", "TEXT")
+        _migrate_add_column(conn, "leads", "companies_house_evidence", "TEXT")
+        conn.execute(
+            "CREATE INDEX IF NOT EXISTS idx_leads_ch_status ON leads(companies_house_status)"
+        )
         conn.commit()
```

No table rebuild, no CHECK constraint change, no backfill inside the migration — five nullable
`ALTER TABLE ADD COLUMN`s, idempotent, safe to run against the live `leads.db` with the loop up.

Plus one new query, next to `list_owned_site_leads_for_audit`:

```python
def list_leads_for_company_screen(
    statuses: tuple[str, ...] = ("new", "researched", "designed"), include_screened: bool = False
) -> list[dict[str, Any]]:
    """Queued leads whose corporate-subscriber status is unknown: never
    screened, or last screened before an API outage left them
    'unavailable'. With include_screened, every queued lead -- used when
    COMPANIES_HOUSE_RECHECK_DAYS has elapsed or the matching rule changed.
    Fed to pipeline/screen_companies.py."""
    placeholders = ", ".join("?" for _ in statuses)
    unscreened = "" if include_screened else (
        "AND (companies_house_status IS NULL OR companies_house_status = 'unavailable') "
    )
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM leads WHERE status IN ({placeholders}) {unscreened}ORDER BY id ASC",
            statuses,
        ).fetchall()
        return [dict(r) for r in rows]
```

### 4.4 `pipeline/agents/lead_agent.py` — screen during research

Import (L22): `from utils import companies_house, db, email_verify, retry, scoring, site_audit, tracer`.

Both write branches of `_research_lead_impl` gain the screen. The no-website branch (L359):

```diff
+        screen = companies_house.screen(
+            lead["business_name"], lead.get("address") or "", lead.get("location") or ""
+        )
         db.update_lead_fields(
             lead_id,
             website_status=website_status,
             contact_channel=channel,
             site_score=0 if website_status == scoring.WEBSITE_STATUS_NONE else lead.get("site_score"),
             lead_score=scoring.lead_score(
                 website_status, 0, channel,
                 review_count=lead.get("google_reviews_count"), business_name=lead["business_name"],
             ),
+            **screen.as_lead_fields(),
         )
```

and the researched branch (L416) identically, with the note extended:

```diff
     site_note = f"site scored {audit.score}/10" + (f" ({audit.keys})" if audit.defects else " (no defects found)")
+    company_note = f"; Companies House: {screen.status}" + (
+        f" ({screen.company_number})" if screen.company_number else ""
+    )
     notes = (
-        f"Research complete; {site_note}" if email_addr
-        else f"Website found but no contact email discovered; proceeding without one; {site_note}"
+        f"Research complete; {site_note}{company_note}" if email_addr
+        else f"Website found but no contact email discovered; proceeding without one; {site_note}{company_note}"
     )
```

Research still never marks a lead `lost` — that stays the design and send agents' job, exactly as the
module docstring says.

### 4.5 `pipeline/agents/design_agent.py` — `park_reason`, gate #1

```python
def park_reason(lead: dict) -> Optional[str]:
    """Why this lead should never get a preview, or None if it should.

    A preview only earns its deploy if there is an address to send it to:
    SalesAgent would mark an unmailable lead lost at send time anyway, and
    doing it here saves a GitHub repo and a Vercel project (6 of 12 sourced
    leads on the first live day were built and then never sent). And a
    business whose existing site already scores as healthy is not who this
    pitch is for (utils/scoring.py): parking it here costs no repo, no
    Vercel project and no send slot.

    The Companies House screen is second, not first: a lead with no contact
    email is unmailable whatever the register says, and that message is the
    one the operator has learned to read. A lead the screen could not reach
    (NULL or 'unavailable') is NOT parked -- an outage must cost sends, not
    leads -- it simply never passes the send gate until it is screened.
    """
    if not (lead.get("contact_email") or "").strip():
        return "No contact email found; preview not built"
    if companies_house.status_of(lead) in (
        companies_house.STATUS_NO_MATCH, companies_house.STATUS_AMBIGUOUS
    ):
        return companies_house.park_note(lead)
    if scoring.is_good_site(lead.get("website_status"), lead.get("site_score")):
        return (
            f"Existing website scored {lead.get('site_score')}/10 (healthy; SITE_SCORE_GOOD_AT="
            f"{scoring.good_site_threshold()}); not a target, preview not built"
        )
    return None
```

`run()` needs one more line so an unscreened lead is skipped rather than built into a preview that
can never be sent:

```diff
         reason = park_reason(lead)
         if reason:
             db.update_lead_status(lead["id"], "lost", notes=reason)
             continue
+        if not companies_house.is_sendable(lead):
+            continue  # never screened, or the register was unreachable: retry next cycle
```

### 4.6 `pipeline/agents/sales_agent.py` — gate #2, the compliance control

In `_send_cold_email_impl`, immediately after the `email_verify` check and **before** the cooldown
check (L636):

```diff
+    # PECR reg 22: an unsolicited marketing email may go to a corporate
+    # subscriber only. `matched` is the only stored value that proves this
+    # business is one; NULL, no_match, ambiguous and unavailable are all "no".
+    # Same contract as the cooldown and at-cap paths -- return False, no status
+    # change, no sender pinned -- so an unreachable register holds the lead
+    # instead of destroying it. design_agent parks the leads that are
+    # definitively not companies; this gate catches everything that reached
+    # 'designed' another way (by hand, reviewed-batch, or before this existed).
+    if not companies_house.is_sendable(lead):
+        return False
+
     # One cold email per niche+town per OUTREACH_COOLDOWN_DAYS. ...
     if _cooldown_blocked_until(lead):
         return False
```

`send_next_pending` gets the matching `continue` so the loop moves to the next lead instead of
stalling on an unscreened one:

```diff
     for lead in db.list_sendable_leads_by_priority():
+        if not companies_house.is_sendable(lead):
+            continue  # not proven a corporate subscriber; see utils/companies_house.py
         if _cooldown_blocked_until(lead):
             continue
```

And `send_follow_up_if_due` — a reminder is a second unsolicited marketing email, so it is gated
identically (L415):

```diff
     lead = due[0]
+    if not companies_house.is_sendable(lead):
+        return None  # a follow-up is another unsolicited marketing email (PECR reg 22)
```

That last hunk is the urgent one: six leads are queued for a follow-up right now (§5).

### 4.7 New: `pipeline/screen_companies.py` + `run.py` mode `screen`

A backfill/audit command shaped exactly like `rescore.py` — the file it should be copied from:

```
python run.py screen --dry-run [--limit N]   # print a verdict per lead, write nothing
python run.py screen [--limit N]             # write companies_house_* on each lead
python run.py screen --all                   # re-screen already-screened leads (rule/TTL change)
python run.py screen --park                  # also park no_match/ambiguous queued leads as lost
```

`screen(limit, dry_run, park, include_screened)` calls `companies_house.start_run()`, walks
`db.list_leads_for_company_screen()`, prints the `rescore`-style table
(`id / status / verdict / company_number / evidence`) and returns counts
`{screened, matched, no_match, ambiguous, unavailable, parked}`. `--dry-run` is the measurement
asked for in §2: it is the only honest way to learn what the real match rate is on this database.
Add `"screen": run_screen` to `run.py`'s `_MODES` (L226) with a `run_rescore`-shaped subprocess
wrapper, and a line in the module docstring.

### 4.8 `pipeline/preflight.py` — the new check

A probe next to the other data probes:

```python
def _company_screen_state(lead_count: Optional[int]) -> tuple[bool, str, str]:
    """(ok, detail, level) for the PECR corporate-subscriber screen.

    Three different failures, three severities. No API key while sends are
    armed is a blocker: every screen returns 'unavailable', so the whole
    send queue is frozen and the only way to unfreeze it is a key (or
    turning off a compliance control, which is a Commander decision).
    Unscreened designed leads are a warning with the command that fixes
    them. A clean queue is INFO."""
    if not config.COMPANIES_HOUSE_SCREEN_ENABLED:
        return False, (
            "COMPANIES_HOUSE_SCREEN_ENABLED=false: cold email would go out with no evidence that the "
            "recipient is a corporate subscriber (PECR reg 22, fines to £17.5m/4%)"
        ), BLOCKER
    if not config.COMPANIES_HOUSE_API_KEY:
        return False, (
            "COMPANIES_HOUSE_API_KEY is empty, so every screen returns 'unavailable' and NOTHING can "
            "be sent; create a free key at developer.company-information.service.gov.uk"
        ), _escalate(bool(config.ENABLE_LIVE_SEND))
    if lead_count is None:
        return True, "screen armed; database not readable, queue not counted", INFO
    unscreened = _scalar(
        config.DB_PATH,
        "SELECT COUNT(*) FROM leads WHERE status IN ('researched', 'designed') "
        "AND COALESCE(companies_house_status, '') NOT IN ('matched', 'no_match', 'ambiguous')",
    ) or 0
    blocked = _scalar(
        config.DB_PATH,
        "SELECT COUNT(*) FROM leads WHERE status = 'designed' "
        "AND COALESCE(companies_house_status, '') != 'matched'",
    ) or 0
    if unscreened:
        return False, (
            f"{unscreened} queued lead(s) have never been screened against Companies House and can "
            f"never be sent ({blocked} designed lead(s) held in total); run `python run.py screen`"
        ), WARN
    return True, f"every queued lead screened; {blocked} held as not a corporate subscriber", INFO
```

wired inside `collect_checks`'s `if lead_count is not None:` block, next to `"lead quality"`:

```diff
         checks.append(Check("lead quality", quality_ok, ...))
+        screen_ok, screen_detail, screen_level = _company_screen_state(lead_count)
+        checks.append(Check("PECR corporate-subscriber screen", screen_ok, screen_detail, level=screen_level))
```

(The `COMPANIES_HOUSE_SCREEN_ENABLED` and key checks read no lead data, so in a full implementation
they belong just above the `if lead_count is not None:` guard; the helper is written to take
`lead_count=None` so either placement works.)

### 4.9 `pipeline/utils/scoring.py` — one docstring edit, no behaviour change

```diff
 Modifiers on owned sites: -2 if no contact channel is known, +1 for 20+
 Google reviews (a trading business with customers), +1 if the name reads as
-a limited company (Ltd/Limited/LLP/PLC: budget, and a corporate subscriber
-under PECR rather than a sole trader). Capped to 0-10.
+a limited company (Ltd/Limited/LLP/PLC). That is a *budget* signal only --
+whether PECR permits a cold email is decided by utils/companies_house.py
+against the register, never by the trading name. Capped to 0-10.
```

`looks_like_limited_company()` itself is untouched, and so are its tests.

### 4.10 Docs

`AGENTS.md` hard-gates list gains `COMPANIES_HOUSE_SCREEN_ENABLED` (turning a compliance control off
is a Commander decision). `docs/STATE_AND_PLAN.md` and `README.md` get the new `run.py screen` mode.
`docs/research/pecr-and-companies-house.md` gets a pointer to this file.

### 4.11 Tests

Named the way the existing suite is: a file per behaviour, sentence-shaped test names, no network.

**`tests/test_companies_house_screen.py`** — new. Docstring in house style: *"utils/companies_house.py:
the evidence that a lead is a corporate subscriber. Every request is monkeypatched; nothing here
connects anywhere."*

```
test_names_normalise_across_suffixes_ampersands_and_punctuation
    normalise_name("Kang Heating & Plumbing Ltd") == "KANG HEATING AND PLUMBING"
    == normalise_name("KANG HEATING AND PLUMBING LIMITED")
    normalise_name("The Refuge") == "REFUGE";  normalise_name(None) == ""
test_outward_code_is_the_first_half_of_a_uk_postcode
    "12 High St, Crawley RH10 1AB, UK" -> "RH10";  no postcode -> ""
test_one_active_company_at_the_same_postcode_is_a_match
    decide(...).status == matched, company_number/company_type/title captured,
    evidence["corroborator"] == "postcode"
test_a_name_that_fits_but_a_town_that_does_not_is_ambiguous_not_a_match
    registered office in another outward code and another locality -> ambiguous
test_two_corroborated_companies_are_ambiguous
test_no_candidate_is_a_no_match_not_an_error
test_only_matched_is_ever_sendable
    parametrised over (None, "", no_match, ambiguous, unavailable, matched);
    is_sendable True exactly once
test_a_rate_limit_returns_unavailable_and_is_not_retried
    stub returns 429; screen(...).status == unavailable; the stub was called once
test_a_transport_failure_returns_unavailable_rather_than_raising
test_a_missing_api_key_returns_unavailable_without_a_request
test_the_same_company_is_only_looked_up_once
    two screen() calls for two leads at the same name+outward code -> one request;
    the second comes from pipeline_state
test_an_outage_is_never_cached
    429 then a good response -> the second call really does hit the API
test_a_cached_verdict_expires_after_the_recheck_window
    monkeypatch COMPANIES_HOUSE_RECHECK_DAYS and rewrite the stored checked_at
test_unavailable_never_overwrites_an_earlier_finding
    as_lead_fields() for unavailable carries only companies_house_status
test_the_request_budget_stops_a_run_before_the_register_does
```

**`tests/test_send_requires_company_match.py`** — new, deliberately mirroring the existing
`test_send_requires_public_url.py`.

```
test_an_unscreened_lead_is_never_cold_emailed
    companies_house_status NULL, everything else perfect -> send_cold_email False,
    status still 'designed', no email_threads row, no sender_account written
test_a_no_match_lead_is_never_cold_emailed
test_an_ambiguous_lead_is_never_cold_emailed
test_an_unavailable_screen_holds_the_lead_instead_of_losing_it
    status unchanged, no state_history row -- an outage costs a send, not a lead
test_a_matched_lead_still_sends_exactly_as_before
    the existing happy path is untouched
test_send_next_pending_skips_past_an_unscreened_lead_to_a_matched_one
    proves an unscreened lead at the head of the queue cannot block the rest,
    the same property test_bad_recipient_does_not_block_queue.py protects
test_a_follow_up_is_gated_by_the_same_screen
    a lead due a follow-up with a non-matched screen gets none
```

**`tests/test_lead_quality_gate.py`** — additions to the existing file:

```
test_a_lead_that_is_not_a_registered_company_is_parked_before_any_deploy
    park_reason(...) startswith "Not verified as a registered company"; no repo, no project
test_no_email_is_still_the_first_reason_to_park   (existing, must still pass unchanged)
test_an_unscreened_lead_is_skipped_rather_than_parked
    run() leaves it 'researched' -- the register being down must not mark leads lost
```

**`tests/test_preflight.py`** — additions to the existing file, using its `env` fixture:

```
test_a_missing_companies_house_key_blocks_once_live_send_is_armed
test_a_disabled_company_screen_is_a_blocker_in_every_mode
test_unscreened_queued_leads_warn_with_the_command_that_fixes_them
```

**`tests/test_screen_companies.py`** — new, shaped like `tests/test_rescore.py`: `--dry-run` writes
nothing, `--park` parks only `no_match`/`ambiguous`, `--limit` is respected, an `unavailable` run
leaves every lead untouched.

---

## 5. Blast radius: what this would have blocked, on the real database

Read-only query against `pipeline/leads.db` (opened `file:...?mode=ro`), 2026-09-20:

```sql
SELECT l.status,
       COUNT(*) AS leads,
       SUM(CASE WHEN l.business_name LIKE '%Ltd%' OR l.business_name LIKE '%Limited%'
                  OR l.business_name LIKE '%LLP%'  OR l.business_name LIKE '%PLC%'
                THEN 1 ELSE 0 END) AS name_says_company,
       SUM(CASE WHEN l.business_name LIKE '%Ltd%' OR l.business_name LIKE '%Limited%'
                  OR l.business_name LIKE '%LLP%'  OR l.business_name LIKE '%PLC%'
                THEN 0 ELSE 1 END) AS name_says_nothing
FROM leads l
WHERE l.status IN ('designed','emailed')
GROUP BY l.status
ORDER BY l.status;
```

| status | leads | name says company | name says nothing |
|---|---:|---:|---:|
| designed | 51 | 10 | 41 |
| emailed | 30 | 5 | 25 |
| **total in the send path** | **81** | **15** | **66** |

**The number is 81.** Of the 164 leads in the database, 81 have ever reached the send path, not one
of them has ever been screened, and `companies_house_status` would be `NULL` for every one — so the
gate would have held all 81. That includes all 30 cold emails that have actually gone out (verified:
`SELECT COUNT(DISTINCT lead_id) FROM state_history WHERE to_state='emailed' AND notes='Cold email
sent'` returns 30, and there are zero dry-run drafts — these were real sends), and it would hold all
51 previews currently built and waiting.

The other 83 leads are already `lost` for reasons this screen does not touch: 73 "No contact email
found", 10 "preview expired after 7 days", 9 "No usable email at send time", 1 placeholder address.
The screen is not additive to those.

What survives a backfill is the open question. 15 of the 81 carry a corporate suffix and are
near-certain matches. The remaining **66 (81% of the send path)** hinge entirely on whether the
register can match a bare trading name — "The Refuge", "Mai Salon", "Copthorne Plumbing and Heating",
"Ecotec - emergency plumber crawley.". The one encouraging fact: all 81 have a UK postcode in
`leads.address`, so the corroborator is available for every single one. Two discouraging ones: 0 of
164 have a company registration number in their captured homepage text, so there is no offline
second opinion; and the 25 no-suffix leads already emailed cannot be un-emailed.

**Live exposure, dated.** Six of the 30 emailed leads have had no reply and no follow-up yet, so
`send_follow_up_if_due()` will email them again `FOLLOW_UP_AFTER_DAYS` (3) after their cold email —
that is 2026-09-21 and 2026-09-22 for outbound messages sent 2026-09-18 18:33 and 2026-09-19 19:28.
Five of those six have no corporate suffix in the name. The §4.6 follow-up hunk is the part of this
design that is time-sensitive rather than merely correct.

---

## 6. What could still go wrong, and the decision Oscar must make

### Residual risk

1. **A false positive is the expensive failure and it is still possible.** `ACME PLUMBING LTD`
   registered in Crawley while the Crawley sole trader on the Places listing is a different Acme is
   exactly the scenario the postcode corroborator cannot resolve. The screen lowers the odds; it does
   not eliminate them. The stored evidence is the mitigation: if a complaint lands, the pipeline can
   show precisely why it believed the recipient was a company.
2. **Registered-office drift creates false negatives at scale.** If most small traders in these towns
   use an accountant one town over, the corroborator will reject genuine companies and the match rate
   could be far worse than the estimate in §2. Measure with `screen --dry-run` before committing.
3. **Named-address leads are only half-covered.** PECR is satisfied by the corporate-subscriber
   match, but `dave@davesplumbing.co.uk` at a company is still personal data under UK GDPR: lawful
   basis, an absolute right to object, privacy information within a month [S1]. That needs a written
   legitimate-interests assessment and a privacy-notice link, and this design does not provide either.
   Preferring role addresses (`info@`, `hello@`) would reduce the surface; `lead_agent._extract_email`
   currently takes the first usable address it finds, in DOM order.
4. **The register is not the whole population.** Ordinary and Scottish general partnerships are not
   on it. `no_match` therefore conflates "sole trader", "partnership" and "we failed to find them" —
   safe for sending, useless for analytics. Do not report `no_match` counts as "sole traders".
5. **The guidance is under review.** ICO direct-marketing pages carry a post-DUAA "under review"
   banner [S2]. Re-check before treating any single line here as settled, and note this document is
   not legal advice.
6. **The screen freezes the queue the moment it lands.** Every existing `designed` lead is `NULL`.
   Deploy order matters: migration, then `run.py screen` over the queue, then the send gate — or the
   pipeline sends nothing and looks broken.
7. **Zero sends can look like zero risk.** If the backfill parks most of the queue, the tempting fix
   is loosening the corroborator. That is the one change that converts this from a control into
   decoration. If the match rate is unusable, the answer is a different *channel*, not a weaker rule.
8. **Nothing here is built.** Every diff above is against the code as of 2026-09-20 and has not been
   applied or compiled, let alone run.

### The one decision

**What happens to a lead the register cannot confirm — park it, or route it?**

Roughly 66 of the 81 leads in the send path (and, going forward, most newly sourced leads) will come
back `no_match` or `ambiguous`. Two mutually exclusive answers:

- **Park them.** Simple, safe, shippable this week. The email queue shrinks to whatever the register
  confirms — plausibly a quarter to a half of today's volume — and the rest are marked `lost`. Cold
  email stays the only channel and gets much smaller.
- **Route them.** `no_match` becomes an instruction to reach that business by letter or a TPS/CTPS-
  screened call instead of email — post is outside PECR entirely. Nothing is thrown away, but it
  requires `docs/research/no-site-leads-plan.md` items 1-7 (~23 hours) to exist first, plus print and
  postage costs, plus someone to make calls.

Everything else in this document is the same either way; only the last column of the §3.3 table
changes. Oscar should answer this **before** the backfill runs, because `screen --park` writes `lost`
to leads that the routing answer would have kept. If he is undecided, run `python run.py screen
--dry-run` first — it writes nothing, costs one free API key and about 164 requests, and turns the
estimate in §2 into a number.

---

## Sources

Companies House pages re-verified 2026-09-20 for this document; ICO pages likewise. The PECR
analysis is carried over from `docs/research/pecr-and-companies-house.md` (2026-09-20), which lists
27 sources in full.

- [S1] ICO, *Business-to-business marketing* (accessed 2026-09-20) — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
- [S2] ICO, *Guide to PECR: Electronic mail marketing* (accessed 2026-09-20) — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/
- [S3] ICO, *Statement on the commencement of the Data (Use and Access) Act* (Feb 2026; accessed 2026-09-20) — https://ico.org.uk/about-the-ico/media-centre/news-and-blogs/2026/02/statement-on-the-commencement-of-the-data-use-and-access-act-duaa/
- [S4] PECR 2003 reg 23 (accessed 2026-09-20) — https://www.legislation.gov.uk/uksi/2003/2426/regulation/23
- [S5] Companies House, *Search companies* (accessed 2026-09-20) — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/search-companies
- [S6] Companies House, *Advanced company search* (accessed 2026-09-20) — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/reference/search/advanced-company-search
- [S7] Companies House, *CompanySearch resource* (accessed 2026-09-20) — https://developer-specs.company-information.service.gov.uk/companies-house-public-data-api/resources/companysearch?v=latest
- [S8] Companies House, *Authorisation* (accessed 2026-09-20) — https://developer-specs.company-information.service.gov.uk/guides/authorisation
- [S9] Companies House, *How to create an application* (accessed 2026-09-20) — https://developer.company-information.service.gov.uk/how-to-create-an-application
- [S10] Companies House, *Free Company Data Product* (accessed 2026-09-20) — https://download.companieshouse.gov.uk/en_output.html
- [S11] Companies House, *Rate limiting* (accessed 2026-09-20) — https://developer-specs.company-information.service.gov.uk/guides/rateLimiting
- Repo: `docs/research/pecr-and-companies-house.md`, `docs/research/no-site-leads-plan.md` (both 2026-09-20)
