# Final Leader Decision Report

Generated: 2026-07-03T10:06:10.078882+00:00

## Decision

**Final policy:** raw EV pick unless base MC, stress test and high-N confirmation all approve the same switch
**Approved defensive switches:** 0
**Rejected defensive switches:** 0

## Monte Carlo Summary

| Run | Simulations | Baseline P(first) | Final P(first) | Delta | P(first/tied) | Accepted switches | Total EV loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| base_mc | 100000 | 0.732080 | 0.732080 | 0.000000 | 0.772210 | 0 | 0.000000 |
| confirmation_500k | 500000 | 0.732010 | 0.732010 | 0.000000 | 0.771636 | 0 | 0.000000 |

## Final Picks

| Kickoff | Home | Away | Raw pick | Final pick | Switched | Risk | Confidence |
|---|---|---|---|---|---:|---|---|
| 2026-07-03T18:00:00Z | Australia | Egypt | 0-1 | 0-1 | False | high | medium |
| 2026-07-03T22:00:00Z | Argentina | Cape Verde | 2-0 | 2-0 | False | low | strong |
| 2026-07-04T01:30:00Z | Colombia | Ghana | 2-0 | 2-0 | False | low | strong |
| 2026-07-04T17:00:00Z | Canada | Morocco | 0-2 | 0-2 | False | high | medium |
| 2026-07-04T21:00:00Z | Paraguay | France | 0-2 | 0-2 | False | low | strong |
| 2026-07-05T20:00:00Z | Brazil | Norway | 2-0 | 2-0 | False | high | fragile |
| 2026-07-06T00:00:00Z | Mexico | England | 0-1 | 0-1 | False | high | fragile |
| 2026-07-06T19:00:00Z | Portugal | Spain | 0-2 | 0-2 | False | high | fragile |
| 2026-07-07T00:00:00Z | United States | Belgium | 2-1 | 2-1 | False | high | fragile |

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
