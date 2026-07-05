# Final Leader Decision Report

Generated: 2026-07-05T15:24:04.295410+00:00

## Decision

**Final policy:** raw EV pick unless base MC, stress test and high-N confirmation all approve the same switch
**Approved defensive switches:** 0
**Rejected defensive switches:** 0

## Monte Carlo Summary

| Run | Simulations | Baseline P(first) | Final P(first) | Delta | P(first/tied) | Accepted switches | Total EV loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| base_mc | 100000 | 0.769810 | 0.769810 | 0.000000 | 0.899970 | 0 | 0.000000 |
| confirmation_500k | 500000 | 0.769658 | 0.769658 | 0.000000 | 0.899792 | 0 | 0.000000 |

## Final Picks

| Kickoff | Home | Away | Raw pick | Final pick | Switched | Risk | Confidence |
|---|---|---|---|---|---:|---|---|
| 2026-07-05T20:00:00Z | Brazil | Norway | 2-0 | 2-0 | False | high | fragile |
| 2026-07-06T00:00:00Z | Mexico | England | 1-2 | 1-2 | False | high | fragile |
| 2026-07-06T19:00:00Z | Portugal | Spain | 1-2 | 1-2 | False | high | fragile |
| 2026-07-07T00:00:00Z | United States | Belgium | 1-2 | 1-2 | False | high | fragile |
| 2026-07-07T16:00:00Z | Argentina | Egypt | 2-0 | 2-0 | False | low | strong |
| 2026-07-07T20:00:00Z | Switzerland | Colombia | 0-1 | 0-1 | False | high | fragile |
| 2026-07-09T20:00:00Z | France | Morocco | 2-0 | 2-0 | False | medium | medium |

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
