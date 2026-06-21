# Validation Layer Workflow

The validation layer separates two questions:

1. Is the final decision stable?
2. Are the model probabilities calibrated against actual results?

The final leader decision pipeline answers the first question. Calibration diagnostics and the pick validation report answer the second.

## Step 1: Run calibration diagnostics

Create `inputs/results_to_date.csv` with completed matches:

```csv
match_id,home_team,away_team,home_goals,away_goals
,Mexico,South Africa,2,0
,South Korea,Czech Republic,2,1
```

Then run:

```powershell
python scripts\run_calibration_diagnostics.py `
  --predictions-csv outputs/latest/predictions.csv `
  --results-csv inputs/results_to_date.csv `
  --out-dir outputs/calibration_diagnostics
```

Best practice: use the prediction snapshot that existed before the matches were played. If `outputs/latest/predictions.csv` has been refreshed after the match, calibration may be overstated or mismatched.

## Step 2: Build the pick validation report

```powershell
python scripts\build_pick_validation_report.py `
  --final-picks-csv outputs/final_leader_decision_round_summary_profiles/final_picks.csv `
  --final-report-json outputs/final_leader_decision_round_summary_profiles/final_decision_report.json `
  --stress-support-csv outputs/final_leader_decision_round_summary_profiles/stress/stress_switch_support.csv `
  --predictions-csv outputs/latest/predictions.csv `
  --calibration-summary-json outputs/calibration_diagnostics/calibration_summary.json `
  --out-dir outputs/pick_validation_report
```

## Outputs

The validation report writes:

- `pick_validation_report.csv`
- `pick_validation_summary.json`
- `pick_validation_report.md`

## Interpretation

Per-pick labels:

- `validated`: strong enough to submit with no additional review.
- `acceptable`: reasonable pick, but not a lock.
- `fragile`: keep the model pick unless there is strong external evidence.
- `review`: should be manually reviewed before submission.

The validation score uses:

- final pick confidence tier,
- scoreline risk tier,
- stress-switch pressure,
- Monte Carlo stability,
- quality gates,
- calibration diagnostics when available.

## Important distinction

A low validation score does not automatically mean switch. It means the pick deserves manual review. The final decision policy still requires base MC, stress and confirmation to agree before a switch is accepted.
