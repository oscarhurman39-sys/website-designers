# Website Designers agent workboard

This is the active handoff board for the agents already seated in the Agency room. It implements the room roster as concrete work lanes and now points every lane at the first sellable agency system in `docs/AGENCY_SALES_SYSTEM.md`. The system is now live, so this board is about controlled revenue work: more geographic spread, stronger previews, safer queue control, and human-readable proof before a send slot is burned.

Source of truth: `docs/STATE_AND_PLAN.md`, then `docs/ROOM_ROSTER.md`, `docs/STARNET_SKILL_WIRING.md`, `docs/AGENT_LOCKS.md`, and the affected source/tests.

## Current safety state

- `SOURCING_ENABLED=true` and `ENABLE_LIVE_SEND=true` were enabled by the Commander; agents must not flip either value during normal work.
- Stripe is LIVE (keys and webhook endpoint since 2026-09-06): a YES reply on a live send creates a real, payable checkout.
- `EMAIL_MAX_PER_DAY` and `EMAIL_MAX_PER_DAY_PER_ACCOUNT` are Commander-owned warm-up limits. Agents may read them, but never raise them.
- The live-send danger is not just volume: do not send the same generic preview to a cluster of competing businesses in one town. Crawley plumbers already proved why this needs queue discipline.
- `python run.py preflight` is the go-live gate: it must print `GO (ARMED)` before any agent starts or resumes the loop, and `NO-GO` means stop.
- The sellable offer, lead rules, payment terms, and go-live checklist live in `docs/AGENCY_SALES_SYSTEM.md`.
- Proposal/reply/payment wording lives in `docs/CLIENT_PROPOSAL_AND_TERMS.md`.
- Every code change goes through FINN's review lane and the full test suite before it is considered ready.

## Landed outside the lanes (Claude Code, 2026-09-06)

Commander decisions were wired in directly; FINN should review rather than
redo: two-option pricing (£589 / £39 per month), guarantee wording, one
follow-up reminder, monthly-plan escalation to a human, `won_amount` +
`run.py report`, Kent/Sussex sourcing towns. Details and the five
pre-launch questions: `docs/STATE_AND_PLAN.md` sections 6a and 6b. The
uncommitted lead-quality slice (website_status / lead_score / send priority)
was committed as found, tests green.


## StarNet skill gate

The enabled web-design skills are wired through `docs/STARNET_SKILL_WIRING.md`:

- `Make a Plan` is mandatory before any non-trivial source, template, payment, schema, deployment, or multi-file process change.
- `Creative Ideation` is for bounded ideas: niches, offers, proof assets, page sections, follow-up angles. It cannot alter live send/source/payment switches.
- `Popular Web Designs` is for concept/prototype directions and self-contained page explorations. It does not directly modify production templates until AGENCY-DESIGNER renders them and FINN reviews the patch.
- `ASCII Art` is internal only: docs, console labels, morale banners, and operator-facing summaries.
- The saved `website_designers_pipeline` skill is withheld until the Commander approves it in `ABILITIES > SKILLS`; agents must not guess or recreate it.

Skill outputs are proposals until FINN verifies the relevant tests/renders/docs. No skill can turn on `SOURCING_ENABLED`, `ENABLE_LIVE_SEND`, Stripe live mode, webhook mode, or outbound volume.

## Daily revenue shift

Every active Agency agent starts with the same four inputs, then works only its lane:

```bat
python run.py ops status --json
python run.py preflight
python run.py control --json
python run.py report
```

If `ops status --json` is not all-up, FINN or LIL BEAR may run `python run.py ops start` and re-check. If preflight is `NO-GO`, nobody sources, sends, rebuilds, or changes queue state until FINN explains the blocker to MASKY.

The daily objective is to spend the available send slots on varied, business-specific previews across southern UK towns. A good day is not "maximum emails at any cost"; it is the maximum safe number of reviewed, distinct, reachable prospects the warm-up caps allow. Same-town/same-niche repeats are held unless AGENCY-DESIGNER verifies that each preview is meaningfully unique.

## Active lanes

### FINN — room lead

**Now:** own the live revenue gate. Keep the public URL up, keep preflight green, review every code/doc change, and decide whether the loop is allowed to spend today's send slots.

**Scope:** `run.py`, `pipeline/main.py`, `pipeline/agents/*`, `pipeline/utils/*`, tests, and this document.

**Acceptance:**

- `ops status --json` shows the webhook, tunnel and loop state before work starts.
- `preflight` is GO/READY before any send/rebuild/source work proceeds.
- `control --json` has no blocked/action_due item being ignored.
- Same-town/same-niche batches are either split across locations/niches or explicitly held.
- Every repo patch has tests and an updated state note when it changes reality.

**Proof:**

