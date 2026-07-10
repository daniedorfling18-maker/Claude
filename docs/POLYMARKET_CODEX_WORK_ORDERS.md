# Polymarket Codex Work Orders

Last updated: 2026-07-05 (WO-1..WO-30 landed; **WO-31 is the only open work order**; WP13 is a venue
decision, not a WO. Crypto up/down is frozen as a diagnostic — see `AGENTS.md`. Read
`docs/POLYMARKET_EDGE_STRATEGY_RESET.md` first.)

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

## WO-14 — Generalise the sweep to any registered strategy — `done` (2026-07-03)

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

**Landed 2026-07-03:** `algo-sweep` now accepts `algo_sweep.strategies` as a generic per-strategy
parameter grid. Each strategy's cartesian product is replayed with plain `algo.*` overrides; the
summary reports one global `selected` combo plus `by_strategy` bests, and the combos CSV now includes
`strategy` and `params` columns. Legacy config without `strategies:` still runs the old
`tight_spread_join_bid_shadow` spread/imbalance grid and preserves the WO-6 assertions. The dashboard
sweep panel now shows selected strategy/params, per-strategy bests, and strategy/params for each
combo.

---

## WO-15 — Evidence history time series (CLV + attribution per cycle) — `done` (2026-07-03)

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

**Landed 2026-07-03:** added `evidence-history`, which appends idempotent rows to
`evidence_history.csv` from `closing_line_value.json`, `edge_attribution.json`, and
`algo_sweep_summary.json`. `refresh-governance` now appends history immediately after rebuilding those
three artifacts. The row timestamp is the source artifact's `generated_at_utc`, so repeated refreshes
do not duplicate unchanged evidence.

---

## WO-16 — Per-family calibration scorecard — `done` (2026-07-03)

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

**Landed 2026-07-03:** added `family-calibration`, which joins clean settled predictions through the
existing market-relative validation path, classifies each row with the shared market-family
classifier, and writes `family_calibration_scorecard.json/.csv`. The scorecard reports Brier/log-loss
versus the market by family, clustered bootstrap confidence intervals for Brier gain, and fail-closed
evidence classes (`model_beats_market`, `market_beats_model`, or
`insufficient_calibration_evidence`). It is diagnostic only and keeps paper/live flags false.

---

## WO-17 — Websocket collection coverage report — `done` (2026-07-03)

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

**Landed 2026-07-03:** added `collection-coverage`, which reads websocket quote features and shadow
positions, reports family-level quote coverage/gaps, and lists positions missing a quote in the
pre-close window. It writes `collection_coverage.json`, plus CSVs for family coverage and missing
positions. This turns stale/provisional CLV into concrete collection targets without invoking paper or
live trading.

---

## WO-18 — Dashboard evidence funnel panel — `done` (2026-07-03)

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

**Landed 2026-07-03:** added a top-level dashboard "Evidence funnel" section and a testable
`evidence_funnel` payload. It surfaces liquidity target coverage, shadow candidates, open/closed
shadow positions, final CLV rows, attributed positions, attribution-class counts, positive CLV
cohorts, model-beats-market families, pre-close quote gaps, sweep decision, paper gate status, and
recent evidence-history rows. `refresh-governance` now rebuilds family calibration and collection
coverage before rendering the dashboard so this section stays fresh.

---

## WO-19 — Invariant property tests — `done` (2026-07-03)

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

**Landed 2026-07-03:** the first execution-cost invariant check exposed a real safety gap:
known shallow depth produced a higher cost than no depth at all. Before landing the test-only
invariants, the estimator was hardened conservatively so missing depth now applies a depthless
slippage penalty and a zero acceptable-impact stake cap, and the risk layer treats a zero impact
cap as binding. `tests/polymarket_predictive_engine/test_safety_invariants.py` now pins Kelly
shrinkage monotonicity, execution-cost conservatism, the risk sizing envelope, and order-intent
schema safety over 200 seeded samples.

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

## VPS dashboard audit — 2026-07-03 (RESOLVED — historical)

A live-dashboard audit against main filed four findings, all since fixed (the detailed root-cause
writeup was removed as stale):

1. Collection did not follow open positions → **WO-20** (position-aware quote collection).
2. Raw vs audited P&L surfaced without a caveat → **WO-22** (evidence-gated display).
3. Evidence-free extrapolations rendered as facts → **WO-22**.
4. Shadow-cycle vs legacy-live-loop deployment-mode confusion → **WO-23**.

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
needs `THE_ODDS_API_KEY` visible inside the VPS container. The manual deploy workflow can inject the
sealed GitHub secret into the VPS `.env`; the current runtime blocker is SSH deploy access, not the
odds-key code path.

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
and no-guess unmapped outcomes.

**Superseded in part by WO-30 (2026-07-05):** the per-game h2h broadening to `basketball_nba` /
`baseball_mlb` / `mma_mixed_martial_arts` / `tennis_atp` / `tennis_wta` was reverted — Polymarket
lists no per-match markets for those, so they mapped to nothing and burned Odds API budget. Still
live from WO-24: pipeline activation, provider sports-list validation, unknown-sport skipping,
`max_requests_per_run`/`fetch_interval_minutes` budget gating, and the h2h join machinery (kept for
WC h2h → the WO-29 composite). Runtime blocker resolved: `PM_VPS_SSH_PRIVATE_KEY` is populated and
the VPS deploy lane runs green.

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

## WO-28S — Smart-flow CLV watchlist for public wallet fills — `done` (2026-07-03, MEDIUM)

_(Renumbered 2026-07-05 from a duplicate "WO-28". The h2h anchor→token join below keeps `WO-28`
because WO-29 builds on it by number.)_

**Goal:** add the first "follow proven flow" research lane from
`docs/POLYMARKET_STRATEGY_OPTIONS.md`: score public wallet fills by CLV, then watch only wallets
that repeatedly beat later market lines.

**Files:** `smart_flow_clv.py`, CLI, governance refresh, dashboard, config, tests.

**Constraints:** diagnostic only. No paper/live order generation, no promotion-gate change, no
threshold loosening. Positive wallets are research targets only.

**Landed 2026-07-03:** Added `smart_flow_clv.py` plus CLI command `smart-flow-clv`. The builder
reads configured public wallet fills (`inputs/polymarket/public_wallet_fills.csv` by default),
joins each buy fill to existing websocket quote history, computes CLV using the same
settlement-independent line standard as `closing_line.py`, and aggregates by wallet and wallet
cohort with bootstrap CIs. It writes
`outputs/polymarket_model_governance/smart_flow_clv.json` and
`smart_flow_clv_positions.csv`, both explicitly `paper_trading_invoked=false` and
`live_trading_invoked=false`. Governance refresh now rebuilds it, and the dashboard renders a
"Smart-flow CLV" watchlist. Tests cover positive/negative wallet classification, empty input
fail-closed behavior, dashboard rendering, and refresh ordering.

