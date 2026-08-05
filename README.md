# website-designers -- Cold Email Web Design Sales Pipeline

An autonomous pipeline that finds local businesses, audits their existing
website, builds them a free website preview, sends a personalized cold
email (plus a day-3 nudge and day-7 breakup if they don't reply), monitors
replies, and hands off payment/transfer to a human once a lead is ready to
buy.

Previews deploy git-less to Vercel -- **no GitHub repo is created per
preview**. The private hand-off repo is created only at `transfer` time
for a sold site. (If an older version of this pipeline filled your account
with `*-preview-N` repos, `scripts/cleanup_preview_repos.py` lists and,
on explicit confirmation, deletes them.)

See `PLAN.md` for the current improvement plan and the audit of sibling
repos this project borrows ideas from.

Every automated step has a human-in-the-loop safeguard: positive replies
pause outreach for that lead and alert an operator; payment and repo/site
transfer are only ever triggered by an explicit console command.

## Quick Start

1. `pip install -r requirements.txt && playwright install chromium`
2. `cp .env.example .env` and fill in your real keys
3. `cd pipeline && python -m agents.lead_agent ../test_lead.csv && cd ..` to load 3 sample leads
4. `python run.py quick-test` to run one lead through the whole pipeline and watch it work
5. `python run.py loop` (always-on) or `python run.py dashboard` (Streamlit UI) once you're ready

## Directory structure

```
website-designers/
├── pipeline/
│   ├── agents/
│   │   ├── lead_agent.py      # CSV -> enriched, researched leads
│   │   ├── design_agent.py    # template -> deployed preview site
│   │   ├── editor_agent.py    # client editor: edit + republish a lead's site
│   │   ├── onboarding_agent.py # automated post-payment handoff (repo/collaborator invites)
│   │   └── sales_agent.py     # drafting, sending, inbox monitoring
│   ├── utils/
│   │   ├── db.py                 # SQLite schema + queries
│   │   ├── github_api.py         # repo create/push/transfer
│   │   ├── vercel_api.py         # deployment
│   │   ├── email_utils.py        # SMTP send / IMAP poll
│   │   ├── stripe_utils.py       # checkout sessions, webhook verification
│   │   ├── compliance.py         # unsubscribe tokens, CAN-SPAM footer
│   │   ├── tracker.py            # click-tracking links
│   │   ├── editor_auth.py        # DB-backed client editor session tokens (expiry + revocation)
│   │   ├── ssrf_guard.py         # validates a URL's resolved IP before connecting (SSRF hardening)
│   │   ├── content_importer.py   # logo/photos/hours/services/reviews/colors from a lead's own site
│   │   ├── site_audit.py         # prospect-site audit + our-own-site readiness scanner
│   │   └── tracer.py             # trace/agent/tool span logging (VoltAgent + local)
│   ├── config.py               # env loading & validation
│   ├── main.py                 # orchestrator loop + operator console
│   ├── maintenance.py           # scheduled re-audit + monthly report for sold client sites
│   ├── webhook_server.py       # Flask: /click, /unsubscribe, /edit(+request-link), /buy, /onboard, /webhook/stripe
│   └── requirements.txt
├── templates/                   # one subfolder per niche (index.html + style.css)
│   └── _shared/                 # sections.html: reusable Jinja section macros (see "Section library")
├── dashboard.py                 # Streamlit monitoring UI
├── run.py                       # entrypoint: quick-test / loop / dashboard
├── test_lead.csv                # 3 sample leads for a first test run
├── .claude/agents/               # Claude Code subagent personas (see below)
├── .env.example
├── .gitignore
└── README.md
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r pipeline/requirements.txt

cp .env.example .env
# then fill in every value in .env -- see the comments in .env.example
```

Required `.env` variables: `GITHUB_TOKEN`, `VERCEL_TOKEN`, `EMAIL_HOST`,
`EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD`, `STRIPE_SECRET_KEY`,
`STRIPE_WEBHOOK_SECRET`, `ADMIN_EMAIL`, `SENDING_DOMAIN`,
`PHYSICAL_ADDRESS`. `config.py` validates these at startup and fails
loudly, listing everything missing, if any are unset.

`HF_API_TOKEN` (LLM-personalized email openings; a deterministic template
is used without it), `VERCEL_TEAM_ID`, `SLACK_BOT_TOKEN`,
`SENDGRID_API_KEY`/`SENDGRID_FROM_EMAIL`, `UNSPLASH_ACCESS_KEY`,
`VOLTAGENT_PUBLIC_KEY`, `VOLTAGENT_SECRET_KEY` are optional.

## Running it

All commands below are run from the repo root with the venv active.

**Ingest leads from a CSV** (columns: `business_name,niche,location`):

```bash
cd pipeline
python -m agents.lead_agent /path/to/leads.csv
cd ..
```

**Quick single-shot test run** (takes the oldest `'new'` lead through
LeadAgent -> DesignAgent -> SalesAgent once, no loop, no rate limits --
good for a first end-to-end smoke test):

```bash
python pipeline/quick_run.py
```

**Start the orchestrator loop** (researches leads, builds/deploys sites,
sends rate-limited cold emails, polls the inbox, all on a 60s cycle):

```bash
cd pipeline
python main.py
```

While `main.py` is running, type commands at its console:

| Command | Effect |
|---|---|
| `takeover <lead_id>` | Pause automation for a lead, hand negotiation to a human |
| `payment ready <lead_id>` | Create + email a Stripe Checkout link |
| `transfer <lead_id>` | Create the private hand-off GitHub repo (first time one exists for this lead), invite the client to it and to the Vercel project (requires `VERCEL_TEAM_ID`), optionally remove your own GitHub access |
| `status` | Print a lead-count-by-status summary |
| `pause` / `resume` | Pause/resume the automated loop |
| `help` | List commands |
| `quit` | Shut down |

**Start the webhook server** (needed for click tracking, one-click
unsubscribe, and Stripe payment webhooks -- must be publicly reachable at
`PUBLIC_BASE_URL`):

```bash
cd pipeline
python webhook_server.py
```

**Start the dashboard**:

```bash
streamlit run dashboard.py
```

## Templates

`templates/` holds one subfolder per business niche, each with an
`index.html` and a matching `style.css`. `design_agent.py` only builds a
site for a lead if a subfolder matching its `niche` column exists -- add
more niches by adding more subfolders.

Two template styles currently coexist:

- **`landscaper`, `cafe`, `plumber`, `salon`, `electrician`** -- newer,
  Tailwind CSS (via CDN, no build step) single-page designs. Placeholders:
  `{{ business_name }}`, `{{ phone }}`, `{{ hero_headline }}`,
  `{{ services }}` (real service/menu names -- see `niche_services()` --
  rendered via the shared `services_grid` section, see "Section library"
  below), `{{ pain_point_solution }}`, `{{ testimonial }}`,
  `{{ location }}`, `{{ year }}`, and `{{ preview_url }}` (a real, working
  link back to the site's own click-tracked preview URL, shown at the
  bottom as a "share this preview" link).
- **`restaurant`, `gym`, `dentist`** -- original hand-rolled CSS designs
  from the pipeline's first iteration. Placeholders: `{{ business_name }}`,
  `{{ phone }}`, `{{ pain_point_solution }}`, `{{ testimonial }}`,
  `{{ location }}`, `{{ hero_image_url }}`, `{{ year }}`.

`design_agent.py`'s `build_context()` supplies the union of both
placeholder sets to every render, so either style works regardless of
which niche a lead matches -- unused keys are simply ignored by whichever
template doesn't reference them.

`build_context()` also supplies content imported from the lead's own
existing site (`utils/content_importer.py`, populated by `lead_agent.py`
at research time): `{{ logo_url }}`, `{{ photos }}` (a list, `photos[0]` is
also used as `hero_image_url` when present), `{{ hours }}` (a list of
human-readable lines), `{{ brand_colors }}` (a list of hex strings), and
`{{ reviews }}` (a list -- falls back to a single `[testimonial]` entry
when nothing real was scraped). Every one of these is empty/falsy when
nothing was found, so templates guard them with `{% if %}` rather than
assuming they're populated (`templates/default` is the reference
implementation for all five).

