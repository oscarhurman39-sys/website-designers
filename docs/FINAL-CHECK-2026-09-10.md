# Final check -- 2026-09-10

Read-only review before handover to the StarNet room. Nothing in the code was changed by this
review. Line numbers are from commit `109bad6`.

> **Update, later on 2026-09-10:** items 1, 2, 4, 5 and 6 below are fixed in the commit
> "Fix the five loop bugs from the final check" (tests: `test_reply_quote_stripping.py`,
> `test_github_username_capture.py`, `test_send_requires_public_url.py`,
> `test_sourcing_request_budget.py`, `test_design_rebuild_loop.py`). Item 3 is an operator
> action and is still open: nothing runs until the autostart task is registered and
> `start_all.bat` is run. Everything from item 7 down is still open.

## Snapshot at 13:30 BST

| Item | State |
|---|---|
| Tests | 260 passed, 4 skipped (the skips are the live-network tests). Plain `pytest` fails on this PC with `PermissionError` on the system temp dir; run `pytest --basetemp=.pytest_tmp/<name>`. |
| Preflight | **NO-GO**. Public URL (ngrok) answers 404. Everything else OK. |
| Processes | **None running.** No `main.py`, `scheduler.py`, `webhook_server.py`, or ngrok. Scheduled task `website-designers` is **not registered**. `pipeline/.scheduler.pid` is stale (2026-09-09 23:51). |
| Flags | `ENABLE_LIVE_SEND=true`, `SOURCING_ENABLED=true`, Stripe LIVE. `.paused` absent. |
| DB | 32 leads: 17 `designed` (all held by the 5-day cooldown), 7 `emailed` (real sends 2026-09-09 18:07-18:47 UTC), 8 `lost` ("no usable email"). 0 inbound replies processed. 0 unsubscribes. |

Consequence right now: 7 real prospects hold emails whose unsubscribe and click links are dead,
their replies are not being read, the Stripe webhook is deaf and the 10-minute reconciliation is
not running.

## A. Fix before the loop is started again

1. **FIXED 2026-09-10 -- CRITICAL -- a quoted footer turns every reply into an opt-out.**
   `pipeline/agents/sales_agent.py:57` (`\bunsubscribe\b`, `\bstop\b`), `:673-688` (`classify_reply`
   runs negative-before-positive over the whole body), `pipeline/utils/compliance.py:100` (every
   outbound carries `Unsubscribe: <url>`), `pipeline/utils/email_utils.py:407-416` (body includes
   quoted text). Verified: `"Yes please, how do I pay?"` plus the quoted original -> `negative` ->
   `lost`, goodbye sent, address suppressed for life, no human alert (`:1339-1350`). Also
   `"can you stop by the shop?"` -> `negative`. `payment_sent` is missing from the `unless_current`
   guard at `:1347`, so a paying customer who replies "paid, thanks" is demoted too.
   Fix: strip quoted text (lines starting `>`, everything from `On ... wrote:` / `From:` header
   blocks, and our own footer marker) before classifying; drop bare `stop`; add `payment_sent` to
   the guard; alert a human on every negative classification.

2. **FIXED 2026-09-10 -- CRITICAL -- the GitHub username parser hands the repo to a stranger.**
   `sales_agent.py:1107-1123` (`extract_github_username`), `:1142-1154`, `:1287-1288`;
   `pipeline/main.py:143-173`. Verified captures: `"I don't use GitHub - is there another way"` ->
   `is`; `"GitHub: none - never used it"` -> `none`; `"github username: n/a"` -> `n`;
   `"GitHub: I don't have an account yet"` -> `I`. The capture suppresses the human alert, the next
   cycle invites that GitHub account as collaborator, marks the site transferred and emails the
   customer "it's yours".
   Fix: validate the handle shape (`^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){2,38}$`), denylist
   common words, confirm the account exists via the GitHub API before storing, and alert a human
   whenever nothing valid is found.

3. **CRITICAL (ops) -- nothing runs and nothing restarts it.**
   Register the logon task from an admin PowerShell (`.\install_autostart.ps1`), then run
   `start_all.bat`. Note the trigger is at-logon, not at-boot: the PC must log in.

