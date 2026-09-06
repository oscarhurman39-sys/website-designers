# Agency sales system

This is the first sellable version of the Website Designers agency pipeline. It gives the room agents a concrete offer, lead rules, outreach flow, reply handling, payment handoff, and go-live checklist without turning on real outbound by accident.

## Default offer

**Product:** a finished local-business website preview built before the first pitch, then handed over after payment.

**Promise:** "I already built the draft. Pay only if you want to own it."

**Included in the standard package:**

- One-page mobile-first website built from the relevant niche template.
- Real business name, town, phone, address, opening/contact CTA, Google rating/review count where available.
- One round of copy/design edits before handover.
- Customer photos and logo swapped in at no extra cost when supplied.
- Domain connection or handover guidance after payment.
- GitHub repo + Vercel project transfer where the client wants to own the stack.

**Not included by default:**

- Ongoing SEO retainers.
- Paid ads.
- Booking system integrations.
- Copywriting for multi-page brochure sites.
- Unlimited revisions.
- Hosting forever unless a hosting plan is sold separately.

## Pricing defaults

These match the current config defaults unless `.env` overrides them:

- Standard package anchor: `STANDARD_PACKAGE_PRICE=2000`.
- Pre-built draft offer: `WEBSITE_OFFER_PRICE=750`.
- Checkout price: `WEBSITE_PRICE=750`.
- Negotiation band: floor defaults to 80% of the offer price; ceiling defaults to the offer price.
- Currency: `CURRENCY=gbp` by default.

**Agent rule:** never quote or charge outside the code-clamped negotiation band. If a buyer asks for refunds, guarantees, subscriptions, hosting, legal terms, data processing, or anything beyond the one-page site, AGENCY-NEGOTIATOR escalates instead of improvising.

## Ideal customers

Start with local service businesses where a better site can plausibly increase calls:

- Plumbers, electricians, landscapers, salons, cafes, gyms, dentists, restaurants.
- Towns outside the home exclusion zone.
- Businesses that are operational and reachable.
- Businesses with a weak owned site, a platform-only presence, or no listed site plus a phone number.

Avoid:

- Businesses inside the configured home-patch exclusion radius.
- Chains/franchises with centralised marketing.
- Businesses with polished modern sites.
- Anything regulated enough that claims need professional review.
- Prospects with no lawful/reasonable contact path.

## Lead scoring v1

The first code-backed scoring slice is intentionally simple. Need comes first; reachability breaks ties.

| website_status | Meaning | site_score | contact_channel | lead_score |
| --- | --- | ---: | --- | ---: |
| `none` | Google lists no website | 0 | phone/email known | 10 |
| `none` | Google lists no website | 0 | unknown | 8 |
| `platform_only` | Facebook, Instagram, Linktree, Yell, Checkatrade, Google business profile, etc. | 0 | phone/email known | 9 |
| `platform_only` | Platform/directory only | 0 | unknown | 7 |
| `owned_site` | A normal website exists | blank until inspected | email | blank until inspection |
| `unscored` | Legacy/manual/unknown state | blank | unknown | blank |

Send ordering uses `lead_score DESC`, then older lead id. Blank scores go after scored leads.

## Outreach flow

### 1. Source

FINN/LeadSourcer adds leads through Google Places or CSV. Sourcing stays off unless `SOURCING_ENABLED=true` is explicitly set.

Required before a sourced lead is useful:

- Business name.
- Niche.
- Location.
- Website URL or phone/contact route.
- Place id where available.
- Website status and contact channel.

### 2. Research

LeadAgent scrapes normal owned sites for email, useful snippets, testimonial copy, and pain points. If no email is found, the lead can still be designed, but SalesAgent cannot email it until a usable address exists.

### 3. Preview

AGENCY-DESIGNER/DesignAgent builds a preview using the modern template. A preview must pass these checks before any pitch:

- Public URL loads without auth walls.
- Screenshot exists or the plain-text email still makes sense without it.
- Business facts are true: name, town, phone, address, rating, review count.
- CTA is visible on mobile.
- No fake testimonials, fake awards, or unsupported claims.
- If source data is thin, copy stays generic rather than pretending to know the business.

### 4. Pitch

SalesAgent sends the existing "I built a website for X" email only when `ENABLE_LIVE_SEND=true`; otherwise the system writes to dry-run logs. The first pitch should go to a small batch only.

Default first-batch cap:

- Week 1: 5-10/day from one warmed mailbox.
- Week 2: 10-15/day if replies/bounces are clean.
- Week 3: up to 20-25/day per warmed mailbox.

### 5. Reply handling

- Positive reply: negotiate inside the configured band, or ask for photos/logo if they want edits first.
- Price objection: counter once within band; if below floor, explain the lowest code-backed price and stop pushing.
- Asset submission: refresh preview, keep same lead state, reply with updated link.
- Payment question: send Stripe checkout link only for an agreed code-clamped price.
- Opt-out: suppress the address and stop contact.
- Complaint/refund/legal/data request: escalate to human.
- Ambiguous reply: treat as interested, but stay polite and bounded.

### 6. Payment and handover

Stripe Checkout is the payment path. After `checkout.session.completed`, the system can mark the lead won, then hand over the GitHub/Vercel project or trigger a human hosting/domain step.

Handover checklist:

- Payment event verified via Stripe webhook signature.
- Lead id extracted from Stripe metadata.
- Final quoted price matches code clamp and Stripe amount.
- Client email or GitHub username is present.
- Repo/Vercel transfer succeeds, or the failure creates a human alert.
- No project is torn down after the lead is won/transferred.

## Agent lanes

### MASKY

Owns orchestration, final go/no-go, and repo verification.

### FINN

Owns implementation safety. No scoring, send-queue, state, or config change ships without tests.

### AGENCY-DESIGNER

Owns preview conversion quality and truthfulness. Defects are filed with niche, viewport, expected/actual, and screenshot path.

### PROMO-MARKETER

Owns offer positioning and lead-source strategy. Can change copy proposals, not live send switches.

### AGENCY-NEGOTIATOR

Owns reply rules, price objections, refund/dispute escalation, and payment-language safety.

### ENGINE-DBA

Owns allowed states, schema compatibility, score fields, queue ordering, and migration safety.

### STUDIO-PRODUCER

Owns before/after clips and walkthrough scripts made only from real current previews.

### LIL BEAR

Owns operator QA: dry-run, checklists, and stale-command reporting.

### PIKACHU / SPACEY

Reserve agents for one-off research, QA, or automation once FINN/MASKY names a bounded task.

## Go-live checklist

Do not send real outreach until every line below is true:

- `.env` contains real GitHub, Vercel, email, Stripe, admin, sending-domain, physical-address values.
- `python pipeline/config.py` passes.
- `ENABLE_LIVE_SEND=false` dry-run has produced correct email bodies.
- `SOURCING_ENABLED=false` stays false until the first manual/small batch works.
- Stripe is intentionally in test or live mode; no ambiguity.
- Webhook public URL is live and reachable.
- Mailbox SPF/DKIM/DMARC are configured and warmed.
- The first batch is small enough to watch by hand.
- Dashboard/Slack alert path is watched.
- Full tests pass locally.

## First batch recipe

1. Source or enter 10 leads outside the exclusion zone.
2. Prefer `platform_only` and weak/no-site leads with a phone or email path.
3. Build previews.
4. Review each preview manually at desktop and mobile widths.
5. Run in dry-run mode and inspect `pipeline/dry_run.log`.
6. Turn on live send only for the reviewed batch.
7. Watch every reply and payment event.
8. After 10 real outcomes, adjust pricing/copy/scoring instead of guessing.
