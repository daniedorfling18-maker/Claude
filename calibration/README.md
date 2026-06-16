# Calibration record — 2026-06-15

**Decision: keep the `big5_h2h_totals` profile — `devig_method: multiplicative`,
`dixon_coles_rho: -0.08`, market `h2h,totals`, and no ratings blend for market-backed
fixtures.** The active config now matches that calibration anchor directly.

## Method
Football-Data Big Five leagues (E0, D1, SP1, I1, F1) as a free proxy for World
Cup h2h+totals (no World-Cup totals history exists to test against directly).
Metric = mean Superbru points/match (3 exact / 1.5 close / 1 outcome / 0 wrong),
with `ci_cutoff` fixed at 1.5 (the actual Superbru close rule — not a tunable).

Grid: devig {multiplicative, power} × rho {-0.04, -0.08} × mode {h2h, h2h_totals},
closing odds. **Train = 2021/22–2023/24 (5,404 matches). Validation = held-out
2024/25 (1,752 matches).** Run with the engine's built-in grid:

```
football-data-league-backtest --seasons <set> --divisions E0,D1,SP1,I1,F1 \
  --market-mode-grid h2h,h2h_totals --devig-method-grid multiplicative,power \
  --rho-grid=-0.04,-0.08 --ci-grid 1.5 --odds-set closing
```

## h2h_totals results (the live market mode)

| devig | rho | train avg pts (rank) | val avg pts (rank) | val edge vs naive |
|---|---|---|---|---|
| multiplicative | **-0.08** | 0.90387 (2) | **0.91324 (1)** | **+0.0066** |
| power | -0.08 | 0.90257 (4) | 0.90668 (2) | +0.0031 |
| multiplicative | -0.04 | 0.90442 (1) | 0.90554 (3) | -0.0011 |
| power | -0.04 | 0.90368 (3) | 0.90468 (4) | +0.0011 |

## Findings
1. **`multiplicative / -0.08` (the active `big5`) is #1 on the held-out season**
   and beats the naive low-score-favourite baseline there. Top-2 in both periods
   → the most consistent cell.
2. **The in-sample best (`multiplicative / -0.04`) does not generalise**: it tops
   train but drops to #3 on validation and its edge over naive goes *negative*
   (-0.0011) → mild overfit. Not adopted.
3. `rho = -0.08` generalises better than `-0.04`; `multiplicative >= power` in
   both periods.
4. **Totals earn their keep** on validation too (best h2h_totals 0.9132 vs best
   h2h-only 0.8978).
5. The calibration surface is flat (all cells within ~0.005 pts/match),
   consistent with the larger pooled grid under `outputs/` (10,706 matches) on
   the source project.

Ratings are deliberately fallback-only. `ratings_weight: 0.0` for market-backed
fixtures keeps the live profile aligned with the odds-only calibration above. If
no usable 1X2 market exists, the ratings prior still supplies a lower-confidence
fallback distribution.

Raw per-cell output: `oos_train_calibration.csv`, `oos_val_calibration.csv`.
