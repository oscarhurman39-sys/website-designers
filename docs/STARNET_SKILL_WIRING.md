# StarNet skill wiring for Website Designers

This document wires the enabled StarNet web-design skills into the seated Agency-room agents and the repo process.

## Current skill state

| Skill | Status | How it is used |
|---|---|---|
| Make a Plan | active | Required before non-trivial repo, template, schema, payment, deploy, or process changes. |
| Creative Ideation | active | Used for bounded niche, offer, page-section, proof-asset, and outreach-angle ideas. |
| Popular Web Designs | active | Used by design agents for self-contained concept/prototype directions only; not direct production edits. |
| ASCII Art | active | Internal docs, logs, console labels, and operator morale only. |
| website_designers_pipeline | withheld | Needs Commander approval in Abilities > Skills before agents may load it. Do not guess, mirror, or recreate it. |

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
