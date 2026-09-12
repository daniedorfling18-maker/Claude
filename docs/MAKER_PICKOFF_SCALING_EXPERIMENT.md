# Pre-registered protocol: does maker adverse selection scale with position size?

**Status: WITHDRAWN — NOT ADMISSIBLE, NOT REGISTERED, NOT DISPATCHED.** Written 2026-08-23 at owner
request. Nothing here authorises an order, and Stage 2 below is a human action outside this system's
paper-only governance.

> **Do not build from this document.** An independent S8 admission gate failed it on **seven of ten
> rules (A1, A2, A5, A6, A8, A9, A10)**, and the estimator below was subsequently shown to be
> incapable of measuring what it claims: our quote size enters the replay at exactly one site, the
> `min(quote_size_shares, fillable)` cap at `maker_fill_replay.py:1960`, so the ratio is constant in
> size when nothing caps and falls purely in proportion to capping when it does. On the delivered
> corpus the 1x adverse is exactly $0.00, so the pre-registered denominator vanishes. **The
> authoritative record is the dated result "the maker scaling axis is not observationally
> identifiable" (2026-09-03) in `docs/POLYMARKET_QUANT_MODE_CHARTER.md`.** This file is retained only
> as the artifact that record refers to.

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

Raise book snapshots per replay window from 1 toward one per evaluated print.

**Success criterion: `no_contemporaneous_state_rate <= 0.20`.** *Basis (A1):* the registered
`h1_tier0_min_horizon_coverage_ratio: 0.80` in `decision_policy` requires 80% horizon coverage; a
0.80 coverage floor is a 0.20 miss ceiling, so this reuses an existing registered number rather than
inventing one. Current value is 0.775 — roughly a fourfold reduction in miss rate.

Until it holds, no sweep has power: the denominator of every pick-off rate is unobserved. Cost is
collection frequency only; no capital, no orders.

### Stage 1 — offline size sweep (FREE, decisive if Stage 0 succeeds)

Run the Tier-0 replay at **1x, 5x and 25x** `rewards_min_size_shares`, over identical windows,
changing only `quote_size_shares` in the portfolio contract.

*Basis for the rungs (A1):* the deployed portfolio already runs at `size_multiple: 5`. 1x is the
measurement baseline at which every existing adverse figure was taken; 5x is the size actually
deployed today; 25x is one further geometric step of the same factor. The ladder is anchored on a
delivered value, not chosen for convenience.

**Pre-registered estimator, fixed before any run:**

> For each multiple *k*: `adverse_per_dollar(k) = realized_adverse_usd(k) / capital_deployed(k)`.
> **Quantity of interest: `ratio = adverse_per_dollar(25x) / adverse_per_dollar(1x)`.**
> Linear scaling predicts **1.0**; sub-linear **< 1.0**; super-linear **> 1.0**.
> Bootstrap 95% CI, **2,000 resamples, seed 20260703** — *basis:* both values match the registered
> `edge_strategy_search` settings (`roi_bootstrap_samples: 2000`, `bootstrap_seed: 20260703`), so the
> resampling parameters are inherited rather than invented.

**Sample floor: at least 10 confirmed fills at EACH rung.** *Basis (A1):* the registered
`h1_tier0_min_confirmed_fills: 10` in `decision_policy`. The corpus currently holds 3 fills in total,
so this is roughly a tenfold increase and is the honest price of the answer.

**Clustering:** the bootstrap resamples MARKETS, not windows, because windows within one market are
not independent. With 5 distinct markets the interval will be wide; that is a true statement about
the evidence, not a defect to tune away.

**A2 — what missing, empty, unparseable and non-finite inputs do, in every case fail-closed:**

- `capital_deployed(k) <= 0`, missing, or non-finite -> that rung reports `unmeasurable`; **no ratio
  is computed and none is quoted.** The division is never attempted.
- `realized_adverse_usd(k)` missing, unparseable or non-finite -> the contributing window is
  EXCLUDED from that rung and counted in `windows_excluded_non_finite`; a rung whose exclusions drop
  it below the 10-fill floor reports `insufficient_power`.
- A window lacking contemporaneous book state -> excluded from the denominator, never counted as a
  zero-pickoff observation. **Treating an unobserved window as "no pickoff occurred" is the specific
  fail-open this rule exists to prevent**, and it is how `pickoff_events_per_day: 0.0` arises today.
- The ratio's denominator `adverse_per_dollar(1x) == 0` -> report `undefined_baseline`, not
  infinity, and not a large finite number.

### Stage 2 — live quoting (CAPITAL AT RISK; only if Stage 1 is inconclusive)

Reached only if Stage 1 reports `insufficient_power` after Stage 0 succeeds, or if the replay's
last-in-queue assumption is judged the binding uncertainty. Stage 1 cannot observe **real queue
position** or **competitor reaction to our size** — precisely what the study's honesty clause names
as unverifiable.

Rungs 1x / 5x / 25x of `rewards_min_size_shares`; at the sized market's economics, $94 / $470 /
$2,350 (*A8 derivation:* `capital_usd: 94.0` at 1x, multiplied by the stated rungs).

**Rung scheduling, and the A5 correction.** An earlier draft of this document required randomised or
interleaved rung ordering AND a stop-at-5x rule, which contradict: a stop rule keyed on a completed
5x result cannot be evaluated under an interleaved schedule. Resolved as follows, and the resolution
is registered rather than silently applied:

- **Stage 1 (offline) has no ordering problem at all** — all three rungs run over the *same* recorded
  windows, so market conditions are held constant by construction. The stop rule applies here.
