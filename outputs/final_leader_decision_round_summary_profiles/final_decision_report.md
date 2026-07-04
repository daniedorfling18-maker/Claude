# Final Leader Decision Report

Generated: 2026-07-04T15:19:13.308669+00:00

## Decision

**Final policy:** raw EV pick unless base MC, stress test and high-N confirmation all approve the same switch
**Approved defensive switches:** 0
**Rejected defensive switches:** 0

## Monte Carlo Summary

| Run | Simulations | Baseline P(first) | Final P(first) | Delta | P(first/tied) | Accepted switches | Total EV loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| base_mc | 100000 | 0.706280 | 0.706280 | 0.000000 | 0.752010 | 0 | 0.000000 |
| confirmation_500k | 500000 | 0.707150 | 0.707150 | 0.000000 | 0.752268 | 0 | 0.000000 |

## Final Picks

| Kickoff | Home | Away | Raw pick | Final pick | Switched | Risk | Confidence |
|---|---|---|---|---|---:|---|---|
| 2026-07-04T17:00:00Z | Canada | Morocco | 0-2 | 0-2 | False | medium | medium |
| 2026-07-04T21:00:00Z | Paraguay | France | 0-2 | 0-2 | False | low | strong |
| 2026-07-05T20:00:00Z | Brazil | Norway | 2-0 | 2-0 | False | high | fragile |
| 2026-07-06T00:00:00Z | Mexico | England | 0-1 | 0-1 | False | high | fragile |
| 2026-07-06T19:00:00Z | Portugal | Spain | 0-2 | 0-2 | False | high | fragile |
| 2026-07-07T00:00:00Z | United States | Belgium | 1-2 | 1-2 | False | high | medium |
| 2026-07-07T16:00:00Z | Argentina | Egypt | 2-0 | 2-0 | False | low | strong |
| 2026-07-07T20:00:00Z | Switzerland | Colombia | 0-2 | 0-2 | False | high | fragile |

## Quality Gates

- **PASS**: `input_exists:config`
- **PASS**: `input_exists:fixtures`
- **PASS**: `input_exists:odds_json`
- **PASS**: `input_exists:predictions_csv`
- **PASS**: `input_exists:leaderboard_csv`
- **PASS**: `input_exists:chaser_profiles_csv`
- **PASS**: `predictions_required_columns`
- **PASS**: `predictions_non_empty`

## Interpretation

Use the final pick column. A defensive switch is only accepted when base Monte Carlo, stress testing and high-N confirmation all support the same switch. Otherwise the raw expected-value pick is retained.
