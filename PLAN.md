# Growth & Compliance Plan

Written 2026-08-09, when the decision was taken to put real money into this.
Covers four questions: the sending address, UK legal compliance, better
per-niche websites, and how to get unstuck on volume.

**Not legal advice.** The law below is researched and sourced, but before
sending at volume, one paid hour with a solicitor who does data protection is
cheap next to the current penalty ceiling (see §2). Everything here is written
so that hour is short and specific.

---

## 0. The one decision that changes everything

**Filter every lead to registered companies (Ltd / LLP) before emailing.**

This started as a legal requirement and turned out to be a business upgrade:

- **Legally** it moves every recipient into the category the UK lets you cold
  email with only an opt-out (§2). Sole traders need prior consent — which a
  cold pipeline cannot obtain.
- **Commercially** an incorporated business is bigger, has a bank account, has
  a bookkeeper, and can approve a £750 invoice without it being a personal
  purchase. Sole traders are the hardest sale in the list.

So the filter that keeps you legal also raises the quality of every lead. It is
the highest-value thing to build, and it's the gate everything else sits behind.

**How:** Companies House runs a free public data API. Before a lead is emailed,
search it by name + location; proceed only on an active company whose type is
`ltd`, `plc`, or `llp`. Register for a free API key, then a lookup per lead
against the company search endpoint. `[Unverified]` on exact endpoint shape and
current rate limits — read the Companies House developer docs when building.

Where it goes in the pipeline: a new status between `researched` and
`designed`. Leads that fail become `lost` with a note, and are never emailed.

---

## 1. The sending address

**Never your home address.** It goes in the footer of every email you send, to
strangers, permanently.

### What the address has to do

Two separate jobs — don't conflate them:

1. **The footer address on marketing email.** UK rules require the recipient to
   know who is contacting them and to have a valid contact address to opt out
   to. A rented virtual address with mail forwarding satisfies this.
2. **A registered office**, if you incorporate. Since the Economic Crime and
   Corporate Transparency Act 2023 (in force from 4 March 2024) this must be an
   "appropriate address" — somewhere documents delivered by hand or post would
   come to someone acting for the company, and delivery can be acknowledged.
   **A PO Box no longer qualifies.** A staffed virtual office that offers a
   registered-office service does.

### Choosing a provider

Realistic budget is **£15–£35/month, and almost every headline price excludes
VAT** — add 20% before comparing. Watch for these, which is where the cheap
deals make their money back:

- The advertised rate is usually the annual-prepay rate; paying monthly is
  commonly 30–50% more.
- Scanning allowances are small (often ~10 items/month) then ~£1 per item.
- Forwarding is postage at cost plus a per-item handling fee.

**Checklist for the provider:**

- [ ] Explicitly offers **registered office** service (not just a mailing
      address) — needed if you incorporate, and it's the marker of a provider
      that meets the appropriate-address test
- [ ] Real staffed building, not a PO Box
- [ ] Mail scanned to email (forwarding physical post is slow and costs more)
- [ ] Price quoted **inc. VAT**, monthly rate stated, scan allowance stated
- [ ] Anti-money-laundering ID check at signup — a provider who *doesn't* ask
      for ID is cutting corners you don't want to be downstream of

### Should you incorporate?

Not required to send email, but worth it here for one reason you already
raised: **liability**. You're publishing pages carrying other businesses' names
(§4) and processing personal data at volume. A limited company puts a wall
between the business and your personal finances. The virtual address doubles as
the registered office, so the marginal cost is small.

Decision, not a recommendation — it brings filing obligations. Worth the same
paid hour as the compliance question.

---

## 2. UK compliance — what the law actually says

Your `pipeline/utils/compliance.py` is titled "CAN-SPAM compliance helpers."
CAN-SPAM is US law. You're in the UK emailing UK businesses, so the rules that
bind you are **PECR** (the marketing message) and **UK GDPR** (the personal
data). The machinery you already have is good — it's aimed at the wrong statute.

