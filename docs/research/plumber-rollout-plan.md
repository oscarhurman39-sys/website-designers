# Plumber rollout: how many towns, in what order, and what it costs

Written 2026-09-20. The companion to `docs/research/england-plumber-towns.md` (the list itself)
and `docs/research/mass-plumber-campaign-plan.md` (the wider campaign research). Nothing here was
changed in the pipeline; every config value named below is a decision, not an action taken.

Everything is measured against the live database (`pipeline/leads.db`, read-only) where a measured
number exists, and marked as an estimate where it does not.

---

## 1. The one thing to understand before anything else

**Widen the towns first. Narrow to plumbers second.** In that order, or volume goes down instead
of up.

Here is why. The pipeline refuses to cold-email two businesses in the same trade and the same town
inside `OUTREACH_COOLDOWN_DAYS`, which is currently **5 days**. So each "trade + town" pair - the
code calls it a bucket - can produce one cold email every five days, and no more.

- **Today:** 44 towns x 8 trades = 352 buckets. 352 / 5 = **70 cold emails a day** allowed by the
  cooldown. Nowhere near the limit; something else is holding you back.
- **If you switched to plumbers only tomorrow, keeping 44 towns:** 44 buckets. 44 / 5 = **8.8 cold
  emails a day**. That is *below* your current daily email cap of 15. You would have cut your own
  ceiling roughly in half.
- **Plumbers only with 100 towns:** 100 / 5 = **20 a day**. Back above the cap, with room.

The rule of thumb for the whole plan is therefore:

> **Cold emails a day that the town list allows = number of towns ÷ 5.**

To send 15 a day on plumbers alone you need at least 75 towns. To send 91 a day you need all 456
English towns in the list. To go past that you would have to shorten the cooldown or add a second
trade - the full list of English towns cannot feed more than about 97 plumber emails a day at a
5-day cooldown.

---

## 2. What is actually holding you back today

Measured from `pipeline/leads.db` on 2026-09-20:

| Thing | Measured value | Is it the bottleneck? |
|---|---|---|
| Leads sourced per day | **12**, every day for the last 12 days | **Yes.** This is `SOURCING_DAILY_LIMIT`. |
| Of those, how many had a findable email | 81 of 164 leads ever sourced = **49%** | This halves everything. |
| Cold emails allowed per day | **15** (`EMAIL_MAX_PER_DAY`, one mailbox) | Second bottleneck. |
| Cold emails actually sent per day | 1 to 15, typically 3-13 | Supply-starved, not cap-starved. |
| Buckets the cooldown allows | 70 a day | No. Miles of headroom. |
| Replies received, ever | 1, from 30 cold emails | The real risk, see §7. |

So: 12 leads a day in, about 6 of them have an email address, and the 15-a-day cap is never the
thing that stops you. **Sourcing volume is the constraint, and `SOURCING_DAILY_LIMIT` is the dial.**

One more measured fact that changes the sums: 43 of the 47 leads that have been site-scored scored
8 or above, meaning their existing website is healthy. If the site-quality gate
(`SITE_SCORE_GOOD_AT`, default 8) is parking those, only about 1 sourced lead in 50 ever becomes a
cold email instead of 1 in 4.5. That is decision (b) in §8.

---

## 3. The arithmetic, shown

**The funnel, per 100 plumbers sourced:**

| Step | Survivors | Where the number comes from |
|---|---:|---|
| Sourced from Google Places | 100 | - |
| Has a website (required) and an email we can scrape | 50 | **measured**: 49% of leads in the DB have an email; 82 of the 83 dead leads died here |
| Is a limited company, so lawful to cold-email (see §6) | ~22 | **estimate**: 45% Ltd, from `mass-plumber-campaign-plan.md` §e. The screen does not exist in code yet |
| Survives the site-quality gate, **if it is left on** | ~2 | **measured**: 43 of 47 scored sites are "healthy" |

So with the site gate **off**: about **22 sendable cold emails per 100 plumbers sourced**, or one
sendable email per 4.5 sourced. With the gate **on**: about 2 per 100, one per 50.

Everything below assumes the gate is off. If it stays on, multiply every `SOURCING_DAILY_LIMIT`
by roughly 10 and the Google bill with it - which is really an argument for turning it off, not
for sourcing ten times as hard.

**Turning a target into a daily sourcing number:**

```
SOURCING_DAILY_LIMIT  =  cold emails you want per day  ÷  0.225
                      =  cold emails you want per day  x  4.5
```

**Turning a daily sourcing number into a Google Places bill.** The pipeline asks Places for fields
that fall in the "Enterprise" price band: **$35 per 1,000 requests, first 1,000 each month free**
(figures from `mass-plumber-campaign-plan.md` §a, sourced from Google's pricing page on
2026-09-20). One search of one town is one request and returns a page of 20 results, of which
`SOURCING_PER_BUCKET` are kept. At one plumber per town - which is the point of this campaign -
that is roughly **1.2 requests per lead** once you allow for towns that have been fully mined and
return nothing new.