4. **FIXED 2026-09-10 -- HIGH -- the loop sends while the public URL is dead.**
   Only `pipeline/reviewed_batch.py:337` fails closed on `tracker.public_endpoint_up()`;
   `send_next_pending` / `_send_cold_email_impl` (`sales_agent.py:601`, `:658-666`) and
   `send_follow_up_if_due` do not. Fix: fail closed in both, and Slack-alert.

5. **FIXED 2026-09-10 -- HIGH -- Google Places spend is unbounded once the grid saturates.**
   `pipeline/agents/sourcing_agent.py:409` (budget = leads to insert, not requests), `:309-343`
   (walks all 8 x 41 = 328 searches x up to 3 pages until it finds them; 429/RuntimeError per query
   is logged and the walk continues). Every hourly cycle can burn several hundred Enterprise-SKU
   Text Search requests for zero leads. Fix: per-run request budget, abort the run on 429 /
   RESOURCE_EXHAUSTED, remember exhausted buckets for N days.

6. **FIXED 2026-09-10 -- HIGH -- a failed send-time URL check rebuilds the preview from scratch every 60 s.**
   `sales_agent.py:588-597` demotes to `researched`; `pipeline/agents/design_agent.py:724-766`
   (`_process_lead_impl`) has no "already has a website" check -> new repo push, new Vercel deploy,
   new screenshot, new `websites` row per cycle. Fix: if `get_website_by_lead` exists, re-validate or
   `rebuild_preview` in place, with a retry cap and backoff.

## B. Fix before handover -- money and compliance

7. **HIGH -- checkout links expire after 24 h (Stripe default) and are re-sent forever.**
   `pipeline/utils/stripe_utils.py:35-55` (no `expires_at`), `sales_agent.py:1026-1030`,
   `:1045-1047`, `:1096`. Fix: store the session's creation time; re-mint on the next CLOSE/REPLY
   when it is older than about 23 h or Stripe reports it expired.

8. **HIGH -- a torn-down preview can still be sold and charged for.**
   No `torn_down_at` check anywhere in `sales_agent.py`; `emailed` is expirable
   (`pipeline/utils/db.py:628`) and not in the resurrection guard (`sales_agent.py:1367`). The paid
   handover then 404s on the deleted repo forever. Fix: on a positive reply, rebuild first if
   `torn_down_at` is set; block CLOSE when there is no live preview.

9. **HIGH -- one-click unsubscribe is advertised but rejected.**
   `email_utils.py:176` sets `List-Unsubscribe-Post`; `pipeline/webhook_server.py:45` is
   `methods=["GET"]` -> Gmail/Yahoo POST gets 405. Fix: accept POST.

10. **HIGH -- replies and bounces match by exact, case-sensitive From only.**
    `sales_agent.py:1462`, `db.py:253-256`, `:433-455`; `In-Reply-To` is stored and never read.
    A reply from a personal address, or a differently-cased address, is dropped and the 3-day
    follow-up still goes out. Fix: lowercase on store and lookup (`COLLATE NOCASE`), match
    `In-Reply-To` / `References` against `email_threads.message_id` first.

11. **HIGH -- junk and third-party contact emails are accepted and nothing validates before send.**
    `pipeline/agents/lead_agent.py:213-226`, `:287-295`: first `mailto:` wins (web-agency credit
    links), `logo@2x.png`, `privacy@`, `noreply@`, garbage from a malformed `data-cfemail`.
    `design_agent.py:740` and `sales_agent.py:574-577` only check non-empty. Fix: syntactic check,
    role/junk denylist, and prefer addresses on the business's own domain.

12. **HIGH -- one SMTP-rejected recipient stalls all cold sending.**
    `email_utils.py:192-195` raises; `send_next_pending` (`sales_agent.py:658-666`) has no per-lead
    try; all live leads have `lead_score NULL` so the same lead is first every cycle. Fix: per-lead
    try/except that marks permanent SMTP failures `bounced`.

13. **HIGH -- robots.txt fetch has no timeout; page fetch has no size cap.**
    `lead_agent.py:95-106` (`RobotFileParser.read()` -> `urlopen` with no timeout), `:185-193`. A
    silent host hangs the single-threaded loop for good. Fix: fetch robots.txt with `requests` and a
    timeout; stream page bodies with a 2 MB cap; wrap each lead in `_research_new_leads`
    (`main.py:218-220`) in its own try.