### The rule that decides your whole targeting strategy

PECR Regulation 22 bans unsolicited marketing email to an **individual
subscriber** without prior consent. The definitions are what matter:

| Recipient type | Status | Can you cold email? |
|---|---|---|
| Limited company, LLP, Scottish partnership, public body | **Corporate subscriber** | **Yes** — consent rule doesn't apply; opt-out required |
| Sole trader | **Individual subscriber** | **No** — needs prior consent |
| Unincorporated partnership (outside Scotland) | **Individual subscriber** | **No** — needs prior consent |

Plumbers, landscapers, salons and cafés — your exact target list — are very
often sole traders. Emailing them cold is the single biggest legal exposure in
the current pipeline, and §0's Companies House filter is the fix.

### UK GDPR sits on top, even for corporate subscribers

PECR says you may send the message; UK GDPR governs holding the address.

- `jane@company.com` identifies a person, so it is **personal data** even
  though PECR treats the company as corporate. `info@company.com` generally
  isn't.
- Your lawful basis is **legitimate interests**, which requires a documented
  three-part balancing test (a Legitimate Interests Assessment — purpose,
  necessity, balance). Write it once, keep it on file.
- You need a **privacy notice** reachable from the email explaining what you
  hold, why, and how to object.
- The **right to object to direct marketing is absolute**. When someone opts
  out, you stop permanently — no override, no re-add. Your `db.mark_unsubscribed`
  already does this correctly; it just needs to survive a database rebuild.

### The penalty ceiling changed this year

The Data (Use and Access) Act 2025 raised the maximum PECR fine from **£500,000
to £17.5m or 4% of global annual turnover**, effective **5 February 2026** —
aligning PECR with UK GDPR. A ~35× increase, six months old at the time of
writing. This is why the compliance work happens before the volume work, not
after.

### Build list — concrete changes to this repo

- [ ] Companies House gate before send (§0)
- [ ] Rewrite `compliance.py` header and footer for UK: sender name, trading
      name, registered/contact address, link to privacy notice, unsubscribe
- [ ] Write the Legitimate Interests Assessment and a privacy notice; host the
      notice at a stable URL and link it in every footer
- [ ] Persist the suppression list outside `leads.db` (a plain append-only file
      too) so an opt-out cannot be lost to a database rebuild
- [ ] Record, per lead: where the address came from, when, and the Companies
      House match — that provenance record is your evidence if challenged

### Sources

