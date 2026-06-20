# SmartBet Grid Calibration

This workflow converts Oddspedia SmartBet correct-score probability grids into calibration inputs.

Use it when uploaded grid CSVs contain columns such as:

- `home_name`
- `away_name`
- `actual_score`
- `winner_home_pct`
- `winner_draw_pct`
- `winner_away_pct`
- `modal_score`
- `home_goals`
- `away_goals`
- `probability_pct`

## Input location

Put all SmartBet grid CSVs in:

```text
inputs/smartbet_grids/
```

The converter can also accept explicit file paths.

## Convert grids

```powershell
python scripts\convert_smartbet_grids_to_calibration.py `
  --smartbet-grid-glob "inputs/smartbet_grids/*.csv" `
  --out-dir outputs/smartbet_grid_calibration
```

Or pass explicit files:

```powershell
python scripts\convert_smartbet_grids_to_calibration.py `
  --smartbet-grid-csv inputs/smartbet_grids/smartbet_correct_score_uploads_2026_06_20.csv `
  --smartbet-grid-csv inputs/smartbet_grids/smartbet_correct_score_uploads_2026_06_20_updated.csv `
  --out-dir outputs/smartbet_grid_calibration
```

## Outputs

The converter writes:

- `smartbet_predictions_baseline.csv`
- `smartbet_results_to_date.csv`
- `smartbet_grid_calibration_detail.csv`
- `smartbet_grid_calibration_summary.json`

## Run calibration diagnostics

```powershell
python scripts\run_calibration_diagnostics.py `
  --predictions-csv outputs/smartbet_grid_calibration/smartbet_predictions_baseline.csv `
  --results-csv outputs/smartbet_grid_calibration/smartbet_results_to_date.csv `
  --out-dir outputs/smartbet_grid_calibration/calibration_diagnostics
```

## Rebuild validation report with SmartBet calibration

```powershell
python scripts\build_pick_validation_report.py `
  --final-picks-csv outputs/final_leader_decision_round_summary_profiles/final_picks.csv `
  --final-report-json outputs/final_leader_decision_round_summary_profiles/final_decision_report.json `
  --stress-support-csv outputs/final_leader_decision_round_summary_profiles\stress\stress_switch_support.csv `
  --predictions-csv outputs/latest/predictions.csv `
  --calibration-summary-json outputs/smartbet_grid_calibration/calibration_diagnostics/calibration_summary.json `
  --out-dir outputs/pick_validation_report
```

## Notes

- The baseline uses the SmartBet modal score where available and present in the grid; otherwise it uses the highest-probability cell.
- `p_exact` is the SmartBet exact-score probability for the recommended scoreline, converted from percentage to decimal.
- `p_outcome` is the SmartBet outcome probability corresponding to the recommended scoreline.
- `smartbet_results_to_date.csv` only includes rows where `actual_score` is present.
- More completed grids make the calibration more reliable.