14. **MEDIUM -- a real payment raises no Slack alert; the username request is sent once, then silence.**
    `sales_agent.py:138-169` (`record_payment` only prints), `main.py:126-141`. Fix: alert on every
    payment; re-ask after 3 days; escalate after 7.

15. **MEDIUM -- the handover cannot deliver what the email promises.**
    Vercel invite body is probably the wrong shape and the role is team-wide
    (`pipeline/utils/vercel_api.py:219-247`); the confirmation says the invite was sent regardless
    (`sales_agent.py:1211-1215`, `main.py:157-173`). GitHub `permission="admin"` is ignored on
    personal repos (client gets push), and removing the owner's own access cannot work
    (`pipeline/utils/github_api.py:54-69`, `main.py:370-381`). Fix: use the repo-transfer API (or an
    org), make the confirmation copy conditional on success.

16. **MEDIUM -- the cold email is 179 words (199 with footer).**
    The 150-word cap lives only in `_parse_subject_body` (`sales_agent.py:230`), reached only via
    `draft_cold_email`, which nothing in production calls. Fix: enforce in `_plain_text_body`.

17. **MEDIUM -- auto-acknowledgements start negotiations.**
    `email_utils.py:449-470` ignores `Auto-Submitted` / `Precedence: auto-reply` / `X-Autoreply`;
    `"Thanks for your email, we'll get back to you within 2 working days"` -> `positive` -> up to 6
    priced emails to a robot. Fix: honour those headers.

18. **MEDIUM -- IMAP marks messages read before processing.**
    `email_utils.py:459-464` (`SEARCH UNSEEN` + `FETCH RFC822`). Anyone opening the mailbox on a phone
    hides that reply from the pipeline forever; a crash mid-batch loses the batch. Fix: `BODY.PEEK`,
    mark seen after handling, or track processed Message-IDs in the DB.

## C. Previews and templates

19. **HIGH -- previews deploy as indexable production sites that look like the real business.**
    `vercel_api.py:57` (`target: production`), `templates/modern/index.html:3-9,92` (no robots meta,
    title `{name} | Plumbing & Heating in {city}`, only 14 px footer text says preview). Fix:
    `<meta name="robots" content="noindex,nofollow">`, a visible "Draft preview" banner, and expire
    `designed`/`negotiating` previews too.

20. **MEDIUM -- any attachment claiming an image or SVG type is saved verbatim and published.**
    `pipeline/utils/assets.py:33,55,60,89-92`; lead matched by From header only, so spoofable. Fix:
    re-encode rasters through Pillow, reject or sanitise SVG, cap size, and catch
    `DecompressionBombError` (`:76`).

21. **MEDIUM -- hardcoded five stars regardless of rating.**
    `templates/modern/index.html:19`, `blocks/hero/*.html`, `blocks/reviews/*.html`. A 4.5 renders
    as five stars: a fabricated metric on a real business's page. Fix: render from `google_rating`.

22. **MEDIUM -- portrait phone photos render sideways.** `assets.py:103-115` strips EXIF without
    `ImageOps.exif_transpose`. One-line fix.

23. **MEDIUM -- photo handling oddities.** Client photos are interleaved with stock photos in the
    About grid (`design_agent.py:527-530`); HEIC is accepted but cannot be decoded and is silently
    dropped (`assets.py:33,76-78`); the `logo|brand|mark|icon` substring rule tags
    `Benchmark Plumbing van.jpg` as the logo (`assets.py:34,58-60`).

24. **LOW -- fabricated testimonial paths.** Legacy templates fall back to an invented quote
    (`design_agent.py:504-508`), currently gated off by `DESIGN_TEMPLATE_STYLE=modern`;
    `pipeline/force_research.py:8-19` injects "Great service, highly recommended!" and resets every
    `lost` lead. Delete `force_research.py`.

25. **LOW -- the pitch invents a weakness.** `sales_agent.py:181` falls back to "a slow or outdated
    website" whenever scraping produced no pain point, which is exactly the case for sites that
    blocked the scraper.

## D. Ops and handover

26. **HIGH -- host security.** Three orphan scheduled tasks on this PC fire every few minutes and
    point at deleted binaries: `C:\ProgramData\Mircosolt\CUTE.exe`,
    `%APPDATA%\EventViewer\eventvwr.exe`, `%APPDATA%\Microsoft\PerfMon\PerfWatson2.exe`. That is the
    shape of malware persistence. This PC holds the live Stripe key, a GitHub token with
    `delete_repo`, the mailbox password and the Slack token in `.env`. Before handover: full AV scan,
    delete the three tasks, rotate every secret in `.env`, remove the commented-out old Slack
    app-level token and SendGrid key from `.env`, delete `.env.bak-2026-09-07`.

