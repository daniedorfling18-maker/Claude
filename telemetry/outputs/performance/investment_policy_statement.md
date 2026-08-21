# Investment Policy Statement — Polymarket research system

Generated: 2026-08-20T03:12:34Z

> Generated from enforced code constants and the loaded effective configuration. This document is reporting-only and cannot authorise paper or live orders.

## Policy provenance

- Source defaults SHA-256: `5034fc724b802b41448c3145c5fd7a654b7fce03dcc45ff290150b60cc16f0f0`
- Effective VPS configuration SHA-256: `b903f401470bb1d58d9299e78664f54e1aefba7169b0f232be347ac96379bf23`
- Configuration: `/app/polymarket_predictive_config.example.yaml`
- Deployed revision marker: `036d09be26a0d0f7d9cba5a049e468b688decd70`

## Mandate and execution posture

- Trading mode: **paper**
- Paper configuration enabled: **true**
- Live configuration enabled: **false**
- Repository mandate: research, dry-run, and governed paper evidence only; human action remains outside the system.

## Frozen WO-50 decision policy — effective values

| Setting | Source default | Effective deployed value |
|---|---|---|
| below_target_review_runs | 7 | 7 |
| below_target_review_threshold | 3 | 3 |
| composition_required_recurrence | 4 | 4 |
| composition_stable_days | 7 | 7 |
| decision_date | 2026-07-20 | 2026-07-20 |
| enabled | true | true |
| fill_alert_multiple | 2 | 2 |
| kelly_fraction_cap | 1 | 1 |
| kelly_full_weight_days | 20 | 20 |
| kill_cumulative_net_score_usd | -25 | -25 |
| kill_fill_overrun_days | 2 | 2 |
| kill_input_max_age_seconds | 1800 | 1800 |
| kill_single_day_net_usd | -15 | -15 |
| quarter_kelly_multiplier | 0.25 | 0.25 |
| stage0_capital_usd | 100 | 100 |
| stage1_capital_usd | 250 | 250 |
| stage1_min_consecutive_days | 7 | 7 |
| stage2_additional_days | 14 | 14 |
| stage2_capital_usd | 500 | 500 |
| stage2_reward_realisation_multiple | 0.5 | 0.5 |

### Registered action table

| Priority | Policy row | Condition | Indicated human action |
|---|---|---|---|
| 1 | kill_criteria | Any registered kill criterion is triggered. | stop_quoting_review_before_resume |
| 2 | post_gate_below_target | M-A and M-B pass, but net is below target on more than the registered recent-run threshold. | maker_lane_not_supported_program_review |
| 3 | gates_pass_composition_stable | M-A and M-B pass and the top portfolio market meets the registered recurrence floor. | fund_100_min_size_single_calmest_market |
| 4 | gates_pass_composition_churning | M-A and M-B pass but portfolio composition is not stable. | fund_100_but_only_most_recurrent_market_half_target |
| 5 | decision_date_pending_gate | The registered decision date is reached while M-A or M-B remains pending. | defer_funding_continue_study |
| 6 | study_missing | The maker-carry study artifact is absent. | collect_maker_carry_study |
| 7 | pre_decision_evidence_pending | No earlier policy row binds before the registered decision date. | continue_study_until_policy_date |

### Registered kill criteria

| Criterion | Rule | Effective threshold |
|---|---|---|
| kill_data_stale | Stop when a configured or observed live stage has no kill-input observation inside the registered freshness maximum. | kill_input_max_age_seconds=1800 |
| cumulative_real_net_score | Stop when cumulative real net score is at or below the registered loss floor. | kill_cumulative_net_score_usd=-25 |
| single_day_net_score | Stop when any single UTC-day net score is at or below the registered loss floor. | kill_single_day_net_usd=-15 |
| fills_outrunning_model_two_days | Stop when realised fills outrun the model for the registered number of days. | kill_fill_overrun_days=2 |
| uma_dispute_inventory | Stop on a UMA dispute affecting held inventory. | structural |
| scoreboard_stop | Stop when the read-only live-test scoreboard emits its registered STOP state. | structural |

## Registered maker gates

| Gate | Rule | Effective threshold |
|---|---|---|
| M_A_carry_evidence | Trusted net carry must meet the daily target on the required number of distinct UTC days, including the latest run, and each counted day's portfolio markout must be measured (an observed markout, not merely the minimum print count). | gate_min_runs_at_target=7 |
| M_B_adverse_realism | Every portfolio market must carry a measured empirical markout charge. | structural |
| M_C_payout_floor | Every sized market must clear the venue's daily reward payout floor. | min_daily_payout_usd=1 |

### Maker policy constants

| Setting | Source default | Effective deployed value |
|---|---|---|
| capital_cap_usd | 500 | 500 |
| gate_min_runs_at_target | 7 | 7 |
| maker_max_hold_days | 30 | 30 |
| maker_min_book_history_hours | 48 | 48 |
| maker_min_book_snapshots | 100 | 100 |
| maker_switch_margin_frac | 0.25 | 0.25 |
| max_size_multiple | 5 | 5 |
| max_trusted_reward_share | 0.05 | 0.05 |
| min_daily_payout_usd | 1 | 1 |
| share_model_c | 3 | 3 |
| share_model_mid_band_max | 0.9 | 0.9 |
| share_model_mid_band_min | 0.1 | 0.1 |
| target_net_usd_per_day | 3.33 | 3.33 |

## Registered taker verdict gates

