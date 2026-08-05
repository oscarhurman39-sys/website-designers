# PLATFORM.md — from one website-agency tool to a cold-email sales pipeline

Written 2026-08-04, in response to: turn this framework into a large-scale
cold email pipeline creation and sales team — explicitly **not** phone
sales. This is a plan, not a refactor in progress; nothing below has been
built yet. `BASELINE.md` is about leveling up *what this app builds*
(templates -> AI content). This doc is about *what this app is for* —
generalizing it from "sells websites" to "runs cold-email sales pipelines
for whatever you plug into it."

## The reframe: what's actually generic here already

Strip away "website" and this pipeline is already a specific instance of
a general pattern:

1. Find a lead with a real, verifiable gap (`lead_agent.py`'s scrape +
   `site_audit.py`'s pain-point findings).
2. Build a real, tangible artifact of value **before ever contacting
   them** — proof, not a promise, not a pitch (`design_agent.py`'s
   preview site).
3. Cold-email them the artifact directly, no discovery call, no phone
   (`sales_agent.py`).
4. Let them self-serve buy it, no negotiation required
   (`/buy/<lead_id>`, Stripe Checkout).
5. Auto-fulfill (`onboarding_agent.py`).
6. Anyone who replies instead of buying goes to a human for negotiation —
   over email, via the existing takeover flow. Never a call.

That six-step shape has nothing to do with websites. It's "build it
first, sell it as a fait accompli, fulfill automatically" — which works
for anything you can pre-build cheaply and deliver digitally: an audit
report, a demo, a sample deliverable, a personalized proposal, a piece of
content, a lookalike/prospect list. "Sales team" in this plan means
**more people running this same async, self-serve, email-first loop** —
not people picking up phones. Nothing here proposes calling anyone, and
nothing about the architecture below requires it.

## What's already generic vs. what's welded to "website"

| Module | Status |
|---|---|
| `sales_agent.py` — drafting, sending, rate limits, follow-ups, reply classification, takeover | Generic. Niche-flavored copy is a small surface inside it, not the core. |
| `stripe_utils.py`, `webhook_server.py`'s `/buy` + webhook handler | Fully generic — a lead id and a price, nothing website-specific. |
| `compliance.py`, `tracker.py`, `email_utils.py` | Fully generic. |
| `db.py`, `main.py`'s orchestration loop | Generic *shape*, but the schema and status machine assume one lead = one website deal (see below). |
| `lead_agent.py` | Partly generic — the scrape/enrich mechanics are reusable; "does this business have a good website" is the one website-specific judgment inside it. |
| `design_agent.py`, `templates/`, `onboarding_agent.py`, `editor_agent.py` | Fully website-specific. This is the part that has to become swappable. |

## The core change: an `Offer` abstraction

Today, "what we're selling" is hardcoded across `design_agent.py` (the
artifact), `config.WEBSITE_PRICE_USD` (the price), and
`onboarding_agent.py` (fulfillment). Introduce a single interface an
"offer" module implements:

```
build_artifact(lead) -> artifact     # design_agent.process_lead() today
pitch_copy(lead, artifact) -> ...    # the niche-specific slice of sales_agent's copy
price(lead) -> int                   # config.WEBSITE_PRICE_USD today
fulfill(lead) -> dict                # onboarding_agent.complete_onboarding() today
```

`agents/offers/website/` becomes the first implementation (today's code,
moved and adapted, not rewritten) — proof that the interface actually
fits real code before a second offer ever gets built. `main.py`'s cycle
and `sales_agent.py` call through the interface instead of importing
`design_agent`/`onboarding_agent` directly.

## Data model: one pipeline, many offers running at once

`leads.niche` currently conflates two different things: which template to
render, and (implicitly) which "deal" this lead is even in. Add an
`offer_id` column (or reuse/rename `niche` to mean "offer" once
`Offer` exists) so:

- Lead sourcing, pricing, and messaging can differ per offer.
- The dashboard can report funnel metrics (sent → replied → closed) **per
  offer**, so you can compare offers on real data instead of one global
  number, and kill or scale each independently.
- A single sending domain's daily cap can be allocated across offers
  deliberately instead of implicitly.

This is a schema migration plus routing changes in `main.py`/
`sales_agent.py`/the dashboard queries — mechanical once the `Offer`
interface exists, not a rewrite.

## Scaling lead generation

Still true from `BASELINE.md`: DuckDuckGo scraping (`lead_agent.
find_business_website`) is a volume ceiling, and picking a paid data
source is a real vendor/cost decision, not something to default into. At
"large scale" this decision can't stay deferred indefinitely the way it
could for one offer — but the fix is still "pick one real source and
prove it on the first offer," not "integrate five sources speculatively."

## Scaling the sending side

`config.py`'s `EMAIL_MAX_PER_HOUR`/`EMAIL_MAX_PER_DAY`/`EMAIL_HOST`/
`EMAIL_USER` are single global values — one sending identity, full stop.
`main.py`'s `_run_cycle()` sends **at most one** cold email per 60-second
cycle from that one identity. This is the real ceiling on "large scale,"
not lead volume or offer count:

- Multiple sending domains/mailboxes, each independently warmed up
  (`WARMUP.md`'s process today is a manual one-time checklist for a
  single domain — it needs to become a per-domain ramp schedule the
  pipeline tracks, not a doc you follow once).
- Per-identity rate-limit tracking in the DB (today's caps are pure
  in-process state), so sends round-robin across warmed domains instead
  of bottlenecking on one.
- Deliverability monitoring per domain (bounce/complaint rate is already
  tracked via `compliance.py`; it needs to become a per-domain view, not
  a global one, or one burned domain's damage is invisible until it's
  already sunk the others).

## The "sales team" part, concretely

`sales_agent._TAKEOVER_LEAD_IDS` today is a single in-process set, and the
only interface to it is `main.py`'s stdin console — meaning exactly one
person, at one terminal, on one machine, can ever run takeover. A real
team needs:

- Takeover state moved to the DB (it's in-memory/per-process right now,
  which is also why a restart silently drops it — a bug worth fixing
  regardless of team size).
- A claim mechanism so a reply is picked up by one operator, not raced
  by several (an `assigned_to` column + a lightweight claim endpoint on
  `dashboard.py`, replacing the stdin-only `takeover <lead_id>` command
  with something a browser can hit).
- Everything downstream of "claimed" stays exactly what it is today: an
  email thread. No phone step gets introduced anywhere in this design.

## Recommended sequencing

1. **Still Path A first, unchanged from `BASELINE.md`.** Validate the
   core six-step pattern against one real offer (websites) before
   multiplying it — scaling something unproven just multiplies the
   waste if the pattern itself doesn't convert. Still not done — see
   `LEDGER.md`.
2. **Extract the `Offer` interface**, with website as the first (proven)
   implementation. **Done (2026-08-05, see `PLAN.md`).**
   `agents/offers/base.py`'s contract (`build_artifact`/`price`/
   `fulfill`) plus `agents/offers/website.py` as pure delegation to the
   unchanged `design_agent.py`/`onboarding_agent.py`/`config.py`;
   `main.py`'s `payment ready` and `webhook_server.py`'s `/buy` +
   `/onboard` now go through `agents/offers/registry.get_offer(lead)`
   instead of hardcoding website specifics. `design_agent.run()`'s build
   loop and its retry/attempt-counting were deliberately left in place,
   still called directly by `main.py` — generalizing that loop is step
   3's job, once there's an `offer_id` to dispatch on.
3. **Add `offer_id` to the data model** and offer-scoped reporting.
4. **Pick and build ONE second offer**, end to end, through the new
   interface — a real decision only you can make, and the only real test
   of whether the abstraction actually generalizes. Don't build a
   speculative plugin system for offers that don't exist yet.
5. **Scale sending infra (multiple domains)** only once ≥2 real offers
   genuinely need more volume than one warmed domain supports —
   premature multi-domain infrastructure before that is pure waste and
   extra reputation risk to manage for no reason.
6. **Move takeover to the DB + a claim UI** only once single-operator
   reply volume is the actual bottleneck, not before.

## Decisions only you can make

- What are the first offers beyond websites — what else can you (or a
  small team) pre-build cheaply enough to give away as proof before the
  ask? This plan can't pick that; it can only make the pipeline ready to
  carry whatever you choose.
- Lead data source and budget, once volume across offers actually
  requires one.
- Whether you're bringing on other people for takeover/negotiation now,
  or building the DB/claim-UI groundwork solo first and adding people
  once step 6 is reached.
