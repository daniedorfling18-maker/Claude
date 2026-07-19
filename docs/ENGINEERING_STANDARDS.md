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

Until protected `main` is available, direct/manual merges are prohibited. A
second push-capable GitHub identity must review the exact current head and then
dispatch `.github/workflows/independent-pr-merge.yml` with the PR number and
that 40-character head SHA. The lane fails closed unless all of the following
remain true immediately before its atomic SHA-bound merge:

1. the dispatcher is not the PR author and is the trusted approver of the
   current head; the actual `github.triggering_actor` for a rerun must satisfy
   the same rule;
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
