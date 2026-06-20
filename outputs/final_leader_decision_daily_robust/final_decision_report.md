# Final Leader Decision Report

Generated: 2026-06-20T14:08:20.006598+00:00

## Decision

**Final policy:** raw EV pick unless base MC, stress test and high-N confirmation all approve the same switch
**Approved defensive switches:** 0
**Rejected defensive switches:** 1

## Monte Carlo Summary

| Run | Simulations | Baseline P(first) | Final P(first) | Delta | P(first/tied) | Accepted switches | Total EV loss |
|---|---:|---:|---:|---:|---:|---:|---:|
| base_mc | 100000 | 0.290240 | 0.295790 | 0.005550 | 0.362390 | 1 | 0.004410 |
| confirmation_500k | 500000 | 0.292204 | 0.292204 | 0.000000 | 0.360744 | 0 | 0.000000 |

## Final Picks

| Kickoff | Home | Away | Raw pick | Final pick | Switched | Risk | Confidence |
|---|---|---|---|---|---:|---|---|
| 2026-06-20T17:00:00Z | Netherlands | Sweden | 2-0 | 2-0 | False | medium | medium |
| 2026-06-20T20:00:00Z | Germany | Ivory Coast | 2-0 | 2-0 | False | medium | medium |
| 2026-06-21T00:00:00Z | Ecuador | Curacao | 2-0 | 2-0 | False | low | medium |
| 2026-06-21T04:00:00Z | Tunisia | Japan | 0-2 | 0-2 | False | medium | medium |
| 2026-06-21T16:00:00Z | Spain | Saudi Arabia | 3-0 | 3-0 | False | low | medium |
| 2026-06-21T19:00:00Z | Belgium | Iran | 2-0 | 2-0 | False | low | strong |
| 2026-06-21T22:00:00Z | Uruguay | Cape Verde | 2-0 | 2-0 | False | low | strong |
| 2026-06-22T01:00:00Z | New Zealand | Egypt | 0-2 | 0-2 | False | medium | medium |
| 2026-06-22T17:00:00Z | Argentina | Austria | 2-0 | 2-0 | False | medium | medium |
| 2026-06-22T21:00:00Z | France | Iraq | 3-0 | 3-0 | False | low | medium |
| 2026-06-23T00:00:00Z | Norway | Senegal | 2-1 | 2-1 | False | high | medium |
| 2026-06-23T03:00:00Z | Jordan | Algeria | 0-2 | 0-2 | False | medium | medium |
| 2026-06-23T17:00:00Z | Portugal | Uzbekistan | 2-0 | 2-0 | False | low | strong |
| 2026-06-23T20:00:00Z | England | Ghana | 2-0 | 2-0 | False | low | strong |
| 2026-06-23T23:00:00Z | Panama | Croatia | 0-2 | 0-2 | False | medium | medium |
| 2026-06-24T02:00:00Z | Colombia | DR Congo | 2-0 | 2-0 | False | medium | medium |
| 2026-06-24T19:00:00Z | Bosnia & Herzegovina | Qatar | 2-0 | 2-0 | False | low | strong |
| 2026-06-24T19:00:00Z | Switzerland | Canada | 1-0 | 1-0 | False | high | fragile |
| 2026-06-24T22:00:00Z | Scotland | Brazil | 0-2 | 0-2 | False | low | strong |
| 2026-06-24T22:00:00Z | Morocco | Haiti | 2-0 | 2-0 | False | low | strong |
| 2026-06-25T01:00:00Z | Czech Republic | Mexico | 0-2 | 0-2 | False | high | fragile |
| 2026-06-25T01:00:00Z | South Africa | South Korea | 0-2 | 0-2 | False | medium | medium |
| 2026-06-25T20:00:00Z | Curacao | Ivory Coast | 0-2 | 0-2 | False | low | medium |
| 2026-06-25T20:00:00Z | Ecuador | Germany | 0-2 | 0-2 | False | medium | medium |
| 2026-06-25T23:00:00Z | Japan | Sweden | 2-1 | 2-1 | False | high | fragile |
| 2026-06-25T23:00:00Z | Tunisia | Netherlands | 0-2 | 0-2 | False | low | strong |
| 2026-06-26T02:00:00Z | Paraguay | Australia | 1-0 | 1-0 | False | high | medium |
| 2026-06-26T02:00:00Z | Turkey | United States | 1-2 | 1-2 | False | high | medium |
| 2026-06-26T19:00:00Z | Norway | France | 1-2 | 1-2 | False | high | fragile |
| 2026-06-26T19:00:00Z | Senegal | Iraq | 2-0 | 2-0 | False | low | strong |
| 2026-06-27T00:00:00Z | Cape Verde | Saudi Arabia | 1-2 | 1-2 | False | high | medium |
| 2026-06-27T00:00:00Z | Uruguay | Spain | 0-2 | 0-2 | False | medium | medium |
| 2026-06-27T03:00:00Z | New Zealand | Belgium | 0-2 | 0-2 | False | low | strong |
| 2026-06-27T03:00:00Z | Egypt | Iran | 2-0 | 2-0 | False | high | fragile |
| 2026-06-27T21:00:00Z | Croatia | Ghana | 2-0 | 2-0 | False | medium | medium |
| 2026-06-27T21:00:00Z | Panama | England | 0-2 | 0-2 | False | low | strong |
| 2026-06-27T23:30:00Z | Colombia | Portugal | 0-2 | 0-2 | False | high | fragile |
| 2026-06-27T23:30:00Z | DR Congo | Uzbekistan | 2-1 | 2-1 | False | high | fragile |
| 2026-06-28T02:00:00Z | Algeria | Austria | 0-1 | 0-1 | False | high | fragile |
| 2026-06-28T02:00:00Z | Jordan | Argentina | 0-2 | 0-2 | False | low | strong |

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
