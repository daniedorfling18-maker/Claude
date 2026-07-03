# Polymarket Quant Mode Charter

Last updated: 2026-07-02

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

### WP4 — CLV-aware promotion review (advisory, fail-closed) — `open`

- `promotion_review.py`: add CLV as a *corroborating* signal — a cohort with positive settlement or
  round-trip evidence AND `positive_clv_evidence` ranks above one without; `negative_clv_evidence`
  adds a blocker note. CLV alone must never promote.
- Acceptance: promotion review output includes CLV fields; a cohort with only positive CLV still
  reads `blocked`; tests assert both directions.

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

### WP6 — Portfolio-level correlated exposure from live positions — `open`

- `risk_decision` already takes `current_correlated_exposure`, but callers must compute it from open
  positions sharing a `correlation_key` (see `worldcup_validation.normalised_correlation_key`).
  Audit the paper/shadow callers, close any gaps, and add a portfolio-level VaR snapshot using
  `quant_lab.risk` over open-position marks.
- Acceptance: risk state artifact reports correlated exposure by key and portfolio VaR; a test shows
  two same-event candidates draining the same correlated budget.
- Status: partially landed by Codex (`portfolio_state` computes correlated exposure per
  `normalised_correlation_key`); the remaining VaR/reporting slice is specced as **WO-12**.

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

**2026-07-02 — overnight queue issued.** WO-7 and WO-10..WO-19 are specced in the work orders doc
with a night-shift protocol (fixed order, stop conditions, skip-and-note rules, end-of-night
report). New ground covered by the queue: portfolio VaR reporting (WP6 remainder), microstructure
hypotheses as replay strategies + generalised sweep, per-family calibration scorecard, collection
coverage for CLV finality, evidence history time series, a dashboard evidence funnel, and
invariant property tests that lock the safety envelope. Nothing in the queue can loosen a gate;
WO-19 explicitly changes zero source files.

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
