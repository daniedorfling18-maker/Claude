# Backtesting: historical and diagnostic use only

The repository contains SuperBru/World Cup and Polymarket backtest utilities
for reproducibility. They are not production schedulers, promotion evidence or
authority to run anything locally. Approved analysis runs only in an isolated
VPS container under `AGENTS.md`.

## Evidence limits

- SuperBru result joins and signal archives are historical World Cup research.
  The tournament automation is seasonal and is currently disabled by the
  committed example configuration.
- Template selection and evaluation on the same result set are in-sample.
  Comparisons, bootstrap intervals and p-values after selection are exploratory,
  not independent confirmation.
- Historical odds must be proven pre-kickoff. A latest or undated snapshot can
  contain look-ahead information and must not be treated as point-in-time.
- `src/polymarket_predictive_engine/backtest.py` is diagnostic. Its current
  signal/label join has no as-of relationship, its stake-to-P&L arithmetic is
  not an execution-faithful share calculation, and it omits venue depth/fees.
  Its output cannot feed funding, promotion or gate decisions.
- Reconstructed, modeled, shadow, paper and live evidence remain distinct.

Historical scripts and outputs remain in the tree so results can be reproduced
after their data dependencies and timestamps are independently verified. Do
not infer freshness, scheduling or profitability from their presence.

Current controls are documented in `AGENTS.md`, `docs/OPERATING_STATE.md`,
`docs/EXPERIMENT_REGISTRY.md` and
`docs/REPOSITORY_LINE_AUDIT_2026-07-21.md`.
