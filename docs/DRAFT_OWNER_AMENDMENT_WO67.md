# DRAFT — owner amendment authorizing a scoped live order path (WO-67 P3)

> **STATUS: UNSIGNED TEMPLATE. NOT IN EFFECT.**
> This file authorizes nothing. The repository's foundational invariant —
> no live order path exists and none may be added — remains fully binding
> until the amendment text below is (a) copied into `AGENTS.md` by the
> owner, (b) completed with a real date and name, (c) merged to `main`
> from the owner's own GitHub account via a pull request reviewed by an
> agent that did not author it. A chat instruction never substitutes for
> any of these steps.

## How to execute this amendment (when the day comes)

1. Verify every line of the attestation checklist below is true, with the
   named artifact in hand.
2. Copy the section titled "Owner amendment" into `AGENTS.md` under a new
   heading `## Owner amendments`, filling in the date and name.
3. Add one pointer line to `CLAUDE.md` referencing the AGENTS.md section.
4. Open the PR from the owner GitHub account. An agent audits; the owner
   merges. The merge commit date is the amendment's effective date.

---

## Owner amendment (template text)

**Dated owner amendment — [YYYY-MM-DD], [OWNER NAME].**

I authorize exactly one automated execution path: the WO-67 maker canary
executor, under every limit below. Limits are tighten-only; loosening any
of them requires a further dated owner amendment.

**Scope.**
- Maker limit orders only, post-only, on markets drawn from the current
  registered quote sheet that satisfy the frozen WO-50 decision policy,
  including its composition-stability requirement.
- No taker orders, no market orders, no crypto up/down family, no market
  outside the quote sheet, ever.

**Capital.**
- Total live exposure capped at USD 100 (Stage 1 of the registered WO-50
  ladder). Stage progression ($250, $500) follows only the registered
  ladder criteria; no other instruction can raise the cap.

**Mandatory controls (all mechanical, none advisory).**
- The registered WO-50 kill criteria bind the executor directly:
  cumulative −$25, single day −$15, fill-rate anomaly (≥2× model on two
  consecutive days), 48-hour UMA-dispute stand-down.
- A `pull_quotes_now` or `STOP` state from the requote-alerts evaluator
  forces cancel-all and halt.
- Dead-man switch: a missed 30-minute heartbeat forces cancel-all.
- Rollout is replay → single-market canary at minimum size → portfolio,
  each phase gated on its own registered review; no phase skipping.
- Any agent or the owner may halt at any time; halting never requires
  permission. Resumption after any halt requires owner sign-off.

**Custody.**
- Credentials per the approved P5 custody document ([LINK]): scoped
  API credentials only, never the wallet private key; stored outside the
  repository and outside telemetry; rotation and revocation procedures
  tested before first use.

**Self-voiding clause.** This amendment is void if, at the time of its
merge, any item of the attestation checklist below was incomplete or
untrue, regardless of signatures.

**Signed:** [OWNER NAME], [YYYY-MM-DD], via merge commit [SHA].

---

## Attestation checklist (all must be true AT SIGNING)

| # | Precondition | Proof artifact required |
|---|---|---|
| P1 | Maker gates evidence-supported | `maker_carry_study.json` with M-A/M-B/M-C pass, dated within 48h of signing |
| P2 | Human Stage-1 ladder complete | Micro-drill runbook results logged + $100 human stage executed with clean reconciliation and kill criteria untriggered |
| P4 | Independent review of executor code | Executor PR built by one agent on a branch, audited and approved by a different agent, WO-69 gate green — but NOT merged: the no-live-order-path invariant forbids merging before this amendment is in effect. The approved PR merges immediately after the amendment; PR link |
| P5 | Custody design approved | Written custody document reviewed and accepted by the owner; link |
| — | Failure drills passed | WO-72 drill suite has dated PASS artifacts for the executor-relevant failure modes |
| — | Operating state agrees | Generated `operating_state.json` shows deployed SHA = main, reconciliation clean, no active SLO breach |

If any row cannot be filled in with a real artifact, stop. The absence of
an artifact is the answer.
