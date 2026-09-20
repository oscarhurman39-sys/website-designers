# No-site channel: build order, checked against the code

Written 2026-09-20, against working tree at `621fad0` (the no-site plan was
written against `6daf22b`; `lead_agent.py` has changed since, so its line
numbers in that document are stale — see §1.7). Every line number here is
from the files as they are today. Config is referred to by NAME only.

Companion to `docs/research/no-site-leads-plan.md` (the design) and
`docs/research/pecr-and-companies-house.md` (the law). This document answers a
narrower question: **what can actually be started this week, in what order, and
which parts of the 44-hour estimate are already paid for.**

Nothing here has been implemented. No source file was modified.

---

## 1. What is already built

This is the useful part. Roughly a third of the no-site plan describes code
that already exists and passes tests. Strike these off before estimating
anything.

### 1.1 Website classification — BUILT

`sourcing_agent.website_status_for_url()` (L171-178) already returns
`"none"` / `"platform_only"` / `"owned_site"`, backed by
`sourcing_agent.is_platform_host()` (L164-168) and the `PLATFORM_HOSTS`
frozenset (L100-102: facebook, instagram, linktr.ee, yell, checkatrade,
google). `sourcing_agent.site_score_for_status()` (L190-194) already writes
`site_score = 0` for both `none` and `platform_only`.

STATE_AND_PLAN §5.1 asked for exactly this and estimated "~a day". It is done.
Tests: `tests/test_sourcing_agent.py::test_is_platform_host_matches_profile_hosts_and_subdomains`,
`::test_is_platform_host_ignores_real_websites`,
`::test_platform_profile_websites_are_prioritised`.

### 1.2 Scoring no-site leads highest — BUILT

`scoring.lead_score()` (utils/scoring.py L61-92) already puts need first:

```
none          + known channel = 10      none          + unknown = 8
platform_only + known channel =  9      platform_only + unknown = 7
owned_site    scored 0-3      =  8   … 4-5 = 6 … 6-7 = 4 … >=SITE_SCORE_GOOD_AT = 1
```

`scoring.lead_score_for_row()` (L95-104) does the same straight from a DB row.
`scoring.is_good_site()` (L47-54) is the healthy-site test. Both queues already
sort by it — `db._list_by_status_and_priority()` (utils/db.py L316-323), used by
`db.list_designable_leads_by_priority()` (L309-313) and
`db.list_sendable_leads_by_priority()` (L304-306), NULL scores last.

The plan's §4 says the same thing and it is right: **the score was never the
problem, the gate was.** No scoring change is needed to start. The one addition
the plan proposes (a `post` contact_channel worth 9/8) is cosmetic and can wait
until letters exist. Test: `tests/test_lead_scoring.py::test_no_site_and_platform_only_keep_their_sourcing_scores`.

### 1.3 `contact_channel`, written twice and corrected — BUILT

`sourcing_agent.contact_channel_for_place()` (L181-187) makes the first guess
at sourcing time: `email` for an owned site, `phone` for a listing with a phone
and no owned site, `unknown` otherwise.
`lead_agent._contact_channel()` (L209-217) corrects it after research actually
looked: `email` if one was found, else `phone` if the lead has a number, else
`unknown`. The column exists (`db.init_db` migration, utils/db.py L170).

STATE_AND_PLAN §5.2 lists "they need a `contact_channel` on the lead" as
outstanding work. It is not. Test:
`tests/test_research_scores_site.py::test_no_email_found_corrects_the_channel_sourcing_guessed`.

### 1.4 Phone, address, rating and reviews are already collected — BUILT

`PLACES_FIELD_MASK` (sourcing_agent L35-49) already requests
`places.nationalPhoneNumber` and `places.formattedAddress`, and
`sourcing_agent._insert()` (L462-478) writes `phone`, `address`,
`google_rating`, `google_reviews_count` on every lead. The columns were
migrated in at utils/db.py L151-153.

The plan's opening claim — "Google Places already gives `nationalPhoneNumber`
and `formattedAddress` for most listings … enough for a call or a letter" — is
**true and now measured**: of the 73 leads lost for want of an email, 65 have a
phone number and 73 out of 73 have an address containing a valid UK postcode
(§3). No sourcing change is needed to get the data for a call sheet or a letter.
It is already in `pipeline/leads.db`.

