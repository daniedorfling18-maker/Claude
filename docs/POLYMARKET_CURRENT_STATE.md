# Polymarket Current State

Last updated: 2026-07-01

## Status in one paragraph

The Polymarket engine is currently in **automated local paper/shadow-research mode**. Mechanical paper readiness is open, live trading remains disabled, and the broker must still receive governed signals before it can fill paper orders. The infrastructure now works locally: websocket collection, metadata enrichment, broad liquidity discovery, prediction, alpha scoring, shadow evidence capture, Strategy V2 price-action evidence, scheduled automation, and dashboard reporting. The blocker is no longer a broken broker or broken websocket path. The blocker is **insufficient positive forward/price-action cohort evidence**.

## Current operating verdict

```text
paper_readiness = true
approved_price_action_paper_signals = 0
live_trading_invoked = false
```

The latest audit blocks new entries for three reasons:

```text
no approved normal trade_signals rows
no price-action cohort has passed positive bid/ask round-trip evidence
current fast price-action scout evidence is negative/incomplete
```

This is the correct fail-closed state.

## What changed during the audit/debug cycle

### 1. The websocket route was fixed

The collector now selects liquidity-derived token IDs and retries the supported Polymarket websocket subscription envelopes. The working envelope observed locally is `assets_ids`. The normaliser now enriches websocket feature rows with market metadata, so `unknown` no longer dominates target data.

### 2. The system moved from silent failure to explainable refusal

Earlier, no trades occurred because no approved signals reached the broker. The paper broker was not the root problem. Rejections happened upstream in alpha, validation, cohort promotion, liquidity, spread, and model-window gates. The current audit makes that explicit.

### 3. Bad 5-minute crypto families are not trusted

Fast 5-minute crypto Up/Down families produced negative shadow/settlement evidence and remain excluded from actionable fast-feedback routing:

```text
crypto_btc_updown_5m
crypto_sol_updown_5m
crypto_xrp_updown_5m
crypto_updown_5m
```

They may still appear in diagnostics, but they should not be treated as a path to paper risk.

### 4. `unknown` is diagnostic, not actionable

Some liquid markets classify as `unknown` because the market-family parser does not yet understand them. Examples include esports, tennis outrights, and legal/policy questions. These are useful for research and classifier improvement, but they must not be promoted until a family-specific model and validation path exist.

### 5. `sports_other` is the first real alpha candidate, but not proven

The accepted alpha-shadow candidates are currently World Cup round-of-16 related `sports_other` markets, especially Mexico/Portugal style markets. They are real candidates, but they are still open, negative mark-to-market, and not settled. They close slowly, so they cannot yet support paper promotion.

### 6. BTC 15-minute Up/Down is fast feedback, not the strategy

`crypto_btc_updown_15m` is currently the only valid fast-feedback family discovered with tight liquidity and a short time-to-close window. It is useful because it can test infrastructure/model timing quickly. It is **not** the target strategy by itself, and it has not become accepted alpha-shadow evidence yet.

### 7. Discovery has been broadened

Liquidity discovery now searches broadly across Polymarket opportunity areas, not only crypto:

```text
sports, soccer, football, basketball, baseball, tennis, golf, UFC,
esports, World Cup, politics, elections, Trump, Fed, inflation,
economy, stocks, crypto, BTC/ETH/SOL/XRP, AI, OpenAI, SpaceX,
weather, culture
```

Websocket selection now balances liquid targets across families instead of allowing one family, such as BTC 15m, to consume every slot.

## Current automation

The local Windows scheduled task is:

```text
Polymarket Shadow Research Cycle
```

It runs:

```text
liquidity discovery
websocket collection
websocket normalisation
feature build
prediction
mispricing alpha scoring
signal generation as dry/governance output
alpha-candidate shadow evidence
local-history audit
profit-sprint target refresh
price-action microstructure evidence
price-action scout round-trip evidence
governance/model/research-focus refresh
dashboard render
```

It writes the latest status here:

```text
work/shadow_research_cycle_latest_status.json
```

