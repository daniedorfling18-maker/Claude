# Final Leader Decision Report

Generated: 2026-07-08T08:43:18.860011+00:00

## Decision

**Final policy:** raw EV pick unless base MC, stress test and high-N confirmation all approve the same switch
**Approved defensive switches:** 1
**Rejected defensive switches:** 0

## Monte Carlo Summary

| Run | Simulations | Baseline P(first) | Final P(first) | Delta | P(first/tied) | Accepted switches | Total EV loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| base_mc | 100000 | 0.278830 | 0.310450 | 0.031620 | 0.781240 | 1 | 0.007456 |
| confirmation_500k | 500000 | 0.279396 | 0.310736 | 0.031340 | 0.779440 | 1 | 0.007456 |

## Final Picks

| Kickoff | Home | Away | Raw pick | Final pick | Switched | Risk | Confidence |
|---|---|---|---|---|---:|---|---|
| 2026-07-09T20:00:00Z | France | Morocco | 2-0 | 2-0 | False | medium | medium |
| 2026-07-10T19:00:00Z | Spain | Belgium | 2-0 | 2-0 | False | medium | medium |
| 2026-07-11T21:00:00Z | Norway | England | 1-2 | 0-2 | True | high | fragile |
| 2026-07-12T01:00:00Z | Argentina | Switzerland | 2-0 | 2-0 | False | medium | medium |

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
