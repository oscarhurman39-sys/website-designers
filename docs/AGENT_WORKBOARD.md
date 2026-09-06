# Website Designers agent workboard

This is the active handoff board for the agents already seated in the Agency room. It implements the room roster as concrete work lanes and now points every lane at the first sellable agency system in `docs/AGENCY_SALES_SYSTEM.md` without enabling sourcing, outbound email, Stripe live mode, or any other go-live switch.

Source of truth: `docs/STATE_AND_PLAN.md`, then `docs/ROOM_ROSTER.md`, `docs/AGENT_LOCKS.md`, and the affected source/tests.

## Current safety state

- `SOURCING_ENABLED=false` unless the operator deliberately opts into hourly Google Places sourcing.
- `ENABLE_LIVE_SEND=false` unless the operator deliberately turns real email on.
- Stripe remains in test mode until the operator deliberately supplies live keys.
- Existing data in `pipeline/leads.db` is test data and must not be treated as a sales queue.
- The sellable offer, lead rules, payment terms, and go-live checklist live in `docs/AGENCY_SALES_SYSTEM.md`.
- Proposal/reply/payment wording lives in `docs/CLIENT_PROPOSAL_AND_TERMS.md`.
- Every code change goes through FINN's review lane and the full test suite before it is considered ready.

## Active lanes

### FINN — room lead

**Now:** own the safe lead-quality slice: maintain the deterministic scoring functions and send-priority queue for the website statuses the code detects.

**Scope:** `pipeline/agents/sourcing_agent.py`, `pipeline/utils/db.py` only if necessary, and relevant tests.

**Acceptance:**

- `none` with a known contact route maps to `lead_score=10`.
- `platform_only` with a known contact route maps to `lead_score=9`.
- Unknown-contact versions stay below the known-contact versions.
- No-site/platform-only records keep `site_score=0` until site inspection exists.
- Prioritised queue ordering uses the stored `lead_score`.
- Neither sourcing nor sending becomes enabled.

**Proof:** `venv\Scripts\python.exe -m pytest -q`

**Handoff:** identify the next scoring input that needs a commander decision, if any. Update `docs/STATE_AND_PLAN.md` when the slice lands.

### AGENCY-DESIGNER — preview and conversion owner

**Now:** conduct the first render audit of every supplied sample lead in the modern template.

**Scope:** use `pipeline/render_preview.py`; do not change live preview/deploy logic during the audit.

**Acceptance:**

- Review desktop, tablet, and phone widths for each sample render.
- Check logo/nav contrast, hero crop, gallery order, CTA visibility, phone/contact fallback, ratings and testimonial truthfulness.
- Explicitly inspect fallback niches `vehicle-repair` and `picture-framer`.
- Record defects with the lead/niche, viewport, expected result, actual result, and screenshot path.

**Proof:**

```bat
venv\Scripts\python.exe pipeline\render_preview.py
venv\Scripts\python.exe -m pytest -q
```

**Handoff:** send defects and any template patch proposal to FINN. Do not make broad template changes without that review.

### PROMO-MARKETER — scoring rules and offer positioning

**Now:** define the business logic for the next score expansion without changing production switches.

**Deliverable:** maintain the offer and scoring rubric in `docs/AGENCY_SALES_SYSTEM.md`; propose the next expansion for review count, rating, niche value, and owned-site quality only after the first batch has outcomes.

**Acceptance:** every implemented input has a bounded score contribution and a reason; rules make no claims about an unverified business; no outbound volume change is proposed as an implementation default.

**Handoff:** FINN receives the rubric; ENGINE-DBA receives required fields and allowed values.

### ENGINE-DBA — data contract owner

**Now:** review the proposed scoring rubric and provide the minimal schema/migration contract.

**Acceptance:** documented allowed values for `website_status`, `site_score`, `lead_score`, and `contact_channel`; migration is backward-compatible with current test data; send ordering is deterministic.

**Handoff:** FINN receives migration/test requirements. No schema change lands without FINN review.

### AGENCY-NEGOTIATOR — close and escalation owner

**Now:** maintain the reply-state and proposal/payment contract in `docs/CLIENT_PROPOSAL_AND_TERMS.md` before the first live reply exists.

**Deliverable:** a state map for interested, price objection, asset submission, payment question, opt-out, complaint/refund, and human escalation.

**Acceptance:** price language respects existing code clamps; no unsupported guarantee; opt-outs suppress further contact; refund/complaint paths escalate to a human.

**Handoff:** ENGINE-DBA receives state requirements; FINN receives guardrail/test requirements.

### STUDIO-PRODUCER — sales proof owner

**Now:** wait for AGENCY-DESIGNER's approved render targets, then make only evidence-backed before/after or walkthrough material.

**Acceptance:** every asset depicts a real current preview; no asset implies a capability the pipeline cannot deliver.

### LIL BEAR — QA/operator support

**Now:** verify dry-run and go-live documentation against actual commands after FINN's scoring slice is ready.

**Proof:** run the supplied dry-run commands and `venv\Scripts\python.exe -m pytest -q`; report stale command/docs issues to FINN.

### PIKACHU and SPACEY — reserve lanes

No active implementation work. They are pulled in only for a concrete automation or product question approved by FINN/MASKY.

## Handoff format

Every report contains:

1. Lane and task completed.
2. Files changed, or the files proposed for change.
3. Verification command and exact result.
4. Remaining risk.
5. A Commander decision only when one is genuinely needed.

## Definition of this setup being live

The room is operational when the code-backed lead-quality slice passes tests, AGENCY-DESIGNER can return a render-audit report, and the remaining specialists have bounded inputs/outputs ready for those results. This document does not authorise real sourcing, sending, charges, or handover; the go-live gate is the checklist in `docs/AGENCY_SALES_SYSTEM.md`.
