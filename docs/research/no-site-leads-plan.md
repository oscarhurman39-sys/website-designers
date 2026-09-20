# Reaching businesses with no website: change set (design only)

Written 2026-09-20 against commit 6daf22b. Nothing here is built. Line numbers
are from that commit. Config is referred to by variable NAME only.

The stated target is a business with no site or a Facebook/directory page.
Today it cannot be reached: every lead needs a scraped email and the only
outbound path is `send_cold_email`. 73 of 164 sourced leads died on "no contact
email"; a no-site lead never enters the DB. Google Places already gives
`nationalPhoneNumber` and `formattedAddress` for most listings (`leads.phone`,
`leads.address`), enough for a call or a letter.

## 1. What happens today

| Stage | `none` (no `websiteUri`) | `platform_only` (Facebook/Yell/Checkatrade URL) |
|---|---|---|
| **Source** | `sourcing_agent._skip_reason()` L214-216 returns `"no website"` because `config.SOURCING_REQUIRE_WEBSITE` (config.py L241) defaults true. Never inserted. | Passes `_skip_reason`. `_to_candidate()` L289-307 stores `website_status=platform_only`, `contact_channel=phone` (`contact_channel_for_place()` L180-186), `site_score=0`, `lead_score=9` (7 without a phone). Inserted as `new`. |
| **Research** | Only via CSV or with the flag off. `_research_lead_impl()` L316-337: DuckDuckGo lookup; if nothing, `researched`, `contact_channel=phone` (`_contact_channel()` L203-211), `contact_email` NULL, `lead_score=10`. Survives. | `website_url` is the Facebook URL, so the DDG fallback is skipped. `_fetch_response()` L185-195 obeys robots.txt; facebook.com refuses, so L339-346 marks `researched` "Could not fetch website". A Yell/Checkatrade page that does fetch hits L349-395: `site_audit` scores the *directory* page and overwrites `site_score` 0, `_extract_email()` L242-255 may store a directory-owned address (blocklist L30 lacks yell/checkatrade/facebook), and `pain_point` becomes the directory's defects. |
| **Design** | `design_agent.park_reason()` L822-823: `"No contact email found; preview not built"`; `run()` L850-853 and `_process_lead_impl()` L737-740 mark `lost`. This gate killed the 73. Scoring 10, these leads head `list_designable_leads_by_priority()` and are parked first every cycle. | Same. |
| **Send** | Never reached. `_send_cold_email_impl()` L623-627 would mark `lost` "No usable email at send time"; `send_next_pending()` L753-765 has no other outbound path. | Same. |
| **Teardown** | `db.list_expired_previews()` L802-838 anchors the TTL on `websites.created_at` and the last outbound `email_threads` row; `PREVIEW_EXPIRABLE_STATUSES` L781 is `emailed/lost/bounced/unsubscribed`. A phone/post lead has neither: never torn down under `designed`, torn down 7 days after *build* under `lost`. | Same. |

Preflight only echoes the flag (`sourcing website rule`, preflight.py
L455-461); it does not warn that flipping it feeds leads into the design park.

## 2. Proposed flow, per contact channel

One new module, `pipeline/utils/channels.py`, decides at build time which
channel carries the preview:

```
choose_outreach_channel(lead) -> "email" | "call" | "letter" | None
  email   contact_email present                                   (unchanged path)
  call    phone present, CALL_CHANNEL_ENABLED, phone not suppressed
  letter  LETTER_CHANNEL_ENABLED, address carries a UK postcode
  None    -> park (today's behaviour)
sms_allowed(lead) -> SMS_CHANNEL_ENABLED and lead.is_limited_company == 1
```

`contact_channel` keeps meaning "best route we *know*" (email/phone/post/
unknown, written by sourcing and research). New `outreach_channel` is the route
we *chose*, written once at build, so a later flag change never rewrites
history. `is_limited_company` / `company_number` come from the Companies House
work; `NULL` (unchecked) counts as "not limited" everywhere, so SMS can never go
to an unverified number.

### (a) Email found on a platform/directory page

Honestly: Facebook refuses the crawler in robots.txt and the pipeline's own
`_robots_allows` rule says do not fetch; the About panel is JS-rendered behind
a login wall; Graph API needs a token for a page we do not manage. **Do not
build a Facebook scraper.** Yell and Checkatrade are server rendered but mostly
show a contact *form* and a phone, not an address. Expect low single-digit
yield. Two cheap fixes make the path safe rather than productive:

- Research must not audit or take a pain point from a `platform_only` page
  (`site_score` stays 0), and `_EMAIL_BLOCKLIST_SUBSTR` gains `facebook.com`,
  `yell.com`, `checkatrade.com`, `thomsonlocal.com`.
