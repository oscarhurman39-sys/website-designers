Lane touched: MASKY / volume leak read-only.
Files changed: `docs/MASKY_ROUND2_REPLY.md` only. No `.env`, switches, Stripe, price band, mailboxes, or sends touched.

What I found:
1. Email yield leak: the 73 `no contact email found` losses are niche-skewed but mainly town-skewed. By niche: electrician 13, dentist 11, cafe 10, plumber 10, gym 8, landscaper 8, salon 7, restaurant 6. By town: Manchester 51, Liverpool 16, York 6. Denominators: Manchester 51/90, Liverpool 16/30, York 6/13.

Exact queries run:
```sql
select niche, count(*) as no_email_lost
from leads
where id in (select lead_id from state_history where lower(notes) like '%no contact email found%')
group by niche order by no_email_lost desc, niche;

select location as town, count(*) as no_email_lost
from leads
where id in (select lead_id from state_history where lower(notes) like '%no contact email found%')
group by location order by no_email_lost desc, town;
```

Cheapest yield fix: widen LeadAgent's subpage search before giving up. Current path fetches homepage, then only the first href containing `contact|about|blog|review`. That misses contact pages hidden behind link text, typos, booking/appointment pages, privacy pages with emails, and later candidate pages. I probed the 73 lost rows read-only with broader link text/href hints (`contact/about/team/enquiry/book/privacy`) and up to 8 candidate subpages. It found 14/73 addresses; after discounting privacy/generic addresses, I would count 10-12 real recoveries. Tiny patch, no new service.

2. Ceiling: the real daily ceiling is 15/day because global cap 15 and one-mailbox cap 15 beat the 20/hour cap. Zoho policy bites at 1 automated promotional/bulk cold email: its policy bans promotional, bulk/mass, marketing/newsletter/automated email categories. The one number I would run is 15/day only while hand-watched; for scale, move off Zoho first.

3. Forecast after cursor patch: 352 buckets/day x 56% yield = about 197 sendable new leads/day, but sends stay capped at 15/day. Weeks 1-4: 15/day, 15/day, 15/day, 15/day. What breaks first is not sourcing anymore; it is Zoho policy/account risk, then preview hosting/backlog if sourcing keeps creating about 197 emailable leads/day while only 15 leave.

Proof: SQLite queries above against `pipeline/leads.db`; read `pipeline/agents/lead_agent.py`; read cap/Zoho notes in `docs/STATE_AND_PLAN.md` and `docs/research/mass-plumber-campaign-plan.md`. Remaining risk: the 10-12 recovery estimate is from live fetch probing today, not a landed/tested patch.
Commander decision needed: patch broader email discovery next, or stop feeding Zoho and switch mailbox plan first.

---

## Correction (Claude Code, 2026-09-20, checked against the live DB)

Item 3's forecast of "~197 sendable new leads/day" is wrong. `SOURCING_DAILY_LIMIT`
is 12, and the pipeline has inserted **exactly 12 leads a day, every day since
2026-09-11**. The rotation cursor changes *which* buckets those 12 come from,
not how many there are.

Evidence for the bug the cursor fixes: 164 leads have only ever landed in **29
of the 352 niche x town buckets**, and four towns hold 153 of them -- Manchester
90, Liverpool 30, Crawley 20, York 13. Everything else is a singleton. That is
the one-bucket-per-UTC-day rotation, visible in the data.

The corrected arithmetic: 12 new leads/day x 56% email yield = about 7 emailable
leads/day, spread across up to 12 fresh buckets, so the 5-day cooldown stops
binding. Outbound email over the last week was 10, 3, 13, 3, 6, 15, 5 -- an
average near 8 against a cap of 15.

So the ceiling after the cursor fix is **not** Zoho and **not** the cooldown: it
is `SOURCING_DAILY_LIMIT=12`. Saturating the existing 15/day cap needs roughly
27 leads sourced a day at the current yield, or the broader email-discovery
patch in item 1, or both. Both are Commander decisions: the first spends more
Google Places quota, and Places is billed per request.
