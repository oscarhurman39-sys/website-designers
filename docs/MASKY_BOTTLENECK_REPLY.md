Lane: MASKY / bottleneck diagnosis. 
 
Files changed: docs/MASKY_BOTTLENECK_REPLY.md only. No .env, sourcing, send, Stripe, price, or mailbox switch touched; no email sent. 
 
What I found: 
1. Cooldown is the active brake. DB query over leads + state_history shows 51 designed leads in 13 niche+town buckets, and every bucket had a cold email on 2026-09-18 or 2026-09-19. With OUTREACH_COOLDOWN_DAYS=5, 0 buckets are eligible on 2026-09-20, 0 on 09-21/22, 3 on 09-23, then 13/day from 09-24. Current config is 44 towns x 8 niches = 352 theoretical buckets, but live sourcing has concentrated the sendable queue into 13 buckets, so the real allowance is 0-13/day until sourcing spreads. Cheapest safe fix: add/spread towns before shortening cooldown. To reliably hit 15/day with a 5-day cooldown, keep at least 75 active niche+town buckets with mailable leads; today has 13, so it needs about 62 more active buckets. 
 
2. Vercel previews are not currently failing the send-time check. I ran sales_agent._validate_preview_link_for_send against all active/not-torn-down website rows: 81 checked, 81 OK, 0 failed. History contains old Design failed rows: leads 1,2,5,7,8,9,10 repeatedly hit Vercel 404 on 2026-09-08 before later unique URLs worked; lead 101 has one blank Design failed note on 2026-09-15 but its current preview validates. Cause: old Vercel deployment propagation/stale URL failures, now self-healed. Fix: no emergency patch; keep the existing refresh/rebuild path. 
 
3. This week, Oscar-only decisions: 
- Buy a VPS now: Hetzner CX23 if available, otherwise DigitalOcean 2GB London, and create track.caseywebsites.com + Stripe endpoint. 
- Choose targeting: keep the healthy-site gate and accept low volume, or deliberately chase no-site/platform-only leads next. 
- Choose scale posture: stay around 15/day on current Zoho/Vercel, or commit budget for Workspace mailboxes + warm-up + Pro/static previews. 
 
Proof commands: DB cooldown query; preview validation command (81 OK / 0 FAIL); read docs/research/mass-plumber-campaign-plan.md and vps-move-runbook.md. Remaining risk: 0 replies from 30 means scale should wait until the target list improves. 
