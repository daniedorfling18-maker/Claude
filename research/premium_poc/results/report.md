# WO-166 proof-of-concept results (historical-class diagnostic)

This is a historical-class result computed from the committed inputs listed in `manifest.json`. The gate in Lane A is applied to the lower bound of a bootstrap interval minus a haircut of 2.0% per year for bias channels this data cannot measure (slippage beyond the taker fee, the intra-hour liquidation path, venue operational frictions, and selection). **That haircut is a declared assumption, not a measurement.** Unfavourable channels (VIP0 taker fees with no rebate, zero collateral yield, capital at 1.5x notional, no re-leveraging) are not credited back. Nothing here is verification of record, registers a primary, or authorises capital.

Generated at 2026-09-12T14:48:28Z from code revision `58e996f2b2b895a28ba8fc64e0bb46e7b0ae38d7`; manifest sha256 `2152b1b4e3fd1a6ffc8f9881c1a7663d33a24e27b8205824d98438c95df2ea50`.

## Verdicts

**Lane A — funding carry (V0, always on): NO-GO** (G1=pass, G2=pass, G3=FAIL, G4=pass)

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
| max drawdown, all weeks (peak-to-trough, negative; G3 reads its magnitude) | -0.32% |
| CVaR 95% weekly (mean loss magnitude in the worst 5% of weeks) | 0.05% |
| forced liquidations | 0 |
| open-position periods with missing bars (liquidation unverifiable; G3 requires 0) | 16 |
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
| max drawdown, all weeks (peak-to-trough, negative) | -5.56% |
| CVaR 95% weekly (mean loss magnitude in the worst 5% of weeks) | 0.27% |
| forced liquidations | 0 |
| open-position periods with missing bars (liquidation unverifiable) | 14 |
| rebalances | 59 |
| complete ISO years positive (a year needs >= 45 eligible weeks) | 5 of 6 |

### Deribit cross-check (coin-margined, funding only; descriptive)

| currency | eligible weeks | gross funding on notional, annualised | net of one amortised round trip |
|---|---|---|---|
| BTC | 359 | 6.98% | 6.94% |
| ETH | 359 | 5.01% | 4.97% |

## Lane B — variance risk premium existence (DVOL² minus realised, 30-day non-overlapping windows)

| quantity | value |
|---|---|
| windows accepted (pooled, one cluster per window) | 66 of 66 |
| mean VRP (variance points) | 1120.7 |
| 0.025-quantile lower bound, minimum of the two | 258.6 points |
| complete years positive (a year needs >= 10 accepted windows) | 3 of 4 |

| ISO year | mean VRP (variance points) |
|---|---|
| 2021 | 3280.1 |
| 2022 | 2456.6 |
| 2023 | 579.4 |
| 2024 | 572.9 |
| 2025 | -168.5 |
| 2026 | 7.4 |

| currency | windows accepted / total | mean VRP (points) | share positive | rejections |
|---|---|---|---|---|
| BTC | 66 / 66 | 1233.9 | 72.7% | {} |
| ETH | 66 / 66 | 1007.6 | 65.2% | {} |

Lane B answers only whether a premium exists; whether a defined-risk option structure captures it net of option spreads needs historical option quotes, which are paid data.

