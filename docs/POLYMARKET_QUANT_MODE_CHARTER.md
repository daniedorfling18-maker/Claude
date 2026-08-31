# Polymarket Quant Mode Charter

Last updated: 2026-08-22

This is the **orchestration charter** for turning the Polymarket predictive engine into a full quant
trading system. It is written for every coding agent working on this repo — Claude, Codex, or any
other code changer. Read `AGENTS.md` first; this charter adds the quant-mode roadmap on top of it and
never overrides its safety rules.

## TERMINAL VERDICT — the registered evidence clock expired 2026-08-19

**Recorded 2026-08-22.** This section is dated deliberately: the answer below was pre-committed a
month before it was read, and it must survive any single container, agent or session. Nothing in
this section loosens a gate; it closes a question.

### What was registered, and when

On **2026-07-10T21:30:00Z** amendment 7 was registered into `profit_verdict.py`
(`REGISTERED_EXTENSION_PROTOCOL`, tighten-only): the evidence window could extend **exactly once**,
through `extension_window_end_utc = 2026-08-19T23:59:00Z`. Amendment 8 (2026-07-17T04:30:00Z) closed
the pending-on-significance gap, so that at the terminal read **all three gates passing is the only
outcome that is not a NO** — a failing gate, a pending gate, and an unmet unit floor all resolve to
`no_for_tested_edge_classes`.

That instant has passed. Checked against the real clock on 2026-08-22T10:57Z, the engine reports the
terminal regime, and it is not reversible: the single registered extension is spent. The point of
registering it a month in advance was that the answer could not be renegotiated after being seen.
It is not being renegotiated here.

**How this surfaced.** `main` went red on 2026-08-22. Six tests had hard-coded the pre-terminal
verdict string without pinning the clock, so they passed for the whole extension window and failed
together the moment the deadline arrived. The engine did exactly what it was registered to do; the
tests were reading the calendar. Fixed in PR #452, which also added the coverage whose absence let
this land silently — nothing in the suite had ever run the engine under the real clock, so nothing
stated which regime the system was in.

### What the NO is a verdict on

`no_for_tested_edge_classes` — the tested classes, each with the measurement that closed it:

| hypothesis | state | the measurement |
|---|---|---|
| **H1** sharp-anchor maker carry | **insufficient_evidence**, capacity-capped | net carry **+$1.68/day** against a $3.33/day target; **$50.40/month** against $100/month. Adverse selection is **UNMEASURED** — the $63.62 first recorded here is unsupported, and the $0.68 that replaced it rests on **3 confirmed fills** with 77.5% of opportunities lacking book state, which is why M-B is pending. M-A also PENDING. The load-bearing fact is capacity: the capital curve is flat from $500 to $2,000 at one market and $470 deployed. |
| **H2** dutch-book consistency | dead | **0** flagged in 300 events; maximum executable basket **$0.00**. |
| **H3** smart-flow CLV | **unmeasured** | the wallet axis is the instrument and it is still in PR #451. |
| directional model | refuted | claimed **+0.08337/share**; realised 95% upper bound **+0.01435** across 109 positions. |
| **H4** crypto up/down | dead twice | admission gate NOT ADMISSIBLE; capacity **$4-10 at touch** against 3,127 instances/day. |

Market calibration is also unmeasured.

**PROVENANCE, and the correction it should have forced sooner**: these figures were recorded as a
reading of VPS telemetry during the 2026-08 review. No Polymarket output artifact is committed to
this repository (`git ls-files outputs` returns nothing under any Polymarket path) — but the
`vps-telemetry` MIRROR BRANCH does carry a periodic snapshot, expressly so that sessions without VPS
network access can read live state, and consulting it is what exposed the H1 figures above as wrong
by two orders of magnitude. **Any figure in this section that has not been checked against
`origin/vps-telemetry` should be treated as unverified.** The
registered protocol, its dates and the terminal regime ARE verifiable here, in `profit_verdict.py`
and its tests.

**CONFIRMED 2026-08-23 — the verdict has printed, and it is no longer an expectation.** Read from
`origin/vps-telemetry`, snapshot `2026-08-21T02:00:09Z`, artifact
`outputs/polymarket_model_governance/profit_verdict.json`, generated `2026-08-21T01:59:04Z` — after
the terminal instant:

```json
"verdict": "no_for_tested_edge_classes",
"extension_resolution": { "regime": "terminal", "terminal_no_applied": true,
                          "terminal_read_due_utc": "2026-08-19T23:59:00Z" }
```

Gate A **FAILED** on its own measurement: 55 independent settled market units, equal-weight unit
mean net settlement return per dollar **-0.013943**, one-sided sign-test p **0.947605** against
alpha 0.10 — "the settled positions were not profitable on average". Gates B and C are
`not_evaluated` by construction, each being reachable only after the one before it passes. An
earlier version of this paragraph said the terminal read still needed confirming on the VPS; it did
not, because the mirror branch already carried it.

**H4 deserves its own line, because it was an agent's own claim.** An earlier version of the
research plan asserted crypto up/down cleared the 4c/share bar on n=9 with a line-movement lower
bound of +7.6c. A falsifier was registered against it and the gate executed it: a silently dropped
-$10 loss; `line_movement` PINS TO SETTLEMENT on up/down markets, making the "skill" component an
accounting artifact; the wrong variance and the wrong decision rate in the power calculation; and no
estimator named in advance. The cohort had also been described to the gate as best-of-22 when it was
best-of-39. Capacity then killed what was left. The falsifier working is the one thing on this board
that went right.

### H1 / the maker lane — CORRECTED 2026-08-23 against live telemetry

**The first version of this section was wrong, and wrong in the direction that made the case for
stopping sound stronger than the evidence supports.** It asserted gross **$3.02/day** against
adverse selection **$63.62/day** — a factor of 21 — and a net of **-$60.60/day**, and it called the
maker lane the most conclusively dead of the tested classes. It also flagged, correctly, that no
output artifact is committed to this repository and that the figures could not be re-derived here.

They have now been checked, and they do not survive. The `vps-telemetry` mirror branch — which
exists precisely so that remote sessions with no VPS network access can read live state — carries
the snapshot pushed at **2026-08-21T02:00:09Z**. Against that snapshot:

| quantity | asserted | measured |
|---|---|---|
| portfolio net carry | **-$60.60/day** | **+$1.68/day** (`portfolio_net_carry_usd_per_day`) |
| adverse selection | **$63.62/day** | **at most $0.68/day** anywhere in the entire snapshot |
| maker verdict | "dead" | **`insufficient_evidence`** (M-A pending, M-B pending, M-C pass) |

Every adverse-selection field in the whole telemetry tree reads `0.0`, `0.004`, `0.236902`,
`0.524231` or `0.682944` per day. The $63.62 figure appears nowhere. The lane is **not** losing
money on its own measurement; it is earning a little and falling short of its target.

**What is actually true of the maker lane, from the artifact:**

- Sized portfolio net carry **$1.68/day** against a registered target of **$3.33/day**;
  **$50.40/month** against the `$100/month` goal, with `clears_100_per_month_target: false`.
- **One** market in the portfolio (`mojtaba-khamenei-seen-in-public-by-december-31`), **$470**
  deployed of a $500 cap, `markout_measured: true`.
- **M-A carry evidence: PENDING** — 8 runs at or above target against 7 required, but
  `latest_run_at_target: false`, and the registered rule requires the latest run to count. The lane
  oscillates around its target rather than clearing it.
- **M-B adverse realism: PENDING** — `mb1_tier0_coverage_sufficient: false`. The adverse charge is
  NOT yet measured to the registered standard on every portfolio market, so the true pick-off cost
  may be higher than the observed $0.68/day. Higher, however, is not $63.62.
- The study's own honesty clause: net carry is an **UPPER BOUND** on the reward-share side and an
  approximation on the pick-off side.

**THE ARGUMENT THAT SURVIVES IS CAPACITY, NOT LOSS.** The registered capital curve is flat:

| capital cap | capital used | markets | net/day |
|---|---|---|---|
| $250 | $0 | 0 | $0.00 |
| $500 | $470 | 1 | $1.68 |
| $1,000 | $470 | 1 | $1.68 |
| $2,000 | $470 | 1 | $1.68 |

**More capital buys nothing.** The lane saturates at one market and $470 deployed, so its ceiling is
roughly **$50/month measured and $100/month at its own target**, at any capital level. That is the
honest reason the maker lane does not carry a funding case — not that it bleeds, but that it cannot
absorb money. No fix to measurement coverage, veto quality or shortlist breadth changes a ceiling
set by how much reward-share capacity exists in an 80-market rewarded universe.

**Process note, recorded because it is the same failure this document catalogues elsewhere.** The
erroneous figures were carried forward from a prior session's analysis and repeated here as fact by
an agent that could not re-derive them and said so in the same breath — then recommended acting
before checking. The check took minutes once the telemetry mirror was consulted. An assertion that
cannot be re-derived from a named artifact is not evidence, and that rule applies to this document's
own authors.

### Tests run on the gathered data, 2026-08-23 — the model's claims carry no information

The board had been assembled from summary FIELDS. The position-level artifacts are complete in the
mirror (not truncated: 109 rows, under the 200-row tail), so the obvious tests were finally run
rather than quoted. `edge_attribution_positions.csv`, using the identity the artifact itself
publishes — `exit - entry_fill == settlement_surprise + line_movement - execution_cost`, per share.

**1. What the model claims, against what happened.**

| quantity | n | mean | 95% CI |
|---|---|---|---|
| `model_claimed_edge_per_share` | 98 | **+0.08371** | [+0.07483, +0.09258] |
| realised edge per share (identity) | 109 | **-0.02660** | [-0.06898, +0.01577] |
| `line_movement_per_share` | 109 | -0.00020 | [-0.06875, +0.06836] |
| `settlement_surprise_per_share` | 109 | -0.02916 | [-0.10167, +0.04336] |

The model claims **+8.4 cents per share with a confidently positive interval**. Realised is
**-2.7 cents**, indistinguishable from zero and pointing the wrong way. This independently reproduces
Gate A's conclusion by a different route — Gate A clustered into 55 units and got -0.013943 at
p=0.9476; this is the per-share identity on the raw positions.

