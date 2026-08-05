# Engineering standards (binding on every work order)

Registered 2026-07-15 after three same-class production defects (WO-89,
WO-90, WO-91) traced to specification gaps, not implementation error. These
standards bind every future work order — filed by any agent — and every
review. They are tighten-only: a WO may demand more than this, never less.

## S1 — Time and clocks

- Every "recent"/"last N hours"/freshness computation anchors to ONE clock:
  the run's `generated_at_utc` (or wall-clock UTC now). NEVER to the maximum
  or minimum of observed data timestamps. Data-relative windows are permitted
  only for replaying recorded history, and the docstring must say so.
- Every external timestamp is normalized AT THE INGESTION BOUNDARY:
  numeric values > 10_000_000_000 are milliseconds and divide by 1000;
  strings parse via `utils.parse_timestamp`; unparseable values become
  None/0.0 and must fall on the fail-safe side of every comparison.
- Any code computing a time window MUST ship a clock-advance test: assert an
  event inside the window, advance the clock past it, assert it leaves.

## S2 — Artifact writes and concurrency

- Every artifact another process may read concurrently is written atomically:
  `utils.write_json` / `utils.write_text_atomic` / temp + `os.replace`.
  Plain `write_text`/`open("w")` on a shared output is a review-blocking
  defect.
- A WO that adds a new writer, a new cadence, or a new concurrent path must
  list every artifact both old and new paths touch and state the interleaving
  outcome for each.

## S3 — Data-dependency contracts

- A feature that reads an artifact it does not produce must state, in the WO:
  the producer, the coverage guarantee it needs (which rows/tokens/windows),
  and what the feature displays when coverage is absent. "The corpus
  happens to contain it today" is not a contract.
- If the needed coverage is not already guaranteed, the WO includes the
  producer-side change that guarantees it — in the same WO or as a named
  blocking prerequisite. (WO-91 is the canonical failure: a diagnostic was
  specified against a corpus whose collection policy could starve it.)

## S4 — Tests must use recorded reality

- Any parser or consumer of an external API payload must have at least one
  fixture RECORDED from the real endpoint (sanitized), not hand-written.
  Synthetic fixtures encode the author's assumptions and cannot catch unit,
  field-name, or shape mismatches.
