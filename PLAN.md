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
