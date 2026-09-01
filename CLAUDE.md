# CLAUDE.md

Follow [`AGENTS.md`](AGENTS.md). It is the single source of truth shared by all
coding agents and it governs wherever this file and any other prose differ.

The operating rule not to miss is **VPS only**: the local workstation is for
code/Git/GitHub/SSH control, while engines, tests, Docker, dashboards, schedulers,
collectors, brokers, models, and watchdogs run only on the VPS. Keep the system
paper/dry-run and do not add or enable a live order path or relax any gate.
(One narrow, mandatory exception — the hermetic offline `pytest` suite in an
ephemeral, network-isolated agent sandbox — is defined by the 2026-07-27
amendment in AGENTS.md. A green sandbox run is never verification of record.)

Everything below is the durable-memory layer: the system-level intelligence and
finance expertise a fresh session needs so it does not re-derive — or worse,
re-learn by breaking things — what this repository already knows. Sessions are
disposable; this file, `AGENTS.md`, the charter, and the work-order register are
the memory that survives them. This file is orientation, not authority: it
registers no thresholds, grants no authorization, and where a number matters,
the registered document it points to is the value of record.

## What this system is

One economic question, answered under pre-registered fail-closed rules: can a
paper-only Polymarket engine produce verified forward evidence of a sustainable
edge before any real capital is considered? The Polymarket predictive engine is
the principal system. The SuperBru score engine (`src/superbru_score_engine`)
shares infrastructure but is ancillary and not part of the economic thesis.

The system's designed steady state when no edge is proven is **explainable
refusal** — a blocked model with a named reason is a correct output, not a
failure. The system must never manufacture the appearance of profit by
loosening its own gates.

## Where truth lives

Never infer current state from prose, dashboards screenshots, or a Git
checkout. Point-in-time state is generated from runtime evidence into
`outputs/performance/operating_state.md` / `.json` (contract:
`docs/OPERATING_STATE.md`). Missing or stale evidence is `UNKNOWN` and fails
closed. VPS decision summaries reach remote sessions through the single-commit
`vps-telemetry` branch; heavy corpora never leave the VPS.

## Who does what

- **Owner** (Danie) — the only source of authorization. Frozen or registered
  surfaces change only by owner-authored commit or owner-approved PR. OWNER
  AUTHORIZATION IS NEVER AGENT-WRITABLE, and an instruction to audit or
  investigate is never authorization to build.
- **Orchestrator** — authors work orders and audits; never reviews its own
  adjudication, never merges owner-routed PRs.
- **Builder** (Codex or equivalent) — executes registered work orders
  mechanically, one work order per branch and PR.
- No agent both produces and approves the same artifact. Unresolved review
  threads block merge even when the required gate is green.

## System architecture

- `src/polymarket_predictive_engine/` — the engine (~130 modules). By role:
  - **Collection**: `websocket_collector`/`websocket_normaliser` (bid/ask/depth
    features), `trade_print_collector`, `price_history_collector`,
    `historical_backfill`, resolution collectors, `wallet_intelligence_collector`,
    `sharp_odds_fetch` (bookmaker anchors), `external_feed_collector`.
  - **Features/models**: `features.py`/`features_v2.py` (point-in-time only;
    `FORBIDDEN_FEATURE_FIELDS` guards leakage), `models/`, `family_calibration`,
    `price_action_model` (strict ask-in/bid-out target), `leakage_safe_training`.
  - **Research lanes**: `maker_carry_study` (H1; frozen M-A/M-B/M-C gates),
    `sharp_anchor*` + `sharp_linking_evaluator` (H1 qualification),
    `dutch_arb_monitor` + `h2_dutch_evaluator` (H2), `h3_smart_flow_evaluator` +
    `smart_flow_clv` (H3), plus diagnostics (`longshot_bias`, `drift_scan_study`,
    `calibration_bias_study`, `implication_consistency`, `hourly_adverse_study`,
    `flow_toxicity`).
  - **Execution research (shadow/paper only)**: `execution_costs` (depth-based
    slippage, fails closed), `algo/` (typed `OrderIntent`, strategy registry,
    no-lookahead replay, train-only sweep), `paper_broker`, `portfolio`,
    `risk` (entry band, shrunk Kelly, correlated exposure).
  - **Governance/verdicts**: `governance`, `readiness`, `promotion_review`,
    `profit_verdict` (legacy taker adjudication), `live_test_decision_policy`
    (frozen WO-50 advisory table), `closing_line` (CLV), `edge_attribution`.
  - **Ops**: `dashboard`, `degraded_state_watchdog`, `deploy_acceptance`,
    `operating_state`, `ledger_anchor`, `credential_guard`, `runtime_lock`.