- Property tests are required for: time windows (S1), counters that must
  decay, dedup keys, and any fail-safe default ("unmatched stays in the
  alarmed population").

## S5 — Fail-safe direction statement

- Every WO states, per feature: "when the input is missing, stale, or
  malformed, the observable behavior is X" — and X must be the conservative
  direction (alarm, exclude, pending, STOP). The review checks the code
  against this sentence, path by path.

## S6 — Day-after production check (mandatory WO section)

- Every WO ends with a `Day-after check:` line naming the exact telemetry
  artifact, field, and expected value/behavior that proves the change works
  in production — checkable by the owner without reading code. Where
  feasible the same check is added to deploy acceptance or a watchdog
  registration so a human does not have to remember it.
- A WO without a day-after check is not buildable.

## S7 — Review checklist (the auditor runs this list, in writing)

1. Clock sources and unit normalization (S1) — quote the lines.
2. Every write path atomic where shared (S2).
3. Data dependencies contracted (S3).
4. At least one recorded-reality fixture; property tests for windows (S4).
5. Fail-safe sentence verified against each failure path (S5).
6. Day-after check present and wired (S6).
7. Frozen surfaces untouched: gates, thresholds, registered policy
   constants byte-identical unless the WO is a dated tighten-only amendment.
8. The report states what was verified by which method (static read / test
   executed / production telemetry) and never claims absence of defects —
   only the absence of findings under the methods used.

## S8 — WO admission checklist (gates REGISTRATION, not delivery)

- A draft work order is not registerable until every rule below passes. S7 is
  run by the auditor against a delivered change; S8 is run against the *text*,
  before it becomes registered specification and drives a build. Registered
  text is the most expensive artifact to get wrong: it is permanent, it drives
  builds, and it fails silently.
- The agent that runs this checklist is never the agent that drafted the work
  order. No agent both produces and approves the same artifact.
- **Each rule is derived from a specific defect found in review; the derivation
  is part of the rule.** Do not edit the attributions out — they are what makes
  the rules auditable rather than stylistic.

1. **A1 — literal thresholds.** Every threshold is a literal number with a
   stated basis. "A registered ceiling", "an appropriate bound", "a reasonable
   window" are rejected. *Derived from:* a clause requiring validation "against
   a registered ceiling" that existed nowhere in config or code, leaving the
   builder to invent a registered value.
2. **A2 — non-finite and missing inputs.** Every threshold comparison states
   what missing, empty, unparseable, and non-finite inputs do, and the stated
   answer is the fail-closed branch (S5). *Derived from:*
   `utils.safe_float("nan")` returns NaN — there is no non-finite guard at
   `src/polymarket_predictive_engine/utils.py:373-379` — and `nan > ceiling` is
   `False`, so a corrupt timestamp would have classified as *fresh*, the exact
   fail-open the clause existed to close.

   **Scope clarification:** A2's object is measurement and health
   comparisons — an unverifiable input must never read as healthy, fresh,
   compliant, or measured. The one registered exception is a scheduler
   liveness due-stamp: a stamp whose only writer is the job's own fire
   branch, per the registered WO-120 `seconds_since_stamp` convention in
   `scripts/run_vps_ops_scheduler.sh`. There, the fail-closed branch — never
   fire — is permanent silent disablement of the very measurement A2 exists
   to protect, so unreadable-means-immediately-due is the fail-safe direction
   for that stamp class alone; any new liveness stamp adopting a different
   convention must register its basis explicitly. *Derived from:* the
   WO-151 §151.2 escalation and its independent line audit, plus external
   review, 2026-08-05 — the same defect-derived pattern as A1-A9.
3. **A3 — exhaustive scan roots.** Every "scan all callers/files" rule names its
   roots exhaustively, anchors them off `__file__` rather than the process CWD,
   and asserts a non-zero visit count. *Derived from:*
   `tests/polymarket_predictive_engine/test_shadow_cohort.py` roots its scan at
   `Path("src")`, which is why it never reached the `scripts/` caller that
   motivated the work order — and which is also CWD-relative.
4. **A4 — amendments reconcile the parent.** An amendment updates the parent
   work order's Scope paragraph and touched-file list, or it is incomplete.
   *Derived from:* a Scope paragraph reading "do NOT touch either caller" while
   the same work order's own item required editing five call sites.
5. **A5 — internal contradiction check.** A new clause is checked for
   contradiction against every existing clause of the same work order. *Derived
   from:* an added-flags clause that would have changed a shared artifact on
   every path, colliding with the same work order's byte-identity guarantee for
   the unchanged scope.
6. **A6 — triggers observable at the acting site.** A trigger condition is
   stated in terms of what is observable at the site that must act, never a
   callee's return value the callee may never produce. *Derived from:* an
   antecedent keyed on a returned status on a path where the updater is never
   called, so a literal implementation leaves the defect in place.
7. **A7 — preamble counts stay true.** File counts and file lists in a preamble
   are updated by every amendment that changes them. *Derived from:* a preamble
   reading "exactly these eight files" after a later item had made it ten.
8. **A8 — numeric dismissals show their work.** A dismissal on numeric grounds
   shows the worst-case derivation including per-item fan-out, and states for
   every timeout whether it is wall-clock or per-operation. *Derived from:* a
   settlement pass dismissed at 25 x 20s = 500s when the real fan-out is up to
   three HTTP calls per position (1500s against an 1800s window), and
   `urlopen(timeout=N)` is a per-socket timeout rather than a deadline, so a
   trickling server is unbounded.
9. **A9 — dormancy claims enumerate callers.** A "dormant" or "changes nothing
   in production" claim enumerates every caller of the changed function, not
   only the hot path. *Derived from:* a dormancy claim that missed
   `_run_degraded_prediction_cycle`, which calls `run_paper_cycle` at
   `scripts/run_polymarket_local_live_loop.py:1045` and is armed in production.
10. **A10 — the house shape.** Every work order carries what S1-S7 already
    require: the exact touched-file list, the fail-safe direction sentence (S5),
    enumerated offline tests with hand-computed expectations (S4), and a
    `Day-after check:` line (S6). *Derived from:* S1-S7 themselves; A10 exists
    so admission is one gate rather than two lists.

- A draft that fails any rule returns to its drafter. Registering it with the
  failure noted is the outcome this section exists to prevent.
- The lifecycle around this checklist — gate order, resourcing by work-order
  class, and the calibration row every closed work order appends — is
  `.claude/skills/wo-lifecycle/SKILL.md`. What "registered" means is defined
  once, in the GLOBAL RULE at the top of
  `docs/POLYMARKET_CODEX_WORK_ORDERS.md`, and is not restated here.
- This section is process discipline. It grants no authorization and changes no
  gate, threshold, eligibility rule, or merge routing.

## Honest scope note

These standards raise the defect bar; they do not reach "commercial grade"
by themselves. That bar additionally requires an ENFORCED merge gate
(branch protection; currently advisory-only on the GitHub Free plan), more
than one reviewing human, and time-in-production. The registered capital
gates exist precisely so that scaling waits for that proof.

## Independent merge control

The preferred control is protected `main` with a workflow-identity-capable
required-workflow ruleset,
stale-review dismissal, approval after the latest push, resolved conversations,
admin enforcement, and no bypass actors. GitHub does not expose those controls
for this private repository on its current Free plan, so their absence must be
reported as a blocker rather than inferred from a green workflow.

Legacy branch protection that requires only the check context
`WO-69 guard and invariants` is insufficient even when GitHub records the
GitHub Actions app ID: it does not bind that context to
`.github/workflows/required-pr-gate.yml`. The audit must report
`required_workflow_identity_enforced=false` and must not classify that normal
merge path as enforced.

Workflow identity means all four immutable coordinates: the exact workflow
path, this repository's numeric ID, `main`, and the commit SHA of the latest
accepted revision that changed the required workflow. A path/ref match alone
is not enforcement; a foreign-repository or stale-workflow pin must fail
closed.

Until protected `main` is available, direct/manual merges are prohibited. A
second push-capable GitHub identity must approve the exact current head. The
repository owner must then post exactly
`/independent-merge <40-character-lowercase-head-sha>` as a comment on that
pull request. GitHub loads this `issue_comment` workflow from the default
branch, so the candidate branch cannot select or replace the write-capable
workflow definition. The lane fails closed unless all of the following remain
true immediately before its atomic SHA-bound merge:

1. the original workflow actor and actual `github.triggering_actor` are the
   repository owner, while the trusted approver of the current head is a
   distinct push-capable identity who is neither the owner nor the PR author;
2. current `main` is contained in the head, so the tested branch is not behind;
3. the newest required check and required workflow run on that exact head both
   succeeded;
4. no latest review requests changes and every review thread is resolved; and
5. the PR is open, non-draft, targets `main`, and remains mergeable;
6. the PR does not change either merge workflow or either merge-control script;
   and
7. the squash-equivalent commit uses the verified main SHA as its only parent
   and updates `refs/heads/main` non-force, so a concurrent main advance makes
   the final update fail rather than silently changing the tested base.

The repository currently has only one push-capable identity, so this fallback
is configured but operationally BLOCKED until an independent reviewer is added.
It is not branch protection and cannot technically prevent the owner from
bypassing it; the audit and operating state must continue to say so. Funding
remains CLOSED and WO-67 remains BLOCKED regardless of merge-lane state.
