---
name: wo-lifecycle
description: The binding lifecycle for every Polymarket work order — the standing team roster and its limits, the A1-A11 admission checklist that gates registration, the draft→register→review→dispatch gate order, resourcing by WO class F/M/D/X, and the calibration loop. Use when authoring, registering, reviewing, dispatching, building, or closing any WO, and when deciding which agent and tier to spawn.
---

# Work-order lifecycle standard

Governs **every** work order and every work order's creation — not one cycle's
agents. The damage this standard exists to prevent originated in *WO authoring*
(an unnamed threshold, a scan rooted at the wrong directory, a Scope paragraph
contradicting its own amendment), not in execution: the Sonnet builders found
zero spec mismatches and escalated correctly. The failures were all upstream.

A fixed roster would be obsolete the moment the task mix or the model lineup
changes. So what is encoded here is the *derivation rule* plus the loop that
recalibrates it, not a headcount snapshot.

**Status.** This file is process doctrine. It grants no authorization and
nothing in it may be cited as owner approval. Registered surfaces are changed
only as `AGENTS.md` provides.

## Scope boundary — read this before anything else

This standard governs WO authoring, gate ordering, agent allocation, and the
feedback loop **only**. It does not touch and cannot relax the safety
constraints: paper/dry-run posture, no live order path, no gate/threshold/
eligibility loosening, VPS-only runtime, owner-merge routing for frozen and
registered surfaces. Those come from `AGENTS.md`, and this standard defers to
them in every conflict — where the two appear to disagree, the constraint wins
and this file is the text that must change.

## Where this standard lives

| Text | File | Role |
|---|---|---|
| Operating procedure | `.claude/skills/wo-lifecycle/SKILL.md` (this file) | how the lifecycle is run |
| Admission checklist | `docs/ENGINEERING_STANDARDS.md` S8 | **canonical**; binds every WO and every review |
| Binding clause | `AGENTS.md`, "Work-order and Git discipline" | binds S8 and the GLOBAL RULE. **This file is the non-binding companion** — agent-configuration space is not register-reviewed and carries no frozen-surface protection |
| Definition of "registered" | `docs/POLYMARKET_CODEX_WORK_ORDERS.md`, GLOBAL RULE | the ancestry test; not restated here |

A1-A11 appear in this file for operating convenience. **S8 is the canonical
text**; if the two ever differ, S8 governs and this file is corrected. S8 is
also the copy that reaches the containers — `tests/test_polymarket_vps_docker.py`
mounts `AGENTS.md`, `CLAUDE.md` and `docs/` read-only, but not `.claude/`.

## Part 0 — the standing team

| role | tier | standing remit | hard limits |
|---|---|---|---|
| **Orchestrator** | Opus | adjudicate findings, author and register WOs, sequence work, own the critical path, own owner comms | never reviews its own work; never merges an owner-routed PR; never self-provisions recurring autonomy |
| **Reviewer** | **Opus** subagent | gates **registration** and **delivery** | read-and-test only; never edits, commits, or merges |
| **Engineer** | **Sonnet** subagent, worktree-isolated | builds against a precise registered spec; runs targeted tests then the full suite | escalates ambiguity instead of improvising; one WO per branch |
| **Analyst** | **Sonnet** subagent | class-X read-only traces, rate calculations, telemetry reads | **never recommends a gate or threshold change** |
| **External reviewer** | n/a — Codex (GitHub PR bot) | unsolicited PR review, arrives via webhook | findings are **adjudicated, never auto-applied**; not an Engineer |
| **Owner** | — | merges every owner-routed PR; authorises governance changes; provisions secrets and collaborators | the sole authorisation source; no agent may write, cite, or imply owner authorization in any artifact |

**Which reviewer shape gates which gate.** The registration gate attacks *text*
— omissions, unnamed thresholds, dismissals — so it uses the
`red-team-auditor` shape. The delivery gate audits *code against registered
spec*, so it uses the `line-auditor` shape. Both are Opus and both are
independent of whoever produced the artifact.

**Standing headcount: 1 orchestrator + at most 2 concurrent subagents.** Tiers
are assigned by WO class (Part 3), not by how hard the task feels.

