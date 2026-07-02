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
`index.html` (Jinja2 placeholders: `{{ business_name }}`, `{{ phone }}`,
`{{ pain_point_solution }}`, `{{ testimonial }}`, `{{ location }}`,
`{{ hero_image_url }}`, `{{ year }}`) and a matching `style.css`.
`design_agent.py` only builds a site for a lead if a subfolder matching
its `niche` column exists -- add more niches by adding more subfolders.

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
