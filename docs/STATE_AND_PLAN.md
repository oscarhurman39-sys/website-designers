# State of the system and the plan forward

Written 2026-09-05 for whoever operates this repo next, human or agent. Read
this first, then `README.md`, then `docs/PHOTOS_AND_LOGO_PLAYBOOK.md`.
Branch: `claude/website-preview-email-upgrade-nz0s8a` (includes everything on
`main` plus the work below). Tests: `venv\Scripts\python.exe -m pytest -q`
(94 pass). Nothing has been sent to a real prospect yet.

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

## 7. Known gaps

- Attachment flow tested against constructed emails only; first real
  photo reply should be watched.
- Stripe is test mode. `_finalize_won_leads` hands over the repo and Vercel
  project; under a hosting model that step changes (keep hosting, add their
  domain instead).
- No follow-up email, no lead scoring, no website_status yet (section 5).
- The Gmail connector in this workspace lacks read permission, so inbox
  placement of test sends was not verified from here.
