# website-designers -- Cold Email Web Design Sales Pipeline

An autonomous pipeline that finds local businesses, builds them a free
website preview, sends a personalized cold email, monitors replies, then
negotiates, takes payment, and hands over the finished site -- end to end,
with no human in the loop for the happy path.

Safety comes from code-enforced guardrails, not a human gate: negotiated
prices are clamped to a configured band and can never rise above what was
quoted; the LLM only proposes moves while code computes every price, link,
and Stripe amount; a per-lead round cap stops runaway back-and-forth and
alerts a human. Payment (Stripe) fires automatically on a close, and the
GitHub/Vercel handover fires automatically once payment clears. A human is
alerted -- never blocked -- for the exceptions (round cap hit, paid-customer
support, failed invite); the manual `transfer` command remains as an
override and is the only path that removes your own repo access.

The Agency room operating docs are in `docs/AGENCY_SALES_SYSTEM.md` and
`docs/CLIENT_PROPOSAL_AND_TERMS.md`. They define the first sellable offer,
lead-scoring rules, outreach flow, proposal/payment language, and go-live
checklist the StarNet agents use when running this repo. StarNet skill-to-agent wiring lives in `docs/STARNET_SKILL_WIRING.md`; it binds the enabled design skills to the Agency room without authorising live sourcing, sending, or money mode changes.

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
│   │   ├── sourcing_agent.py  # Google Places -> new leads (opt-in)
│   │   ├── design_agent.py    # template -> deployed preview site
│   │   └── sales_agent.py     # drafting, sending, inbox monitoring
│   ├── utils/
│   │   ├── db.py              # SQLite schema + queries
│   │   ├── github_api.py      # repo create/push/transfer
│   │   ├── vercel_api.py      # deployment
│   │   ├── email_utils.py     # SMTP send / IMAP poll
│   │   ├── stripe_utils.py    # checkout sessions, webhook verification
│   │   ├── compliance.py      # unsubscribe tokens, CAN-SPAM footer
│   │   ├── tracker.py         # click-tracking links
│   │   ├── tracer.py          # trace/agent/tool span logging (VoltAgent + local)
│   │   └── teardown.py        # expire previews past PREVIEW_TTL_DAYS
│   ├── config.py               # env loading & validation
│   ├── main.py                 # orchestrator loop + operator console
│   ├── cleanup_tests.py        # delete throwaway test leads + their repos/projects
│   ├── source_leads.py         # one-shot lead sourcing CLI (run.py source)
│   ├── webhook_server.py       # Flask: /click, /unsubscribe, /webhook/stripe
│   └── requirements.txt
├── templates/                   # one subfolder per niche (index.html + style.css)
├── dashboard.py                 # Streamlit monitoring UI
├── run.py                       # entrypoint: quick-test / loop / dashboard / test-email / cleanup-tests / source / rebuild
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
`EMAIL_PORT`, `EMAIL_USER`, `EMAIL_PASSWORD`, `HF_API_TOKEN`,
`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `ADMIN_EMAIL`,
`SENDING_DOMAIN`, `PHYSICAL_ADDRESS`. `config.py` validates these at
startup and fails loudly, listing everything missing, if any are unset.

`VERCEL_TEAM_ID`, `SLACK_BOT_TOKEN`, `UNSPLASH_ACCESS_KEY`,
`VOLTAGENT_PUBLIC_KEY`, `VOLTAGENT_SECRET_KEY` are optional, as are
`EMAIL_ACCOUNTS` / `EMAIL_MAX_PER_DAY` / `EMAIL_MAX_PER_DAY_PER_ACCOUNT`
(see "Multiple sending mailboxes" below).

## Running it

All commands below are run from the repo root with the venv active.

**Ingest leads from a CSV** (columns: `business_name,niche,location`):

```bash
cd pipeline
python -m agents.lead_agent /path/to/leads.csv
cd ..
```

**Lead sourcing from Google Places** (optional). Instead of typing leads
in, let the pipeline find them: for every `SOURCING_NICHES` x
`SOURCING_LOCATIONS` combination it runs a Places API (New) text search
and inserts businesses that are OPERATIONAL. By default it still requires a
listed website (`SOURCING_REQUIRE_WEBSITE=true`), because owned websites are
the safest route for LeadAgent to find a contact email. If you deliberately
set `SOURCING_REQUIRE_WEBSITE=false`, sourcing may also keep businesses with
no site or only a platform/directory presence; those are scored as weak-web
leads and marked for phone/platform follow-up rather than assumed emailable.
Results are deduped on the Google place id (plus a name+location fallback
for hand-typed leads) and capped at `SOURCING_DAILY_LIMIT` per day.


Businesses within `SOURCING_EXCLUDE_RADIUS_MILES` (default 6) of
`SOURCING_EXCLUDE_CENTER` (default Oxted) are skipped, so nobody on your
own doorstep gets a cold email while the pitch is still being refined; the
default `SOURCING_LOCATIONS` are all further out than that.
```bash
python run.py source --dry-run      # print what would be inserted, write nothing
python run.py source --limit 10     # insert up to 10 leads now
```

Needs `GOOGLE_PLACES_API_KEY` in `.env` (with "Places API (New)" enabled
on the key). The orchestrator loop runs the same step once an hour when
`SOURCING_ENABLED=true` -- it's off by default so the lead list, and with
it your outbound email volume, never grows without you opting in.

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

Negotiation, payment, and handover are fully automated: a positive reply
routes into the LLM negotiation agent (price-clamped in code to
`NEGOTIATION_FLOOR`..`NEGOTIATION_CEILING`, in `CURRENCY`), a close automatically
creates + emails the Stripe Checkout link, and once Stripe's webhook marks
the lead paid the loop sends the GitHub/Vercel invites itself. The console
commands that remain are optional conveniences:

| Command | Effect |
|---|---|
| `transfer <lead_id>` | Manual handover override (e.g. the client never sent a usable GitHub username), and the only way to remove your own GitHub access |
| `status` | Print a lead-count-by-status summary |
| `pause` / `resume` | Pause/resume the automated loop |
| `help` | List commands |
| `quit` | Shut down |

**Preview expiry.** The cold email promises the preview is "live for 7
days". `main.py` makes that true: at most once an hour it deletes the
Vercel project and GitHub repo of any preview older than
`PREVIEW_TTL_DAYS` (default 7) whose lead is `emailed` with no reply,
`lost`, `bounced` or `unsubscribed`. Leads in `replied` / `negotiating` /
`payment_sent` / `won` (and not-yet-emailed `designed` leads) are never
touched, and the lead's status doesn't change -- only
`websites.torn_down_at` is set and a state-history note is added. Set
`PREVIEW_TEARDOWN_ENABLED=false` to turn it off, or run it by hand:

```bash
python pipeline/utils/teardown.py --dry-run   # list what would be removed
python pipeline/utils/teardown.py             # remove it
```

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

Every lead renders through **`templates/modern/`** by default: one photo-led,
Tailwind (CDN, no build step) design, themed per niche from
`NICHE_THEMES` in `pipeline/agents/design_agent.py`. A theme supplies the
*base* accent pair -- which `design_agent.accent_pair()` then rotates
deterministically from the business name, so two plumbers in one town never
get identical colours -- a hand-picked hero and gallery photo, the tagline, an
"about" paragraph, six services with one-line blurbs, and the section
headings and calls to action (a cafe says "What we serve" and "Find us", a
plumber says "What we do" and "Get a quote"). Adding a niche is adding one
dict entry, not a folder.

The page is built from what is actually known about the business and hides
anything that isn't: phone and call buttons, address and the embedded Google
map, the Google rating (only once it has 10+ reviews), a testimonial (only
one scraped from the lead's own site, never invented). There is no
placeholder text anywhere. Leads sourced from Google Places arrive with
phone, address, rating and review count already filled in.

Check a change by eye without deploying anything:

```bash
python pipeline/render_preview.py            # all niches, sample data -> pipeline/out/*.png
python pipeline/render_preview.py --lead 81  # a real lead from the DB
```

**Client photos and logo.** Every cold email offers to swap in the prospect's
own photos and logo at no extra cost. Images attached to any reply are saved
to `pipeline/assets/<lead_id>/` (normalised: logo as PNG/SVG, photos to
1600 px JPEG, up to six), the preview is rebuilt in place at the same URL
with the logo in the nav and their photos in the hero and gallery, and a
confirmation reply goes out. Files that arrive another way go in the same
folder, then `python run.py rebuild <lead_id>`. How to handle the
conversation itself is in [`docs/PHOTOS_AND_LOGO_PLAYBOOK.md`](docs/PHOTOS_AND_LOGO_PLAYBOOK.md).

The older per-niche folders (`cafe`, `default`, `dentist`, ...) are kept and
used when `DESIGN_TEMPLATE_STYLE=legacy`; they are text-only and the
placeholders (`{{ business_name }}`, `{{ phone }}`, ...) are documented in
the files themselves.

## Compliance

Every outbound email is routed through `utils/compliance.py` and always
carries a physical mailing address, a one-click unsubscribe link, and a
`List-Unsubscribe` header, per CAN-SPAM. Rate limits (120-300s between
sends, 20/hour, 50/day) are enforced in `agents/sales_agent.py`. A real
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
(cold-email drafting, compliance, GitHub/Vercel infra, Stripe, autonomy
guardrails, lead research). Only
`.claude/agents/01-core-development/backend-developer.md` is reproduced
verbatim from the real
[VoltAgent/awesome-claude-code-subagents](https://github.com/VoltAgent/awesome-claude-code-subagents)
collection, under the MIT License, Copyright (c) 2025 VoltAgent (full
text in `.claude/agents/LICENSE-voltagent-subagents`). The rest are
custom-authored for this project in the same style/frontmatter
convention -- the roles this pipeline needed (e.g. `stripe-checkout`,
`autonomy-guardrails`) don't have upstream analogs in that collection. See
`.claude/agents/README.md` for the full per-file provenance breakdown.

StarNet-side web-design skills are wired separately in `docs/STARNET_SKILL_WIRING.md`. In short: `Make a Plan` gates non-trivial repo work, `Creative Ideation` feeds bounded offer/design concepts, `Popular Web Designs` creates prototype directions only, and `ASCII Art` is internal operator/docs sugar, not production UI unless explicitly requested.

## Domain warm-up

Before sending real cold email at volume, read `WARMUP.md` -- it covers
SPF/DKIM/DMARC setup, a manual or tool-assisted (Mailwarm etc.) warm-up
ramp, and reputation monitoring. The pipeline's `EMAIL_MAX_PER_HOUR=20` /
`EMAIL_MAX_PER_DAY=50` caps (`pipeline/config.py`) stop the *pipeline*
from sending too fast; they don't substitute for actually warming up a
new domain first.

## Multiple sending mailboxes

A single mailbox is good for roughly 25 cold emails a day before inbox
placement starts to slide, whatever the pipeline's caps allow. Sending
more means more mailboxes -- ideally spread over a few domains, so one
domain's reputation dip doesn't take the whole pipeline down with it.

`EMAIL_USER` / `EMAIL_PASSWORD` is always mailbox 0. Add the rest in
`.env` as `EMAIL_ACCOUNTS`, `;`-separated:

```
EMAIL_ACCOUNTS=casey@domain-two.com:app-pw;casey@domain-three.com:app-pw:smtp.zoho.eu:imap.zoho.eu
EMAIL_MAX_PER_DAY_PER_ACCOUNT=25
EMAIL_MAX_PER_DAY=75
```

Each entry is `user:password` (SMTP/IMAP hosts inherited from `EMAIL_HOST`
/ `EMAIL_IMAP_HOST`) or `user:password:smtp_host:imap_host`. A password
that contains `:` needs the four-field form. `EMAIL_MAX_PER_DAY_PER_ACCOUNT`
caps each mailbox; `EMAIL_MAX_PER_DAY` stays the global total across all of
them. With `EMAIL_ACCOUNTS` unset nothing changes: one mailbox, same caps.

How it behaves (`pipeline/utils/mailboxes.py`):

- A new lead's cold email goes out from the mailbox with the fewest sends
  in the last 24h that is still under its per-mailbox cap (ties rotate).
  When every mailbox is at cap, cold sending pauses until one frees up.
- Every later email to that lead -- negotiation replies, the payment link,
  the goodbye, the handover -- leaves from the *same* mailbox (stored in
  `leads.sender_account`), so the thread stays in one inbox and the From
  address never changes mid-conversation.
- Reply polling covers every mailbox; a mailbox whose login fails is
  reported and skipped rather than blocking the others.

The cheap way to add mailboxes is a Zoho Mail Lite mailbox on each extra
domain (about $1/user/month): register the domain, set up SPF/DKIM/DMARC as
in `WARMUP.md`, create an app password, add the mailbox to `EMAIL_ACCOUNTS`.
Warm each new mailbox up like a new domain -- rotation spreads the load, it
doesn't build reputation.

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

**Cleaning up after manual test runs.** `python run.py test-email ...`
and `quick-test` create real leads ("Test Business" / "Acme Cafe", or
anything emailed to `ADMIN_EMAIL`) plus a real GitHub repo and Vercel
project each, and nothing removes them. To clear them all out:

```bash
python run.py cleanup-tests --dry-run   # list the test leads that would go
python run.py cleanup-tests             # delete their repos, projects and DB rows
```

Matching is by the test business names, a `Testville` location, or a
contact email equal to `ADMIN_EMAIL`. When the whole database is pre-launch
test data (the state before the first real batch), reset it in two
retry-safe steps instead of picking rows:

```bash
python pipeline/utils/teardown.py --all --dry-run   # every deployed preview, any age/status
python pipeline/utils/teardown.py --all             # delete those Vercel projects + GitHub repos
python run.py cleanup-tests --reset --dry-run
python run.py cleanup-tests --reset                 # archive leads.db + traces.json to pipeline/archive/, start empty
```

`--reset` refuses if anything looks real (an unsubscribed address, a paid
lead, a site handed to a client) or if any preview is still deployed, so
nothing remote is orphaned.

**Deleting GitHub repos needs the `delete_repo` scope on `GITHUB_TOKEN`.** A
token with only `repo` gets a 403 worded "Must have admin rights to
Repository" even on your own repos. The teardown treats that as a token
problem rather than a failure: the Vercel project is deleted either way (so
the preview URL is dead, which is what the 7-day promise is about), the row
is recorded as torn down, and the repos that survived are listed in
`pipeline/archive/orphan-repos-<stamp>.txt`. To clear them afterwards, add
the scope at github.com/settings/tokens (editing a classic token's scopes
keeps the same token value, so `.env` needs no change) and run:

```bash
python pipeline/utils/teardown.py --repos-from pipeline/archive/orphan-repos-<stamp>.txt
```

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
  this way has no attached terminal, so the optional console commands
  (`transfer`, `status`, `pause`/`resume`) aren't usable against it --
  attach a real terminal session (tmux/screen) when you need one. The
  sell/negotiate/close/handover flow itself is fully automated and needs
  no terminal.

## Go-live checklist

Work through these in order the first time you switch from dry runs to real
sending. Each one is cheap; skipping them is how domains get burned.

Start with the repo's read-only preflight check:

```bash
python run.py preflight            # probes the public URL and logs in to the mailbox
python run.py preflight --offline  # config, database and copy checks only
python run.py control              # owner, next action, age and stalled reason for every open lead
python run.py control --json       # the same board for agents/automation
```

It prints one line per check and ends with `NO-GO` (a blocker), `READY (dry
run)` or `GO (ARMED)`. Checks: required config, postal address, sender
domain, the live-send and sourcing switches, `PUBLIC_BASE_URL` set *and
answering* `/health`, Stripe key mode and webhook secret, SMTP+IMAP login,
daily caps against the week-one limit, the cold email's price/guarantee/
address/unsubscribe link, test leads still in the database, the send queue,
render freshness and leftover test artefacts. Several checks are warnings
while `ENABLE_LIVE_SEND=false` and become blockers once it is true, so run it
again right after flipping the switch. To read the exact email a lead would
get without sending anything:

```bash
python run.py preview-email             # built-in sample lead
python run.py preview-email --lead 81   # a real lead, with its real preview URL
```

**Alerts.** A positive reply, a payment and a negotiation round-cap all raise a
Slack alert, and a failed post is swallowed with one console line, so a wrong
`SLACK_BOT_TOKEN` fails silently. Preflight checks the token; only a real post
proves the channel is reachable:

```bash
python run.py test-alert
```

**Agent control board.** `python run.py control` is the read-only operating
queue shared by the room. It maps each live lead state to one accountable
StarNet agent, names the next action, shows the age of the current stage, and
sorts inconsistent, due and stalled work above waiting work. `--json` provides
stable machine-readable output; `--all` also includes closed-lost,
unsubscribed and completed-handover records. The command does not update a
lead, send mail, deploy a preview or change any `.env` gate.

**Keeping the public URL up.** Every unsubscribe, click-tracking and Stripe
link resolves against `PUBLIC_BASE_URL`, so those links are dead whenever the
webhook server or the ngrok tunnel is down:

```bash
python run.py serve-public            # start whatever is missing, then verify
python run.py serve-public --status   # report only; starts nothing
```

Prefer this over double-clicking `start_public.bat`. It is safe to run twice
(the .bat is not: a second webhook server cannot bind port 5000 and a second
ngrok cannot claim a static domain already in use), it takes the tunnel domain
from `PUBLIC_BASE_URL` rather than a hard-coded string, and it waits for
`/health` to answer through the public URL before claiming success. Both
processes open in their own console windows and keep running after the command
returns, so an agent can call it too. For a machine that should always be up,
use `install_autostart.ps1` or the VPS steps in `deploy/README.md` instead.

To set the bot up, open the [pre-configured app link in `.env`](.env), pick your
workspace, Create, then **Install to Workspace** and copy the *Bot User OAuth
Token*. The manifest asks for `chat:write` and `chat:write.public`, so the bot
can post to a public `SLACK_ALERT_CHANNEL` without being invited; a private
channel still needs `/invite`. Leaving `SLACK_BOT_TOKEN` blank is a valid
choice: alerts then print to the console only and preflight treats that as
deliberate.

1. **Keep it running.** Windows: `.\install_autostart.ps1` once. Real
   sending: the VPS steps in [`deploy/README.md`](deploy/README.md).
2. **`ENABLE_LIVE_SEND=true`** in `.env` -- until then every send is a
   logged dry run, whatever the rest of the config says.
3. **Warm up.** Domain age is not mailbox reputation. Start at 5-10 emails a
   day per mailbox for the first week, 15-20 the second, then the 25-ish
   ceiling. `EMAIL_MAX_PER_DAY` is the hard cap; lower it for week one.
4. **Watch the first replies by hand.** The Slack alerts and the dashboard
   show every classification and negotiation move. The classifier and the
   price band have only been exercised on dummy data until real prospects
   answer.
5. **Stripe live mode.** Swap `STRIPE_SECRET_KEY` for the live key and
   create a live-mode webhook endpoint at `PUBLIC_BASE_URL/webhook/stripe`
   (its signing secret goes in `STRIPE_WEBHOOK_SECRET`). Test keys never
   charge anyone. Done on 2026-09-06 for this deployment; preflight shows
   the current mode. The endpoint is tied to the ngrok URL and must be
   recreated if `PUBLIC_BASE_URL` ever changes.
6. **DMARC.** The domain starts at `p=none` (monitor only). After a month of
   clean sending change the `_dmarc` TXT record at Porkbun to
   `p=quarantine`, then `p=reject` once you trust it. Stricter DMARC lifts
   inbox placement.
7. **Prune test artefacts.** `python run.py cleanup-tests` removes the
   known test leads plus their Vercel projects and GitHub repos. For a
   database that is *entirely* pre-launch test data, archive the whole thing
   instead (see "Cleaning up after manual test runs" under Testing):
   `python pipeline/utils/teardown.py --all`, then
   `python run.py cleanup-tests --reset`.

## Deployment

### Always-on, the short version

- **Headless (agents, routines):** `python run.py ops start` brings up the webhook
  server, ngrok and the loop with no windows, logging to `pipeline/logs/`;
  `ops status --json` is the machine-readable state; `ops stop` takes it down.
  This is what StarNet agents use. See `docs/STARNET_INTEGRATION.md`.
- **Desktop app:** `Casey Websites.exe` on the Desktop (source in
  `tools/control_panel.py`, rebuild with `toolsuild_control_panel.bat`) is
  one window with a button per command: start/stop everything, preflight,
  dashboard, report, email preview, sourcing, checks. Start/stop/status go through `ops` (hidden); read-only buttons open one
  console each. Only one copy of the panel can run at a time.

- **Windows dev box:** run `.\install_autostart.ps1` once (PowerShell, repo
  root). It registers a logon task that runs `start_all.bat`, which opens the
  webhook server, the ngrok tunnel and the `scheduler.py` supervisor (which
  restarts `main.py` if it crashes). The pipeline then comes back after every
  reboot for as long as the PC is on.
- **VPS (recommended for real sending):** everything needed is in
  [`deploy/`](deploy/README.md): systemd units for the loop and a gunicorn-served
  webhook server, a Caddyfile for `track.caseywebsites.com` with automatic TLS,
  and step-by-step install notes. A ~£4/month box is plenty.

### A cheap VPS with tmux/screen

The simplest way to run this pipeline continuously is a small VPS
(Hetzner CX22, DigitalOcean's cheapest droplet, etc. -- this pipeline is
lightweight; the smallest tier is plenty) kept alive with `tmux` or
`screen`, which lets you disconnect your SSH session while `main.py` keeps
running *and* stay able to reconnect and type the optional operator
commands (`transfer`, `status`, `pause`/`resume`) later.

```bash
# On the VPS, after cloning the repo and completing Setup above:
sudo apt update && sudo apt install -y tmux   # or screen

tmux new -s pipeline
cd website-designers/pipeline
source ../venv/bin/activate
python main.py
# Ctrl-B then D to detach -- main.py keeps running.

# Reconnect later (e.g. to check "status" or run a manual "transfer"):
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
   Shortcut: `start_public.bat` in the repo root launches `webhook_server.py`
   and the ngrok tunnel on the account's free static dev domain in two windows.
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
