# Polymarket Edge Strategy Reset — 2026-07-03

An honest strategic audit, written after two days of forward evidence and a full audit of the live
VPS system. Conclusion up front: **the machine is sound; the targets are wrong.** No gate gets
loosened. The research program gets re-aimed at markets where a small quant operation can
plausibly have edge, and away from markets where it demonstrably cannot.

## What the evidence actually says (all numbers verified on the live system, 2026-07-03)

| Evidence stream | Result |
|---|---|
| Audited paper P&L since baseline (38.9h) | **+$0.03** on $15 audited stake |
| Raw ledger P&L (quote-conflicted, 5 conflicted round trips) | +$55.27 — not real |
| Shadow cohorts, crypto up/down (all intervals) | negative everywhere (e.g. eth_updown_event −$1.86 shadow) |
| Algo sweep, 119,855 events, 9 combos | best combo **failed out-of-sample** |
| Replay, tight-spread join-bid | **−$8.55 on $30** cost |
| Microstructure/price-action model | fail-closed: positives don't transfer across cohorts |
| "Edge route" dashboard figures ($1,973–$3,443/mo) | annualised 4-minute windows; statistical mirage |

Meanwhile the two mechanisms with the strongest prior of real edge are **dormant**:

| Edge source | Status on the live system |
|---|---|
| Sharp-anchor divergence (de-vigged Pinnacle/Betfair vs Polymarket) | `missing_api_key` — has never run |
| Dutch-book / negative-risk arb scanner (`dutch_arb_monitor`) | CLI-only, not wired into any loop or dashboard |
| Broad discovery (sports/politics/macro/esports) | `broad_discovery_enabled: false`; adaptive queries collapsed to "eth/btc/xrp/solana updown" |

## Why the current targets cannot work

Short-horizon crypto up/down markets are priced continuously off the same Binance feed the bot
reads, by participants with better latency. A $2 retail-style taker order buying a 0.87–0.96
favourite needs a win rate the market has already priced in, minus spread, minus adverse
selection. There is no plausible mechanism by which this engine wins there, and every one of its
own evidence streams agrees. Continuing to concentrate collection, modelling, and paper probes on
these families is the definition of going nowhere — the user is right.

The failure was a feedback loop: liquidity-weighted discovery found updown markets (always liquid,
always open), fast feedback produced analogue "positives", research focus injected more updown
queries, and breadth collapsed. AGENTS.md explicitly warns against exactly this.

## Where edge plausibly exists for this operation

Ranked by prior probability of real, harvestable edge:

1. **Cross-book fair-value divergence (sports, then politics).** De-vig sharp books
   (Pinnacle/Betfair) and buy Polymarket when its price is materially past the no-vig fair. This
   is the only retail-accessible strategy class with a long documented track record, and the repo
   already implements the whole pipeline (`sharp_odds_fetch` -> `sharp_anchor` ->
   `fundamental_probability_paths` -> alpha cross-check). **It has never run for want of an API
   key.** Slow-settling positions are fine: CLV (already built) measures the edge within days.
2. **Dutch-book / negative-risk arbitrage.** Sum of asks across a full outcome set < $1 (or
   NO-side equivalents) is mechanical profit; capacity is small but the evidence is unambiguous
   and it exercises the entire pipeline with essentially no model risk. The monitor exists.
3. **Structural longshot bias on slow markets.** Selling-side analogue: prediction markets
   systematically overprice tails. Research (shadow-only) the buy-cheap-NO family on
   liquid, multi-week policy/macro/sports markets, validated through CLV — not settlement-waiting.
4. **Event/roster news latency in niche sports & esports** — later, only after 1–3 produce
   evidence.

Crypto up/down keeps exactly one role: a fast **timing diagnostic** for infrastructure, never a
candidate family. Its share of collection attention is capped (WO-26).

## Changes landed with this reset (2026-07-03)

1. **Entry-price band in `risk_decision`** (`risk.minimum_entry_price` 0.05 /
   `risk.maximum_entry_price` 0.90, base-config only — override profiles cannot widen it).
   Ends the buy-0.95-favourites probe pathology at the risk layer. Strictly tightening.
2. This document, the charter update, and work orders **WO-24..WO-27** (see
   `docs/POLYMARKET_CODEX_WORK_ORDERS.md`): sharp-anchor activation and broadening, dutch-arb
   wiring, an anti-concentration guard on adaptive queries, and the longshot-bias research family.
3. **WO-24 code landed after the reset:** the sharp-anchor fetch is now budgeted, broadened to
   match-market sports, validates provider sports before spending odds calls, skips unsupported
   sports, and maps clear h2h YES contracts without guessing unmapped outcomes. The only remaining
   sharp-anchor blocker is runtime secret injection on the VPS.

## The one action only a human can take

**Set `THE_ODDS_API_KEY` on the VPS** (free tier: the-odds-api.com; 500 credits/month suffices for
a few sports at sane cadence) and restart the loop container. Without it, the highest-probability
edge path stays dead no matter what any agent codes. Everything else in this reset is automated.

## What "seeing potential profits" means honestly

With this reset, the leading indicators to watch (in order, each visible on the dashboard):

```text
1. dutch-arb monitor: any persistent >0 annualised basket    (days)
2. sharp-anchor coverage: markets with a no-vig fair         (days)
3. CLV: positive final-line cohorts among anchor-driven bets (1-2 weeks)
4. audited paper P&L on promoted anchor cohorts              (2-4 weeks)
```

If after ~3 weeks of the re-aimed program none of streams 1–3 shows life, the honest conclusion
will be that this venue offers no harvestable edge at this scale — and knowing that cheaply is
itself the system working, not failing. What the system will never do is manufacture the
appearance of profit by loosening its own gates.