### 1.5 The preview already builds without a contact email — BUILT

`design_agent.build_context()` (L511-585) needs only name, niche, town, phone,
address and rating. `design_agent._places_facts()` (L411-431),
`design_agent.google_rating_badge()` (L434-441) and `design_agent._phone_href()`
(L450-452) supply the "this is really their business" facts. `contact_email`
appears in the context exactly once, at L544, as an optional field that renders
as nothing when absent.

So the plan's §2(b) assertion — "preview built exactly as today
(`build_site_files` needs only name, niche, town, phone, address, rating)" — is
correct. **Nothing in the design agent has to change to build a preview for a
phone-only or letter-only lead.** Only `park_reason()` stops it.

### 1.6 The no-site and platform-only email copy — BUILT

`sales_agent._intro_line()` (L284-317) already has three branches: an
`owned_site` branch that names a concrete audited defect, a `platform_only`
branch ("people searching for X only find a directory or social-media page"),
and a default no-site branch ("only find your Google listing"). STATE_AND_PLAN
§5.1 asked for the "your Facebook page is doing the work of a website" opener.
It landed on 2026-09-20.

### 1.7 Research never marks a no-email lead lost — BUILT (and important)

`lead_agent._research_lead_impl()` (L336-437) pushes every lead forward to
`researched` with whatever it found, on every exit path — the docstring at
L336-345 states this deliberately. **The entire no-email loss is one `if` in one
function**: `design_agent.park_reason()` L822-823.

That means step 4 below is genuinely a two-hour change, not a refactor.

Note on stale line numbers: `lead_agent.py` gained
`_candidate_subpages()` / `_MAX_RESEARCH_SUBPAGES` in `621fad0` (contact-email
search widened past one subpage, `tests/test_lead_agent_subpage_email_discovery.py`).
Every `lead_agent.py` line number in `no-site-leads-plan.md` is now off by
roughly 20-90 lines. The function names in it are still correct.

### 1.8 The schema plumbing for new columns — BUILT

`db._migrate_add_column()` (utils/db.py L178-185) is idempotent and already
runs eleven times in `db.init_db()` (L128-175). Adding `outreach_channel`,
`first_contact_at`, `next_action_at`, `call_attempts` costs **one line each** —
not part of the plan's 6-hour item 1.

`db.backup_database()` (L220-241) and the `pipeline_state` key/value table
(schema L115-119, `db.get_state` / `db.set_state` L200-217) also already exist.
`pipeline_state` means a call-sheet cursor or a skip-count metric needs no
schema change at all.

**What the plan's item 1 actually costs is only the `contacted` status**, which
sits inside a SQL `CHECK` constraint (`ALLOWED_STATUSES` L19-31, interpolated
into `_SCHEMA` at L50 via `_STATUS_LIST_SQL` L33) and so needs a full table
rebuild on the live DB. See step 6: **do not do that in week one.**

### 1.9 Preflight already reports the flag — PARTLY BUILT