**2. Does a bigger claimed edge predict a better outcome? No.**

```
corr(claimed, realised) = +0.0738      bootstrap 95% CI [-0.1789, +0.3514]
```

Zero is comfortably inside the interval. The tercile split is not even monotonic:

| claimed tercile | n | mean claimed | mean realised |
|---|---|---|---|
| low | 32 | +0.0395 | **+0.0177** |
| mid | 32 | +0.0887 | **-0.1403** |
| high | 34 | +0.1206 | **+0.0494** |

The middle tercile is the worst of the three. **The model's edge estimate carries no usable
information about the outcome.** That is a stronger and more specific statement than "no edge was
found": the estimator itself is uninformative, so collecting more decisions from the same estimator
does not converge on anything.

**3. Every cohort searched, and none survives.** Six cohorts have n>=5:

| cohort | n | mean | 95% CI | |
|---|---|---|---|---|
| crypto | 21 | +0.0504 | [-0.0075, +0.1083] | spans zero |
| worldcup | 7 | -0.1591 | [-0.3496, +0.0313] | spans zero |
| unknown | 7 | -0.2821 | [-0.4318, -0.1325] | **negative** |
| ai_model_leader | 6 | -0.0233 | [-0.0420, -0.0046] | **negative** |
| near_miss_learning\|worldcup | 5 | +0.1811 | [-0.2281, +0.5903] | spans zero |
| structural\|longshot_no\|ai_model_leader | 5 | -0.0924 | [-0.2382, +0.0534] | spans zero |

**Cohorts with an interval strictly above zero: ZERO.** Two are significantly negative. The best,
`crypto`, spans zero before any correction for having run six tests.

**Caveat, stated because it cuts one way only:** positions can share markets, and this per-share
test does not cluster. Gate A found 55 independent units behind 70 finals, so effective n is roughly
half and the true intervals are WIDER than shown. Since every point estimate is already at or below
zero, clustering cannot rescue a positive result — it can only move these further from significance.

**What this changes.** The directional lane is not "insufficient evidence pending more data". It is a
measured absence of skill in the estimator, on the data already gathered. The power ladder asks how
many more decisions are needed to detect an edge of size X; this asks whether the thing generating
the decisions knows anything, and the answer is no. That question was answerable at any point in the
last month from artifacts already in the repository.

### Was there ever a maker profit? No — and the projection is not evidence of one

Recorded because it is the question any later reader will ask of a lane described as
"+$1.68/day", and because the honest answer needs all four facts together.

**1. Realised: zero, over forty days.** `maker_live_test_history.csv`, 3,290 observations from
2026-07-12 to 2026-08-21:

```
rewards_usd_total     min/max =  0.0    /  0.0
inventory_pnl_usd     min/max = -0.0067 /  0.0
net_score_usd         min/max = -0.0067 /  0.0
```

Total rewards ever earned: **$0.00**. Best P&L ever recorded: **$0.00**. Worst: minus two thirds of
one cent. `maker_live_test.json` describes itself as a "READ-ONLY scoreboard for a human-run
experiment. This system never places orders; it only watches the wallet the human chose to trade
from." **CAVEAT, stated rather than glossed:** it is not establishable from telemetry whether the
operator ever placed the quotes the sheet recommended. If they did not, $0.00 is an untested model
rather than a refuted one. It is not, either way, evidence of foregone profit.

**2. The "+$1.68/day net" charges NOTHING for adverse selection.** From `maker_fill_replay.json`:

```
study_adverse_usd_per_day:             0.0        <- what the $1.68 subtracts
simulated_adverse_charge_usd_per_day:  0.0
realized_adverse_usd_per_day:          0.682944   <- what was actually observed
```

It is a GROSS figure wearing the word "net". Subtracting even the tiny three-fill realised charge
takes it to roughly **$1.00/day**, about $30/month.

**3. The realism correction was never computed.** `simulation_to_reality_haircut: None`,
`realism_ratio: None`, `realism_ratio_by_source: {"archive": "insufficient_coverage", "official": null}`.
The artifact's own note states the stake: "A numeric haircut above 1 means the maker-carry study may
undercharge adverse selection." The haircut is absent, so the size of the undercharge is unknown —
only its direction, and its direction is against the projection.

**4. The fill assumption that generates the reward share is 86% unconfirmed.**
`confirmed_fill_ratio: 0.136364` — 3 confirmed of 22 opportunities, `simulated_fills_per_day: 0.281946`.

**Therefore:** the projection does not say a profit was available and missed. It says that under a
model which assumes fills land as simulated and adverse selection is free, one market yields $1.68/day
against a flat capital curve capping the lane near $50/month. Every term in that sentence is either
an upper bound, unconfirmed, or uncharged. The registered maker verdict — `insufficient_evidence`
with M-A and M-B both pending — is the correct description of this lane, and it always was.

**Scoreboard labelling defect, found while checking the above and unrelated to the research
question.** Of the 3,290 scoreboard observations, **1,068 read `winning_so_far`** on a lane whose
`net_score_usd` never once exceeded 0.0. The registered scoring rule requires the score "held
positive across a week"; the classifier evidently treats zero as positive. A dashboard reading
"winning" for a third of its life, on zero fills and zero rewards, is a live reporting defect that
survives any decision about the research surface.

### Second pass, same day — the remaining figures, and a correction to the correction

**`$0.68/day adverse selection` was itself over-confident, and is withdrawn as a measurement.**
`maker_fill_replay.json` publishes `implied_adverse_usd_per_day: 0.682944`, which is where that
number came from — but the same artifact says what stands behind it:

```
confirmed_fills:                          3
last_in_queue_evaluable_opportunities:   22
no_contemporaneous_state_opportunities: 316
no_contemporaneous_state_rate:      0.77451
```

**Three confirmed fills.** 77.5% of opportunities have no contemporaneous book state at all. That is
not a small adverse-selection charge; it is an ABSENT one, and it is precisely why gate M-B reads
`pending` with `mb1_tier0_coverage_sufficient: false`. The honest statement is that the maker lane's
COST side is unmeasured — neither the $63.62 that was asserted nor the $0.68 that replaced it is
supported evidence. The revenue side rests on a single market. **Capacity remains the only
load-bearing fact about this lane, because it is the only one derived from a full population rather
than from a handful of fills.**

**H3 is unmeasured for a specific, findable reason.** `smart_flow_clv.json` reports `fills_seen: 0`,
`fills_scored: 0`, `wallets: []`, reading `inputs/polymarket/public_wallet_fills.csv` — and it was
last generated **2026-07-17**, a month before every other artifact in the snapshot. The lane did not
fail to find an edge; its input was never collected and its job stopped running. This is why PR
#451's approach — measuring the wallet axis from the 198,555-fill trade-print corpus instead — is
the right instrument rather than merely a bigger one.

**The "smart wallet" definition verifies exactly, and is worse than the phrase suggests.** The
latest leaderboard snapshot carries 100 rows and 100 distinct wallets, with
`leaderboard_probe_params: {orderBy: "PNL", timePeriod: "ALL", requested_limit: 100, complete: true}`.
"Smart" means **top-100 by lifetime profit**. A new number sharpens the population defect further:
of 80 holder groups observed in the tracked markets, `holder_leaderboard_overlap_count` is **4**.
The wallets actually holding positions in the markets under study barely intersect the list the
study calls smart.

**The resolved-corpus figure was a request size, not a corpus size.** `historical_resolution_summary.json`:
`requested_closed_markets: 1000`, `fetched_markets: 681`, `clean_settlement_markets: 680`. The
recorded "0 / 1000" conflated the two. The substance survives on three independent artifacts —
`clean_settled_joined_rows: 0` against 17,420 rejections, `families_scored: 0`, and a leakage-safe
substrate still at `status: collecting` with `midpoint_only_rows_accepted: 0` — but the phrasing
should be "0 usable joins from a 680-market clean-settled corpus", not "0 / 1000".

**The power ladder cannot be verified from this mirror, and is now marked accordingly.** The push
script tails every CSV to its last 200 rows (`CSV_TAIL_LINES=200`), so full position history is not
present, and the quantities that ARE present are on a different basis from the `sd 0.226` the ladder
uses (per-share dollars vs fractional return). What the available tail supports, recorded as its own
data point rather than as a check on the ladder: the last **200 closed shadow positions** have mean
`return_pct` **-0.1090** with sd **0.4771**, and mean `realised_pnl_usdc` **-$1.09** with sd **$4.77**.
A negative mean on the shadow cohort is consistent with the terminal NO; the ladder's own inputs
remain unverified.

**Standing limitation of this mirror, worth knowing before the next reader trusts a CSV-derived
number from it:** JSON artifacts are complete; CSV artifacts are the last 200 rows only.

### Verification pass, 2026-08-23 — every figure above checked against `origin/vps-telemetry`

After the H1 error was found, the rest of the board was checked the same way rather than assumed
sound. Snapshot `2026-08-21T02:00:09Z`. **The H1 error was ISOLATED, not systemic:** every other
substantive figure either verifies exactly or has moved with time in the expected direction.

| claim as recorded | artifact | result |
|---|---|---|
| H1 net **-$60.60/day**, adverse **$63.62/day** | `maker_carry_study.json` | **WRONG** — +$1.68/day, adverse at most $0.68/day. Corrected above. |
| H2 **0** flagged in **300** events | `implication_scan.json` | **VERIFIED EXACTLY** — `events_scanned: 300`, `flagged_deviations: 0` |
| H2 max executable basket **$0.00** | `event_group_scan.json` | **VERIFIED EXACTLY** — `max_executable_basket_usd: 0.0`, 67 neg-risk groups |
| attribution drops ~**49.5%** of closed positions | `edge_attribution.json` | **VERIFIED, slightly worse** — 117 of 226 skipped = **51.8%** |
| calibration join **0 / 16,910** | `family_calibration_scorecard.json` | **VERIFIED** — now **0 / 17,420**, `families_scored: 0` |
| directional model, **109** positions | `closing_line_value.json` | **VERIFIED** — `positions_scored: 109` of 227 seen; mean final CLV **+0.008424**; **beat_close_rate 0.3945** |
| ~**200,000** attributed fills | `flow_toxicity_summary.json` | **VERIFIED** — `trades_seen: 198,555` |
| decision layer **88** rewarded markets | `maker_carry_study.json` | **MOVED** — now **80**; same order, time-varying |
| markout coverage **176 -> 259** markets | `flow_toxicity_summary.json` | **MOVED SUBSTANTIALLY** — now **530** markets scored |
| H4 capacity **$4-10 at touch** | — | **NOT VERIFIABLE HERE.** No telemetry artifact carries it; it came from a live Gamma read in an earlier session and remains unverified. |