## Content importer

`utils/content_importer.py` extracts logo, photos, opening hours,
services, reviews, and brand colors from a lead's existing website during
research (`lead_agent.py`), so `design_agent.py` can put a business's real
identity into its own preview instead of a generic stock photo, curated
service list, and fabricated testimonial. Everything is best-effort and
independently optional -- a thin small-business site still researches
successfully with whatever subset was actually found. See the module
docstring for exactly what each extractor looks for and where it falls
back.

## Section library

`templates/_shared/sections.html` is a Jinja2 macro library (`services_grid`,
`photo_gallery`, `service_area_banner`, `hours_list`) any niche template can
`{% import "sections.html" as sections %}` instead of hand-rolling its own
copy of the same section -- `design_agent.render_template_files()` resolves
templates against the niche's own folder first, then `_shared/`, so the
import works regardless of which niche is rendering. `_shared` itself is
excluded from `available_niches()` (leading underscore), so it can never be
matched as a lead's niche.

Every macro no-ops (renders nothing) when its data is empty, so a template
can call one unconditionally. Which niches opt into which *extra* sections
beyond the universal ones (services, hours) is declared centrally in
`design_agent.py`'s `NICHE_SECTIONS` manifest, exposed to templates as the
`enabled_sections` context list:

```python
NICHE_SECTIONS = {
    "landscaper": ("photo_gallery", "hours_list"),
    "cafe": ("hours_list",),
    "plumber": ("service_area_emergency", "hours_list"),
}
```

