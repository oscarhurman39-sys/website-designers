# What StarNet can be hooked up to, to make this business work harder

Written 2026-09-20 from the live station (v0.11.2) and this repo. Everything
here is a change to StarNet or to how the crew is pointed at the pipeline --
none of it touches `.env`, the send gates, Stripe or the caps.

Ordered by what it is worth, not by effort.

---

## 1. Point the night shift at the real backlog (the biggest single win)

The station has produced **56 finished night-shift deliverables** -- 44 code
patches and 12 standalone tools -- sitting unapplied in
`workspaces\agent\workshop\`. Read the titles and the problem is obvious:

> Live Send Guardrail Checker · Email Send Safety Checklist Widget · Stranger
> Email Final Gate Simulator · Live Send Tripwire Harness · Live Send Tripwire
> Simulator · Live Send Permission Receipt Generator · Batch Review Decision
> Sheet · Guardrail Status Patch · Offline Readiness Helper Patch

Nine straight days of work, all of it re-inventing a safety check that
`run.py preflight` already does, and none of it applied to anything. The crew
was never told what the standing job is, so it invented one, and it picked the
most cautious thing it could see.

The fix is not more agents. It is the four dossier files (CREW → AGENTS → pick
an agent → CONFIG → WHAT IT KNOWS). `purpose.md` is the standing job;
`context.md` is this repo, its commands and what "good" looks like. Draft text
is in `docs/STARNET_CREW_PLAN.md` section 2. Until those are written, every
run re-derives the basics from nothing.

A good standing job for the night shift, in one line: *"Raise the number of
qualified UK businesses in the send queue, and the reply rate of the email
that reaches them. Propose patches against this repo's tests. Never touch
`.env`, the send gates, Stripe or the caps."*

## 2. Make an unapplied patch a thing that expires

56 proposals with nobody deciding on them is worse than none: it hides the
good ones. Rule to adopt: a workshop patch is either applied within 48 hours
or deleted, and a run that produces a patch nobody applies twice in a row is a
sign the standing job is wrong, not that the agent is lazy.

The one still staged in COMMS today (`change.patch`, 15 September, 5.4 KB)
adds a `_read_env_value()` helper to `run.py` that reads `.env` and prints
gate values into a report. It should not be applied: it is five days stale,
and it puts a general-purpose `.env` reader into the one file every other
command goes through. See item 6.

## 3. Wire the crew into the alerts channel that already exists

`run.py preflight` shows Slack alerts are live: token valid for workspace
OSCAR as `casey_alerts`, posting to `#leads`. StarNet can read and post to the
same channel through a connector portal prop. That turns "a prospect replied"
into something the crew can act on instead of something only Oscar sees, and
it gives every agent a shared, human-readable log of what the business
actually did today.

Prop needed: connector portal in the Agency room (one MCP server per portal).

## 4. Stop paying for models that cannot run

From the station ledger, all-time: **$26.79 spent, and $7.03 of it went to
`gemini-3.1-pro-preview`, which failed 48% of its 112 runs.** Worse, these
model ids failed **100%** of every run they were ever given:

| model id | runs | outcome |
|---|---|---|
| `gpt-5.6-sol` (metered) | 12 | all errored |
| `gpt-6-astra` (metered) | 9 | all errored |
| `openai/gpt-6-astra` | 9 | all errored |
| `gemini-2.5-pro` | 5 | all errored |
| `gemini-2.5-flash` | 4 | all errored |
| `antigravity-preview-05-2026` | 2 | all errored |

These are ids that were typed rather than chosen from the dropdown, which is
the live model catalogue. Every pin should come from the dropdown, and every
agent that does real work belongs on the ChatGPT (Codex) lane, which is
unmetered: 66% of runs already are, and they cost nothing.

Add a fallback after the Gemini pin (SETTINGS → MODELS → FALLBACK CHAIN) so a
rate-limit retries on Codex instead of failing the run.

## 5. Put a ceiling on a runaway run

MAX ITERATIONS is set to 40 (it was unlimited). BUDGET → PER RUN and PER DAY
are still blank. Suggested: PER RUN 2, PER DAY 10. Nothing on this station
legitimately needs more, and a loop that repeats one tool call will otherwise
run until the tokens are gone. 174 million tokens have gone through this
station; a cap is what makes that number stop mattering.

## 6. A hook that makes the `.env` rule real

The rule "agents never read or print `.env`" is currently enforced by asking
nicely, and a night-shift patch already proposed a way around it (item 2).
StarNet supports hooks that block a tool call before it runs. One hook that
refuses any read or write whose path ends in `.env` turns a house rule into
something the machine enforces, for every agent, including the ones that run
while nobody is watching.

## 7. Approvals from a phone

Interactive runs wait 120 seconds for a consent prompt and then auto-deny, so
a walked-away Commander silently loses that turn. Two options: the Telegram
remote (approve from a phone), or set the agents that only ever read to FULL
POWER so they never ask. Today the whole station is on FULL POWER, which
overrides every per-agent "asks" setting -- including AGENCY-NEGOTIATOR, the
one agent that most needs to ask, because it is the one that talks to
customers about money.

## 8. The back-and-forth that actually worked today

Worth keeping as a pattern, because it produced a real diagnosis in one pass:

1. Give the agent a **lane** and a hard constraint list ("read and diagnose
   only, never `.env`, never the send switches, never email anyone").
2. Give it **numbered questions** that each demand a number, not an opinion.
3. Ask for the reply as **a file in the repo** in the `AGENTS.md` handoff
   format, under 400 words.

MASKY came back with the cooldown arithmetic bucket by bucket, proved the
Vercel previews were fine (81 of 81 links validate), and listed the three
decisions only Oscar can make. That reply is `docs/MASKY_BOTTLENECK_REPLY.md`.
It is the first time an agent on this station produced something that changed
what the pipeline does.

---

## Not recommended yet

- **More agents.** 21 are already seated and most of them have never run.
- **A web-search connector for lead research.** Google Places is the sourcing
  path and it is billed per request; a second research channel adds cost
  before the first one is being swept properly.
- **Ollama for agent work.** Proven on this station not to survive the tool
  loop: a 4B model looped and answered in Chinese.