**Two things this pass changes beyond H1.**

1. **`beat_close_rate` is 0.3945.** The scored positions beat the closing line under 40% of the time.
   Note also Gate A's own caveat, recorded in `profit_verdict.json`: `unit_mean_final_clv` and
   `units_beating_close` are one-release compatibility ALIASES for settlement-return fields and are
   "not true pre-event CLV". The lane's headline metric is not measuring what its name says.
2. **Markout coverage is now 530 markets**, not the 176 -> 259 that PR #451's case for the wallet
   axis is built on. That argument — coverage tripled while smart-fill markets moved 16 -> 17 —
   should be re-derived at current coverage before the H3 read is interpreted, not carried forward.

**Standing rule this pass establishes:** every quantitative claim in this document names the artifact
it came from, and any claim that cannot be pointed at a file in `origin/vps-telemetry` is marked
unverified rather than stated flat. The H1 figure survived four months of repetition because nobody,
including its authors, could say which file it came from.

### Why the whole board reads this way — one defect, five times

**Every component was scoped to a different population, and none intersected the one being traded:**

- resolved corpus vs traded markets — **0 / 1000** overlap
- decision layer — **88** rewarded markets
- markout prices — a **110**-token list against **475** traded
- "smart" wallets — the **100** on a public PnL leaderboard
- calibration join — **0 / 16,910**

Five instruments, five universes, no shared sample. That is why every lane read either "no edge" or
"insufficient evidence": mostly the thing being measured was not the thing being traded. **Any future
hypothesis must fix this before anything is measured again**; re-running the same instruments on the
same mismatched populations will reproduce the same answer more slowly.

### What continuing would cost

From measured variance (sd 0.226) and the measured decision rate (2.4-5.0/day):

| edge to detect | decisions needed | calendar time |
|---|---|---|
| 4c/share | 250 | 50-103 days |
| 2c/share | 1,001 | 7-14 months |
| 1c/share | 4,005 | **2.2-4.5 years** |

Nothing measured clears 4c, so continuing means committing to the 2c or 1c row — months to years —
on a system whose own pre-registered deadline has already returned NO.

### What this section does and does not decide

**Decided, and not by any agent:** the registered verdict for the tested edge classes is terminal.
The evidence question was asked properly, with pre-committed gates and a pre-committed deadline, and
it was answered. No agent may extend the window: the single registered extension is spent, and that
was the point of registering it.

**NOT decided here — these are the repo owner's calls alone, and no agent-authored text may stand in
for them:**

1. **STOP** — freeze the research surface, keep the paper system running as a telemetry archive, and
   stop spending on the edge question; or
2. **A DATED ALTERNATIVE** — a *new* registered hypothesis with a *new* pre-committed deadline, with
   the population-scope defect fixed first.

**Close-out state, updated 2026-08-23.** PR #452 landed and is `main`'s tip; `main` is green
(2155 passed, 3 skipped — one further failure is a git-worktree artifact, not a defect: the restore
shell requires `.git` to be a directory and it is a file inside a worktree). PR #451 — the H3
instrument, the only tested class with no measurement at all — remains open after eleven rounds of
external review (134 threads, 132 resolved) with two deliberately unresolved for the owner: that its
registration followed rather than preceded its build, and that its ranking sample erodes under the
rolling 200,000-row ledger. Reading the wallet axis once would complete the board and make the NO
unanimous rather than 4-of-5.

**RECOMMENDATION — AGENT-AUTHORED ADVICE, NOT A DECISION.** The owner asked for a call on
2026-08-23 and this records the answer given. It does not substitute for, pre-empt or stand in for
the owner's choice between (1) and (2) above, and no later reader may treat it as that choice having
been made.

> Take option (1), STOP, and take it now rather than after H3. The registered verdict is terminal and
> the single extension is spent, so H3 cannot reopen this question under the protocol — it can only
> seed a NEW hypothesis with a NEW deadline. Read the wallet axis once anyway, for the completeness
> of the record and as possible seed evidence, but not as an input to the closed question. Keep the
> paper system running as a telemetry archive; freeze the research surface; land fail-open and
> record-integrity fixes only, and no eligibility changes. In particular WO-154 should NOT land under
> a stop: its own registration states it is not tighten-only, that it changes which markets can hold
> a portfolio slot, and that the owner must ratify that framing — a ratification that only carries
> meaning if the lane is being funded.

**What would reopen the maker lane**, stated so the stop is falsifiable rather than a mood: adverse
selection per unit of gross carry moving by an order of magnitude, measured on one population over
one period. That is a new registered hypothesis with a new pre-committed deadline, not a resumption
of this one.

**Unchanged throughout:** paper/dry-run only, no live order path, no gate, threshold or eligibility
loosened, VPS-only runtime, and every owner-routed merge and deploy left to the owner.

## What "full quant mode" means here

A quant trader is not "a bot that trades more". It is a system where every stake is justified by a
measured, out-of-sample, cost-aware edge, sized against explicit risk budgets, and audited after the
fact. Concretely, the target operating loop is:

```text
hypothesis -> point-in-time data -> features -> model -> calibration scorecard
  -> cost-aware expected value -> shadow evidence (settlement + CLV + bid/ask round trips)
  -> governance promotion -> risk-sized paper probes -> post-trade attribution -> repeat
```

The engine already implements most of this loop. Quant mode is about closing the remaining gaps and
raising the evidence standard, **not** about loosening gates to force activity.

## Non-negotiable invariants (verbatim from AGENTS.md — do not weaken)

1. Everything stays **shadow / dry-run / paper-gated by default**. There is no approved live order path.
2. Live trading stays gated four independent ways (kill switch, `trading.mode: live`,
   `POLYMARKET_LIVE_TRADING=1`, human approval file) plus the `LiveExecutor` gates.
3. No label leakage: point-in-time features only; chronological validation; train-only thresholds.
4. Promotion requires **forward shadow evidence**, never in-sample backtest ROI.
5. Do not loosen alpha thresholds, same-category gates, cohort-promotion gates, or family exclusions.
6. New risk/sizing code may only ever make sizing **more** conservative by default.
7. Local-first: plain Python + `pytest`; Docker is deploy-only.

Any work package below that appears to conflict with these invariants loses; the invariants win.

## Assimilation: what already exists (do not rebuild)

| Capability | Where |
|---|---|
| Websocket collection + normalisation (bid/ask/depth/imbalance) | `websocket_collector.py`, `websocket_normaliser.py` |
| Point-in-time features, leakage guards | `features.py`, `features_v2.py`, `FORBIDDEN_FEATURE_FIELDS` |
| Calibrated models, category calibration, skill model | `models/` |
| Brier/log-loss/decomposition vs market baseline, bootstrap CIs | `market_relative_validation.py` |
| Mispricing alpha scoring | `mispricing_alpha.py` |
| Strict executable price-action model (ask-in / bid-out) | `price_action_model.py` |
| Microstructure rule lab (train/validation, shadow-only) | `price_action_microstructure.py` |
| Shadow cohorts + settlement evidence | `shadow_cohort.py` |
| Closing-line-value (CLV) forward evidence | `closing_line.py` (new, WP1) |
| Reconstructed sharp-anchor CLV research (non-verdict) | `reconstructed_signal_clv.py` (WO-55) |
| Martingale drift scan (term-structure research) | `drift_scan_study.py` (WO-43) |
| Pre-trade risk controls + capped/shrunk Kelly sizing | `risk.py` (WP2 adds shrinkage) |
| VaR/CVaR/drawdown/Sharpe primitives | `quant_lab/risk.py` |
| Fail-closed governance, promotion gates, audits | `governance.py`, `readiness.py`, `promotion_review.py` |
| Paper broker + typed ledger + reconciliation | `paper_broker.py`, `portfolio.py` |
| Quant curriculum primitives (8/8 modules) | `src/quant_lab/`, `docs/QUANT_CURRICULUM.md` |

The binding constraint is **not** infrastructure. It is *evidence*: no family has positive
closed/settled forward evidence yet. Quant-mode work must therefore prioritise anything that
increases the rate and quality of forward evidence per day of wall-clock time.

## Work packages

Each work package (WP) lists its interface and acceptance criteria so any coding agent can pick it up
independently. Statuses: `done`, `open`. Work top-to-bottom; the list is priority-ordered.

### WP1 — Closing-line-value (CLV) evidence stream — `done` (2026-07-02)

Settlement is slow; CLV is the canonical settlement-independent edge proxy (did the line move toward
our entry?). Implemented in `closing_line.py`, CLI `closing-line-value`, artifacts
`outputs/polymarket_model_governance/closing_line_value.json` + `closing_line_value_positions.csv`.
Fail-closed evidence classes: `positive/negative/insufficient_clv_evidence` (bootstrap CI on final
pre-close lines only). Diagnostic input to governance review; not an automatic promotion trigger.

### WP2 — Uncertainty-shrunk Kelly sizing — `done` (2026-07-02)

`risk.shrunk_kelly_fraction`: shrinks the model probability toward the market price (no-edge prior)
before Kelly; `risk.kelly_shrinkage` config (default 0.0 = unchanged behaviour). Guaranteed
`<=` plain capped Kelly. Sets up probability-uncertainty-aware sizing for paper probes.

### WP3 — Wire CLV into the shadow research cycle and dashboard — `done` (2026-07-02)

Detailed implementation instructions are written as work orders **WO-1, WO-2, WO-3** in
`docs/POLYMARKET_CODEX_WORK_ORDERS.md` — any coding agent can execute them mechanically.

