# Website Designers — house rules

StarNet injects this file into every run anchored to this folder. It is the standing contract for
this repo. A direct instruction from the Commander always wins over it.

This repo runs a local-business website acquisition pipeline: find businesses with no site, a weak
site, or a directory/Facebook-only presence; build a real preview; contact them safely; handle
replies; take payment; hand over or host the finished site.

## Hard gates — never flip these as part of another task

Real money and real outbound sit behind flags in `.env`. Changing any of them is a go-live decision
by the Commander, never routine development, never a side effect of a refactor:

- `SOURCING_ENABLED` — real lead discovery
- `ENABLE_LIVE_SEND` — real outbound email (otherwise sends log to `pipeline/dry_run.log`)
- Stripe mode — **Stripe is LIVE as of 2026-09-06**. Treat every payment path as real money.
- The price clamp / negotiation band — enforced in code. Never widen it to close a deal.
- Compliance invariants: unsubscribe, `List-Unsubscribe`, physical-address footer, bounce
  suppression, send-rate limits, the under-150-word cold-email cap.

If a task appears to require flipping one of these, stop and say so instead of doing it.

## Proving your work

There is one verification command and it is exit-code clean:

    python run.py preflight            # prints GO / READY / NO-GO; exit 1 on NO-GO, 0 otherwise
    python run.py preflight --offline  # same, no network checks

Tests are pytest from the repo root (`tests/conftest.py`, run as `pytest`). A change is not done
until the relevant tests pass and, for anything touching sourcing, sending, payment, or deploy,
`preflight` still reports GO or READY.

Never edit a test to make it pass. If a test is wrong, say so and explain why.

## The run.py surface — the only supported entry points

    quick-test   loop        dashboard     test-email <addr>   cleanup-tests [--dry-run]
    source [--limit N] [--dry-run]         rebuild <lead_id>   report
    preflight [--offline]                  preview-email [--lead ID]
    test-alert   serve-public [--status]   reviewed-batch ...
    ops start|stop|restart|status [--json]|logs [webhook|ngrok|loop]

`run.py` is a thin dispatcher — each mode runs as its own subprocess. Do not reimplement a mode
in-process; call it.

## Running the pipeline without a human at the keyboard

`python run.py ops start` brings up the webhook server, the ngrok tunnel and the
pipeline loop **hidden** (no console windows, logs in `pipeline/logs/`), and is
idempotent. `ops status --json` is the machine-readable truth about what is up;
exit code 0 means everything, including the public URL, is reachable. `ops stop`
takes it all down. Never use `start_all.bat` from an agent run: it opens three
windows on the Commander's screen. The Desktop app (`tools/control_panel.py`)
calls the same `ops` commands, so agents and the Commander see one state.

A daily agent shift is: `ops status --json` -> if not all up, `ops start` ->
`preflight` -> read `report` and `ops logs loop` -> act within your lane ->
hand back through FINN/MASKY. Details, ownership per StarNet agent, and the
open questions are in `docs/STARNET_INTEGRATION.md`.

## Design work

Production preview pages need: inspect the current template first, then desktop/tablet/phone
renders, then screenshots, then review. Concept work and prototypes are proposals, not edits to
production templates. Proof and promo material must come from real current previews — never
invented client results, never fabricated testimonials or metrics.

## The room

The Commander runs this repo through a StarNet room. Display name -> StarNet agentId:

| Agent | agentId | Lane |
|---|---|---|
| MASKY | `agent` | Overseer. Breaks work into lanes, records decisions. |
| FINN | `hello-3` | Room lead. Implementation order, reviews, tests, merge readiness. Owns the test gate. |
| AGENCY-DESIGNER | `webdesigner` | Preview templates, niche layouts, photo/logo handling, render checks. |
| PROMO-MARKETER | `marketer` | Niches, towns, lead scoring, cadence, channel choice, positioning. |
| AGENCY-NEGOTIATOR | `negotiator` | Replies, pricing, refunds/disputes, close logic, escalation. |
| ENGINE-DBA | `dbhelper` | SQLite schema, lead states, scoring fields, queries, migrations, dashboard data. |
| STUDIO-PRODUCER | `producer` | Before/after screenshots, demo clips, promo packaging. |
| LIL BEAR | `hello` | QA and operator support. Smoke runs, checklists, plain-English docs. |
| PIKACHU | `hello-2` | Automation/integration support outside the core pipeline. |
| SPACEY | `hello-4` | Product/app-launch perspective. Reserve. |
| ACCESSIBILITY CHECKER | `a11y` | Who a page locks out, and why. |

Work a named lane and hand back through FINN/MASKY rather than making broad cross-lane changes
alone. Skills and concepts advise; the repo's gates decide. A skill output is a proposal until the
tests, render audit, and safety flags agree.

## Repo-local personas

`.claude/agents/**` holds lane instructions for code assistants working inside the repo
(`lead-researcher`, `cold-email-drafter`, `email-compliance`, `client-assets`, `github-manager`,
`vercel-deployer`, `stripe-checkout`, `autonomy-guardrails`, `backend-developer`). They are
advisory prose, not dispatched by code. Where a persona and this file disagree, this file wins.

## Where state actually lives

- `docs/STATE_AND_PLAN.md` — current repo state and plan. Keep it honest.
- `docs/AGENT_WORKBOARD.md` — active bounded work.
- `docs/ROOM_ROSTER.md` — the room, in full.
- `docs/STARNET_SKILL_WIRING.md` — how StarNet skills map onto the room.
- `README.md` — operational detail.
- `leads.db` — SQLite; the real pipeline state. Test leads are cleaned with
  `python run.py cleanup-tests`.

The `.task-*.txt` and `.batch_*.txt` files in the repo root are untracked session scratch, not a
protocol. Do not build on them and do not treat them as current.

## Secrets

`.env` holds live credentials and is gitignored. Never print its values, never copy them into
docs, code, commit messages, or `.env.example`. `.env.example` carries placeholders only — keep it
that way.

## Handoff format

Every report says: what lane it touched, what files changed, what command proves it, what risk
remains, and what decision is needed from the Commander.
