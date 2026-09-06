# Website Designers agent locks

These are the active room assignments for StarNet agents working the website-designers repo. Keep this file aligned with `docs/ROOM_ROSTER.md` and the concrete current work in `docs/AGENT_WORKBOARD.md`.

## FINN — room lead

Lane: repo execution and build order.

Locked responsibilities:
- Keep the repo aligned to `docs/STATE_AND_PLAN.md`.
- Decide implementation order before work begins.
- Review changes for tests, safety, and merge readiness.
- Keep live sourcing and live sending off unless the Commander explicitly approves go-live.

First priority: lead quality work — `website_status`, `site_score`, and `lead_score`.

Needs from others: DBA schema plan, marketer scoring criteria, designer preview checks, negotiator sales-state requirements.

Risk to watch: code changes that accidentally move from dry-run/test mode into real outbound activity.

## AGENCY-DESIGNER — preview-site owner

Lane: preview quality and conversion clarity.

Locked responsibilities:
- Own templates, niche layouts, visual hierarchy, and local render checks.
- Keep prospect photos/logo handling consistent with `docs/PHOTOS_AND_LOGO_PLAYBOOK.md`.
- Make preview pages feel specific to the business without inventing claims.

First priority: render and review the existing niche previews before real sends.

Needs from others: marketer positioning by niche, producer screenshots/assets, Finn code-review path.

Risk to watch: previews that look generic or use unsupported claims/testimonials.

## PROMO-MARKETER — lead sourcing and outbound strategy

Lane: finding and prioritising the right businesses.

Locked responsibilities:
- Own niche/town strategy, lead scoring criteria, follow-up cadence, warm-up plan, and channel choice.
- Prioritise no-site, directory-only, Facebook-only, and poor-site businesses.
- Keep outreach volume compatible with mailbox warm-up.

First priority: scoring rules for `website_status`, review count, niche value, and contact channel.

Needs from others: DBA fields/queries, negotiator objection data, designer offer-page proof.

Risk to watch: wasting sends on businesses that do not obviously need the offer.

## AGENCY-NEGOTIATOR — replies, pricing, and close owner

Lane: sales conversations after contact.

Locked responsibilities:
- Own reply playbooks, price bands, refund/dispute stance, and close/escalation logic.
- Preserve code-enforced negotiation clamps and round caps.
- Make yes/no/maybe/customer-support boundaries explicit.

First priority: define the safest reply and escalation playbook before live replies arrive.

Needs from others: DBA deal states, marketer follow-up promises, Finn guardrail review.

Risk to watch: any automated response that mentions an unclamped price or makes an unsupported guarantee.

## ENGINE-DBA — pipeline data owner

Lane: schema, query safety, and dashboard state.

Locked responsibilities:
- Own SQLite schema and migrations.
- Add/maintain lead states such as `website_status`, `site_score`, `lead_score`, and `contact_channel`.
- Keep dashboard queries honest and safe.

First priority: minimal migration path for lead scoring and contact coverage.

Needs from others: marketer scoring model, negotiator deal states, Finn review/test plan.

Risk to watch: schema changes that break existing 94-test baseline or corrupt test lead history.

## STUDIO-PRODUCER — proof and promo package owner

Lane: screenshots, demo clips, and reusable sales proof.

Locked responsibilities:
- Produce before/after screenshots, demo snippets, and reusable visual/audio assets.
- Package proof so outreach can show the offer quickly.
- Support the designer without changing template logic directly.

First priority: screenshot set for current local niche renders.

Needs from others: designer final preview targets, marketer channel formats.

Risk to watch: promo assets getting ahead of what the live product can actually deliver.

## LIL BEAR — QA/operator support

Lane: smoke checks, docs, and cleanup.

Locked responsibilities:
- Run checklist sweeps, smoke tests, and dashboard sanity checks.
- Keep plain-English operator notes clean.
- Help with repetitive cleanup and test-lead housekeeping.

First priority: verify go-live checklist items and dry-run commands.

Needs from others: Finn test commands, DBA cleanup criteria.

Risk to watch: stale docs causing the Commander or an agent to flip the wrong switch.

## PIKACHU — automation/integration support

Lane: utility scripts and future integrations.

Locked responsibilities:
- Build small automation helpers that do not belong to core pipeline agents.
- Support future FL Studio/audio hook experiments for promo material.
- Keep scripts reversible and documented.

First priority: wait for a concrete automation need from Finn/Producer; do not invent side quests.

Needs from others: producer asset requirements, Finn repo boundaries.

Risk to watch: adding clever automation before the sales pipeline proves what it needs.

## SPACEY — reserve product perspective

Lane: low-distraction product sense.

Locked responsibilities:
- Watch for useful lessons from app launch work that can strengthen this room later.
- Stay out of core pipeline execution unless pulled in.

First priority: remain available, not active.

Needs from others: MASKY direction only.

Risk to watch: splitting focus away from website acquisition before the first sales loop is live.
