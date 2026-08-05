# Improvement plan & sibling-repo audit

Written 2026-07-30. This documents (a) what was actually wrong with the
pipeline, (b) what was fixed in this pass, (c) an honest verdict on every
sibling repo that was surveyed for reusable code, and (d) what to build
next, in order.

## What was actually broken (found by reading + running the code)

1. **The pipeline could not send a single email.** `sales_agent.py`'s
   `_validate_preview_link_for_send` was a botched merge: it referenced
   variables and constants that don't exist in that module and called
   itself recursively. Every send raised `NameError`. Fixed by moving the
   shared URL checks into `utils/url_safety.py` (used by both design and
   sales agents).
2. **The pipeline could not even be imported.** `email_utils.py` imported
   `sendgrid` unconditionally, but sendgrid was never in requirements.
   Now a soft import (SMTP path needs nothing), and sendgrid is pinned in
   requirements for those who set `SENDGRID_API_KEY`.
3. **Every preview created a private GitHub repo** (`design_agent.py`)
   even though the Vercel deploy was already git-less — this is what
   filled the account with ~40 dead repos. Previews no longer touch
   GitHub at all: rendered files are kept in `pipeline/rendered_sites/`
   (gitignored) and the private hand-off repo is created lazily by
   `create_handoff_repo()` only when a client pays and you run
   `transfer <id>`. `scripts/cleanup_preview_repos.py` (dry-run by
   default, sold/transferred leads protected) cleans up the existing mess.
4. **Stripe webhook had no replay protection.** Stripe retries events;
   each retry re-fired the "PAYMENT RECEIVED" handling. Now guarded by a
   `processed_events` table (`db.record_event_once`).
5. **Scraped business names flowed into email subject headers raw** —
   a newline in a hostile page could inject extra headers into outbound
   mail. Subjects are now sanitized at the transport layer.
6. **The cold-email intro lied.** It claimed the business had no website
   ("people only find your Google listing") — while the pipeline had just
   scraped their website to get their email. The intro now uses a real
   audit finding, or an honest generic line.
7. **A failing deploy retried forever, every 60s.** Now capped at 3
   attempts (`design_attempts` column), then marked lost with the error.
8. `utils/email_verify.py` was a stub that always returned `True` and was
   called by nothing. Deleted.

## What was added

- **`utils/site_audit.py`** — a cheap, honest audit of the lead's
  existing site (HTTPS, mobile viewport, meta description, title, h1,
  image alts, response time, page weight, stale copyright year, social
  links). Produces `pain_points` (their site, for email personalization),
  `improvements` (our preview, for the "what we improved" checklist), and
  an additive prospect score. Inspired by lighthouse-ci's purpose, sized
  for this pipeline.
