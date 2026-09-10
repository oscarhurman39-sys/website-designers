# Website Designers room roster

This room owns the local-business website acquisition pipeline in this repo. The job is to find businesses with no site, a weak site, or a directory/Facebook-only presence; build a useful preview; contact them safely; handle replies; take payment; and hand over or host the finished site.

The standing contract StarNet loads automatically for every run in this folder is `AGENTS.md` at the repo root; this document is the long form of its room section. Source of truth for current repo state: `docs/STATE_AND_PLAN.md`, then `README.md`, then `docs/PHOTOS_AND_LOGO_PLAYBOOK.md`. Active bounded work for the seated room agents is in `docs/AGENT_WORKBOARD.md`. StarNet skill wiring for those agents is in `docs/STARNET_SKILL_WIRING.md`.

## Operating rule

MASKY is the overseer. FINN is the room lead for the website-designers repo. Every other agent works a named lane and hands work back through FINN/MASKY instead of making broad cross-lane changes alone.

Do not turn on live sourcing or live sending casually. The repo currently keeps real outbound activity behind `SOURCING_ENABLED=false` and `ENABLE_LIVE_SEND=false`; changing those is a go-live decision, not routine development. Enabled StarNet skills may create plans, concepts, prototypes, and QA checklists, but they do not override FINN's test gate or the go-live checklist.

## StarNet room crew

These are real StarNet agents, not just names in this document. The display name and the StarNet
`agentId` differ — when addressing an agent through StarNet's API, cron, or loops, the `agentId` is
what identifies it. Verified against `agent.roster.json` on 2026-09-09.

`MASKY=agent` · `FINN=hello-3` · `AGENCY-DESIGNER=webdesigner` · `PROMO-MARKETER=marketer`
`AGENCY-NEGOTIATOR=negotiator` · `ENGINE-DBA=dbhelper` · `STUDIO-PRODUCER=producer`
`LIL BEAR=hello` · `PIKACHU=hello-2` · `SPACEY=hello-4` · `ACCESSIBILITY CHECKER=a11y`

Nine of the eleven run with executionProfile `trusted-project`, which scopes them to the agent
workspace plus approved project folders. This repo is an approved (blessed) folder.

| Agent | Role | Owns | Hands off to |
|---|---|---|---|
| MASKY | Overseer / coordinator | Breaks work into lanes, keeps the room moving, records decisions, checks that work matches the business goal. | FINN for repo execution, specialists for lane work. |
| FINN | Website-designers room lead | Repo-level direction, implementation order, reviews, tests, and merge readiness. Keeps `docs/STATE_AND_PLAN.md` honest. | MASKY for commander-facing decisions; ENGINE-DBA for schema; AGENCY-DESIGNER for templates; PROMO-MARKETER for sourcing; AGENCY-NEGOTIATOR for replies/pricing. |
| AGENCY-DESIGNER | Preview-site owner | Template quality, niche layouts, photo/logo handling, local render checks, conversion clarity on preview pages. | FINN for code changes; STUDIO-PRODUCER for assets; PROMO-MARKETER for offer positioning. |
| PROMO-MARKETER | Lead sourcing and outbound strategy | Niches, towns, lead scoring, follow-up cadence, channel choice, warm-up plan, and wording angles for businesses with no/poor sites. | ENGINE-DBA for scoring fields; AGENCY-NEGOTIATOR for sales objections; FINN for implementation. |
| AGENCY-NEGOTIATOR | Replies, pricing, and close owner | Negotiation bands, reply playbooks, refund/dispute stance, close logic, and when to escalate to a human. | ENGINE-DBA for deal state; PROMO-MARKETER for follow-ups; FINN for code guardrails. |
| ENGINE-DBA | Pipeline data owner | SQLite schema, lead statuses, lead_score/website_status/contact_channel fields, query safety, migrations, dashboard data. | FINN before schema edits; PROMO-MARKETER for scoring logic; AGENCY-NEGOTIATOR for sales states. |
| STUDIO-PRODUCER | Proof and promo package owner | Before/after screenshots, short demo clips, client-facing promo snippets, reusable visual/audio assets for the offer. | AGENCY-DESIGNER for page visuals; PROMO-MARKETER for channel packaging. |
| LIL BEAR | QA / operator support | Smoke runs, checklist sweeps, plain-English docs, dashboard sanity checks, repetitive cleanup. | FINN for defects; MASKY for status. |
| PIKACHU | Automation/integration support | Local scripts, future FL Studio/audio hook experiments, utility automation that does not belong to core pipeline agents. | FINN for repo changes; STUDIO-PRODUCER for audio/promo assets. |
| SPACEY | Reserve product perspective | Keeps an eye on how this room can later connect to app-launch lessons from the botanical app without distracting current work. | MASKY only unless pulled in. |
| ACCESSIBILITY CHECKER | Accessibility reviewer | Who a preview page locks out, and why: contrast, tap targets, semantics, keyboard and screen-reader paths. | AGENCY-DESIGNER for fixes; FINN for gating. |

## Repo-local subagent lanes already present

The repo also contains project-scoped persona files under `.claude/agents/`. Treat these as lane instructions for code assistants working inside the repo:

| Repo persona | Lane |
|---|---|
| `lead-researcher` | Website discovery, public research, contact email extraction, pain point/testimonial extraction. |
| `cold-email-drafter` | Cold email prompt, fallback copy, subject/body parser, under-150-word tone. |
| `email-compliance` | Unsubscribe, List-Unsubscribe, physical-address footer, bounce suppression, send-rate limits. |
| `client-assets` | Prospect photos/logo, inbound attachments, in-place preview rebuild, asset confirmation replies. |
| `github-manager` | Private repo creation, file push, collaborator invite, handover flow. |
| `vercel-deployer` | API deployment of preview files, project names, deployment polling. |
| `stripe-checkout` | Checkout session creation, webhook verification, close-to-payment chain. |
| `autonomy-guardrails` | Price clamp, model safety filter, negotiation cap, alerts, pause/resume controls. |
| `backend-developer` | General backend implementation when no narrower lane owns it. |

## First outward build lanes

1. Lead quality: add `website_status`, `site_score`, and `lead_score` so sends target the businesses most likely to need help.
2. Contact coverage: add a safe path for no-site or directory-only businesses instead of skipping them because no website email exists.
3. Follow-up: add one polite reminder after 3 days, then stop.
4. Offer model: decide whether to keep one-off pricing or add an upfront-plus-monthly hosting package before live sales.
5. Go-live readiness: clean test leads (`teardown.py --all` + `cleanup-tests --reset`), Stripe is already live (2026-09-06), keep sending/sourcing off until the Commander explicitly flips those flags and `run.py preflight` says GO.

## Handoff format

Every agent report should say: what lane it touched, what files changed or should change, what command proves it, what risk remains, and what decision is needed from the Commander if any.