```
Places requests per day    =  SOURCING_DAILY_LIMIT  x  1.2
Places bill per month      =  (requests per day x 30  -  1,000 free)  x  $0.035
```

---

## 4. The stages

Each stage is cumulative: stage 2 means the stage 1 towns **plus** the stage 2 towns in
`SOURCING_LOCATIONS`. The paste-ready values are in `england-plumber-towns.md`.

**Why this order: population, biggest first.** Not distance, not region. Three reasons, and they
are all about the search rather than about geography:

1. A bigger town has more plumbers on Google, so a search is more likely to return one that is new,
   has a website, has a scrapable email, and is a limited company. In a town of 20,000 the first
   page may be four plumbers, two of whom you have already emailed.
2. Sorting by population spreads across the country on its own. Stage 1's 100 towns cover all eight
   English regions outside London: 21 in the North West, 19 South East, 14 East of England, 12 West
   Midlands, 11 Yorkshire and the Humber, 11 South West, 7 North East, 5 East Midlands. You do not
   have to engineer the spread; England's cities provide it.
3. It fails cheaply. If the offer does not work, you find out in stage 1 on the towns with the most
   plumbers in them, not after paying to mine 456 small towns.

London is held back to a separate final stage because its boroughs are administrative areas rather
than towns, and it is the most competitive plumbing market in the country - the worst place to test
an unproven email.

| Stage | Towns added | Towns total | Cold emails/day the towns allow (towns ÷ 5) | Mailboxes needed at 25/day | `EMAIL_MAX_PER_DAY` | `SOURCING_DAILY_LIMIT` needed | Places requests/day | Places $/month |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **Now** | - | 44 (x 8 trades) | 70 | 1 | 15 | 12 (measured) | ~14 | free |
| **1** | 100 | **100** | **20** | 1 | 15 | **70** | ~84 | **~$53** (about £41) |
| **2** | 100 | **200** | **40** | 2 | 40 | **180** | ~216 | **~$192** (about £148) |
| **3** | 150 | **350** | **70** | 3 | 70 | **320** | ~384 | **~$368** (about £283) |
| **4** | 106 | **456** | **91** | 4 | 90 | **410** | ~492 | **~$482** (about £371) |
| **5** (London, optional) | 32 | **488** | **97** | 4 | 95 | **440** | ~528 | **~$519** (about £399) |

Read the table like this: the "towns allow" column is the ceiling the cooldown puts on you; the
`EMAIL_MAX_PER_DAY` column is what you should actually set, which is the smaller of that ceiling
and what your mailboxes can safely carry at 25 cold emails per mailbox per day.

**Two warnings about that table:**

- **`EMAIL_MAX_PER_DAY` counts follow-ups too.** `FOLLOW_UP_ENABLED` is on by default and a
  reminder goes to roughly everyone who did not reply, which is about 95% of them. So a cap of 15
  with follow-ups on is really **about 8 cold emails a day plus 7 reminders**. Either double the
  cap or turn follow-ups off - see decision (c).
- **`SOURCING_MAX_REQUESTS_PER_RUN` is the only hard lid on the Google bill.** It is 30 today, and
  sourcing runs hourly, so the worst case is 24 x 30 = 720 requests a day, about **$722 a month**
  if every single request were billed. A run stops once the day's lead limit is filled, so the real
  bill is the table above - but the lid is what protects you from a bug. Raise it to **40 at stage
  3** and **60 at stage 4** (a run can only take about 25 leads at the current lid, and stage 4
  needs 410 leads a day, which is 17 of the 24 hourly runs - no slack if the PC sleeps). Set a
  Google Cloud budget alert at whatever number you pick in decision (e).

**What to check before moving to the next stage.** All of these except the first are in `python
run.py report` or the dashboard:

1. **Did sourcing actually fill `SOURCING_DAILY_LIMIT`?** If you set 70 and got 45, the towns at
   that stage are mined out and adding the next 100 is the right move. If it filled easily, the
   limit was the constraint, not the towns.
2. **Bounces under 2% and spam complaints under 0.1%** across the last two weeks. Above that,
   stop - adding towns makes a deliverability problem worse, not better.
3. **At least one reply per 100 cold emails.** With 1 reply from 30 emails so far there is not
   enough data to call it either way, and that is exactly why stage 1 runs for a fortnight before
   stage 2.
4. **No new mailbox went to a blocklist.** Check each sending domain on MXToolbox.
5. **Previews are not piling up.** `PREVIEW_TTL_DAYS` is 7, so live previews settle at seven times
   the daily email count: 105 at stage 1, 280 at stage 2. Vercel's free plan allows 200 projects
   and 100 deploys a day, so **stage 2 needs the preview-hosting decision from
   `mass-plumber-campaign-plan.md` §b made first.**
