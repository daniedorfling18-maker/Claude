# Polymarket live learning system design

Last reviewed: 2026-06-26

This bot is designed as a local, paper-first, continuously learning trading system. The goal is not to trust a clever one-off model. The goal is to run a live loop that repeatedly observes markets, scores opportunities, rejects weak signals, records forward evidence, and only promotes cohorts after positive paper/shadow evidence.

## Operating method: build, test, measure, learn

1. **Build small edges as isolated layers.** Each edge source must expose diagnostics and fail closed when its inputs are missing, stale, or outside the model’s design window.
2. **Test before restart.** Any model or trading-rule change needs focused tests for the exact failure mode it is trying to prevent.
3. **Measure forward evidence.** Backtests and probes can suggest hypotheses, but promotion depends on live paper/shadow cohorts.
4. **Learn from the newest data.** Live snapshots, predictions, rejects, shadow fills, paper fills, and settlements feed the cohort ledger and future training runs.
5. **Promote only after evidence.** A cohort can only graduate after the promotion gate sees positive forward P&L/ROI and enough observations.

## Current dependency and interdependency map

```text
Polymarket public/Gamma/CLOB APIs
        |
        v
live market scanner
        |
        v
raw market snapshots  ----->  data quality + freshness checks
        |                                |
        v                                v
feature builder ---------------> calibrated baseline model
        |                                |
        v                                v
contract-specific overlays ---> mispricing alpha engine
        |                         |      |
        |                         |      +--> rejection reasons
        |                         |      +--> signal cohorts
        |                         v
        |                   paper strategy + risk controls
        |                         |
        v                         v
shadow ledger <------------ paper broker ledger
        |                         |
        v                         v
cohort forward P&L + promotion gate
        |
        v
dashboard + next scan priorities + future training evidence
```

Key external dependencies:

- Polymarket Gamma/CLOB: market discovery, prices, depth, slugs, outcomes, close times.
- Binance public market data: contract-specific daily crypto Up/Down pricing anchor.
- Optional sharp/fundamental sources: bookmaker odds, Deribit option-implied probabilities, manually supplied probability CSVs.
- Local Docker: runs the paper bot locally so it can access local data and the host machine’s Docker/network context.
- Local dashboard server: reads generated dashboard artifacts and exposes them on the laptop.

## Separation of concerns

- **Scanner:** finds live candidate markets; does not decide what to buy.
- **Feature builder:** normalises fields and preserves market metadata such as question, outcome, close time, price, spread, and liquidity.
- **Baseline model:** provides calibrated market-aware probabilities when enough historical data exists.
- **Contract overlays:** add domain-specific probability estimates, such as the daily crypto Up/Down model.
- **Alpha layer:** blends model evidence, applies costs and uncertainty penalties, and emits transparent rejection reasons.
- **Strategy/risk layer:** decides whether a signal is paper-tradable under bankroll, spread, liquidity, and promotion rules.
- **Paper broker/shadow broker:** records hypothetical fills and settlement outcomes.
- **Cohort validation:** measures forward P&L by signal family and controls promotion.
- **Dashboard:** reports state; it must not contain trading logic.

## Live learning loops

- **Market loop:** scan fresh markets continuously in bounded batches.
- **Prediction loop:** score every eligible row with diagnostics and fail-closed statuses.
- **Shadow loop:** record promising but unpromoted signals so the model can learn without risking bankroll.
- **Paper loop:** only place paper trades when risk and promotion/probation rules allow it.
- **Settlement loop:** resolve completed markets and update P&L.
- **Promotion loop:** promote only cohorts with positive forward evidence.
- **Retraining loop:** periodically rebuild calibrated/optimized models from the expanded labelled dataset.
- **Priority loop:** scan promoted/probationary/high-evidence cohorts more often.

## Fail-fast and fail-closed rules

The system should reject instead of guessing when:

- market metadata is missing;
- close time cannot be parsed;
- market is outside the model’s supported window;
- spread or relative spread is too high;
- liquidity is too low;
- external price data cannot be fetched;
- the row budget for the cycle is exhausted;
- memory pressure crosses the runtime guard;
- a cohort has not produced positive forward evidence.

## Non-functional requirements

- **Safety:** paper trading by default; no live-money promotion without the approval checklist.
- **Observability:** every score needs diagnostics, rejection reasons, cohort ID, and dashboard output.
- **Resource control:** bounded batch scans, API timeouts, row budgets, cache windows, and memory guard.
- **Reproducibility:** config-driven settings, committed tests, versioned outputs, and deterministic model seeds where possible.
- **Security:** no credentials in git; secrets stay in local environment or GitHub Secrets for actions that need them.
- **Resilience:** external API failures should skip that evidence source, not crash the bot.
- **Latency awareness:** live markets move quickly; the local loop should refresh evidence continuously rather than relying on manual probes.

## Bot framework decision

Official docs checked on 2026-06-26:

- [NautilusTrader adapters](https://nautilustrader.io/docs/latest/developer_guide/adapters/) describe a venue/data-provider adapter model that maps well to Polymarket long term.
- [NautilusTrader execution](https://nautilustrader.io/docs/latest/concepts/execution/) includes explicit trading states such as active, halted, and reducing, which is the right safety pattern for a future production engine.
- [Hummingbot](https://hummingbot.org/docs/) is a modular Python framework for automated market-making and algorithmic bots, strongest when a connector already fits the venue.
- [Freqtrade](https://www.freqtrade.io/en/stable/) is a strong crypto bot with backtesting, web UI, and machine-learning optimisation, but its official exchange model is CCXT-oriented, which is less natural for Polymarket’s prediction-market CLOB.

Decision:

- **Now:** keep the current lightweight local Docker loop. It already has the correct Polymarket-specific scanner, paper broker, promotion ledger, and dashboard.
- **Near term:** keep refactoring toward Nautilus-style adapter boundaries: market data adapter, model adapter, risk adapter, execution adapter, and persistence adapter.
- **Later:** consider a NautilusTrader adapter if the system graduates from paper trading to robust live trading. Consider Hummingbot only if market-making/connector reuse becomes the central strategy. Do not migrate to Freqtrade for Polymarket unless the strategy becomes ordinary crypto exchange trading.

## Current edge-development priority

The next edge work should remain cohort-driven:

1. Daily crypto Up/Down contract-specific live model.
2. World Cup validation layer with bookmaker cross-checks and fundamental probability haircuts.
3. Spread/liquidity filters before any paper fill.
4. Forward paper/shadow P&L by signal cohort.
5. Promotion only after positive cohort evidence.

