# StarNet skill wiring for Website Designers

This document wires the enabled StarNet web-design skills into the seated Agency-room agents and the repo process.

## Current skill state

Verified against StarNet's live state on 2026-09-09. StarNet keeps skills in two different places,
and this document previously conflated them.

**1. Catalog skills** — toggled in Abilities > Skills, recorded in `skillprefs.jsonl`. Enabled for
this station right now:

| Skill | Status | How it is used |
|---|---|---|
| Popular Web Designs | enabled | Design agents use it for self-contained concept/prototype directions only; not direct production edits. |
| Marketing Plan | enabled | PROMO-MARKETER: channel, niche, and cadence planning. |
| Landing Copy | enabled | Page and preview copy proposals. |
| Email Sequence | enabled | Follow-up cadence proposals, inside the compliance rules. |
| Code Review | enabled | FINN's review lane. |
| Web Research | enabled | Lead and niche research. |

**2. Agent-scoped learned skills** — written by StarNet's own `background-review` curator after real
runs, recorded in `skills.jsonl` and scoped to one agentId. These were learned from work on this
repo and are all `active`:

| Skill | Agent | Covers |
|---|---|---|
| `website_designers_pipeline` | MASKY (`agent`) | Pipeline SOP, testing rules, negotiation guardrails. |
| Lead scoring implementation and documentation alignment | FINN (`hello-3`) | Deterministic lead scoring without touching live sourcing/sending. |
| Repo process safety skill wiring review | FINN (`hello-3`) | Reviewing agent/skill wiring for repo process safety. |
| StarNet agency room staffing and connector readiness | FINN (`hello-3`) | Room staffing, connector props, safety gates. |
| Website Agency Room Wiring and Go-Live Readiness | AGENCY-DESIGNER (`webdesigner`) | Room seating, role lanes, launch gates. |
| Website Template Preview Conversion Review | AGENCY-DESIGNER (`webdesigner`) | Reviewing rendered previews for conversion and credibility. |
| Sales Closing Terms and Reply Playbooks | AGENCY-NEGOTIATOR (`negotiator`) | Offer terms, negotiation ladders, payment/refund boundaries. |
| Python Test Troubleshooting and Verification | AGENCY-NEGOTIATOR (`negotiator`) | Separating product failures from environment failures. |
| Go-live review for autonomous cold-email sales pipelines | PROMO-MARKETER (`marketer`) | Pre-go-live review of sourcing, outbound, payment, handoff. |

### Corrections to earlier versions of this document

- `website_designers_pipeline` was described here as **withheld, pending Commander approval**. It is
  not withheld — it is `state: active` on MASKY and has been for some time. Treat it as live.
- "Make a Plan" and "ASCII Art" were listed as active skills. **Neither exists in StarNet's skill
  store.** Planning discipline is a working practice, kept below under Agent wiring; it is not a
  skill you can enable.
- "Creative Ideation" was listed as an active skill. It is not a registered skill either. Where this
  document says an agent "uses Creative Ideation", read it as the bounded-ideation practice
  described under Agent wiring.

Do not re-add a skill row to this document without checking it against StarNet's live state first.

## Global rule

Skills advise; repo gates decide. A skill output is a proposal until FINN verifies the relevant tests, render audit, docs, and safety flags. No skill may enable SOURCING_ENABLED, ENABLE_LIVE_SEND, Stripe live mode, webhook mode, outbound volume, unsubscribe bypasses, or price changes outside the code-clamped negotiation band.

## Agent wiring

- MASKY uses Make a Plan for multi-agent/multi-file work and Creative Ideation for sellable angles after safety state is named.
- FINN uses Make a Plan for implementation, migration, queue, payment, and template changes; FINN owns final verification.
- AGENCY-DESIGNER uses Make a Plan before template/shared CSS/render/deploy-facing edits, Creative Ideation for niche/page concepts, and Popular Web Designs for prototype directions. Production design work still needs inspection, desktop/tablet/phone renders, screenshots, and FINN review.
- PROMO-MARKETER uses Creative Ideation for lead sources, niches, follow-ups, positioning, and proof packaging; marketer outputs are bounded proposals and cannot change live send/source switches.
- AGENCY-NEGOTIATOR uses Make a Plan for reply/payment/refund/state changes and Creative Ideation only for objection-handling variants inside existing legal/payment guardrails.
- ENGINE-DBA uses Make a Plan before migrations, lead states, scoring fields, queue ordering, report queries, or dashboard data changes.
- STUDIO-PRODUCER uses Creative Ideation and Popular Web Designs to package proof from real current previews only; no imaginary client results.
- LIL BEAR uses Make a Plan for checklist sweeps and ASCII Art only for internal/operator-facing material.
- PIKACHU and SPACEY use these skills only when MASKY or FINN assigns a bounded support task.

## Repo persona overlay

The .claude/agents personas inherit this overlay when invoked through StarNet:

- backend-developer: Make a Plan before backend/API/payment/deploy/multi-file changes.
- lead-researcher: Make a Plan before discovery-source changes; Creative Ideation only for lawful lead-source experiments.
- cold-email-drafter: Creative Ideation for copy variants, never bypassing compliance footer, unsubscribe, word cap, or preview-link rules.
- email-compliance: Make a Plan before compliance changes; compliance invariants beat every creative suggestion.
- client-assets: Make a Plan before asset pipeline edits; Creative Ideation for client-photo request wording.
- github-manager: Make a Plan before repo lifecycle or handover changes.
- vercel-deployer: Make a Plan before deployment behavior changes; design concepts do not alter deployment architecture.
- stripe-checkout: Make a Plan before payment changes; no unclamped or unsigned payment path.
- autonomy-guardrails: Make a Plan before automation changes; final guardrail review for skill-driven suggestions.

## Process checkpoints

New template/page direction: AGENCY-DESIGNER generates concept, plans before edits, inspects current template, renders phone/tablet/desktop screenshots, then hands the patch to FINN.

New niche/offer angle: PROMO-MARKETER generates the angle, AGENCY-DESIGNER confirms the preview can support it truthfully, AGENCY-NEGOTIATOR checks terms, ENGINE-DBA checks scoring/state needs, and FINN verifies before any outbound batch uses it.

Payment/close flow change: AGENCY-NEGOTIATOR proposes behavior, stripe-checkout and FINN review the money boundary, ENGINE-DBA reviews deal state/reporting, and tests must cover checkout, bad signatures, valid completion, and cancelled/failed state.

Launch readiness: before the first live batch, FINN/MASKY read back mailbox, cap, clean DB, Stripe mode, webhook reachability, render sign-off, SOURCING_ENABLED, and ENABLE_LIVE_SEND.