Not on the team: Fable (credits exhausted); Codex-as-Engineer (quota, and it
serves better as the adversarial external reviewer).

Engineer test runs obey the `AGENTS.md` sandbox carve-out: the offline suite
runs in an ephemeral, network-isolated sandbox and the result is stated in the
PR. A sandbox run is never verification of record; the self-hosted ARM64
required PR gate remains the sole authority on whether a change passes.

**The one structural rule underneath all of it: no agent both produces and
approves the same artifact.** An orchestrator cannot find its own omissions.

## Part 1 — WO admission checklist (gates REGISTRATION, for every WO)

A draft is not registerable until every line passes. **Each rule is derived
from a specific defect a real review found — none is invented.** Canonical text:
`docs/ENGINEERING_STANDARDS.md` S8.

| # | rule | defect it prevents |
|---|---|---|
| A1 | Every threshold is a **literal number with a stated basis**. No "a registered ceiling", "an appropriate bound", "a reasonable window". | a clause demanded validation against "a registered ceiling" that existed nowhere; the builder would have invented a registered value |
| A2 | Every threshold comparison states what **missing / empty / unparseable / non-finite** inputs do, and the answer is the fail-closed branch. | `safe_float("nan")` returns NaN and `nan > ceiling` is `False`, so a corrupt timestamp reads *fresh* — the exact fail-open the clause existed to close |
| A3 | Every "scan all callers/files" rule **names its roots exhaustively**, anchors them off `__file__` not the process CWD, and asserts a non-zero visit count. | a remediation scan rooted at `src/` only — which is why it never reached the `scripts/` caller that motivated the WO |
| A4 | An amendment **reconciles the parent WO's Scope paragraph and touched-file list**, or it is incomplete. | a Scope paragraph reading "do NOT touch either caller" while the same WO's own item required editing five call sites |
| A5 | A new clause is checked for **contradiction against every existing clause of the same WO**. | an added-flags clause collided with the same WO's byte-identity guarantee for the unchanged scope |
| A6 | A trigger condition is stated in terms of what is **observable at the site that must act**, never a callee's return value the callee may never produce. | an antecedent keyed on a returned status on a path where the updater is never called; a literal implementation leaves the defect in place |
| A7 | File counts and file lists in a preamble are **updated by every amendment that changes them**. | "exactly these eight files" after a later item had made it ten |
| A8 | A **dismissal on numeric grounds** shows the worst-case derivation including per-item fan-out, and states for each timeout whether it is wall-clock or per-operation. | a pass dismissed at 25 x 20s = 500s when real fan-out is three HTTP calls per item → 1500s, and `urlopen` timeouts are per-socket, not deadlines |
| A9 | A **"dormant" / "changes nothing in production" claim enumerates every caller** of the changed function, not just the hot path. | a dormancy claim missed `_run_degraded_prediction_cycle`, armed in production |
| A10 | Every WO carries the house shape already required: exact touched-file list, fail-safe sentence, enumerated offline tests, `Day-after check:`. | pre-existing `docs/ENGINEERING_STANDARDS.md` S1-S7 |
| A11 | Every estimator names, per identified bias channel, the direction it pushes; a design where all channels push toward the favourable answer is not admissible without an argument that the effect exceeds the aggregate bias. | two successive maker-scaling estimators, each incapable of measuring what it claimed, with every identified bias channel pointing at the favourable conclusion |