- **Stage 2 (live) uses day-level randomised assignment** of rung to trading day, and its stop rule
  is evaluated on **accumulated** data per rung at each weekly review, never on rung completion
  order. Sequential 1x-then-5x-then-25x is prohibited: it confounds size with any market-wide change
  in flow, unrecoverably after the fact.

**This system places no orders.** Every Stage 2 rung is a human action with human money, outside the
paper-only governance, exactly as the quote sheet already states.

## Fail-safe direction (S5)

**Every ambiguous or unobserved input reduces the measured evidence rather than increasing it.** A
window without book state is excluded, never scored as zero pickoff. A rung below the fill floor
reports `insufficient_power` and quotes no ratio, not even directionally. A zero or missing
denominator reports `unmeasurable` or `undefined_baseline`, never a number. **The only way this
protocol can produce a favourable ratio is from windows where a pickoff could actually have been
observed** — which is the opposite of how `pickoff_events_per_day: 0.0` was produced.

## Kill conditions, pre-committed

1. **Stop the rung** if realised fills exceed the modelled band-crossing rate — the quote sheet's
   existing standing rule 4, not a new one.
2. **Stop the experiment** if `adverse_per_dollar(5x) / adverse_per_dollar(1x) > 1.0` with a 95% CI
   excluding 1.0. Super-linear scaling at 5x settles the question and 25x is not worth running at
   any capital. In Stage 1 this is evaluated once, all rungs sharing windows; in Stage 2 it is
   evaluated on accumulated per-rung data at each weekly review.
3. **Report `insufficient_power`**, quoting no ratio, whenever any rung holds fewer than 10 confirmed
   fills.
4. **Never size up to clear the payout floor as an experimental objective.** The floor makes minimum
   size unpayable in 39 of 41 markets — a separate registered finding — and conflating "size up to
   get paid" with "size up to measure pickoff" makes the result uninterpretable.

## Touch ONLY these files (A10; `git diff --stat` must show exactly these three)

- `src/polymarket_predictive_engine/maker_fill_replay.py` — a size-sweep entry point that runs the
  existing replay against N synthetic portfolio contracts differing only in `quote_size_shares`, plus
  the `unmeasurable` / `insufficient_power` / `undefined_baseline` statuses above. **A9 — callers of
  the changed surface, enumerated:** `cli.py` (the `maker-fill-replay` command) and
  `scripts/run_vps_ops_scheduler.sh` (the scheduled job). The existing single-portfolio path is
  unchanged; the sweep is a new entry point, so neither caller changes behaviour.
- `polymarket_predictive_config.example.yaml` — the sweep's rung multiples and fill floor as
  configuration, defaulting to the registered values above.
- `tests/polymarket_predictive_engine/test_maker_fill_replay.py` — the tests below.

## Enumerated offline tests, with hand-computed expectations (A10/S4)

1. `test_the_sweep_runs_every_rung_over_identical_windows` — three rungs against one recorded window
   set; assert each rung reports the same `windows_evaluated`, proving conditions are held constant.
2. `test_adverse_per_dollar_is_adverse_over_capital` — a rung with `realized_adverse_usd = 5.00` and
   `capital_deployed = 500.0` reports **exactly 0.01**.
3. `test_the_ratio_is_25x_over_1x` — `adverse_per_dollar(25x) = 0.02`, `adverse_per_dollar(1x) = 0.01`
   -> ratio **exactly 2.0**, and the run is flagged super-linear.
4. `test_a_zero_capital_rung_reports_unmeasurable` — `capital_deployed = 0.0` yields status
   `unmeasurable`, and **no ratio key is present in the output at all**.
5. `test_a_non_finite_adverse_window_is_excluded_not_zeroed` — one window with `adverse = nan` among
   twelve valid ones: `windows_excluded_non_finite == 1`, and the rung's mean is computed over the
   remaining eleven, **not** over twelve with a zero substituted.
6. `test_a_window_without_book_state_is_excluded_from_the_denominator` — a window with no
   contemporaneous book state is not counted as a zero-pickoff observation; `windows_evaluated`
   excludes it.
7. `test_a_rung_below_the_fill_floor_reports_insufficient_power` — 9 confirmed fills against the
   floor of 10 yields `insufficient_power` and **no ratio**, proving the floor is exclusive of 9 and
   inclusive of 10.
8. `test_a_zero_baseline_reports_undefined_baseline_not_infinity` — `adverse_per_dollar(1x) == 0.0`
   yields `undefined_baseline`; the output contains no `inf` and no large finite stand-in.
9. `test_the_bootstrap_resamples_markets_not_windows` — a corpus of 2 markets with 50 windows each
   yields a CI strictly wider than the same corpus resampled by window, proving clustering is applied.
10. `test_the_existing_single_portfolio_replay_is_byte_identical` — the pre-existing
    `maker_fill_replay` output is unchanged by the presence of the sweep entry point.

Each test must be confirmed to FAIL with its own guard reverted.

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

## Day-after check (A10/S6)

After the first Stage 1 run: `maker_fill_replay.json` carries a `size_sweep` block with three rungs,
each reporting either a finite `adverse_per_dollar` with `confirmed_fills >= 10`, or an explicit
`insufficient_power` / `unmeasurable` status. **No rung reports a ratio while below the fill floor,
and `windows_excluded_non_finite` is present and non-null on every rung.** If Stage 0 has not yet
landed, every rung is expected to read `insufficient_power` — and that is the correct output, not a
failure of the run.
