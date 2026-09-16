# StarNet write surface: what agents may change, and how

Written 2026-09-16 for MASKY, FINN and the Commander. `docs/STARNET_INTEGRATION.md`
covers how agents *read* the pipeline and take the daily shift. This file maps the
other half: the verbs an agent may use to *change* pipeline state, ranked by blast
radius, with the gate pattern every one of them must satisfy. Nothing here is built
yet except where the "Exists?" column says so.

## 0. The finding that motivates this

On 2026-09-16 no cold email had gone out by midday. `ops status --json` showed all
three services dead with stale PID files in `pipeline/.run/`. The loop had died after
its last send at 22:36 the night before and nothing restarted it.

Two facts behind that:

1. There was no Windows scheduled task for this repo at all. `install_autostart.ps1`
   had never been run, and it registered a **logon** trigger, which on this PC (sleeps,
   never shuts down) fires roughly never.
2. StarNet's routine `Daily Sales Pipeline` (cron `15 19 * * *`, runs as MASKY) had
   been the de-facto restart path: its times match the `ops start` lines in
   `pipeline/logs/loop.log` on Sep 12, 13 and 14 exactly. The station's E-STOP was
   engaged (scheduling and loops HALTED), so it did not fire on Sep 15 and would not
   have fired on Sep 16.

The fix (`\CaseyPipelineWatchdog`, a 10-minute **timer** task running
`tools/watchdog.ps1` -> `ops start`) is in `install_autostart.ps1` and does not
depend on StarNet being un-halted. It restarts only what is missing, logs only real
restarts and failures to `pipeline/logs/watchdog.log`, and honours `pipeline/.paused`
because that flag is read by the loop process itself, not by `ops`. **A pause survives
a watchdog restart.** That makes `.paused` the one agent-level "stop everything" that
works regardless of the station's state.

## 1. What agents can do today

| Verb | Surface | Risk | Notes |
|---|---|---|---|
| Read state | `ops status --json`, `control --json --agent-id X`, `report`, `preflight`, `ops logs` | none | Exit codes are the contract. |
| Start/stop services | `ops start` / `ops stop` / `ops restart` | low | Idempotent, headless. The watchdog owns "up"; agents rarely need this now. |
| Rebuild one preview | `rebuild <id>` | low-med | Redeploys to Vercel. ID-bounded. |
| Bounded send | `reviewed-batch --send --lead N [--lead N]` | **med** | Requires `pipeline/.paused` present, takes `.reviewed-batch.lock`, refuses without `ENABLE_LIVE_SEND`, checks the public `/health` before every lead and **stops fail-closed** on the first failure. This is the model every new verb copies. |

Status per verb (first draft 2026-09-16 morning; Phase 1 notes inline):