`landscaper` shows a "Recent Work" gallery of real scraped photos (never
fabricated before/after labels -- generic scraping can't truthfully tell
which photo is which), `cafe` frames its services section as "Menu", and
`plumber` adds a "Service Area" section with a 24/7 emergency badge. Extend
an existing niche's template (or add a new one) to another shared section
by adding the macro call and a `NICHE_SECTIONS` entry -- `templates/default`
is the reference implementation for `services_grid`/`hours_list` used
unconditionally (no manifest entry needed; they're inherent to that
template, not opt-in extras).

## Client editor

Once a lead has a site (preview or transferred), `utils/editor_auth.create_editor_link(lead_id)`
builds a magic-link URL (surfaced in the dashboard's Lead detail panel, and
printed by `main.py`'s `transfer` command) to `GET /edit/<lead_id>?token=...`
-- a form where the client can edit **text, photos, opening hours,
services/menu items, reviews, and their logo**. Spacing, typography, colour
hierarchy, mobile layout, navigation structure, and component styling have
no field in the form at all, so they can't be touched this way; `publish()`
only ever re-renders the SAME niche template the lead already has. The
business's name is also locked -- it's baked into the lead's stable Vercel
project name and GitHub repo name (`github_api.make_repo_name`), so editing
it would silently redeploy to a brand-new project instead of updating the
existing one.

**Session security.** Unlike the stateless HMAC links `utils/compliance.py`
(unsubscribe) and `utils/tracker.py` (click tracking) use, editor links are
DB-backed sessions (`utils/editor_auth.py`, `db.editor_sessions`): each has
an `expires_at` (30 days by default), can be individually `revoked_at`
without touching any other lead's link or rotating the whole app
`SECRET_KEY`, and only the session's SHA-256 hash is ever stored. A lost or
expired link is recovered via `GET /edit/<lead_id>/request-link`, which
emails a fresh one to the lead's **on-file** `contact_email` -- it never
returns the link in the HTTP response itself and always shows the same
"if that email is on file..." message regardless of whether it matched, so
the endpoint can't be used to enumerate valid emails or fetch a working
link without controlling that inbox.

**URL fields are SSRF-guarded.** `logo_url` and `photos` are submitted
directly by the client, so `utils/ssrf_guard.py` resolves and validates
each URL (rejecting loopback/link-local/private/reserved/multicast
addresses, including the `169.254.169.254` cloud-metadata address) both
when the edit is submitted (`editor_agent.apply_edit`) and again wherever
the readiness scanner's broken-link crawl visits URLs found in the
rendered HTML (`site_audit.check_broken_links`) -- redirects are
re-validated at every hop too. See that module's docstring for the one
documented residual gap (DNS-rebinding) it does not close.

Edits are stored as `site_edits` rows (one per field, latest value wins --
see `utils/db.py`), layered on top of the lead's original content by
`agents/editor_agent.py`'s `effective_lead()` at render time -- so
`design_agent.build_context()` and `content_importer.load_content()` need
no changes at all to support edited content. A submission validates every
field before persisting any of them (`editor_agent.apply_edits`) -- a
single bad field (e.g. a blocked photo URL) never leaves a partial,
confusing save behind.

**Publishing and handoff ownership.** Submitting the form calls
`publish()`, which redeploys to the same Vercel project and, if a GitHub
hand-off repo exists but hasn't been fully transferred yet, also updates
the files there. Once `website.transferred` is set -- the operator
explicitly removed their own GitHub access as part of `transfer` --
`publish()` refuses outright rather than silently redeploying Vercel only:
that would leave Vercel ahead of the GitHub repo the client now believes
IS their website, with no way to tell which copy is "real." This is a
**"true handoff"** model: self-service editing through this tool stops at
full transfer. Two alternative architectures, not built here: a
**fully-managed** model (never remove GitHub access, keep editing working
forever) or a **client-connected editor** (commit edits to the client's
own repo first, let Vercel deploy from that commit via Git integration
instead of the current git-less deploy-without-Git flow) -- the latter is
the strongest long-term direction if continuous self-service editing after
a complete handoff is wanted, but it's a real rework of `vercel_api.py`'s
deploy mechanism, not a small change.

Every publish also re-runs the readiness scanner (see "QA/readiness
scanner" below) against the newly rendered HTML.

**Known MVP limits, deliberately not built yet:** no per-service pricing
field (the data model has no such column), no staff/team-member content
type, and edit history is overwrite-only (each field's previous value isn't
kept once replaced) -- a full change-request/approval workflow with
history and screenshots is a natural next step, not required for the core
"client can safely self-edit content" loop this ships today.

## Self-serve checkout

`GET /buy/<lead_id>` (`webhook_server.py`) creates a **fresh** Stripe
Checkout Session on every click and redirects to it -- never a pre-
generated URL embedded in an email, since Checkout Sessions expire (24
hours by default) and a cold email or follow-up might be opened days
later. This link is in the cold email, both follow-ups, and as a "Get
This Website" button on the preview site itself (`design_agent.py`'s
`buy_url` context key), so a buyer can pay with **zero human action on
our side** -- `agents/sales_agent.py`'s "reply YES" path and `main.py`'s
operator-triggered `payment ready <lead_id>` command both still work
unchanged for a lead who'd rather negotiate first. Either path ends at
the same place: the existing `checkout.session.completed` webhook handler,
unmodified, sets the lead to `won`. We never charge anyone without their
own action on Stripe's hosted page -- self-serve only removes the step of
a human *generating* the checkout link, not any part of collecting
payment itself.

## Automated onboarding

The moment a Stripe payment completes, `webhook_server.py`'s
`/webhook/stripe` handler now also calls
`agents/onboarding_agent.send_onboarding_email()`, which emails the client
a link to `GET /onboard/<lead_id>` -- the same DB-backed session mechanism
as the content editor (`utils/editor_auth.create_onboarding_link`, since a
valid session proves the same thing regardless of which form it lands on).
That form collects an optional GitHub username and a confirm-or-override
email for Vercel access; submitting it runs
`onboarding_agent.complete_onboarding()`, which:

1. Creates the hand-off repo if one doesn't exist yet (idempotent, reuses
   `design_agent.create_handoff_repo`).
