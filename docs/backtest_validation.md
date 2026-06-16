# Backtest validation metrics

The main `backtest` command now reports more than average Superbru points. This is intended to stop the model from being judged on a single noisy metric.

## Files written

The command still writes the existing files:

- `calibration_results.csv`
- `backtest_results.csv`
- `reliability_cells.csv`
- `backtest_summary.json`

It also writes:

- `low_score_calibration.csv`
- `favourite_band_calibration.csv`
- `baseline_metrics.json`

## Summary metrics

`backtest_summary.json` includes:

- `matches_scored`
- `average_model_points`
- `average_naive_points`
- `model_edge_vs_naive`
- `exact_score_hit_rate`
- `right_result_accuracy`
- `close_score_rate`
- `average_expected_points`
- `average_actual_points`
- `expected_vs_actual_points_gap`
- `brier_score_1x2`
- `log_loss_1x2`
- `draw_calibration`
- `favourite_band_calibration`
- `low_score_calibration`
- `baseline_metrics`

## Baselines

The main backtest now scores several baselines under the same Superbru scoring rules:

- the model recommendation
- the naive low-score favourite baseline
- favourite 1-0 or 0-1
- favourite 2-0 or 0-2
- draw 1-1
- modal exact-score baseline

Each baseline reports average points, exact hit rate, right-result accuracy, and close-score rate.

## Calibration warnings

The summary includes `validation_timing_note`. Historical or saved odds may be closing odds or have unknown timing, so they should not automatically be treated as equivalent to early pre-match Superbru picks.

Football-Data league validation remains a proxy validation. It is useful for testing market structure, such as h2h-only versus h2h plus totals, but it does not replace a World Cup-specific validation.

## Interpretation

A model can score well on average points but still be poorly calibrated. Use the additional metrics to check whether:

- exact-score probabilities are realistic;
- draw probabilities are calibrated;
- favourite probabilities behave sensibly by band;
- low-score probabilities are not over- or under-stated;
- the model only beats weak baselines, or also beats more direct scoreline baselines.

Do not claim a proven Superbru edge from one backtest run. Treat the validation as evidence, not proof.