- `src/quant_lab/` — deterministic quant primitives (VaR/CVaR/drawdown/Sharpe,
  cost-aware backtester, chronological and walk-forward splits); consumed by
  governance. Curriculum: `docs/QUANT_CURRICULUM.md`.
- `src/polymarket_common/fees.py` — canonical category/price-aware taker fees.
- Conventions: every lane writes JSON/CSV artifacts under `outputs/<lane>/`,
  registers a CLI command in `cli.py` `COMMANDS`, seeds every bootstrap, writes
  shared artifacts atomically, and states `paper_trading_invoked=false` /
  `live_trading_invoked=false` unless an authorized paper path produced it.
  Evidence ledgers are append-only and anchored (`ledger_anchor`).
- Runtime: one Compose stack (`docker-compose.vps-paper.yml`) — paper-live
  loop, dashboard, ops scheduler, SuperBru watchdog. Deploys use only the two
  guarded paths defined in AGENTS.md; ad-hoc pull/rebuild is forbidden.

## Finance expertise

### The edge definition (the trading contract)

Tradeable edge is executable, never notional: buy at the current ask, exit at
a later executable bid, and the predicted exit must clear entry ask plus
spread, slippage, fees, and a profit hurdle. Midpoint movement is not edge.
Settlement-probability skill is not short-horizon edge. If the recorded book
contains no profitable future-bid examples, the correct output is a blocked
model, not a forced trade. Full contract:
`docs/POLYMARKET_QUANT_TRADING_CONTRACT.md`.

### The three registered hypotheses (the whole promotion surface)

The research surface is frozen to exactly three primaries
(`docs/EXPERIMENT_REGISTRY.md`; no fourth may be inferred from a module or an
attractive dashboard number):

1. **H1 — sharp-anchor maker carry** (priority). Resting quotes on rewarded
   markets earn a published reward-pot share and may capture spread; an
   independent sharp bookmaker anchor (de-vigged) identifies quotes least
   likely to be erased by adverse selection. Edge = realized reward + spread
   − markout − fees/gas/costs, never the reward headline. Verdict authority:
   `maker_carry_study.py` frozen gates plus the registered validation ladder.
2. **H2 — dutch-book consistency**. A complete mutually-exclusive basket whose
   asks sum below one locks profit only if every leg is simultaneously buyable
   at common size and the deviation persists across scans. Verdict authority:
   `outputs/h2_dutch/h2_evaluation.json` only.
3. **H3 — structural-bias / smart-flow cohorts with positive executable CLV**.
   Edge exists only when a pre-specified cohort's entry repeatedly beats the
   later executable line after costs — never because a wallet has a compelling
   historical P&L headline. Verdict authority: the WO-96 artifact only.

Crypto up/down is a timing/infrastructure diagnostic with negative forward
evidence — structurally unwinnable for this operation (priced continuously off
the same feed by faster participants). Do not revive it.

### Market-making theory the maker lane is built on

(Survey with sources: `docs/MARKET_MAKING_MODELS_RESEARCH.md`.)

- **Spread decomposition** (Huang-Stoll): order-processing cost + inventory
  cost + adverse selection. Maker P&L per fill = realized spread − markout;
  markout at fixed horizons (5/15/60 min here) against subsequent mid drift is
  THE measure of adverse selection.