6. **The Google bill matches the table.** If it is higher, towns are being re-searched for nothing.

**Suggested pace:** two weeks per stage, minimum. New mailboxes need three to four weeks of
warm-up before they carry real volume, so the mailbox for stage 2 should be bought and warming
during stage 1.

**How long the whole thing lasts.** England has roughly 4,000-6,000 plumbers who are limited
companies, have a website, and have a findable email (`mass-plumber-campaign-plan.md`). At 91 a
day that is **six to ten weeks** before the country is exhausted and the campaign needs a second
trade. That is not a reason to go slower; it is a reason to have the next trade ready.

---

## 5. Config values, named

Nothing here should be changed as a side effect of anything else. `.env` values marked
**Commander** are Oscar's decision only, per `AGENTS.md`.

| Setting | Now | Stage 1 | Stage 4 | Who | What it does in one line |
|---|---|---|---|---|---|
| `SOURCING_LOCATIONS` | 44 towns | 100 towns | 456 towns | operator | The town list. One line, semicolons between entries. |
| `SOURCING_NICHES` | 8 trades | `plumber` **after** stage 1 is in | `plumber` | **Commander** | Which trades to search. See §1 for the order. |
| `SOURCING_PER_BUCKET` | 2 | **1** | 1 | operator | Plumbers taken per town per sweep. 1 = literally one per town; 2 roughly halves the Google bill but builds a queue the cooldown cannot drain. |
| `SOURCING_DAILY_LIMIT` | 12 | 70 | 410 | **Commander** | Leads inserted per day. This is the dial that spends money. |
| `SOURCING_MAX_REQUESTS_PER_RUN` | 30 | 30 | 60 | **Commander** | Hard lid on Google requests per hourly run. The bill's safety catch. |
| `SITE_SCORE_GOOD_AT` | 8 | 0 if you want volume | 0 | **Commander** | 8 parks leads whose current site is healthy - which is 43 of 47 of them. |
| `OUTREACH_COOLDOWN_DAYS` | 5 | 5 | 5 | operator | Days before the same trade+town can be emailed again. Leave it at 5; the town list is what should grow. |
| `EMAIL_MAX_PER_DAY` | 15 | 15 | 90 | **Commander** | All sends, cold and follow-up, across all mailboxes. |
| `EMAIL_MAX_PER_DAY_PER_ACCOUNT` | 15 | 25 max | 25 max | **Commander** | Never above 25. Volume comes from more mailboxes, not busier ones. |
| `FOLLOW_UP_ENABLED` | true | false at stage 1 | true | **Commander** | A reminder costs the same as a cold email against the cap. |
| `EMAIL_ACCOUNTS` | 1 | 1 | 4 | **Commander** | One entry per mailbox. Multi-mailbox rotation already works in code. |

One practical note on switching the list over: the cooldown matches the **exact** town string. The
database currently holds leads under `Manchester`, while the new list says
`Manchester, Greater Manchester`. Those are two different buckets as far as the cooldown is
concerned, so on the first day after the switch a Manchester plumber can be emailed even if one
was emailed recently. It is a one-off, and it affects a handful of towns.

---

## 6. The legal gate: most plumbers cannot lawfully be cold-emailed

This is not a technical preference. UK law (PECR, the Privacy and Electronic Communications
Regulations, as the ICO explains it) says marketing email without consent is lawful to **corporate
subscribers** - limited companies, limited liability partnerships, PLCs, Scottish partnerships.
**Sole traders and ordinary partnerships are treated as individuals**, and emailing them without
consent or a prior relationship is not lawful.

Plumbing is one of the worst trades for this. The trade research in
`mass-plumber-campaign-plan.md` §e puts sole traders at 55-65% of UK plumbing businesses, an
installer trade poll found close to a 50/50 split among heating engineers, and across all UK
businesses 57% are sole proprietorships. Businesses with a Google listing *and* a website skew a
little more incorporated than average.

**Plainly: expect to lose roughly half the list - somewhere between 45% and 60% of the plumbers
you source.** That loss is already in every number in §3 and §4 (the 45% Ltd line).

**A Companies House screen is required before a plumber-only campaign goes out, and it does not
exist in the code yet.** Two things to know:

- The check in `pipeline/scoring.py` today only looks for "Ltd", "Limited", "LLP" or "PLC" in the
  trading name. That is not the same thing - plenty of limited companies trade as "Dave's
  Plumbing". Measured: only 25 of 164 leads in the database have an incorporated-looking name,
  which is far below the real rate. The name is a hint, not a gate.
