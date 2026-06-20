# Round Summary Behaviour Evaluation

Use this workflow when only aggregate round charts are available.

This method evaluates behaviour between rounds from summary counts such as total points, exacts, closes, result-only hits and total matches. It does not reconstruct exact scoreline picks.

## Input

Create `inputs/round_summary_behaviour.csv`:

```csv
round,player,round_points,total_matches,exact_count,close_count,result_count,wrong_count
1,Danie,16.5,24,2,5,3,14
2,Danie,12.5,8,3,1,2,2
2,Bijal,11,8,2,3,0,3
```

`wrong_count` is optional if the exact, close and result counts are supplied. It will be derived as:

```text
wrong_count = total_matches - exact_count - close_count - result_count
```

If only round points are known, the script will preserve the points but it cannot infer category counts reliably.

## Run

```powershell
python scripts\fit_chaser_profiles_from_round_summary.py `
  --round-summary-csv inputs/round_summary_behaviour.csv `
  --out-dir outputs/round_summary_behaviour
```

## Outputs

The script writes:

- `round_behaviour_summary.csv`
- `round_behaviour_trends.csv`
- `chaser_profiles_from_round_summary.csv`
- `round_summary_behaviour.json`

## Interpretation

The key fields are:

- `points_per_match`: normalises rounds with different numbers of completed matches.
- `weighted_accuracy`: round points divided by maximum possible points.
- `exact_rate`: exacts divided by matches.
- `wrong_rate`: wrong outcomes divided by matches.
- `trend`: improved, regressed, stable or baseline.
- `behaviour_note`: short explanation of the movement.

## Using profiles in the final leader pipeline

```powershell
python scripts\run_final_leader_decision.py `
  --config config.yaml `
  --fixtures data/fixtures_real.csv `
  --odds-json data/odds_snapshot_real.json `
  --predictions-csv outputs/latest/predictions.csv `
  --leaderboard-csv inputs/pool_leaderboard.csv `
  --chaser-profiles-csv outputs/round_summary_behaviour/chaser_profiles_from_round_summary.csv `
  --leader-player "Danie" `
  --base-simulations 100000 `
  --stress-simulations 100000 `
  --confirmation-simulations 500000 `
  --exclude-match "Turkey|Paraguay" `
  --support-threshold 0.70 `
  --out-dir outputs/final_leader_decision_round_summary_profiles
```

## Caveat

This is weaker than exact pick history and weaker than match-by-match points, but it is still useful for setting conservative chaser priors and identifying who improved or regressed between rounds.
