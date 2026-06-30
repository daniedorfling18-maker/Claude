# Polymarket Strategy V2 — Anchored Edge Research

Last updated: 2026-06-29

## Status

This document defines the next Polymarket research lane. Strategy V2 is **shadow-only** and must run in parallel with the existing baseline shadow-research cycle. It must not execute paper or live orders, weaken existing governance gates, or overwrite Strategy V1 evidence.

Outputs for this lane should live under:

```text
outputs/polymarket_strategy_v2/
```

Strategy V1 remains useful as baseline telemetry, but it is no longer the main path to the `$100/month` target.

---

## 1. What failed in Strategy V1

Strategy V1 largely asked whether an internal model could beat the Polymarket market midpoint. The evidence so far says it cannot.

Observed failure pattern:

```text
approved_for_paper_trading = false
approved_for_live_trading = false
approved_signals = 0
rejected_signals = all current predictions
promoted_cohorts = 0
probationary_cohorts = 0
oos_beats_market_significantly = false
oos_brier_skill_vs_market < 0
```

The current blocker is not the scheduler, the broker, or the websocket path. The blocker is that the model has not shown statistically credible out-of-sample skill over the market midpoint.

Specific learnings:

1. **Market-midpoint prediction is not enough.** A model trained mostly around the market price becomes a market mirror. It may be calibrated, but it does not create tradeable edge.
2. **Generic 5-minute crypto is noisy.** Several 5m crypto cohorts have negative forward evidence and should remain diagnostic only.
3. **Positive-looking thin cohorts are not reliable.** Small-sample wins with one or two settled fills are not promotion evidence.
4. **`unknown` cannot be promoted.** The positive `near_miss_learning|unknown` bucket is explicitly metadata-blocked and must remain shadow-only until classified.
5. **The $100/month target is not reachable from tiny, unproven probes.** At low stake and low ROI, the required trade count is unrealistic.

Strategy V1 did succeed at building strong machinery: discovery, collection, scoring, shadow evidence, and governance. Strategy V2 reuses that machinery but changes the edge thesis.

---

## 2. New edge thesis

Strategy V2 does not ask:

```text
Can our model predict better than Polymarket?
```

It asks:

```text
Where is Polymarket price wrong relative to an independent, measurable fair-value anchor?
```

The edge must come from a source outside the Polymarket price itself.

Core thesis:

```text
No independent anchor = no alpha.
```

A candidate can only enter Strategy V2 shadow tracking if it has:

1. a classified market family,
2. an independent fair-probability anchor,
3. an executable Polymarket price,
4. sufficient liquidity and spread quality,
5. a positive anchor edge after penalties,
6. no metadata blocker.

The primary score is:

```text
anchor_edge = anchor_fair_probability - executable_price
risk_adjusted_anchor_edge = anchor_edge - spread_penalty - liquidity_penalty - uncertainty_penalty
```

Strategy V2 is a soft-price scanner, not a self-contained forecasting model.

---

## 3. Accepted market families

Only families with a plausible external anchor are accepted.

### Initially accepted

| Family | Status | Rationale |
|---|---|---|
| `macro_rates` | accepted | Can be anchored to rates-implied probabilities or other observable rate-market sources. |
| `crypto_btc_special` | accepted | Can be anchored to options-implied or spot/volatility-derived fair values. |
| `crypto_eth_special` | accepted | Can be anchored to options-implied or spot/volatility-derived fair values. |
| `crypto_updown_event` | accepted for research | Can be anchored to explicit short-window price/volatility model, but must remain shadow-only until proven. |
| `sports_other` | accepted only with bookmaker/no-vig anchor | World Cup and sports markets need sharp odds or exchange odds before any edge is trusted. |
| `esports_match` | accepted only with external odds anchor | Useful fast feedback if an external odds source exists. |
| `tennis_total` / `tennis_itf_total` / `tennis_atp_total` | accepted only with external odds anchor | Needs no-vig sportsbook or exchange anchor. |
| `ai_model_leader` | research-only | Needs a defined external anchor methodology before candidates can be considered anchored. |

### Explicitly not accepted

| Family | Reason |
|---|---|
| `unknown` | Metadata/classification unresolved. Research only. |
| `near_miss_learning|unknown` | Metadata-blocked. Never promotable. |
| unclassified event hashes | No family path and no anchor. |
| generic 5m crypto without scored external model | Too noisy; diagnostic only. |
| any family with no independent fair-value source | No anchor, no alpha. |

---

## 4. External anchors required

Strategy V2 accepts these anchor types:

### Sports and esports

Required anchor:

```text
sharp bookmaker odds, exchange odds, or no-vig fair probabilities
```

Expected input shape:

```text
market_slug,outcome,token_id(optional),decimal_odds,anchor_source,anchor_timestamp_utc
```

The pipeline must de-vig probabilities within a market before comparing against Polymarket.

### Crypto specials

Required anchor:

```text
options-implied probability, spot/volatility-derived probability, or other explicit market-implied fair value
```

Expected input shape:

```text
token_id,market_slug,outcome,fair_probability,anchor_source,anchor_timestamp_utc
```

Deribit-style probability files are acceptable when the market maps cleanly to strike/expiry logic.

### Macro rates

Required anchor:

```text
rates-implied fair probability from a transparent source
```

Expected input shape:

```text
market_slug,outcome,token_id(optional),fair_probability,anchor_source,anchor_timestamp_utc
```

### Manual anchors

Manual anchors are allowed only if every row includes:

```text
anchor_source
anchor_timestamp_utc
methodology_note
```

