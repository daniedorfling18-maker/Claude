# Polymarket Quant Trading Contract

This engine must treat quantitative trading as a strict research-to-execution process, not as a dashboard or signal-generation exercise.

## Trading objective

For paper trading, the primary edge is executable price movement:

```text
buy at current ask -> later sell/mark at executable bid
```

A model has not found a tradeable edge unless the predicted future bid clears:

```text
entry ask + spread/slippage/profit hurdle
```

Midpoint movement is not enough. Settlement probability is useful for slower markets, but it is not the same as a short-horizon trading edge.

## Required quant loop

1. Define the hypothesis before fitting.
2. Build point-in-time features from historical websocket/order-book data.
3. Label trades using executable prices only: entry ask and future exit bid.
4. Split chronologically; do not tune on validation.
5. Fit the model on train only.
6. Select thresholds on train only.
7. Validate out of sample against a buy-all/baseline policy.
8. Require positive selected validation P&L, ROI, win rate, trade count, clustered ROI confidence, and acceptable selected-trade drawdown/VaR.
9. Emit only shadow candidates until forward evidence confirms the edge.
10. Promote to paper only through governance after positive cohort evidence.

## Current strict price-action model

The strict price-action model is:

```text
src/polymarket_predictive_engine/price_action_model.py
```

Its target is:

```text
future executable bid reprices above historical entry ask
```

It writes:

```text
outputs/polymarket_price_action/price_action_model_summary.json
outputs/polymarket_price_action/price_action_model_validation_predictions.csv
outputs/polymarket_price_action/price_action_model_current_candidates.csv
```

If the available bid/ask history has no profitable future-bid examples, the correct output is a blocked model, not a forced trade.

## Quant research stack

The supporting quant curriculum is implemented under:

```text
src/quant_lab/
```

The bot now consumes that stack in governance:

```text
outputs/polymarket_model_governance/quant_research_status.json
```

The most important production-facing integrations are:

```text
selected-trade drawdown, VaR, CVaR, and performance reporting
promotion gating that fails closed when validation risk is unacceptable
chronological validation and train-only threshold selection
dashboard visibility of the quant stack and current model blockers
```