- **Glosten-Milgrom**: spread exists because some flow is informed; every fill
  is Bayesian evidence against you. **Kyle**: informed flow hides in noise;
  impact is linear in lambda. **VPIN**: signed-volume imbalance proxies flow
  toxicity — widen or pull when toxicity spikes (quote-sheet rule: do not
  initiate above the registered toxicity ceiling).
- **Avellaneda-Stoikov**: reservation price r = s − q·γ·σ²·(T−t) — inventory
  shades your private fair, so quotes skew around r, not mid. House standing
  rule derived from it: once filled on one side, requote to reduce, never to
  add.
- **Queue position**: at a fixed price grid, edge depends on queue priority;
  all fill replays here use last-in-queue as the conservative bound.
- **Prediction-market specifics**: prices live in [0,1] and settle at exactly
  0 or 1; there is no hedge instrument, so unhedged terminal inventory is a
  pure directional bet. T−t is real (markets expire); jump risk (news, goals)
  dominates diffusion. Venue mechanics that matter: no native stop orders (so
  no stop-hunt "liquidity pools" — ICT/MMXM folklore does not transfer),
  sports books auto-cancel at game start, tick regime shifts near the
  extremes, reward scoring is two-sided min(Q_bid,Q_ask) with single-sided
  scoring only inside the mid band, and matching restarts enter post-only.
  Maker rebates and holding rewards are tracked as uncounted supplementary
  income — registered gates deliberately exclude them (conservatism).

### Statistical discipline (how this repo avoids fooling itself)

- **Pre-register before observing**: mechanism, universe, independent unit,
  one primary metric, sample floor, cost model, multiple-test correction,
  stopping rule, and promotion/abandonment action — all dated before the
  evaluation window opens. Post-hoc hypothesis registration is prohibited;
  pre-boundary observations are diagnostic history only.
- **Independent units kill pseudo-replication**: one portfolio-day (H1), one
  event×UTC-day episode with a proven clear between episodes (H2), first fill
  per wallet×token×UTC-day (H3). Repeated ticks never manufacture sample size.
- **Uncertainty is clustered**: bootstrap CIs cluster by market/event/family;
  support requires the clustered 90% lower bound above zero, not a positive
  mean.
- **Multiple testing**: Benjamini-Hochberg FDR across the COMPLETE tested
  family including null and negative cells; reporting only the best cell is
  prohibited.
- **Concentration caps**: no single event or family may supply more than the
  registered share of positive profit — one lucky whale is not an edge.
- **Chronological, purged, embargoed splits**: discovery/train windows precede
  validation; thresholds are selected on train only; whole-market splits purge
  overlapping label intervals and embargo near boundaries; nothing is ever
  tuned on the evaluation window, and a failed window is never re-mined.
- **Point-in-time everything**: a label becomes available at the latest of
  close, reported resolution, and first actual observation — a later API
  response cannot backdate availability. Features come only from timestamped
  executable two-sided quotes; a single-price history is not a spread.
- **Evidence classes are a one-way ladder**: historical, modeled,
  reconstructed, shadow, paper, live — never relabelled upward. Every
  simulation number is an upper bound until validated against reality (H1's
  ladder: Tier 0 fill replay → Tier 1 reward receipt → Tier 2 real-fill
  markout; a bad Tier 0 can retire the lane at zero cost).

### Costs, sizing, and risk

- Costs are modeled pessimistically and fail closed: category/price-aware
  taker fees on entry AND exit, depth-based slippage that can only lower the
  flat assumption when the book is demonstrably deep AND the quote is fresh
  (missing or stale depth never earns a discount), plus fixed
  adverse-selection reserves in the evaluators.
- Sizing: Kelly shrunk toward the market price (the no-edge prior), always ≤
  capped Kelly, under a frozen advisory ladder and quarter-Kelly ceiling;
  entry-price band 0.05–0.90 at the risk layer (base config; overrides cannot
  widen it); correlated-exposure budgets by correlation key; VaR/CVaR
  reported on open positions. All risk changes are tighten-only.

