# Baseline & Next-Level Plan -- website-designers

Written 2026-08-04. `PLAN.md` is the engineering log -- what got built, in
what order, and why. This file is different: a snapshot of where the app
actually stands today, an honest answer to "are we leveling up or just
adding features," and a prioritized plan for a real step-change.

## The baseline: what this app actually is today

The pipeline, end to end:

1. **Lead research** -- `lead_agent.py` finds a business's existing website
   (`find_business_website`, DuckDuckGo search + `_fetch`, a
   `requests`+BeautifulSoup scrape that respects `robots.txt`), then pulls
   contact email/phone, pain points, and testimonials off whatever site it
   finds. `content_importer.py` can also pull real copy from an existing
   site to seed a niche's template content.
2. **Design generation** -- `design_agent.py` builds a static site from
   Jinja2 templates (`templates/<niche>/` or `templates/default/`), a
   per-niche `NICHE_SECTIONS` manifest that picks which optional sections
   (testimonials, gallery, etc.) a niche's template renders, and curated
   copy dicts (`NICHE_SERVICES`, `NICHE_HERO_TEXT`). `build_context()`
   assembles the template variables per lead.
3. **Outreach** -- `sales_agent.py` sends a cold email (HF-personalized
   opener if `HF_API_TOKEN` is set, deterministic template otherwise), two
   follow-ups, and now a self-serve Stripe **Buy Now** link embedded in all
   three plus the preview site itself (`/buy/<lead_id>`, added this
   session).
4. **Payment + handoff** -- Stripe Checkout confirms payment via webhook;
   `onboarding_agent.py` (added this session) automatically creates the
   client's repo, invites them as a GitHub/Vercel collaborator, and emails
   them a claim-your-website link (`/onboard/<lead_id>`) -- no operator
   action required unless `AUTO_REMOVE_GITHUB_ACCESS` is left off, in which
   case final access removal stays a manual `transfer` command.
5. **Client editing** -- a magic-link-gated editor (`editor_auth.py`,
   `editor_agent.py`) lets the client tweak copy/images post-handoff.
6. **Maintenance/QA** -- `maintenance.py` and the content QA scanner catch
   broken links, stale content, and template regressions on a schedule.

All of it is real, tested code (199 tests as of this session, all against
mocked Stripe/GitHub/Vercel/email/HF). None of it has been run against a
real lead, a real inbox, or a real paying client yet -- see `LEDGER.md`'s
standing brick. That gap matters more to what "next level" means than
anything below.

## Is this still "the regular stuff"? Two things worth naming directly

Everything in sections 3-6 above is now reasonably mature: outreach,
payment, and handoff are automated end-to-end. The two pieces that
*haven't* moved past their original, simplest form are lead discovery and
site generation -- both from the very first version of this app.

### Still scraping?

Yes. `find_business_website` is a DuckDuckGo HTML search plus a
best-effort scrape of whatever comes back, with no paid data source behind
it. It works, but it's a volume-and-precision ceiling: no way to filter by
"has no website" or "under 10 employees" or "opened in the last year"
up front, and every lead costs a live scrape that can silently fail on
sites that block bots or render client-side.

The obvious upgrade is a real business-data API (Google Places, or a paid
B2B lead list) that returns structured, filterable results instead of
whatever DuckDuckGo's HTML happens to contain that day. That's a real
recurring cost and a vendor decision, not something to default into.
**Recommendation: don't switch yet.** Lead *sourcing* quality isn't
provably the bottleneck until there's real reply-rate data showing it is
-- and there isn't any real data yet at all (see above). This is the same
call `PLAN.md` already made when it filed this under "genuine
product/data-source decision, not mine to default into."

### Still templates?

Yes. Every site is Jinja2 + a fixed set of niche templates + copy pulled
from `NICHE_SERVICES`/`NICHE_HERO_TEXT` dicts (or imported real copy via
`content_importer.py`, but still dropped into the same fixed template
structure). Two leads in the same niche get structurally identical sites
with swapped-in text.

