# Control and retention plan

## Position

Run the agency as one software-backed queue, not a room full of autonomous
generalists. Every lead state has one accountable owner, one next action and
one visible clock; the agents take the highest-severity row assigned to them
from `python run.py control` and hand code changes through FINN.

The acquisition retention problem is currently silent prospects, not paying
customer churn: the live database has outreach and follow-up states but no
durable post-handover service lifecycle. Improve reply recovery first, then
add a separate client-success lifecycle after real wins exist.

## The 1-3-1 decision

**Question:** how should the room control work and improve retention without
creating another system beside the pipeline?

### A. Control by documents

Keep assigning work in `AGENT_WORKBOARD.md` and rely on handoffs.

- Pros: no code; easy to change.
- Cons: it drifts from SQLite, cannot detect stalled leads, and leaves agents
  interpreting who owns the same state.

### B. Control from the existing pipeline state

Generate a read-only owner/next-action/SLA queue from SQLite through `run.py`.

- Pros: one source of truth, no migration, machine-readable, testable, and
  incapable of bypassing live-send/payment gates.
- Cons: improves execution discipline but does not itself create demand or a
  full paying-client retention product.

### C. Build a second CRM/orchestrator

Create new task, ownership and client-success tables plus a dedicated UI.

- Pros: supports rich assignment, service tickets and recurring accounts.
- Cons: duplicates current lead state before the first proven wins, increases
  operator burden, and creates two places for agents to disagree.

## Recommendation

Choose **B**. `python run.py control` now turns the existing database into the
room's control plane, while `control --json` gives StarNet routines and agents
a stable queue. Do not build a separate CRM until actual paying customers
prove which post-sale work recurs.

## Ranked next moves

1. **Operate from the control queue every shift.** Run `ops status --json`,
   `preflight`, `control`, `report`, then inspect the loop log. Each agent
   handles only its highest-severity owned row; FINN remains the merge/test
   gate for code.
2. **Stop calling raw non-replies “retention.”** Track the first-contact,
   follow-up and closed-silent outcomes separately. The existing one permitted
   follow-up is surfaced as `action_due`; no reply after its response window
   is surfaced as `stalled` and should be closed for learning, not chased.
3. **Run one watched cohort, then compare conversion.** Use the existing
   reviewed-batch and live gates. Measure delivered -> reply -> qualified reply
   -> paid, by niche and website status. Do not tune copy from aggregate send
   counts alone.
4. **After the first three paying clients, add client success as a separate
   lifecycle.** Minimum states: `onboarding`, `live`, `check_in_due`,
   `renewal_due`, `at_risk`, `closed`. Assign this to a dedicated support or
   registrar lane rather than overloading lead status.
5. **Only then automate the £39/month offer.** Subscription interest is
   correctly human-escalated today. Build Stripe Billing and post-sale service
   ownership only after the service promise and workload are known.

## Ownership contract

| Lead state | Accountable owner | Control outcome |
|---|---|---|
| `new` | PROMO-MARKETER | verify target/contact route |
| `researched` | AGENCY-DESIGNER | build and validate preview |
| `designed` | PROMO-MARKETER | review/release or hold for cooldown |
| `emailed` | PROMO-MARKETER | wait, single follow-up, then close silent |
| `replied`, `negotiating`, `payment_sent` | AGENCY-NEGOTIATOR | respond/close/escalate within guardrails |
| `won` | FINN | verify payment-to-handover completion |
| `bounced` | PROMO-MARKETER | suppress and review once |
| `lost`, `unsubscribed` | PROMO-MARKETER | terminal learning/suppression |

## Riskiest assumption and kill-test

**Assumption:** poor response is mainly caused by weak queue discipline and
inconsistent follow-up, rather than offer-market mismatch or inbox placement.

**Cheapest kill-test:** run one reviewed cohort of 20-30 leads through the
new control discipline and compare it with the previous cohort on delivery,
reply and qualified-reply rates. If delivery is healthy but qualified replies
do not improve, stop polishing agent control and change the target/offer. If
delivery is weak, fix domain/mailbox reputation before judging the offer.

## Definition of done

- `python run.py control` lists every open lead with owner, action, age and
  severity, with blocked/due/stalled rows first.
- `python run.py control --json` is valid stable JSON for agent consumption.
- Closed/suppressed leads are omitted by default and retained with `--all`.
- The command is proven read-only in tests.
- The full test suite and `python run.py preflight --offline` pass.
- No sourcing, sending, pricing or payment gate is changed by this work.