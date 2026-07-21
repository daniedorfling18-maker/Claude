# Owner amendment — sharp-linking funding evaluator (Route A reconciliation)

Status: **FINAL — SIGNED; EVALUATOR LANDED.**

At signing, `FUNDING_GOVERNANCE_RECONCILED = False` pending the built,
passing evaluator. WO-105 subsequently landed the evaluator and flipped the
flag in the same commit as required; the current registered state is
`FUNDING_GOVERNANCE_RECONCILED = True`. This reconciles governance only. It
does not open funding.

## Purpose

Resolve the governance contradiction recorded in WO-103 /
`OWNER_DECISION_FUNDING_GOVERNANCE.md` in the Route-A direction, and register
the sharp-linking evaluator that `docs/EXPERIMENT_REGISTRY.md` H1 already
anticipates ("a future evaluator must be registered before it links anchor
state to maker markout").

## Reconciliation (what signing decides)

1. Registry H1's sharp-anchor requirement **STANDS**. The frozen M-gates
   (M-A/M-B/M-C) remain necessary but **not sufficient** for funding.
2. The WO-93-revert record's statement that "generic reward carry may be
   funded" is **SUPERSEDED**. Generic (non-sharp-anchored) reward carry is
   **not fundable** under H1. Testing generic carry would require a separate,
   freshly pre-registered hypothesis with its own gates and out-of-sample
   window (Route B) — explicitly NOT authorized here.
3. Funding eligibility gains one **added precondition** (below). This is
   strictly tighten-only: it can only *withhold* funding the frozen policy
   would otherwise indicate; it never enables funding, loosens a gate, or
   alters the WO-50 action table or M-gate arithmetic.

## The evaluator contract (pre-registered; built only after signing)

The evaluator grades the ONE exact market the WO-50 composition rule would
fund. Aggregate/funnel counts can never satisfy it. It publishes
`outputs/maker_carry/sharp_linking_qualification.json` (atomic) and feeds the
WO-99 eligibility gate as an added condition. Fail-closed throughout: missing,
stale, ambiguous, or malformed evidence = not qualified.

Qualification requires ALL of:

- **Exact-token sharp anchor.** A fresh, unambiguous bookmaker join for the
  exact quoted token (not the fixture in aggregate), with a current executable
  Polymarket bid and ask, and the sharp consensus fair price lying inside the
  proposed maker quote band. Anchor age and price age within registered
  tighten-only maxima; anchor/venue disagreement within a registered maximum.
- **Exact-market Tier-0 replay evidence** for the same current market:
  at least the registered minimum evaluable last-in-queue opportunities and
  confirmed fills, adequate 5/15/60-minute markout coverage with minimum
  samples, and a market-level simulation-to-reality adverse haircut at or
  below the registered ceiling. (This depends on real Tier-0 coverage, which
  is ~0% today — so this precondition will correctly hold funding closed until
  the WO-104 markout-coverage work lands. That is intended.)
- **All existing conditions** (M-A/M-B/M-C pass, kill clear, composition,
  toxicity screen incl. the WO-102 absolute floor, resolution risk, size,
  reconciliation, freshness) continue to bind unchanged and independently.

Registered constants are mechanically tighten-only: maxima may only shrink,
minima may only grow; invalid overrides fall back to the registered defaults.

## What signing authorizes — and what it does NOT

Signing authorizes the orchestrator to:
1. Set the registry/policy in agreement (record this reconciliation in
   `EXPERIMENT_REGISTRY.md` H1 and supersede the WO-93-revert funding line).
2. Build the evaluator to the contract above, with recorded-fixture and
   fail-closed tests per `ENGINEERING_STANDARDS.md`, after the WO-104
   dependencies it relies on (Tier-0 coverage) exist.
3. Flip `FUNDING_GOVERNANCE_RECONCILED = True` **only** in the commit that
   lands the built, passing evaluator — never on signing alone. Funding stays
   fail-closed between signing and that commit.

Signing does NOT authorize: any autonomous order, signer, credential, or
cancellation path (WO-67 remains blocked behind P1–P5); any loosening of a
gate, threshold, stake, or the toxicity screen; or funding generic reward
carry.

## Owner signature

Sign by committing this file with the line below completed, from your own
account (an owner-authored commit is the only valid authorization; no agent
may complete it):

    Route A reconciliation APPROVED — <Danie Dörfling>, <17h20 18 July>.

paper_trading_invoked: false
live_trading_invoked: false
