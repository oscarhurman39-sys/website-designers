# State of the system and the plan forward

Written 2026-09-05 for whoever operates this repo next, human or agent. Read
this first, then `README.md`, then `docs/PHOTOS_AND_LOGO_PLAYBOOK.md`.
Branch: `claude/website-preview-email-upgrade-nz0s8a` (includes everything on
`main` plus the work below). Tests: `venv\Scripts\python.exe -m pytest -q`
(105 pass). Nothing has been sent to a real prospect yet.

## 1. What the system does today

A cold-email pipeline for selling pre-built websites to local businesses.

1. **Source leads** from Google Places (`run.py source`), skipping anything
   within 6 miles of Oxted, keeping open businesses that have a website (so a
   contact email can be scraped). Stores phone, address, rating, review count.
2. **Research** each lead: find the site, scrape a contact email, testimonial,
   pain point.
3. **Build a preview site** from `templates/modern`: photo-led, themed per
   niche, populated with the business's real phone, address, Google rating
   and map. No placeholder text, ever. Deployed to Vercel + a private GitHub
   repo. `python pipeline/render_preview.py` renders locally for review.
4. **Email** the preview from casey@caseywebsites.com (Zoho, EU). Offer: the
   finished draft at `WEBSITE_OFFER_PRICE`, anchored against
   `STANDARD_PACKAGE_PRICE`; their own photos/logo swapped in at no cost.
5. **Handle replies** automatically: classify, negotiate within a
   code-enforced price band, send a Stripe link on a yes, suppress on a no,
   save attached photos/logo and rebuild the preview in place.
6. **After payment**: invite the client to the repo and Vercel project.
7. **Housekeeping**: previews of leads that never replied are torn down after
   7 days (the email promises this); multi-mailbox rotation is ready for when
   volume needs it.

## 2. Infrastructure state

