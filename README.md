# website-designers -- Cold Email Web Design Sales Pipeline

An autonomous pipeline that finds local businesses, builds them a free
website preview, sends a personalized cold email, monitors replies, and
hands off payment/transfer to a human once a lead is ready to buy.

Every automated step has a human-in-the-loop safeguard: positive replies
pause outreach for that lead and alert an operator; payment and repo/site
transfer are only ever triggered by an explicit console command.

## Directory structure

```
website-designers/
├── pipeline/
│   ├── agents/
│   │   ├── lead_agent.py      # CSV -> enriched, researched leads
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
│   │   └── tracer.py          # trace/agent/tool span logging (VoltAgent + local)
│   ├── config.py               # env loading & validation
│   ├── main.py                 # orchestrator loop + operator console
│   ├── webhook_server.py       # Flask: /click, /unsubscribe, /webhook/stripe
│   └── requirements.txt
├── templates/                   # one subfolder per niche (index.html + style.css)
├── dashboard.py                 # Streamlit monitoring UI
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
`VOLTAGENT_PUBLIC_KEY`, `VOLTAGENT_SECRET_KEY` are optional.

## Running it

All commands below are run from the repo root with the venv active.

**Ingest leads from a CSV** (columns: `business_name,niche,location`):

```bash
cd pipeline
python -m agents.lead_agent /path/to/leads.csv
cd ..
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
| `transfer <lead_id>` | Invite the client to the GitHub repo, optionally remove your own access |
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
  `{{ services_list }}` (falls back to a niche-appropriate default via
  Jinja's `default()` filter if not supplied), `{{ pain_point_solution }}`,
  `{{ testimonial }}`, `{{ location }}`, `{{ year }}`, and
  `{{ preview_url }}` (a real, working link back to the site's own
  click-tracked preview URL, shown at the bottom as a "share this preview"
  link).
- **`restaurant`, `gym`, `dentist`** -- original hand-rolled CSS designs
  from the pipeline's first iteration. Placeholders: `{{ business_name }}`,
  `{{ phone }}`, `{{ pain_point_solution }}`, `{{ testimonial }}`,
  `{{ location }}`, `{{ hero_image_url }}`, `{{ year }}`.

`design_agent.py`'s `build_context()` supplies the union of both
placeholder sets to every render, so either style works regardless of
which niche a lead matches -- unused keys are simply ignored by whichever
template doesn't reference them.

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