---

## WO-28 — Join h2h sharp anchors to Polymarket match tokens — `done` (2026-07-04, CRITICAL)

**Live evidence (2026-07-04 07:04Z dashboard):** the sharp-anchor pipeline works end-to-end —
102 odds rows fetched from Pinnacle/Betfair, 40 markets de-vigged — but `direct_token_joins: 0`
and **`skipped_no_token: 86`**: every h2h match row (e.g. `canada-vs-morocco`,
`paraguay-vs-france` — the LIVE World Cup knockout matches) is dropped as
`unmapped_sharp_anchor_row`. The 16 surviving fundamental rows are WC-winner outrights only.
The knockout match markets are the most liquid sports markets on Polymarket right now; this
join gap is the single blocker between the anchor and real candidates.

**Files:** `src/polymarket_predictive_engine/sharp_anchor.py` (the joiner), tests.

**Steps:**

1. Build a match-market index from the token map (`outputs/polymarket/market_snapshot.csv`) plus,
   when enabled, the same public-search path already used for WC-winner mapping: for each
   Polymarket market, extract candidate team names from slug + question via
   `canonical_team_key`-style normalisation (reuse the WC mapper's normaliser; do not write a new
   one), and index by (team_key_a, team_key_b) unordered pair + close-time day.
2. Join each unmapped h2h anchor row by team pair + date window (default +/- 1 day around the
   sharp market's commence date). Map outcomes conservatively:
   - a Polymarket binary market whose question names exactly one of the two teams as winner
     ("Will Canada beat Morocco?", "Canada to win") -> that team's fair probability maps to YES;
   - a draw-specific market ("Draw", "Match drawn") -> the Draw fair maps to YES;
   - anything ambiguous (handicaps, totals, advance/qualify markets — "to advance" includes
     penalties and MUST NOT take the 90-minute h2h fair) stays unmapped with reason
     `ambiguous_market_shape`.
3. "Advance/qualify" is the trap: knockout Polymarket markets are often "to advance", which is
   win+draw-then-penalties. Only map the 90-minute h2h fair to markets that are clearly
   90-minute results; report `advance_market_needs_composite_fair` for the rest (a composite
   fair from h2h + draw split is a later WO, not this one).
4. Keep every unmapped row in `skipped_no_token_samples` with its reason — the fail-closed
   reporting that exposed this gap must keep working.
5. Tests: fixture snapshot rows + anchor rows -> exact expected joins; ambiguous/advance market
   stays unmapped with the right reason; team-name normalisation cases (accents, "USA"/"United
   States", "Ivory Coast"/"Cote d'Ivoire").

**Out of scope:** alpha thresholds, haircuts, gates; composite advance-market fairs.

**Landed 2026-07-04:** H2H sharp anchors now join conservatively to Polymarket match tokens from
both the configured local token map and the optional public-search enrichment. Local snapshots can
map clear binary "Will Team beat Team?" YES rows, three-way "Who will win Team vs Team?" rows, and
draw rows without a network lookup. Advance/qualify/handicap/total market shapes remain fail-closed;
advance rows report `advance_market_needs_composite_fair` instead of generic no-token skips. Tests
cover direct local H2H joins, public-search H2H joins, three-way draw markets, and the advance-market
trap.

---

## WO-29 — Conservative composite fair for knockout advance markets — `done` (2026-07-04, CRITICAL)

**Why:** WO-28 correctly refused to map 90-minute h2h prices directly onto Polymarket
advance/qualify markets. The live dashboard then exposed a second bottleneck: many liquid knockout
markets still had a usable YES token but no usable independent anchor because the wording was "to
advance." Dropping all of them made the sharp-anchor lane too narrow.

**Landed 2026-07-04:** `sharp_anchor.py` now builds a labelled composite only when the full
three-way h2h fair exists and the local token map exposes an explicit advance YES token:

```text
advance fair = regulation-win fair + draw fair * advance_composite_draw_split
```

The default split is neutral/conservative (`0.50`) and is clamped to `[0, 1]`. Output rows are
tagged as `anchor_type=advance_composite_from_h2h` with the regulation-win probability, draw
probability, draw-share assumption, source suffix, and target Polymarket question. Missing draw legs
still fail closed with `advance_market_needs_composite_fair_missing_draw`. This does not loosen
paper/live gates or sizing; it only turns previously skipped sharp-anchor evidence into an auditable
candidate input for the existing alpha haircuts and forward paper proof loop.

**Files:** `src/polymarket_predictive_engine/sharp_anchor.py`,
`polymarket_predictive_config.example.yaml`, tests.

---

## Market-structure finding — 2026-07-04/05 (verified against Polymarket's live Gamma API)

Direct API query settles the sharp-anchor join question. Polymarket **does not list per-match /
per-game sports markets** — the fixtures the anchor kept dropping (`usa-vs-belgium`,
`switzerland-vs-colombia`, `atlanta-braves-vs-new-york-mets`) all return zero markets. What
Polymarket lists per sport is **outrights / futures / advance markets only** (World Cup Winner,
Group/continent/advance, MLB World Series Champion, NBA 2027 Champion, awards). The per-game h2h
fetches for NBA/MLB/MMA/tennis therefore mapped to nothing and burned Odds API budget every cycle.

## WO-30 — Refocus sharp_odds_fetch on markets Polymarket actually lists — `done` (2026-07-05, HIGH)

Landed: `sharp_odds_fetch.sports` now fetches only mappable markets — keep
`soccer_fifa_world_cup_winner` (outrights) + `soccer_fifa_world_cup` (h2h, feeds the WO-29 composite
advance fair); swap NBA/MLB per-game h2h for `basketball_nba_championship_winner` /
`baseball_mlb_world_series_winner` (outrights); remove `mma_mixed_martial_arts` / `tennis_atp` /
`tennis_wta` h2h (no mappable Polymarket market). `validate_sports` skips any futures key the
provider does not expose, so unknown keys are logged-and-skipped, never fatal. Test:
`test_example_config_fetches_only_polymarket_mappable_markets`. No gate/threshold/de-vig change.

## WO-31 — Per-sport anchor coverage reconciliation and auto-trim signal — `done` (2026-07-05, PR #65, MEDIUM)

**Goal:** measure per-sport join rate over cycles and surface sports that produce zero token joins
across N cycles, so the fetch list is trimmed on evidence, not intuition.

**Files:** new `src/polymarket_predictive_engine/sharp_anchor_coverage.py` (pattern:
`closing_line.py`), CLI `sharp-anchor-coverage`, wire into `refresh_governance` after
`build_sharp_anchor`, a dashboard line reusing the existing `coverage_by_sport_market` block if
present, tests.

**Steps:** per configured sport record rows_fetched / rows_mapped / join_rate; append one row per
sport per run to `outputs/polymarket_model_governance/sharp_anchor_coverage_history.csv` (idempotent
on the anchor artifact `generated_at_utc`); classify fail-closed `mappable` (join_rate>0 this run) /
`no_mappable_market` (0 joins across `sharp_anchor_coverage.zero_join_cycles_before_flag`, default 5)
/ `collecting_coverage_evidence`; `no_mappable_market` is a **recommendation string only**, never an
automatic config edit. Standard `paper_trading_invoked`/`live_trading_invoked` false flags.

**Landed 2026-07-05 (PR #65):** `sharp_anchor_coverage.py` + CLI `sharp-anchor-coverage`, wired into
`refresh-governance`. Writes `sharp_anchor_coverage.json` and idempotent
`sharp_anchor_coverage_history.csv`; classification is fail-closed and `no_mappable_market` is a
recommendation string only (verified: the module never writes config, mutates gates, or touches
env). The same PR added a dashboard **proof-questions overlay** (`dashboard_proof_questions.py`,
`proof_questions` payload key) answering the four go/no-go questions — (1) sharp-anchor rows mapped?
(2) dutch-arb persistent opportunities? (3) focus-view CLV positive with enough samples? (4) audited
paper P&L positive after governed probes? — plus a scheduled `polymarket-vps-proof-health` workflow
(every 6h) that alerts on stale artifacts, bad proof status, or `sharp_fetch_health=attention`. No
gate/threshold/live-path change; full suite green (552).

## WP13 (decision, not a work order) — per-match sports belongs on Kalshi/Betfair

The market-structure finding means the per-match sharp-anchor edge cannot be harvested on Polymarket
at all. If per-match is where the edge is believed to be, that is a **venue** decision — stand up a
Kalshi collector feeding the same normalised schema (`docs/POLYMARKET_STRATEGY_OPTIONS.md` option 4).
Left as a decision, not an open WO; spec it only on an explicit go.

---

## Status (2026-07-05)

Runtime: `PM_VPS_SSH_PRIVATE_KEY` is populated and the VPS deploy lane runs green (the deploy
workflow is hardened with SSH keepalives). Crypto up/down is frozen as a diagnostic — see the
focus-discipline rule in `AGENTS.md`.

- **Done:** WO-1 .. WO-27, WO-28 (h2h anchor→token join), WO-28S (smart-flow CLV watchlist),
  WO-29 (composite advance fair), WO-30 (sharp fetch refocused onto mappable markets),
  WO-31 (per-sport anchor coverage + proof-questions overlay + scheduled proof-health check, PR #65).
- **Open:** none. Every filed work order has landed.
- **Decision, not a WO:** WP13 (per-match sports edge belongs on Kalshi/Betfair; spec only on an
  explicit go).
- **Next evidence, not code:** let the VPS accumulate the four proof-question signals over ~a week
  (sharp-anchor coverage, dutch-arb persistence, focus-view CLV, audited paper P&L). The
  proof-health workflow now watches these automatically.

WP-level status lives in the charter (`docs/POLYMARKET_QUANT_MODE_CHARTER.md`): WP3/WP4/WP5/WP6/
WP7/WP8 and the algo track (WP9–WP11) have all landed.

---

## WO-33 — Wire the resolved-market corpus into the trainer (leakage-reviewed)

Filed 2026-07-09. The training-data audit found the harvest machinery parked and
the live feature substrate being destroyed on retention roll-off. Both are fixed
operationally (features now archive to `outputs/polymarket_training_archive`
before deletion; the VPS ops scheduler runs `backfill-resolved-markets` +
`collect-price-history` daily — free, key-less, outcome-labelled sequences
across thousands of resolved markets). What remains is MODEL work, not plumbing:

1. Build a leakage-safe training-set assembly from the harvested corpus:
   features strictly point-in-time from price history; labels from resolution
   outcomes only at/after market close; explicit time-based splits per market.
2. Use the corpus to attack the two standing blockers with evidence:
   the validation gap (positive out-of-sample executable examples) and
   cohort transfer (train/validate across DIFFERENT market categories).
3. Report through the existing gates. No gate, stake, or promotion threshold
   changes; the corpus earns its way in through validation metrics or not at all.

Also filed from the same audit, smaller: trade-print (time & sales) collection
for signed-flow features and empirical fill/slippage modelling (sharpens verdict
Gate B), and a guarded partial re-widening of live collection
(assets 60→90, retention 72→96h) now that the cgroup memory guard is active.

---

## WO-34 — Event-group structure features (sum-to-one violations)

Filed 2026-07-09. Gamma groups related outcomes into events; the outcomes of
one event obey a bounded-sum constraint. Transient violations across a group
(sum of asks < 1 already caught by dutch-arb; softer mid-price inconsistencies
are NOT) are an unexploited mispricing family. Build event-group joins in the
feature layer and expose group-consistency features to the model. No gates or
stakes change.

**Detection layer SHIPPED 2026-07-09** (`event_group_consistency.py`,
`scan-event-groups`, 15-min VPS cadence with the trade-prints job): scans the
top negRisk event groups, charges live taker fees per leg, appends net
deviations > 0.2c/basket to a persistence ledger. First live scan: 83 groups,
one flagged - a zero-fee politics group at +4.5c/basket on the buy-all-YES
side. Open question the ledger now answers: are deviations persistent and
deep enough to be an edge class, or stale-quote mirages? Feature wiring into
the model remains open (leakage review with WO-33).

## WO-35 — Wallet intelligence (smart-money positioning)

Filed 2026-07-09. Polymarket positions are public on-chain: the data-API
holders/positions endpoints plus the full historical trade tape allow per-wallet
PnL attribution. Spec: (1) build a wallet ledger from resolved markets;
(2) rank wallets by realised risk-adjusted PnL out-of-sample; (3) expose
smart-wallet net positioning per market as a feature. The most venue-specific
edge available; largest build; collection-first like WO-33, model wiring only
through the existing validation gates.

---

## WO-36 — Liquidity-rewards making lane (actuarial evaluation first)

Filed 2026-07-09 from the Polymarket API-docs deep read. Two facts invert the
taking-vs-making economics for a $100/month goal:

1. Takers now pay fees: rate x p x (1-p) per share (sports 3%), i.e. ~1.0-1.5%
   of turnover at mid prices. The verdict engine charges this as of amendment 4.
2. Makers pay NOTHING and earn daily liquidity rewards (quadratic in-spread
   scoring, paid daily at 00:00 UTC, min payout $1). Reward configs per market
   (rate_per_day, rewards_min_size, rewards_max_spread) are public and free:
   GET https://clob.polymarket.com/rewards/markets/multi (no auth, paginated).

Spec, evaluation BEFORE any build: (1) daily job pulls reward configs and sizes
the opportunity actuarially - total daily reward pool on liquid markets, min
size, max spread, implied competition from the book; (2) paper-simulate a
two-sided quoting strategy against recorded books + trade prints (inventory
risk = adverse fills near events; this is the real cost); (3) verdict-style
gate: expected rewards minus expected adverse-selection loss must be positive
with the same statistical discipline as the taking lane. No live orders; the
existing governance gates stay in charge.

**Step (1) SHIPPED 2026-07-09** (`maker_carry_study.py`, `maker-carry-study`,
daily VPS cadence with the training harvest): live universe scan + book
competition (quadratic score inside the band) + dual-window pick-off charge
(worse of 24h@1min and 7d@10min mids) + capital-capped sized portfolio.
First live run: ~$45k/day of reward pots across 132 markets; trusted sized
portfolio $6.30/day (~$189/month upper bound) on $443 capital, driven by the
Fed July-meeting market. Two failure modes are guarded by construction (both
observed live): thin in-game books faking 40-86% reward shares (untrusted
above 5% share), and calm-24h windows hiding news gaps (LeBron market:
$0/day fast window vs $11.67/day slow window - the worse window is charged).
The daily history ledger tracks stability. Steps (2)-(3) remain open and are
required before ANY quoting behaviour is even paper-simulated.

**Steps (2)+(3) SHIPPED 2026-07-09 (same day, second pass)**: (2) empirical
markout charge - every executed print that swept through the hypothetical
quote level is marked out at +5 minutes, queue-share weighted against resting
band depth; the candidate is charged the WORST of bar windows and markout.
$1/day payout floor enforced in sizing (sub-floor accruals pay nothing).
(3) pre-registered maker gates (2026-07-09T13:00Z): M-A carry evidence
(>= 7 daily runs at target incl. latest), M-B adverse realism (every
portfolio market markout-MEASURED), M-C payout floor (by construction).
All-pass yields ``evidence_supported_pending_human_decision`` - never an
order. A daily ``maker_quote_sheet.md`` (research output, standing rules,
event-risk flags) lets a HUMAN act outside the system if the verdict
supports it; the repo stays paper-only regardless. First live run of the
upgraded study: $11.76/day net (~$353/month upper bound) on $490, M-B pass,
M-A 1/7 - earliest supported verdict ~2026-07-16.

Also from the docs read, smaller: batch endpoints (/books, /prices,
/midpoints, batch prices-history) to cut poller overhead; klines endpoint;
sports websocket channel (real-time scores - could reduce Odds API dependence
for in-window pick timing); rate limits confirmed generous for all our pollers.

---

# Codex execution batch — filed 2026-07-10

**Standing constraints for EVERY work order below (non-negotiable):**

1. Paper/dry-run only. No order placement, amendment, or cancellation code of
   any kind; no CLOB auth flows. `paper_trading_invoked` /
   `live_trading_invoked` are always `False` in every summary artifact.
2. Never loosen a gate, threshold, stake, or registered amendment. Tightening
   requires a dated comment explaining why it tightens.
3. Free key-less public endpoints only (Gamma / data-API / CLOB read paths).
   Respect rate limits: sleep >= 0.1s between requests; never parallel-hammer.
4. Follow the collector template (`trade_print_collector.py` /
   `maker_carry_study.py`): `_settings()` merged from a config block with
   defaults, summary JSON always written (status `disabled` when off),
   append-only CSV ledgers with dedup + row caps rolling into the training
   archive, `main(config_path)` entrypoint, CLI command registered in
   `cli.py`, VPS cadence via `scripts/run_vps_ops_scheduler.sh` (piggyback an
   existing job; do NOT add new scheduler intervals), tests in
   `tests/polymarket_predictive_engine/` with monkeypatched `requests`.
5. `python -m pytest -q` green before any PR. Config example additions to
   `polymarket_predictive_config.example.yaml` with a dated comment.
6. Every STUDY work order (anything that estimates an effect from data) must
   ship a planted-truth test: a synthetic dataset with a KNOWN effect that
   the estimator must recover within tolerance, and a null dataset (pure
   martingale / unbiased coin) on which it must flag NOTHING. A backtest
   harness is not trusted until it fails to find edges that do not exist.
7. Multiple-testing registry (registered 2026-07-10, before any new-lane
   data): the edge-class family currently under test is {sharp-anchor taker,
   event-group partitions, maker carry, implication networks, calibration
   bias, drift term-structure}. Any study claiming an effect uses
   Benjamini-Hochberg FDR at 10% ACROSS the bins/lanes it scans, and states
   its minimum sample floor up front. Scanning many bins and reporting the
   best one raw is how false edges are manufactured; it is prohibited.

## WO-37 — Wallet-intelligence collection lane (holders + leaderboard)

Status: LANDED by Codex on 2026-07-10. Artifacts:
`outputs/wallet_intelligence/leaderboard_history.csv`,
`outputs/wallet_intelligence/holders_history.csv`, and
`outputs/wallet_intelligence/wallet_intelligence_summary.json`. CLI:
`collect-wallet-intel`. Collection only; no paper/live trading path invoked.

The build order for WO-35's data layer. The only unconsumed API streams with
real alpha content are data-API `holders` and `leaderboard`: rank wallets by
realized PnL, then observe where proven winners are positioned vs the crowd.
Collection ONLY - no features, no model wiring (that stays WO-35/WO-33 with
leakage review).

Spec:
1. Module `wallet_intelligence_collector.py`, CLI `collect-wallet-intel`,
   config block `wallet_intelligence:` (enabled, max_markets 40,
   top_holders_per_market 20, leaderboard_limit 100, max_ledger_rows 200000).
2. Daily leaderboard snapshot: `GET data-api.polymarket.com/leaderboard`
   (probe `window`/`rankType` params defensively; record raw fields
   wallet/rank/pnl/volume + snapshot stamp) -> append-only
   `outputs/wallet_intelligence/leaderboard_history.csv`.
3. Holder snapshots for tracked markets (reuse `_tracked_markets` pattern
   from trade prints - the websocket feature table): `GET /holders?market=
   {condition_id}` -> per-market top-N holders (wallet, outcome, size) ->
   append-only `holders_history.csv`, dedup by (date, market, wallet).
4. Summary JSON: markets polled, wallets seen, overlap count between
   current holders and latest leaderboard top-100 (the first smart-money
   scalar, reporting only).
5. Cadence: piggyback `run_training_harvest` (daily). Tests: endpoint fakes,
   dedup, cap-roll, disabled mode, no-trading invariants.

## WO-38 — Executable-depth capture for flagged event-group deviations

Status: LANDED by Codex on 2026-07-10. The event-group ledger now carries
`executable_basket_usd`, `depth_weighted_net`, and `book_fetch_ok`, and the
summary reports `flagged_with_executable_depth` plus
`max_executable_basket_usd`. Measurement only; no gate/threshold/order changes.

WO-34's detector found a +4.5-5.0c/basket deviation persisting >= 20h on a
zero-fee politics group. Detection reads Gamma best bid/ask only; the open
question is DEPTH - was the basket executable at size, or is it a $5 mirage?

Spec:
1. Extend `event_group_consistency.py`: for each FLAGGED event only (never
   the full scan set), fetch the CLOB `GET /book?token_id=` for every leg and
   compute `executable_buy_all_yes_usd`: walk each leg's ask side to the
   worst price that keeps the basket net-positive after fees; the basket size
   is the min across legs; record depth-weighted net edge and max basket
   dollars.
2. New ledger columns: `executable_basket_usd`, `depth_weighted_net`,
   `book_fetch_ok`. Old rows keep empty values (append-only schema growth).
3. Summary gains `flagged_with_executable_depth` and
   `max_executable_basket_usd`.
4. Same cadence (rides trade_prints job). Tests: synthetic books where (a)
   depth confirms the edge, (b) thin books kill it after the first $10.

## WO-39 — Open-interest and market-quality ride-along

Status: LANDED by Codex on 2026-07-10. `trade_print_collector.py` now appends
`outputs/polymarket_trade_prints/open_interest_history.csv` during the same
collection loop and reports `oi_markets_captured`/`oi_errors` in the trade
print summary. Missing OI endpoints are tolerated and cannot fail the print job
by themselves.

Cheap context features for every lane. Data-API open interest + Gamma
liquidity/volume fields for the markets we already track.

Spec:
1. Extend `trade_print_collector.py` (same request loop, zero new jobs):
   after prints, `GET data-api.polymarket.com/oi?market={condition_id}`
   (probe the exact path/params defensively; if unavailable, record miss and
   move on) -> append `open_interest_history.csv` (stamp, market, oi).
2. Summary gains `oi_markets_captured`. Tests: fake payloads, endpoint-miss
   tolerance (collector must never fail the job over a missing OI endpoint).

## WO-40 — Maker fill realism: replay against the recorded book archive

Status: LANDED by Codex on 2026-07-10. CLI `maker-fill-replay` writes
`outputs/maker_carry/maker_fill_replay.json` from archived/live websocket
features and trade prints, using last-in-queue fill logic and +5/+15/+60
markouts. It reports realism only and does not auto-modify the study or gates.

Strengthens WO-36's M-B gate before any human size-up decision. The markout
charge assumes fills at our quote whenever a print crosses the level; the
websocket book archive (the only order-book history that exists) lets us
replay actual book states and measure queue-position realism.

Spec:
1. Module `maker_fill_replay.py`, CLI `maker-fill-replay`, config block
   `maker_fill_replay:` (enabled, max_markets 10, replay_days 7).
2. For markets in the current quote-sheet portfolio, load archived websocket
   features (gzip archive + live CSV), reconstruct per-minute best bid/ask
   and depth-at-level; simulate our resting quote (size and distance from
   the quote sheet) with LAST-in-queue priority: a fill requires traded
   volume at our level (from `trade_prints.csv`) to EXCEED the resting depth
   ahead of us. Mark every simulated fill out at +5/+15/+60 minutes.
3. Output `outputs/maker_carry/maker_fill_replay.json`: simulated fills/day,
   markout per fill by horizon, implied adverse $/day - reported NEXT TO the
   study's charge with a `realism_ratio` (replay / study). Ratio > 1 means
   the study undercharges; the study is NOT auto-modified (tightening
   proposals go through a dated amendment, per constraint 2).
4. Daily cadence with the harvest. Tests: synthetic archive slices proving
   queue-ahead logic (no fill when depth ahead absorbs the print; fill when
   volume exceeds it), horizon markouts, absent-archive tolerance.

## WO-41 — Implication-network arbitrage scanner (generalises WO-34)

Status: IMPLEMENTED by Codex in PR branch `agent/wo-41-implication-networks`
on 2026-07-10. Artifacts:
`outputs/implication_consistency/implication_deviations.csv` and
`outputs/implication_consistency/implication_scan.json`. CLI:
`scan-implication-networks`. Measurement only; no paper/live trading path
invoked.

Sum-to-one partitions are the trivial case. Binary markets on logically
linked events must satisfy Frechet-Boole inequalities, and Polymarket runs
dozens of linked families simultaneously (verified live 2026-07-10: Winner /
Nation To Reach Semifinals / Finals Exact Matchup / continent-winner all
trade at once). Every violated inequality is a model-free spread with a
known worst case.

Spec:
1. Module `implication_consistency.py`, CLI `scan-implication-networks`,
   config block `implication_networks:` (enabled, event_pages 3,
   deviation_threshold_per_basket 0.005, max_ledger_rows 100000).
2. Build the linkage graph from Gamma events + question parsing (reuse the
   team-key helpers in `sharp_anchor.py`). Checks, all net of per-leg taker
   fees (reuse the WO-34 fee model):
   a. Monotone chains per team T: P(T wins) <= P(T reaches final)
      <= P(T reaches SF). Violation trade: buy the cheap senior leg / the
      implied spread; record both directions.
   b. Aggregation identities: P(continent wins) vs sum of member-nation
      win markets (equality both directions for exhaustive members).
   c. Matchup consistency: sum over Y of P(final = T vs Y) vs
      P(T reaches final).
3. Append-only deviations ledger with persistence stamps (same shape as
   WO-34); summary JSON with best net violation per class.
4. Cadence: rides the trade_prints 15-min job. Tests: planted violations of
   each class recovered; consistent synthetic families flag NOTHING
   (constraint 6); malformed/partial families skipped, never crash.

## WO-42 — Calibration-curve harvesting (favorite-longshot bias)

The most replicated anomaly in binary markets: longshots overpriced, heavy
favorites underpriced. The fee schedule is an accelerant on the favorite
side: fee/dollar = rate x (1-p), so a 90c favorite costs ~0.3c to buy while
documented FLB at that end runs 1-3c.

Spec:
1. Module `calibration_bias_study.py`, CLI `calibration-bias-study`, config
   block `calibration_bias:` (enabled, price_horizons_hours [24, 72],
   min_markets_per_bin 200, price_bins [0.05..0.95 by 0.05], fdr_alpha 0.10).
2. Corpus join: resolved outcomes (`outputs/polymarket_training/
   market_resolutions.csv`) x price at each horizon before close (from the
   harvested CLOB price histories). Skip markets without both.
3. Fit isotonic regression price -> realised frequency per category and
   horizon; bootstrap CIs CLUSTERED BY MARKET (never by row - correlated
   tokens are one observation, same discipline as Gate A).
4. Report: curve tables; bins where the CI excludes the diagonal AFTER
   BH-FDR across all bins (constraint 7) AND the deviation exceeds the full
   taker cost stack (fee + 0.5c exit + 0.5c adverse). Those bins - if any -
   are candidate edges for a future pre-registered lane; the study itself
   trades nothing.
5. Planted-truth tests (constraint 6): synthetic corpus with a known bias
   curve recovered within tolerance; unbiased synthetic corpus yields zero
   flagged bins. Daily cadence with the harvest.

## WO-43 — Martingale drift scan (term-structure of returns)

Under efficiency, prices are martingales. Any systematic drift conditional
on (price level, time-to-close, category) is a timing edge for BOTH lanes:
entries for the taker, quote-skew for the maker.

Spec:
1. Module `drift_scan_study.py`, CLI `drift-scan`, config block
   `drift_scan:` (enabled, horizons_hours [24, 48], price_bins as WO-42,
   time_to_close_bins_hours [24, 72, 168], min_markets_per_bin 150,
   fdr_alpha 0.10).
2. Input: harvested price histories with close times. Estimate
   E[dP | price bin, time-to-close bin, category] with cluster-robust
   errors (cluster by market).
3. Report bins where drift CI excludes 0 after BH-FDR AND |drift| exceeds
   the taker cost stack. Study only; no lane, no trades.
4. Planted-truth tests: synthetic martingale corpus flags NOTHING (this is
   the critical false-positive control); planted 2c drift recovered within
   tolerance. Daily cadence with the harvest.

# Codex batch 2 — filed 2026-07-10 (post docs-assimilation + MM research)

Sources: docs/POLYMARKET_API_ASSIMILATION.md and
docs/MARKET_MAKING_MODELS_RESEARCH.md. All batch-1 standing constraints
(1-7 above) bind every order below, plus one more:

8. REGISTRATION INTEGRITY: the maker gates (M-A/M-B/M-C) and the taker
   verdict gates are FROZEN as registered. Orders below that measure new
   income or refine models must publish their numbers as SUPPLEMENTARY
   fields next to the registered metric - never fold them into the value a
   gate reads. Loosening-by-enrichment is still loosening.
   Process reminder: ONE work order per PR (WO-37..40 landed as a single
   direct commit - do not repeat that).

## WO-44 — Official order-book history: upgrade replay + depth analytics

The 2026-07-10 assimilation live-verified `GET clob.polymarket.com/
orderbook-history` (params: `market` or `asset_id`, `startTs`, `limit`
<= 1000; returns timestamped full bid/ask ladders; 100k+ snapshots observed
for one WC token). This supersedes the websocket archive as WO-40's data
source: complete coverage, no gaps from our collector restarts.

Spec:
1. Extend `maker_fill_replay.py` with a `book_source` setting
   (`archive` | `official` | `both`, default `both`): fetch official
   snapshots for the replay window via cursor/timestamp pagination, replay
   fills against BOTH sources, and report `realism_ratio` per source plus a
   `source_agreement` diagnostic (fills/day divergence between archive and
   official books).
2. New collector function (same module) `snapshot_official_books`: for
   quote-sheet portfolio markets, pull the last `replay_days` of official
   book history into `outputs/maker_carry/official_books/{condition_id}.csv.gz`
   (dedup by timestamp+hash; cap via the training-archive roller).
3. Respect rate limits: sleep >= 0.1s/page; markets capped at 10.
4. Tests: paginated fake with `hash` dedup, both-source replay agreement,
   absent-endpoint tolerance (must degrade to archive silently).

## WO-45 — Supplementary maker income: rebates + holding rewards

The canonical docs add two maker income streams the carry study omits
(conservative today, but the funding decision deserves the full picture):
(a) maker REBATES - 15% (sports) to 25% (politics/finance/other) of the
taker fee generated when OUR resting order is filled, paid daily, $1 floor;
(b) HOLDING rewards - 4% annualized on position value, sampled hourly.

Spec (constraint 8 applies - supplementary only):
1. In `maker_carry_study.py`, per candidate compute
   `supplementary_rebate_usd_per_day` = expected fills/day (band-crossing x
   queue share, already computed) x fee_equivalent per fill
   (C x category feeRate x p x (1-p)) x category rebate share. Category and
   feeRate from the market's `feeType`/`feesEnabled`/fee table
   (docs/POLYMARKET_API_ASSIMILATION.md).
2. `supplementary_holding_usd_per_day` = 0.04/365 x capital_usd.
3. Both appear in candidates CSV, summary, and quote sheet as
   "uncounted income (not in gates)". `portfolio_net_carry_usd_per_day`
   and the M-gates remain EXACTLY as registered.
4. Tests: category rate table lookup, zero for fee-free geopolitics, gates
   unchanged with/without supplementary fields.

## WO-46 — Reward-share model fidelity (published scoring rule)

Our share model approximates the pool as min(bid_score, ask_score) of the
SAME token's book. The published rule (docs/POLYMARKET_API_ASSIMILATION.md,
liquidity rewards): scores aggregate across the market AND its complement
(bids on m + asks on m'), sample EVERY MINUTE, and inside mid [0.10, 0.90]
single-sided liquidity scores at 1/3 (Q_min = max(min(Q1,Q2),
max(Q1,Q2)/c), c = 3.0); outside that band double-sided is required.

Spec:
1. In `maker_carry_study.py::_book_competition`, fetch BOTH tokens' books
   (Gamma `clobTokenIds` has both; batch via `POST /books` to halve calls),
   build Q_one/Q_two per the published equations, and apply the c-rule by
   mid band. Our own hypothetical quote enters both sides symmetrically.
2. Emit `share_model: "published_v2"` in the summary assumptions; keep the
   old share as `share_model_legacy` for one release so the trend ledger
   can be compared across the change (document the discontinuity in the
   history CSV via a new `share_model` column).
3. Candidates outside mid [0.10, 0.90] are marked `band_ineligible` and
   excluded from the portfolio (matches quote-sheet rule 7 mechanically).
4. Tests: worked example from the docs (the 0.49/0.48/0.51 order set),
   single-sided /3 rule inside band, strict min outside band,
   band-ineligible exclusion.

## WO-47 — Resolution + new-market websocket capture

The market websocket's custom features carry `market_resolved`
(winning_asset_id - authoritative settlement stamps, faster and cleaner
than polling Gamma) and `new_market` (fee_schedule incl rebate_rate,
game_start_time, sports_market_type, tick size at birth).

Spec:
1. Extend `websocket_collector.py` subscription with
   `custom_feature_enabled: true`; persist `market_resolved` events to
   `outputs/polymarket_websocket/resolution_events.csv` (append-only,
   dedup by market+winning_asset_id) and `new_market` events to
   `market_births.csv` (question, condition_id, fee_schedule fields,
   game_start_time, tick size).
2. Wire NOTHING downstream yet: closing-line grading keeps its current
   close-time source; these ledgers are validation data first (compare
   event close stamps vs our derived close_time for amendment-5 fixture
   clustering QA; a later dated tightening may switch the source).
3. Tests: fake websocket frames for both event types, dedup, and the
   existing collector behaviour unchanged when the flag is off.

## WO-48 — Avellaneda-Stoikov quote-sheet layer (research output; POST-GATES)

From docs/MARKET_MAKING_MODELS_RESEARCH.md: reservation price
r = mid - q*gamma*sigma^2*(T-t); optimal spread gamma*sigma^2*(T-t) +
(2/gamma)*ln(1+gamma/k). We already estimate sigma (1-min series) and k
(fill-intensity decay across the three sweep distances).

Spec (BLOCKED until maker_verdict = evidence_supported; build behind
`enabled: false` default):
1. Module `maker_quote_advisor.py`, CLI `maker-quote-advisor`, config
   `maker_quote_advisor:` (enabled false, gamma 0.5, horizon = hours to
   market close capped at 168h).
2. Per quote-sheet market: estimate sigma from the 1-min series, fit k by
   log-linear regression of band-crossing rate vs the three sweep
   distances, output suggested half-spread and the inventory-skew table
   (suggested requote mid-shift per 100 shares of inventory).
3. Output is a section appended to maker_quote_sheet.md labelled
   "advisory only - the standing rules override this table"; no gate, no
   order, no study-metric change.
4. Tests: k-fit on synthetic exponential intensity, skew sign (long
   inventory -> both quotes shift down), disabled default emits nothing.

## WO-49 — Flow-toxicity conditioning (VPIN-lite + wallet tiers)

WO-37's wallet ledgers now exist. Two toxicity signals per market:
(a) VPIN-lite: signed volume imbalance over rolling volume buckets from
trade prints; (b) wallet-tier markout: markout-by-fill where the
counterparty wallet is in the leaderboard top-100 vs not (the arXiv
Polymarket microstructure study documents exactly these tiers).

Spec:
1. Module `flow_toxicity.py`, CLI `flow-toxicity`, config `flow_toxicity:`
   (enabled, volume_bucket_usd 500, buckets 50, markout_horizon_minutes 5).
2. Inputs: `trade_prints.csv`, `holders_history.csv` /
   `leaderboard_history.csv` (WO-37 outputs), 1-min price series.
   Output per tracked market: `toxicity_score` (0-1 percentile of
   imbalance), `smart_fill_markout` vs `crowd_fill_markout`, appended to
   `outputs/maker_carry/flow_toxicity.csv` + summary JSON.
3. Surface: quote sheet gains a per-market toxicity column and a standing
   rule 8 ("do not initiate quotes in a market whose toxicity_score >
   0.9"); the study's adverse charge is NOT modified (constraint 8) - a
   dated tightening may later take max(charge, toxicity-implied charge).
4. Cadence: rides the daily harvest. Tests: planted toxic flow raises the
   score, balanced flow does not (constraint 6 null test), wallet-tier
   split arithmetic, missing-WO-37-data tolerance.

# Codex batch 3 — filed 2026-07-10 (decision discipline + carry optimization)

Constraints 1-8 bind. One WO per PR.

## WO-50 — Live-test decision policy engine (policy FROZEN in this order)

Converts the July 16-20 evidence into pre-committed action, removing
decision-under-influence risk. The policy constants below are REGISTERED at
filing time (2026-07-10); Codex mechanizes evaluation and display. Changes
may only tighten, with a dated comment.

REGISTERED POLICY:
- Evidence -> action table (evaluated daily from maker_carry_study.json +
  maker_carry_history.csv + maker_live_test.json):
  a. M-A pass AND M-B pass AND composition stable (same top portfolio
     market on >= 4 of last 7 daily runs) -> indicated_action =
     "fund_100_min_size_single_calmest_market".
  b. M-A pass AND M-B pass but composition churning (< 4/7) ->
     "fund_100_but_only_most_recurrent_market_half_target".
  c. Any gate pending on 2026-07-20 -> "defer_funding_continue_study".
  d. Net below target on > 3 of last 7 runs after gates were reachable ->
     "maker_lane_not_supported_program_review".
- Sizing ladder (post-funding, uses live-test scoreboard):
  Stage 0: $100 bankroll, minimum quote size only.
  Stage 1: $250 after >= 7 consecutive real days with cumulative
  net_score_usd > 0 AND fills <= 2x modelled rate throughout.
  Stage 2: $500 after 14 further days meeting the same conditions AND
  realized rewards >= 0.5x modelled gross.
  Kelly overlay: stage capital is additionally capped at quarter-Kelly
  computed from the trend ledger's daily net mean/std via the existing
  uncertainty-shrunk Kelly module (`kelly_sizing`); the binding cap is the
  SMALLER of ladder and Kelly.
- Kill criteria (ANY triggers "stop_quoting_review_before_resume"):
  cumulative real net_score_usd <= -$25; single-day net <= -$15; fills >
  2x modelled band-crossing rate on 2 distinct days; any quoted market
  enters a UMA dispute while inventory is held (exit all, 48h stand-down);
  live-test scoreboard = STOP_fills_outrunning_model.

Spec:
1. Module `live_test_decision_policy.py`, CLI `decision-policy`, config
   `decision_policy:` (enabled; all constants above as defaults, frozen).
2. Output `outputs/maker_carry/decision_policy.json`: registered_at stamp,
   inputs snapshot, `indicated_action`, `ladder_stage_permitted`,
   `kill_criteria_status` (each criterion evaluated when live-test data
   exists), and `policy_note` ("indicates, never executes - the human
   decides; the system never trades").
3. Render on the quote sheet (top) and the dashboard maker panel
   (indicated_action badge).
4. Cadence: rides the daily harvest after the study. Tests: each table row
   (a-d) reachable via synthetic ledgers; ladder promotion arithmetic;
   quarter-Kelly cap binding vs ladder; each kill criterion trips
   individually; no-live-data tolerance.

## WO-51 — Resolution-risk screen for quoted markets

A maker holding inventory through a DISPUTED UMA resolution loses weeks of
carry at once. Resolution risk is screenable: objective-source questions
(published numbers, final scores) resolve cleanly; subjective wording
("announce", "officially", "deal", "considers") invites disputes. We
already grade our own resolved corpus (`resolution_quality_report.csv`).

Spec:
1. In `maker_carry_study.py`: classify each candidate question into
   resolution_risk low/medium/high via (a) keyword classes - LOW: fed/rate
   decision, match/game winner, numeric close above/below, official
   election result; HIGH: announce/announcement, officially, deal,
   agreement, ceasefire, blockade, considers, attempts, intends, meeting,
   talks; MEDIUM: otherwise; (b) corpus overlay: per keyword-class clean
   settlement share from `resolution_quality_report.csv` when >= 50
   graded markets exist for the class - a LOW class with measured clean
   share < 0.9 escalates to MEDIUM (data may only escalate, never
   downgrade - tighten-only).
2. Portfolio EXCLUDES high; quote sheet gains a resolution_risk column and
   standing rule 9: "Only quote markets with objective, verifiable
   resolution sources and no open clarifications; exit quotes immediately
   if a proposal on a held market is disputed."
3. Tests: keyword classes, corpus escalation with sample floor, HIGH
   exclusion from portfolio, absent-report tolerance.

## WO-52 — Hour-of-day adverse-selection concentration study

Reward sampling pays uniformly across 1,440 minutes; pick-off risk almost
certainly does not arrive uniformly. If the charge concentrates in
identifiable UTC hours, calm-hours quoting keeps most reward income while
shedding most adverse cost - a direct carry improvement with zero new
data collection.

Spec:
1. Module `hourly_adverse_study.py`, CLI `hourly-adverse-study`, config
   `hourly_adverse:` (enabled, min_events_per_bucket 30, fdr_alpha 0.10,
   markets from the current candidates file).
2. Inputs: existing 1-min price histories + trade prints. Per UTC hour
   bucket: band-crossing rate, bar-move pick-off charge share, print
   volume share. Flag buckets whose charge share exceeds uniform after
   BH-FDR across the 24 buckets (constraint 7); report a recommended
   calm-hours window (largest contiguous low-charge span covering >= 50%
   of reward minutes).
3. Output `outputs/maker_carry/hourly_adverse.json` + advisory line on the
   quote sheet ("calm-hours schedule (advisory)"). The study charge is NOT
   modified (constraint 8); a later dated tightening may apply the
   schedule.
4. Planted-truth tests (constraint 6): uniform synthetic flow flags NO
   buckets; planted 3-hour toxic window recovered; sample-floor respected.

## WO-53 — Intraday competition sampling (second daily study run)

The reward-share estimate reads order books once daily; per-minute reward
sampling means the truth is the day-long average. A second run ~12h
offset doubles trend-ledger resolution. M-A counts DISTINCT UTC DAYS, so
this cannot fast-forward the gate (registered 2026-07-10 clarification).

Spec:
1. `scripts/run_vps_ops_scheduler.sh`: new stamp `maker_study_intraday`,
   interval 24h, running `maker-carry-study` only when the last
   training-harvest stamp is 11-13h old (keeps the two runs ~antipodal).
2. No schema change (generated_at_utc already distinguishes runs).
3. Tests: scheduler contains the job + offset guard; vps docker test
   asserts the new job string; a same-day second run leaves
   runs_at_or_above_target unchanged (already covered - extend assertion).

## WO-54 — Deep trade-print backfill (backsolve markout/toxicity history)

The data-API /trades endpoint serves deep per-market history via pagination;
our 15-min collector only keeps recent prints. Backfilling turns weeks of
already-recorded venue history into usable markout and toxicity datapoints
TODAY instead of accruing them forward.

Spec:
1. Extend `trade_print_collector.py` with `backfill_trade_prints(cfg)` (CLI
   `backfill-trade-prints`): for the maker-study candidate markets plus the
   quote-sheet portfolio, page /trades with offset/limit until exhausted or
   `backfill_max_prints_per_market` (default 5,000) is reached; dedup into
   the existing ledger; respect rate limits (sleep >= 0.1s/page; /trades cap
   200 req/10s).
2. One-shot + idempotent: a stamp file marks completed markets so reruns
   skip them; new markets entering the study get backfilled on the next run.
3. Cadence: rides the daily harvest AFTER the study selects candidates.
4. Tests: pagination fake with exhaustion, dedup against existing ledger,
   stamp skip, cap respected.

## WO-55 — Reconstructed-signal CLV study (RESEARCH ONLY - never verdict)

We hold the one archive money cannot re-buy: timestamped sharp-anchor odds
snapshots. Every historical anchor divergence that our thresholds WOULD have
entered can be reconstructed - entry price from the official price history
at signal time, closing line at market close - multiplying CLV research
sample size far beyond the 8 forward units.

HARD CONSTRAINT (this is the entire point): reconstructed entries are
research datapoints, NEVER Gate A evidence. The verdict is pre-registered on
forward-stamped entries precisely because retro-selection and look-ahead
cannot be excluded from reconstructions. Outputs live in their own artifact,
labelled non-verdict, and profit_verdict.py is NOT touched.

Spec:
1. Module `reconstructed_signal_clv.py`, CLI `reconstructed-clv-study`,
   config `reconstructed_clv:` (enabled, thresholds MIRRORING the live
   anchor entry rules as of 2026-07-10 - frozen in config, tighten-only).
2. Inputs: stored sharp-anchor snapshot artifacts (odds + mapped tokens +
   timestamps), official /prices-history for entry/close lines, Gamma
   closedTime. Cluster by fixture (reuse amendment-5 union-find via
   profit_verdict helpers) and report unit mean CLV, beat rate, sign-test p
   - clearly stamped `evidence_class: reconstructed_research`.
3. Constraint 6/7 discipline: planted-truth test (synthetic snapshots with
   known CLV recovered; martingale null flags nothing) and BH-FDR if
   multiple thresholds/cohorts are scanned.
4. Value: a power-multiplied read on the anchor mechanism BEFORE the next
   event regime, informing whether the extended taker window is worth
   keeping open - as judgment input, not as gate input.

## Priority order for Codex (updated 2026-07-10, batch 3 filed)

Batch 3 first, in order: **WO-50 -> WO-51 -> WO-52 -> WO-53** (decision
discipline before more measurement; 50/51/53 are small). Then **WO-54**
(deep print backfill - backsolves markout/toxicity history for everything
downstream) and the prior queue: WO-41 -> WO-46 -> WO-44 -> WO-45 -> WO-49
-> **WO-55** (reconstructed-signal research read before the taker window
extension decision) -> WO-42 -> WO-43; WO-47 anytime; WO-48 BLOCKED until
the maker gates read evidence-supported; WO-33 last pending the leakage
review.

## Superseded priority note (2026-07-10 earlier)

Batch-1 remainder first: **WO-41** (implication networks - structural, high
prior). Then batch 2 in this order: **WO-46** (share-model fidelity - it
changes the accuracy of every number the funding decision reads),
**WO-44** (official book history - replay realism), **WO-45** (supplementary
income - completes the income picture without touching gates), **WO-49**
(toxicity - uses WO-37 data), then batch-1 **WO-42** and **WO-43**
(corpus-bound studies). **WO-47** anytime (independent). **WO-48** stays
BLOCKED until the maker gates read evidence-supported. WO-33 remains last
overall pending the leakage review.
