# Mass plumber campaign: one plumber per town across England

Research note, 2026-09-20. Read-only against the live repo; no config was changed, nothing was run. Every external number carries a URL and was read on 2026-09-20 (list at the end). Money is quoted in the source's currency; GBP totals assume £1 ≈ $1.30 ≈ €1.17.

## Headline

- England has **458 built-up areas of 20,000+ people** (173 of 50,000+). One plumber per town per day therefore caps sourcing at ~458 leads/day.
- Sourced plumber to sendable cold email is **~22%** with the site-quality gate off (50% scrapable email × ~45% limited companies) and **~3%** with the gate at its default of 8 (43 of 47 audited sites scored "healthy" on 2026-09-20). **Gate on, all 458 towns yield ~12 emails/day.** Volume needs `SITE_SCORE_GOOD_AT=0`, a Commander decision reversing the 2026-09-20 change.
- **100 cold/day** = ~450 sourced/day, 8 warmed mailboxes on 3–4 domains, ~£190–£520/month (the spread is Places). **300/day** = ~1,350 sourced/day (3 per town, not 1), 24 mailboxes on 8–12 domains, ~£650–£1,500/month, not reachable before week 7 if warm-up rules hold.
- The universe is small: ~15–20k England plumbers surface through Places, so **~4–6k emailable Ltd plumbers in total**: 2–3 months at 100/day, under three weeks at 300/day. 300/day only makes sense as a multi-niche machine.
- Two code facts block this today: the sourcing walk restarts from the same bucket every hourly run (it re-mines ~30 towns instead of touring England), and Vercel Hobby (100 deploys/day, 200 projects, non-commercial) cannot host 100 previews/day.

## (a) Target maths