- A second DDG query, `"<name>" <town> email`, for `none`/`platform_only`
  leads only, snippet scanned by `_EMAIL_RE`, hit verified by `email_verify`.
  No new flag: this is the existing email path.

Lead row needs nothing new; "sent", replies and teardown are the existing path.

### (b) Phone-first: preview, call sheet, SMS/WhatsApp link for Ltd only

- Preview built exactly as today (`build_site_files` needs only name, niche,
  town, phone, address, rating), `outreach_channel=call`.
- `python run.py call-sheet [--csv]` lists `designed` leads with
  `outreach_channel=call`, best `lead_score` first, capped at
  `CALL_SHEET_MAX_PER_DAY`: name, town, niche, phone, rating/reviews, preview
  URL, `is_limited_company`, TPS/CTPS status (a `tps_checked_at` column the
  legal work defines), attempts so far, a 6-line script. Writes nothing.
- Oscar calls, then logs `python run.py call-log <id> --outcome
  no_answer|voicemail|interested|not_interested|callback|wrong_number|do_not_call
  [--email x@y] [--note ...]`:
  - any first dial: status `contacted`, `first_contact_at`, `call_attempts+=1`,
    a `contact_events` row (`channel=call, direction=outbound, outcome`).
  - `interested --email`: `contact_email` set, `outreach_channel=email`, back to
    `designed`; the normal cold email goes next cycle with a warm intro ("good
    to speak just now"). It is an ordinary email lead from then on.
  - `interested`/`voicemail`/`no_answer` on a lead with `sms_allowed`: one SMS
    or WhatsApp with the link, under 160 chars, ending "Reply STOP to opt
    out". One per lead, ever. `ENABLE_LIVE_SMS=false` logs it to
    `pipeline/dry_run.log` like email.
  - `no_answer`/`voicemail`: `next_action_at = now + CALL_RETRY_DAYS`, back on
    the sheet after that, `lost` after `CALL_MAX_ATTEMPTS`.
  - `not_interested`/`do_not_call`/`wrong_number`: `lost`, phone into
    `suppressed_phones` (the phone twin of `unsubscribes`).
- Replies: inbound SMS hits `POST /webhook/twilio/sms` (signature checked),
  matched to `leads.phone` in E.164, logged to `contact_events`, classified by
  the existing keyword `classify_reply`. STOP/negative -> `lost` + suppression;
  else `replied` + `alert_positive_reply`. No autonomous negotiation over SMS.
  An inbound call is logged with `call-log --phone <number>`.
- Teardown: `contacted` joins `PREVIEW_EXPIRABLE_STATUSES`; the TTL anchors on
  `leads.first_contact_at` (set by every channel, cold email included) instead
  of the `email_threads` subquery; `PREVIEW_TTL_DAYS_CALL` (14) covers two
  dials. A lead still `designed` on the sheet is never torn down, as today.

### (c) Letter with a QR code, for everything else

- Preview built as today, `outreach_channel=letter`. Eligible only if
  `address` matches a UK postcode regex; town-only addresses are parked.
- `python run.py letters --render` writes `pipeline/out/letters/<id>.pdf` from
  `templates/letter/letter.html` (Jinja) through the Playwright browser
  `screenshot.py` already drives. Content: address block, cached preview
  screenshot, QR code (`segno`, pure Python) plus the URL typed out, reference
  `L-<id>`, `_pricing_line()`, `_guarantee_line()`, "live until <date>" (today +
  `PREVIEW_TTL_DAYS_LETTER`), the `PHYSICAL_ADDRESS` footer, an opt-out line.
  **The QR targets the Vercel `preview_url`, not `tracker.create_click_link()`**:
  that lives on `PUBLIC_BASE_URL`, an ngrok tunnel that is down whenever the
  PC is off, which is when letters get read. Scan tracking is lost; accept it.
- `python run.py letters --posted <ids...>` marks `contacted`,
  `first_contact_at = posted date`, `contact_events(letter, outbound)`. An
  unrendered letter stays `designed`.
- Replies come by email or phone from an address we never stored, so
  `check_inbox()` L1620 (`get_lead_by_email`) misses them. Add
  `_lead_from_reference(msg)`: regex `\bL-(\d+)\b` on subject+body; on a match
  set `contact_email = from_addr` and hand to `_handle_inbound`, so the
  existing negotiator takes over. Any other unmatched inbound naming a
  business in the DB raises `alert_needs_human` instead of being dropped.
- Teardown: `PREVIEW_TTL_DAYS_LETTER` 21 (2-4 days in the post, a week to
  read, a week to act), same `first_contact_at` anchor. The letter never says
  "7 days".

Cooldown: `_cooldown_blocked_until()` reads `db.last_cold_email_at()`, which
matches `notes = 'Cold email sent'` only. Calls and letters must count too, or
two Crawley plumbers get the same template days apart by different routes:
`last_first_touch_at(niche, town, days)` unions `contact_events`.

## 3. Work items, in order

Every new channel defaults OFF. The existing hard gates are untouched.

| # | Item | Files / functions | Config (name = default) | DB | Tests | h |
|---|---|---|---|---|---|---|
| 1 | Schema and `contacted` status | `db.py`: `ALLOWED_STATUSES`, one-off table-rebuild migration (SQLite cannot alter a CHECK) after `backup_database()`; `insert_contact_event`, `count_designed_by_channel`, `list_call_sheet`, `list_letter_queue`, `suppress_phone`, `is_phone_suppressed`, `last_first_touch_at` | none | `leads.outreach_channel`, `first_contact_at`, `next_action_at`, `call_attempts INT DEFAULT 0`, `is_limited_company`, `company_number` (if not landed); tables `contact_events(id, lead_id, channel, direction, outcome, body, timestamp)`, `suppressed_phones(phone PK, timestamp)`; status `contacted` | `test_db_contacted_migration.py`: migrate a copy of a pre-change DB; CHECK accepts `contacted`; rows and indexes intact | 6 |
| 2 | Channel chooser and flags | new `utils/channels.py`; `config.py` | `CALL_CHANNEL_ENABLED=false`, `LETTER_CHANNEL_ENABLED=false`, `SMS_CHANNEL_ENABLED=false`, `ENABLE_LIVE_SMS=false` **(Commander-only, add to AGENTS.md hard gates)**, `CALL_SHEET_MAX_PER_DAY=10`, `CALL_RETRY_DAYS=2`, `CALL_MAX_ATTEMPTS=2`, `SMS_MAX_PER_DAY=5`, `PREVIEW_TTL_DAYS_CALL=14`, `PREVIEW_TTL_DAYS_LETTER=21`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_WHATSAPP_FROM` (names in `.env.example`, blank) | none | `test_channels.py`: flag matrix; `sms_allowed` false for NULL/0 `is_limited_company`; suppressed phone never chosen | 3 |
| 3 | Sourcing skip rule | `sourcing_agent._skip_reason()`: a no-website place is skipped unless a channel that can reach it is enabled ("no website and no enabled channel"); `preflight.py` L455-461 becomes WARN when `SOURCING_REQUIRE_WEBSITE=false` and no non-email channel is on | none | none | `test_sourcing_agent.py`: with flag off and channels off, still skipped; with `CALL_CHANNEL_ENABLED` and a phone, inserted with `contact_channel=phone` | 2 |
| 4 | Research: platform pages | `lead_agent._research_lead_impl()`: skip `site_audit` and pain point for `platform_only`; extend `_EMAIL_BLOCKLIST_SUBSTR`; `_contact_channel()` returns `post` when only a postcode address exists; DDG email query for `none`/`platform_only` | none | none | `test_research_scores_site.py`: Yell page keeps `site_score=0`, directory address rejected, `post` channel | 3 |
| 5 | DesignAgent gate | `park_reason()` uses `choose_outreach_channel`; `_process_lead_impl()` stores `outreach_channel`; `run()` caps per channel (`count_designed_by_channel`) so ten call-sheet leads cannot starve the email queue | `DESIGN_BACKLOG_MAX` unchanged (email); `CALL_SHEET_MAX_PER_DAY` and `LETTER_BACKLOG_MAX=10` double as build caps | none | `test_lead_quality_gate.py`, `test_design_skips_unmailable.py`: defaults off -> still parked with a message starting "No contact email" (existing assertions hold); call on + phone -> built; letter on + postcode -> built; Ltd-ness irrelevant to building | 3 |
| 6 | Send-queue isolation and reply matching | `db.list_sendable_leads_by_priority()` adds `COALESCE(outreach_channel,'email')='email'`; `sales_agent.check_inbox()` gains `_lead_from_reference`; `_intro_line()` warm branch when a `call/interested` event exists; `last_first_touch_at` used by `_cooldown_blocked_until` | none | none | `test_send_queue_channels.py`: a `designed` call lead is never passed to `send_cold_email`; ref-code reply attaches email and reaches `_handle_inbound`; cooldown counts a posted letter | 3 |
| 7 | Teardown per channel | `db.PREVIEW_EXPIRABLE_STATUSES` += `contacted`; `list_expired_previews()` anchors on `first_contact_at` with TTL by `outreach_channel`; cold-email send sets `first_contact_at` | the two TTL names above | none | `test_preview_teardown.py`: letter lead survives day 8, expires day 22; call lead day 15; existing email cases unchanged | 3 |
| 8 | Call sheet and call log | new `pipeline/call_sheet.py`; `run.py` modes `call-sheet`, `call-log` (`_MODES`); script text in the sheet | see 2 | uses 1 | `test_call_sheet.py`: ordering, cap, each outcome's state transition, retry after `CALL_RETRY_DAYS`, `lost` after max attempts, suppression | 5 |
| 9 | SMS/WhatsApp path | new `utils/sms_utils.py` (Twilio REST; the installed `twilio` plugin is for docs, not runtime); `webhook_server.py` `/webhook/twilio/sms`; Ltd check enforced *inside* `send_sms` (raises otherwise) | `ENABLE_LIVE_SMS`, `SMS_MAX_PER_DAY` | `contact_events` | `test_sms_channel.py`: non-Ltd raises; dry run logs; one-per-lead; STOP -> `lost` + `suppressed_phones`; webhook signature rejected | 6 |
| 10 | Letters | new `pipeline/letters.py`, `templates/letter/letter.html`; `run.py letters`; `segno` in requirements | see 2 | uses 1 | `test_letters.py`: HTML carries ref, QR points at `preview_url` not `PUBLIC_BASE_URL`, "live until" date, footer address; `--posted` transitions; town-only address parked | 6 |
| 11 | Preflight, report, dashboard | `preflight.py`: "outreach channels" INFO line, BLOCKER if `SMS_CHANNEL_ENABLED` with blank Twilio names, send-queue line split by channel, "contacted leads overdue" WARN; `db.weekly_funnel()` gains `called`, `sms`, `letters` | none | none | `test_preflight.py`, `test_weekly_report.py` additions | 3 |
| 12 | Docs | AGENTS.md hard gates (`ENABLE_LIVE_SMS`), STATE_AND_PLAN 5.2, README sourcing wording (already stale), `.env.example` | | | none | 1 |

About 44 hours. Items 1-7 (23 h) are the plumbing and pay off even if only the
letter channel is ever switched on.

## 4. Scoring and gate changes

`scoring.lead_score()` (L61-92) already puts need first: `none`+phone = 10,
`platform_only`+phone = 9. The problem was never the score, it was the gate.
Two changes, neither moving an existing test:

- New `contact_channel` value `post` (postcode address, no phone or email):
  `none`+post = 9, `platform_only`+post = 8, one below phone because a letter
  is slower and costs money. `unknown` stays 8/7.
- `lead_score()` stays pure. Whether a channel is *enabled* is a routing fact,
  not a quality fact, and lives in `channels.choose_outreach_channel` and
  `park_reason`. With every channel off the behaviour is exactly today's
  (scored 10, parked at design): unreachable is unreachable.

`park_reason()` becomes:

```
channel = channels.choose_outreach_channel(lead)
if channel is None:
    return "No contact email found and no other channel enabled (phone/letter); preview not built"
if scoring.is_good_site(...): return <existing healthy-site message>
return None
```

A lead with a phone or postcode address and the matching flag on is no longer
parked. The healthy-site gate is unchanged and still runs second.

## 5. Risks and open decisions for Oscar

1. **Who makes the calls, and when.** The sheet only works if it is worked
   most days; a lead left `designed` on it still holds a repo and a Vercel
   project. Pick a daily slot and set `CALL_SHEET_MAX_PER_DAY` to match.
2. **Letter cost.** Roughly £1.10-1.30 each (second-class stamp, print,
   envelope); 10 a day is about £250 a month at an unmeasured reply rate.
   Start with `LETTER_BACKLOG_MAX=5`. A print-and-post API (Stannp, Docmail) is
   a later item.
3. **`SOURCING_REQUIRE_WEBSITE=false` and the daily limit.** No-site listings
   are plentiful and score 10, so they fill `SOURCING_DAILY_LIMIT` (12) ahead
   of every owned-site lead. With channels off that is 12 leads a day parked
   lost and zero emails. Item 3 makes the skip channel-aware, but do not flip
   the flag until a non-email channel is on, and expect email sends to fall
   while the mix shifts.
4. **Duplicate previews per town.** `db.duplicate_preview_risks()` only warns.
   With three routes into one niche+town the cooldown must count calls and
   letters (item 6) or neighbours compare identical drafts.
5. **Legal edges the parallel research must settle before items 8-9 go on:**
   TPS/CTPS screening for calls (sole traders and partnerships can register),
   the Companies House match rule (name alone is weak; want postcode too), a UK
   Twilio sender with STOP handling, the letter's opt-out wording.
6. **The `contacted` table rebuild** touches the live `leads.db`. Once, from a
   backup, with the loop stopped (`ops stop`).
7. **QR without tracking.** No scan count. If that matters, front the tunnel
   with a real domain first, the prerequisite `CLICK_TRACKING_ENABLED` already
   has.
