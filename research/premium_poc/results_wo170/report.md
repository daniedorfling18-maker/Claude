# WO-170 proof-of-concept results (historical-class diagnostic)

Completeness scope for unverifiable liquidation status: **perp** — absent perpetual-side data only, the leg the liquidation check reads (WO-167's registered rule).

Drawdown basis: **nav** (G3 reads the ledger's own NAV path over every boundary; the WO-166 compounded weekly-increment curve is reported for reconciliation and not read). Realised-variance alignment: **return_intervals** (721 closes, 720 return intervals covering each 30-day window exactly). Return basis for G1, G2 and G4: `simple_on_inception_capital` — the mean eligible weekly change in wealth divided by the inception capital, annualised by 52, as WO-166 registered; nothing about those gates changes here. This pass inherits WO-167's completeness scope and its rejected-open disclosure.

This is a historical-class result computed from the committed inputs listed in `manifest.json`. The gate in Lane A is applied to the lower bound of a bootstrap interval minus a haircut of 2.0% per year for bias channels this data cannot measure (slippage beyond the taker fee, the intra-hour liquidation path, venue operational frictions, and selection). **That haircut is a declared assumption, not a measurement.** Unfavourable channels (VIP0 taker fees with no rebate, zero collateral yield, capital at 1.5x notional, no re-leveraging) are not credited back. Nothing here is verification of record, registers a primary, or authorises capital.

Generated at 2026-09-13T06:22:35Z from code revision `a4f1cf07cf48ea9b1ccaf376a48d9c4add163821`; manifest sha256 `2152b1b4e3fd1a6ffc8f9881c1a7663d33a24e27b8205824d98438c95df2ea50`.

## Verdicts

**Lane A — funding carry (V0, always on): GO** (G1=pass, G2=pass, G3=pass, G4=pass)

**Lane B — variance risk premium existence: GO** (G5=pass) — existence observed, selection bias unaddressed (no haircut can be derived in variance points)

## Lane A — pooled BTC + ETH, V0

Entry boundary 2020-01-06T00:00:00Z, exit boundary 2026-08-31T00:00:00Z (6.65 years).

| quantity | value |
|---|---|
| eligible weeks | 343 of 347 |
| mean weekly net return on capital | 0.1922% |
| annualised net return on capital (mean weekly x 52) | 9.99% |
| G2 quantity: point estimate minus the declared 2.0% haircut | 7.99% |
| 90% interval, week-cluster | [0.1626%, 0.2255%] weekly |
| 90% interval, stationary block (L = 7) | [0.1228%, 0.2740%] weekly |
| 0.025-quantile lower bound, minimum of the two | 0.1140% weekly = 5.93% annualised |
| G1 quantity: that lower bound annualised minus the haircut | 3.93% |
| margin of the G2 quantity over the 6.0% hurdle | 1.99 pp |
| margin of the G1 quantity over zero | 3.93 pp |
| Sharpe (weekly, annualised) | 3.95 |
| max drawdown, compounded weekly-increment curve (WO-166 basis; not read by G3 under this configuration) | -0.32% |
| max drawdown, NAV path over every boundary (peak-to-trough, negative; G3 reads its magnitude) | -0.49% |
| CVaR 95% weekly (mean loss magnitude in the worst 5% of weeks) | 0.05% |
| forced liquidations | 0 |
| open-position periods with perpetual-side data absent (liquidation unverifiable; G3 requires 0) | 0 |
| open-position periods rejected for an absent bar on either leg (excluded from every estimator; the WO-166 either-scope count; not read by G3 under the perp scope) | 16 |
| rebalances | 26 |
| complete ISO years positive (a year needs >= 45 eligible weeks) | 6 of 6 |

| ISO year | pooled net return on capital (eligible weeks only) |
|---|---|
| 2020 | 15.88% |
| 2021 | 28.67% |
| 2022 | 1.30% |
| 2023 | 3.69% |
| 2024 | 10.83% |
| 2025 | 4.67% |
| 2026 | 0.89% |

### Per asset (V0)

| asset | eligible weeks | annualised net on capital | Sharpe | max drawdown | rebalances | forced liquidations | turnover / yr |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 343 | 9.09% | 4.16 | -0.48% | 11 | 0 | 1.02 |
| ETHUSDT | 343 | 10.90% | 3.72 | -0.71% | 15 | 0 | 1.18 |

### What the prices and cash flows are

| quantity | basis |
|---|---|
| mark_price | Binance 1h kline close of the hour ending at the boundary (last traded price), not the venue mark price |
| execution_price | the same close plus taker fees (spot 10 bps, perpetual 5 bps); no spread, no slippage, no market impact — a favourable channel, covered only by the declared haircut |
| funding_notional | position size × that close; the venue settles on mark-price notional (WO-166 A8 bound: ≤ 1 × 10⁻⁵ of notional per period, direction indeterminate) |
| liquidation_check | kline high of the last traded price against the period-start margin ratio; the venue liquidates on the mark price, which is smoothed, so the last-price high triggers at least as often — a conservative channel |
| collateral | margin 0.5 × notional in USDT; cash and spot earn zero; no cross-margin netting — an unfavourable channel |
| haircut | 2.0 pp per year, a declared assumption; it is not a measured bound on venue, stablecoin-depeg or liquidation risk and this WO measures none of them |

### NAV-path statistics (V0; descriptive, never gated)

| quantity | value |
|---|---|
| compounded annual growth of the pooled NAV | 8.02% |
| total simple return on inception capital | 67.12% |
| mean weekly return on NAV (eligible weeks) | 0.1472% |
| annualised return on NAV (mean weekly x 52) | 7.65% |

### Recent-period stability (V0; retrospective diagnostic, not a prospective validation)

| cut | state | weeks | mean weekly | annualised simple | 90% week-cluster interval (weekly) | Sharpe | NAV drawdown over the span |
|---|---|---|---|---|---|---|---|
| last_52 | ok | 52 | 0.0441% | 2.29% | [0.0329%, 0.0551%] | 6.53 | -0.19% |
| last_104 | ok | 104 | 0.0841% | 4.37% | [0.0701%, 0.0987%] | 6.79 | -0.19% |

Rolling 52-eligible-week annualised simple return: state ok, windows 292, min 1.14%, max 32.40%, last 2.29% (overlapping windows; no share-positive statistic is reported).

### Regime cut (BTC spot against its 200-day SMA at the week's start; descriptive)

| regime | eligible weeks | mean weekly net return on capital |
|---|---|---|
| above | 183 | 0.2992% |
| below | 133 | 0.0348% |
| unknown | 27 | 0.2426% |

### Fee sensitivity (2x taker fees; descriptive)

Annualised net on capital 9.91%, point after haircut 7.91%, lower bound after haircut 3.90%, max drawdown -0.32%.

### V1 (conditional entry; descriptive, never gated)

| quantity | value |
|---|---|
| eligible weeks | 343 of 347 |
| mean weekly net return on capital | 0.1531% |
| annualised net return on capital (mean weekly x 52) | 7.96% |
| point estimate minus the declared 2.0% haircut | 5.96% |
| 90% interval, week-cluster | [0.1202%, 0.1886%] weekly |
| 90% interval, stationary block (L = 7) | [0.0754%, 0.2400%] weekly |
| 0.025-quantile lower bound, minimum of the two | 0.0635% weekly = 3.30% annualised |
| lower bound annualised minus the haircut (descriptive; V1 is never gated) | 1.30% |
| Sharpe (weekly, annualised) | 2.88 |
| max drawdown, compounded weekly-increment curve (WO-166 basis; not read by G3 under this configuration) | -5.56% |
| max drawdown, NAV path over every boundary (peak-to-trough, negative) | -4.09% |
| CVaR 95% weekly (mean loss magnitude in the worst 5% of weeks) | 0.27% |
| forced liquidations | 0 |
| open-position periods with perpetual-side data absent (liquidation unverifiable) | 0 |
| open-position periods rejected for an absent bar on either leg (excluded from every estimator; the WO-166 either-scope count; not read by G3 under the perp scope) | 14 |
| rebalances | 59 |
| complete ISO years positive (a year needs >= 45 eligible weeks) | 5 of 6 |

### NAV-path statistics (V1; descriptive, never gated)

| quantity | value |
|---|---|
| compounded annual growth of the pooled NAV | 6.65% |
| total simple return on inception capital | 53.51% |
| mean weekly return on NAV (eligible weeks) | 0.1229% |
| annualised return on NAV (mean weekly x 52) | 6.39% |

### Recent-period stability (V1; retrospective diagnostic, not a prospective validation)

| cut | state | weeks | mean weekly | annualised simple | 90% week-cluster interval (weekly) | Sharpe | NAV drawdown over the span |
|---|---|---|---|---|---|---|---|
| last_52 | ok | 52 | -0.0128% | -0.66% | [-0.0318%, 0.0060%] | -1.10 | -0.88% |
| last_104 | ok | 104 | 0.0360% | 1.87% | [0.0154%, 0.0573%] | 1.97 | -0.88% |

Rolling 52-eligible-week annualised simple return: state ok, windows 292, min -5.59%, max 31.41%, last -0.66% (overlapping windows; no share-positive statistic is reported).

### Ledger columns (money in units of the inception notional N = 1; one CSV per asset and variant)

| column | meaning |
|---|---|
| boundary_ms / boundary_iso | the funding boundary (UTC) |
| position_open | a position is held after this boundary's actions |
| marks_carried_forward | true at a merged boundary whose close is absent: the marks are the last marked closes |
| spot_qty | spot units held (also the perpetual short size) |
| spot_mark / perp_mark | the 1h close of the hour ending at the boundary (last traded price) |
| spot_value | spot_qty x spot_mark, 0 when nothing is held |
| margin | the perpetual margin account, marked and credited with funding |
| cash | everything else: the whole wealth when flat, the cumulative fees while open |
| nav | cash + margin + spot_value; equals wealth at every row within 1e-12 |
| funding_received / fees_paid / traded_notional | this period's funding, fees and traded notional |
| period_return_on_capital | change in nav divided by the inception capital 1.5 (the gates' basis) |
| period_return_on_nav | change in nav divided by the previous nav (0 at the entry row) |
| flagged / rebalances / forced_liquidations / unverifiable_open / rejected_open | the period's flags and counts |

V0 ledgers (sha256): `ledger_BTCUSDT_V0.csv` `092d6d7b5a2c…`, `ledger_ETHUSDT_V0.csv` `ebe313f380e2…`.

### Deribit cross-check (coin-margined, funding only; descriptive)

| currency | eligible weeks | gross funding on notional, annualised | net of one amortised round trip |
|---|---|---|---|
| BTC | 359 | 6.98% | 6.94% |
| ETH | 359 | 5.01% | 4.97% |

## Lane B — variance risk premium existence (DVOL² minus realised, 30-day non-overlapping windows)

| quantity | value |
|---|---|
| windows accepted (pooled, one cluster per window) | 66 of 66 |
| mean VRP (variance points) | 1116.7 |
| 0.025-quantile lower bound, minimum of the two | 250.5 points |
| complete years positive (a year needs >= 10 accepted windows) | 3 of 4 |

| ISO year | mean VRP (variance points) |
|---|---|
| 2021 | 3272.5 |
| 2022 | 2461.5 |
| 2023 | 581.1 |
| 2024 | 548.8 |
| 2025 | -168.3 |
| 2026 | 9.3 |

| currency | windows accepted / total | mean VRP (points) | share positive | rejections |
|---|---|---|---|---|
| BTC | 66 / 66 | 1233.1 | 74.2% | {} |
| ETH | 66 / 66 | 1000.2 | 65.2% | {} |

Lane B answers only whether a premium exists; whether a defined-risk option structure captures it net of option spreads needs historical option quotes, which are paid data.

Realised variance is computed over the 720 return intervals of each window (the closes from the window's start to its end, 721 closes); WO-166's alignment used the 720 closes whose bars open inside the window, 719 intervals. BTC: 66 of 66 windows carry exactly one more valid interval than under WO-166's alignment; ETH: 66 of 66 windows carry exactly one more valid interval than under WO-166's alignment.

## Reconciliation against WO-167

Every leaf differing from the committed `results_wo167/` files, with its registered reason; exact-path differences are listed one by one and prefix-matched groups are summarised.

### carry_v0.json: 49 differing leaves, 237 identical

| leaf | WO-167 | WO-170 | reason |
|---|---|---|---|
| `code_revision` | bd560fbe40c380c61239fe3dec76a1c69b8a050e | a4f1cf07cf48ea9b1ccaf376a48d9c4add163821 | clock_or_revision |
| `drawdown_basis` | <absent> | nav | configuration_switch |
| `generated_at` | 2026-09-13T05:45:08Z | 2026-09-13T06:22:35Z | clock_or_revision |
| `per_asset.BTCUSDT.cagr_nav` | <absent> | 0.07417875381680172 | new_descriptive_field |
| `per_asset.BTCUSDT.max_drawdown_nav` | <absent> | -0.008474862744197953 | drawdown_basis |
| `per_asset.ETHUSDT.cagr_nav` | <absent> | 0.08609140437400309 | new_descriptive_field |
| `per_asset.ETHUSDT.max_drawdown_nav` | <absent> | -0.005228286354487799 | drawdown_basis |
| `pooled.annualised_return_on_nav` | <absent> | 0.0765383017267117 | new_descriptive_field |
| `pooled.cagr_nav` | <absent> | 0.0802279284188423 | new_descriptive_field |
| `pooled.max_drawdown_nav` | <absent> | -0.004877957240028152 | drawdown_basis |
| `pooled.mean_weekly_return_on_nav` | <absent> | 0.0014718904178213786 | new_descriptive_field |
| `pooled.total_return_on_capital_simple` | <absent> | 0.6712372604883686 | new_descriptive_field |
| `return_basis` | <absent> | simple_on_inception_capital | configuration_switch |
| `rv_alignment` | <absent> | return_intervals | configuration_switch |
| `work_order` | WO-167 | WO-170 | work_order_label |
| `bases.*` (6 leaves) | — | — | bases_block |
| `ledger_files.*` (2 leaves) | — | — | ledger_export |
| `pooled.recent_period.*` (26 leaves) | — | — | recent_period_cut |

### carry_v1.json: 49 differing leaves, 205 identical

| leaf | WO-167 | WO-170 | reason |
|---|---|---|---|
| `code_revision` | bd560fbe40c380c61239fe3dec76a1c69b8a050e | a4f1cf07cf48ea9b1ccaf376a48d9c4add163821 | clock_or_revision |
| `drawdown_basis` | <absent> | nav | configuration_switch |
| `generated_at` | 2026-09-13T05:45:08Z | 2026-09-13T06:22:35Z | clock_or_revision |
| `per_asset.BTCUSDT.cagr_nav` | <absent> | 0.06046736231230798 | new_descriptive_field |
| `per_asset.BTCUSDT.max_drawdown_nav` | <absent> | -0.03248578110765532 | drawdown_basis |
| `per_asset.ETHUSDT.cagr_nav` | <absent> | 0.07238543201278902 | new_descriptive_field |
| `per_asset.ETHUSDT.max_drawdown_nav` | <absent> | -0.049573730015776696 | drawdown_basis |
| `pooled.annualised_return_on_nav` | <absent> | 0.06390305666435707 | new_descriptive_field |
| `pooled.cagr_nav` | <absent> | 0.06652052512056938 | new_descriptive_field |
| `pooled.max_drawdown_nav` | <absent> | -0.040864109333557885 | drawdown_basis |
| `pooled.mean_weekly_return_on_nav` | <absent> | 0.0012289049358530206 | new_descriptive_field |
| `pooled.total_return_on_capital_simple` | <absent> | 0.5350742884895947 | new_descriptive_field |
| `return_basis` | <absent> | simple_on_inception_capital | configuration_switch |
| `rv_alignment` | <absent> | return_intervals | configuration_switch |
| `work_order` | WO-167 | WO-170 | work_order_label |
| `bases.*` (6 leaves) | — | — | bases_block |
| `ledger_files.*` (2 leaves) | — | — | ledger_export |
| `pooled.recent_period.*` (26 leaves) | — | — | recent_period_cut |

### vrp.json: 1112 differing leaves, 93 identical

| leaf | WO-167 | WO-170 | reason |
|---|---|---|---|
| `code_revision` | bd560fbe40c380c61239fe3dec76a1c69b8a050e | a4f1cf07cf48ea9b1ccaf376a48d9c4add163821 | clock_or_revision |
| `drawdown_basis` | <absent> | nav | configuration_switch |
| `generated_at` | 2026-09-13T05:45:08Z | 2026-09-13T06:22:35Z | clock_or_revision |
| `per_currency.BTC.mean_realised_variance` | 0.3062166213179693 | 0.30629580747748997 | rv_alignment |
| `per_currency.BTC.mean_vrp` | 0.12338914625778832 | 0.12330996009826767 | rv_alignment |
| `per_currency.BTC.mean_vrp_points` | 1233.8914625778834 | 1233.0996009826767 | rv_alignment |
| `per_currency.BTC.share_of_windows_positive` | 0.7272727272727273 | 0.7424242424242424 | rv_alignment |
| `per_currency.ETH.mean_realised_variance` | 0.541465397748203 | 0.5422051556679445 | rv_alignment |
| `per_currency.ETH.mean_vrp` | 0.10076074528210006 | 0.10002098736235855 | rv_alignment |
| `per_currency.ETH.mean_vrp_points` | 1007.6074528210004 | 1000.2098736235857 | rv_alignment |
| `pooled.mean_vrp` | 0.11207494576994419 | 0.11166547373031312 | rv_alignment |
| `pooled.mean_vrp_points` | 1120.7494576994418 | 1116.6547373031312 | rv_alignment |
| `return_basis` | <absent> | simple_on_inception_capital | configuration_switch |
| `rv_alignment` | <absent> | return_intervals | configuration_switch |
| `work_order` | WO-167 | WO-170 | work_order_label |
| `bases.*` (6 leaves) | — | — | bases_block |
| `per_currency.BTC.windows.*` (528 leaves) | — | — | rv_alignment |
| `per_currency.BTC.yearly_mean_vrp.*` (6 leaves) | — | — | rv_alignment |
| `per_currency.ETH.windows.*` (528 leaves) | — | — | rv_alignment |
| `per_currency.ETH.yearly_mean_vrp.*` (6 leaves) | — | — | rv_alignment |
| `pooled.bootstrap.*` (10 leaves) | — | — | rv_alignment |
| `pooled.lower_bound_gate_level.*` (3 leaves) | — | — | rv_alignment |
| `pooled.year_check.*` (4 leaves) | — | — | rv_alignment |
| `pooled.yearly_mean_vrp.*` (6 leaves) | — | — | rv_alignment |

## Reconciliation against WO-166

WO-167 changed exactly two things against WO-166's committed results, recorded in WO-167's charter entry of 2026-09-13: the completeness scope of G3's unverifiable count (`"either"` → `"perp"`, 16 → 0, with the 16 spot-rejected periods disclosed beside it) and the resulting G3 and Lane A verdict. Every other leaf was identical to the last digit. This pass reconciles against WO-167's files, so those two changes are inherited here and every further difference is listed above.

