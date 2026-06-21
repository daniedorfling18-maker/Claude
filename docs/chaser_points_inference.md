# Chaser Profile Inference from Round Points

Use this workflow when Superbru screenshots show each player's points per match but not their exact picked scorelines.

The method is probabilistic. It does not pretend to know the exact historical pick. Instead, it asks which behavioural buckets could have produced the observed points given the actual result and the model's candidate scorelines.

## Inputs

### 1. Match order CSV

Create `inputs/historical_match_order.csv`:

```csv
round,match_order,match_id,home_team,away_team,actual_scoreline
1,1,,Mexico,South Africa,2-0
1,2,,South Korea,Czech Republic,2-1
1,3,,Canada,Bosnia & Herzegovina,1-1
```

`match_id` is optional if the home and away team names match/canonicalise with the prediction rows.

### 2. Historical points CSV

You can use wide screenshot-style input:

```csv
round,player,match_1,match_2,match_3
1,Danie,3,1.5,1
1,Dheben,1,3,0
1,Bijal,1.5,1.5,3
```

Or long input:

```csv
round,match_order,player,points
1,1,Danie,3
1,2,Danie,1.5
1,3,Danie,1
```

Points use the Superbru values:

- `3` exact
- `1.5` close
- `1` result only
- `0` wrong result

## Run

```powershell
python scripts\fit_chaser_profiles_from_points.py `
  --historical-points-csv inputs/historical_round_points.csv `
  --match-order-csv inputs/historical_match_order.csv `
  --predictions-csv outputs/latest/predictions.csv `
  --out-dir outputs/chaser_profile_points_fit
```

## Outputs

The script writes:

- `chaser_profiles_from_points.csv`
- `chaser_points_inference_detail.csv`
- `normalized_historical_points.csv`
- `chaser_points_inference_summary.json`

## Use in the final leader pipeline

The output file has the same weight columns as the normal chaser profile file. Use it as the chaser profile input:

```powershell
python scripts\run_final_leader_decision.py `
  --config config.yaml `
  --fixtures data/fixtures_real.csv `
  --odds-json data/odds_snapshot_real.json `
  --predictions-csv outputs/latest/predictions.csv `
  --leaderboard-csv inputs/pool_leaderboard.csv `
  --chaser-profiles-csv outputs/chaser_profile_points_fit/chaser_profiles_from_points.csv `
  --leader-player "Danie" `
  --base-simulations 100000 `
  --stress-simulations 100000 `
  --confirmation-simulations 500000 `
  --exclude-match "Turkey|Paraguay" `
  --support-threshold 0.70 `
  --out-dir outputs/final_leader_decision_points_profiles
```

## Interpretation

High `unexplained_rate` means the observed points often cannot be explained by the current candidate buckets. This can happen when:

- the historical prediction file does not cover the same matches,
- the match order is wrong,
- team names do not match,
- the player picked unusual scorelines outside raw/top/modal/chalk/chase candidates.

Low-sample profiles should be treated as priors, not hard truth.
