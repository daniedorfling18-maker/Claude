# Polymarket Codex Work Orders

Last updated: 2026-07-03 (WO-11/12/13/23/24/25/26/27 code landed; strategic edge reset: WO-24..WO-27 added; WO-20/21/22 landed; read docs/POLYMARKET_EDGE_STRATEGY_RESET.md first)

Mechanical, file-level implementation instructions for coding agents (Codex or any other code
changer). The architecture and priorities live in `docs/POLYMARKET_QUANT_MODE_CHARTER.md`; this file
tells you exactly what to build, where, and how to prove it works. Read `AGENTS.md` first.

## Ground rules (apply to every work order)

1. **One work order per branch/PR.** Do not combine work orders or drive-by refactor.
2. **Run `pip install -e ".[dev]"` once, then `pytest` before pushing. All tests must pass.**
3. **Never touch gate logic**: no changes to `readiness.py` promotion gates, `risk_decision` check
   thresholds, `_paper_decision` blockers in the audit script, config gate defaults, or anything in
   `execution/live.py`. If a work order seems to require it, stop — the work order is wrong.
4. **Fail closed**: new evidence defaults to `insufficient`; new order paths default to `shadow`.
5. **Determinism**: seed every random process; tests must run offline with fixture data.
6. **Artifacts over prints**: outputs are JSON/CSV under `outputs/…`; every new pipeline entry point
   gets a CLI command in `cli.py` `COMMANDS` (keep list position near related commands).
7. Every new artifact JSON must include `"paper_trading_invoked": false` and
   `"live_trading_invoked": false`.
8. When a work order lands, flip its status here and in the charter (status + date + artifact paths).

Reference implementations to imitate: `closing_line.py` (module shape, settings, artifact style),
`test_closing_line.py` (test shape: minimal `EngineConfig(raw={"paths": {...}})`, fixture CSVs via
`write_csv`, exact-value assertions).

## Pre-flight checklist — run through this before you push

Answer every question. A single "no" means stop and re-read the work order.

1. Did you touch ONLY the files the work order lists? (`git diff --stat` — anything else is a bug.)
2. Is every threshold, gate boolean, and blocker list byte-identical to before your change, unless
   the work order explicitly says otherwise? Grep your diff for `minimum_`, `maximum_`,
   `approved`, `blocked`, `promotion` and justify every hit.
3. Can your change make ANY code path looser — bigger stakes, lower slippage, more approvals,
   fewer blockers — under any input? If yes and the work order didn't demand it, invert it.
4. Do all your new artifact JSONs carry `"paper_trading_invoked": false` and
   `"live_trading_invoked": false`?
5. Are your tests asserting exact values computed by hand in the test body — not just "no crash"?
6. Does `pytest` pass in full, offline, from a clean checkout?
7. Did you flip the work-order status here AND the WP status in the charter, with a dated
   "Landed:" note saying what exists now and where?

Common failure modes seen or anticipated in this repo — do not repeat them:

- **Turning advisory evidence into a gate.** CLV, replay results, and classifier families are
  diagnostics. If your diff makes any of them flip an `approved`/`blocked` boolean, it is wrong.
- **"Improving" an estimate in the optimistic direction.** Estimators here may only get more
  conservative by default; below-flat/below-baseline results need demonstrated evidence (deep AND
  fresh book, explicit config opt-in).
- **Merging later data into earlier rows.** Anything that joins latest quotes/prices onto stored
  rows is scoring-time only. Training, labels, and backtests read stored point-in-time data. When
  in doubt, add the WO-9-style explode-test before refactoring.
- **Silent try/except around builders.** The pipeline is fail-loud by design; swallowing an error
  hides broken evidence and is worse than a crash.
- **Loosening a check "because the test broke".** If an existing test fails, your change is wrong
  or the work order says to update that exact test. Never widen the assertion to make it pass.
- **Forgetting Windows.** The scheduled cycle runs on Windows; paths through `Path`, no
  POSIX-only calls in engine code (`os.open` with O_EXCL is fine; `fcntl` is not).

---

## WO-1 — Call the CLV builder from the governance refresh (WP3a) — `done` (2026-07-02)

**Goal:** `closing_line_value.json` is rebuilt on every scheduled shadow-research cycle.

**Why here:** the cycle's `refresh-governance` step runs
`python -m polymarket_predictive_engine.refresh_governance` after shadow evidence updates, so wiring
into `refresh_governance()` covers the scheduled task with zero PowerShell changes.

**Files:** `src/polymarket_predictive_engine/refresh_governance.py`,
`tests/polymarket_predictive_engine/test_refresh_governance.py`.

**Steps:**

1. In `refresh_governance.py`, import `build_closing_line_value` from `.closing_line`.
2. Inside `refresh_governance()`, call `closing_line = build_closing_line_value(cfg)` immediately
   after `build_paper_round_trip_evidence(cfg)` and **before** `build_promotion_review(cfg)` and
   `render_dashboard(cfg)` (promotion review and dashboard will consume the artifact later).
3. Add `"closing_line_value": True` to the `refreshed` dict, and these top-level result fields:
   `"closing_line_positions_scored"`, `"closing_line_final_positions"`,
   `"closing_line_mean_final_clv"`, `"closing_line_positive_cohorts"` — mapped from the
   `build_closing_line_value` return keys `positions_scored`, `final_line_positions`,
   `mean_final_clv`, `positive_clv_cohorts`.
4. Do not add try/except around it (match the existing style: every builder is fail-loud).

**Definition of done:** existing tests still pass; `test_refresh_governance.py` gains assertions
that (a) `refreshed["closing_line_value"]` is `True`, (b)
`outputs/polymarket_model_governance/closing_line_value.json` exists after a refresh, (c) the new
top-level fields are present.

**Out of scope:** PowerShell scripts, dashboard, audit script, promotion review logic.

**Landed:** `refresh_governance()` now rebuilds
`outputs/polymarket_model_governance/closing_line_value.json` after paper round-trip evidence and
before promotion review/dashboard, and exposes CLV summary fields in `governance_refresh.json`.

---

## WO-2 — Show CLV on the dashboard (WP3b) — `done` (2026-07-02)

**Goal:** per-cohort CLV evidence is visible at `http://127.0.0.1:8765/` next to the other
governance sections.

**Files:** `src/polymarket_predictive_engine/dashboard.py`, its test
(`tests/polymarket_predictive_engine/test_dashboard_and_profit_target.py`).

**Steps:**

1. In `dashboard.py`, grep for `quant_research_status` and copy its pattern exactly, three places:
   - the `read_json(governance / "quant_research_status.json", ...)` block → add the same for
     `closing_line_value.json` (default `{}`, coerce non-dict to `{}`);
   - the dashboard data payload dict (`"quant_research_status": quant_research_status,`) → add
     `"closing_line_value": closing_line_value,`;
   - the embedded JS (`const quantResearch = data.quant_research_status || {};`) → add a
     `closingLine` section.
2. Render a compact section titled "Closing-line value (CLV)" showing: `positions_scored`,
   `final_line_positions`, `mean_final_clv`, `beat_close_rate`, and a table of `cohorts` rows with
   columns cohort / final positions / mean final CLV / CI low / CI high / evidence class. Show
   `positive_clv_cohorts` as a highlighted list when non-empty. No new chart libraries; match the
   existing plain-HTML/JS style.
3. Handle the empty artifact gracefully (section renders "no CLV evidence yet").

**Definition of done:** a test writes a small `closing_line_value.json` fixture, renders the
dashboard, and asserts the payload JSON contains the `closing_line_value` key and the HTML contains
the section title; empty-artifact case asserted too.

**Out of scope:** dashboard server/runner scripts, any other dashboard section.

**Landed:** `dashboard.py` now reads
`outputs/polymarket_model_governance/closing_line_value.json`, includes it in
`outputs/polymarket_dashboard/dashboard_data.json`, and renders a "Closing-line value (CLV)"
dashboard section with summary metrics, positive cohorts, and per-cohort evidence classes. Empty
artifacts render a no-evidence-yet message.