The agent that runs the admission check is never the agent that drafted the WO
(Part 0's structural rule).

## Part 2 — gate order (binding for every WO)

```
draft → ADMISSION CHECK → register → INDEPENDENT REVIEW → merge to main
      → dispatch → build → INDEPENDENT REVIEW → owner merge → deploy
      → day-after check → calibration row
```

"register" above is the act of writing the WO text into
`docs/POLYMARKET_CODEX_WORK_ORDERS.md`. The WO only *counts as registered* once
the merge on the same line has happened and the ancestry test in that file's
GLOBAL RULE passes against the build branch.

Two rules that were bought at high cost:

- **Review gates registration, not just delivery.** Registered text is the most
  expensive artifact to get wrong: permanent, drives builds, fails silently.
  Running register → dispatch → review once left roughly 390k tokens of build
  work standing on text with seven pending corrections.
- **Registration before dispatch is machine-checked.** Run the GLOBAL RULE's
  `git merge-base --is-ancestor` test at dispatch time and record the tested SHA
  in the WO's status line. Do not substitute a timestamp comparison — the GLOBAL
  RULE explains why that test passes on a real incident and is unverifiable
  from pushed refs.

## Part 3 — resourcing, derived per WO class

Tier by **cost-of-error x detectability**, never by difficulty. **Cheap tier
gathers; expensive tier judges.** Where a task mixes both, split it rather than
paying the high tier to gather.

- **Opus** — judgment whose failure is expensive and silent: adjudicating
  findings, reviewing frozen or registered surfaces, producing or validating
  registered text, any gate-adjacent conclusion.
- **Sonnet** — mechanical execution against a spec already made precise. Once
  the enumerated tests and the exact contract are registered, the judgment has
  been spent and the spec is the check.

| WO class | drafts spec | reviews spec | builds | reviews build |
|---|---|---|---|---|
| **F** frozen/registered surface (gates, thresholds, M-A/B/C, funding, anchor registry) | Opus | Opus (independent) | Opus | Opus (independent) |
| **M** mechanical, non-frozen (collection, ops, telemetry honesty, tests) | Opus | Opus (independent) | **Sonnet** | Opus (independent) |
| **D** docs/register only | Opus | Opus (independent) | — | — |
| **X** diagnosis, no code | — | — | **Sonnet** (trace only) | Opus judges |

**Merge routing is deliberately absent from this table.** It is set by
`AGENTS.md` and the register — frozen and registered surfaces are owner-merge,
and the orchestrator merges only non-frozen PRs — and this standard does not
change it. An earlier draft of this table assigned owner-merge to whole WO
classes, which would have quietly altered routing for every non-frozen WO; that
is a governance change and does not belong in a process document.

Class X carries a standing instruction: the tracer reports raw findings and
**never recommends a gate change** — that judgment returns to Opus.

Standing rules:

- **Orchestrator ≠ reviewer.** An orchestrator cannot find its own omissions.
- **Resume over respawn.** Two cold respawns of in-flight engineers would have
  cost ~390k tokens against ~0 for resuming.
- **Concurrency is capped by invalidation risk, not capacity.** Never run
  builders alongside a review that can invalidate their input.
- **Brief reviewers to attack omissions and dismissals first.** In the review
  that produced this standard, 8 of 9 mechanisms held; the damage was in what
  was left unsaid and what was waved away.

## Part 4 — the feedback loop (build → test → measure → learn → improve)

Each WO appends one calibration row to the table under the heading
**"Calibration log"** in `docs/POLYMARKET_CODEX_WORK_ORDERS.md`:

```
WO | class | tier used per stage | tokens per agent | defects found at spec review |
defects found at build review | defects escaped to deploy | day-after result
```

Recalibration, applied every ~10 WOs:

- Spec-review defects per WO trending **down** → the admission checklist is
  working. Trending flat → add the rule that would have caught the recurring
  one, exactly as A1-A9 and A11 were derived. (A10 alone restates S1-S7 rather
  than deriving from a review defect.)
- A class-M WO whose build review finds **zero** defects across several cycles
  → candidate to drop build review to spot-check. A class-M WO where Sonnet
  escalates repeatedly → the spec was under-specified; fix authoring, not tier.
- Any defect that **escapes to deploy** forces a new admission rule, named
  after the incident.
- The day-after check is the outer loop: a WO whose day-after check cannot be
  evaluated was mis-specified at A10, and the WO is not `done`.

## Verification — how to tell this standard is working

- No build is ever dispatched against unmerged or unreviewed registered text
  (machine-checked at dispatch by the GLOBAL RULE's ancestry test).
- Spec-review defects per registered item trend down across cycles.
- Expensive-tier token share trends down while defect-escape stays flat or
  falls.
- No WO closes without its calibration row and an evaluable day-after check.

Process improvement is ranked below the research loop, not equal to it. A cycle
that improves process while the measurement loop is down has improved nothing.