- The real screen is a Companies House register lookup, free, 600 requests per 5 minutes. It is
  written up as item (d)5 in `mass-plumber-campaign-plan.md` - and there is now a full design for
  it in `docs/research/companies-house-screen.md` (also 2026-09-20, also unbuilt): look the
  business up after the email scrape, store the company number, and park sole traders before a
  preview is ever built - which also saves the build.

Until that screen exists, a plumber-only campaign at any volume is sending unlawful email to
roughly half its recipients. The other compliance machinery you already have (unsubscribe link,
`List-Unsubscribe` header, physical address in the footer, bounce suppression) is necessary but
does not fix this.

---

## 7. The risk nobody's arithmetic can fix

30 cold emails have produced 1 reply. That is too small a sample to mean anything - but it is also
the only real evidence there is, and every number in this plan assumes 1-3% of people reply.

If stage 1 runs a fortnight at 15 a day (roughly 150 emails) and produces no replies, **the
problem is the offer or the copy, and adding towns will not fix it**. Stop at stage 1, keep the
Google bill at £40, and change what the email says. Scaling a message that does not work just
spends more money faster and burns more domains.

---

## 8. The decisions only Oscar can make

Each is a question, with what I would do and why.

**(a) Go plumber-only, and when?**
*Recommended: yes, but only after the stage 1 town list is live and has run for a few days.*
Switching to plumbers with today's 44 towns cuts the ceiling from 70 emails a day to 8.8 - below
your current cap. Towns first, trade second.

**(b) Turn off the site-quality gate (`SITE_SCORE_GOOD_AT=0`)?**
*Recommended: yes, if the goal is volume.* 43 of 47 scored sites came back healthy, so with the
gate on you get about one sendable lead per 50 sourced and the Google bill goes up tenfold for the
same output. Keeping it on is a legitimate choice - it means only approaching businesses whose site
is genuinely poor, which is a better pitch - but then accept roughly 10-12 emails a day as the
ceiling and do not fund stages 2-4.

**(c) Follow-ups on or off during stage 1?**
*Recommended: off until stage 2.* With a 15-a-day cap, every reminder displaces a new prospect.
Turn them back on at stage 2 when the cap is 40 and there is room for both.

**(d) Build the Companies House screen before or after the first plumber-only send?**
*Recommended: before. This is the one item on the list I would not ship without.* It is a legal
gate, not an optimisation, and it also stops you building previews for people you cannot email.

**(e) What is the monthly ceiling for the Google Places bill?**
*Recommended: £50 a month through stage 2, reviewed before stage 3 (which is about £280).* Put a
Google Cloud budget alert at the number you choose - `SOURCING_MAX_REQUESTS_PER_RUN` is the only
other thing standing between a bug and a four-figure bill.

**(f) One plumber per town (`SOURCING_PER_BUCKET=1`) or two?**
*Recommended: 1.* It is what "one plumber per town" means, and with a 5-day cooldown the second
plumber from a town cannot be emailed for another five days anyway - he just sits in the queue.
Setting it to 2 roughly halves the Google bill per lead, so it is the first thing to change if
decision (e) bites.

**(g) Include London (stage 5)?**
*Recommended: not yet.* 32 boroughs adds 6 emails a day to the ceiling and they are the hardest,
most-contested plumbers in the country. Revisit once stages 1-4 have a reply rate you trust.

**(h) When does the second mailbox get bought?**
*Recommended: during stage 1, so it is warmed up by stage 2.* A new mailbox needs three to four
weeks of warm-up before it can carry 25 a day. Buying it the week you need it is three weeks too
late, and every stage past 1 is mailbox-limited, not town-limited.

**(i) What is the trade after plumbers?**
*Recommended: decide it now, start it at stage 3.* England runs out of emailable plumbers six to
ten weeks after stage 4 begins. `electrician` is the obvious next one - it is already in
`SOURCING_NICHES` today and has a template.

---

## Sources

- The town list, its population cut and its sources: `docs/research/england-plumber-towns.md`
- Places pricing, Ltd share, mailbox and hosting research: `docs/research/mass-plumber-campaign-plan.md`
- PECR and the corporate-subscriber rule: ICO, *Business-to-business marketing* -
  https://ico.org.uk/for-organisations/direct-marketing-and-privacy-and-electronic-communications/business-to-business-marketing/
  (read 2026-09-20)
- Companies House API rate limit (600 per 5 minutes) -
  https://developer-specs.company-information.service.gov.uk/guides/rateLimiting (read 2026-09-20)
- Measured pipeline numbers: `pipeline/leads.db`, read-only on 2026-09-20 (164 leads, 12 sourced
  per day for 12 days, 81 with an email, 47 site-scored of which 43 scored 8+, 30 cold emails sent,
  1 reply)
- Config behaviour: `pipeline/config.py`, `pipeline/agents/sourcing_agent.py`,
  `pipeline/agents/sales_agent.py`, `pipeline/utils/db.py`
