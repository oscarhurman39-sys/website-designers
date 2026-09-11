# StarNet and the pipeline: how the agents hold it

Written 2026-09-10 for MASKY, FINN and the Commander. `AGENTS.md` is the
contract every StarNet run already receives; this file is the operating
detail behind its "Running the pipeline without a human" section, plus the
questions StarNet should answer before the agents take the daily shift.

## 1. One entry point, no windows

Everything a StarNet agent needs is a `run.py` command, and the three long-
running services are managed by one of them:

```
python run.py ops start            # webhook server + ngrok + loop, hidden, idempotent
python run.py ops status --json    # {"webhook":..,"ngrok":..,"loop":..,"public_url_reachable":..,"all_up":..}
python run.py ops logs loop -n 60  # what the loop has done lately
python run.py ops stop
```

Exit codes are the contract: `ops status` exits 0 only when everything
including the public URL is reachable; `preflight` exits 1 on NO-GO. An
agent never needs to parse prose to know the state.

Logs: `pipeline/logs/webhook.log`, `ngrok.log`, `loop.log`. PID files:
`pipeline/.run/`. The Desktop app (`Casey Websites.exe`) calls the same
`ops` commands, so the Commander and the agents always see one truth.

`start_all.bat` and `install_autostart.ps1` still exist for a human who
wants visible console windows. Agents must not use them.

## 2. The daily shift (a routine, not a conversation)

```
1. ops status --json            -> if not all_up: ops start; if still not: report and stop
2. preflight                    -> NO-GO: report the failing line, do nothing else
3. control                      -> owner, next action, stage age, stalled/inconsistent work
4. report                       -> funnel per niche (leads / emailed / replied / won / revenue)
5. ops logs loop -n 80          -> errors, alerts, anything "needs a human"
6. lane work (section 3)        -> take only rows owned by your lane; code still goes through FINN
7. handoff (AGENTS.md format)   -> to FINN/MASKY; decisions needed -> the Commander
```

Expected runtime for steps 1-5: under two minutes. Nothing in the shift
flips `ENABLE_LIVE_SEND`, `SOURCING_ENABLED`, or Stripe mode. Those remain
Commander-only, by hand, in `.env`.

`control --json` is the machine-readable assignment contract. Severity sorts
`blocked` -> `action_due` -> `stalled` -> `watch` -> `on_track`; agents work
the highest row assigned to their `agent_id`, then run the command again.
Terminal lost/unsubscribed records are hidden unless `--all` is requested.

## 3. Who holds what

The three agents the Commander named, mapped to the pipeline's real seams:

| StarNet agent | Holds | Reads | Changes (via FINN's review) |
|---|---|---|---|
| **AGENCY-DESIGNER** (`webdesigner`) | The preview a prospect sees | `pipeline/out/*.png` from `render_preview.py --all-widths`; `docs/PHOTOS_AND_LOGO_PLAYBOOK.md` | `templates/modern/`, `NICHE_THEMES` in `design_agent.py`; `run.py rebuild <id>` after a client sends photos |
| **AGENCY-NEGOTIATOR** (`negotiator`) | Every reply and every price | Slack `#leads` alerts; `run.py dashboard`; `ops logs loop`; `docs/CLIENT_PROPOSAL_AND_TERMS.md` | The negotiation prompt and classifier in `sales_agent.py`; the follow-up copy; escalation wording. Never the price band (code-enforced) |
| **ACCESSIBILITY CHECKER** (`a11y`) | Who a page locks out | The same renders, at phone width first; the live preview URLs in the `websites` table | Contrast, focus, alt text, tap targets in `templates/modern/` and the accent rules (`accent_pair`, WCAG tests in `tests/test_design_context.py`) |

Shared rules: a change is a branch + tests + `preflight` still GO/READY, handed
to FINN. The repo personas under `.claude/agents/` (`client-assets`,
`cold-email-drafter`, `email-compliance`, ...) are advisory prose for code
assistants; the StarNet agents above are the operators and outrank them.

## 4. What "communicate directly" means in practice

- **Agents to pipeline**: `run.py` commands and code changes. There is no
  chat interface into the pipeline and there should not be one; the loop is
  deterministic on purpose.
- **Pipeline to agents**: Slack `#leads` (positive replies, human-needed
  alerts, paid leads), `ops logs loop`, and `run.py report`. A StarNet routine
  that reads `#leads` gives the negotiator its trigger; a routine over
  `pipeline/out/` gives the designer and the a11y checker theirs.
- **Agents to each other**: `docs/AGENT_WORKBOARD.md` (active lanes) and the
  handoff format in `AGENTS.md`. Not the `.task-*.txt` scratch files.
- **Anything with money or outbound**: the Commander, in `.env`, by hand.

## 5. Questions for StarNet (answer in docs/AGENT_NOTES.md)

1. **Routine or agent?** Should the daily shift (section 2) be one StarNet
   routine owned by LIL BEAR that posts to `#leads`, or MASKY's night shift?
   Routine delivery must be re-saved from the target chat session or it
   fails silently ("origin delivery has no captured channel target").
2. **Trigger for the negotiator.** Is a Slack `#leads` message a usable
   trigger in StarNet today, or does the negotiator poll `ops logs loop`?
3. **Model lane.** Confirm all three agents are pinned to an unmetered Codex
   model explicitly (not "follow station default") and the fallback chain is
   non-empty, per `starnet-station-operations` memory. The shift dies silently
   otherwise.
4. **Reach.** Do the three agents have project-folder reach on
   `D:\projects\website-designers` plus terminal, and nothing wider? They need
   nothing wider.
5. **Screenshots.** Can the designer and a11y checker read `pipeline/out/*.png`
   directly (IMAGES prop), or do they need the panel's "Render sample sites"
   run first by a human?
6. **What did the first shift find?** After the first real `ops start ->
   preflight -> report` run by an agent, paste its handoff here verbatim.
