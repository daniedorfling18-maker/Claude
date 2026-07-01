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
8. Require positive selected validation P&L, ROI, win rate, trade count, and clustered ROI confidence.
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