`preflight.py` L455-461 prints an INFO line for `SOURCING_REQUIRE_WEBSITE`,
including the correct warning text when it is false ("no-site/platform-only
leads allowed; they need phone/platform follow-up"). It is an INFO, never a
WARN, and it does not know whether a follow-up channel exists — which is the
plan's item 3, and is real.

### Struck off the plan's 44 hours

| Plan item | Verdict |
|---|---|
| §2 "`lead_score()` already puts need first" | correct, nothing to do |
| §4 scoring changes | only the `post` channel value remains; defer |
| item 1, the *columns* | ~15 min via `_migrate_add_column`, not 6 h |
| item 1, the `contacted` *status* | real, 6 h, but **move it out of week one** (step 6) |
| item 4, "extend `_EMAIL_BLOCKLIST_SUBSTR`" | still needed for `_extract_email` (L30), but `_NON_BUSINESS_DOMAINS` (L47-56) already blocks facebook/yell/checkatrade/thomsonlocal from becoming `website_url` via `find_business_website()` — so only *sourced* platform-only leads are exposed |
| item 5, the healthy-site half of `park_reason` | built (L824-828), untouched by this work |
| item 12, the `platform_only` email copy | built (`_intro_line` L307-312) |

Realistic remaining cost for a working phone channel: **about 12 hours**, not 44.
Letters and SMS are the other 30 and neither is needed to start.

### One assumption in the plan that is wrong

The plan says no-site leads "Scoring 10, these leads head
`list_designable_leads_by_priority()` and are parked first every cycle." That
will be true of *future* no-site leads. It is **not** true of the 73 already
lost: all 73 have `lead_score = NULL` and `site_score = NULL`, so they sorted
*last*, not first (§3). They were parked because the queue eventually reached
them, not because they jumped it.

---

## 2. Build order

Smallest useful step first. Every step names the test that proves it. No step
touches `ENABLE_LIVE_SEND`, `SOURCING_ENABLED`, Stripe, the price band,
`EMAIL_MAX_*`, `OUTREACH_COOLDOWN_DAYS` or mailbox settings. Every new channel
defaults OFF.

### Step 1 — Count the pool before spending a week on it (1.5 h) ← START HERE

**Nobody has measured how many no-site businesses the existing niche × town
grid is actually throwing away.** `sourcing_agent._print_skip_summary()`
(L443-449) already prints `[sourcing] Skipped -- no website: N` on every run,
and that line is thrown away: `pipeline/logs/loop.log` is re-headed on every
`ops start` and holds none of it. The whole 44-hour plan is a bet on a number
that is one command away.

*Measurable today, zero code:* `python run.py source --dry-run --limit 12`
walks the grid read-only and prints the skip summary. Costs at most
`SOURCING_MAX_REQUESTS_PER_RUN` Places requests, inserts nothing.
**Caveat found in the code:** `iter_candidates()`'s `finally` block (L393-398)
calls `_save_rotation_offset()` on every exit path, so a "dry run" *does* write
`pipeline_state` and advance the live sourcing cursor, despite
`source_leads.py` L49 printing "Nothing was written." Run it right before a
real sourcing run, not in the middle of one.

- **Changes:** `_print_skip_summary()` also writes the run's counts to
  `pipeline_state` via the existing `db.set_state()` (suggested key
  `sourcing.last_skip_summary`); `preflight.py` prints it as a second INFO line
  beside the existing `sourcing website rule` check at L455-461.
- **Files:** `pipeline/agents/sourcing_agent.py`, `pipeline/preflight.py`.
- **Test:** new `tests/test_sourcing_skip_summary_recorded.py` — monkeypatch a
  Places page containing one place with no `websiteUri`, run `iter_candidates`,
  assert `db.get_state("sourcing.last_skip_summary")` contains `no website: 1`;
  assert it is still written when the run aborts on `PlacesQuotaError` (the
  `finally` already guarantees this). `tests/test_sourcing_agent.py` and
  `tests/test_preflight.py` must stay green unchanged.
- **Unlocks:** the go/no-go on steps 3-10, and the denominator for every
  conversion number afterwards. If the grid only discards two no-site places a
  run, stop reading this document and go widen `SOURCING_LOCATIONS` instead.

### Step 2 — Stop losing email leads to the robots gate (2 h)

Not strictly part of this channel, but it is the cheapest lead recovery in the
repo and it **changes why the 73 were lost** (§3). 45 of the 73 died on
`"Could not fetch website (robots.txt or network)"`, not on "no email exists".

`lead_agent._robots_allows()` (L101-112) uses `urllib.robotparser`, whose
`read()` fetches robots.txt with `urllib.request.urlopen` — the default
`Python-urllib/3.14` user agent, and **no timeout**. Two consequences, both
reproduced offline against the repo's own venv (Python 3.14.6):

```
robots.txt HTTP 401 -> _robots_allows = False     (disallow_all set inside read())
robots.txt HTTP 403 -> _robots_allows = False     <- Cloudflare/WAF blocking the urllib UA
robots.txt HTTP 404 -> _robots_allows = True
robots.txt HTTP 500 -> _robots_allows = False     (last_checked never set)
robots.txt HTTP 503 -> _robots_allows = False
robots.txt URLError -> _robots_allows = True
```

`RobotFileParser.read()` swallows `HTTPError` internally, so the
`except Exception: return True` at L108-111 never fires for these. The
docstring's claim "Fails open only when robots.txt itself can't be fetched" is
false for 401, 403 and 5xx. A site that blocks `Python-urllib` at the WAF —
which is most of the ones in the lost list: puregym.com, voodou.co.uk,
yorkdentalpractice.co.uk — is treated as a total disallow and never scraped,
even though `_fetch_response()` (L191-201) would have fetched the page happily
with the pipeline's own declared `ColdEmailSalesPipelineBot/1.0` UA.

- **Changes, in two clearly separate parts:**
  1. *Uncontroversial.* Fetch robots.txt with `requests`, using the same
     `REQUEST_HEADERS` (L26) and `REQUEST_TIMEOUT` (L25) as the page fetch, and
     hand the body to `RobotFileParser.parse()`. This declares the bot honestly
     and gets a real 200 + real rules from most of these hosts. It also removes
     the unbounded `urlopen` with no timeout from the main loop's path.
  2. *A decision for Oscar, not an automatic fix.* What to do on 401/403.
     RFC 9309 §2.3.1.3 treats 4xx as "unavailable" and permits crawling;
     §2.3.1.4 treats 5xx as "unreachable" and says assume disallow. Python is
     stricter than the RFC on 401/403 and matches it on 5xx. Aligning with the
     RFC (fail open on 4xx, keep 5xx blocked) is defensible and standard, but it
     is a policy call about someone else's server. Part 1 alone probably
     recovers most of the 45 without needing it.
- **Files:** `pipeline/agents/lead_agent.py`.
- **Test:** new `tests/test_robots_gate.py`, fully offline — a 200 robots.txt
  with `User-agent: * / Disallow: /` blocks; a 200 allowing all permits; a
  transport error fails open; 5xx blocks; and one case pinning whatever 401/403
  policy is chosen so the decision is recorded in a test rather than in a
  changelog. `tests/test_research_scores_site.py::test_unreachable_site_leaves_the_score_unknown`
  must still pass.
- **Measurable:** re-fetch robots.txt for the 45 hosts with both clients and
  count how many flip from blocked to allowed. That number is how many already
  paid-for leads come back.
- **Unlocks:** up to 45 leads returning to the *email* queue at zero sourcing
  cost. Expect roughly a quarter to survive the healthy-site gate (43 of 47
  audited sites score >= 8), so call it ~10 pitchable leads. Still the best
  hourly rate in this document.

### Step 3 — Let no-site places in, behind their own switch (2 h)

`sourcing_agent._skip_reason()` L215-217 returns `"no website"` whenever
`SOURCING_REQUIRE_WEBSITE` is true. Flipping that flag alone is a trap: no-site
listings are plentiful and score 10, so they fill `SOURCING_DAILY_LIMIT` ahead
of every owned-site lead and are then all parked `lost` at the design gate — 12
leads a day burned and zero emails sent.

- **Changes:** make the skip channel-aware. A no-website place is skipped
  unless at least one non-email channel is enabled, with the skip reason
  `"no website and no enabled channel"`. New config names, both defaulting
  false: `CALL_CHANNEL_ENABLED`, `LETTER_CHANNEL_ENABLED`. Upgrade the
  `preflight.py` L455-461 INFO to a WARN when `SOURCING_REQUIRE_WEBSITE` is
  false and no non-email channel is on.
- **Files:** `pipeline/agents/sourcing_agent.py`, `pipeline/config.py`,
  `pipeline/preflight.py`, `.env.example`.
- **Test:** extend
  `tests/test_sourcing_agent.py::test_website_filter_respects_sourcing_require_website`
  — flag off + channels off still skips; flag off + `CALL_CHANNEL_ENABLED` + a
  `nationalPhoneNumber` inserts with `website_status='none'`,
  `contact_channel='phone'`, `lead_score=10`. Add a `tests/test_preflight.py`
  case for the new WARN.
- **Unlocks:** Oscar can flip `SOURCING_REQUIRE_WEBSITE` himself without the
  pipeline quietly eating its own daily limit. The flag stays his decision.

### Step 4 — Don't park a lead we can reach (2 h)

`design_agent.park_reason()` L822-823 is the whole loss. One function.

- **Changes:** new `pipeline/utils/channels.py` with
  `choose_outreach_channel(lead) -> "email" | "call" | "letter" | None`, exactly
  as the plan's §2 specifies. `park_reason()` calls it and only parks on `None`;
  the healthy-site branch (L824-828) is untouched and still runs second.
  `_process_lead_impl()` (L737-740) writes the chosen channel to a new
  `leads.outreach_channel` column (one `_migrate_add_column` line, §1.8), so a
  later flag change cannot rewrite history.
- **Files:** `pipeline/utils/channels.py` (new),
  `pipeline/agents/design_agent.py`, `pipeline/utils/db.py`,
  `pipeline/config.py`.
- **Test:** `tests/test_design_skips_unmailable.py` and
  `tests/test_lead_quality_gate.py::test_no_email_is_still_the_first_reason_to_park`
  both assert on the existing behaviour and **must pass unchanged** — so with
  every channel off the park message must still start `"No contact email"`.
  New `tests/test_channels.py`: the flag matrix; a suppressed phone is never
  chosen; a town-only address (no postcode) is not letter-eligible.
- **Unlocks:** a no-site lead gets a real preview built instead of dying. This
  is the step that makes the channel exist.

### Step 5 — Keep the send queue email-only (1.5 h)

Once step 4 lets call/letter leads reach `designed`, they land in
`db.list_sendable_leads_by_priority()` (L304-306) with `lead_score` 9-10,
ahead of every email lead. `sales_agent._send_cold_email_impl()` L623-627 would
then mark each one `lost` with `"No usable email at send time"` — swapping one
silent leak for another.

- **Changes:** `db.list_sendable_leads_by_priority()` adds
  `COALESCE(outreach_channel,'email') = 'email'`.
- **Files:** `pipeline/utils/db.py`.
- **Test:** new `tests/test_send_queue_channels.py` — a `designed` lead with
  `outreach_channel='call'` is never returned; an email lead behind it still is;
  a legacy row with `outreach_channel` NULL still is.
- **Unlocks:** steps 3-4 are safe to leave running overnight.

Steps 1-5 total **9 hours** and are the whole of week one. Stop here and look at
the numbers before committing to the rest.

### Step 6 — The call sheet, without the table rebuild (5 h)

The plan's item 1 spends 6 hours rebuilding the `leads` table on the live DB to
add a `contacted` status, before anything works. **Skip it.** Keep a lead at
`designed` while it is being called and track the call state in a new
`contact_events` table plus `next_action_at` / `call_attempts` columns. The
`designed` status already means "preview built, not yet sent", which is exactly
true of a lead on the call sheet. Add `contacted` later, when step 8 needs a
teardown anchor — from a backup, with the loop stopped.

- **Changes:** new `pipeline/call_sheet.py`; `run.py` `_MODES` (L226-245) gains
  `call-sheet` and `call-log`; `db` gains `insert_contact_event`,
  `list_call_sheet`, `suppress_phone`, `is_phone_suppressed`; new tables
  `contact_events` and `suppressed_phones`; `CALL_SHEET_MAX_PER_DAY`,
  `CALL_RETRY_DAYS`, `CALL_MAX_ATTEMPTS`.
- **Files:** `pipeline/call_sheet.py` (new), `pipeline/utils/db.py`, `run.py`,
  `pipeline/config.py`.
- **Test:** new `tests/test_call_sheet.py` — ordering by `lead_score`, the daily
  cap, each outcome's state transition, retry after `CALL_RETRY_DAYS`, `lost`
  after `CALL_MAX_ATTEMPTS`, `do_not_call` writing `suppressed_phones`, and
  `interested --email` putting the lead back on the ordinary email path with
  `outreach_channel='email'`.
- **Unlocks:** the first lead reached by a route that is not email.

### Step 7 — TPS/CTPS screening, before the first dial (3 h)

Must land **before** step 6 is used against a real number, not after. §4.
Store `tps_checked_at` and the result on the lead; `choose_outreach_channel`
refuses `call` when the check is missing or older than 28 days; the call sheet
shows the status per row. Budget ~£40 per 5,000 checks
(`pecr-and-companies-house.md` §3, unverified).
**Test:** `tests/test_channels.py` gains "an unscreened or stale-screened lead
is never routed to `call`".

### Step 8 — Teardown and cooldown per channel (4 h)

`db.PREVIEW_EXPIRABLE_STATUSES` (L812) is `emailed/lost/bounced/unsubscribed`
and `db.list_expired_previews()` (L833-869) anchors the TTL on
`websites.created_at` plus a "no outbound `email_threads` row since" subquery. A
called or posted lead has neither, so its preview is torn down 7 days after
*build* regardless of when it was contacted. `db.last_cold_email_at()`
(L396-421) matches `notes = 'Cold email sent'` only, so a call or a letter does
not arm `OUTREACH_COOLDOWN_DAYS` and two plumbers in one town can get the same
draft by different routes. This is where `contacted` finally earns its table
rebuild. **Test:** `tests/test_preview_teardown.py` and
`tests/test_outreach_cooldown.py` additions; every existing case unchanged.

### Step 9 — Letters with a QR code (6 h)

As the plan's item 10. The QR must target the Vercel `preview_url`, never
`PUBLIC_BASE_URL` — that is an ngrok tunnel which is down whenever the PC is
off, which is when letters get read. Scan tracking is lost; accept it.
Prerequisite: step 8, because a letter's TTL promise has to survive the post.

### Step 10 — SMS / WhatsApp (6 h), last

Needs Companies House matching, a Twilio sender, a signed webhook and the
`ENABLE_LIVE_SMS` gate added to AGENTS.md. Reaches only verified limited
companies — **6 of the 73** by name (§3). Lowest reach per hour of work in this
document. Do it last or not at all.

---

## 3. The contact problem, with numbers from the live DB

All figures from `pipeline/leads.db`, read-only, 2026-09-20. 164 leads total.

### The cohort

The loss reason lives in `state_history.notes`, not `leads.notes` —
`db.update_lead_status()` (L440-450) writes the note to `state_history` while
`leads.notes` still holds the JSON blob `sourcing_agent.Candidate.summary()`
(L141-149) put there. Query the history:

```sql
-- the cohort, and everything §3 claims about it, in one query
WITH no_email_loss AS (
  SELECT l.*
    FROM leads l
   WHERE l.status = 'lost'
     AND EXISTS (SELECT 1 FROM state_history sh
                  WHERE sh.lead_id  = l.id
                    AND sh.to_state = 'lost'
                    AND sh.notes    = 'No contact email found; preview not built')
)
SELECT COUNT(*)                                    AS leads,
       SUM(COALESCE(TRIM(phone),    '') <> '')     AS with_phone,
       SUM(COALESCE(TRIM(place_id), '') <> '')     AS with_place_id,
       SUM(COALESCE(TRIM(address),  '') <> '')     AS with_address,
       SUM(site_score IS NULL)                     AS never_audited,
       SUM(REPLACE(phone,' ','') LIKE '07%')       AS mobile_numbers
  FROM no_email_loss;
```

```
leads  with_phone  with_place_id  with_address  never_audited  mobile_numbers
   73          65             73            73             73              23
```

**Answers to the two questions asked:**

- **Phone number: 65 of 73 (89%).** 42 landlines (01/02/03), 23 mobiles (07),
  8 with no number at all.
- **Google listing: 73 of 73 (100%)** — every one carries a `place_id`, so a
  Maps URL is constructible as
  `https://www.google.com/maps/place/?q=place_id:<place_id>`. There is **no
  stored Google listing URL column**; the plan does not claim one and none is
  needed. Zero of the 73 have a `website_url` on a google.com/goo.gl/business.site
  host, so none of them are "Google-listing-only" in the `platform_only` sense.
- Bonus: **73 of 73 have an address containing a valid UK postcode**
  (checked with `[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}`), so the letter
  channel's eligibility rule is satisfied by every single one.

### Three things about this cohort the plan does not say

```sql
SELECT website_status, contact_channel, COUNT(*) FROM no_email_loss GROUP BY 1,2;
--   owned_site | email | 73

SELECT CASE WHEN sh.notes LIKE 'Could not fetch website%' THEN 'could-not-fetch'
            ELSE 'fetched, no email found' END AS outcome, COUNT(*)
  FROM state_history sh
 WHERE sh.to_state = 'researched'
   AND sh.lead_id IN (SELECT id FROM no_email_loss)
 GROUP BY 1;
--   could-not-fetch          | 45
--   fetched, no email found  | 28
```

1. **All 73 have their own website.** `website_status = 'owned_site'`, 73 of 73.
   Not one is a no-site or platform-only lead. They are not the population this
   channel is for — they are owned-site businesses whose email could not be
   scraped. **Building the call channel does not, by itself, recover them**;
   step 2 does.
2. **45 of the 73 were never actually read.** They died on
   `"Could not fetch website (robots.txt or network)"`, which §2 shows is
   overwhelmingly the `urllib` user agent being blocked at a WAF, not an absence
   of an email address. Only 28 were fetched and genuinely had no findable
   address — and all 28 predate the wider subpage search added in `621fad0`.
3. **All 73 have `site_score` and `lead_score` NULL.** Both research exit paths
   that produced them (L372-379 and the pre-audit code) skip the
   `update_lead_fields` call that writes scores, so these leads sorted *last* in
   `_list_by_status_and_priority()`, not first. And because they were never
   audited, we do not know how many of them even need a new website — among the
   47 leads that *were* audited, 43 score >= 8 and would have been parked as
   healthy anyway.

The leak is also **still running**: 7 of the 73 were lost today, and the daily
count has been 4-10 every day since 2026-09-10.

Niches: electrician 13, dentist 11, plumber 10, cafe 10, gym 8, landscaper 8,
salon 7, restaurant 6. Towns: Manchester 51, Liverpool 16, York 6.

### Which non-email channel to build first

**Phone.** Not close.

- **The data is already there.** 65 of 73 numbers, collected on every lead since
  sourcing began (§1.4). Zero new acquisition, zero new spend on data.
- **The preview already builds** for a phone-only lead (§1.5). A call sheet is a
  query and a print — `pipeline/call_sheet.py` writes nothing to the outside
  world.
- **A call converts to the existing pipeline in one step.** `call-log <id>
  --outcome interested --email x@y` sets `contact_email`, flips
  `outreach_channel` to `email`, and the lead is an ordinary email lead from
  then on — with consent, which is the only thing that makes a sole trader
  emailable at all (§4). Nothing downstream changes.
- **It suits one person.** Ten rows and a phone, worked in a fixed slot. No
  service to run, no Twilio account, no webhook, no signature verification, no
  new thing that breaks at 3am while Oscar is asleep. Letters cost £1.10-1.30
  each and put a two-to-four-day postal delay between the work and any signal;
  SMS needs Companies House plus Twilio plus a webhook to reach 6 of 73 leads.
- **It produces information fastest.** Fifty dials in a week tell Oscar whether
  no-site businesses in Manchester want a website at all. Fifty letters tell him
  in three weeks, for £60.

The one real prerequisite is TPS/CTPS screening (step 7) — and that is a
£40 API key and three hours, not a redesign. **Letters are the correct second
channel** (legally simplest, §4) and the right fallback for the 8 leads with no
number. **SMS is last.**

---

## 4. The honest risk: PECR makes this channel mostly un-emailable

From `pecr-and-companies-house.md` §1, and it is the central fact about this
whole idea.

Under PECR reg 22 a **sole trader or ordinary partnership is an individual
subscriber**: no unsolicited marketing email without prior consent, and the soft
opt-in explicitly **does not cover a first cold email** to a new contact. Only
corporate subscribers — Ltd, PLC, LLP, Scottish partnerships, public bodies —
can be cold-emailed. Since 5 February 2026 the maximum PECR penalty is £17.5m or
4% of turnover. For a one-person business that is not a theoretical risk.

**Businesses with no website skew heavily towards sole traders.** That is the
same fact that makes them good prospects — nobody has sold them anything yet —
and it is what makes them hard to reach legally. In this database, using the
free first-pass signal that already exists,
`scoring.looks_like_limited_company()` (utils/scoring.py L57-58):

```sql
SELECT COUNT(*) FROM leads
 WHERE business_name LIKE '%Ltd%' OR business_name LIKE '%Limited%'
    OR business_name LIKE '%LLP%' OR business_name LIKE '%PLC%';
```

- 25 of 164 leads (15%) read as incorporated by name.
- **6 of the 73** in the no-email cohort (8%).
- 10 of 51 `designed`, 5 of 30 `emailed`.

A name check is weak evidence in both directions — "Dave's Plumbing" may well
trade as DAVES PLUMBING LTD, and a Companies House match on a generic name is a
false-positive risk that would put a sole trader in the email bucket. But the
direction is unambiguous: **the large majority of what this pipeline sources is
probably unincorporated, and it is already being cold-emailed.** That applies to
the 30 already emailed, not just to future no-site leads. It is the single
largest unaddressed risk in the business, and it is not created by this channel
— this channel just makes it worse, because no-site businesses skew further that
way.

**How that interacts with the channel:**

- **Email does not extend to no-site leads.** Scraping harder — a second DDG
  query, a Facebook About page — finds addresses the pipeline may not lawfully
  use. The plan's §2(a) already judges the yield "low single-digit"; PECR makes
  the yield worse than useless, because the reachable ones are exactly the ones
  most likely to be individual subscribers. Drop §2(a) from the build order
  entirely, except for the `_EMAIL_BLOCKLIST_SUBSTR` hygiene fix.
- **Phone is legal but narrower than it looks.** Cold B2B calls are permitted to
  any number not on TPS or CTPS — but sole traders and ordinary partnerships
  register on the **TPS**, and that is precisely this population. Screening must
  cover both registers plus an internal do-not-call list, no older than 28 days
  (registrations take up to 28 days to activate). Expect screening to remove a
  meaningful slice; the 23 mobile numbers in the cohort are the most likely to
  be both personal and registered.
- **Post is outside PECR altogether.** "Direct marketing by post is not covered
  by PECR." A letter to "The Owner, <Business>, <address>" at a company uses no
  personal data at all; for a sole trader the trading address may be personal
  data, so it needs a legitimate-interests note on file and a privacy notice on
  the QR landing page. No consent, no preference register with statutory force
  (the MPS is good practice, not law), no screening subscription.

**So: does PECR make phone or post the better route?**

Post is the *safer* route and phone is the *better* one, and the difference is
smaller than it looks. Post has no consent problem, no screening cost and no
28-day staleness rule; its costs are money (~£1.20 a letter) and latency (three
weeks to a signal). Phone has the lowest cost per contact, the fastest feedback
and the one property nothing else has — **a call is how you get consent**, so it
converts a legally unemailable sole trader into a lead who has said "yes, email
me the link", which is what makes everything downstream lawful.

Recommendation: **phone first, gated behind TPS/CTPS screening (step 7), with
post as the fallback for the 8 leads with no number and for anyone the screen
removes.** Do not treat either as a way to get more email addresses to cold-mail;
treat the call as the point at which permission is asked for out loud.

Separately, and outside this document's scope: the Companies House check belongs
on the *email* path, before the next live batch, not on the no-site path. That
is a bigger decision than this build order and it is Oscar's, not an agent's.

---

## 5. For Oscar

If you do one thing from this document, do **step 1: run
`python run.py source --dry-run --limit 12` and read the
`[sourcing] Skipped -- no website: N` line**, then spend the ninety minutes to
make that number stick in `preflight`. Everything else here is a bet on a pool
size nobody has measured, and you are about to be asked for forty-four hours on
the strength of it. Within a week you will know two things you do not know
today: how many no-site businesses your current towns and trades are actually
throwing away per run, and — if you also take step 2, which is two hours — how
many of the 73 leads you have already paid Google for were not unreachable at
all but simply blocked by your own robots.txt check using the wrong user agent.
My expectation is that step 2 puts roughly ten pitchable leads back in the email
queue this week for two hours' work, while step 1 tells you whether the no-site
channel is a real seam or a rumour. Do not flip `SOURCING_REQUIRE_WEBSITE` until
step 3 lands — today that flag would fill your daily limit with leads that get
built and immediately parked, and your sends would go to zero while it happened.

---

## Appendix: verification

- Robots behaviour reproduced offline with the repo's own interpreter
  (`venv/Scripts/python.exe`, Python 3.14.6) by monkeypatching
  `urllib.request.urlopen` to raise `HTTPError` with each status and calling
  `lead_agent._robots_allows()`. No network request was made to any business
  site.
- All DB figures read from `pipeline/leads.db` opened
  `file:pipeline/leads.db?mode=ro` (read-only URI). Nothing was written.
- No source file, no `.env`, no service and no git branch was touched.