---

## WO-3 — CLV block in the local-history audit report (WP3c) — `done` (2026-07-02)

**Goal:** the audit report humans actually read
(`outputs/polymarket_model_governance/local_history_audit_report.md`) shows CLV evidence beside
settlement and round-trip evidence.

**Files:** `scripts/audit_polymarket_local_history.py`, plus a test (create
`tests/polymarket_predictive_engine/test_local_history_audit_clv.py` if no audit test exists).

**Steps:**

1. In `run()`, `read_json` the `closing_line_value.json` artifact (default `{}`).
2. Add a `"closing_line_value"` block to the audit payload: `positions_scored`,
   `final_line_positions`, `mean_final_clv`, `positive_clv_cohorts`, and the `cohorts` list filtered
   to rows with `final_positions > 0`.
3. In `_write_markdown()`, add a "Closing-line value (CLV)" section: one summary line plus a bullet
   per cohort with final evidence (`cohort — n_final=…, mean_final_clv=…, CI=[…, …], evidence=…`).
   When the artifact is empty write "No CLV evidence collected yet."
4. **Do NOT touch `_paper_decision()`.** CLV must not add or remove blockers/warnings in this work
   order (that is WP4, separately governed).

**Definition of done:** test builds a fixture `closing_line_value.json`, runs the audit `run()`
against a temp output root, asserts the payload block and the markdown section exist, and asserts
the paper-decision blockers are byte-identical with and without the CLV artifact present.

**Landed:** `scripts/audit_polymarket_local_history.py` now includes a report-only
`closing_line_value` summary in `local_history_audit_summary.json` and a "Closing-line value (CLV)"
markdown section in `local_history_audit_report.md`. The regression test proves paper-decision
blockers are unchanged with and without CLV.

---

## WO-4 — Typed order-intent schema (WP9, algo compatibility) — `done` (2026-07-02)

**Goal:** a single typed, validated order representation between "a strategy wants to trade" and
"a broker executes", so strategies, replay, paper, and (far-future, human-gated) live all speak one
language. Today's signal CSV path keeps working unchanged.

**Files:** new `src/polymarket_predictive_engine/execution/intents.py`, new
`tests/polymarket_predictive_engine/test_execution_intents.py`.

**Spec:**

1. `@dataclass(frozen=True) OrderIntent` with fields:
   `intent_id: str`, `created_at_utc: str`, `market_id: str`, `token_id: str`,
   `side: str` (`"BUY"`/`"SELL"` only), `quantity: float`, `limit_price: float`,
   `time_in_force: str` (`"IOC"`, `"GTD"`), `expire_at_utc: str` (required when GTD),
   `execution_policy: str` (`"cross_spread"`, `"join_bid"`, `"work_midpoint"`),
   `max_slippage: float`, `mode: str` (`"shadow"`, `"paper"` — **no `"live"` value exists**),
   `source_strategy: str`, `signal_ref: str` (free-form link to the originating signal/decision).
2. `validate_intent(intent) -> list[str]` returning human-readable violations (empty = valid):
   price not in (0,1) exclusive; quantity <= 0; unknown enum values; GTD without expiry;
   mode not in {shadow, paper}. `require_valid(intent)` raises `ValueError` listing violations.
3. `intent_to_dict` / `intent_from_dict` round-trip (JSON-safe, all floats rounded to 6dp).
4. Adapter `intent_from_risk_decision(signal: dict, decision: dict, *, mode: str = "shadow") ->
   OrderIntent | None`: returns `None` unless `decision["approved"]` is truthy; maps
   `decision["quantity"]`, `decision["limit_price"]`, side `"BUY"`, policy `"cross_spread"`,
   `max_slippage` from the signal's slippage field (default 0.0), `signal_ref` from
   market/token/prediction-timestamp. `mode` defaults to `"shadow"`; passing `"paper"` is the
   caller's assertion that governance already approved the signal — the adapter does not check
   gates itself and must say so in its docstring.
5. Adapter `intent_to_paper_signal(intent) -> dict` producing a row shaped like the rows
   `paper_broker._paper_signal_rows` reads today (grep `submit_paper_signal` for consumed keys).
   Pure function; no broker changes in this work order.

**Definition of done:** tests cover every validation branch, dict round-trip equality, the
`approved=False → None` path, that constructing/validating a `mode="live"` intent fails, and that
`intent_to_paper_signal(intent_from_risk_decision(sig, risk_decision(cfg, sig)))` yields a dict the
current broker code accepts field-wise (assert expected keys, not broker behaviour).

**Out of scope:** any change to `paper_broker.py`, `strategy.py`, `execution/live.py`.

---

## WO-5 — Algo strategy protocol + registry (WP10) — `done` (2026-07-02)

**Blocked by WO-4.**

**Goal:** pluggable event-driven strategies: quote event in, order intents out. This is the seam
that makes the system "algo trading compatible" rather than batch-only.

**Files:** new package `src/polymarket_predictive_engine/algo/` (`__init__.py`, `events.py`,
`base.py`, `registry.py`), new `tests/polymarket_predictive_engine/test_algo_strategy.py`.

**Spec:**

1. `events.py`: `@dataclass(frozen=True) QuoteEvent` mirroring the normalised websocket feature row
   (`websocket_normaliser.FEATURE_FIELDS`): timestamps, market/asset ids, slug, category,
   best_bid/best_ask/midpoint/spread, depth fields, book_imbalance. Classmethod
   `from_feature_row(row: dict) -> QuoteEvent | None` (None when no usable bid/ask/mid; reuse
   `safe_float`/`parse_timestamp` semantics from `closing_line.py`).
2. `base.py`:
   - `StrategyContext`: read-only dataclass — `config: EngineConfig`, `open_positions: tuple`,
     `now_utc: str`. No mutable references, no I/O.
   - `class AlgoStrategy(Protocol)`: `name: str`;
     `on_quote(self, event: QuoteEvent, context: StrategyContext) -> list[OrderIntent]`;
     `on_fill(self, fill: dict, context: StrategyContext) -> None`.
   - Strategies must be pure/deterministic given (event, context) — document this in the module
     docstring; no network, no file I/O inside strategies.
3. `registry.py`: `register(strategy_cls)`, `get_strategy(name) -> AlgoStrategy`,
   `available_strategies() -> list[str]`. Register two built-ins:
   - `NullStrategy` (`name="null"`): always returns `[]` — the safety baseline.
   - `TightSpreadJoinBidShadow` (`name="tight_spread_join_bid_shadow"`): emits a **shadow-mode**
     BUY intent with `execution_policy="join_bid"` at the current best bid when
     `spread <= context.config` setting `algo.tight_spread_maximum` (default 0.02) and
     `book_imbalance >= algo.minimum_book_imbalance` (default 0.6), stake fixed at
     `algo.shadow_stake_usdc` (default 1.0). This is a research probe of an existing microstructure
     hypothesis (tight-book bid momentum) — intents are shadow-only and go nowhere until WO-6.
4. Every intent a strategy emits must pass `require_valid`; the registry wrapper enforces it and
   also enforces `mode == "shadow"` unless `cfg.raw["algo"]["allow_paper_intents"]` is explicitly
   true **and** the intent's cohort appears in the promotion gate's approved list — otherwise the
   wrapper downgrades the intent to shadow and records the downgrade reason.

**Definition of done:** tests for registry lookup/unknown-name error; NullStrategy emits nothing;
the example strategy emits exactly the expected intent on a crafted event and nothing when spread
or imbalance fails; wrapper downgrades a paper-mode intent to shadow by default.

---

## WO-6 — Websocket replay harness (WP11) — `done` (2026-07-02)

**Blocked by WO-4 and WO-5.**

**Goal:** run any registered strategy against recorded websocket history, chronologically, with
conservative simulated fills — the event-driven backtester that closes the algo loop, entirely
offline and shadow-only.