Manual anchors must remain shadow-only until reproducible.

---

## 5. Shadow-only experiment rules

Strategy V2 candidates are written to files only. They do not enter `trade_signals.csv` and do not call the paper broker.

Candidate output:

```text
outputs/polymarket_strategy_v2/anchored_edge_candidates.csv
outputs/polymarket_strategy_v2/worldcup_validated_anchors.csv
```

Report output:

```text
outputs/polymarket_strategy_v2/anchored_edge_report.json
outputs/polymarket_strategy_v2/anchored_edge_report.md
outputs/polymarket_strategy_v2/strategy_v2_forward_evidence.json
outputs/polymarket_strategy_v2/strategy_v2_forward_evidence.csv
outputs/polymarket_strategy_v2/strategy_v2_cohort_forward_evidence.csv
outputs/polymarket_strategy_v2/strategy_v2_round_trip_evidence.json
outputs/polymarket_strategy_v2/strategy_v2_round_trip_evidence.csv
outputs/polymarket_strategy_v2/strategy_v2_round_trip_cohort_evidence.csv
```

The forward-evidence files are mark-to-market research ledgers built from the Strategy V2
persistence log. They answer: "If we had shadow-bought this candidate at first observation, where
would it mark now?" They are not paper trades, not live trades, and not settlement proof. Promotion
still requires resolved/settled evidence plus human review.

The round-trip evidence files answer the faster trading question: "Could the candidate have been
bought at the entry ask/executable price and later sold into the observed websocket bid?" They apply
paper-only take-profit/stop-loss rules and use the bid, not midpoint, for exits. This gives faster
price-action feedback while markets remain unresolved, but it is still not proof that the probability
model is right and it does not bypass settlement governance.

Minimum candidate filters:

```text
classified family is accepted
anchor_fair_probability exists
0 < executable_price < 1
minimum_anchor_edge_after_penalty >= 0.03 for watchlist
minimum_anchor_edge_after_penalty >= 0.05 for shadow candidate
spread <= 0.02 for normal markets
relative_spread <= 0.15
liquidity >= 250 for normal markets
metadata_blocker is empty
```

Fast-feedback research markets may use lower liquidity only if explicitly labelled as fast-feedback and never promoted from thin evidence.

No Strategy V2 output may be used for paper trading unless a separate governance promotion report approves it later.

World Cup rows get one extra bridge artifact: `worldcup_validated_anchors.csv`. It is built from the
mispricing-alpha validation layer and only includes World Cup rows where the bookmaker/fundamental
cross-check passed. Strategy V2 uses the conservative `haircut_fundamental_probability` as the anchor
when available, not the raw fundamental probability.

---

## 6. Promotion gates

Promotion is deliberately harder than candidate selection.

A Strategy V2 family can only be considered for probationary paper review when all of these are true:

```text
family is classified and accepted
all candidate rows have independent anchors
no metadata blockers
>= 20 shadow entries
>= 10 settled shadow entries
settled P&L > 0
settled ROI >= 3%
monthly run-rate >= $20
median risk_adjusted_anchor_edge > 0
no single market contributes > 35% of settled P&L
latest validation report still blocks live trading by default
human review confirms anchor methodology
```

A Strategy V2 family can only be considered for full promotion when:

```text
>= 50 shadow entries
>= 30 settled shadow entries
settled ROI >= 5%
P&L remains positive after conservative spread/slippage haircut
performance remains positive across at least two independent days
anchor data is reproducible
family-specific false-positive review is clean
```

Paper probation, if ever allowed, must use the existing tiny stake cap first.

---

## 7. Kill criteria

Every hypothesis must have a hard stop.

Kill a Strategy V2 family if any of the following happens:

```text
10 settled candidates and ROI < 0
20 settled candidates and ROI < 3%
more than 50% of candidates lack clean anchors
more than 20% of candidates are unknown/unclassified
edge disappears after spread/liquidity/uncertainty penalties
one market or one event contributes more than 50% of positive P&L
anchor source cannot be reproduced
classification changes invalidate prior candidates
```

Kill the entire Strategy V2 lane if, after a reasonable collection window:

```text
no accepted family produces >= 20 anchored candidates
all accepted families have negative settled ROI
all apparent wins are thin-sample or metadata-blocked
```

No thresholds may be loosened to avoid a kill decision.

---

## 8. Daily reporting format

The daily report should answer only decision-useful questions.

Required summary:

```text
Strategy V2 status: collect_more_evidence | candidate_family_found | promote_to_review | kill_family | kill_lane
Generated at: <timestamp>
Accepted families scanned: <count>
Anchored candidates: <count>
Shadow candidates: <count>
Settled candidates: <count>
Settled P&L: <amount>
Settled ROI: <percent>
Top blocker: <reason>
Recommended action: <action>
```

Required family table:

| Family | Candidates | Settled | P&L | ROI | Median edge | Status | Action |
|---|---:|---:|---:|---:|---:|---|---|

Required candidate table:

| Family | Market | Outcome | Anchor fair | Executable price | Edge after penalty | Liquidity | Spread | Status |
|---|---|---|---:|---:|---:|---:|---:|---|

Required warnings:

```text
unknown candidate count
missing anchor count
thin sample count
metadata-blocked count
family concentration risk
single-market concentration risk
```

---

## Operating rule

Keep Strategy V1 scheduled shadow research running as baseline telemetry, but do not wait for it to solve the goal by itself. Strategy V2 is the primary research direction from here: independent anchors, family classification, strict shadow evidence, and fast kill criteria.