| Gate | Rule | Effective settings |
|---|---|---|
| A_edge_exists | Use equal-weight independent fixture units; pass only with positive unit mean net settlement return per dollar (pre-fee) and the registered one-sided sign-test threshold on settled-profitable units after the sample floor. | minimum_final_samples=12, sign_test_alpha=0.1 |
| B_edge_survives_costs | Pass only when unit mean net settlement return per dollar remains positive after the exit, adverse-selection, and taker-fee charges. | exit_cost_haircut_per_dollar=0.005, adverse_selection_haircut_per_dollar=0.005, taker_fee_rate=0.05 |
| C_scale_feasible | Pass only when observed focus-entry capacity at the registered per-trade cap can fund the monthly target turnover. | max_stake_per_trade_usdc=10, days_per_month=30 |

### Registered amendments 1–7

| Amendment | Registered | Direction | Effect |
|---|---|---|---|
| 1 | 2026-07-09T11:00:00Z | tighten_only | Gate A clusters correlated finals into one market-level unit before sample counting and the sign test. |
| 2 | 2026-07-09T11:00:00Z | tighten_only | Gate B adds an adverse-selection haircut for preferential real-world fills absent from shadow execution. |
| 3 | 2026-07-09T11:00:00Z | tighten_only | Gate C labels the World Cup liquidity regime and requires an underpowered read to extend rather than resolve. |
| 4 | 2026-07-09T11:00:00Z | tighten_only | Gate B charges the actual outcome-price-dependent Polymarket taker fee. |
| 5 | 2026-07-09T11:00:00Z | tighten_only | Gate A transitively merges markets sharing a real-world fixture so side markets cannot inflate independence. |
| 6 | 2026-07-10T00:00:00Z | tighten_only | The registered sports taker-fee rate increases from 0.03 to 0.05 under the canonical venue fee schedule. |
| 7 | 2026-07-10T21:30:00Z | tighten_only | The evidence window may extend exactly once through 2026-08-19, after which the verdict resolves terminally. |
| 8 | 2026-07-17T04:30:00Z | tighten_only | Any final-read verdict other than all-gates-pass takes the single extension; any terminal-read outcome other than all-gates-pass resolves NO, closing the pending-on-significance enumeration gap. |

## Quote-sheet standing rules

1. **Scheduled announcement** — Never quote through a scheduled announcement; pull flagged rows at least 24h before the event and stay out until it settles.
2. **Minimum-size start** — Start at minimum size for a full reward day before any size-up.
3. **Payout floor** — Rewards below $1.0/market/day pay NOTHING; stay above the floor.
4. **Fill-model breach** — If realised fills exceed the modelled band-crossing rate, stop: faster flow is beating the markout model.
5. **Daily refresh** — Re-read the sheet daily because reward pots and competition move with the calendar.
6. **Inventory skew** — Once filled on one side, requote to REDUCE the position, never to add; unhedged binary inventory at resolution is a directional bet, not market making.
7. **Band discipline** — Quote only while the mid is inside [0.10, 0.90]; exit as price leaves the band and do not chase it.
8. **Flow toxicity** — Do not initiate quotes where toxicity_score > 0.9. This conditions human action only; the registered study charge is unchanged absent a later dated tightening.
9. **Resolution risk** — Only quote markets with objective, verifiable resolution sources and no open clarifications; exit immediately if a proposal on a held market is disputed.

## Effective risk controls

| Risk setting | Effective configured value |
|---|---|
| bankroll | 1000 |
| minimum_edge | 0.03 |
| minimum_confidence | 0.65 |
| maximum_spread | 0.08 |
| minimum_liquidity | 50 |
| minimum_time_to_close_minutes | 15 |
| maximum_resolution_risk | 0.25 |
| maximum_single_market_exposure | 0.02 |
| maximum_category_exposure | 0.1 |
| maximum_correlated_exposure | 0.1 |
| maximum_daily_loss | 0.03 |
| maximum_drawdown | 0.05 |
| maximum_open_orders | 10 |
| maximum_order_rate_per_minute | 30 |
| maximum_slippage | 0.02 |
| liquidity_cap_fraction | 0.05 |
| kelly_cap | 0.005 |
| kelly_shrinkage | 0 |
| minimum_entry_price | 0.05 |
| maximum_entry_price | 0.9 |

## Current risk annex

| Measure | Current value |
|---|---|
| Status | partial |
| Source | outputs/polymarket_portfolio/risk_state.json |
| Open positions | — |
| Total cost exposure (USDC) | 0 |
| VaR 95 (USDC) | — |
| CVaR 95 (USDC) | — |
| Worst marked position return (%) | — |
| Missing-input note | WO-12 VaR state missing; exposure is from the latest portfolio snapshot |

### Exposure concentration — correlation groups

No correlation concentration rows available.

### Exposure concentration — categories

No category concentration rows available.

## Capacity statement

| Measure | Value |
|---|---|
| Status | ok |
| Evidence class | modeled_upper_bound_not_gate_input |
| Registered net/day | 1.47 |
| Registered net/month | 44.1 |
| First measured cap reaching $100/month | — |
| Missing-input note | — |

### Supplementary capital curve — not a gate input

| Capital cap (USD) | Capital used (USD) | Modelled net/day (USD) |
|---|---|---|
| 250 | 165 | 1.47 |
| 500 | 165 | 1.47 |
| 1000 | 165 | 1.47 |
| 2000 | 165 | 1.47 |
| 5000 | 165 | 1.47 |

## Control declaration

- `paper_trading_invoked = false`
- `live_trading_invoked = false`
- No gate, threshold, stake, broker, or order path reads this IPS.
