# Polymarket Quant Mode Charter

Last updated: 2026-07-16

This is the **orchestration charter** for turning the Polymarket predictive engine into a full quant
trading system. It is written for every coding agent working on this repo — Claude, Codex, or any
other code changer. Read `AGENTS.md` first; this charter adds the quant-mode roadmap on top of it and
never overrides its safety rules.

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

**2026-07-12 — WO-69 implemented to the platform boundary by Codex; runner moved 2026-07-13.** A repository-scoped Linux ARM64
self-hosted runner on the upgraded VPS now serves the deterministic `Required PR Gate`: ruff, config validation, and the
registered governance/invariant subset. `scripts/audit_github_merge_gate.py` writes the WO-68 P4
artifact and can apply the exact protection payload. Enforcement remains fail-closed and incomplete:
GitHub returns HTTP 403 for private-repository branch protection/rulesets on the current Free plan.
Upgrade to Pro/Team and a clean `--apply-protection` audit are mandatory before further live capital;
the repository must not be made public as a workaround.

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