A healthy run has:

```text
status = ok
price_action_model_decision = collect_more_bid_ask_price_action_model_evidence or better
paper_trading_invoked = false
live_trading_invoked = false
```

If RAM crosses the configured local guardrail after the cycle has already started, the runner now stops
cleanly instead of continuing through heavier modelling/dashboard steps:

```text
status = stopped_high_memory
paper_trading_invoked = false
live_trading_invoked = false
```

When memory is below the stricter dashboard-only guardrail, the runner may still refresh the static
dashboard after a protected stop so oversight stays current without starting websocket/model work.

## Current important output files

```text
work/shadow_research_cycle_latest_status.json
outputs/polymarket_model_governance/local_history_audit_report.md
outputs/polymarket_model_governance/local_history_audit_summary.json
outputs/polymarket_model_governance/liquidity_discovery_summary.json
outputs/polymarket_model_governance/websocket_liquidity_targets.csv
outputs/polymarket_model_governance/alpha_candidate_shadow_evidence_inputs.csv
outputs/polymarket_shadow/shadow_positions.csv
outputs/polymarket_shadow/shadow_fills.csv
outputs/polymarket_predictions/mispricing_alpha_scores.csv
outputs/polymarket_predictions/rejected_signals.csv
outputs/polymarket_predictions/trade_signals.csv
```

`trade_signals.csv` being empty is currently expected. It means the governance layer is not allowing paper trades.

## Promotion requirements before paper can be considered

Do not paper trade until the audit stops blocking and the relevant family has forward evidence. The important requirements are:

```text
approved trade signals exist
family-specific shadow evidence is positive
closed/settled fills exist
ROI is positive and clears the configured threshold
monthly run-rate is plausible
cohort is probationary/promoted by governance
```

The goal is not to get any trade. The goal is to find a repeatable family with positive forward evidence.

Strategy V2 also maintains a separate round-trip price-action ledger. That ledger simulates buying
at the candidate entry ask/executable price and selling or marking at later websocket bids, so it can
learn whether odds movement creates a tradable edge before markets settle. It is useful fast feedback,
but it is not settlement proof and does not by itself authorise paper or live trading.

The engine also runs a fast price-action scout for liquid short-window/profit-sprint targets. This
persists shadow-only entry prices from liquidity/profit-sprint candidates and evaluates later websocket
bids for take-profit/stop-loss evidence. It is a throughput layer for learning, not a promotion bypass:
candidate cohorts still need positive evidence and governance review before paper trading.

The price-action model now treats a positive label as a tradable repricing event, not merely any small
green mark-to-bid movement. It also tests train-only rank/quantile thresholds for rare-event ranking,
then still fails closed unless the selected validation slice clears P&L, ROI, win-rate, confidence, and
risk gates. The model now also checks cohort transfer: if train positives and validation positives
appear in different market families, the edge is not treated as repeatable. On the 2026-07-01
model-only refresh this improved the diagnosis but did not approve paper: train-ranked slices remained
negative and the tradable positives did not transfer across cohorts, so the next edge work is better
feature separation and more forward bid/ask evidence rather than looser thresholds.

There is now a separate settlement-independent price-action paper-signal bridge:

```text
outputs/polymarket_price_action/price_action_paper_signals.csv
outputs/polymarket_price_action/price_action_paper_rejections.csv
outputs/polymarket_price_action/price_action_paper_signal_summary.json
```

This bridge can create paper broker signals without waiting for market settlement, but only after a
cohort has already passed closed bid/ask round-trip gates. It keeps the key distinction clear:

```text
settlement evidence = did the final outcome model beat the market?
price-action evidence = could the bot buy at ask and sell/mark at bid profitably?
```

The paper broker now reads both normal model trade signals and price-action paper signals, marks open
positions from websocket bid/ask quotes when available, and applies fast price-action take-profit/
stop-loss settings to price-action entries.

The newest edge layer is the websocket microstructure lab:

```text
outputs/polymarket_price_action/microstructure_trade_events.csv
outputs/polymarket_price_action/microstructure_rule_evidence.csv
outputs/polymarket_price_action/microstructure_current_candidates.csv
outputs/polymarket_price_action/microstructure_summary.json
```

It tests pre-declared bid/ask patterns such as tight-book bid momentum, midpoint momentum, buy-pressure,
and spread compression. Each rule is split chronologically into train/validation evidence and is scored
using entry ask and future exit bid. Rules that fail validation stay shadow-only and do not feed the
paper broker.

The control loop now also writes a consolidated price-action feedback artifact:

```text
outputs/polymarket_model_governance/price_action_feedback.json
```

This treats price movement as a first-class outcome: it consolidates Strategy V2 round trips, fast
scout cohorts, and microstructure validation into collect/promote/suppress actions. It is still
shadow-only governance. Positive bid/ask cohorts are prioritised for more websocket collection; negative
cohorts are suppressed until a new thesis appears; paper promotion still requires positive forward cohort
evidence rather than a forced trade.

The governance refresh now also writes post-trade edge attribution:

```text
outputs/polymarket_model_governance/edge_attribution.json
outputs/polymarket_model_governance/edge_attribution_positions.csv
outputs/polymarket_algo/algo_sweep_summary.json
outputs/polymarket_algo/algo_sweep_combos.csv
```

This decomposes closed shadow P&L into execution cost, line movement, and settlement surprise, then
classifies cohorts as cost-dominated, direction-wrong, settlement-adverse, mixed, or positive-edge
confirmed. The algo sweep lab searches event-driven strategy parameters over recorded websocket
history with train-only selection and out-of-sample validation. Both are diagnostic only and do not
authorise paper or live trading; collection steering consumes them after the WO-11 research-focus
wiring.

### 2026-07-13 governance-refresh recovery

The strict price-action model became stale even while websocket collection and the dashboard stayed
fresh. The failure was operational, not evidence-positive: paper fills used market slugs while
snapshots used condition ids, so the intentional token-id alias lookup repeatedly scanned the full
multi-gigabyte snapshot ledger. Full governance timed out before reaching model training.

The durable recovery adds the missing `(token_id, collected_at)` index, an OS-released cross-process
governance lock, and `outputs/polymarket_model_governance/governance_refresh_status.json` with the
active stage and stage durations. The model-critical stages now run before slower research/reporting
stages, so a later report failure cannot leave the strict model timestamp stale. These changes alter
neither evidence thresholds nor paper/live gates and invoke no trading path.

## Why $100/month is not solved yet

At the current probationary stake of $2, a 3% ROI produces only $0.06 per trade. Hitting $100/month at that level would require about 1,667 trades/month, which is not realistic. The route to the target is therefore:

```text
1. Discover broad liquid opportunities.
2. Classify them into reliable families.
3. Prove one family in shadow with positive closed evidence.
4. Move only that family to tiny probationary paper.
5. Scale stake only after the evidence remains positive.
```

Scaling stake before evidence would scale losses, not expected value.

## Next research priorities

1. **Keep broad discovery enabled.** The scanner must search all plausible Polymarket opportunity areas, not only BTC Up/Down.
2. **Improve family classification.** Liquid `unknown` markets should be categorised into real families such as esports, tennis outrights, legal/policy, culture, weather, macro, and company/tech events.
3. **Add independent anchors where possible.** Sports candidates need sharper fair probabilities, ideally bookmaker/no-vig or exchange-based fair odds. Crypto event candidates need independent fundamental anchors where possible.
4. **Keep BTC 15m as a timing diagnostic only.** It is useful for fast feedback, but it should not become the strategy unless it produces accepted positive shadow evidence.
5. **Wait for sports evidence to close/settle.** `sports_other` is currently the only accepted alpha-shadow family, but it is not yet positive or settled.

## Safety rule

Do not weaken gates to force activity. No paper or live risk should be taken because the system is bored, quiet, or waiting for settlement. The current correct state is automated shadow research plus explicit audit blocking.
