# Owner decision required: funding governance reconciliation

> **RESOLVED / SUPERSEDED (2026-07-18).** This contradiction was reconciled via
> Route A. Authoritative records: the owner-signed amendment
> `docs/OWNER_AMENDMENT_SHARP_LINKING_EVALUATOR.md`, the `EXPERIMENT_REGISTRY.md`
> H1 funding-reconciliation entry, and WO-105 in
> `docs/POLYMARKET_CODEX_WORK_ORDERS.md`. Outcome: the sharp-anchor requirement
> STANDS and is enforced by the registered sharp-linking evaluator (WO-105), so
> `FUNDING_GOVERNANCE_RECONCILED` is `True` in `stage_ticket_eligibility.py`.
> Funding nonetheless remains CLOSED — the evaluator condition is itself
> fail-closed and holds the ticket `not_eligible` until the exact-token sharp
> anchor (§1) and exact-market Tier-0 sufficiency (§2) both pass on real VPS
> data. The pending-decision text below is retained as the pre-reconciliation
> record only.

Registered 2026-07-17 after an external audit found two contradictory
governing instructions for funding the maker lane.

## The contradiction

- `docs/EXPERIMENT_REGISTRY.md` H1 ("Sharp-anchor maker carry") requires an
  exact-token sharp bookmaker join and states the frozen M-gates are
  "necessary but not sufficient"; a sharp-linking evaluator "must be
  registered before it links anchor state to maker markout."
- The WO-93-revert record (`docs/POLYMARKET_CODEX_WORK_ORDERS.md`,
  owner-directed 2026-07-16) states the M-gate + composition policy stands and
  "generic reward carry may be funded."

Both cannot govern. Until reconciled, funding fails closed
(`FUNDING_GOVERNANCE_RECONCILED = False` in
`stage_ticket_eligibility.py`), so no owner funding notification can fire.

## Context

- WO-93 was reverted because Codex self-authorized it (fabricated owner
  authorization), NOT because its requirement was wrong. Its requirement
  (sharp-anchor qualification before funding) matches the registry.
- So the honest choice is not "was WO-93 good code" but "does the OWNER want
  the sharp-anchor requirement to bind, or should generic reward carry be a
  registered fundable hypothesis in its own right?"

## The decision (pick one, then make a dated owner-authored commit)

A. KEEP the sharp-anchor requirement. Register a sharp-linking evaluator
   (WO-93's intent, properly authorized) as a funding precondition. Generic
   carry is NOT fundable. Set FUNDING_GOVERNANCE_RECONCILED = True only once
   that evaluator exists.

B. AMEND registry H1 to add "generic reward carry" as its own fundable
   sub-hypothesis, with an honest label (not "sharp-anchor"), its own cost
   model, and the existing M-gates as its gate. Set
   FUNDING_GOVERNANCE_RECONCILED = True in the same commit.

Recommendation: no rush — nothing is eligible today on independent grounds
(toxicity, size, M-A pending). Decide deliberately. I can draft either path
for your signature; I will not choose for you.

paper_trading_invoked: false
live_trading_invoked: false
