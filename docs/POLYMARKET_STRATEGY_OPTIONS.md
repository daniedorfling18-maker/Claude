# Strategy Options Memo — beyond the current program (2026-07-03)

What else the existing infrastructure could earn with, ranked by (edge prior x infrastructure
reuse x effort). Companion to `POLYMARKET_EDGE_STRATEGY_RESET.md`; nothing here loosens a gate.

## 1. Smart-flow tracking: measure OTHER traders' CLV, follow the proven ones

Polymarket activity is public (on-chain + data API): every wallet's fills and positions are
observable. Our CLV engine doesn't care whose fills it scores. Repurpose it:

```text
collect public fills per wallet -> score each wallet's CLV with closing_line.py machinery
-> treat each wallet as a cohort in the existing promotion pipeline
-> wallets with sustained positive CLV on 50+ closed lines become signal sources
-> shadow their entries (existing shadow-cohort path), then paper via normal governance
```

Why this is the strongest repurpose: we stop needing our own forecasting alpha and instead
*detect who has it*, with the same fail-closed evidence standards we apply to ourselves. Wallet
promotion = cohort promotion; nothing new philosophically. Risks: latency (their fill moves the
price before we see it — measurable as entry slippage by edge_attribution), capacity, wallets
going cold (CLV decay handles this). Effort: one new collector (data API fills by market/wallet)
+ a wallet-cohort adapter. Everything downstream exists.

## 2. Market making / liquidity provision (flip from taker to maker)

Polymarket runs liquidity rewards for resting two-sided quotes near the mid. We already have:
real-time book + depth/imbalance features, `market_making_pnl.py`, join-bid execution policies in
the intent schema, and the replay harness to calibrate quoting offsets/inventory caps offline.
The business changes from "predict outcomes" to "get paid for spread + rewards while controlling
adverse selection" — and our microstructure tooling (imbalance, anti-chase features) becomes the
*defence* rather than the alpha. Prior: moderate-to-good; MM in prediction markets is a real
business, but adverse selection near news/resolution is the killer — quoting must auto-widen or
pull on volatility signals we already compute. Start: replay-based MM simulation strategy class
(pure research), then paper. Verify current reward-program terms before sizing anything.

## 3. Cross-market consistency arbitrage (internal relative value)

Dutch-book scanning within one market generalises to constraint graphs ACROSS markets:

```text
monotone date buckets:  P(X by July) <= P(X by August)
containment:            P(wins final) <= P(reaches final)
partition sums:         sum of mutually-exclusive winners <= 1 (+ fees)
conditional chains:     P(A and B) <= min(P(A), P(B))
```

Violations are pure-logic mispricings needing no external anchor and no model. The family
classifier + market metadata give us the raw material to auto-detect related market sets
(same event slug, same entity across date buckets). Extends `dutch_arb_monitor` into a
"consistency scanner". Capacity small, but the evidence class is mechanical — same tier as arb.

## 4. Cross-venue basis: Polymarket vs Kalshi (and Deribit for crypto)

Same real-world event, two venues, persistent price gaps (documented on Fed/election/econ
markets). Kalshi is US-legal with a clean API — relevant given Polymarket's US geoblock for any
future live path. The whole stack is venue-shaped, not Polymarket-shaped: a Kalshi collector
feeding the same normalised schema unlocks basis monitoring with the dutch-arb reporting pattern,
and later two-legged paper. The Deribit fetcher (`crypto_fundamental.py`) already does this
one-sidedly for crypto strikes — the pattern is proven in-repo.

## 5. News-latency on slow markets (human-in-the-loop first)

`external_feed_collector.py` exists. Slow policy/sports markets reprice minutes-to-hours after
news. A stale-price flagger (news timestamp vs last book move on related markets) that ALERTS a
human is realistic; fully automated news trading is not, at our latency. Pairs naturally with #1
(smart wallets often ARE the news traders — their fills are the alert).

## 6. Analytics as the product (non-trading monetisation)

The pipeline already produces institutional-grade prediction-market analytics nobody publishes:
per-family calibration scorecards, CLV leaderboards, arb/consistency violation logs, order-book
history. If trading edge stays elusive, the same artifacts are a data product (newsletter/API).
Zero market risk; the honesty machinery becomes the selling point.

## Considered and rejected

- **Theta harvesting near-certain outcomes** (buy 0.97, collect 3c): negative skew, oracle/
  dispute tail risk wipes months of grind; also now outside our entry-price band by design.
- **Fully automated news sniping**: latency game we lose.
- **Higher-frequency microstructure taking**: our own sweep/replay evidence already says no.

## Suggested sequencing

Land the reset queue first (WO-24 sharp anchor, WO-25 dutch-arb, WO-26 anti-concentration —
they're specced and cheap). Then the two best candidates from this memo, in order:

1. **Smart-flow tracking** (new collector + wallet-cohort adapter; reuses CLV/promotion wholesale)
2. **Consistency scanner** (extends dutch-arb; pure logic, no external dependencies)

with **market-making simulation** as the replay-lab research track behind them, and **Kalshi
basis** as the expansion once one venue is evidently instrumented end-to-end. Each becomes a
work order with the usual fail-closed acceptance criteria before any agent codes it.