Assumptions, to be replaced by measured values: email found on 50% of sourced leads (`docs/STATE_AND_PLAN.md` §2); 45% pass the Ltd filter (§e); gate off; one preview per email; one follow-up after 3 days to the ~95% who do not reply (`FOLLOW_UP_ENABLED`, counts against the caps); replies 1–3% (industry range; the pipeline's own figure is 0/30); **10% of replies close** (a third positive, a third of those pay; stated, not measured); £589 per sale.

| | 100 cold/day | 300 cold/day |
|---|---|---|
| Towns walked/day × plumbers per town | 450 × 1 | 450 × 3 (or ~900 towns × 1.5 if a 10k+ tier is added) |
| Sourced leads/day (`SOURCING_DAILY_LIMIT`) | 450 | 1,350 |
| After email scrape (50%) | 225 | 675 |
| After Ltd filter (45%) = previews/day | 100 | 300 |
| Sends/day incl. follow-ups | ~195 | ~585 |
| Mailboxes at 25/day | 8 (4 without follow-up) | 24 (12 without) |
| Replies/day at 1–3% | 1–3 | 3–9 |
| Replies/month (22 send days) | 22–66 | 66–198 |
| Sales/month at 10% of replies | 2–7 (£1.2k–£3.9k) | 7–20 (£3.9k–£11.8k) |
| Days until England plumbers (≈4–6k emailable) run out | 40–60 | 13–20 |
| Places requests/month | ~16k at 1/town (PER_BUCKET=1) | ~16k at 3/town; ~49k at 1/town |

Places cost: `sourcing_agent.PLACES_FIELD_MASK` asks for `websiteUri`, `nationalPhoneNumber`, `rating`, `userRatingCount`, which are Text Search **Enterprise** fields: **$35 per 1,000 requests, 1,000 free/month** [G1][G2]. One page of 20 results is one request, so `SOURCING_PER_BUCKET=1` pays a full request per lead and discards 19; cost scales with 1/PER_BUCKET. At 1/town: (16,200 − 1,000) × $0.035 ≈ **$530/month**; 3/town for 300/day is the same; 1/town for 300/day ≈ **$1,680/month**. Once a town's first page is all in the DB each visit costs 2–3 requests (`MAX_PAGES_PER_QUERY=3`) and nothing remembers exhausted buckets, so the bill climbs unless (d)1 is built. Confirm in the console that no mask field lands in Enterprise + Atmosphere (one reading of the SKU page puts `businessStatus` there).

Request budget: 24 hourly runs × `SOURCING_MAX_REQUESTS_PER_RUN`; each run's lead budget is `remaining_today`. 450 leads at ~1.2 requests each needs ≥23/run, so 40 is enough; 1,350 needs ~70 (set 80).

## (b) Infrastructure at each level

**Towns.** ONS 2021 built-up areas in England, counted from the Wikipedia list's tables [T1] (ONS defines the classes but publishes no per-class count [T2]): **26 ≥200k, 112 ≥75k, 173 ≥50k, 296 ≥30k, 458 ≥20k.** The list ends at 20,005 (Hazlemere); no 10–20k count was found, so a tier 4 is unverified and not needed until 300/day. Greater London is one 8.78M row and must be split into its 33 ONS built-up areas.

`SOURCING_LOCATIONS` keeps today's format (semicolon-separated `Town, County`, as the name/County columns of [T1] give them), held as three env values the operator concatenates:

- Tier 1 (≥50k, 172 towns + ~33 London areas ≈ 205 buckets): `Crewe, Cheshire; South Shields, Tyne and Wear; ...`
- Tier 2 (30–50k, 123): `Witney, Oxfordshire; Congleton, Cheshire; ...`
- Tier 3 (20–30k, 162): `Harwich, Essex; ... Hazlemere, Buckinghamshire`

Tier 1 alone yields ~45 cold emails/day; all three ~100/day. The 6-mile Oxted exclusion still applies.

| Level | Mailboxes / domains | Warm-up | Preview hosting | Places | Monthly cost |
|---|---|---|---|---|---|
| Today (15/day) | 1 Zoho | none | Vercel Hobby + GitHub | ~$0 | ~£3 |
| 100/day | 8 on 3–4 domains (2–3 per domain) | 3–4 weeks per mailbox; tool-driven | VPS + Caddy (or Vercel Pro) | ~16k req | Workspace 8 × £5.90 = £47 [M1]; domains ~£3; Instantly Growth $47 for warm-up only [W1]; Hetzner CX23 €5.49 [H1] or Vercel Pro $20 [V1]; Places $530 (1/town) or ~$110 (5/town). **≈ £190–£520** |
| 300/day | 24 on 8–12 domains | as above, staggered | VPS + Caddy | ~16k (3/town) – 49k | Workspace £142; domains ~£8; Instantly Hypergrowth $97 [W1]; Hetzner €5.49; Places $530–$1,680. **≈ £650–£1,500** |

Mail provider. Zoho Mail's usage policy: the service "cannot be used for sending out emails in the following categories: Promotional emails, Bulk or Mass emails, Marketing emails, Newsletters, Automated Emails", no refund on suspension [Z1]; the current mailbox is already outside it. Google Workspace's AUP also bans "unsolicited mass email" [M2], but Workspace is what every cold-email tool assumes; its technical limit is 2,000 messages/user/day (500 on trial) [M3], the practical ceiling is reputation. Tools: Instantly ($47/$97/$358, unlimited mailboxes and warm-up) [W1], Smartlead ($39/$94/$174/$379) [W2], lemlist ($55–$87 per seat, lemwarm included) [W3], Mailreach ($19.50 per mailbox/month, warm-up only) [W4]. Instantly's terms put legality on the subscriber and do not ban cold email [W5]; Smartlead bans "unsolicited commercial electronic messages ... as defined by Spam Laws" (PECR does not make B2B email to a corporate subscriber unlawful) [W6]; lemlist permits compliant outbound [W7]. Recommendation: Workspace mailboxes, Instantly for warm-up only, the pipeline keeps sending over SMTP; swapping the sender for a tool would also swap reply polling and thread stickiness.

SendGrid prohibits "unsolicited or unwanted emails in bulk" and "email addresses that you obtained from the Internet ... without obtaining prior affirmative consent" [S1]: `send_email_sendgrid` is **not usable** for cold outreach, receipts only, as `config.py` says.

Per domain before the first send: SPF, DKIM, DMARC (`p=none` for a month, then `quarantine`), Postmaster Tools. Thresholds: Gmail spam rate under 0.10%, never 0.30% [D1]; Microsoft complaints under 0.3% (0.1% target), bounces under 2% [D2]. Verify addresses before sending (`utils/email_verify.py` exists).

Preview hosting. Vercel Hobby: **200 projects, 100 deployments/day**, "restricted to non-commercial, personal use only", where commercial includes "Receiving payment to create, update, or host the site" [V1][V2]. At 100/day the deploy cap has no headroom for `design_agent`'s rebuild/refresh paths, and 7-day TTL × 100/day = 700 live projects, over the cap by day 2. Pro (6,000 deploys/day, unlimited projects) is $20/seat/month. GitHub Free: unlimited private repos [GH1]; the binding limit is 500 content-creating requests/hour, 80/minute [GH2], and `push_files` commits one file per request, ~5 per preview, fine at 300/day. Cloudflare Pages free: 100 projects, 500 builds/month, Direct Upload without git [C1][C2], but wildcard custom domains are not documented [C3], so previews would be paths in one project with whole-site deploys; awkward. **VPS + Caddy** is the simple option: Hetzner CX23 (2 vCPU/4 GB/40 GB, 20 TB traffic) €5.49/month [H1]; Caddy serves `*.preview.<domain>` from one wildcard certificate via the DNS challenge (DNS-provider plugin required) [K1]; a preview is a folder. `deploy/` already plans to run the loop and webhook on such a box, which also fixes PC uptime.

## (c) Config changes by name

`Commander` = only Oscar changes it, in the conversation where he says so (`docs/STATE_AND_PLAN.md` §8).

| Name | 100/day | 300/day | Who | Note |
|---|---|---|---|---|
| `SOURCING_NICHES` | `plumber` | `plumber` (then `plumber,electrician`) | Commander | niches were a 2026-09-06 decision |
| `SOURCING_LOCATIONS` | tiers 1–3 (~490 buckets) | tiers 1–3, add a 10k tier if built | operator | format above |
| `SOURCING_PER_BUCKET` | `1` | `3` | operator | 1 = one plumber per town per run; cost ∝ 1/value |
| `SOURCING_DAILY_LIMIT` | `450` | `1350` | Commander | spends Places money |
| `SOURCING_MAX_REQUESTS_PER_RUN` | `40` | `80` | Commander | hard cap on the bill: 960 / 1,920 requests/day worst case |
| `SOURCING_REQUIRE_WEBSITE` | `true` | `true` | — | no website = no email |
| `SITE_SCORE_GOOD_AT` | `0` | `0` | Commander | keeps scoring for best-first order, stops parking healthy sites |
| `EMAIL_ACCOUNTS` | 7 extra (8 total) | 23 extra | Commander | `user:password:smtp_host:imap_host` per entry; mixed providers work |
| `EMAIL_MAX_PER_DAY` | `200` (100 + follow-ups) | `600` | Commander | global |
| `EMAIL_MAX_PER_DAY_PER_ACCOUNT` | `25` | `25` | Commander | never higher; add mailboxes instead |
| `OUTREACH_COOLDOWN_DAYS` | `0` | `0` | operator | moot: with ~490 buckets and one per town, a town repeats every ~5 days at 100/day; at 3/town/day any value >0 blocks sends |
| `FOLLOW_UP_ENABLED` | `true` | decide | Commander | it doubles sends and mailbox count |
| `DESIGN_BACKLOG_MAX` | `0` (= 2 × cap = 400) | `0` (1,200) | operator | previews expire at `PREVIEW_TTL_DAYS=7` |
| `SEND_WINDOW_*` | 08–18, Mon–Fri once on the VPS | same | Commander | B2B hours |
| `PREVIEW_TTL_DAYS` | `7` | `7` | — | live previews = 7 × daily previews |

Hard-coded, not env, and binding at 300/day: `EMAIL_MAX_PER_HOUR = 20`, `EMAIL_MIN/MAX_DELAY_SECONDS = 120/300`, `SOURCING_INTERVAL_SECONDS = 3600`. 195 sends × ~3.5 min = 11.4 h fits the 14-hour window; 585 sends do not (34 h). See (d)3.

## (d) Code changes

1. **Sourcing cursor (small, required).** `sourcing_agent._rotation_offset()` is the UTC day ordinal: every hourly run starts at the same bucket and tomorrow starts one bucket later, so the day's 24 runs take plumber 1, 2, 3… from the same ~30 towns and town 300 is reached in ten months. Persist a cursor advanced by buckets consumed, and skip a bucket for N days once all its pages are "already in DB" instead of paying 3 requests per visit.
2. **Preview hosting swap (medium, required above ~60/day).** New `utils/preview_host.py` behind `PREVIEW_HOST=vercel|static` and `PREVIEW_BASE_DOMAIN`. In `design_agent._process_lead_impl`, `rebuild_preview` and `_refresh_existing_preview`, replace `github_api.create_repo_with_files` + `vercel_api.deploy_files` with an upload of `build_site_files(lead)` to `/srv/previews/<slug>/` (SFTP or a token-protected PUT); `preview_url = https://<slug>.preview.<domain>`. `_validate_deployment_url` and the screenshot are URL-based and stay. `teardown._teardown_one` deletes the folder and already skips GitHub when `repo_full_name` is NULL. The GitHub repo is created only in `main.py`'s `_finalize_won_leads`, re-rendered from the DB and `utils/assets.py` (`build_site_files` is deterministic). ~1–2 days with Caddy config and tests.
3. **Send pacing (small, required at 300/day).** Make `EMAIL_MAX_PER_HOUR` and the delay bounds env-configurable and apply the delay **per mailbox** in `sales_agent`, so 24 mailboxes each keep a human 2–5 minute gap instead of the whole pipeline sending one message every 3.5 minutes.
4. **Multi-mailbox rotation: already works.** `mailboxes.least_loaded_account` spreads new threads across `EMAIL_ACCOUNTS` under `EMAIL_MAX_PER_DAY_PER_ACCOUNT`; `account_for_lead` pins a thread to the mailbox that opened it; `email_utils.send_email(account=…)` sets From, login and envelope per account; `fetch_unseen_emails` polls every mailbox; `parse_email_accounts` takes per-account SMTP/IMAP hosts, so Workspace and Zoho can coexist. Gaps, cosmetic: one `SENDER_NAME` and one `SENDING_DOMAIN` (Message-ID) for all accounts; no per-mailbox bounce alert.
5. **Companies House filter (separate design).** Hook into `LeadAgent` after the email scrape, store `company_number`/`legal_form`, extend `design_agent.park_reason` so sole traders are parked before a preview is built. Free API, 600 requests per 5 minutes [L3].
6. **Exhaustion signal.** A `report --weekly` line, buckets exhausted / total, so the next-niche switch is data-driven.

## (e) The legal gate

PECR: the electronic-mail rule "does not apply to corporate subscribers ... you do not need their consent under PECR"; corporate subscribers are "companies; corporation soles; limited liability partnerships; Scottish partnerships". Sole traders and ordinary partnerships are individual subscribers and need consent or soft opt-in [L1]. Even to a Ltd you "must not disguise or conceal your identity" and must "give a valid address ... to opt-out"; a named address (`john@…`) is personal data under UK GDPR with an absolute right to object [L1][L2]. The footer, address check and one-click unsubscribe cover the mechanics; keep a legitimate-interests note on file and prefer `info@` over named mailboxes.

Ltd share. UK-wide, start of 2025: 3.2M sole proprietorships (57%), 2.1M companies (37%), 368k ordinary partnerships (6%); construction is the largest sector (885k) [P1]. Plumbing: ~46,300 UK businesses, 55–65% one-person sole traders [P2]; an installer-press poll found "almost a 50/50 split" among heating engineers [P3]. Businesses with a listing and a website skew incorporated, so the working estimate is **40–55% Ltd among sourced plumbers, 45% used above**; the filter roughly halves volume. The `scoring.py` Ltd bonus only matches "Ltd/Limited/LLP/PLC" in the trading name, so the register lookup, not the regex, must be the gate.

## (f) Four-week ramp

Week-over-week growth stays under ~40% (`WARMUP.md`); 300/day is therefore a week-7 target, not week 4.

- **Week 0 (prep).** Oscar decides (g). Buy 4 domains; 8 Workspace mailboxes; SPF/DKIM/DMARC `p=none`; Postmaster Tools; connect all 8 to Instantly warm-up. VPS + Caddy with the wildcard cert; land (d)1–3 and the Companies House filter; dry-run the new mailboxes.
- **Week 1.** Warm-up only from the new mailboxes. Sourcing at `SOURCING_DAILY_LIMIT=450`, `SITE_SCORE_GOOD_AT=0`, `DESIGN_BACKLOG_MAX=100` so the Ltd-filtered queue builds without previews expiring. The Zoho mailbox carries on at 15/day pending retirement from cold sends.
- **Week 2.** 8 × 8 = ~60 cold/day, `FOLLOW_UP_ENABLED=false`, `EMAIL_MAX_PER_DAY=60`. Gate: bounces <2%, complaints <0.1%, no blocklist hits (check MXToolbox for each domain), replies observed by hand.
- **Week 3.** 8 × 15 = 120 sends/day: ~100 cold + follow-ups switched on. `EMAIL_MAX_PER_DAY=120`.
- **Week 4.** 8 × 25 = 200/day: 100 cold + ~95 follow-ups. **100/day reached.** Start warming 16 more mailboxes on 6–8 new domains; first weekly report on replies by tier and bucket exhaustion.
- **Weeks 5–7.** Add the warmed mailboxes in two steps (16, then 24), `SOURCING_PER_BUCKET=3`, `SOURCING_DAILY_LIMIT=1350`, `EMAIL_MAX_PER_DAY=600`, only if replies ≥1% and complaints <0.1% held for two weeks. Expect plumbers to run out in week 8; have `electrician` ready.

## (g) Risks and the decisions Oscar must make

Risks: **blocklisting** (contained per domain, hence 2–3 mailboxes per domain and never above 25/day each); **Zoho suspension** (its policy bans exactly this; move cold sends off Zoho); **Workspace suspension** (same AUP wording; mitigated by warm-up, low per-mailbox volume, a verified list); **Vercel non-commercial terms** (this is paid work by Vercel's definition: Pro or leave); **duplicate previews** (`_already_in_db` dedupes on place id and name+town and `_refresh_existing_preview` stops second repos, but a torn-down lead re-researched gets a fresh build; keep the `torn_down_at` checks when swapping hosting); **PC uptime** (unsubscribe links and the Stripe webhook die when the PC sleeps; at 300/day one dark day strands 300 previews, so the loop moves to the VPS before week 3); **Places spend** (`SOURCING_MAX_REQUESTS_PER_RUN` is the only hard cap; add a Google Cloud budget alert); **small universe** (4–6k emails, then a new niche); **the 0/30 baseline** (nothing here fixes copy; if week 2 shows <1% replies, stop scaling and fix the offer).

Decisions for Oscar, in order: (1) gate off (`SITE_SCORE_GOOD_AT=0`) or accept ~12 emails/day; (2) 100 or 300/day, follow-up on or off; (3) Workspace + Instantly warm-up, and whether Zoho stops cold sending; (4) VPS + Caddy previews (code change) or Vercel Pro (£15/month, no code change, 700+ projects); (5) `SOURCING_PER_BUCKET` 1 versus 3–5 (one-per-town purity costs ~5× on Places); (6) monthly budget ceiling; (7) the niche after plumbers.

## Sources (all accessed 2026-09-20)

- [T1] Wikipedia, List of ONS built-up areas in England by population — https://en.wikipedia.org/wiki/List_of_ONS_built-up_areas_in_England_by_population (counts derived from the page's wikitext tables)
- [T2] ONS, Towns and cities, characteristics of built-up areas, England and Wales: Census 2021 — https://www.ons.gov.uk/peoplepopulationandcommunity/housing/articles/townsandcitiescharacteristicsofbuiltupareasenglandandwales/census2021
- [G1] Google Maps Platform pricing list (Text Search Essentials/Pro $32, Enterprise $35 per 1,000; free 10k/5k/1k per month) — https://developers.google.com/maps/billing-and-pricing/pricing
- [G2] Places API (New) Text Search, SKU field lists — https://developers.google.com/maps/documentation/places/web-service/text-search
- [M1] Google Workspace pricing UK (Business Starter £5.90/user/month, annual) — https://workspace.google.com/pricing
- [M2] Google Workspace Acceptable Use Policy — https://workspace.google.com/terms/use_policy.html
- [M3] Gmail sending limits in Google Workspace — https://knowledge.workspace.google.com/admin/gmail/gmail-sending-limits-in-google-workspace
- [Z1] Zoho Mail Usage Policy — https://www.zoho.com/mail/help/usage-policy.html (Mail Lite $1/user/month annual per https://www.g2.com/products/zoho-mail/pricing)
- [S1] SendGrid, Email Prohibited Content Types and Uses — https://support.sendgrid.com/hc/en-us/articles/4404316003483-Email-Prohibited-Content-Types-and-Uses
- [W1] Instantly pricing — https://instantly.ai/pricing; [W5] Instantly terms — https://instantly.ai/terms
- [W2] Smartlead pricing — https://www.smartlead.ai/pricing; [W6] Smartlead terms — https://www.smartlead.ai/new-terms-and-conditions
- [W3] lemlist pricing — https://www.lemlist.com/pricing; [W7] lemlist terms — https://www.lemlist.com/terms
- [W4] Mailreach pricing — https://www.mailreach.co/pricing
- [D1] Gmail email sender guidelines FAQ (0.10% / 0.30%) — https://support.google.com/a/answer/14229414
- [D2] Mailgun on Microsoft Outlook 2025 sender requirements (complaint <0.3%, bounce <2%) — https://www.mailgun.com/blog/deliverability/microsoft-sender-requirements/
- [D3] Smartlead, how many cold emails per day (3–4 week ramp to 25–30/day) — https://www.smartlead.ai/blog/how-many-cold-emails-per-day
- [V1] Vercel Hobby plan — https://vercel.com/docs/plans/hobby; Vercel limits — https://vercel.com/docs/limits
- [V2] Vercel fair use guidelines, Commercial usage — https://vercel.com/docs/limits/fair-use-guidelines
- [GH1] GitHub plans — https://docs.github.com/en/get-started/learning-about-github/githubs-plans
- [GH2] GitHub REST API rate limits — https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
- [C1] Cloudflare Pages limits — https://developers.cloudflare.com/pages/platform/limits/; [C2] Direct Upload — https://developers.cloudflare.com/pages/get-started/direct-upload/; [C3] Custom domains — https://developers.cloudflare.com/pages/configuration/custom-domains/
- [H1] Hetzner price adjustment 15 June 2026 (CX23 €5.49) — https://docs.hetzner.com/general/infrastructure-and-availability/price-adjustment/
- [K1] Caddy automatic HTTPS, wildcard certificates — https://caddyserver.com/docs/automatic-https
- [L1] ICO, Business-to-business marketing — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
- [L2] ICO, Electronic mail marketing — https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/guide-to-pecr/electronic-and-telephone-marketing/electronic-mail-marketing/
- [L3] Companies House API rate limiting (600 per 5 minutes) — https://developer-specs.company-information.service.gov.uk/guides/rateLimiting
- [P1] DBT, Business population estimates 2025: statistical release (2 Oct 2025) — https://www.gov.uk/government/statistics/business-population-estimates-2025/business-population-estimates-for-the-uk-and-regions-2025-statistical-release
- [P2] Whito, UK plumber marketing in numbers (May 2026) — https://whito.co.uk/research/uk-plumber-marketing-data/
- [P3] Installer Online, Limited company vs sole trader (July 2023) — https://www.installeronline.co.uk/heating/limited-company-vs-sole-trader/