**Files:** new `src/polymarket_predictive_engine/algo/replay.py`, CLI wiring in `cli.py`
(command `algo-replay`, reuse `--websocket-input` for the features CSV and add `--strategy`,
default `"null"`), new `tests/polymarket_predictive_engine/test_algo_replay.py`.

**Spec:**

1. `iter_quote_events(features_path) -> Iterator[QuoteEvent]`: read
   `outputs/polymarket_training/websocket_market_features.csv` rows, convert via
   `QuoteEvent.from_feature_row`, drop Nones, sort by (timestamp, asset_id) — strictly
   chronological, no lookahead.
2. `run_replay(cfg, strategy_name, *, features_input=None) -> dict`:
   - feed events one at a time to the strategy with a context reflecting replay state;
   - simulate fills conservatively: a BUY `cross_spread` intent fills at the event's `best_ask`
     only if `limit_price >= best_ask`; a `join_bid`/`work_midpoint` intent rests and fills only
     when a **later** event on the same asset shows `best_ask <= limit_price` (crossing down);
     expire GTD intents past `expire_at_utc`; IOC intents cancel if not immediately fillable;
   - mark open positions to the latest `best_bid` (never midpoint) — same conservative standard
     as the shadow cohort;
   - track a ledger of intents, fills, cancels, expiries, and per-position P&L;
   - write `outputs/polymarket_algo/replay_<strategy>_summary.json` (include strategy name, events
     processed, intents/fills/cancels counts, realised+unrealised P&L marked to bid, per-cohort
     breakdown, and the two mandatory `*_invoked: false` flags) plus
     `replay_<strategy>_fills.csv`;
   - deterministic: no wall-clock reads inside the loop (use event timestamps), no network.
3. Replay must refuse (`status: "refused"`, no outputs) if any emitted intent has
   `mode != "shadow"` — replay is a research instrument, not an execution path.

**Definition of done:** tests: NullStrategy replay over a fixture CSV → zero intents, zero fills,
valid summary artifact; example strategy over a crafted fixture → exact expected fills and P&L
(hand-computed in the test); a strategy stub emitting a paper-mode intent → replay refuses;
`polymarket-engine algo-replay --strategy null` wired and listed in `COMMANDS`.

---

## WO-7 — CLV-aware promotion review, advisory only (WP4) — `done` (2026-07-03)

**Goal:** promotion review ranks cohorts using CLV as a *corroborating* signal without letting CLV
alone promote anything. This is annotation + ordering, nothing else.