- [PECR reg 22 / corporate vs individual subscriber](https://www.salespeople.co.uk/explained/cold-email-pecr-regulation-22) · [Keepabl](https://keepabl.com/news/b2b-email-marketing-rules-pecr/) · [RD Marketing](https://rdmarketing.co.uk/knowledge-hub/pecr-explained-for-uk-b2b-email-campaigns/)
- [DUAA 2025 fine increase](https://www.mfmac.com/insights/data-protection/data-use-and-access-act-2025-increased-maximum-fines-for-cookies-and-direct-marketing-practices/) · [Mayer Brown](https://www.mayerbrown.com/en/insights/publications/2025/06/the-data-use-and-access-act-pecr-reform-rules-relating-to-electronic-marketing-and-cookies-in-the-uk)
- [Legitimate interests for B2B cold email](https://leadistry.co.uk/blog/cold-email-uk-gdpr-legitimate-interest) · [ea.partners](https://ea.partners/post/is-cold-email-legal-in-the-uk)
- [ECCTA registered office rules](https://www.1stformations.co.uk/blog/new-rules-for-registered-office-addresses/) · [PwC](https://www.pwc.co.uk/services/legal/insights/economic-crime-and-corporate-transparency-act-2023.html)
- [Virtual office pricing](https://betaoffice.uk/blog/how-much-does-a-virtual-office-cost-in-the-uk) · [hidden costs](https://wezoo.com/insights/cheap-virtual-office-address)

The ICO's own guidance is the authority above all of these; its site was
unreachable from the build environment, so verify the table in §2 against
ico.org.uk directly before you send.

---

## 3. The previews themselves — legal hygiene

You asked whether this could get you sued. Three real exposures, all cheap to
close:

1. **Their images and logo.** Copying photos or a logo off their site into the
   preview is copyright infringement. `lead_agent.py` currently scrapes text
   only and respects `robots.txt` — keep it that way. Use licensed stock, and
   never lift their logo.
2. **Customer confusion / search competition.** A public page carrying their
   name could rank in search or be mistaken for their official site. Add
   `<meta name="robots" content="noindex,nofollow">` to every preview template
   and a `robots.txt` disallow on the preview host. **Not currently
   implemented** — I checked.
3. **A claim you don't honour.** The email says *"This preview is live for 7
   days — after that it'll be repurposed."* Nothing in the code expires
   anything. Either enforce it (a `created_at` check on the preview route) or
   stop saying it. An unenforced claim is the kind of small dishonesty that
   costs you the sale when someone checks.

Plus: an instant, no-questions takedown on request, and a plain line on the
preview saying it's an unofficial concept by you and not affiliated with them.
That line costs a little persuasive punch and buys a lot of safety.

---

## 4. Better websites, per niche

Your instinct — spawn an agent that understands the vertical — is right about
*where* the quality comes from and wrong about *when* to spend it.

**Don't run an agent per lead.** It's slow, costs per site, and gives you a
different site every time, so you can never tell whether a change helped.

**Do build a niche pack, once, per vertical.** For each niche:

- A hand-tuned template with a layout that suits that trade (a landscaper needs
  a gallery; a plumber needs a phone number above the fold and an emergency
  callout line)
- A trade-specific service vocabulary and section order
- A copy prompt that knows what that trade's customers actually search for

Then at run time the only LLM call is **filling copy fields** — headline, about
paragraph, service descriptions — grounded in what you scraped from their real
site. Fast, cheap, consistent, and testable.

Use Claude to *build* the niche packs (a long, high-effort session per vertical,
which is exactly what it's good at) rather than to run each lead.

### Model and cost

The repo currently drafts with Hugging Face Mistral-7B. Moving copy generation
to the Claude API is a clear quality step up. Use `claude-opus-5` — at
**$5/M input, $25/M output**, a site's copy (~2k in, ~500 out) is roughly
**2.3p per site**, so ~£2.30/day at 100 sites. Prompt-caching the niche pack
prompt cuts the input side by ~90% on repeat calls.

If cost ever bites, `claude-sonnet-5` ($3/$15) is the step down — but that's
your call to make on quality, not a saving I'd take by default at these volumes.

### Which niche first

Pick **one** and go deep — you're right that it's the better option. My pick for
you: **landscaping and grounds maintenance.**

- You work at a garden centre. You know the trade vocabulary, the seasonal
  cycle, and what these businesses actually sell. That's an edge no competitor
  buying a scraper has.
- Job values are high (£1,000s), so £750 for a site that wins one extra job is
  an easy argument.
- There's already a `landscaper` template in the repo.
- The Companies House filter (§0) automatically steers you to the incorporated,
  larger operators — grounds-maintenance contractors rather than one-van
  gardeners. Better able to pay, and legal to email.

Restaurants are the weaker choice: low margin, time-poor, saturated by agencies,
and increasingly served by Instagram and delivery platforms instead of a site.

---

## 5. Volume — the real bottleneck, and the unlock

You spotted the tension yourself: you can't generate 1,000 mock sites a day. Two
answers — one technical, one arithmetic. **The arithmetic one matters more.**

### The technical unlock: stop deploying one project per lead

Today every lead gets its own GitHub repo *and* its own Vercel project, built
and deployed before anyone has shown a flicker of interest. That's what makes
1,000/day impossible, and it's unnecessary.

**Serve every preview as a static page from one host you already run.**

- Generating 1,000 static HTML files a day is trivial — the templates render in
  milliseconds.
- `webhook_server.py` is already a Flask app on a public URL. Add a
  `/p/<signed-token>` route that serves the rendered file for that lead. Same
  signing approach `utils/tracker.py` already uses.
- The email's hook is the **screenshot plus a working link** — both still work.
- Only when a lead says yes do you create the real GitHub repo and Vercel
  deployment. That's where the existing `transfer` flow already lives.

This removes GitHub and Vercel from the cold path entirely. It also removes a
commercial-use question: Vercel's Hobby tier isn't for client work, and 1,000
dead projects a day would be a problem on any plan.

### The arithmetic: 1,000/day is the wrong target

Deliverability first: sustainable cold-email volume is roughly **30–50/day per
mailbox** `[Inference]`, so 1,000/day means ~25 mailboxes across ~10 domains,
each needing weeks of warm-up before it carries full volume. Real money, real
setup time, and a lot of reputation to lose at once.

But the harder limit is you. Work it forward:

| Emails/day | /month (22 days) | Positive replies @1% | Sales @25% close | Revenue @£750 |
|---:|---:|---:|---:|---:|
| 50 | 1,100 | 11 | ~3 | ~£2,000 |
| **100** | **2,200** | **22** | **~5.5** | **~£4,100** |
| 1,000 | 22,000 | 220 | ~55 | ~£41,000 |

`[Inference]` on the 1% positive-reply and 25% close rates — you have no data
yet, which is the point.

At 100/day you're already fielding roughly one serious conversation per working
day, and building ~5 real sites a month, alongside a job. **That is at or past
your delivery capacity.** At 1,000/day you'd burn 200 leads a month you never
replied to, and torch the domain reputation you spent weeks building.

**So: target ~100/day.** One well-warmed domain, two or three mailboxes. Spend
the effort you would have spent on 25 mailboxes on conversion rate instead —
better sites, better copy, faster replies. Ten times fewer emails at three times
the conversion is the same revenue, a tenth of the risk, and a business you can
actually service.

Revisit 1,000/day when you have a real conversion number and someone other than
you handling replies. The infrastructure change above means the pipeline will
already be capable of it when that day comes — you just turn up the dial.

### What's still missing to know any of this

There is currently **no funnel measurement**: no reply rate, no click rate, no
cost per reply. Build the counter before the volume. Every number in the table
above is a guess until the dashboard shows the real one — and picking the right
volume is impossible without it.

---

## 6. Money, roughly

| Item | Cost | When |
|---|---|---|
| Virtual address (inc. VAT, monthly) | ~£20–£40/mo | Before first send |
| Domain for sending | ~£10–15/yr | Before warm-up |
| Mailboxes (2–3) | ~£15–20/mo | Before warm-up |
| VPS (pipeline + previews + webhooks) | ~£4–5/mo | Before first send |
| Claude API (copy, 100 sites/day) | ~£2.50/day | Once sending |
| Company incorporation (optional) | one-off, small | Your call |
| Solicitor hour on PECR/GDPR | one-off | **Before volume** |

`[Unverified]` on all figures except the Claude API rate — price them yourself;
they move.

---

## 7. Order of work

Compliance and honesty first, because they gate everything and are cheap.
Volume last, because it's worthless until conversion is measured.

1. Companies House filter (§0) — the legal gate and the lead-quality upgrade
2. UK compliance rewrite (§2) — footer, LIA, privacy notice, durable suppression
3. Preview hygiene (§3) — noindex, expiry or drop the claim, takedown route
4. Virtual address (§1) — needed before the first real send
5. Single-host previews (§5) — removes the per-lead deploy bottleneck
6. Funnel metrics (§5) — reply rate, click rate, cost per reply
7. Landscaper niche pack + Claude copy generation (§4)
8. Warm up one domain, ramp to ~100/day, measure, then decide about scale

Steps 1–4 are the ones that stop you doing something expensive by accident.