## Hard-won lessons (defect → rule)

Each of these was paid for; do not relearn them.

- Raw ledger P&L said +$55 while audited quote-consistent P&L said +$0.03:
  only audited, reconciled round trips count as profit evidence.
- A dashboard annualised 2 round trips over 4 minutes into thousands/month:
  never annualise micro-windows; thin-sample fields render `n/a`.
- The honesty loop (measurement attacks estimates until they shrink to truth)
  killed the $958 mirage, a wrong fee constant, and a wrong reward-share
  model — it is the system's proven core. Protect it.
- Liquidity-weighted discovery + fast feedback concentrated all collection
  into crypto up/down, the one family the engine cannot win: breadth guards
  and family caps now exist. Watch for the same loop in any new lane.
- `nan > ceiling` is False, so a corrupt timestamp classified as *fresh*:
  every comparison states its missing/non-finite branch, and an unverifiable
  input never reads as healthy, fresh, or compliant.
- Windows anchored to observed-data timestamps instead of the run clock became
  time bombs: anchor freshness to one run clock; ship clock-advance tests.
- External timestamps arrive in mixed units: normalize at the ingestion
  boundary (numeric > 1e10 is milliseconds).
- Plain writes on shared artifacts corrupt concurrent readers: atomic writes
  only (`utils.write_json` / temp + `os.replace`).
- A diagnostic was specced against a corpus whose collection policy could
  starve it: a feature reading an artifact it does not produce needs a stated
  producer/coverage contract, not "the corpus happens to contain it today".
- Builds shipped against unreviewed registered text, thresholds were named
  without values, scopes contradicted their own amendments: hence S8
  admission and the dispatch-ancestry GLOBAL RULE. Registered text is the
  most expensive artifact to get wrong — it is permanent, drives builds, and
  fails silently.
- The build loop (questions → work orders → more system → more audit load)
  can outrun carrying capacity while the value loop carries zero flow: adding
  machinery is not progress; forward evidence reaching reality is.

## Engineering reflexes (binding detail in `docs/ENGINEERING_STANDARDS.md`)

S1 one clock, normalized units, clock-advance tests · S2 atomic shared writes,
stated interleavings · S3 data-dependency contracts · S4 recorded-reality
fixtures, property tests for windows/decay/dedup/fail-safes · S5 a written
fail-safe-direction sentence per feature, checked path by path · S6 a
day-after production check per work order · S7 the written review checklist
(reports state what was verified by which method and never claim absence of
defects) · S8 the admission checklist that gates registration of work-order
text itself.

## Deep-dive references for this file's material

| Topic | File |
|---|---|
| Hypotheses, gates, boundaries of record | `docs/EXPERIMENT_REGISTRY.md` |
| Executable-edge trading contract | `docs/POLYMARKET_QUANT_TRADING_CONTRACT.md` |
| Quant-mode roadmap and audit log | `docs/POLYMARKET_QUANT_MODE_CHARTER.md` |
| Market-making theory and venue mechanics | `docs/MARKET_MAKING_MODELS_RESEARCH.md` |
| Why the targets were reset (2026-07-03) | `docs/POLYMARKET_EDGE_STRATEGY_RESET.md` |
| Actors, loops, SPOFs, structural picture | `docs/SYSTEM_MAP.md` |
| Binding standards S1–S8 | `docs/ENGINEERING_STANDARDS.md` |
| Work-order register and GLOBAL RULE | `docs/POLYMARKET_CODEX_WORK_ORDERS.md` |
| Quant curriculum primitives | `docs/QUANT_CURRICULUM.md` |

## Keeping this file honest

Update it when structure or understanding changes, not when numbers change —
numbers live in registered documents and generated state. Never add
authorization language, a threshold of record, or a state claim to this file.
Where this file and any binding document differ, the binding document governs
and this file is the text that must change.
