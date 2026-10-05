# Send-volume gate assessment — 2026-10-04

## Verdict

**Do not increase send volume now.** The live offline preflight is technically `GO (ARMED)`, but it reports three material warnings: unattended sourcing while live sending is armed, caps above the documented week-one warm-up range, and 17 queued leads whose existing sites already score healthy (plus 4 unaudited). More importantly, the observed funnel is not yet proving that more volume is useful: the weekly review records 1 reply from 133 emailed leads overall (0.8%), no sales, and 40 queued healthy-site leads; the repo also records that the current Zoho policy is incompatible with promotional/bulk/automated cold outreach. The safe recommendation is to hold at the existing 15/day ceiling, and preferably pause unattended sending until targeting, legal classification, and monitoring gates below are satisfied.

This assessment changes no settings and sends no mail.

## Evidence and gate criteria

- **Current live preflight:** `venv\\Scripts\\python.exe run.py preflight --offline` returned `GO (ARMED)` with `ENABLE_LIVE_SEND=true`, `SOURCING_ENABLED=true`, global/per-mailbox caps `15/15`, 21 designed leads, all 21 held by the five-day niche+town cooldown, 4 queued leads not audited, 17 queued leads with healthy sites, and 1 suppressed address. It explicitly warns that sourcing is running unattended while armed and that the caps exceed `WARMUP.md`'s week-one 5–10/day guidance. Source: current command output, 2026-10-04; repo docs `docs/STATE_AND_PLAN.md` and `WARMUP.md`.

- **No evidence of positive unit economics yet:** `docs/STRATEGY.md` reports 1 reply from 133 emailed leads (0.8%) and 0 sales; the same review says healthy-site leads were consuming queue space and recommends stopping sends to existing sites scoring >=8. This fails a sensible scale gate: first demonstrate a materially better, reviewed segment rather than multiply an unproven baseline. Source: `docs/STRATEGY.md` (weekly sales review in the repository; date in file header).

- **The present cap is not the same as a safe entitlement:** `docs/STATE_AND_PLAN.md` records `EMAIL_MAX_PER_DAY=15` and `EMAIL_MAX_PER_DAY_PER_ACCOUNT=15`, with a hard-coded 20/hour limit and 2–5 minute gaps. It also records that only Oscar changes caps. `WARMUP.md` says caps protect the pipeline's pace but do not warm a new domain, recommends gradual growth, and says to reduce or pause if bounce/complaint/reputation signals worsen. Gate: no increase without a documented, observed warm-up history and stable delivery metrics for the current level. Sources: `docs/STATE_AND_PLAN.md`; `WARMUP.md`.

- **Provider-policy gate:** `docs/research/mass-plumber-campaign-plan.md` cites Zoho's Usage Policy as excluding promotional, bulk/mass, marketing, newsletter, and automated email. `docs/MASKY_ROUND2_REPLY.md` therefore recommends staying around 15/day only while hand-watched and moving cold sends off Zoho before scale. Gate: do not scale the current Zoho mailbox for this campaign; obtain a provider/domain arrangement whose current terms explicitly permit the intended use, then re-check the terms before ramping. Source: `docs/research/mass-plumber-campaign-plan.md`, sources section `[Z1]`; `docs/MASKY_ROUND2_REPLY.md`.

- **UK legal classification gate:** ICO guidance says PECR rules apply to electronic-mail direct marketing and distinguish corporate subscribers from individual subscribers. The repo's legal research states that limited companies/LLPs/corporate subscribers are treated differently from sole traders and ordinary partnerships, and that a public/scraped address is not itself consent. Gate: maintain auditable evidence of the recipient's legal form before sending; absent positive corporate evidence, do not cold-email the address. Prefer role addresses; retain identity, postal address, and working opt-out/suppression controls. Sources: ICO, “Business-to-business marketing,” https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/ ; ICO, “How do we comply with the PECR electronic mail marketing rules?”, https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guidance-on-direct-marketing-using-electronic-mail/how-do-we-comply-with-the-pecr-electronic-mail-marketing-rules/ ; repo `docs/research/pecr-and-companies-house.md` and `docs/research/companies-house-screen.md`.

- **Content/compliance controls are necessary but not sufficient:** the current preflight confirms price, physical address, sender identity, unsubscribe link, and monthly option in the cold email. Repo review also identified a historical one-click unsubscribe POST defect and operational dependence on the public URL for unsubscribe links; therefore gate volume on a live end-to-end unsubscribe test, suppression read-back, and continuous public URL health. Sources: current preflight output; `docs/FINAL-CHECK-2026-09-10.md`; `docs/STATE_AND_PLAN.md`.

- **Queue-quality gate:** before any increase, every candidate in the next cohort should have a completed site audit, a lead score, a legally screened recipient classification, a distinct/business-specific defect, a validated preview URL, and no same-niche/same-town cooldown collision. The current preflight fails this because 4 are unaudited and 17 are healthy-site candidates likely to waste slots. Source: current preflight output; `docs/AGENCY_SALES_SYSTEM.md`; `docs/AGENT_WORKBOARD.md`.

## Safe recommendation

1. **No cap increase and no additional mailbox/domain yet.** Keep the existing 15/day maximum as a hard ceiling; do not treat it as a target.
2. **Do not let unattended sourcing + sending run as-is.** Require a reviewed cohort and human watch of replies, bounces, complaints, suppressions, and unsubscribe handling.
3. **Fix the gate before adding volume:** complete site audits, park healthy existing sites where the offer is not justified, and add a Companies House/legal-form screen so sole traders and ordinary partnerships are not treated as safe corporate recipients.
4. **Resolve provider fit before scale:** Zoho is documented in the repo as prohibiting the campaign category. A different provider is not automatically safe; its terms and anti-abuse rules must be checked and the sending setup authenticated.
5. **Define a measured scale gate:** only consider a modest increase after a clean, manually reviewed cohort at the current level shows stable delivery, low bounce/complaint rates, working opt-outs, no provider warnings/blocks, and improving reply/qualified-reply evidence. The repo's own research proposes illustrative thresholds of bounce <2% and complaints <0.1%; treat these as operational guardrails, not legal safe harbours. Source: `docs/research/mass-plumber-campaign-plan.md` and `WARMUP.md`.

## Could not confirm

- I could not independently inspect Zoho's live policy page in this run because the web fetch route failed; the policy conclusion above is based on the repo's dated citation to Zoho's official Usage Policy and should be rechecked before any provider decision.
- I could not verify current mailbox-provider reputation, Gmail/Outlook placement, complaint rate, or bounce rate from live provider dashboards.
- `preflight --offline` deliberately skipped public URL, mailbox login, GitHub, and Slack probes; this is not a full live-readiness proof.
- The repo contains time-stamped snapshots from September 2026 and the current preflight from October 4, 2026; where they conflict, the current preflight is authoritative for the present flags, while the historical funnel remains evidence about observed outcomes.

## Confidence

**High** that increasing volume now is not justified. **Medium** on the exact legal treatment of any individual address because that depends on recipient legal form, address type, and the controller's documented UK GDPR/PECR assessment. **Medium** on provider-policy risk because the repo cites the official Zoho policy but the live page was not fetched in this run.