- WO-1: `done` (2026-07-02) — call `build_closing_line_value` from `refresh_governance()` (covers
  the scheduled cycle with zero PowerShell changes). Artifact:
  `outputs/polymarket_model_governance/closing_line_value.json`; summary also appears in
  `outputs/polymarket_model_governance/governance_refresh.json`.
- WO-2: `done` (2026-07-02) — dashboard CLV section following the `quant_research_status`
  pattern. Artifact appears in `outputs/polymarket_dashboard/dashboard_data.json` under
  `closing_line_value` and renders as "Closing-line value (CLV)" in the dashboard.
- WO-3: `done` (2026-07-02) — CLV block in the local-history audit report (report-only;
  `_paper_decision` untouched). Artifacts:
  `outputs/polymarket_model_governance/local_history_audit_summary.json` and
  `outputs/polymarket_model_governance/local_history_audit_report.md`.
- Acceptance: per work order; `paper_trading_invoked` stays `false`; tests cover the wiring.

### WP4 — CLV-aware promotion review (advisory, fail-closed) — `done` (2026-07-03)

- `promotion_review.py`: add CLV as a *corroborating* signal — a cohort with positive settlement or
  round-trip evidence AND `positive_clv_evidence` ranks above one without; `negative_clv_evidence`
  adds an advisory note. CLV alone must never promote.
- Acceptance: promotion review output includes CLV fields; a cohort with only positive CLV still
  reads `blocked`; tests assert both directions.

Landed: `promotion_review.json` now includes per-row `clv_evidence`, `clv_mean_final`,
`clv_ci_low`, `clv_ci_high`, `clv_final_positions`, plus top-level `clv_source` and
`clv_is_advisory_only`. Positive CLV is only the final ordering tiebreaker; negative CLV is stored
in `advisory_notes` and does not alter mechanical gate fields, status, or promotion booleans.

### WP5 — Execution cost model from order-book depth — `done` (2026-07-02)

Implemented in `execution_costs.py`: expected fill price for a given stake is estimated from
normalised depth fields (`bid_depth_1pct/5pct`, `ask_depth_*`, `top_*_size`, `book_imbalance`)
instead of relying only on the flat `costs.slippage` assumption. The estimator outputs expected
slippage plus max stake at acceptable impact, fails closed when depth is missing, and only lowers
flat slippage when the book is demonstrably deep enough.

Consumers: shadow entry fill price (`shadow_cohort._shadow_slippage`), `risk_decision` stake cap,
strategy slippage checks, and EV in `mispricing_alpha`. `models/calibrated.py` and `strategy.py`
preserve bid/ask/depth fields into predictions and signals, and `mispricing_alpha.py` can enrich
stale prediction rows with the latest fresh websocket quote/depth row before scoring.

Acceptance: `tests/polymarket_predictive_engine/test_execution_costs.py`, the depth-risk test in
`test_hardening_controls.py`, prediction handoff coverage in `test_predictive_power_expansion.py`,
and mispricing-alpha depth/enrichment tests.

### WP6 — Portfolio-level correlated exposure from live positions — `done` (2026-07-03)

- `risk_decision` already takes `current_correlated_exposure`, but callers must compute it from open
  positions sharing a `correlation_key` (see `worldcup_validation.normalised_correlation_key`).
  Audit the paper/shadow callers, close any gaps, and add a portfolio-level VaR snapshot using
  `quant_lab.risk` over open-position marks.
- Acceptance: risk state artifact reports correlated exposure by key and portfolio VaR; a test shows
  two same-event candidates draining the same correlated budget.
- Status: landed by Codex. `portfolio_state` computes correlated exposure per
  `normalised_correlation_key`; WO-12 adds the remaining risk-state VaR/CVaR and correlated-exposure
  reporting slice, plus dashboard visibility.

### WP7 — Family classifier for liquid `unknown` markets — `done` (2026-07-02)

`worldcup_validation.classify_market_family()` now maps liquid metadata-only `unknown` rows into
research families such as `macro_rates`, `macro_economy`, `equities_macro`, `ai_model_leader`,
`tennis_tennis_winner`, `esports_match`, `policy_legal`, `weather`, `geopolitics`, and crypto
specials. `features_v2`, `mispricing_alpha`, and `strategy_search` consume the shared classifier so
fresh websocket rows and stale prediction rows both stop collapsing into one unusable unknown bucket.

Acceptance: `tests/polymarket_predictive_engine/test_family_classifier.py` plus
`test_worldcup_validation.py` prove Fed/AI/tennis/esports/equities/crypto rows resolve to real
research families. This does **not** loosen promotion: newly classified families still need their own
positive bid/ask, CLV, settlement, and paper evidence before any governed paper sizing.

### WP8 — Edge attribution / post-trade analytics — `done` (2026-07-02, orchestrator)

- Per closed shadow/paper position, decompose realised P&L into: entry edge (model vs market),
  line movement (CLV), spread/slippage cost, and settlement surprise. Aggregate per cohort.
- Acceptance: an `edge_attribution.json` governance artifact; used by research-focus refresh to
  direct collection toward cohorts whose losses are cost-driven vs model-driven.

Landed: `edge_attribution.py`, CLI `edge-attribution`. Exact per-share identity
`exit - entry_fill == settlement_surprise + line_movement - execution_cost`, joined from closed
shadow positions and CLV lines. Cohort classes: `positive_edge_confirmed`, `cost_dominated`,
`model_direction_not_confirmed`, `settlement_adverse`, `mixed_attribution`,
`insufficient_attribution_evidence` — each with a recommended research action. Artifacts:
`outputs/polymarket_model_governance/edge_attribution.json` + `edge_attribution_positions.csv`.
Research-focus consumption is WO-11 (Codex).

## Algo execution compatibility track (WP9–WP11)

Quant research finds the edge; algo execution trades it. To be "algo trading compatible" the engine
needs an event-driven seam — typed orders, pluggable strategies, and a replay backtester — so that
when a cohort finally earns promotion, execution is a policy choice rather than a rewrite. All of it
stays shadow-only by construction: the order schema has **no live mode value at all**, and the
replay harness refuses non-shadow intents.

Detailed specs are work orders **WO-4, WO-5, WO-6** in `docs/POLYMARKET_CODEX_WORK_ORDERS.md`.

Implementation note (2026-07-02): all six work orders landed. One deliberate deviation from the
WO-5 text: paper-mode intents are approved via explicit config (`algo.allow_paper_intents` plus
`algo.paper_approved_strategies`) rather than by reading the promotion-gate artifact inside the
registry wrapper — equally fail-closed, but deterministic and free of I/O in the hot path. The
promotion-gate check belongs to the caller that flips that config, which stays a human decision.
Artifacts: `outputs/polymarket_algo/replay_<strategy>_summary.json` + `_fills.csv`; CLI:
`polymarket-engine algo-replay --strategy null`.

### WP9 — Typed order-intent schema — `done` (2026-07-02, WO-4)

One validated `OrderIntent` dataclass between "strategy wants to trade" and "broker executes":
side/quantity/limit price, time-in-force (IOC/GTD), execution policy (cross spread / join bid /
work midpoint), and `mode` restricted to `shadow`/`paper`. Adapters bridge today's
`risk_decision` output and the paper broker's signal rows without changing either.

### WP10 — Algo strategy protocol + registry — `done` (2026-07-02, WO-5)

`QuoteEvent` (from normalised websocket rows) in, `list[OrderIntent]` out; pure, deterministic,
no I/O inside strategies. Registry enforces intent validity and downgrades anything non-shadow
unless governance has approved the cohort. Ships with a Null strategy and one tight-spread
join-bid shadow probe.

### WP11 — Websocket replay harness — `done` (2026-07-02, WO-6)

Chronological, no-lookahead replay of recorded websocket features through any registered strategy
with conservative fill simulation (cross at ask only when the limit crosses; resting orders fill
only on later crossing quotes; mark to bid). This is the event-driven backtester that closes the
algo loop offline. WP5's depth-based cost model now supplies the cost-aware execution layer used by
alpha scoring, shadow fills, strategy checks, and risk sizing.

### WP12 — Algo parameter sweep lab — `done` (2026-07-02, orchestrator)

`algo/sweep.py`, CLI `algo-sweep`: grids strategy parameters over recorded websocket history
through the replay harness, ranks on the TRAIN window only, then scores the single selected
combination once out-of-sample. Fail-closed decisions: `insufficient_events_for_sweep`,
`no_sweep_candidate_reached_minimum_train_fills`, `sweep_candidate_failed_out_of_sample_validation`,
`sweep_candidate_validated_shadow_only`. A validated candidate is a research lead for more forward
collection — it never promotes, sizes, or trades. Artifacts:
`outputs/polymarket_algo/algo_sweep_summary.json` + `algo_sweep_combos.csv`. Config: `algo_sweep:`.

## Audit log

**2026-07-02 — post-merge audit of Codex's landing on main (orchestrator).** PR #59 merged; Codex
then landed WO-1..WO-6 (content-equivalent to the branch versions, verified by diff) plus WP5
(`execution_costs.py` wired into `risk_decision`, signal edge netting, shadow fills, and an alpha
penalty), WP7 (`classify_market_family` shared across features/alpha/strategy-search), a
prediction-cycle runtime lock, price-action model hardening (anti-chase entry-book features +
per-token dedup of selected candidates, model v5), and an algo-replay dashboard section.
Verified: live/readiness/governance gate files untouched; the execution-cost estimator fails
closed (missing depth -> flat slippage unchanged, below-flat slippage only when top-of-book
demonstrably fills the stake); alpha quote enrichment respects a staleness window and the new
execution-cost term only ever penalises; the classifier is metadata-only and newly named families
start with zero evidence, so promotion stays fail-closed. 663 tests green. Follow-ups raised as
WO-7..WO-9 in the work orders doc.