- **Research agent upgrades** (`lead_agent.py`): phone extraction
  (tel: links + UK number patterns, fills the templates' `{{ phone }}`),
  obfuscated-email de-obfuscation ("info [at] domain [dot] com"), up to 3
  same-site subpages crawled (contact page first), audit stored per lead.
- **Per-lead email personalization** (`sales_agent.py`): the "what we
  improved" checklist now leads with the audit's specific findings; the
  LLM drafting (optional, HF) now uses a two-step facts→draft prompt with
  explicit negative constraints, drafts ONLY the subject + opening (the
  compliance-critical body stays deterministic), and falls back cleanly —
  `HF_API_TOKEN` is no longer required to run the pipeline.
- **Follow-up sequence**: nudge ~day 3, breakup ~day 7 (config:
  `FOLLOWUP_GAPS_DAYS`), sent as "Re:" on the original thread, same rate
  limits and unsubscribe footer, at most one automated send per cycle,
  never after any reply. Cadence cross-checked against the outreach
  playbook in show-me-the-money.
- **Template SEO/a11y floor**: all 9 templates now ship a meta
  description, Open Graph tags, an emoji favicon, and XSS-safe
  schema.org LocalBusiness JSON-LD built from real lead data — the exact
  things `site_audit` flags on prospects' sites, so the preview passes
  its own audit.

## Sibling repo verdicts (what was reusable, honestly)

| Repo | Verdict |
|---|---|
| show-me-the-money | Prompt library, no code. Its outreach cadence (day 0/3/7, short follow-ups, warm-up ramp) informed `FOLLOWUP_GAPS_DAYS` and confirms `WARMUP.md`. Note: it argues cold email is a weak primary channel for local retail — worth reading before scaling volume. CC BY-NC, so numbers/ideas only, no copied prose. |
| hive | Big multi-agent framework (Apache-2.0); do NOT adopt wholesale. Three patterns ported as ideas: retry caps with `last_error` (design_attempts), additive scoring rubrics (site_audit score), two-step personalization prompts with negative constraints (drafting prompt). Its stall-detector and per-niche memory files are the best future borrows. |
| worldmonitor | Production TS/Convex SaaS, AGPL — no code copying. Provoked three real fixes: webhook event dedupe, header sanitization for scraped strings, free-email-domain-as-signal (future). |
| ui-skills | MIT. `fixing-metadata` and `fixing-accessibility` rules directly drove the template SEO/a11y block. Its `baseline-ui` skill mandates Tailwind/React — wrong stack here, skipped. |
| skills (Anthropic) | `webapp-testing`'s Playwright scripts are the best future borrow (verify rendered templates pre-deploy). `frontend-design` is good taste input for template quality. Per-skill licenses (mostly Apache-2.0) — check before vendoring. |
| superpowers | MIT, dev-workflow skills. `verification-before-completion` and `condition-based-waiting` are worth reading; nothing to vendor into a Python pipeline. |
| awesome-agent-skills | A link catalog, zero code. Shopping list only (cold-email, seo-audit, schema-markup entries). |
| firecrawl | Scraper API (API core is AGPL). Overkill dependency for scraping 5-page local-business sites; `lead_agent` + `site_audit` cover the need. Revisit only if lead sourcing moves to serious crawling. |
| lighthouse-ci | Apache-2.0, Node. Running real Lighthouse per lead is heavy; `site_audit.py` implements the 20% that matters for this pitch. Revisit if you want scored PDF audits as a paid product. |
| umami | MIT analytics platform. Self-hosting it is overkill; the pipeline's own click tracker covers the funnel. The idea worth taking later: a tiny visit beacon on preview pages ("they viewed 3 times today" = hot-lead signal). |
| playwright | Already a dependency (screenshots). Nothing else to take. |
| n8n | Workflow platform (fair-code). Wrong tool: this pipeline IS the workflow engine. Reading its AGENTS.md conventions influenced nothing code-level. |
| anthropic-sdk-python | Not yet used. Best single future upgrade: optional Claude drafting/classification (see below). |
| claude-quickstarts, claude-code | Reference material only. |
| qlib, nautilus_trader, Promo-Repo (empty) | Nothing relevant to this pipeline. |

## Next, in order (not started)

1. **Warm up the sending domain and run a real 20-lead batch.** The code
   path is tested; the market isn't. Everything below is worthless until
   real reply-rate data exists. (`WARMUP.md`, then `run.py loop`.)
2. **Preview-visit beacon** (umami's idea, ~30 lines): ping
   `webhook_server` from the preview page; "viewed 3×" → prioritize
   follow-up, surface in dashboard.
3. **Optional Claude drafting** via `anthropic` (soft dependency like
   sendgrid): better drafts + reply classification than Mistral-7B, same
   fallback chain (Claude → HF → deterministic).
4. **Per-niche playbook memory** (hive's queen-memory idea, flat files):
   record which subject/intro variant got replies per niche, feed the
   winner back into drafting. This is the real "working like a mind"
   loop: outcome → memory → next email.
5. **Free-email-domain scoring + duplicate-lead guard** in `lead_agent`.
6. **Lead sourcing** beyond CSVs (the pipeline's actual bottleneck):
   a scraper or purchased lists feeding `leads_inbox/`.

## 2026-07-31: strategic pivot -- machinery for the designer, not just a builder

The above list optimized the cold-outreach funnel. The bigger opportunity
is different: this product's real edge is being the sales/fulfillment
machinery *behind* a local-business web designer, not just another site
builder. Prioritized by what most directly improves sales conversion,
build time, or recurring revenue (see README's per-feature sections for
how each actually works):

1. **Content importer** (`utils/content_importer.py`) -- real logo,
   photos, opening hours, services, reviews, and brand colors scraped from
   a lead's own site, replacing stock photos/a curated generic list/a
   fabricated testimonial wherever real content exists.
2. **QA/readiness scanner** (`utils/site_audit.py`'s `audit_readiness()`)
   -- broken-link + WCAG contrast checks + a 0-100 readiness score,
   run automatically after every deploy and every published edit.
3. **Industry-specific section library** (`templates/_shared/sections.html`
   + `design_agent.NICHE_SECTIONS`) -- a real photo gallery for landscaper,
   menu framing for cafe, an emergency-service banner for plumber, instead
   of one copy-substituted section skeleton for every niche.
4. **Locked client editor** (`agents/editor_agent.py`, `webhook_server.py`'s
   `/edit` routes) -- a magic-link form letting a client edit content
   while layout/colours/typography/nav stay designer-controlled.
5. **Maintenance automation + monthly report** (`maintenance.py`) --
   scheduled re-audits of every sold client's live site, regression
   alerts, and a report built ONLY from real tracked data (no fabricated
   visitor/enquiry analytics this pipeline doesn't actually have).

Also found and fixed along the way: the HMAC token-signing pattern shared
by `compliance.py` (unsubscribe links), `tracker.py` (click tracking), and
the new `editor_auth.py` had a real ~12% verification-failure bug (a
literal `.` separator byte could collide with random mac bytes) -- a CAN-
SPAM risk, not just an editor bug. Fixed by slicing on sha256's fixed
32-byte length instead of a separator.

**Deliberately not built this pass** (lower-leverage per the same
prioritization -- see README's "Client editor" section for the specific
gaps): per-service pricing, staff/team-member content, a full change-
request/approval workflow with history, instant redesign variations,
automated onboarding intake, and audit-as-a-standalone-lead-magnet. Each
is a reasonable next brick on top of what exists now (e.g. redesign
variations is a `variant` parameter through the existing
`render_template_files`/`NICHE_SECTIONS` machinery; audit-as-lead-magnet
reuses `site_audit.audit_html()`'s existing pain-point/improvement output
as a standalone report instead of a silent email-drafting input).

## 2026-07-31 (cont'd): closing the manual-intervention gaps

Every step from "new lead" through "site deployed" already runs
unattended (`main.py`'s 60s loop). Every step from "client wants to buy"
onward still needs a human at a keyboard. Full inventory of what actually
requires a person, ranked by automation value/risk:

1. **No self-serve payment path.** The cold email says "reply YES to
   buy," but nothing turns that into a checkout link automatically --
   `stripe_utils.create_checkout_session` is only ever called from the
   human-typed `payment ready <id>` console command. A buyer who wants to
   pay immediately has no way to.
2. **Editor link is never actually delivered.** `editor_auth.create_editor_link`
   is real and secure, but it's only ever shown in the dashboard or
   printed to the operator's console -- nobody emails it to the client.
   (This was already this repo's own `LEDGER.md` next-brick pointer.)
3. **`transfer` is fully interactive.** Three `input()` prompts (GitHub
   username, Vercel email, y/n on removing our own GitHub access) --
   cannot run unattended, and the underlying data (GitHub username, which
   email to invite) is never collected from the CLIENT, only typed by the
   operator on the spot.
4. **No way to end a takeover.** `sales_agent.end_takeover()` exists but
   no console command ever calls it -- once a lead is manually taken
   over, it stays that way until a process restart wipes the (non-
   persistent) in-memory set. A real gap, not by design.
5. **Lead sourcing is CSV-only.** No automated prospecting -- this is a
   genuine product/data-source decision (paid Places API vs. scraping
   ToS risk), not something to default into unilaterally; documented here
   as the largest remaining gap, deliberately not built.
6. **Reply intent is a blunt 3-way bucket.** positive/negative/
   out_of_office, with every "positive" needing a human read before
   deciding to trigger payment -- lower priority than (1), since a
   self-serve checkout link removes the *need* for that decision on the
   common path entirely rather than making the classifier smarter.

(1) and (2) are pure upside with no new risk (Stripe owns payment
security; emailing a link that already existed is not a new capability).
(3) is the one that actually grants access/moves money on click, so its
automation is config-gated and defaults to today's safer behavior --
see `AUTO_REMOVE_GITHUB_ACCESS` in config.py once built.

**Closed this pass:** (1) self-serve `/buy/<lead_id>` in the cold email,
both follow-ups, and on the preview site itself (README's "Self-serve
checkout"). (2) editor link now actually emailed at `transfer`. (3)
automated onboarding (`agents/onboarding_agent.py`, README's "Automated
onboarding") -- repo creation and GitHub/Vercel invites run automatically
off a client-submitted form; removing our own access stays behind
`AUTO_REMOVE_GITHUB_ACCESS` (default off). `transfer`'s interactive
prompts remain as a manual fallback, not replaced. (4) fixed -- `release
<lead_id>` console command added. (5) and (6) still open, deliberately:
(5) is a real product/data-source decision, not mine to default into;
(6) is lower-leverage now that (1) removes the need for the decision on
the common self-serve path.

The end-to-end loop -- cold email -> self-serve payment -> automated
onboarding -> editor access -- can now run with zero human action for a
buyer who self-serves cleanly. A human is still needed for: anyone who
replies instead of clicking Buy Now (by design -- negotiation), and
actually removing our own GitHub access (opt-in by design). Still
nothing has been run against a real lead; see item 1 in "Next, in order"
above -- that is still the actual next step, not more automation.

## 2026-08-04: BASELINE.md and the first AI-native content step

Wrote `BASELINE.md`: an honest snapshot of what this app still is
underneath the automation shipped above (lead discovery is still
DuckDuckGo scraping, site generation is still Jinja2 templates + curated
copy dicts), and a prioritized plan for a real step-change rather than
more incremental features. It names four paths (validate against reality
first, AI-native content, smarter lead sourcing, productizing) and argues
for sequencing: nothing else is provably worth prioritizing until a real
batch has actually run (still this repo's `LEDGER.md` brick).

`BASELINE.md` names AI-native content generation as the one investment
worth starting on immediately regardless of that real-batch data, since
it's cheap to test incrementally and doesn't require a vendor/business
decision the way lead sourcing does. Shipped the first concrete increment:
`design_agent.hero_tagline()` now drafts a per-lead tagline (real
business name/trade/city/services, not a fixed per-niche template) using
the same HF retrieval-then-generate pattern `sales_agent.py` already uses
for cold-email openings -- optional, falls back to the old
`NICHE_HERO_TEXT` template on any failure. 9 new tests
(`tests/test_design_agent_ai_hero_tagline.py`), all HF calls mocked.

**Deliberately not touched this pass:** the services list and section
selection (`NICHE_SECTIONS`) are still fixed per niche -- the natural next
increments on the same pattern, not done here to keep this change small
and independently measurable. Lead sourcing (`BASELINE.md`'s Path C) and
productizing (Path D) remain untouched, per the same reasoning as before:
real business/vendor decisions, not mine to default into.

## 2026-08-05: PLATFORM.md and extracting the Offer interface

Wrote `PLATFORM.md`: a plan for generalizing this from "sells websites"
to "runs cold-email sales pipelines for whatever gets plugged in" --
explicitly not phone sales; "sales team" means more people running the
same async, email-first takeover flow, never calls. It found that
`sales_agent.py`, `stripe_utils.py`, `webhook_server.py`'s `/buy` +
webhook, and `compliance.py`/`tracker.py` are already offer-agnostic; only
three things actually vary by offer (what gets built, what it costs, how
it's handed over), and those were hardcoded directly into otherwise-
generic call sites.

Shipped step 2 of that plan: `agents/offers/base.py` defines the
three-method contract (`build_artifact`/`price`/`fulfill`, plus
`validate_offer()` so a broken offer module fails at import, not on the
first real lead); `agents/offers/website.py` implements it as thin
delegation to the exact same, unmodified `design_agent.py`/
`onboarding_agent.py`/`config.py` -- no behavior changed, nothing
rewritten. `agents/offers/registry.get_offer(lead)` resolves every lead to
the website offer today (there's only one); it's the one seam a future
`offer_id` column changes without touching any caller. Wired the three
real touch points that were hardcoding website specifics: `main.py`'s
`payment ready` command and `webhook_server.py`'s `/buy` route now price
via `offers.get_offer(lead).price(lead)` instead of relying on
`stripe_utils`'s default; `/onboard`'s POST handler now fulfills via
`offers.get_offer(lead).fulfill(...)` instead of importing
`onboarding_agent` directly. 9 new tests
(`tests/test_offers_interface.py`); 2 existing tests updated for the new
`amount_usd` kwarg. 219 total, up from 210.

**Deliberately not touched:** `design_agent.run()`'s per-lead build loop
(with its retry/attempt-counting) still lives in `design_agent.py` and is
still called directly by `main.py` -- generalizing that loop into
"for lead in researched: get_offer(lead).build_artifact(lead)" is step
3's job once an `offer_id` column exists to dispatch on; doing it now
would mean guessing at retry semantics for offers that don't exist yet.
`sales_agent.py`'s pitch copy was never touched or wrapped in the
interface either -- on inspection it turned out to already be fully
generic (drafts from the lead's own scraped facts, not anything website-
specific), so there was nothing there to extract.