2. Invites the GitHub username (if given) as a repo collaborator.
3. Invites the Vercel email (if given and `VERCEL_TEAM_ID` is configured)
   to the Vercel project.
4. Emails the client their editor link (`editor_agent.send_editor_link`).

Every step is independently best-effort -- a typo'd GitHub username still
gets a Vercel invite and an editor link, not a hard failure. Steps 1-3 are
safe to run unconditionally because GitHub/Vercel collaborator invites are
both **invite-acceptance flows the invitee must approve**, not unilateral
access grants.

**The one step that stays opt-in:** removing OUR OWN GitHub access --
completing a fully hands-off transfer with zero operator involvement --
only happens when `AUTO_REMOVE_GITHUB_ACCESS=true` is set in `.env`
(`config.py`). That default is `false` on purpose: granting a client
access is low-risk and reversible (they just don't accept the invite);
permanently giving up our own access is not, so it stays behind an
explicit decision rather than silently replacing today's safer behavior
(the operator confirming via `main.py`'s `transfer` console command, which
remains fully available throughout and converges on the exact same
idempotent primitives -- nothing breaks if a client never touches the
onboarding form and the operator just runs `transfer` instead, or does
both).

## AI-generated hero copy

Every lead in a niche used to get the exact same hero tagline
(`NICHE_HERO_TEXT`, e.g. every cafe: "Your daily cup in {city}") --
identical copy is one of the clearest tells that a site is templated, not
built for the specific business. `design_agent.hero_tagline()` now drafts
a per-lead tagline instead, grounded in that lead's own facts (business
name, trade, city, real services, any recognized specialty -- same
retrieval-then-generate split `sales_agent.py` uses for cold-email
openings, so the model writes FROM real facts instead of inventing them).
Optional and fails soft: falls back to the deterministic `NICHE_HERO_TEXT`
template when `HF_API_TOKEN` is unset, the API call fails, or the model's
response doesn't parse into a short, clean line -- a bad API day never
blocks a deploy or renders broken copy on a live preview.

