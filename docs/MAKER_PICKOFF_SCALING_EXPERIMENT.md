# Pre-registered protocol: does maker adverse selection scale with position size?

**Status: DRAFT PROTOCOL — NOT REGISTERED, NOT DISPATCHED.** Written 2026-08-23 at owner request.
Registration is a separate owner decision. Nothing here authorises an order, and Stage 2 below is a
human action outside this system's paper-only governance.

**Pre-registration is the point.** The estimator, the sample floor and the kill conditions are fixed
in this document BEFORE any sweep is run. H4 died because a best-of-39 selection was presented
without its selection breadth and with no estimator named in advance. This document exists so that
cannot recur here.

## The question

Every figure in the maker business case is GROSS. Whether the case is ~12-15%/year or deeply
negative turns on one unmeasured quantity: **how adverse selection scales with resting position
size.** Current state of that measurement:

| evidence | value |
|---|---|
| `pickoff_events_per_day` on the sized market | **0.0** — at one-fifth of deployed size |
| `confirmed_fills` (whole replay corpus) | **3** |
| `simulated_fill_opportunities` | 22 |
| `no_contemporaneous_state_rate` | **0.775** |
| `simulation_to_reality_haircut` | **None** — never computable |
| gate M-B | `pending`, `mb1_tier0_coverage_sufficient: false` |

**If adverse selection is linear in size, the business case is negative at every capital rung. If it
is strongly sub-linear, the case stands. Nothing in the repository distinguishes these.**

## What already exists — the experiment is cheaper than it looks

`maker_replay_collection_windows.csv` holds **200 recorded windows, all `covered=True`, all
`book_poll_status=ok`, 92,566 trade prints across 5 markets** — and it has ALREADY recorded four
distinct quote sizes:

```
quote_size_shares:  250 (x21)   500 (x25)   600 (x104)   1000 (x50)
```

That is a 4x size spread already on disk. And `maker_fill_replay.py:1960` computes
`fill_size = min(quote_size_shares, fillable)` from a portfolio contract
(`_PORTFOLIO_CONTRACT_FIELDS`), so **size is already a parameter**. A sweep needs no new collection
and no capital: run the replay three times against synthetic portfolios differing only in
`quote_size_shares`.

**The binding constraint is not capital and not the harness. It is book density:
`book_snapshot_rows` is `mean 1.00, max 1` per window** — one book state per window against a mean
of 462 trade prints. That is why 77.5% of fill opportunities cannot be evaluated and why only 3
fills are confirmed.

## Stages, cheapest first

### Stage 0 — book snapshot density (FREE, prerequisite)

Raise book snapshots per replay window from 1 toward one-per-evaluated-print. **Success criterion,
fixed now: `no_contemporaneous_state_rate` below 0.25.** Until that holds, no size sweep has power,
because the denominator of every pick-off rate is unobserved. Cost: collection frequency only.

### Stage 1 — offline size sweep (FREE, decisive if Stage 0 succeeds)

Run the Tier-0 replay at **1x, 5x and 25x** `rewards_min_size_shares` per market, over identical
windows, changing nothing else.

**Pre-registered estimator, named before any run:**

> For each size multiple *k*, `adverse_per_dollar(k) = realized_adverse_usd(k) / capital_deployed(k)`,
> where `realized_adverse_usd` is the existing markout charge over confirmed fills.
> **The quantity of interest is the ratio `adverse_per_dollar(25x) / adverse_per_dollar(1x)`.**
> Linear scaling predicts **1.0**. Sub-linear predicts **< 1.0**. Super-linear predicts **> 1.0**.
> Reported with a bootstrap 95% CI over windows, 2,000 resamples, seed 20260703 (matching
> `edge_strategy_search`'s registered seed).

**Sample floor, fixed now: at least 30 confirmed fills at each of the three rungs.** Below that the
run reports `insufficient_power` and NO ratio is quoted — not even directionally. Current corpus has
3 fills total, so this floor is roughly a 30x increase and is the honest cost of the answer.

**Clustering:** windows within one market are not independent. The bootstrap resamples MARKETS, not
windows. With 5 distinct markets in the current corpus, the CI will be wide; that is a true
statement about the evidence, not a defect to tune away.

### Stage 2 — live quoting (CAPITAL AT RISK; only if Stage 1 is inconclusive)

Only reached if Stage 1 returns `insufficient_power` after Stage 0 succeeds, or if the offline
replay's last-in-queue assumption is judged the binding uncertainty. Stage 1 cannot observe two
things: **real queue position** and **competitor reaction to our size**. Those are exactly what the
study's honesty clause names as unverifiable.

- Rungs 1x / 5x / 25x of `rewards_min_size_shares`; at the sized market's economics, ~$94 / $470 /
  $2,350.
- **Order of rungs must be randomised or interleaved by day, not run sequentially.** A sequential
  1x-then-5x-then-25x schedule confounds size with any market-wide change in flow over the period,
  and that confound is unrecoverable after the fact.
- Same estimator, same sample floor, same clustering as Stage 1.
- **This system places no orders. Every rung is a human action with human money, outside the
  paper-only governance, exactly as the quote sheet already states.**

## Kill conditions, pre-committed

1. **Stop the rung** if realised fills exceed the modelled band-crossing rate — this is the quote
   sheet's existing standing rule 4, not a new one.
2. **Stop the experiment** if `adverse_per_dollar(5x) / adverse_per_dollar(1x)` already exceeds 1.0
   with a CI excluding 1.0. Super-linear scaling at 5x settles the question; 25x is then not worth
   running at any capital.
3. **Stop and report `insufficient_power`** rather than quoting a ratio, whenever any rung has fewer
   than 30 confirmed fills.
4. **Never size up to clear the payout floor as an experimental objective.** The floor makes minimum
   size unpayable in 39 of 41 markets, which is a separate registered finding; conflating "size up
   to get paid" with "size up to measure pick-off" would make the result uninterpretable.

## What would falsify the business case

`adverse_per_dollar(25x) / adverse_per_dollar(1x) >= 1.0` with a CI excluding values below 1.0. At
that point adverse selection scales at least linearly, the measured minimum-size charge of
$543.98/day across 34 eligible markets already exceeds gross at 5% share, and no capital rung in the
business case is positive. **That outcome closes the maker lane on measurement rather than on
assumption — which is the entire purpose of running this.**

## What it does NOT establish

A sub-linear result does not establish profitability. It removes the dominant unknown from a gross
case of ~12-15%/year and leaves competitor reaction, resolution risk, and inventory management
unaddressed. Stage 1 succeeding means the case becomes worth costing properly, not that it is proven.
