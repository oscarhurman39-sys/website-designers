# Warming Up Your Sending Domain

Cold outreach lives or dies on sender reputation. A brand-new domain (or a
domain that suddenly jumps from 0 to 50 emails/day) gets flagged by spam
filters fast, and a burned domain is expensive to recover -- easier to warm
it up properly once than to fix a bad reputation later.

**The pipeline already enforces hard caps** (`EMAIL_MAX_PER_HOUR` /
`EMAIL_MAX_PER_DAY` in `.env`, defaults 20/hour and 10/day), with a random
120-300s delay between sends. Those caps protect you from *the pipeline*
sending too fast -- they do **not** warm up a new domain's reputation for
you. Reputation is built by mailbox providers (Gmail, Outlook, etc.)
observing consistent, low-volume, well-engaged sending over days to weeks
*before* those caps are ever reached. Do the warm-up first; let the
pipeline's caps be the ceiling you grow into, not the plan itself.

## 1. Prerequisites before sending anything

- [ ] Use a **dedicated secondary domain** for cold outreach (e.g.
      `mail.yourdesignco.com`), never your primary business domain. If this
      domain gets flagged, your main domain's deliverability is unaffected.
- [ ] Set up **SPF**, **DKIM**, and **DMARC** DNS records for the sending
      domain before sending a single email. Without these, most providers
      will silently drop or spam-box your mail regardless of warm-up.
  - SPF: `TXT` record authorizing your SMTP provider's servers.
  - DKIM: enable in your SMTP provider's dashboard, publish the provided
    `TXT` record.
  - DMARC: start with `p=none` (monitor only) for the first few weeks,
    e.g. `v=DMARC1; p=none; rua=mailto:you@yourdomain.com`.
- [ ] Verify forward and reverse DNS (PTR record) match if you're on a
      dedicated IP.
- [ ] Set `EMAIL_HOST` / `EMAIL_USER` / `EMAIL_PASSWORD` in `.env` to this
      secondary domain's mailbox, and `SENDING_DOMAIN` to match.

## 2. Warming up with a dedicated tool (recommended)

Services like **Mailwarm**, **Warmbox**, or **Instantly's built-in
warm-up** automate the process by exchanging emails between your new
mailbox and a network of seed accounts, opening/replying to them to
simulate organic engagement. This is the easiest path:

1. Sign up and connect the sending mailbox (`EMAIL_USER`) via IMAP/SMTP.
2. Let it run for **2-4 weeks** before pointing this pipeline at the
   domain for real outreach.
3. Most tools show a "reputation" or "deliverability" score -- don't start
   real sending until it's consistently in the healthy range (check the
   tool's own guidance; generally low-to-mid 90s%+ inbox placement).
4. You can usually leave the warm-up tool running in parallel at low
   volume even after you start real sending, to keep reputation topped up.

## 3. Manual warm-up (if you'd rather not pay for a tool)

Slower, but works:

**Week 1:** Send 5-10 emails/day, only to addresses you control or
colleagues who'll open and reply (e.g. a personal Gmail, a coworker).
Manually open, reply to, and mark as "not spam" every message. This
teaches providers your domain sends wanted mail.

**Week 2:** Increase to 15-25 emails/day. Start mixing in a handful of
real, low-stakes cold sends. Keep manually engaging with test sends.

**Week 3:** Increase to 30-40 emails/day, mostly real cold outreach now.
Watch bounce rate and spam complaints closely (see monitoring below).

**Week 4+:** Ramp toward the pipeline's caps (20/hour, 50/day). Once
you're sending at or near the caps with healthy metrics for a week
straight, the domain is warm.

Rule of thumb: **never more than roughly a 30-50% volume increase week
over week**. A sudden jump (even within the pipeline's caps) looks like
spam-bot behavior to mailbox providers.

## 4. Monitoring sender reputation while warming up (and after)

- **Google Postmaster Tools** (postmaster.google.com) -- free, shows
  spam rate, IP/domain reputation, and delivery errors for domains
  sending to Gmail. Set this up before you start sending.
- **Microsoft SNDS** (Smart Network Data Services) -- same idea for
  Outlook/Hotmail if you're on a dedicated IP.
- **mail-tester.com** -- send a test email there before each ramp-up
  stage; aim for a 9-10/10 score before increasing volume.
- **Bounce rate**: this pipeline already tracks this -- check the
  dashboard or query `SELECT COUNT(*) FROM leads WHERE status='bounced'`.
  Keep bounce rate under ~2%. A spike means your list quality dropped or
  your domain is being blocked somewhere; pause and investigate rather
  than pushing through it.
- **Spam complaint rate**: keep well under 0.1% (most providers consider
  0.3%+ dangerous). If your SMTP provider exposes a complaints dashboard,
  check it weekly.
- **Reply rate**: not a deliverability metric directly, but a sudden drop
  in replies alongside a bounce/complaint spike is a strong signal
  something's wrong with delivery, not just targeting.

## 5. If reputation drops

- Immediately reduce volume back to the last healthy stage, or pause
  entirely (`pause` command in `main.py`, or the dashboard's Pause
  button).
- Check Postmaster Tools / SNDS for the specific cause (spam rate,
  authentication failures, etc.) before resuming.
- Never fight a reputation drop by sending *more* to "prove" you're
  legitimate -- that makes it worse. Slow down, fix the root cause,
  re-warm gradually.

## 6. Once warm

Keep sending consistent day-to-day volume rather than bursty
on-again-off-again patterns, even after reaching the pipeline's caps --
consistency is itself a reputation signal. If you stop sending for more
than a couple of weeks, treat resuming like a mini re-warm (start at ~50%
of your prior volume for a few days) rather than jumping straight back to
the caps.
