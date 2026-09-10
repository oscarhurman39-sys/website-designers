# StarNet setup for the daily shift: exact clicks and paste-ready text

Written 2026-09-10 from the station report. Everything below goes through the
StarNet app's own screens (runtime files must never be edited while the
station is running). Each block is: where to click, then what to paste.

What the report showed, so you know why each step exists:

- Scheduling is **not** actually halted (`cron.halt.json` says false); the
  report script's HALTED line is its known false reading. No resume needed.
- The existing routine **"Daily Sales Pipeline"** (7 PM, as MASKY) failed its
  last run with `openai/gpt-6-astra ... not supported when using Codex` and has
  a vague prompt. It gets rewritten below rather than deleted.
- The routine **"hello"** fails every hour ("no model is configured"). Disable it.
- All crew are already pinned to `codex/gpt-5.6-luna` (the lane with the
  lowest error rate), effort low. No model changes needed.
- MASKY is FULL POWER on the whole computer; AGENCY-DESIGNER is FULL POWER on
  project folders; the other agents ask before risky actions. A routine runs
  unattended, so it must run as a FULL POWER agent: MASKY.

## 1. Rewrite the routine (WORK -> AUTOMATION)

Open **Daily Sales Pipeline** -> RESCHEDULE (or edit). Set:

- Agent: **MASKY**
- Model: pick **gpt-5.6-luna under the GPT / CODEX header** from the dropdown
  (Home, then Down; never type an id). Not the one under OPENAI API.
- Schedule: cron `30 8 * * *` (08:30 every day, Europe/London)
- Delivery: the chat session you want the report in. Re-save from that
  session if it says "origin delivery has no captured channel target".
- Prompt: paste exactly this.

```
Website Designers daily shift. Work only in D:\projects\website-designers and run every command with venv\Scripts\python.exe from that folder. Do these in order and nothing else:
1. python run.py ops status --json  -> if "all_up" is false, run  python run.py ops start  and check status again. If still not all up, report the ops output and stop.
2. python run.py preflight  -> record GO / READY / NO-GO. On NO-GO, report the failing line and stop.
3. python run.py report
4. python run.py ops logs loop -n 80  -> note any error, alert or "needs a human" line.
5. Read docs/AGENT_WORKBOARD.md. If there is one bounded task in your lane that needs no Commander decision, do it: change code, run the relevant tests with  venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp/shift <test file>, and re-run preflight. Otherwise skip this step.
Never change ENABLE_LIVE_SEND, SOURCING_ENABLED, Stripe keys, prices, EMAIL_* or anything else in .env. Never run start_all.bat or install_autostart.ps1. Never email, message or pay anyone.
Finish with a handoff in the AGENTS.md format: lane touched, files changed, the command that proves it, remaining risk, decision needed from the Commander. Under 300 words.
```

Then on **hello**: DISABLE.

## 2. Fill the three agents' context.md (CREW -> AGENTS -> pick -> CONFIG -> WHAT IT KNOWS -> context.md -> EDIT)

### ACCESSIBILITY CHECKER (`a11y`)

```
Project: D:\projects\website-designers (read AGENTS.md first, then docs/STARNET_INTEGRATION.md section 3).
You hold: who a preview page locks out, and why. The previews are templates/modern/index.html rendered per business; screenshots at desktop/tablet/phone are produced by  python pipeline/render_preview.py --all-widths  into pipeline/out/*.png.
Your checks: colour contrast (accent colours come from accent_pair() in pipeline/agents/design_agent.py, tests in tests/test_design_context.py require WCAG AA), focus order and visible focus, alt text, tap-target size on phone, heading order, link text that makes sense out of context.
How you change things: edit templates/modern/ or the accent rules, run  venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp/a11y tests/test_design_context.py , re-render, and hand to FINN with the handoff format in AGENTS.md. Never invent claims or testimonials on a page. Never touch .env or the live switches.
```

### AGENCY-NEGOTIATOR (`negotiator`)

```
Project: D:\projects\website-designers (read AGENTS.md first, then docs/STARNET_INTEGRATION.md section 3 and docs/CLIENT_PROPOSAL_AND_TERMS.md).
You hold: every reply and every price conversation. Replies are classified and answered by pipeline/agents/sales_agent.py; the price band (NEGOTIATION_FLOOR / NEGOTIATION_CEILING, currently 470-589 GBP) is enforced in code and is not yours to widen. Offer: 589 one-off or 39/month; 14-day money-back; free edits for 30 days; the monthly plan is escalated to a human, never closed automatically.
Where you see the conversation: Slack #leads alerts, python run.py dashboard, python run.py ops logs loop -n 80, python run.py report.
How you change things: the negotiation prompt, the reply classifier keywords, the follow-up wording, escalation text - all in sales_agent.py - with tests in tests/test_operator_decisions.py and tests/test_sales_agent_preview_safety.py, then hand to FINN. Stripe is LIVE: a CLOSE sends a real payment link. Never email a prospect yourself; the pipeline sends. Never touch .env or the live switches.
```

### AGENCY-DESIGNER (`webdesigner`)

```
Project: D:\projects\website-designers (read AGENTS.md first, then docs/STARNET_INTEGRATION.md section 3 and docs/PHOTOS_AND_LOGO_PLAYBOOK.md).
You hold: the preview a prospect sees. One template, templates/modern/index.html + style.css, themed per niche by NICHE_THEMES in pipeline/agents/design_agent.py (colours, curated Unsplash photos, tagline, about copy, six services, headings and calls to action). Every fact on the page is real (phone, address, Google rating with 10+ reviews, map) or hidden; there is no placeholder text and tests fail if one appears.
See your work: python pipeline/render_preview.py --all-widths -> pipeline/out/*.png. Rebuild a live preview after a client sends photos/logo: python run.py rebuild <lead_id>.
How you change things: edit the template or a theme entry, run  venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp/design tests/test_design_context.py tests/test_client_assets.py , re-render, screenshot, hand to FINN. Proposals and prototypes stay out of production files until reviewed. Never invent testimonials or metrics. Never touch .env or the live switches.
```

## 3. Optional but worth it (SETTINGS)

- SETTINGS -> MODELS -> FALLBACK CHAIN is empty ("if the model fails, the run
  fails"). Add Codex gpt-5.5 after gpt-5.6-luna. This is the one control the
  dropdown makes hard; add one entry and SAVE CHAIN.
- SETTINGS -> RUNTIME -> MAX ITERATIONS 40 (0 = unlimited today); SETTINGS ->
  BUDGET -> PER RUN 2, PER DAY 10. Stops a looping shift from running all
  night.

## 4. Prove it

WORK -> AUTOMATION -> Daily Sales Pipeline -> RUN NOW, once, while you watch
COMMS. A good run ends with a handoff that quotes the preflight verdict and
the report table. Then it fires itself at 08:30 every day.
