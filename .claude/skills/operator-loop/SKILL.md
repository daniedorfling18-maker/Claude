---
name: operator-loop
description: The operator tick for the Polymarket research repository — read the state brief, apply the owner's priority order, take exactly one action (dispatch, escalate, or stop), and record it. Triage and route only; it never builds, merges, or authorizes. Use only when invoked as the scheduled operator session or when the owner asks for a tick.
---

# Operator loop

One tick does one thing. It reads the state, decides the single highest-value
next action under the priority order below, takes it, and stops. It does not
build; it dispatches builders. It does not merge; it escalates merges. It does
not decide anything the venture thesis or `AGENTS.md` reserves for the owner;
it queues those decisions with the evidence attached.

**Status.** Process doctrine in agent-configuration space. It grants no
authorization and nothing in it may be cited as owner approval. Registered
surfaces change only as `AGENTS.md` provides. Where this file and `AGENTS.md`
or `docs/VENTURE_THESIS.md` appear to disagree, they win and this file is the
text that must change.

## Why this exists

The repository's constraint is not production. On 2026-10-18 it held 260
remote branches, 8 open pull requests dating from August, and a `main` that had
not moved in five weeks — after a session in which two work orders passed four
admission checks and two line audits and still did not land. A loop that adds
agents without a value function does more of that, faster. This file is the
value function.

## The tick, in order

1. **Read.** Run `python3 .claude/skills/operator-loop/state_brief.py` (add
   `--no-network` if the proxy refuses). Read `escalations.md` in this
   directory. Read nothing else until step 3 asks for it.
2. **Check the constraints.** If anything in the brief suggests a live order
   path, a loosened gate, capital in motion, or an engine running off the
   VPS, stop and escalate that alone. Nothing below applies.
3. **Apply the priority order** (next section) top down. The first rule whose
   condition holds names the action. Take only that one.
4. **Act.** One of exactly three things:
   - *Escalate*: append one item to `escalations.md` as `- [ ] <decision>
     — <evidence, one line> — <what the owner's yes and no each unblock>`.
     Never re-add an open item; never close one.
   - *Dispatch*: spawn one builder or one auditor against one registered work
     order, citing the entry by section. The lifecycle in
     `.claude/skills/wo-lifecycle/SKILL.md` governs the dispatch.
   - *Stop*: write "nothing passes the filter" with the reason. Idle is a
     valid, common, and correct outcome of a tick.
5. **Record.** One line in `log.md` in this directory: date, rule that fired,
   action, and what it cost. That line is the loop's own evidence.

## Priority order (the owner's; derived from `docs/VENTURE_THESIS.md`)

The thesis was filed 2026-07-11, before the verdict, with triggers bound to
dates. The terminal verdict resolved NO on 2026-08-19. **Outcome B is in
force.** Under it the thesis says: no new trading lanes for 30 days; then
customer discovery; then a written decision — productize, shelve, or
new-market restart with a fresh registration. And: *no new measurement lanes;
the constraint is evidence-days and distribution, not code.* The order below
is that text made executable. The owner edits it; the loop does not.

| # | If… | Then… |
|---|---|---|
| 1 | The written Outcome-B decision has not been recorded | Escalate it — once. It is the top item until decided, and nothing below builds anything new while it is open. |
| 2 | A pull request is audited, green, and owner-routed | Escalate the merge with the audit verdicts attached. Build nothing new while an audited PR waits. |
| 3 | The required gate cannot execute (runner down, check queued >24h) | Escalate the gate: split the offline suite onto a hosted runner, or restore the self-hosted one. Every PR is blocked on this. |
| 4 | A record disagrees with its artifact (front page, evidence document, register status line) | Dispatch one correction, class D or M, with the hygiene budget below. Honesty of the record outranks new work. |
| 5 | An existing registered lane is unread or untested for an operational reason (H2's verdict artifact unread; H3's collector stopped) | Escalate: the VPS back for *collection only*, no order path. The loop may not restore it. |
| 6 | The owner has recorded a decision to restart | Dispatch the fresh registration the thesis requires, through the full lifecycle. |
| 7 | None of the above | Stop. |

Never proposed by the loop, under any rule: a new measurement lane, a new
venue, a gate change, capital, or anything the thesis's "What NOT to do" names.

## Budgets and the stop rule

- **One action per tick.** Not one per rule — one.
- **Two review rounds per artifact.** After the second, ship with residuals
  disclosed, or escalate if a round found a fail-open. A third round on
  documentation is never taken; a human read costs less and catches more
  (two of the four material findings in the WO-168 audits were editorial, and
  four adversarial rounds missed both).
- **One hygiene work order in flight at a time.** Hygiene is rule 4 only.
- **Stale escalations are not re-raised.** If an item has sat open across
  three ticks, the tick notes it in `log.md` and moves to the next rule;
  it does not add a duplicate or nag.
- **A defensible stop is success.** The thesis says so for the research; it
  holds for the loop.

## What the loop may not do

Merge. Author or cite owner authorization. Change a frozen or registered
surface outside a dispatched, registered work order. Run engines, tests of
runtime paths, Docker, collectors, schedulers, or dashboards anywhere but the
VPS. Restore the VPS. Open a second venue. Delete a branch that has unmerged
commits. Spend past its budget because the work "is nearly done".

## Scheduling it

The tick is meant to run as a fresh session, not a resumed one, so it starts
from the brief and not from a conversation's momentum. Once a day is enough;
the inputs move slower than that. From any session with the Claude Code
Remote tools:

```
create_trigger(
  name="operator tick",
  cron_expression="0 6 * * *",          # 06:00 UTC daily
  create_new_session_on_fire=true,
  initiation="human_request",
  prompt="Run one operator tick under .claude/skills/operator-loop/SKILL.md. Read the brief, apply the priority order, take exactly one action, record it in log.md, and stop. Do not build, merge, or restore anything.",
)
```

It is **not armed by this file**. Arming a recurring session is the owner's
call, and the loop's own rule 3 says the gate must be unblocked first or the
tick will escalate the same item every day.

## Files in this directory

| File | Role |
|---|---|
| `SKILL.md` | this procedure |
| `state_brief.py` | the one-page read; read-only; `--no-network` for a hermetic run |
| `escalations.md` | the owner's queue; the loop appends, the owner ticks |
| `log.md` | one line per tick; created on the first tick |