**Files:** `src/polymarket_predictive_engine/promotion_review.py`,
`tests/polymarket_predictive_engine/test_promotion_gate.py` (or a new
`test_promotion_review_clv.py` following that file's fixture style).

**Where things are in `promotion_review.py` today:** `build_promotion_review(cfg)` reads
`signal_cohort_pnl.json` from `cfg.governance_root`, maps each cohort through `_review_row`, and
writes `promotion_review.json`. You are adding a read of `closing_line_value.json` beside it.

**Exact steps — do not deviate:**

1. In `build_promotion_review`, immediately after the `signal_cohort_pnl.json` read:

   ```python
   clv_artifact = read_json(cfg.governance_root / "closing_line_value.json", default={}) or {}
   clv_by_cohort = {
       str(row.get("signal_cohort")): row
       for row in (clv_artifact.get("cohorts") or [])
       if isinstance(row, dict)
   }
   ```

2. After each review row is built, attach exactly these keys (empty-string/None defaults when the
   cohort has no CLV row — never invent values):
   `clv_evidence` (default `"insufficient_clv_evidence"`), `clv_mean_final`, `clv_ci_low`,
   `clv_ci_high`, `clv_final_positions` (default 0).
3. Ranking is the ONLY behavioural change. Wherever the result rows are ordered for output, use
   CLV as a tiebreaker AFTER all existing sort keys, e.g. append
   `1 if row.get("clv_evidence") == "positive_clv_evidence" else 0` as the last, lowest-priority
   component. Do not touch any `approved`/`blocked`/`promotion_ready` computation. Do not read CLV
   inside `_review_row`'s gating logic.
4. When `clv_evidence == "negative_clv_evidence"`, append the string
   `"negative closing-line value evidence (advisory)"` to the row's existing notes/blockers *list
   of strings* — as an extra note only; it must not change any boolean or status field.
5. `promotion_review.json` gains a top-level `"clv_source": "closing_line_value.json"` marker and
   `"clv_is_advisory_only": true`.

**Definition of done (write these exact tests):**

1. Fixture with two cohorts identical in every existing metric, one with
   `positive_clv_evidence` — assert it sorts first and that all its boolean/status fields equal
   the other cohort's (ranking changed, decision did not).
2. Fixture with a cohort whose ONLY positive stream is CLV (no settlement, no round-trip
   evidence) — assert its status/booleans are identical to the same fixture without the CLV
   artifact present (CLV alone changes nothing but the annotation).
3. Fixture with `negative_clv_evidence` — assert the advisory note string is present and no
   boolean/status field changed.
4. No CLV artifact on disk — assert `build_promotion_review` output is unchanged except for the
   default annotation keys.

**The wrong implementations, spelled out (all are rejections in review):**

- Reading CLV inside promotion/approval boolean logic — CLV must never flip a decision.
- Making positive CLV satisfy, substitute for, or partially weight any evidence threshold.
- Sorting by CLV before existing sort keys (it is the last tiebreaker, not a primary key).
- Writing the advisory note into `_paper_decision` in the audit script (different file, WP4 does
  not touch it).
- try/except around the artifact read (missing file is already handled by `default={}`).

**Out of scope:** `readiness.py`, any gate threshold, the audit script's `_paper_decision`,
`closing_line.py` itself.

**Landed:** `build_promotion_review()` now reads `closing_line_value.json`, adds advisory CLV
fields to each review row, and uses positive CLV only as the final output-order tiebreaker after all
existing sort keys. Negative CLV writes `negative closing-line value evidence (advisory)` to
`advisory_notes`, not to mechanical gate fields. `promotion_review.json` now includes
`clv_source` and `clv_is_advisory_only`. Tests prove positive CLV alone cannot change status,
booleans, gates, or promotion decisions.

---

## WO-8 — Quote-freshness guard on below-flat execution costs — `done` (2026-07-02, orchestrator)

**Goal:** `estimate_execution_cost` may only return below-flat slippage when the depth evidence is
fresh. Stale depth can overstate what the book can absorb.

**Files:** `src/polymarket_predictive_engine/execution_costs.py`, callers that can pass a quote
timestamp (`risk.py`, `strategy.py`, `shadow_cohort.py`, `mispricing_alpha.py`), tests.

**Steps:**

1. Add an optional `quote_age_seconds: float | None = None` parameter and a
   `max_fresh_age_seconds: float = 120.0` knob to `estimate_execution_cost`.
2. `demonstrably_deep` additionally requires `quote_age_seconds is not None and
   quote_age_seconds <= max_fresh_age_seconds`. When the age is unknown or stale, expected
   slippage stays `max(flat, model)` — never below flat (strictly more conservative; no caller
   can get looser behaviour from this change).
3. Callers pass the age where they already know it (`websocket_quote_age_seconds` from alpha
   enrichment; event timestamps in websocket-sourced rows); callers that cannot know it pass
   nothing and keep today's conservative branch.
4. Tests: fresh+deep -> below-flat allowed; stale+deep -> floored at flat; unknown age -> floored
   at flat; missing depth unchanged.

**Landed:** `estimate_execution_cost` takes `quote_age_seconds`/`max_fresh_age_seconds` (default
120s), falls back to the row's `websocket_quote_age_seconds`, and requires freshness for
`depth_is_demonstrably_deep`; output includes `quote_age_seconds` and `quote_is_fresh`.
`strategy.py` propagates `websocket_quote_age_seconds` into signal rows so `risk_decision` sees it.
Tests cover fresh/stale/unknown/row-field/shallow-book-unaffected cases.

---

## WO-9 — Regression guard: quote enrichment must never touch training/backtest paths — `done` (2026-07-02, orchestrator)

**Goal:** `_enrich_with_latest_websocket_quotes` merges the *latest* quotes into rows at scoring
time. That is correct for live decision-making and would be lookahead if it ever reached model
training or historical backtests. Lock this in with tests before anyone refactors it.

**Files:** `tests/polymarket_predictive_engine/test_mispricing_alpha.py` (extend),
`src/polymarket_predictive_engine/mispricing_alpha.py` (docstring only unless a leak is found).

**Steps:**

1. Audit call sites: enrichment must be reachable only from `apply_mispricing_alpha` scoring, not
   from `train_mispricing_alpha_model`, `backtest`, or any label-building path. If a leak exists,
   gate it out with an explicit `allow_quote_enrichment=False` default on the training path.
2. Add a test that trains the alpha model with a websocket features file present and asserts the
   training rows' prices/spreads are untouched by the latest quotes.
3. Document the invariant in the module docstring: "enrichment is a scoring-time convenience;
   training and backtests must consume stored point-in-time rows only."

**Landed:** call-site audit confirmed enrichment is reachable only from `apply_mispricing_alpha`;
the module docstring states the invariant; `test_training_path_never_reaches_quote_enrichment`
makes `train_mispricing_alpha_model` explode if enrichment is ever wired into it, and confirms the
scoring path does route through it.

---

## WO-10 — Wire edge attribution and the algo sweep into the cycle, dashboard, and audit — `done` (2026-07-03)

**Goal:** `edge-attribution` and `algo-sweep` run on every scheduled cycle and their results are
visible where humans look. Pure wiring — the modules exist and are tested; do not change them.

**Files:** `src/polymarket_predictive_engine/refresh_governance.py`,
`src/polymarket_predictive_engine/dashboard.py`, `scripts/audit_polymarket_local_history.py`,
their existing tests.

**Exact steps (copy the CLV wiring, commit-for-commit — it is the same shape):**

1. `refresh_governance.py`: import `build_edge_attribution` from `.edge_attribution` and
   `run_algo_sweep` from `.algo.sweep`. Call both immediately after the
   `build_closing_line_value(cfg)` call (attribution needs the fresh CLV positions CSV, so the
   order is: closing line -> edge attribution -> algo sweep). Add `"edge_attribution": True` and
   `"algo_sweep": True` to `refreshed`; add top-level fields
   `"edge_attribution_positions"` (from `attributed_positions`),
   `"edge_attribution_cohort_classes"` (dict of cohort -> `attribution_class`),
   `"algo_sweep_decision"` (from `decision`). No try/except.
2. `dashboard.py`: copy the `closing_line_value` read/payload/JS pattern for
   `edge_attribution.json` (governance root) and `polymarket_algo/algo_sweep_summary.json`
   (output root). Two sections: "Edge attribution" — facts (attributed positions, identity note)
   plus a cohort table (cohort / positions / total P&L / execution cost / line movement /
   settlement surprise / class / recommended action); "Algo sweep lab" — facts (decision, combos
   tested, train candidates, selected params + train/validation P&L). Empty artifacts render
   "no attribution evidence yet" / "no sweep run yet".
3. Audit script: add report-only `edge_attribution` and `algo_sweep` payload blocks (summary
   fields + cohort classes only) and matching markdown sections, added AFTER `_paper_decision`
   is computed, exactly like the CLV block. **`_paper_decision` stays untouched.**
4. Tests: extend the refresh-governance test (order: closing_line before edge_attribution before
   dashboard; refreshed flags; artifact files exist), the dashboard test (fixture artifacts ->
   payload keys + section titles; empty case), and the audit test (payload blocks present;
   paper decision byte-identical with and without the artifacts).

**Out of scope:** any change to `edge_attribution.py`, `algo/sweep.py`, gates, or
`_paper_decision`.

**Landed:** `refresh_governance()` now runs closing-line value -> edge attribution -> algo sweep
before cohort P&L and downstream governance, and exposes `edge_attribution_positions`,
`edge_attribution_cohort_classes`, and `algo_sweep_decision` in `governance_refresh.json`.
`dashboard.py` reads and renders `edge_attribution.json` plus
`polymarket_algo/algo_sweep_summary.json`; the local-history audit report includes report-only
sections for both after `_paper_decision` is computed. Tests cover refresh order, dashboard payload
and empty-state rendering, and paper-decision invariance.

---

## WO-11 — Research focus consumes attribution, CLV, and sweep decisions — `done` (2026-07-03)

**Goal:** collection priorities steer toward cohorts where the evidence says edge might live and
away from cohorts where the model is simply wrong. Collection steering only — no gate changes.

**Files:** `src/polymarket_predictive_engine/research_focus.py`, its test.

**Exact steps:**

1. In `build_research_focus`, read (all `default={}`, coerce non-dict to `{}`):
   `edge_attribution.json`, `closing_line_value.json` (governance root), and
   `polymarket_algo/algo_sweep_summary.json` (output root).
2. Priority adjustments — affect ONLY collection ordering/queries, never gates:
   - cohorts with `attribution_class == "cost_dominated"` or `"positive_edge_confirmed"`, or in
     `positive_clv_cohorts`: raise collection priority and add their family terms to the
     collection queries;
   - cohorts with `attribution_class == "model_direction_not_confirmed"` AND
     `negative_clv_evidence`: lower collection priority (do NOT blacklist — suppression stays
     governance's job);
   - if the sweep decision is `sweep_candidate_validated_shadow_only`, add a research-focus note
     naming the selected parameters so humans see the lead.
3. The research-focus artifact gains an `evidence_inputs` block recording which cohorts moved and
   why (attribution class / CLV evidence / sweep decision) — every adjustment must be explainable
   from the artifact alone.
4. Tests: fixture artifacts -> assert a cost_dominated cohort's terms appear in collection
   queries; a direction-wrong + negative-CLV cohort ranks below it; no gate/threshold fields
   anywhere in the diff; output unchanged when the three artifacts are absent.

**Out of scope:** thresholds, promotion logic, blacklists, `readiness.py`, the audit script.

**Landed 2026-07-03:** `build_research_focus()` now reads edge attribution, CLV, and algo sweep
artifacts. Cost-dominated, positive-edge, and positive-CLV cohorts get collection-only priority
raises and family queries; model-direction-not-confirmed cohorts with negative CLV get lowered
priority without blacklisting. `research_focus.json` now includes an `evidence_inputs` block that
explains every cohort movement plus any validated shadow-only algo sweep lead. Tests cover the
fixture evidence path, advisory-only sweep note, absence/empty-artifact behavior, and the invariant
that these inputs do not alter gates or thresholds.

---

## WO-12 — Portfolio VaR snapshot and correlated-exposure reporting (WP6) — `done` (2026-07-03)

**Goal:** the risk-state artifact shows portfolio-level VaR/CVaR over open-position marks and
correlated exposure by correlation key. Reporting only — sizing already enforces the caps.

**Facts first:** `paper_broker.portfolio_state` already computes `current_correlated_exposure`
per `normalised_correlation_key` (see the sums near the end of that function). Do not rebuild it.

**Files:** `src/polymarket_predictive_engine/portfolio.py`, new
`tests/polymarket_predictive_engine/test_portfolio_var.py`.

**Steps:**

1. In `portfolio.py`, add `_portfolio_risk_snapshot(con, cfg) -> dict`: load open positions,
   group cost basis by `normalised_correlation_key(dict(row))` and by `category`; compute
   per-position mark-to-entry return series from `latest_mark_price`/`average_entry_price` (skip
   rows without both) and feed the return list into `quant_lab.risk` VaR/CVaR helpers (import
   `quant_lab.risk`, use its existing function signatures — read that module first, do not write
   new math). Output keys: `open_positions`, `total_cost_usdc`, `exposure_by_correlation_key`
   (top 10, sorted desc), `exposure_by_category`, `var_95_usdc`, `cvar_95_usdc`,
   `worst_position_return_pct`.
2. Merge that dict into the `risk_state.json` payload written by `portfolio_snapshot` under a
   `"portfolio_risk"` key.
3. Tests: seed a temp SQLite via the existing storage/init helpers (copy the pattern from
   `test_execution_governance_storage.py`) with 3 open positions, two sharing a correlation key;
   assert the shared key's exposure is the sum of both cost bases, VaR/CVaR are finite and <= 0
   or sensible for the seeded marks (hand-compute), and `risk_state.json` contains the block.

**Out of scope:** `risk_decision`, stake caps, `paper_broker` order logic.

**Landed 2026-07-03:** `portfolio_snapshot()` now writes `portfolio_risk` into
`outputs/polymarket_portfolio/risk_state.json`, including open/marked positions, total cost,
top correlated exposure, category exposure, historical VaR/CVaR, and worst marked position return.
The dashboard loads the risk-state artifact and renders a Portfolio risk panel. Tests seed three
typed SQLite positions, two sharing a correlation key, and verify exact exposure and VaR/CVaR
numbers without changing risk decisions, stake caps, or broker order logic.

---

## WO-13 — Mirror the validated microstructure hypotheses as replay strategies — `done` (2026-07-03)

**Goal:** the sweep lab can hunt over the same hypothesis space the microstructure lab already
tests, but at executable intent level. Three new registered strategies, all shadow-only.

**Files:** `src/polymarket_predictive_engine/algo/registry.py` (or a new
`algo/strategies_microstructure.py` imported by `registry.py`),
`tests/polymarket_predictive_engine/test_algo_strategy.py` (extend).

**Steps — copy `TightSpreadJoinBidShadow` exactly in shape (stable intent ids, GTD + TTL,
config-driven thresholds via `context.algo_setting`, shadow mode hardcoded):**

1. `BidMomentumTightShadow` (`name="bid_momentum_tight_shadow"`): needs the strategy to see the
   PREVIOUS quote per asset — strategies are stateless per event, so keep a small per-instance
   `dict[asset_id, QuoteEvent]` of the last event (document that replay instantiates one strategy
   per run, so this is replay-local state, deterministic, and allowed). Emit a join-bid BUY when
   `best_bid - previous.best_bid >= algo.min_bid_move` (default 0.01) and
   `spread <= algo.tight_spread_maximum`.
2. `MidMomentumTightShadow` (`name="mid_momentum_tight_shadow"`): same, on midpoint moves,
   `algo.min_mid_move` default 0.01.
3. `SpreadCompressionShadow` (`name="spread_compression_shadow"`): emit when the spread narrowed
   by at least `algo.min_spread_compression` (default 0.01) versus the previous event and
   `book_imbalance >= algo.minimum_book_imbalance`.
4. Every strategy: return `[]` whenever any needed field is None or there is no previous event.
   All intents `mode="shadow"`, `execution_policy="join_bid"`, stake `algo.shadow_stake_usdc`.
5. Tests per strategy: exact intent on a crafted two-event sequence; no intent on first event;
   no intent when the move/compression is below threshold; determinism (same events -> same
   intent ids).

**Out of scope:** `price_action_microstructure.py` (the lab stays as-is), replay/sweep internals.

**Landed 2026-07-03:** the algo registry now includes three new shadow-only replay strategies:
`bid_momentum_tight_shadow`, `mid_momentum_tight_shadow`, and `spread_compression_shadow`. Each keeps
only replay-local previous-quote memory by asset, emits deterministic GTD `join_bid` shadow BUY
intents, uses config-driven `algo.*` thresholds, and returns no intent on first events, missing
fields, below-threshold moves, weak imbalance, or non-positive stake. Tests cover exact crafted
two-event intents, no-intent branches, registry exposure, and stable intent ids.

---

## WO-14 — Generalise the sweep to any registered strategy — `open`

**Blocked by WO-13.**

**Goal:** `algo-sweep` reads per-strategy parameter grids from config and sweeps every listed
strategy, not just the tight-spread probe.

**Files:** `src/polymarket_predictive_engine/algo/sweep.py`,
`tests/polymarket_predictive_engine/test_algo_sweep.py` (extend).

**Steps:**

1. New config shape (keep the old keys working as the default grid for
   `tight_spread_join_bid_shadow` — backwards compatible):

   ```yaml
   algo_sweep:
     strategies:
       tight_spread_join_bid_shadow:
         tight_spread_maximum: [0.01, 0.02, 0.03]
         minimum_book_imbalance: [0.55, 0.65, 0.75]
       bid_momentum_tight_shadow:
         min_bid_move: [0.005, 0.01, 0.02]
         tight_spread_maximum: [0.02, 0.03]
   ```

2. Grid = cartesian product of each strategy's param lists (generic: params are plain
   `algo.<key>` overrides). Selection stays per-strategy AND global: report the best combo per
   strategy plus one overall `selected` (same deterministic sort). Decision logic unchanged and
   applied to the overall selection.
3. Combos CSV gains `strategy` and a `params` JSON column; per-strategy bests appear under
   `by_strategy` in the summary.
4. Tests: two strategies in the grid over the existing fixture; assert per-strategy bests, the
   overall selection, and that legacy config (no `strategies:` key) still produces the WO-6-era
   behaviour byte-for-byte on the old assertions.

---

## WO-15 — Evidence history time series (CLV + attribution per cycle) — `open`

**Goal:** see evidence accumulating over time instead of only the latest snapshot.

**Files:** new `src/polymarket_predictive_engine/evidence_history.py`, CLI `evidence-history`,
new test.

**Steps:**

1. `append_evidence_history(cfg)`: read `closing_line_value.json`, `edge_attribution.json`, and
   `algo_sweep_summary.json`; append ONE row per artifact per call to
   `outputs/polymarket_model_governance/evidence_history.csv` with:
   `recorded_at_utc, source, positions_scored/attributed, final_line_positions, mean_final_clv,
   positive_cohorts (joined by |), total_pnl_usdc, decision_or_class_summary`.
   Missing artifacts append nothing for that source. Idempotence: skip the append when the
   artifact's `generated_at_utc` equals the last recorded row's for that source.
2. Register the CLI command; wiring into `refresh_governance` is a one-line follow-up inside
   this same WO (call it LAST, after the three builders).
3. Tests: two calls with unchanged artifacts -> one row per source; artifact regenerated ->
   second row; missing artifacts -> no rows, no crash.

---

## WO-16 — Per-family calibration scorecard — `open`

**Goal:** answer "which families does the model actually beat the market in?" with one artifact:
Brier/log-loss vs the market baseline per classified family, on clean settled data only.

**Files:** new `src/polymarket_predictive_engine/family_calibration.py`, CLI
`family-calibration`, new test.

**Steps:**

1. Reuse, do not rewrite: `market_relative_validation.join_clean_settled_predictions` for the
   joined rows, its `brier_score`/`log_loss`/`brier_decomposition` for metrics, and
   `worldcup_validation.classify_market_family` for the family of each row.
2. Per family with at least `family_calibration.minimum_rows` (default 25) settled rows: model
   Brier, market Brier, brier gain (market - model), log losses, row/market counts, and the
   bootstrap CI machinery already present in `market_relative_validation` for the gain
   (chronological/market-clustered exactly as that module already does it — copy its pattern).
3. Evidence classes, fail closed: `model_beats_market` only when CI low > 0 and rows >= minimum;
   `market_beats_model` when CI high < 0; else `insufficient_calibration_evidence`.
4. Artifact `outputs/polymarket_model_governance/family_calibration_scorecard.json` (+ CSV per
   family). Standard flags, governance note ("scorecard is diagnostic; promotion still requires
   forward shadow evidence").
5. Tests with synthetic settled rows where the model is calibrated in one family and anti-
   calibrated in another; assert exact class per family and that below-minimum families read
   insufficient.

---

## WO-17 — Websocket collection coverage report — `open`

**Goal:** CLV finality depends on having quotes near each market's close; attribution depends on
closed positions having lines. Report where collection is thin so scheduling can fix it.

**Files:** new `src/polymarket_predictive_engine/collection_coverage.py`, CLI
`collection-coverage`, new test.

**Steps:**

1. Read `websocket_market_features.csv` and `shadow_positions.csv`. Per classified family:
   quote rows, distinct assets, first/last quote timestamps, median gap between consecutive
   quotes per asset (report the family median of those).
2. Per shadow position (open or closed): does a quote exist within
   `collection_coverage.pre_close_window_minutes` (default 30) BEFORE `close_time`? Summarise:
   `positions_with_pre_close_quote`, `positions_missing_pre_close_quote`, and list the missing
   ones (id, family, close_time) — these are exactly the positions whose CLV will stay
   provisional forever.
3. Artifact `outputs/polymarket_model_governance/collection_coverage.json`. Standard flags.
4. Tests: fixture with one covered and one uncovered position; assert both lists exact.

---

## WO-18 — Dashboard evidence funnel panel — `open`

**Land after WO-10 and WO-15..17 (it reads their artifacts; render blanks for missing ones).**

**Goal:** one dashboard section answering "where are we on the road to paper?" at a glance.

**Files:** `src/polymarket_predictive_engine/dashboard.py`, dashboard test (extend).

**Steps:** one section "Evidence funnel" with a single facts list, each value read from an
existing artifact (missing -> "-"): liquidity targets discovered; alpha shadow candidates; open /
closed shadow positions; closed with CLV final lines; attributed positions; cohorts by
attribution class; positive CLV cohorts; families with `model_beats_market` calibration; sweep
decision; paper gate status (`approved_for_paper_trading` from the promotion gate artifact —
display only). Follow the CLV section pattern for reads and rendering. Test: fixture artifacts ->
section title + a few exact values; all-missing -> renders with dashes.

---

## WO-19 — Invariant property tests — `open`

**Goal:** lock the safety envelope in tests so future refactors cannot silently loosen it.

**Files:** new `tests/polymarket_predictive_engine/test_safety_invariants.py` only. **This WO
changes zero source files.** If a property fails, STOP and report — do not "fix" source to match.

**Properties (loop over seeded random grids, e.g. 200 samples via `random.Random(20260702)`):**

1. Kelly monotonicity: for random (p, price) with p > price, `shrunk_kelly_fraction` is
   non-increasing in shrinkage and always <= `kelly_fraction`; both always within [0, cap].
2. Execution-cost conservatism: for random books, `expected_slippage >= flat_slippage` whenever
   `quote_is_fresh` is False; removing depth fields never DECREASES expected slippage; the
   stake cap never increases when depth shrinks.
3. Risk decision envelope: for random approved signals, `stake_usdc <= kelly_cap * bankroll`,
   `quantity * limit_price == stake_usdc` (to rounding), and setting any single risk input worse
   (higher spread, lower liquidity, higher slippage) never turns a rejection into an approval.
4. Intent schema: random dict fuzz over `intent_from_dict` -> `validate_intent` never raises
   (returns violations instead), and no accepted intent ever has `mode == "live"`.

---

## Night-shift protocol (read this before starting the queue)

Work the queue in the Sequencing order below. For each work order:

1. Fresh branch from latest `main`, named `codex/wo-<n>-<slug>`.
2. Implement exactly per spec. Run the FULL `pytest` suite.
3. Green -> push, open the PR, flip the statuses (here + charter) with a dated `Landed:` note,
   move on.
4. Red and the failure is yours -> fix or revert your change. NEVER widen an existing assertion,
   skip a test, or add xfail.
5. Blocked, ambiguous, or the spec contradicts the code you find -> do NOT improvise. Append a
   dated note under the work order describing the mismatch, leave the status `open`, skip to the
   next WO.
6. Re-read the Pre-flight checklist before every push. The invariants outrank the queue: a
   finished queue with one loosened gate is a failed night.
7. End of night: append a "Night report <date>" section at the bottom of this file — one line
   per WO: landed / skipped(reason) / blocked(note), plus the final full-suite test count.

## VPS dashboard audit — 2026-07-03 (orchestrator)

Live dashboard at the VPS was audited against main at 03:04Z. The payload is fresh and runs
current code (all sections present, WO-10 wiring live). Findings below are filed as WO-20..WO-23.
Root causes, verified in the repo:

1. **Collection does not follow positions.** 28 of 30 shadow positions had no usable quote
   history (CLV: 0 final lines; attribution: 22 of 24 closed positions unattributable), and all 6
   open paper positions are on July-2 intraday ETH/XRP up-or-down markets that settled hours ago
   but cannot exit or settle — no fresh quotes (exit guard blocks) and no resolution rows. The
   websocket feature file holds 119k rows of history, so retention is NOT the issue; the
   subscribed token set simply never included these positions' tokens.
2. **Raw vs audited P&L.** Raw ledger equity says +$55.27 since baseline; audited quote-consistent
   P&L is +$0.03 (`pnl_audit_state: raw_pnl_contains_quote_conflicts`, 5 conflicted + 6 unverified
   round trips). The engine's decisions correctly use the audited number and the headline card is
   honest, but equity/cash tiles and the account P&L line still surface the raw number without the
   caveat.
3. **Evidence-free extrapolations render as facts.** Promotion watchlist shows a cohort
   "run-rate $1,045/month" from 3 fills over 38h; CLV shows "beat close 0.0%" with zero final
   lines; "Algo replay best" displays the null strategy (0 fills) as best because the only real
   strategy lost money (-$8.55 on $30).
4. **Deployment-mode confusion.** Oversight warns "Shadow research cycle has not started" while
   `evidence_freshness` correctly reports the legacy live loop as the fresh driver on this VPS;
   Strategy V2 renders "missing". Two sections disagree about what should be running.

---

## WO-20 — Position-aware quote collection — `done` (2026-07-03)

**Goal:** every token with an open shadow or paper position stays in the websocket subscription
set until its market close (+ a grace window). This unblocks CLV finality, attribution joins,
paper exits, and settlement detection in one change.

**Files:** the websocket target-selection path (grep `websocket_liquidity_targets` writers and
the collector's asset selection in `websocket_collector.py` / liquidity discovery), plus tests.

**Steps:**

1. Build `position_tokens(cfg) -> list[dict]` reading open rows from
   `outputs/polymarket_shadow/shadow_positions.csv` and the paper positions table/CSV: token_id,
   market_id, close_time. Include tokens whose close_time is in the future OR within
   `collection.position_grace_hours` (default 6) past close.
2. In target selection, reserve up to `collection.position_token_slots` (default 10, capped at
   half the total slots) for these tokens FIRST; fill the rest with the existing liquidity-ranked
   selection. Never drop a position token in favour of a discovery token.
3. Emit the reserved list into the targets CSV with a `selection_reason=open_position` column so
   coverage is auditable.
4. Tests: fixture with 2 open-position tokens + N discovery tokens and a small slot budget ->
   position tokens always selected; grace-window expiry drops them; reason column present.

**Out of scope:** gates, stakes, broker logic.

**Landed:** `websocket_collector.position_tokens()` now reads open shadow positions,
paper-position exports, paper-order source payloads, and the paper positions table when available.
Held tokens are reserved first in `websocket_liquidity_targets.csv` until market close plus the
configured grace window, with `selection_reason=open_position` and summary
`target_position_counts` for auditability. Tests prove shadow + paper position tokens outrank
liquidity-discovery tokens and that positions outside the grace window are dropped.

---

## WO-21 — Settle or loudly flag stuck paper positions on resolved markets — `done` (2026-07-03)

**Goal:** paper positions on markets that closed hours ago must either settle through an
evidence-backed path or be flagged as `stale_open_position` on the dashboard and oversight alerts
— never sit silently "open" at cost basis inside equity.

**Files:** `paper_broker.py` (settlement/proxy path), `dashboard.py` (flag rendering), tests.

**Steps:**

1. Extend the crypto up/down proxy settlement to the hourly/daily slug family
   (`ethereum-up-or-down-july-2-2026-12pm-et` style) by reusing
   `shadow_cohort._crypto_updown_proxy_settlement_price`'s Binance/Coinbase window logic —
   factor that helper out to a shared module rather than duplicating it. Proxy settlement only
   applies when the market's close time has passed and a reference price window is resolvable
   from the slug; otherwise leave the position open.
2. Add `stale_open_position` detection: open paper position whose market close_time (from the
   position row or slug) is more than `paper_trading.stale_open_alert_hours` (default 2) in the
   past and which has neither fresh quotes nor a resolution row. Surface: a list in the broker
   summary, a dashboard warning in the open-positions section, and an oversight alert.
3. Do NOT force-close at cost or at stale marks; fail closed (flag, don't fabricate an exit).
4. Tests: hourly-slug proxy settlement resolves a fixture position with a crafted window price;
   a position with no resolvable window becomes `stale_open_position` and appears in the alert
   list; equity is unchanged by flagging.

**Landed:** crypto up/down proxy settlement is factored into
`crypto_updown_settlement.py` and now supports encoded 5m/15m slugs plus named hourly/daily slugs
such as `ethereum-up-or-down-july-2-2026-12pm-et`. The paper broker settles eligible hourly
crypto up/down positions through the shared proxy when public reference prices are available, and
otherwise reports `stale_open_position` rows for past-close open positions with no fresh executable
quote or clean resolution. The dashboard renders a bad oversight alert and an open-positions
warning table; flagging does not mutate equity, force-close, or fabricate exits. Full suite:
687 tests green.

---

## WO-22 — Evidence-gated display of extrapolated metrics — `done` (2026-07-03)

**Goal:** the dashboard never renders an extrapolation as a fact.

**Files:** `dashboard.py`, dashboard tests.

**Steps:**

1. Everywhere `monthly_run_rate_usdc` renders (promotion watchlist, cohort tables, decision
   targets): when the row's fills/orders are below the promotion policy's `minimum_filled_orders`
   or elapsed evidence is under 72h, render `n/a (N fills, Hh)` instead of the USD figure.
2. CLV section: `beat_close_rate` renders `n/a` when `final_line_positions == 0`; label the
   provisional count clearly ("provisional lines await market close").
3. Algo replay: when the best strategy has 0 fills, render "no strategy beat doing nothing" and
   name the losing strategies with their P&L, instead of "best: null".
3b. **"Best edge route" card (verified worst offender, 2026-07-03):** it renders
   `best_repricing_monthly_run_rate_usdc` / `best_forward_paper_monthly_run_rate_usdc` — the MAX
   across all cohorts of annualised micro-windows. Live example: "forward paper $1,973.93/month"
   was 2 paper round trips totalling +$0.19 on $4 staked inside a 4-minute window
   (paper_elapsed_hours=0.068; 0.19/0.068h*730h), from a cohort whose own total P&L was −$1.67.
   Fix: render the actual evidence — "best paper cohort: +$0.19 on 2 round trips over 4m (n too
   small to annualise)" — and only show a monthly figure when the cohort passes the same
   minimum-fills/72h evidence bar as (1). Apply identically to the shadow repricing figure.
4. Equity/cash tiles and the account P&L line: when `pnl_audit_state` is
   `raw_pnl_contains_quote_conflicts`, append "(raw; audited $X)" using
   `audited_pnl_since_baseline_usdc`. `approved_signal_count` renders as an integer.
5. Tests: fixture payloads asserting each rendering branch (exact strings).

**Landed:** dashboard run-rate renderers now fail closed to `n/a (N fills, Hh)` unless sample count
and observation time clear the evidence bar. CLV beat-close displays `n/a` without final close
lines and labels provisional lines. Algo replay no longer calls the zero-fill null strategy "best"
when real strategies lost money. Raw ledger P&L/run-rate tiles append the audited P&L caveat when
quote conflicts exist, and `approved_signal_count` is forced to an integer. The Best-edge-route card
and decision summary now show actual evidence (P&L, round trips, observed time) and suppress tiny
window monthly extrapolations until the same minimum-fills/72h bar is met. Full suite: 688 tests
green.

---

## WO-23 — Deployment-aware oversight status — `done` (2026-07-03, MEDIUM)

**Goal:** one coherent story about which driver should be running.

**Files:** the oversight/evidence-freshness builder (grep `Shadow research cycle has not
started`), `dashboard.py` Strategy V2 section, tests.

**Steps:**

1. When the legacy live-loop heartbeat is fresh (existing `legacy_full_cycle.effective_status ==
   "live"` logic) and the shadow-cycle status file is absent, replace the warn alert with a
   single info line: "Driver: legacy live loop (VPS deployment); shadow-cycle status file not
   expected." Keep the warn when NEITHER driver is fresh.
2. Strategy V2 section: render "not running in this deployment" instead of "missing" when its
   artifacts are absent but the live loop is fresh.
3. Tests: both alert branches, exact strings.

**Landed 2026-07-03:** the oversight builder now emits the exact VPS-driver info line when the
legacy live loop is fresh and the shadow-cycle file is absent, while preserving the warning when
neither driver is fresh. Strategy V2 now reports "not running in this deployment" when its artifacts
are absent under the fresh legacy-loop deployment, and focused dashboard tests cover both branches.

---

## WO-24 — Activate and broaden the sharp-anchor pipeline — `done` (2026-07-03, HIGHEST VALUE)

**Context:** `docs/POLYMARKET_EDGE_STRATEGY_RESET.md`. The de-vig pipeline exists end-to-end and
has never run (`missing_api_key`). A human must set `THE_ODDS_API_KEY` on the VPS; this WO makes
the pipeline worth running the moment that happens.

**Files:** `polymarket_predictive_config.example.yaml` (`sharp_odds_fetch.sports`),
`sharp_odds_fetch.py`, `sharp_anchor.py`, the VPS loop entry (`run_polymarket_live_paper_loop.py`)
or cycle wiring, tests.

**Steps:**

1. Broaden `sharp_odds_fetch.sports` beyond the WC outright: add h2h for
   `soccer_fifa_world_cup` (match odds), `basketball_nba`, `baseball_mlb`, `mma_mixed_martial_arts`,
   `tennis_atp`/`tennis_wta` if the provider exposes them (check the provider's sports list at
   runtime and skip unknown keys with a logged note — do not crash on one bad key).
2. Budget guard: config `sharp_odds_fetch.max_requests_per_run` (default 5) and
   `fetch_interval_minutes` (default 60); the fetch must be a no-op (status
   `skipped_budget`) when called sooner — free-tier credits are the constraint.
3. Wire `refresh-sharp-anchor` (fetch + de-vig) into the VPS loop / cycle behind the budget guard,
   fail-loud on real errors, `skipped_missing_api_key` status when the env var is absent
   (already the behaviour — keep it).
4. Match-market slug mapping: extend the anchor joiner to map h2h odds onto Polymarket match
   markets (team-name normalisation like the existing WC winner mapping). Unmapped rows are
   reported, never guessed.
5. Tests: provider-response fixtures -> de-vigged fairs; budget guard skip; unknown sport skip;
   missing key skip.

**Out of scope:** alpha thresholds, gates. The anchor feeds `fundamental_probability_paths` which
the alpha layer already consumes with a haircut and cross-check.

**Landed 2026-07-03:** broadened the provider config to World Cup outrights, World Cup match h2h,
NBA, MLB, MMA, ATP, and WTA; added provider sports-list validation, unknown-sport skipping,
`max_requests_per_run`, and `fetch_interval_minutes` budget gating; extended h2h anchor joins so
clear "Will Team beat Team?" YES contracts map to bookmaker team outcomes without guessing NO/draw
rows; added unmapped-row samples; and made sharp-anchor coding errors fail loud in the VPS loop.
Tests cover missing key, provider errors, fallback CSVs, budget skips, unknown sports, h2h joins,
and no-guess unmapped outcomes. Runtime note: GitHub confirms `THE_ODDS_API_KEY` exists as a
sealed secret, but GitHub cannot reveal secret plaintext; the VPS container remains blocked until
that value is populated in the VPS environment.

---

## WO-25 — Wire the dutch-book arb monitor into the loop and dashboard — `done` (2026-07-03, HIGH)

**Goal:** the one strategy class with mechanical (model-free) edge runs continuously and reports.

**Files:** `dutch_arb_monitor.py` (has `run_dutch_arb_monitor`), the VPS loop entry, `dashboard.py`,
tests.

**Steps:**

1. Add a bounded monitor pass to the loop cadence (config `dutch_arb.enabled` default true,
   `max_events_per_pass` default 20, `pass_interval_minutes` default 15): one poll per cadence,
   writing `outputs/polymarket_arb/dutch_arb_latest.json` plus an append-only
   `dutch_arb_opportunities.csv` for anything above `alert_annualised` (default 0.10).
2. Dashboard section "Dutch-book arb watch": last scan time, events scanned, best basket
   (market, sum-of-asks, annualised ROI), count above alert threshold, and the honest caveat
   ("observed ask baskets; execution and fee reality untested — shadow evidence only").
3. Oversight alert (info severity) when a basket persists above the alert threshold across 3+
   consecutive scans — that is a real signal worth a human look.
4. Tests: fixture book -> basket maths exact; persistence alert; empty scan renders cleanly.

**Out of scope:** any order placement. This is a scanner.

**Landed 2026-07-03:** the VPS live-paper loop now runs a bounded one-poll
`run_dutch_arb_monitor` pass on the configured `dutch_arb.pass_interval_minutes` cadence with
`dutch_arb.enabled`, `max_events_per_pass`, and `alert_annualised` controls. The monitor writes
dry-run/latest artifacts under `outputs/polymarket_arbitrage/`, including
`dutch_arb_monitor_summary.json`, `dutch_arb_latest.json`, latest opportunities, append-only
`dutch_arb_opportunities.csv` rows above the alert threshold, and persistent-alert metadata for
baskets that survive 3+ scans. The dashboard now renders "Dutch-book arb watch" plus an info-only
oversight alert for persistent baskets. Tests cover exact basket maths, persistence, loop cadence,
and dashboard rendering. No order placement path was added.

---

## WO-26 — Anti-concentration guard on adaptive collection queries — `done` (2026-07-03, HIGH)

**Goal:** the adaptive research-focus loop can never again collapse discovery into one family.

**Files:** `research_focus.py` (adaptive query injection), liquidity discovery settings plumbing,
tests.

**Steps:**

1. Wherever adaptive collection queries are assembled: classify each query to a family (reuse
   `classify_market_family` on a synthetic row, or a simple keyword map for query strings) and
   enforce `research_focus.max_queries_per_family` (default 2) and
   `research_focus.min_distinct_families` (default 4). Overflow slots go to the configured broad
   base list (world cup, tennis, fed, economy, esports, ai, politics, elections, stocks) in
   round-robin order — deterministic.
2. Crypto up/down specifically: hard cap via `research_focus.max_updown_queries` (default 1) —
   it remains a timing diagnostic, never the majority of collection attention.
3. The research-focus artifact records the pre- and post-guard query lists so the rebalancing is
   auditable.
4. Tests: a feedback fixture that proposes 8 updown queries -> output has 1 updown + broad
   round-robin; distinct-family floor enforced; determinism.

**Out of scope:** gates, model thresholds.

**Landed 2026-07-03:** `build_research_focus()` now writes audited pre/post query lists:
`raw_collection_queries`, guarded `collection_queries`, and `collection_query_guard` with family
counts, rejected-query reasons, up/down count, broad-base fill rows, and the explicit
`decision_use=collection_rebalancing_only_not_trade_authorisation`. Defaults in
`polymarket_predictive_config.example.yaml` enforce `max_queries_per_family=2`,
`min_distinct_families=4`, and `max_updown_queries=1`, with deterministic broad-base fill across
World Cup, tennis, macro, esports, AI, politics, elections, and stocks. Regression tests prove an
8-query crypto up/down proposal becomes one up/down diagnostic plus broad families; CI import-path
tests were also made Linux-safe by loading `scripts/*.py` by path.

---

## WO-27 — Longshot-bias research family on slow markets (shadow-only) — `done` (2026-07-03, MEDIUM)

**Goal:** stand up the structural-bias hypothesis as a first-class shadow research family measured
by CLV, not settlement waiting.

**Files:** new `longshot_bias.py` (pattern: `closing_line.py`), CLI `longshot-bias-scan`, tests.

**Steps:**

1. Scan current liquid markets (from the market snapshot / liquidity watchlist) for YES prices in
   `[longshot_bias.min_price, longshot_bias.max_price]` (defaults 0.02–0.12) with
   `time_to_close_hours >= 168` (slow markets only) and liquidity >= 500. The candidate is the
   **NO side** (buy cheap NO = sell the overpriced tail), which after the WO-20-era collection
   changes will accumulate CLV lines like any shadow position.
2. Emit shadow candidates through the existing shadow-cohort path with cohort
   `structural|longshot_no|<family>` — normal shadow gates apply, nothing is bypassed; stake is
   the standard shadow stake.
3. Fail closed: the scan only nominates; alpha/governance still filter. No new thresholds are
   loosened anywhere.
4. Tests: fixture snapshot -> exact candidate set; deep-longshot exclusion below min_price;
   fast markets excluded.

**Landed 2026-07-03:** Added `longshot_bias.py` plus CLI command `longshot-bias-scan`.
The scanner reads the live liquidity watchlist / websocket targets / prediction snapshot, requires
an actual YES/NO binary pair, filters YES tails to the configured 0.02–0.12 band, slow markets
(`min_time_to_close_hours=168`), and liquidity >= 500, then nominates the NO token with cohort
`structural|longshot_no|<family>`. Artifacts:
`outputs/polymarket_longshot_bias/longshot_bias_summary.json` and
`outputs/polymarket_longshot_bias/longshot_bias_candidates.csv`, both explicitly paper/live false.
The canonical paper cycle now forwards these candidates only into `update_shadow_cohort_evidence`;
they are not written into paper signal generation. Tests cover exact candidate selection,
below-min/fast/thin exclusions, artifact writes, shadow-position emission, and paper-cycle
shadow-only forwarding.

---

## Sequencing

```text
WO-1..WO-6, WO-8, WO-9   done and audited (2026-07-02)

Queue order (strategic reset, 2026-07-03 — read POLYMARKET_EDGE_STRATEGY_RESET.md first):
1. WO-24   done 2026-07-03: sharp-anchor activation + broadening (VPS still needs THE_ODDS_API_KEY populated)
2. WO-20   done 2026-07-03: position-aware quote collection
3. WO-25   done 2026-07-03: dutch-book arb monitor loop/dashboard wiring (mechanical edge, model-free)
4. WO-26   done 2026-07-03: anti-concentration guard on adaptive queries
5. WO-21   done 2026-07-03: settle or flag stuck paper positions
6. WO-27   done 2026-07-03: longshot-bias research family (shadow-only)
7. WO-7    done 2026-07-03: CLV-aware promotion review advisory wiring
8. WO-22   done 2026-07-03: evidence-gated display fixes
9. WO-23   done 2026-07-03: deployment-aware oversight status
10. WO-11  done 2026-07-03: research-focus consumption (after WO-26 so the guard shapes it)
11. WO-12  done 2026-07-03: portfolio VaR + correlated-exposure reporting
12. WO-13  done 2026-07-03: microstructure hypotheses as replay strategies
13. WO-14  generalise the sweep (after WO-13)
14. WO-16  per-family calibration scorecard
15. WO-17  collection coverage report (verifies WO-20)
16. WO-15  evidence history time series
17. WO-18  dashboard evidence funnel
18. WO-19  invariant property tests (zero source changes)
```

After all six land: WP3 is done (flip it in the charter), the algo track (WP9–WP11) is done, and
the remaining charter priority is WP6 (portfolio-level correlated exposure). WP4 (CLV-aware
promotion review), WP5 (depth-based execution costs), WP7 (family classification for liquid
`unknown` markets), and WP8 (edge attribution) have since landed.