**2026-07-02 — WO-8 and WO-9 implemented by the orchestrator.** Below-flat execution costs now
additionally require a fresh quote (`quote_age_seconds` <= 120s, falling back to the row's
`websocket_quote_age_seconds`); stale or unknown-age depth never earns a discount. The
quote-enrichment leakage invariant is pinned by a regression test that makes the alpha training
path explode if enrichment is ever wired into it. WO-7 (WP4, CLV-aware promotion review) is the
single open work order and now carries a near-diff-level spec plus an explicit list of wrong
implementations; the work orders doc also gained a pre-flight checklist every agent must run
before pushing.

**2026-07-02 — WP8 and WP12 implemented by the orchestrator (edge-finding machinery).** Edge
attribution decomposes every closed shadow position's P&L into execution cost, line movement, and
settlement surprise (exact identity, tested) and classifies each cohort with a recommended
research action. The algo sweep lab searches strategy parameter grids over recorded websocket
history with train-only selection and out-of-sample confirmation through the replay harness.
Wiring into the cycle/dashboard/audit landed in WO-10; research-focus consumption of attribution + CLV +
sweep decisions is WO-11 — both specced for Codex.

**2026-07-03 — WO-10 landed by Codex.** Governance refresh now rebuilds edge attribution and the
algo sweep after CLV and before downstream governance; the dashboard renders both diagnostic
sections; the local-history audit includes report-only summaries after `_paper_decision` is
computed. No gates, thresholds, broker paths, or live-trading settings were changed.

**2026-07-03 — WO-7 landed by Codex.** Promotion review now consumes CLV as advisory
corroboration only. The tests prove CLV can reorder otherwise-identical rows for human review but
cannot change status, booleans, gate counts, missing mechanical gates, or paper/live permissions.

**2026-07-03 — VPS dashboard audit added WO-20..WO-23.** The live VPS dashboard showed the next
binding constraint: collection was not following open shadow/paper positions, leaving CLV finality,
edge attribution, paper exits, and settlement detection starved of the exact quotes they need.
The work-order queue now starts with position-aware websocket collection (WO-20), then stuck-position
settlement/flagging (WO-21), display fixes for evidence-free extrapolations (WO-22), and
deployment-aware oversight status (WO-23). WO-7 remains landed and advisory-only.

**2026-07-03 — WO-20 landed by Codex.** Websocket target selection now reserves held shadow/paper
position tokens before discovery tokens, reads paper close times from paper-order source payloads
when the positions table lacks them, and writes `selection_reason=open_position` plus
`target_position_counts` for auditability. This directly increases the chance of collecting the
bid/ask lines needed for CLV finality, edge attribution, paper exits, and settlement detection.
Full suite: 684 tests green.

**2026-07-03 — WO-21 landed by Codex.** Crypto up/down proxy settlement now lives in a shared
module and covers encoded fast slugs plus named hourly/daily slugs. The paper broker settles
past-close hourly crypto up/down positions only when public reference prices resolve the window;
otherwise it emits `stale_open_position` evidence and the dashboard/oversight surfaces a bad alert.
No cost-basis force-close path was added. Full suite: 687 tests green.

**2026-07-03 — WO-22 landed by Codex.** Dashboard extrapolations now fail closed: thin
`monthly_run_rate_usdc` rows render as `n/a (N fills, Hh)`, CLV beat-close is `n/a` until final
close lines exist, the zero-fill null replay is no longer presented as a winning algo, and raw
ledger P&L/run-rate tiles carry the audited P&L caveat when quote conflicts exist. The Best-edge
route card and decision summary now show actual evidence — P&L, round trips, and observed time —
instead of annualising micro-windows such as 2 paper round trips over 4 minutes into a monthly
fact. Full suite: 688 tests green.

**2026-07-02 — overnight queue issued.** WO-7 and WO-10..WO-19 are specced in the work orders doc
with a night-shift protocol (fixed order, stop conditions, skip-and-note rules, end-of-night
report). New ground covered by the queue: portfolio VaR reporting (WP6 remainder), microstructure
hypotheses as replay strategies + generalised sweep, per-family calibration scorecard, collection
coverage for CLV finality, evidence history time series, a dashboard evidence funnel, and
invariant property tests that lock the safety envelope. Nothing in the queue can loosen a gate;
WO-19 began as a test-only lock but was allowed to harden source behavior only after the invariant
exposed a conservative execution-cost gap.

## Strategic reset — 2026-07-03

Two days of forward evidence plus a live-system audit produced a verdict: the machinery works,
the targets were wrong. Crypto up/down families show negative evidence on every stream while the
two highest-prior edge sources (sharp-anchor divergence, dutch-book arb) sat dormant and discovery
had collapsed into updown queries. The reset — full reasoning in
`docs/POLYMARKET_EDGE_STRATEGY_RESET.md` — re-aims the research program (WO-24..WO-27), lands an
entry-price band in `risk_decision` (0.05–0.90, base-config only; ends the buy-0.95-favourites
probe pathology), and defines the leading indicators that count as "seeing potential profit"
honestly: arb baskets, anchor coverage, positive CLV cohorts, then audited paper P&L. No gate was
loosened; none will be.

**2026-07-03 — WO-25 landed by Codex.** The mechanical Dutch-book arb monitor is now wired into
the VPS live-paper loop as a bounded dry-run pass with config cadence/size controls. It writes
`outputs/polymarket_arbitrage/dutch_arb_monitor_summary.json`,
`outputs/polymarket_arbitrage/dutch_arb_latest.json`, latest opportunity rows, and append-only
above-alert rows, and it tracks 3+ scan persistence for human review. The dashboard now has a
"Dutch-book arb watch" section plus an info-only oversight alert for persistent baskets. This added
no order placement path.

**2026-07-03 — WO-26 landed by Codex.** Adaptive research-focus collection now has an audited
anti-concentration guard: `research_focus.json` records raw proposed queries, guarded queries,
family counts, rejected-query reasons, broad-base fill rows, and an explicit
collection-only/no-trade-authorisation decision-use label. Defaults cap each family to two queries,
cap crypto up/down to one timing diagnostic, and enforce at least four distinct families using a
deterministic broad base. This pushes discovery back toward sports/macro/esports/AI/politics/stocks
instead of letting weak up/down evidence consume the loop.

**2026-07-03 — WO-27 landed by Codex.** The structural longshot-bias family now runs as a
shadow-only research lane. `longshot_bias.py` and CLI `longshot-bias-scan` scan slow, liquid binary
markets where the YES tail is 2–12c, require a real NO-side token, and nominate
`structural|longshot_no|<family>` candidates for CLV/forward-shadow validation. The canonical
paper cycle forwards those rows only into shadow evidence, not paper signal generation. Artifacts
live under `outputs/polymarket_longshot_bias/`.

**2026-07-03 — WO-28 landed by Codex.** The first smart-flow research lane now scores public
wallet fills by CLV using the same settlement-independent line standard as our own shadow positions.
`smart_flow_clv.py` and CLI `smart-flow-clv` read configured public fills, join them to websocket
quote history, aggregate by wallet with bootstrap CIs, and publish
`smart_flow_clv.json` / `smart_flow_clv_positions.csv` for dashboard review. Positive wallets are
research/watchlist candidates only; the module cannot place orders or relax promotion gates.

**2026-07-10 — WO-55 implemented by Codex.** `reconstructed_signal_clv.py` and CLI
`reconstructed-clv-study` reconstruct historical sharp-anchor entries under the frozen 2026-07-10
entry rules, price them from official CLOB price history, cluster by fixture, and publish
`reconstructed_signal_clv.json` / `reconstructed_signal_clv_positions.csv` with
`evidence_class: reconstructed_research`. This is judgment input only: it never touches
`profit_verdict.py`, never becomes Gate A evidence, and cannot invoke paper/live trading.
**2026-07-11 — VPS telemetry bridge.** `scripts/push_vps_telemetry.sh` (host cron, every 30 min)
force-pushes a single parentless commit of decision summaries — governance/verdict JSONs, maker-carry
study + gates, quote sheet, study outputs, scheduler status — to the `vps-telemetry` branch, so
remote orchestration sessions read live VPS state through the private repo. Zero Actions cost
(operational workflows are dispatch-only; the WO-69 self-hosted gate is PR-only; `[skip ci]` remains
belt-and-braces), zero history growth (branch always holds exactly one
commit), heavy collection corpora (training archive, websocket features, trade prints, official book
snapshots) never leave the VPS.

**2026-07-10 — WO-43 implemented by Codex.** `drift_scan_study.py` and CLI `drift-scan` estimate
martingale drift from harvested CLOB price histories by price bin, time-to-close bin, category, and
horizon. The scan uses market-clustered bootstrap CIs, BH-FDR across tested bins, and only reports a
research flag when drift exceeds the configured taker cost stack. It is study-only: no lane, no gate
change, no paper/live trading.

**2026-07-03 — WO-23 landed by Codex.** The dashboard now distinguishes the VPS deployment driver
from local shadow-cycle observability. When the legacy live-loop heartbeat is fresh and the shadow
research status file is absent, oversight shows the exact info line
"Driver: legacy live loop (VPS deployment); shadow-cycle status file not expected." and no longer
raises a false missing-shadow warning. Strategy V2 also renders "not running in this deployment"
when its artifacts are absent under that fresh VPS live-loop driver.

**2026-07-03 — WO-11 landed by Codex.** Research focus now consumes edge attribution, CLV, and
algo-sweep artifacts as collection-only feedback. Cost-dominated, positive-edge, and positive-CLV
cohorts are raised in collection priority and mapped to family queries; model-direction-not-confirmed
cohorts with negative CLV are lowered without blacklisting. `research_focus.json` records an
`evidence_inputs` block explaining every movement and any validated shadow-only sweep lead, while
leaving promotion gates, thresholds, and trading authorisation untouched.

**2026-07-03 — WO-12 landed by Codex.** Portfolio snapshots now write a report-only
`portfolio_risk` block into `outputs/polymarket_portfolio/risk_state.json`, covering total open cost,
top correlated exposure, category exposure, historical VaR/CVaR over marked open positions, and worst
position return. The dashboard renders the same block as a Portfolio risk panel. This completes the
WP6 reporting slice without changing risk decisions, stake caps, or broker order logic.