| Piece | State |
|---|---|
| Mail | Zoho Mail Lite (paid to 2027-09), smtppro/imappro.zoho.eu, IMAP on, DNS (MX/SPF/DKIM/DMARC) verified. Login test passes. |
| Public URL | ngrok static domain -> `webhook_server.py` :5000, only while `start_all.bat` (or `install_autostart.ps1`'s logon task) is running. VPS setup ready in `deploy/`. |
| Stripe | TEST keys; test-mode webhook endpoint created at the ngrok URL. Live keys needed before real money. |
| Google Places | New key, Places API (New) enabled, verified. |
| Database | `pipeline/leads.db`: 75 leads, ALL test data (Casey's own addresses). `run.py cleanup-tests --dry-run` lists 45 of them; the rest are older test names. A clean DB before real sourcing is recommended. |
| Live sending | `ENABLE_LIVE_SEND=false`. Every send is a logged dry run until flipped. |
| Sourcing | `SOURCING_ENABLED=false`. Run `run.py source --limit N` by hand first. |

Secrets live only in `.env` (gitignored). `.env.example` documents every key.

## 3. Commands

```
python run.py source --dry-run --limit 10   # preview what sourcing would add
python run.py source --limit 10             # insert 10 real leads
python run.py loop                          # the pipeline (research/design/send/replies)
python run.py dashboard                     # Streamlit view of leads and threads
python run.py test-email you@x.com          # end-to-end on a dummy lead
python run.py rebuild <lead_id>             # redeploy a preview (new photos/logo/template)
python run.py cleanup-tests [--dry-run]     # delete test leads + their repos/projects
python pipeline/render_preview.py           # render every niche locally, screenshots in pipeline/out
python pipeline/utils/teardown.py --dry-run # what the 7-day teardown would remove
start_all.bat                               # webhook server + ngrok + pipeline supervisor
```

Go-live checklist is in `README.md`.

## 4. The honest constraint

Nothing guarantees a sale. What we can do is stack the odds: pick businesses
that visibly need a site, that can afford one, that we can actually reach,
and show them something that already looks like theirs. Right now we are
strong on the last point and weak on the first three. The plan below is
ordered by how much each step moves conversion.

## 5. Plan: from "sends emails" to "closes the right clients"

### 5.1 Know for certain whether they have a website

Today "no website on Google" is treated as "can't reach them" and skipped.
Flip that: businesses with **no site or a bad site** are the target, and the
system should be sure which it is. Add a `website_status` per lead:

- `none`: no `websiteUri` on Places, and a name+town search (DuckDuckGo,
  already used by the researcher) returns no domain that matches the name.
- `facebook_only` / `directory_only`: the "website" is a Facebook page, Yell,
  Checkatrade, Nextdoor. These are ideal targets and are currently discarded.
- `poor`: a real site that fails cheap checks: no HTTPS, no mobile viewport
  meta, copyright year 3+ years old, page weight over 5 MB, title tag
  missing. Score 0-10.
- `good`: everything else. Low priority.

Work: ~a day. Touches `sourcing_agent`, `lead_agent`, a `leads.website_status`
+ `site_score` column, and the email copy (a "your Facebook page is doing
the work of a website" opener beats the generic one).

### 5.2 Reach the people who have no website

The reason no-site businesses were skipped: no email to scrape. Options,
best first:

1. **Phone number is already collected.** A short call script for a human,
   or the `twilio` plugin already installed in this environment for SMS.
   B2B calls are legal in the UK if the number is screened against CTPS;
   SMS to a sole trader's mobile needs consent, so SMS is for limited
   companies only. Screen with a Companies House lookup (free API).
2. **Facebook page "About" email.** Many small businesses list one. Fetch
   is unreliable (bot walls); worth a best-effort scrape with a fallback.
3. **Directory listings** (Yell, Checkatrade) frequently expose an email.
4. **A physical letter with a QR code** to the preview. Slow, but nobody
   else does it, and the preview is the whole pitch.

Work: 1 and 3 are a few days; they need a `contact_channel` on the lead and
a second outbound path beside `send_cold_email`.

### 5.3 Score leads and send the best first

A single `lead_score` combining: website_status (none/facebook > poor > good),
review count (busy = budget), rating (reputation to protect), niche margin
(dentists and gyms pay more than cafes), and distance (further from home is
fine, but keep it within a drive for the domain-connection step if you ever
visit). Sort the send queue by score; cap sends per day but never waste a
slot on a score-2 lead while score-9 leads wait. Half a day.

### 5.4 Search further afield, deliberately

Sourcing already takes a list of towns. Add radius rings: 15-30 miles for
the first month, then widen. Different county, same template, no social
awkwardness. Add towns with weak web presence (market towns beat commuter
towns). Trivial config; the useful part is tracking reply rate per town in
the dashboard so the list tunes itself.

### 5.5 Make the offer easier to say yes to

- Hosting model: lower upfront plus a monthly fee (hosting, edits, domain).
  Recurring revenue makes low-balling work; it also keeps the client, which
  makes the photos/logo interaction the start of a relationship rather than
  a one-off. Config + copy change.
- "Pay when you're happy": they already only pay after seeing the site.
  Say it out loud in the email.
- A second email 3 days later to non-openers with a different subject, then
  stop. The follow-up is not built yet; it's the single cheapest lift in
  cold email.

### 5.6 Trust and warm-up (do not skip)

- Warm up the mailbox: 5-10/day week one, 20 by week three. Add a second
  domain + mailbox before going past 25/day (`EMAIL_ACCOUNTS`).
- Watch the first ten real replies by hand via Slack alerts / dashboard.
- Move DMARC to `p=quarantine` after a month of clean sending.

## 6. Questions for the operating agent

Answers to these change what gets built next. Reply in this file or in a
`docs/AGENT_NOTES.md`.

1. Which contact channel for no-website businesses are you willing to run:
   phone calls (human), SMS to limited companies, letters, or email-only?
   This decides whether 5.2 is worth building.
2. Price model: keep the one-off at 750, or move to an upfront + monthly
   hosting fee? What monthly figure feels right for the area?
3. Niches: any to drop or add? (Dentists and gyms are higher value but slower
   to decide; plumbers and electricians reply fastest.)
4. Radius: is 15-30 miles from Oxted the right first ring, or go further
   (Kent coast, Sussex)?
5. Follow-up email: one reminder after 3 days, or none? Any wording rules?
6. What should "guarantee" mean in the pitch: money-back within 14 days,
   free edits for 30 days, or nothing beyond pay-after-seeing-it?
7. Anything in the current template you'd change before the first real
   sends? Render it with `python pipeline/render_preview.py` and look at
   `pipeline/out/*.png`.

## 6a. Decisions applied on 2026-09-06 (Commander, via StarNet)

| Decision | Where it lives |
|---|---|
| Email only; no SMS/calls/letters until revenue | Section 5.2 shelved. `contact_channel` is still recorded per lead for later. |
| £589 one-off (engine works in whole pounds, so not 589.99) | `.env`: `WEBSITE_PRICE`, `WEBSITE_OFFER_PRICE`, `NEGOTIATION_CEILING=589`, `NEGOTIATION_FLOOR=470` |
| Monthly plan as a secondary option, £39/month stub | `SUBSCRIPTION_ENABLED`, `SUBSCRIPTION_MONTHLY_PRICE`; quoted in the cold email and negotiation prompt; a "monthly" reply gets a warm holding reply + human alert, never an automated close |
| Keep niches broad, log outcomes | `won_amount` stored from the Stripe webhook; `python run.py report` gives leads/emailed/replied/won/revenue per niche |
| Kent + Sussex first | `SOURCING_LOCATIONS` = 26 towns across Kent, East and West Sussex; 6-mile Oxted exclusion stays |
| One follow-up email | `sales_agent.send_follow_up_if_due()` in the main loop: 3 days after the cold email, silent leads only, once, counts against send caps |
| Money-back + free edits | `GUARANTEE_DAYS=14`, `FREE_EDITS_DAYS=30` in every cold email, follow-up and the negotiation prompt |
| Commander reviews visuals before launch | `ENABLE_LIVE_SEND=false`, `SOURCING_ENABLED=false` unchanged. Renders: `python pipeline/render_preview.py` -> `pipeline/out/*.png` |

## 6b. Five questions StarNet should confirm before the first live batch

1. **Which mailbox and how many a day?** Confirm the loop will run from
   casey@caseywebsites.com only, `EMAIL_MAX_PER_DAY_PER_ACCOUNT` set to 10 for
   week one, and that `install_autostart.ps1` (or the VPS) keeps
   `webhook_server.py` + ngrok up the whole time, because every email
   carries an unsubscribe link that dies when they are down.
2. **Is the database clean?** All 75 leads are test data. Confirm
   `run.py cleanup-tests` has been run (or the DB replaced) before
   `run.py source`, otherwise the first live cycle emails 28 old test
   leads at your own addresses and the report is polluted from day one.
3. **Who is on the first ten replies?** The classifier, negotiator and the
   new monthly-plan escalation have only seen synthetic replies. Confirm a
   human (or MASKY) watches Slack alerts and the dashboard for the first
   ten real replies and has the `pause` console command ready.
4. **Stripe live switch owner.** Test mode cannot take money. Confirm who
   swaps `STRIPE_SECRET_KEY` to live, creates the live webhook endpoint at
   `PUBLIC_BASE_URL/webhook/stripe`, puts its signing secret in
   `STRIPE_WEBHOOK_SECRET`, and re-runs `tests/test_stripe_money_pipe.py`,
   and that this happens before the first `CLOSE`, not after.
5. **Visual sign-off is recorded.** Confirm the Commander has looked at
   `pipeline/out/_heroes.png` and one full page per niche they intend to
   sell, and that any requested change is in `NICHE_THEMES` before
   `ENABLE_LIVE_SEND=true`. The template is the pitch; nothing else in the
   email matters if the page looks generic.

## 6c. StarNet skill wiring applied on 2026-09-06

The agency room now has explicit StarNet skill routing in `docs/STARNET_SKILL_WIRING.md`:

- `Make a Plan` gates non-trivial repo, template, payment, schema, deploy, and process changes.
- `Creative Ideation` feeds bounded niche, offer, copy, proof, and page-section concepts.
- `Popular Web Designs` is limited to design/prototype directions until render audit and FINN review.
- `ASCII Art` is internal/operator-facing only.
- The withheld saved `website_designers_pipeline` skill still needs Commander approval in `ABILITIES > SKILLS`; no agent may infer or recreate it.

This wiring changes agent process only. It does not enable live sourcing, live sending, Stripe live mode, or prospect contact.

## 7. Known gaps

- Attachment flow tested against constructed emails only; first real
  photo reply should be watched.
- Stripe is test mode. `_finalize_won_leads` hands over the repo and Vercel
  project; under a hosting model that step changes (keep hosting, add their
  domain instead).
- No follow-up email yet. Lead scoring and `website_status` now have a v1 code-backed slice for no-site/platform-only sourcing and send-priority ordering; the next gap is owned-site quality scoring after actual render/reply outcomes.
- The Gmail connector in this workspace lacks read permission, so inbox
  placement of test sends was not verified from here.