27. **HIGH -- nothing stops two loops.** `run.py loop` and the logon task's `scheduler.py` run
    together; `start_all.bat:12` only looks for `scheduler.py`; the supervisor never reads the PID
    file it writes. Two loops double-process replies and can mint two checkout links. Fix: a lock in
    `main.py` itself; docs name one way to start.

28. **MEDIUM -- pytest runs against the live `.env`.** `tests/conftest.py:39-57` loads `.env`
    (no `.env.test` exists) and only overrides `DB_PATH`/`TRACES_PATH`; safety is per-test
    monkeypatching; test dry runs already pollute `pipeline/dry_run.log`. Fix: conftest forces
    `ENABLE_LIVE_SEND=false`, `SOURCING_ENABLED=false`, a dummy `sk_test_` key and a test log path.

29. **MEDIUM -- dependency pins do not match the venv.** `pipeline/requirements.txt` pins
    `streamlit==1.38.0` / `pandas==2.2.3`; the venv runs Python 3.14.6 with streamlit 1.59.0 /
    pandas 2.3.3, and `dashboard.py:164,261` uses the 1.59 `width="stretch"` API. A fresh install on
    another machine fails or breaks the dashboard. Fix: re-pin from `pip freeze`; state Python >= 3.12.

30. **MEDIUM -- pause, preflight and backups.** `.paused` is checked once per cycle
    (`main.py:453`); dashboard Quick-run, `test_email.py` and `quick_run.py` bypass pause, the send
    window and the caps. `preflight --offline` can print `GO (ARMED)` with the tunnel dead
    (`pipeline/preflight.py:470-517, 628-633`). The DB backup timer is process-local, so every restart
    takes a backup and only 7 are kept (`main.py:259-269`, `db.py:203-204`): four restarts on
    2026-09-09 already used four slots.

31. **MEDIUM -- docs are stale and will mislead the room.**
    `docs/HANDOVER-2026-09-08.md`, `docs/STATE_AND_PLAN.md`, `docs/AGENT_WORKBOARD.md`,
    `docs/AGENT_LOCKS.md` say nothing has been sent and both gates are false (7 real sends; both
    true). `README.md` says 20/hour, 50/day (real: 10/day, 08-18 Mon-Fri, 5-day cooldown) and
    "comes back after every reboot" (logon trigger, not registered). `AGENTS.md` and `run.py`
    omit `reviewed-batch --send [--max-seconds]`. Test counts quoted as 94/105/146/239 (real 260).
    `AGENT_WORKBOARD.md` names skills that `STARNET_SKILL_WIRING.md` says do not exist.

32. **LOW -- clutter and small traps.** `scheduler.py --check` is Linux-only (a stale PID file
    raises `OSError` on Windows); stale `pipeline/.scheduler.pid`; 0-byte `leads.db` at the repo
    root next to the real `pipeline/leads.db`; 14 `.pytest_tmp` files tracked in git despite
    `.gitignore`; hardcoded ngrok domain in `start_public.bat`; webhook server and dashboard bind
    `0.0.0.0` with no auth on the LAN; `traces.json` is rewritten whole per span from several
    processes with only a thread lock.

## What is solid

- Price band: every price that reaches an email or Stripe is clamped twice; the webhook verifies the
  raw-body signature, checks livemode / paid / mode, and is idempotent on replays; teardown cannot
  touch paid or transferred sites.
- Footer, `List-Unsubscribe` and the suppression check are unconditional at the transport layer;
  hourly and daily caps, the cooldown and the sourcing daily cap are DB-backed and survive restarts.
- Jinja autoescape covers every lead-sourced string; SQL is fully parametrised; WAL plus a busy
  timeout make three-process access safe; migrations are guarded; block assembly is deterministic.

## How this was checked

`pytest` (260 pass), `python run.py preflight` (NO-GO), live DB and process inspection, and five
parallel read-only code reviews (sales/compliance, money/handover, sourcing/DB, previews, ops/docs).
Items 1, 2, 3, 5, 6, 8, 9, 16, 26 and 29 were re-run or re-read by hand.