```bat
set TMP=%CD%\.pytest-tmp
set TEMP=%CD%\.pytest-tmp
venv\Scripts\python.exe -m pytest -q
python run.py preflight --offline
```

**Handoff:** tell MASKY how many reviewed send slots remain today, what is blocked, and whether a Commander decision is needed.

### AGENCY-DESIGNER — preview and conversion owner

**Now:** stop generic previews reaching live prospects. Audit pending `designed` leads before FINN lets the loop send them, especially same-town/same-niche clusters.

**Scope:** use `pipeline/render_preview.py`; do not change live preview/deploy logic during the audit.

**Acceptance:**

- Review desktop, tablet, and phone widths for each pending send candidate.
- Check logo/nav contrast, hero crop, gallery order, CTA visibility, phone/contact fallback, ratings and testimonial truthfulness.
- Explicitly inspect fallback niches `vehicle-repair` and `picture-framer`.
- Mark any unsupported claim, duplicate-looking preview, bad public URL, or generic proof as a hold.
- Record defects with the lead/niche/town, viewport, expected result, actual result, and screenshot path.

**Proof:**

```bat
venv\Scripts\python.exe pipeline\render_preview.py
venv\Scripts\python.exe -m pytest -q
```

**Handoff:** send defects and any template patch proposal to FINN. Do not make broad template changes without that review.

### PROMO-MARKETER — scoring rules and offer positioning

**Now:** spread the send queue across the south of the UK instead of draining one town/niche bucket.

**Deliverable:** maintain the offer and scoring rubric in `docs/AGENCY_SALES_SYSTEM.md`; propose source batches that mix towns, niches, and weak-web opportunities while staying inside warm-up caps.

**Acceptance:** every proposed batch has no more than one same-town/same-niche prospect unless AGENCY-DESIGNER has signed off unique previews; every implemented score input has a bounded contribution and a reason; rules make no claims about an unverified business; no outbound volume change is proposed as an implementation default.

**Handoff:** FINN receives the rubric; ENGINE-DBA receives required fields and allowed values.

### ENGINE-DBA — data contract owner

**Now:** give the team a queue truth source: who can be sent today, who is held, and why.

**Acceptance:** documented allowed values for `website_status`, `site_score`, `lead_score`, and `contact_channel`; send ordering is deterministic; same-town/same-niche clusters are visible in report/control output; held leads keep a reason instead of disappearing.

**Handoff:** FINN receives migration/test requirements. No schema change lands without FINN review.

### AGENCY-NEGOTIATOR — close and escalation owner

**Now:** watch every live reply and keep the close path boring: interested prospects get clear next steps, price objections stay inside the band, and complaints/refunds escalate instantly.

**Deliverable:** a state map for interested, price objection, asset submission, payment question, opt-out, complaint/refund, and human escalation.

**Acceptance:** price language respects existing code clamps; no unsupported guarantee; opt-outs suppress further contact; refund/complaint paths escalate to a human.

**Handoff:** ENGINE-DBA receives state requirements; FINN receives guardrail/test requirements.

### STUDIO-PRODUCER — sales proof owner

**Now:** turn approved live previews into proof assets the marketer can use later: screenshots, short walkthrough scripts, and before/after comparisons when there is a real before site.

**Acceptance:** every asset depicts a real current preview; no asset implies a capability the pipeline cannot deliver.

### LIL BEAR — QA/operator support

**Now:** operator smoke checks for the money machine: run status/preflight/report/log checks, catch stale docs, and keep the Commander-facing handoff plain.

**Proof:** run the supplied dry-run commands and `venv\Scripts\python.exe -m pytest -q`; report stale command/docs issues to FINN.

### PIKACHU — controller automation

**Now:** improve agent control of the pipeline without inventing a second controller. Casey Websites.exe and StarNet both ride the same `python run.py ops` surface, so any helper must call documented `run.py` commands and return machine-readable output.

**Acceptance:** any new tool names the command it wraps, refuses to touch `.env`, and proves state with a read-back command.

### SPACEY — product/revenue perspective

**Now:** stay out of implementation unless MASKY asks for a product call. When asked, judge whether the current offer, preview, and checkout path are easy enough for a non-technical local business owner to buy.

## Handoff format

Every report contains:

1. Lane and task completed.
2. Files changed, or the files proposed for change.
3. Verification command and exact result.
4. Remaining risk.
5. A Commander decision only when one is genuinely needed.

## Definition of this setup being live

The room is operational when FINN can show GO/READY preflight, PROMO-MARKETER can show a spread-out send queue, AGENCY-DESIGNER can show reviewed unique previews, AGENCY-NEGOTIATOR can see live replies, and LIL BEAR can repeat the smoke check without surprises. This document does not authorise changing `.env`, Stripe, prices, or outbound volume; the Commander owns those switches.
