# Maker quote sheet (WO-36) - RESEARCH OUTPUT, NOT ADVICE


<!-- requote-alerts:start -->
> **WO-66 live quote alert: quotes_ok** — All quoted markets pass the current read-only checks.
<!-- requote-alerts:end -->

<!-- decision-policy:start -->
## Registered decision policy (WO-50)

- Indicated action: **defer_funding_continue_study**
- Ladder stage permitted: **0** (binding capital $100.0)
- Kill criteria: **clear**
- Kill-input freshness: **fresh** (kill_data_stale=False, age=3.0s, max=1800.0s)
- Policy note: indicates only; the human decides; this system never trades.
<!-- decision-policy:end -->
Generated: 2026-08-20T10:46:56Z
Maker verdict: **insufficient_evidence** (M-A pending, M-B pending)
Estimated portfolio net carry: $1.68/day (~$50.4/month) on $470.0 capital - UPPER BOUND, see honesty clause.
Uncounted supplementary income shown separately: $0.0515/day (rebates + holding rewards; NOT included in gates or net carry).
Capital curve (planning aid - uncounted, not a gate input): $250 cap -> $0.00/day; $500 cap -> $1.68/day; $1000 cap -> $1.68/day; $2000 cap -> $1.68/day; $5000 cap -> $1.68/day; $100/month target not reached by the largest measured cap.

This system places NO orders. Acting on this sheet is a human decision,
with human money, outside the bot's paper-only governance.

## Exact human order tickets (WO-66)

Each row is a decision-support ticket only: BUY the named outcome at the bid and SELL it at the ask. Re-check the live alert before touching the UI.

| market URL | outcome side | bid | ask | size (shares/order) | capital | reference mid | est net/day | resolution risk | toxicity | risk flags | ticket |
|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---|
| [Mojtaba Khamenei seen in public by December 31?](https://polymarket.com/event/mojtaba-khamenei-public-appearance-by) | Yes | 0.2200 | 0.2500 | 1000 | $470.0 | 0.235 | $1.6821 | medium (other) | 0.45 | - | exact |

Standing rules (non-negotiable if a human ever acts on this):
1. Scheduled announcement: Never quote through a scheduled announcement; pull flagged rows at least 24h before the event and stay out until it settles.
2. Minimum-size start: Start at minimum size for a full reward day before any size-up.
3. Payout floor: Rewards below $1.0/market/day pay NOTHING; stay above the floor.
4. Fill-model breach: If realised fills exceed the modelled band-crossing rate, stop: faster flow is beating the markout model.
5. Daily refresh: Re-read the sheet daily because reward pots and competition move with the calendar.
6. Inventory skew: Once filled on one side, requote to REDUCE the position, never to add; unhedged binary inventory at resolution is a directional bet, not market making.
7. Band discipline: Quote only while the mid is inside [0.10, 0.90]; exit as price leaves the band and do not chase it.
8. Flow toxicity: Do not initiate quotes where toxicity_score > 0.9. This conditions human action only; the registered study charge is unchanged absent a later dated tightening.
9. Resolution risk: Only quote markets with objective, verifiable resolution sources and no open clarifications; exit immediately if a proposal on a held market is disputed.
