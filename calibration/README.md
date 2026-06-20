# Calibration record - current engine note, 2026-06-16

## Revalidation status: PENDING

Fresh calibration rerun required before this profile is considered validated on the current engine.
Run the train + held-out commands under **Current engine calibration protocol** below and fill in the TBD table.

---

## Status

This note has been regenerated for the current engine architecture, after the
Dixon-Coles and validation enhancements were added.

**Important:** the numeric table from 2026-06-15 is now treated as a legacy
benchmark only. A fresh calibration run is required before changing the active
profile or claiming that the current engine has revalidated the old edge.

The provisional decision remains: keep `big5_h2h_totals` active until the fresh
rerun disproves it.

Current provisional active profile:

- `calibration_profile: big5_h2h_totals`
- `devig_method: multiplicative`
- `dixon_coles_rho: -0.08`
- market mode: `h2h,totals`
- `odds_weight: 1.0`
- `ratings_weight: 0.0`
- `ratings.use_as_fallback_only: true`

## Why the old note needed regeneration

The earlier calibration note was based on an older solver shape. The current
engine now fits lambdas after the Dixon-Coles adjustment is considered, reports
proper distributional scores, and adds bootstrap uncertainty diagnostics in the
league backtest summary. That means old point estimates are no longer enough for
model governance.

The calibration decision must now consider both:

1. Superbru objective performance, because the live use case is expected Superbru
   points.
2. Distribution quality, because the scoreline distribution is also used for
   diagnostics, sensitivity, and downstream strategy selection.

## Current engine calibration protocol

Use Football-Data Big Five leagues as the free proxy for the live World Cup
`h2h,totals` market structure. This is still an imperfect domain proxy: club
league football is not World Cup football, but it has the market structure needed
to test whether totals improve the scoreline inversion.

Recommended split:

- Train/tune: 2021/22, 2022/23, 2023/24
- Held-out validation: 2024/25
- Divisions: E0, D1, SP1, I1, F1
- Odds set: closing odds
- Fixed Superbru close rule: `ci_cutoff: 1.5`

Run train grid:

```bash
python -m superbru_score_engine football-data-league-backtest \
  --config config.yaml \
  --seasons 2122,2223,2324 \
  --divisions E0,D1,SP1,I1,F1 \
  --market-mode-grid h2h,h2h_totals \
  --devig-method-grid multiplicative,power \
  --rho-grid=-0.04,-0.08 \
  --ci-grid 1.5 \
  --odds-set closing \
  --download \
  --max-workers 4 \
  --out-dir outputs/calibration/current-engine-train
```

Run held-out validation grid:

```bash
python -m superbru_score_engine football-data-league-backtest \
  --config config.yaml \
  --seasons 2425 \
  --divisions E0,D1,SP1,I1,F1 \
  --market-mode-grid h2h,h2h_totals \
  --devig-method-grid multiplicative,power \
  --rho-grid=-0.04,-0.08 \
  --ci-grid 1.5 \
  --odds-set closing \
  --download \
  --max-workers 4 \
  --out-dir outputs/calibration/current-engine-validation
```

## Required output files

Each run should produce:

- `football_data_league_calibration.csv`
- `football_data_league_mode_comparison.csv`
- `football_data_league_summary.json`
- `football_data_league_backtest_results.csv`
- `football_data_league_reliability_cells.csv`, unless reliability is skipped

The calibration note should be updated only after those files exist for both the
train and held-out validation runs.

## Metrics to review

Primary decision metric:

- `avg_model_points`, because Superbru expected points are the live objective.

Primary governance checks:

- `edge_vs_naive`
- `edge_vs_naive_ci_low`
- `edge_vs_naive_ci_high`
- `edge_vs_naive_p_value`, treated as an approximate bootstrap tail diagnostic
- `edge_vs_naive_significant`
- `avg_rps_1x2`
- `avg_exact_log_loss`
- `avg_total_goals_crps`
- `outcome_accuracy`
- `exact_rate`

Do not pick a profile only because it wins in-sample. Prefer the profile that is
stable on held-out validation and does not degrade the distributional scores.

## Selection rule

A profile can replace the current active profile only if it satisfies all four
conditions:

1. It is top or near-top on held-out validation `avg_model_points`.
2. Its validation `edge_vs_naive` is positive, or at least not materially worse
   than the active profile.
3. Its distributional metrics are not materially worse than the active profile,
   especially `avg_rps_1x2` and `avg_total_goals_crps`.
4. Its train and validation rankings do not show obvious overfit.

If differences are very small and confidence intervals overlap heavily, keep the
simpler or more stable profile rather than tuning to noise.

## Current-engine results table to complete after rerun

Replace `TBD` values after rerunning the current engine.

| market mode | devig | rho | train avg pts | train edge vs naive | validation avg pts | validation edge vs naive | validation edge 95% CI | validation p diagnostic | validation RPS | validation exact LL | validation total-goals CRPS | decision |
|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|
| h2h_totals | multiplicative | -0.08 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | provisional active |
| h2h_totals | multiplicative | -0.04 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | challenger |
| h2h_totals | power | -0.08 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | challenger |
| h2h_totals | power | -0.04 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | challenger |
| h2h | multiplicative | -0.08 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | market-mode baseline |
| h2h | multiplicative | -0.04 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | market-mode baseline |
| h2h | power | -0.08 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | market-mode baseline |
| h2h | power | -0.04 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | market-mode baseline |

## Legacy 2026-06-15 results, retained for comparison only

These numbers came from the previous calibration note. They should not be cited
as current-engine validation until the rerun above is completed.

| devig | rho | train avg pts, legacy | validation avg pts, legacy | validation edge vs naive, legacy |
|---|---:|---:|---:|---:|
| multiplicative | -0.08 | 0.90387 | 0.91324 | +0.0066 |
| power | -0.08 | 0.90257 | 0.90668 | +0.0031 |
| multiplicative | -0.04 | 0.90442 | 0.90554 | -0.0011 |
| power | -0.04 | 0.90368 | 0.90468 | +0.0011 |

Legacy interpretation was that `multiplicative / -0.08` generalised best on the
held-out season and that totals improved over h2h-only. This remains a useful
prior, but it is not a current-engine result.

## Notes on ratings

Ratings remain deliberately fallback-only. `ratings_weight: 0.0` for market-backed
fixtures keeps the live profile aligned with odds-only calibration. If no usable
1X2 market exists, the ratings prior supplies a lower-confidence fallback score
distribution.

Do not blend ratings into market-backed predictions unless a separate calibration
run explicitly tests and validates that blend.

## Notes on bootstrap p-values

The current bootstrap p-value should be read as a practical signal/noise
diagnostic, not as a formal parametric hypothesis test. A future enhancement
should add a paired sign-flip or permutation test for a cleaner null test of
mean paired delta equals zero.