This is the more interesting lever, and unlike lead sourcing it's cheap to
test incrementally: use Claude to generate bespoke section copy, pick which
optional sections a *specific* lead's site should include (not just what
its niche defaults to), or vary layout/tone per lead instead of per niche.
It doesn't require a business-model decision, doesn't require picking a
paid vendor, and it directly addresses the thing a business owner would
actually notice ("this looks like every other site" vs. "this looks like
it was built for me"). **This is the first real next-level investment
worth making**, because the cost to try it is low and the signal
(does reply rate or close rate move) is fast to read once Path A below is
done.

## Three paths to a genuinely new level (not just more features)

### Path A: Validate reality first (prerequisite, not optional)

Warm up a real sending domain (`WARMUP.md`), then run one real batch of
leads through `run.py loop` end to end. This isn't a "path" so much as the
gate everything else should wait on: right now every decision about what
to build next is a guess, because nothing has touched a real inbox or a
real business owner. This is `LEDGER.md`'s current brick, and it's still
the actual bottleneck.

### Path B: AI-native content generation

Replace (or augment) the curated `NICHE_SERVICES`/`NICHE_HERO_TEXT` dicts
and fixed `NICHE_SECTIONS` manifest with Claude-generated, per-lead copy
and section selection, grounded in whatever `lead_agent.py` already
scraped about the business. Testable incrementally -- start with just the
hero copy, measure, expand. This is the highest-leverage "quality" lever
available and doesn't require a business-model decision.

### Path C: Smarter lead sourcing

Swap DuckDuckGo scraping for a real business-data API (Google Places) or a
licensed lead list, with real filtering (industry, size, "no existing
website") instead of post-hoc heuristics. Real recurring cost, real vendor
decision -- worth doing once Path A's data shows sourcing (not content,
not follow-through) is the actual constraint on results.

### Path D: Product-ize -- from Oscar's tool to a platform

Multi-tenant support, billing, a real onboarding flow for *other* people
running their own lead pipelines through this app instead of just one
operator's. The biggest lift by far, and only worth it once Path A
validates there's real demand for the underlying service at all.

## What "just the regular stuff" would look like instead

For contrast -- the lower-ceiling incremental list, most of it already
flagged at various points in `PLAN.md`: per-service pricing tiers,
staff/team bio sections, redesign variation options, a formal
change-request workflow for post-handoff edits, better analytics on the
dashboard, an audit-as-lead-magnet flow. All individually reasonable, none
of it changes the trajectory of the business. Fine to pick off
opportunistically; none of it should be mistaken for "leveling up."

## Recommended sequencing

1. **Path A, first, no exceptions.** It's not optional and it's not a big
   lift -- it's the thing that turns every other decision on this page
   from a guess into a fact.
2. Read the real data, then branch:
   - Opens are fine but replies are near-zero and the sites *look* fine ->
     the message/offer is the problem, not content or sourcing -- revisit
     `sales_agent.py` copy before either Path B or C.
   - Replies happen but people bounce off the site itself ("looks
     templated," "doesn't feel like ours") -> Path B.
   - Reply rate is low across the board and lead quality looks weak
     (dead businesses, wrong contact info, no real pain point) -> Path C.
   - Strong demand signal *and* a desire to scale past one operator's
     client base -> Path D, but only after B and/or C are already paying
     off for the single-operator case.
3. "Regular stuff" items can proceed independently any time -- they're not
   gated on Path A and don't compete for the same attention.

## Decisions only you can make

- Whether to pay for a lead-sourcing data source, and which one (Google
  Places API pricing vs. a licensed lead list vs. staying with free
  scraping longer).
- Whether to invest in AI-generated content given the real per-lead API
  cost that implies at scale.
- Whether to productize this beyond personal use at all -- that's a
  different business than "one operator's automated sales pipeline," with
  different support/legal/billing surface area.
