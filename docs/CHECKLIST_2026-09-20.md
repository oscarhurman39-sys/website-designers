# Oscar's checklist, 2026-09-20 — status

The list Oscar set, item by item, with what was actually done and what is left.
Nothing here flipped a gate: `ENABLE_LIVE_SEND`, `SOURCING_ENABLED`, Stripe, the
price band, `EMAIL_MAX_*` and `OUTREACH_COOLDOWN_DAYS` are untouched, and `.env`
was never read.

---

### 1. Permission to control the PC, first

Done, and it is the grant this whole day's station work ran on: **StarNet
(`skynet-desktop.exe`) at full tier, granted 14:18**, with clipboard-write.
Nothing else on the machine is granted. Two popup processes that sit in front of
the app — `msedgewebview2.exe` (dropdown menus) and `textinputhost.exe` (the
touch-keyboard host) — are not granted, so dropdowns have to be driven by
keyboard, and when the touch-keyboard host takes focus the station UI is
unreachable until its window is closed.

### 2. Sort out StarNet

Partly done. Landed: STRATEGIST and RESEARCHER re-pinned to the unmetered Codex
lane; a "Weekly sales review" routine for Monday 09:00; MAX ITERATIONS capped
(was unlimited); scheduling resumed once. Still Oscar's to do:

- **MAX ITERATIONS 40 → 80.** The cap stopped MASKY at turn 41 with a finished
  patch and no handoff note. On the unmetered lane turns are free; the cap that
  protects the wallet is the budget one, which is still blank.
- **BUDGET → PER RUN 2, PER DAY 10.** Still unset.
- **The station switch is FULL POWER**, which overrides every per-agent "asks"
  setting, including AGENCY-NEGOTIATOR — the one agent that talks to customers
  about money.
- Automation is halted again; the top bar shows RESUME AUTOMATION. This does
  **not** stop the email pipeline, which is a separate process.

### 3. Talk to StarNet, query and patch the repo together

Done, three rounds, and it produced real work. See `MASKY_BOTTLENECK_REPLY.md`
and `MASKY_ROUND2_REPLY.md`. Round three's patch is merged as
`LeadAgent: widen the contact-email search beyond one subpage`.

The brief format that worked is recorded in `STARNET_HOOKUPS.md` section 8.
Two things to know for next time: give the agent its own git worktree, because
it checked the live repo out onto its own branch; and a message sent with a file
still attached in the composer will try to apply that file as a patch. One from
15 September fired that way and was rejected as corrupt, which is the only
reason it did no harm.

### 4. Enhance agentic capability

Done as a plan, not yet applied. `STARNET_HOOKUPS.md` is the list, ordered by
worth. The headline: the station holds **56 finished night-shift deliverables
nobody ever applied**, nine days of re-inventing the check `run.py preflight`
already performs. That is a missing standing job; the paste-ready text is now in
`STARNET_CREW_PLAN.md` section 2.3.

### 5. Learn what else could be hooked up

Done: `STARNET_HOOKUPS.md`. Includes the Slack `#leads` channel the pipeline
already posts to, a hook that would make the "never touch `.env`" rule
enforceable rather than advisory, and the model pins that failed 100% of every
run they were given.

### 6. Sort the failing Vercel previews

Done, and there was nothing to fix. MASKY validated every active preview link:
**81 checked, 81 pass**. The `Design failed` rows in history are propagation
errors from 08 September that healed themselves.

### 7. Send at weekends and all the time

Already true for weekends: the window is **08:00–22:00, all seven days**.
Twenty-four hours would not add one email, because the limit is upstream of the
window, and mail arriving at 3am reads as spam. If Oscar still wants it, it is
`SEND_WINDOW_START_HOUR` and `SEND_WINDOW_END_HOUR` in `.env`, which only he
edits. Recommendation: leave it.

### 8. Funnel vastly more emails out daily

The cause was found and fixed. Sourcing picked its starting bucket from the
calendar date, so the walk across 352 niche-and-town combinations advanced one
step per day; 164 leads had only ever landed in 29 of them, with Manchester 90,
Liverpool 30, Crawley 20 and York 13. The cursor is now persisted and advances
by the buckets each run visits.

With that gone, the binding constraint is `SOURCING_DAILY_LIMIT`, measured at
exactly 12 leads a day since 11 September. Twelve leads at 56% email yield is
about seven sendable a day against a cap of fifteen. Filling the cap needs the
limit near 27, which costs more Google Places quota — Oscar's call. MASKY's
merged patch lifts the yield side of that sum.

### 9. Plan the mass plumber campaign, one town in every English town

Research exists (`research/mass-plumber-campaign-plan.md`). The town list and
the staged rollout are being produced now, along with the Companies House screen
that a plumber-only campaign legally needs, and the build order for reaching
businesses that have no website at all. Those three land in `docs/research/`.

---

## The three things waiting on Oscar

1. **Restart the pipeline.** It has been running since 12:15 today, before every
   merge, so none of this is live yet. The auto-mode classifier refuses the
   command, so it has to be run by hand:
   `D:/projects/website-designers/venv/Scripts/python.exe D:/projects/website-designers/run.py ops restart`
2. **Raise `SOURCING_DAILY_LIMIT`** from 12 towards 27, or accept roughly seven
   emails a day.
3. **Decide on `rescore --park-good`**, which marks 43 healthy-site leads lost
   and deletes their previews.