- `transfer <id>` uses `input()` prompts in the loop's console.
- pause/resume: now `run.py pause|resume --agent-id X --reason R` (PR #16). The flag is
  still `pipeline/.paused`, which `dashboard.py`, `main.py` and `reviewed_batch.py` all honour.
- Per-lead hold: now `run.py hold|release <id> --agent-id X` (PR #16). `held_by`/`held_at`
  on `leads`; `db.list_sendable_leads_by_priority()` and `reviewed-batch` skip held leads.
- Negotiation replies are already autonomous in `sales_agent.py`, inside the code-clamped
  band, escalating through `alert_needs_human`. Agents shape the prompt via FINN review;
  they do not send around the loop.

## 2. The gate pattern: every write verb satisfies all seven

Derived from what `reviewed_batch.py` and `db.update_lead_status_unless()` already do.

1. **Explicit lead IDs only.** No verb discovers leads. `--lead N`, repeatable.
2. **`--agent-id` required**, written into `state_history.notes`. Every write names its
   author; `control` can then show who last touched a lead.
3. **Compare-and-swap.** Status writes go through
   `update_lead_status_unless(..., unless_current=("replied", "negotiating", "payment_sent", "won"))`
   so a slower agent write can never clobber a state the webhook set when Stripe paid.
4. **`--dry-run` is the default** for anything that sends or deploys; `--live` is a
   separate, explicit flag. A dry run writes nothing and prints what it would do.
5. **Fail closed** on `preflight` NO-GO and on public `/health` failure.
6. **JSON out, exit-code contract.** 0 = did it; 1 = refused, reason on stdout. An agent
   never parses prose.
7. **One lock** (the `.reviewed-batch.lock` pattern) so two agents cannot act on the
   same lead at once.

## 3. The verbs, ranked by blast radius

| # | Verb | Does | Lane | Exists? | New work |
|---|---|---|---|---|---|
| 1 | `pause` / `resume` | touch / unlink `pipeline/.paused` | FINN, LIL BEAR | **yes** (PR #16) | done; audit in `pipeline/logs/control.log`, not `state_history` |
| 2 | `hold <id>` / `release <id>` | per-lead exclusion from the send queue | PROMO-MARKETER | **yes** (PR #16) | done: `held_by`/`held_at`; queue and `reviewed-batch` skip held leads; `control` shows the hold |
| 3 | `close-silent <id>` | `emailed` -> `lost` once the one follow-up is exhausted | PROMO-MARKETER | **yes** (PR #16) | done; single guarded `UPDATE` |
| 4 | `reviewed-batch --send` | send N chosen leads | PROMO-MARKETER proposes, FINN releases | **yes** (`--agent-id` PR #16) | done |
| 5 | `rebuild <id>` | redeploy one preview | AGENCY-DESIGNER | **yes** (`--agent-id` PR #16) | done |
| 6 | `transfer <id> --github U --client-email E` | handover after `won` | AGENCY-NEGOTIATOR proposes, FINN runs | interactive only | non-interactive form. **`--revoke-own-access` stays Commander-only**: it removes the Commander's own repo access and is destructive. |
| 7 | `escalate <id> "reason"` | park a negotiation and alert a human | AGENCY-NEGOTIATOR | `alert_needs_human` yes | expose as a verb so the agent has a way *out* of autonomy, not further in |

Verbs 1-3 move no money and send no mail. Start there.

## 4. What must never become a verb

- The `.env` gates: `ENABLE_LIVE_SEND`, `SOURCING_ENABLED`, Stripe mode.
- The price band and the daily caps.
- Any second path that sends negotiation mail. The loop is the one deterministic path.
- Unsubscribe / suppression edits.
- `delete_lead_cascade` outside `cleanup-tests`.

## 5. Station posture: fix this before any verb is handed over

From `station-report.js` on 2026-09-16:

- All eleven Agency-room agents run **FULL POWER** on this repo. The safety auditor's
  baseline is none, or exactly one. FULL POWER skips every consent prompt including the
  post-taint re-confirmation: an agent that read a prospect's web page can still run
  shell.
- MASKY is on the whole-computer profile.
- Stripe is live. A payment key that is "live for agents" is spendable through
  `web_request`.
- The autonomy ledger reads 8,532 declines against 104 acts. The gates are doing their
  job; that is the argument for keeping them, not loosening them.

Recommendation: verbs 1-3 for `negotiator` and `marketer` at **asks**, not FULL POWER.
Verb 4 only from a routine that carries an explicit `unattendedGrants` entry for that
one command. Verbs 6-7 stay on the interactive surface only. FULL POWER: FINN alone, if
anyone. These are the Commander's clicks in StarNet, not repo changes.

## 6. The watchdog and the "GO before start" rule

`docs/AGENT_WORKBOARD.md` says `preflight` must print `GO (ARMED)` before an agent starts
or resumes the loop. The watchdog restarts the loop on a timer without running
`preflight` first. This is deliberate for now: a watchdog that can refuse to start on a
transient `preflight` failure is a watchdog that can silently leave the pipeline dead,
which is the exact failure it exists to prevent. The loop already fails closed on the
things `preflight` checks (mailbox login, public URL) at the moment it tries to use
them.

Open for FINN: whether `watchdog.ps1` should run `preflight --offline` (config only, no
network) and refuse on NO-GO. Cheap, and it would enforce the rule mechanically. The
cost is one more way to be silently dead. Decide, then record it here.

## 7. Proof

Each verb ships with pytest in the `tests/test_control_report.py` style (real SQLite in
`tmp_path`, `monkeypatch` on `config.DB_PATH`) covering the refusal paths: wrong status,
CAS lost to a concurrent writer, `--dry-run` writes nothing, missing `--agent-id`
refused. Then `python run.py preflight` still prints GO or READY. Handoff in the
`AGENTS.md` format.

Known hazard while proving: a bare `pytest` from the repo root also collects
`workshop/*/_patch_sources/tests/`, StarNet night-shift scratch that can hold stale test
copies importing modules that no longer exist. Run `pytest tests/`, or exclude
`workshop/` in pytest config. `run.py offline-readiness` calls bare `pytest -q` and is
broken whenever such scratch is present.

## 8. Order

1. Verbs 1-3, plus `--agent-id` on 4-5. One branch, one PR, through FINN. Money risk: zero.
2. Station posture (section 5). The Commander's clicks.
3. Verb 7, then the non-destructive half of verb 6.
4. Only then decide whether any verb runs unattended on a routine.

## 9. Phase 1 landed (2026-09-16, PR #16)

Implemented by MASKY in the live tree; reviewed, fixed and finished by Claude Code for
FINN's review. Verbs 1-3 exist; 4-5 take `--agent-id`. Decisions taken:

- Every control action appends one line to `pipeline/logs/control.log`. Pause and resume
  write **nothing** to `state_history`: the board's stage clock is each lead's latest
  `state_history` row, so a pipeline-wide pause logged per lead reset every age to "now"
  and blinded stalled-work detection. The `.paused` file records `agent_id`, `reason`,
  `paused_at`; `resume` reports `paused_by`.
- `hold`, `release` and `close-silent` are single guarded `UPDATE`s (gate 3, met).
- Locks `pipeline/.control-actions.lock` and `.reviewed-batch.lock`, both gitignored. An
  orphaned lock refuses every action until deleted; the refusal names the path.
- MASKY built in the live tree. Nothing broke, by luck: the changes were additive and
  default-neutral. `AGENTS.md` now has a "Where to build" rule: worktree, never the live tree.

Open for FINN, not blocking: `reviewed-batch` double-logs transitions the agents already
log; a hold on a `negotiating` lead is ignored by the negotiation loop yet shows as
`blocked`; held leads may belong under `watch` rather than `blocked`; the two locks could
be one.
