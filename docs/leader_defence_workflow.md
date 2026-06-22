# Leader Defence Workflow

This document defines the statistically conservative workflow for defending a Superbru pool lead.

## Principle

Do not change a raw expected-value pick unless the switch survives all three layers:

1. Base leaderboard Monte Carlo approves the switch.
2. Stress testing approves the same switch across the required support threshold.
3. High-N confirmation Monte Carlo approves the same switch.

If any layer fails, use the raw expected-value pick.

## Standard command

```powershell
python scripts\run_final_leader_decision.py `
  --config config.yaml `
  --fixtures data/fixtures_real.csv `
  --odds-json data/odds_snapshot_real.json `
  --predictions-csv outputs/latest/predictions.csv `
  --leaderboard-csv inputs/pool_leaderboard.csv `
  --chaser-profiles-csv inputs/chaser_profiles.csv `
  --leader-player "Danie" `
  --base-simulations 100000 `
  --stress-simulations 100000 `
  --confirmation-simulations 500000 `
  --exclude-match "Turkey|Paraguay" `
  --support-threshold 0.70 `
  --out-dir outputs/final_leader_decision
```

## Outputs

The final decision pipeline writes:

- `final_picks.csv`
- `final_decision_report.md`
- `final_decision_report.json`
- `run_manifest.json`
- `quality_gates.json`
- base Monte Carlo outputs under `base_mc/`
- stress outputs under `stress/`
- high-N confirmation outputs under `confirmation_500k/`

## Decision report

Use `final_picks.csv` as the source of truth. The `final_pick` column is the pick to submit.

The report records:

- approved defensive switches
- rejected defensive switches
- base and confirmation probabilities
- stress-test support threshold
- input and output hashes
- git commit and working-tree state
- quality-gate results

## Quality gates

The final pipeline fails early if key inputs are missing or unreadable:

- config
- fixtures
- odds JSON
- predictions CSV
- leaderboard CSV
- chaser profile CSV

The predictions CSV must also include:

- `commence_time`
- `home_team`
- `away_team`
- `recommended_scoreline`

## Chaser profile learning

When historical pool picks are available, fit behavioural profiles with:

```powershell
python scripts\fit_chaser_profiles.py `
  --historical-picks-csv inputs/historical_pool_picks.csv `
  --predictions-csv outputs/latest/predictions.csv `
  --out-dir outputs/chaser_profile_fit
```

The fitted profiles are descriptive priors. Low-sample players should be blended with defaults rather than used blindly.

## Calibration diagnostics

Once actual results are known, run:

```powershell
python scripts\run_calibration_diagnostics.py `
  --predictions-csv outputs/latest/predictions.csv `
  --results-csv outputs/results.csv `
  --out-dir outputs/calibration_diagnostics
```

Track:

- exact hit rate
- outcome hit rate
- exact Brier score when `p_exact` is available
- outcome Brier/log loss when `p_outcome` is available
- bucket-level calibration error

## Repository hygiene

Commit final outputs with force-add because `outputs/` is usually ignored:

```powershell
git add -f outputs/final_leader_decision
git commit -m "Add final leader decision outputs"
git push origin <your-working-branch>
```

Do not commit raw `data/` snapshots unless they are required for reproducibility evidence.
