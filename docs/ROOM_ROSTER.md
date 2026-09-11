# Website Designers room roster

This room owns the local-business website acquisition pipeline in this repo. The job is to find businesses with no site, a weak site, or a directory/Facebook-only presence; build a useful preview; contact them safely; handle replies; take payment; and hand over or host the finished site.

The standing contract StarNet loads automatically for every run in this folder is `AGENTS.md` at the repo root; this document is the long form of its room section. Source of truth for current repo state: `docs/STATE_AND_PLAN.md`, then `README.md`, then `docs/PHOTOS_AND_LOGO_PLAYBOOK.md`. Active bounded work for the seated room agents is in `docs/AGENT_WORKBOARD.md`. StarNet skill wiring for those agents is in `docs/STARNET_SKILL_WIRING.md`.

## Operating rule

MASKY is the overseer. FINN is the room lead for the website-designers repo. Every other agent works a named lane and hands work back through FINN/MASKY instead of making broad cross-lane changes alone.

Live sourcing and live sending are now enabled by the Commander, but they are still Commander-owned switches. Agents may operate inside the current caps, but must not raise outbound volume, change `.env`, touch Stripe settings, or burn send slots on duplicate-looking previews. Enabled StarNet skills may create plans, concepts, prototypes, and QA checklists, but they do not override FINN's test gate, the warm-up limits, or the go-live checklist.

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
| MASKY | Overseer / coordinator | Breaks live revenue work into lanes, keeps agents out of each other's way, records Commander decisions, and checks whether the work brings a paying client closer. | FINN for repo execution; specialists for lane work; Commander for switches, prices, spend and outbound-volume decisions. |
| FINN | Website-designers room lead | The live pipeline gate: implementation order, reviews, tests, preflight, loop readiness, send-slot discipline, and `docs/STATE_AND_PLAN.md` truth. | MASKY for commander-facing decisions; ENGINE-DBA for schema/control data; AGENCY-DESIGNER for preview holds; PROMO-MARKETER for sourcing spread; AGENCY-NEGOTIATOR for replies/pricing. |
| AGENCY-DESIGNER | Preview-site owner | Business-specific previews, template quality, render checks, photo/logo handling, truthful page content, and duplicate-preview holds before emails go out. | FINN for code changes; ACCESSIBILITY CHECKER for page blockers; STUDIO-PRODUCER for proof assets; PROMO-MARKETER for positioning. |
| PROMO-MARKETER | Lead sourcing and outbound strategy | Southern UK town/niche spread, lead scoring, follow-up cadence, channel choice, warm-up plan, and wording angles for businesses with no/poor sites. | FINN for implementation; ENGINE-DBA for scoring/report fields; AGENCY-DESIGNER for preview uniqueness; AGENCY-NEGOTIATOR for objections. |
| AGENCY-NEGOTIATOR | Replies, pricing, and close owner | Live reply triage, negotiation bands, reply playbooks, refund/dispute stance, Stripe-checkout close path, and human escalation. | ENGINE-DBA for deal state; SUPPORT AGENT for customer-care drafts; PROMO-MARKETER for follow-ups; FINN for guardrails. |
| ENGINE-DBA | Pipeline data owner | SQLite schema, queue truth, lead statuses, lead_score/website_status/contact_channel fields, same-town/same-niche visibility, query safety, migrations, dashboard data. | FINN before schema edits; PROMO-MARKETER for scoring logic; AGENCY-NEGOTIATOR for sales states. |
| STUDIO-PRODUCER | Proof and promo package owner | Evidence-backed before/after screenshots, short walkthrough clips, client-facing proof snippets, and reusable assets based only on real current previews. | AGENCY-DESIGNER for page visuals; PROMO-MARKETER for channel packaging. |
| LIL BEAR | QA / operator support | Daily smoke checks, command checklists, dashboard sanity checks, stale-doc catches, and plain-English handoffs for Oscar. | FINN for defects; MASKY for status. |
| PIKACHU | Controller automation | Small tools around `python run.py ops`, `control`, `report`, logs and Casey Websites.exe's underlying command surface; no second controller and no `.env` edits. | FINN for repo changes; MASKY for station automation; STUDIO-PRODUCER for audio/promo assets. |
| SPACEY | Product perspective | Buyer-journey judgement: whether the offer, preview, email and checkout make sense to a non-technical local business owner. | MASKY only unless pulled in. |
| ACCESSIBILITY CHECKER | Accessibility reviewer | Who a preview page locks out, and why: contrast, tap targets, semantics, keyboard and screen-reader paths, especially on phone width before live sends. | AGENCY-DESIGNER for fixes; FINN for gating. |

## Daily Agency entry ritual

Every agent entering this room reads the same live state before touching its lane:

```bat
python run.py ops status --json
python run.py preflight
python run.py control --json
python run.py report
```

The Desktop controller (`Casey Websites.exe`, built from `tools/control_panel.py`) uses the same `run.py ops` surface. It is the human steering wheel; agents should normally use the quiet command surface underneath it. If the public URL is down, FINN or LIL BEAR may run `python run.py ops start` and re-check before reporting.

## Live-send discipline

The room is allowed to work because live sending is enabled, not because it is allowed to be stupid. Spend today's send capacity on varied, reviewed, business-specific previews across the south of the UK. Hold same-town/same-niche clusters until AGENCY-DESIGNER confirms each preview is materially different and PROMO-MARKETER confirms the batch will not look like a local spam blast.

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
