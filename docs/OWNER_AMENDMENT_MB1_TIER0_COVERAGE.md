# Owner amendment — M-B.1: require the portfolio market's own Tier-0 coverage

Status: **EFFECTIVE THROUGH OWNER MERGE OF PR #262 (2026-07-19).**

The optional signature line below remains historically unfilled. The document
itself defined the owner's merge of the landing pull request as the alternative
signature event; no agent-authored text is being substituted for that event.

This is a change to the FROZEN M-B maker gate. Per `AGENTS.md`, owner
authorization is never agent-writable: this amendment is authorized ONLY by the
owner's merge of the pull request that lands it (or an owner-authored commit
completing the signature line below). No agent may complete that line or state
that the owner approved it.

## Mechanism / why

M-B ("adverse realism") currently passes when every portfolio market carries a
MEASURED markout charge computed from public data-API prints — even when that
exact market has **zero** Tier-0 last-in-queue replay coverage. The external
audit (2026-07-17) flagged this: a print-derived markout is an estimate, and on
the one market where both existed the observed adverse ran **2.08×** the
estimate. A gate that certifies "adverse realism" should not pass on the
estimate alone when the market has no confirmed-fill evidence of its own.

## The amendment (tighten-only)

M-B passes only if, **in addition** to the existing "every portfolio market
carries a measured markout charge" condition, every portfolio market also has
its **own** sufficient Tier-0 coverage from the most recent official-book
replay. This is a logical AND with the existing condition: it can only move
M-B from pass → pending, never pending → pass.

Implemented as `_mb_tier0_coverage_sufficient(...)` in
`maker_carry_study.py`, reading `outputs/maker_carry/maker_fill_replay.json`.

### Registered constants (mechanically tighten-only)

Mirroring the WO-105 evaluator's §2 thresholds so M-B and the funding evaluator
share one bar:

| Constant | Value | Direction |
|---|---|---|
| primary book source | `official` | fixed |
| replay age bound | ≤ 26h vs the study clock | tighten-only (may shrink) |
| evaluable last-in-queue opportunities | ≥ 30 | tighten-only (may grow) |
| confirmed fills | ≥ 10 | tighten-only (may grow) |
| coverage ratio | ≥ 0.80 | tighten-only (may grow) |
| markout windows at each of 5/15/60m | ≥ 10 | tighten-only (may grow) |
| simulation-to-reality adverse haircut | ≤ 1.0 | tighten-only (may shrink) |

Config overrides may only tighten (maxima shrink, minima grow); an invalid or
loosening override falls back to the registered default. A zero-valued maximum
IS a valid (extreme) tightening and is honored; only negative or non-finite
overrides fall back. Non-finite (NaN/inf) Tier-0 values are treated as missing,
so an unknown coverage/haircut fails the gate closed rather than slipping past
a `<`/`>` comparison (Codex-review hardening, both here and in the WO-105
evaluator's §2).

### Data-dependency ordering (deliberate)

The study reads the **prior** pipeline cycle's replay — the current cycle's
replay runs after the study — so an exact-portfolio-version match is impossible
inside the study. M-B therefore uses a coarse recency bound (≤ 26h) and
per-market coverage presence. Exact-portfolio-version identity and the tight
30-minute freshness are separately and independently enforced at the **funding
decision** by the WO-105 sharp-linking evaluator (registry H1 §2). M-B is a
necessary-but-not-sufficient gate; the evaluator is the funding precondition.
This amendment does not change the evaluator, M-A, M-C, the WO-50 policy, the
registry, or any order path.

## Fail-safe statement

A portfolio market with missing, stale, wrong-source, or insufficient Tier-0
coverage evaluates M-B as **pending**. The requirement can only withhold a
pass, never create one. With no replay on disk, M-B is pending (the exact hole
being closed).

## Owner signature

Sign by merging the pull request that lands this amendment, or by committing
this file with the line below completed from your own account (an owner-authored
commit is the only valid authorization; no agent may complete it):

    M-B.1 Tier-0 coverage requirement APPROVED — <name>, <timestamp>.

paper_trading_invoked: false
live_trading_invoked: false