**2026-07-03 — WO-13 landed by Codex.** The algo registry now mirrors the microstructure lab as
three executable shadow replay strategies: bid momentum in tight books, midpoint momentum in tight
books, and spread compression with bid-heavy imbalance. They are deterministic, per-replay
stateful only for previous quotes, emit GTD `join_bid` shadow intents, and remain unavailable for
paper/live execution unless separate governance later approves a promotion path.

**2026-07-03 — WO-14 landed by Codex.** The algo sweep now runs generic per-strategy parameter grids,
reports one global selected combo plus `by_strategy` bests, and writes strategy/params into the combos
CSV. Legacy tight-spread sweep behavior is preserved when no `algo_sweep.strategies` block is set.
The dashboard sweep panel now displays selected strategy/params and the per-strategy leaderboard,
making the executable microstructure search visible to the operator.

**2026-07-03 — WO-16 landed by Codex.** The model-governance lane now writes a per-family calibration
scorecard from clean settled rows only. It compares model Brier/log-loss against the market baseline,
uses clustered bootstrap CIs for family-level Brier gain, and fails closed for thin or inconclusive
families. This makes family selection quant-driven: collect/scale only where market-relative skill
transfers, and keep other families in research until evidence improves.

**2026-07-03 — WO-17 landed by Codex.** The evidence lane now writes collection coverage diagnostics:
family-level websocket quote counts/gaps and exact shadow positions missing pre-close quotes. This
converts stale/provisional CLV into a scheduling problem the VPS can solve, rather than a vague model
blocker.

**2026-07-03 — WO-15 landed by Codex.** Governance refresh now appends an idempotent evidence time
series from CLV, edge attribution, and algo-sweep artifacts. This makes the learning loop auditable
across cycles: the operator can see evidence accumulating, stalling, or degrading instead of trusting a
single latest snapshot.

**2026-07-03 — WO-18 landed by Codex.** The dashboard now has a top-level evidence funnel: liquidity
targets, alpha/shadow candidates, shadow position state, final CLV coverage, attribution classes,
family calibration winners, pre-close collection gaps, algo-sweep decision, paper gate, and recent
history. `refresh-governance` also refreshes family calibration and collection coverage before
rendering, keeping the cockpit decision-useful.

**2026-07-03 — WO-19 landed by Codex.** Seeded safety invariant tests now pin Kelly shrinkage,
execution-cost conservatism, the risk decision sizing envelope, and order-intent schema safety.
The first invariant check found that missing depth could look cheaper than known shallow depth, so
execution costs now fail closed when depth is absent and the risk layer treats a zero acceptable
impact cap as binding. This tightens execution safety without adding any paper/live permission.

**2026-07-03 — verified $100/month proof gate landed by Codex.** The profit tracker and goal
planner no longer certify "on pace" from raw account equity or tiny-sample annualised P&L alone.
The `$100/month` state now requires enough audited, quote-consistent paper round trips, no quote
conflicts/unverified exits, and sufficient tracking time before the dashboard can treat the
run-rate as verified progress.

**2026-07-10 — WO-37 landed by Codex.** The wallet-intelligence collection lane now snapshots
data-API leaderboard and holder streams into `outputs/wallet_intelligence/leaderboard_history.csv`
and `outputs/wallet_intelligence/holders_history.csv`, with
`wallet_intelligence_summary.json` reporting tracked markets, wallets seen, and holder/leaderboard
overlap. It is collection-only and cannot touch paper/live execution.

**2026-07-10 — WO-38 landed by Codex.** Event-group deviations now fetch CLOB books only for
already-flagged groups and record executable basket depth in the existing ledger via
`executable_basket_usd`, `depth_weighted_net`, and `book_fetch_ok`. The summary reports executable
depth coverage without changing any gate, threshold, or order path.

**2026-07-10 — WO-40 landed by Codex.** Maker fill realism replay now reconstructs archived/live
websocket book states for the current quote-sheet portfolio, applies last-in-queue fill logic to
trade prints, and writes `outputs/maker_carry/maker_fill_replay.json` with fills/day, horizon
markouts, implied adverse dollars/day, and the replay/study realism ratio. It reports evidence only;
the maker study is not auto-modified.

**2026-07-10 — WO-39 landed by Codex.** Trade-print collection now rides along with open-interest
snapshots from data-API `/oi`, appending `outputs/polymarket_trade_prints/open_interest_history.csv`
and surfacing `oi_markets_captured` plus fail-soft `oi_errors` in the summary. Missing OI endpoints
do not fail the print job by themselves.

**2026-07-10 — WO-41 implemented by Codex.** The implication-network scanner now measures
Frechet/Boole consistency across linked World Cup-style markets: monotone winner/final/semifinal
chains, continent-winner aggregation, and exact-final-matchup sums. CLI `scan-implication-networks`
writes `outputs/implication_consistency/implication_deviations.csv` and
`outputs/implication_consistency/implication_scan.json`, rides the trade-print cadence, and remains
measurement-only with no signal/gate/order side effects.

**2026-07-10 — WO-46 implemented by Codex.** Maker-carry reward share now follows the published
liquidity-scoring rule: market plus complement books, c=3 single-sided scoring inside the eligible
mid band, strict double-sided scoring outside, and `band_ineligible` exclusion from the portfolio.
The old same-token share remains as `share_model_legacy` for one-release comparison, and the history
ledger records `share_model=published_v2`. Registered M-gates and net-carry gate definitions were
not loosened.

**2026-07-10 — WO-44 implemented by Codex.** Maker-fill replay now can collect official CLOB
`orderbook-history` snapshots into `outputs/maker_carry/official_books/*.csv.gz`, replay archive and
official book states side-by-side, and report per-source realism ratios plus source agreement. Missing
official history degrades to the existing archive source without failing the measurement lane. This
legacy collection/fallback description is superseded by WO-83 below; it is retained as implementation
history and no longer defines validation coverage.

**2026-07-14 — WO-83 implemented by Codex.** Tier-0 maker validation now polls the documented
current CLOB `/book`/`/books` API and public trade prints for exactly the active quote-sheet portfolio
on its 15-minute monitoring cadence. Venue change time and local observation time remain distinct, so
an unchanged book observed again is valid point-in-time coverage without look-ahead. The matched
collection ledger is `outputs/maker_carry/maker_replay_collection_windows.csv`; replay output at
`outputs/maker_carry/maker_fill_replay.json` contains per-market windows covered/simulated,
last-in-queue confirmed-fill ratio, 5/15/60-minute markout distributions, a last-seven-days/prior cut,
and the reported simulation-to-reality haircut. A nonzero simulation with no 5-minute coverage is
`insufficient_coverage`, and persistent blindness opens a WO-78 incident. The haircut is never
auto-applied: only a dated tighten-only M-B amendment could act on it. No registered gate, sizing,
paper/live permission, credential, or order path changed.

**2026-07-10 — WO-45 implemented by Codex.** Maker-carry candidates and quote sheets now show
supplementary maker rebates and holding rewards as uncounted income, with portfolio-level rebate,
holding, and total supplementary summaries. These values are explicitly excluded from registered
M-gates and from `portfolio_net_carry_usd_per_day`.

**2026-07-10 — WO-49 implemented by Codex.** Flow-toxicity conditioning now writes VPIN-lite signed
volume imbalance percentiles plus wallet-tier markout splits to
`outputs/maker_carry/flow_toxicity.csv`. The maker quote sheet displays toxicity and adds standing
rule 8: do not initiate quotes above `toxicity_score > 0.9`. This is conditioning only; adverse
charges, gates, sizing, and order paths are unchanged.

**2026-07-10 — WO-50 implemented by Codex.** The registered maker live-test decision policy is now
mechanised behind CLI `decision-policy`, writing `outputs/maker_carry/decision_policy.json` and a
quote-sheet/dashboard badge. It evaluates the frozen action table, sizing ladder, quarter-Kelly cap,
and kill criteria while remaining strictly advisory: no paper/live orders, no gate changes, and no
automatic funding action.

**2026-07-10 — WO-51 implemented by Codex.** Maker-carry candidates now carry a tighten-only
resolution-risk screen: objective Fed/rate, match/game, numeric-close, and election-result wording
is low by default; subjective UMA-dispute-prone wording is high; and the resolution-quality corpus
can only escalate low classes to medium. High-risk questions are measured but excluded from the
quote portfolio and quote sheet rule 9 tells the human to avoid unclear/disputed resolutions.

**2026-07-10 — WO-52 implemented by Codex.** Hour-of-day adverse-selection concentration now runs
as CLI `hourly-adverse-study`, writing `outputs/maker_carry/hourly_adverse.json`. It compares
per-UTC-hour band-crossing charge share against a uniform reward-minute null with BH-FDR, reports
toxic hours and a calm-hours advisory, and patches the maker quote sheet. This is advisory only and
does not change maker charges, gates, sizing, or order paths.

**2026-07-10 — WO-53 implemented by Codex.** The VPS ops scheduler now runs an intraday
`maker-carry-study` sample on its own `maker_study_intraday` 24h stamp only when the last daily
training harvest is 11-13 hours old. This improves maker reward-competition sampling without
changing schemas or fast-forwarding M-A, which remains distinct-UTC-day based.

**2026-07-11 — WO-54 implemented by Codex.** Trade-print collection now has CLI
`backfill-trade-prints`, which pages data-API `/trades` for current maker-study candidates and the
quote-sheet portfolio, dedups into `outputs/polymarket_trade_prints/trade_prints.csv`, and stamps
completed markets for idempotent reruns. The VPS daily harvest runs it immediately after
`maker-carry-study`, turning venue history into markout/toxicity substrate without changing gates or
orders.

**2026-07-10 — WO-42 implemented by Codex.** Calibration-bias harvesting now joins clean resolved
markets to point-in-time pre-close prices and writes
`outputs/calibration_bias/calibration_curve.csv` plus summary JSON. It reports category/horizon/bin
calibration, isotonic-smoothed frequencies, clustered bootstrap CIs, and BH-FDR-filtered candidate
bins. This is study-only and does not authorise trades.

