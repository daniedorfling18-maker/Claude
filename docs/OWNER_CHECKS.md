# Owner checks on the agents

Created 2026-07-13 at the owner's question: "are there outstanding
checks I have to do on you?" Yes. The agents' errors are caught by
registered text and verification loops, but the owner is the only
INDEPENDENT check on the orchestrator itself. This is the protocol.
Demonstrated orchestrator failure modes that motivate it: an
integration contract missed in a "clean" audit (WO-77's bug), a
venue setting the owner had already shared treated as unknown, a
premature build authorization drafted under conversational pressure
(caught by the WO-67 registration text), and same-day self-merges
while the PR gate is advisory.

## Weekly (~10 minutes)

1. Gate-file diff: on GitHub, view the week's history of
   `profit_verdict.py`, `maker_carry_study.py`,
   `live_test_decision_policy.py`, and
   `polymarket_predictive_config.example.yaml`. Also compare any
   separately deployed config with its previously accepted effective
   values. Any code or config change to a money-gate threshold must
   have a dated tighten-only justification in its PR. The effective
   code-plus-config policy decides money; neither side may be reviewed
   in isolation.
2. Merge sample: pick any two merged PRs and read them. If a PR
   description says "audited clean", the audit must say WHAT was
   checked (including data contracts, post WO-77). "Tests green"
   alone is not an audit.
3. Ask the orchestrator one falsifiable question about a number it
   reported ("show me the artifact for X"). The answer must be an
   artifact, never prose.

## At every decision date (Jul 17, Jul 19-20, ladder stages)

4. Read the machine verdicts YOURSELF: `profit_verdict.json` and the
   maker-gate block in `maker_carry_study.json` (both in telemetry).
   The decision-policy action must match the frozen table
   mechanically. If the orchestrator's recommendation and the
   artifacts disagree, the artifacts win and the disagreement is an
   incident.
5. Confirm the orchestrator argued AGAINST action at least once when
   the registered evidence was insufficient. An orchestrator that
   always says "go" is broken; so is one that always says "wait".

## Before signing the AGENTS.md amendment

6. Commission a fresh external LLM audit of the repository (the
   2026-07-12 one materially improved the system; its successor
   should specifically audit the executor PR and this file's own
   effectiveness). Independent eyes on the execution code are the
   substance of P4 — the shared-account review comments are the
   compensating control, not the ideal.
7. Verify the attestation checklist rows against artifacts you open
   yourself, not against the orchestrator's summary of them.

## Structural fixes that reduce your check burden

- GitHub Pro + branch protection: turns self-merge from a habit the
  owner must sample into an impossibility (the last audit-P0 item).
- WO-78/79 (filed): move "notice degradation" from human reading to
  machine paging.

## Standing bias disclosure

The orchestrator generates work; batch sizes grow when it is asked
open-ended questions (the external audit's overbuild warning applies
to the orchestrator, not only to Codex). Push back on any batch whose
value to the CURRENT phase is not stated in its first paragraph.