This is the first concrete step on `BASELINE.md`'s "Path B" (AI-native
content generation) -- deliberately scoped to just the hero tagline first
("start small, measure, expand") rather than rewriting the whole template
system in one pass. The natural next increments are the services list and
section selection.

## Offer interface

`sales_agent.py` (drafting, sending, follow-ups, reply classification,
takeover), `stripe_utils.py`/`webhook_server.py`'s `/buy` + Stripe
webhook, and `compliance.py`/`tracker.py` were already offer-agnostic --
none of them know or care that what's being sold is a website. Only three
things actually varied by offer: what gets built (`design_agent.
process_lead`), what it costs (`config.WEBSITE_PRICE_USD`), and how it's
handed over (`onboarding_agent.complete_onboarding`) -- and those were
hardcoded directly into the generic call sites (`main.py`'s
`payment ready` command, `webhook_server.py`'s `/buy` and `/onboard`
routes).

`agents/offers/` formalizes that three-method contract
(`build_artifact`/`price`/`fulfill`, see `offers/base.py`) and
`agents/offers/website.py` implements it as thin delegation to the exact
same, unmodified `design_agent.py`/`onboarding_agent.py`/`config.py` --
no behavior changed, no new capability, only where the call sites look
up "which offer." `agents/offers/registry.get_offer(lead)` resolves every
lead to the website offer today (there's only one); it's the one seam a
future `offer_id` column would change without touching any of its
callers. See `PLATFORM.md` for the full plan this is step 2 of.

## QA/readiness scanner

`utils/site_audit.py` has two distinct entry points for two distinct
audiences: `audit_html()`/`audit_url()` audit a **lead's old site**
(pre-sale personalization, unchanged from before); `audit_readiness()`
audits **our own generated preview or live site** as a pre-publish gate --
`design_agent.process_lead()` runs it automatically right after every
deploy (and `editor_agent.publish()` runs it again after every edit),
persisting the result to the `site_audits` table (`audited_target`:
`prospect_site` / `generated_preview` / `live_client_site`). It adds two
checks the prospect-site audit deliberately doesn't do: an SSRF-guarded
broken-link/broken-image crawl (capped at 15) and a WCAG AA (4.5:1)
color-contrast check on inline style pairs.

**Say "Basic publishing checks: 89% (8/9 performed)", never "WCAG
compliant" or "all links verified."** `readiness_pct` is the percentage of
checks *actually performed* that passed -- a skipped check (e.g. the
broken-link crawl, which `maintenance.py`'s daily pass runs with
`check_links=False` to keep it to one request per site) is neither a pass
nor a fail and is excluded from both the numerator and denominator
(`checks_performed`/`checks_total`/`checks_skipped` make this explicit),
never silently counted as a pass -- that bug used to make deploy-time and
maintenance-time scores incomparable. The scanner also covers real but
partial ground: contrast only sees colors declared in an inline `style=`
attribute (not CSS classes, stylesheets, variables, gradients, or text
over a photo), and the link crawl only checks the first 15 links/images on
the one page it was given. The dashboard's leads table and Lead detail
panel, and `maintenance.py`'s monthly report, all use this exact wording.

## Compliance

Every outbound email is routed through `utils/compliance.py` and always
carries a physical mailing address, a one-click unsubscribe link, and a
`List-Unsubscribe` header, per CAN-SPAM. Rate limits (120-300s between
sends, 20/hour, 50/day) are enforced in `agents/sales_agent.py` and apply
to follow-ups too. The sequence is capped at three touches total (initial,
~day-3 nudge, ~day-7 breakup -- `FOLLOWUP_GAPS_DAYS` in `config.py`); any
reply, positive or negative, stops it immediately. A real
domain warm-up tool is still recommended before high-volume sending on a
brand-new sending domain -- this pipeline staggers send timing but does
not warm up domain reputation for you.

## Observability (VoltAgent)

Every meaningful agent action (lead research, site design/deploy, cold
email send, reply classification) is wrapped in a `trace -> agent -> tool`
span by `pipeline/utils/tracer.py`. A local trace log at
`pipeline/traces.json` is **always** written -- it's what the dashboard's
"Agent trace history" section reads from, and it works with zero
configuration.

If `VOLTAGENT_PUBLIC_KEY` and `VOLTAGENT_SECRET_KEY` are also set in
`.env`, every span is additionally mirrored to
[VoltAgent Cloud](https://voltagent.dev) via the real, async
[`voltagent`](https://pypi.org/project/voltagent/) Python SDK
(`sdk.trace()` as an async context manager, `trace.add_agent()` /
`agent.add_tool()` as async calls, `tool.success()` / `.error()` to close
a span). That SDK is write-only -- there's no endpoint to list or fetch
traces back out of it -- so cloud mode is additive observability in
VoltAgent's own hosted UI, not a replacement for the local file the
dashboard reads. Cloud mirroring failures (bad credentials, network
issues) are caught and logged; they never block or fail the underlying
pipeline operation.

## Subagents (`.claude/agents/`)

This repo ships project-scoped [Claude Code subagent](https://docs.claude.com/en/docs/claude-code)
persona files under `.claude/agents/`, one per pipeline concern
(cold-email drafting, compliance, GitHub/Vercel infra, Stripe, human
takeover, lead research). Only
`.claude/agents/01-core-development/backend-developer.md` is reproduced
verbatim from the real
[VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)
collection, under the MIT License, Copyright (c) 2025 VoltAgent (full
text in `.claude/agents/LICENSE-voltagent-subagents`). The rest are
custom-authored for this project in the same style/frontmatter
convention -- the roles this pipeline needed (e.g. `stripe-checkout`,
`human-takeover`) don't have upstream analogs in that collection. See
`.claude/agents/README.md` for the full per-file provenance breakdown.

## Domain warm-up

Before sending real cold email at volume, read `WARMUP.md` -- it covers
SPF/DKIM/DMARC setup, a manual or tool-assisted (Mailwarm etc.) warm-up
ramp, and reputation monitoring. The pipeline's `EMAIL_MAX_PER_HOUR=20` /
`EMAIL_MAX_PER_DAY=50` caps (`pipeline/config.py`) stop the *pipeline*
from sending too fast; they don't substitute for actually warming up a
new domain first.

## Testing

`tests/test_pipeline_real.py` is an opt-in, end-to-end integration test
that exercises the real pipeline against real GitHub/Vercel/SMTP/IMAP
APIs -- it creates a real (throwaway) private repo and Vercel deployment,
sends a real email to the sending mailbox itself and confirms it arrives
via IMAP, and checks a trace was recorded, then deletes everything it
created. It's skipped by default (a plain `pytest` run never touches
external services):

```bash
pip install -r pipeline/requirements.txt   # includes pytest
cp .env.test.example .env.test
# fill in real, test-safe credentials in .env.test (see its comments)
pytest --run-real tests/test_pipeline_real.py -v -s
```

If `.env.test` doesn't exist, `tests/conftest.py` falls back to your real
`.env` rather than skipping -- but always forces `DB_PATH`/`TRACES_PATH`
to throwaway `pipeline/leads.test.db` / `traces.test.json` regardless of
what `.env` specifies, so even a fallback run can't write into your real
`leads.db`. Create `.env.test` if you want explicit control instead.

## Scheduler / keeping it running

`main.py` already contains its own infinite work loop -- once started, it
keeps running and doesn't need to be re-invoked. Two ways to keep the
*process itself* alive:

- **`pipeline/scheduler.py`** (recommended fallback): `python
  scheduler.py` runs as a foreground supervisor that restarts `main.py` if
  it ever crashes. Run it inside tmux/screen exactly like you'd run
  `main.py` directly (see "Deployment" below).
- **cron, via `crontab.example`**: for hosts without a way to keep a
  long-lived session running, `scheduler.py --check` can be invoked every
  few minutes by cron -- it starts `main.py` in the background only if
  it isn't already running (PID-file guarded), acting as a crash-recovery
  net rather than a scheduled re-run. **Limitation:** `main.py` started
  this way has no attached terminal, so the human-in-the-loop console
  commands (`takeover`, `payment ready`, `transfer`) aren't usable against
  it -- attach a real terminal session (tmux/screen) when you need to run
  one of those.

## Deployment

### A cheap VPS with tmux/screen

The simplest way to run this pipeline continuously is a small VPS
(Hetzner CX22, DigitalOcean's cheapest droplet, etc. -- this pipeline is
lightweight; the smallest tier is plenty) kept alive with `tmux` or
`screen`, which lets you disconnect your SSH session while `main.py` keeps
running *and* stay able to reconnect and type operator commands
(`takeover`, `payment ready`, `transfer`) later.

```bash
# On the VPS, after cloning the repo and completing Setup above:
sudo apt update && sudo apt install -y tmux   # or screen

tmux new -s pipeline
cd website-designers/pipeline
source ../venv/bin/activate
python main.py
# Ctrl-B then D to detach -- main.py keeps running.

# Reconnect later (e.g. to type "takeover 12" when a lead replies):
tmux attach -t pipeline
```

Run `webhook_server.py` the same way, in a second tmux window/pane
(`Ctrl-B C` for a new window) or as a separate `tmux new -s webhook`
session -- it needs to keep running to receive Stripe webhooks and serve
click-tracking/unsubscribe links. For a more production-grade setup,
front it with `gunicorn` and a real reverse proxy (nginx/Caddy) with TLS,
since Stripe requires HTTPS webhook endpoints.

`scheduler.py` (see above) can wrap either of these tmux/screen sessions
for automatic restart-on-crash without changing this workflow.

### ngrok for Stripe webhooks during development

Stripe needs a publicly reachable HTTPS URL to deliver webhooks to, which
`localhost:5000` isn't during local development. [ngrok](https://ngrok.com)
gives you one instantly:

```bash
# In one terminal:
cd pipeline && python webhook_server.py   # serves on localhost:5000

# In another:
ngrok http 5000
# ngrok prints a public URL like https://abcd1234.ngrok-free.app
```

Then:
1. Set `PUBLIC_BASE_URL=https://abcd1234.ngrok-free.app` in `.env` (used
   to build unsubscribe/click-tracking links and Stripe success/cancel
   URLs) and restart `main.py`/`webhook_server.py` so it's picked up.
2. In the [Stripe Dashboard](https://dashboard.stripe.com/test/webhooks),
   add an endpoint pointing at `https://abcd1234.ngrok-free.app/webhook/stripe`
   listening for `checkout.session.completed`, and copy its signing
   secret into `STRIPE_WEBHOOK_SECRET` in `.env`.
3. ngrok's free tier issues a new random URL every time it restarts --
   update both places above whenever that happens. A paid ngrok plan (or
   moving to a real domain once you're past development) gives you a
   stable URL instead.

### Custom domain for click tracking (optional)

By default, click-tracking and unsubscribe links point at whatever
`PUBLIC_BASE_URL` is set to (`http://localhost:5000` out of the box). For
production, point a subdomain like `track.yourdesignco.com` at the VPS
running `webhook_server.py`:

1. Add an `A` record: `track.yourdesignco.com` -> your VPS's IP.
2. Put `webhook_server.py` behind a reverse proxy with TLS -- e.g. Caddy,
   which auto-provisions Let's Encrypt certificates:
   ```
   # /etc/caddy/Caddyfile
   track.yourdesignco.com {
       reverse_proxy localhost:5000
   }
   ```
3. Set `PUBLIC_BASE_URL=https://track.yourdesignco.com` in `.env` and
   restart the pipeline.
4. Update the Stripe webhook endpoint (see above) to the new URL too.

This is optional -- the pipeline works fine on the raw VPS IP or an ngrok
URL for testing; a dedicated tracking subdomain mainly matters for a
cleaner-looking link in the emails you actually send to prospects.