**2026-07-11 — WO-56 landed by Codex in PR #134.** Maker-carry coverage now pre-screens rewarded
markets by achievable gross at minimum quote size, records pot/yield rank and scan mode, and falls
back to pot ranking if book screening fails. All thin-book, band, resolution, adverse-selection,
payout, registered-$500, and distinct-day gate semantics remain unchanged. Evidence stays under
`outputs/maker_carry/`.

**2026-07-11 — WO-57 implemented by Codex.** Maker-carry reporting now emits a supplementary
capital-to-target curve at $250/$500/$1,000/$2,000/$5,000 caps in
`outputs/maker_carry/maker_carry_study.json` and the human quote sheet. The registered $500
`portfolio_net_carry_usd_per_day` calculation and every M-gate use the same unchanged sizing path;
the curve is explicitly an uncounted planning aid and is never read by policy or gate code.

**2026-07-11 — WO-58 landed by Codex in PR #131.** Wallet intelligence now probes the production
public `/v1/leaderboard` path before the legacy fallback and can source tracked markets from
maker-carry candidates/study or trade-print history when websocket tracking is empty. Outputs stay
under `outputs/wallet_intelligence/`; collection remains read-only and cannot trade.

**2026-07-11 — WO-59 implemented by Codex.** The WO-50 advisory quarter-Kelly ceiling now passes
through `risk.shrunk_kelly_fraction`, using a tighten-only 20-daily-observation evidence floor.
Short histories shrink toward the no-edge prior, while 20+ observations reproduce the registered
inline value. The frozen ladder remains the outer cap; no gate, action table, or order path changed.

**2026-07-11 — WO-60 implemented by Codex.** CLI `performance-factsheet` packages daily Sharpe,
Sortino, drawdown, Calmar, hit-rate, profit-factor, and bootstrap Sharpe intervals in
`outputs/performance/performance_factsheet.json` and `.md`, with an evidence-classed dashboard
section. Annualised fields stay null below 20 daily observations; every paper/shadow/modeled row is
stamped simulated, and only sample-qualified `maker_live_test` evidence can ever be presentation
ready. This reporting artifact is not read by any gate, policy, sizing, broker, or order path.

**2026-07-11 — WO-47 implemented by Codex.** The market websocket now requests custom lifecycle
events even when asset IDs come from dynamic liquidity/position selection. Authoritative
`market_resolved` stamps append to `outputs/polymarket_websocket/resolution_events.csv`; complete
`new_market` birth metadata, including fee schedule/rebate rate, sports timing/type, and tick size,
append to `market_births.csv`. Both ledgers are deduplicated and byte-append-only. They are isolated
validation evidence and are not consumed by features, closing-line grading, governance, or trading.

**2026-07-12 — WO-68 implemented by Codex.** CLI `operating-state` now generates
`outputs/performance/operating_state.json` and `.md` from effective config, governance and evidence
artifacts, execution ledgers, WO-67 P1-P5 checks, and the host telemetry deployment manifest. The VPS
daily harvest refreshes it and the dashboard renders the same JSON. README and AGENTS now contain
only pointers; a drift test rejects planted or future hard-coded state claims. Missing evidence is
reported as `UNKNOWN`, and this reporting path cannot invoke paper or live trading.

**2026-07-12 — WO-68b implemented by Codex.** The same generated state now reports seven
tighten-only human-alert SLOs (quote sheet, governance duration, scheduler skips, websocket,
dashboard, reconciliation, and anchor) plus `origin/main`, host-checkout, and last-successfully-
deployed SHAs with divergence age. `scripts/preflight_vps_capacity.py` evaluates the target Compose
revision before checkout mutation or service replacement and refuses under-capacity deploys while
leaving the healthy stack running. Exit-75 supervisor events append to the WO-61-anchored
`outputs/performance/background_timeout_incidents.csv`; full governance has one VPS owner, the ops
scheduler. These controls remain reporting/operations-only and cannot invoke paper/live trading.

**2026-07-12 — WO-69 implemented to the platform boundary by Codex; WO-100 rebuilt the gate on
2026-07-19.** A repository-scoped Linux ARM64 self-hosted runner on the upgraded VPS serves the
deterministic `Required PR Gate`: Ruff, both config validations, and the complete unfiltered suite in
a bounded Python 3.11 container. `scripts/audit_github_merge_gate.py` writes the WO-68 P4 artifact and
can apply a legacy review/direct-push hardening payload. It now rejects reuse
of an older successful run, audits latest-push review semantics, and refuses to
treat a name/app-bound status context as proof that the required workflow ran.
Enforcement remains fail-closed and incomplete: GitHub returns HTTP 403
for private-repository branch protection/rulesets on the current Free plan. The documented fallback
requires a second identity to approve and dispatch an exact-head merge; it remains blocked while the
repository has only one push-capable identity. The lane also authorizes the
actual rerun initiator, rejects candidate changes to its trusted control files,
and performs a non-force squash-equivalent ref update parented to the verified
main SHA, so a concurrent main advance fails closed. A workflow-identity-capable
required-workflow ruleset, not a legacy same-name context, is mandatory before
the protected-branch path can report enforced; the repository must not be made
public as a workaround.

**2026-07-12 — external-audit P2/P6 remediation implemented by Codex.** Sharp-anchor coverage now
reconciles independently observed raw-fetch, normalisation, mapping-audit, mapped-token, current
prediction-join, and executable bid/ask stages by source/sport/market. It emits
`sharp_anchor_mapping_audit.csv`, `sharp_anchor_coverage.json`, and the idempotent
`sharp_anchor_funnel_history.csv`; missing timestamps or non-conserving cross-stage counts make the
accounting explicitly incomplete. The dashboard shows the same funnel and uses actual ask-side
divergence for buy actionability. Research is frozen to exactly the three prospective primary
hypotheses in `docs/EXPERIMENT_REGISTRY.md`; all pre-freeze H2/H3 observations remain diagnostic,
and the registered legacy taker verdict runs only as a stopping-rule obligation. No threshold,
paper/live gate, sizing path, or order path changed.

**2026-07-12 — WO-66 implemented by Codex.** Maker portfolio rows now produce exact public human
order tickets (URL, outcome/token, tick-rounded bid/ask, shares, and capital). CLI
`requote-alerts` rides the 15-minute trade-print cycle and writes
`outputs/maker_carry/requote_alerts.json` from current websocket bid/ask, scheduled-event timing,
toxicity, public Gamma UMA proposal/dispute state, lifecycle resolution events, and the registered
kill artifact. The dashboard and quote sheet show one of `quotes_ok`, `requote_advised`,
`pull_quotes_now`, or `STOP`; state-deduplicated notifier artifacts are eligible only for the two
critical states, and daily alert snapshots are WO-61 anchored. This is keyless human decision support: no SMTP credential, exchange auth,
signature, placement, amendment, cancellation, paper, or live order path exists. WO-67 remains
blocked and unimplemented.

**2026-07-13 — WO-77 implemented by Codex.** The production gap was confirmed:
neither current maker carrier condition ID appeared in the 126-token websocket
target set, and the legacy portfolio artifact lacked every executable ticket
field. `requote-alerts` now enriches that legacy metadata from public Gamma,
uses one bounded batch CLOB-book fallback per cycle for uncovered tokens, and
persists exact URL/outcome/token/tick/bid/ask fields with their live source.
The websocket live loop reserves first-priority slots for current or repaired
quote-sheet tokens. Missing public books still fail closed; the change is
read-only and does not alter gates, sizing, policy, credentials, or orders.

**2026-07-13 — WO-78 implemented by Codex.** CLI `degraded-state-watchdog` now
runs after every VPS scheduler tick and distinguishes persistent missing-input
fail-closed states from legitimate risk reasons. Tighten-only registrations
open an incident on requote cycle four, the first non-zero scheduler exit,
wallet partial harvest three, or a known operating row regressing to
`UNKNOWN`. The byte-append-only
`outputs/performance/degraded_state_incidents.csv` is WO-61 prefix-anchored;
current incidents appear in the canonical operating state and dashboard and
emit the existing owner-notification artifact contract. Polls never inflate
counts, repeat incidents deduplicate, and the component cannot alter source
states, gates, sizing, credentials, paper/live trading, or orders.

**2026-07-13 — WO-71 implemented by Codex.** WO-31 zero-join histories now
drive a persisted, reviewable paid-request suppression plan: persistently
unmappable sport/market families drop to a 24-hour probe cadence and recover
normal cadence on the first successful join without editing config. Daily CLI
`corpus-retention` compacts expired high-volume research rows into a separate
bounded daily gzip archive before source pruning, removes only stale invalid
atomic temp files, and logs host-disk projection. Its writable surface is
fixed; WO-61 paths, investor evidence namespaces, and the WO-65 recovery
archive are excluded by construction. The live websocket producer remains the
sole writer of its active table. This is collection operations only and cannot
alter models, evidence gates, paper/live permissions, sizing, or orders.

**2026-07-13 — WO-79 implemented by Codex.** Deployment now has a final
real-current-data acceptance boundary. The runner captures the pre-deploy
operating state and rollback SHA, records independent exit codes for the
quote-sheet/requote/reconciliation/operating producers after restart, and
fails success unless ticket completeness, legitimate requote state,
three-leg reconciliation coverage, and no-new-UNKNOWN comparison all pass.
FAIL is persisted, owner-notifiable, and visible in the cockpit before the
workflow exits. The initial three-interface producer/consumer registry makes fields,
freshness, and coverage machine-testable; its ARM64 PR-gate fixtures include
the socket-coverage miss that caused WO-77. The component is reporting and
deployment control only: it cannot change gates, models, sizing, credentials,
paper/live permissions, or orders, and the prior release remains reversible.

**2026-07-13 — WO-73 items 1–3 implemented by Codex.** Read-only wallet
monitoring now treats the operator and executor sub-account as separate named
entities throughout scoreboard, reconciliation, histories, and operating
state; no NAV or score is silently summed. Executor onboarding is public-ID
only and requires the owner to enable AUTO-REDEEM WINS. Before any telemetry
or archive push, a redacting guard scans the actual whitelisted surfaces and
refuses the push on credential-shaped values; the ARM64 PR gate carries clean
and planted-leak tests. The keyless rotation drill proves an unchanged
fail-flat contract against missing/invalid dummy credentials. WO-73 item 4
remains post-amendment: there is no executor credential loading or order path.

**2026-07-14 — custody Amendment A1 reconciliation.** The separate-wallet
description above is retained as WO-73 implementation history, but A1 now
governs: one project wallet, non-overlapping human/executor UTC windows, and
mode/time attribution from anchored ledgers. The legacy
`executor_wallet_address` stays empty. Fail-flat, rotation, revocation, and
credential-guard controls remain unchanged.

**2026-07-13 — WO-74 implemented by Codex.** A keyless, executor-independent
replay-certification harness now combines recorded WO-44 official-book windows
with every registered stress case and verifies exact ticket boundaries,
policy caps, 5-share multiples, pull/STOP cancellation, flat stale/missing
behavior, heartbeat dead-man behavior, and one-to-one action-ledger appends.
Its dated PASS/FAIL artifact is a canary prerequisite, never authorisation.
The bundled reference stub proves only the harness; no credentials, executor,
broker, paper/live trading, signing, cancellation, or order path exists.

**2026-07-13 — WO-75 items 1, 3, and 4 implemented by Codex.** The VPS ops
scheduler now independently consumes the future executor ledger/heartbeat
contract and publishes an `ABSENT`-until-present live-ops surface covering
mode, open orders, exposure versus stage cap, last-action age, heartbeat
freshness/dead-man countdown, decision-policy kill criteria, and executor-era
project-wallet reconciliation. Under A1 this means executor-era monitoring of
the same project wallet, never a concurrently active second wallet. Registered
alert transitions emit the existing
owner-notification artifact contract and deduplicate unchanged incidents. The
fourth producer/consumer contract makes the future ledger/heartbeat schema,
freshness, and coverage PR-gate testable. The monitor and dashboard cannot
write the heartbeat or invoke execution. WO-75
item 2 remains post-amendment and false in the artifact; WO-67 remains blocked.

**2026-07-15 — WO-85 implemented by Codex.** The daily training harvest is
now a bounded, per-step CLI orchestration with durable progress in
`outputs/ops_scheduler/training_harvest.json`: an early failure cannot starve
later work, ordinary steps beyond the six-hour start budget are explicitly
skipped, and corpus retention plus ledger anchoring are always attempted last.
The scheduler records last successful completion separately from attempts;
WO-78 registers fixed cadence freshness ceilings and immediately alerts after
25 hours without a successful harvest. Gate A fails closed on material
per-position clustering fallback. These are operational and statistical
safety tightenings only; no evidence threshold, sizing, broker, paper/live, or
order path changed.

**2026-07-15 — WO-85 completion-stamp correction.** Daily-harvest cadence and
the intraday offset now read the last successful completion rather than a
touch-before-run stamp. Starts write a separate attempt stamp and only an exit
zero advances `last_success_training_harvest`, so a container restart or failed
harvest re-arms instead of silently consuming the next 24-hour slot.

**2026-07-15 — WO-87 implemented by Codex.** The legacy taker verdict keeps
its registered arithmetic, alpha, sample floor, clustering, and thresholds,
but now labels the binding quantity honestly as unit mean net settlement
return per dollar (pre-fee) and labels positive units
`settled_profitable`. `profit_verdict.json` and the dashboard carry the
mandatory settlement-return caveat plus a separately registered, non-binding
true pre-event CLV diagnostic: the last official same-token in-band price at
or before close minus six hours, aggregated on the exact Gate A units. Missing
references remain `pre_event_clv_ungradeable`; no gate, sizing, broker,
paper/live permission, or order path changed.

**2026-07-15 — WO-86 implemented by Codex.** The advisory maker decision
policy now has an independent fail-safe freshness criterion for its kill
inputs. Once a human maker-test/live configuration, positive ladder stage, or
executor ledger/heartbeat makes the guard active, an absent or older-than-30m
maker-live observation sets `kill_data_stale` and forces
`stop_quoting_review_before_resume`; effective-config overrides may shorten
but never widen that maximum. WO-78 turns the same condition into an immediate,
deduplicated owner-alert incident. Pre-live empty data remains clear and fresh
evaluations are unchanged. This adds no execution, cancellation, credential,
signing, or order path.

**2026-07-15 — WO-88 implemented by Codex.** The read-only maker scoreboard
now partitions public activity-feed trades into raw, owner activity, and
maker-test counts. Exclusion requires an explicit `drill_trade` or
`maintenance_trade` row from the immutable WO-82 log, covered by the latest
verified WO-61 byte-prefix anchor and matching within the fixed five-minute
time, condition, side, price, and cumulative-size contract. Unknown,
malformed, unanchored, and unmatched fills continue to trip the maker alarm;
owner-only days are skipped rather than earned or broken in the ladder. The
current JSON and new anchor-enrolled attribution history preserve both counts.
No gate, sizing, broker, credential, paper/live permission, cancellation, or
order path changed.

**2026-07-16 — WO-95 implemented by Codex in PR #238.** Active discovery now
hard-excludes the frozen crypto up/down family while retaining historical
rows, labels, settlement logic, and websocket coverage for existing open
positions. The paper and liquidity scanners reserve deterministic coverage
for H1 sharp-anchor maker carry, H2 dutch consistency, and H3
structural-bias/smart-flow research, with explicit `ok` or `starved` coverage
telemetry. Primary artifacts are
`outputs/polymarket_model_governance/local_live_loop_discovery_heartbeat.json`
and
`outputs/polymarket_model_governance/liquidity_discovery_summary.json`; both
remain observation-only and cannot alter gates, sizing, paper/live permission,
credentials, or order paths.

**2026-07-16 — WO-96 merged in PR #239.** Wallet intelligence now parses the
recorded nested holder-token groups, public trade rows retain wallet and market
metadata without revising immutable facts, and the registered H3 evaluator
uses first eligible wallet-token-day fills with executable pre-close bids,
frozen chronological discovery/validation, market-clustered uncertainty, FDR,
and concentration controls. The evaluator remains shadow research only and
cannot promote, size, or execute risk.

**2026-07-16 — WO-97 implemented by Codex in PR #240.** WO-39 now consumes the
normaliser's actual
`outputs/polymarket_training/websocket_market_features.csv` producer through a
shared path constant. Missing, empty, or malformed producer coverage is
explicitly non-OK instead of a successful zero-market poll; the summary exposes
the path and coverage state. The 15-minute cadence, API limits, atomic bounded
trade/OI ledgers, and fail-soft per-market OI behavior are unchanged. This is
collection-only and changes no hypothesis, signal, gate, threshold, sizing,
capital, credential, broker, or order path.

**2026-07-16 - WO-98 implemented by Codex.** H2 now has a frozen
prospective evaluator contract: exact complete-basket top-ask depth, canonical
per-leg fees, a fixed adverse/slippage reserve, explicit clear scans,
event-day episode independence, three-scan persistence, a deterministic
event-clustered interval, concentration control, and the original 100-episode/
60-day stop. The exact artifact will become the dashboard's H2 authority;
legacy gross scanner output remains diagnostic. The lane is shadow research
only and cannot alter paper/live, gate, sizing, funding, signer, credential,
broker, or order paths. Exact observations, frozen episodes, and the verdict
are published under `outputs/h2_dutch/`, enrolled in the evidence anchor, and
shown as the dashboard's H2 authority.

**2026-07-19 - WO-101 rebuilt by Codex.** The resolved-market diagnostic
trainer now has explicit point-in-time provenance: append-only resolution
states whose labels cannot predate their first observation, append-only
executable bid/ask observations from the live websocket/archive, separate
feature and label files, and a whole-market purged chronological split with a
24-hour embargo and at least 10 independent validation markets. The venue's
single-price history remains diagnostic and cannot be treated as a spread.
The resulting skill model is H3 structural-bias diagnostic substrate only;
WO-96 remains the exact H3 verdict authority and every paper/live invocation
flag remains false. Funding remains closed and WO-67 remains blocked.

## Rules of engagement for coding agents

0. **Division of labour**: the orchestrating agent writes/updates this charter and the work orders
   in `docs/POLYMARKET_CODEX_WORK_ORDERS.md`; implementing agents (Codex etc.) execute work orders
   exactly as written and flip statuses when they land. If a work order is ambiguous or wrong,
   raise it — do not improvise around a safety rule.
1. **Claim one WP at a time.** Keep diffs scoped to the WP; do not drive-by refactor gate logic.
2. **Fail closed.** Every new evidence stream must default to `insufficient` and require explicit
   sample-size + CI thresholds to turn positive.
3. **Determinism.** Seed every bootstrap/simulation; tests must not depend on network access.
4. **Artifacts over prints.** New signals write JSON/CSV under `outputs/polymarket_model_governance/`
   or a dedicated output folder, and register a CLI command in `cli.py` `COMMANDS`.
5. **Every WP ships with tests** under `tests/polymarket_predictive_engine/` and a short doc note
   (this file's WP status line + `docs/POLYMARKET_CURRENT_STATE.md` if the operating state changes).
6. **Run `pytest` before pushing.** Keep changes leakage-safe and dry-run/shadow-safe.
7. **Update this charter** when a WP lands: flip its status, date it, and note the artifact paths.

## Definition of done for "quant trader bot"

Quant mode is *done* when, for at least one market family, the system can show — from artifacts alone,
without a human digging through code:

```text
1. positive out-of-sample calibration edge vs the market (Brier/log-loss, CI)
2. positive forward evidence on all three streams: settlement, bid/ask round trips, and CLV
3. cost-aware EV that survives depth-based slippage at the intended stake
4. risk-sized paper probes whose realised, reconciled P&L matches the modelled edge attribution
```

Only then does the human-gated promotion path to larger paper stakes (and, far later, any live
discussion) begin. Until then the correct state is exactly what the audit says: explainable refusal.

**Read this against the terminal verdict at the top of this charter (2026-08-22).** No tested edge
class ever reached the four conditions above, and the registered evidence clock for those classes has
expired. This definition still stands as the standard any FUTURE registered hypothesis must meet —
it is not a live checklist for the classes already answered, and nothing below it authorises
re-opening one.
