# Polymarket Codex Work Orders

Last updated: 2026-07-15 (WO-85, WO-87, WO-86, and WO-88 implemented; WO-80, WO-82, WO-81 landed; WO-83 implemented in
PR #203; WO-84 implemented in PR #205; WO-89 through WO-92 implemented. WO-87 now relabels the unchanged legacy verdict metric honestly and
reports non-binding true pre-event CLV on the same units. No numbered work order is currently
buildable. WO-33 remains pending a registered leakage review, with
WO-34/35 model wiring bound to that review and the three-hypothesis freeze.
WO-48 and WO-67 are blocked; WO-70 and WO-72 are deferred; WO-76 is
registration-only. Crypto up/down is frozen as a diagnostic — see
`AGENTS.md`.)

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

**Extended 2026-07-12 (external-audit P2 remediation):** the reconciliation now preserves the
independent stage denominators by anchor source, sport, and market type: raw provider outcomes,
normalised rows, mapping-audit rows, mapped rows, current Polymarket joins, and current eligible
Polymarket tokens. `sharp_anchor_mapping_audit.csv` classifies every normalised row exactly once;
`sharp_anchor_funnel_history.csv` records idempotent source/sport/market snapshots. The JSON and
dashboard also expose ambiguity, anchor/price staleness, missing timestamps, actual ask-side
divergence, current bid/ask actionability, zero joins, and explicit denominator-conservation
failures. Cross-stage count mismatches are reported as incomplete rather than silently deriving one
denominator from another. These additions are reporting-only: historical rows are not retroactively
promoted, no fetch config is edited automatically, and no paper/live gate or order path changes.

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

Status: PENDING REVIEW, not authorized for implementation. Collection and
retention plumbing has landed, but the training-set assembly must first receive
the registered point-in-time leakage review and must map to one of H1-H3 in
`docs/EXPERIMENT_REGISTRY.md`. Do not treat this open text as permission for a
generic fourth modelling lane.

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

Status: DETECTION/COLLECTION IMPLEMENTED; model wiring remains pending with
WO-33. H2's dedicated post-registration OOS evaluator is not supplied by the
historical detector and must follow the exact H2 contract before it can count as
edge evidence.

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

Status: COLLECTION IMPLEMENTED through WO-37/58; model wiring remains pending
with WO-33. Existing wallet outputs are diagnostic history. H3 requires its
registered post-freeze clustering, chronological discovery/validation split,
costs, concentration limit, and FDR evaluator before any cohort can count.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-42-calibration-bias-study` on 2026-07-10. New CLI
`calibration-bias-study` joins clean resolutions to pre-close price histories,
writes `outputs/calibration_bias/calibration_curve.csv` plus summary JSON,
and applies clustered bootstrap CIs with BH-FDR. Study only; flags are future
pre-registration candidates, not trades.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-43-drift-scan-study` on 2026-07-10. CLI `drift-scan` writes
`outputs/polymarket_model_governance/drift_scan.csv` and `drift_scan.json`
with market-clustered bootstrap CIs, BH-FDR, and taker-cost-stack flags. Study
only; no lane, no paper/live trading, no gate changes.

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

Status: IMPLEMENTED by Codex in PR branch `agent/wo-44-official-book-history`
on 2026-07-10. `maker_fill_replay.py` now supports `book_source:
archive|official|both`, snapshots official orderbook-history into
`outputs/maker_carry/official_books/*.csv.gz`, reports per-source replay
results and `source_agreement`, and degrades to archive replay when the
official endpoint is unavailable.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-45-supplementary-maker-income` on 2026-07-10.
`maker_carry_study.py` now reports supplementary maker rebates and holding
rewards in candidates, summary totals, and quote sheet rows as uncounted
income. Registered M-gates and `portfolio_net_carry_usd_per_day` remain
unchanged.

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

Status: IMPLEMENTED by Codex in PR branch `agent/wo-46-share-model-fidelity`
on 2026-07-10. `maker_carry_study.py` now uses `share_model:
published_v2`, keeps `share_model_legacy` in candidates for comparison, adds
`share_model` to the history ledger, and marks out-of-band candidates
`band_ineligible` without changing registered M-gates or portfolio net-carry
gate definitions.

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

Status: IMPLEMENTED by Codex on 2026-07-11. The market-channel subscription now
requests custom features for both configured and dynamically selected assets.
`outputs/polymarket_websocket/resolution_events.csv` records authoritative
market/winner stamps, while `market_births.csv` records condition, fee/rebate,
sports timing/type, and birth tick metadata. Both ledgers append without rewriting
existing bytes and deduplicate repeated lifecycle frames. They remain validation-only:
no feature, closing-line, governance, broker, or order path reads them.

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

Status: IMPLEMENTED by Codex in PR branch `agent/wo-49-flow-toxicity`
on 2026-07-10. New CLI `flow-toxicity` writes
`outputs/maker_carry/flow_toxicity.csv` and summary JSON with VPIN-lite
toxicity percentiles plus smart-wallet/crowd markout splits. The maker quote
sheet shows a toxicity column and standing rule 8; adverse charges and gates
are not modified.

**2026-07-15 completion correction.** Production evidence showed the original
reader decompressed every retained feature archive into Python lists and the
WO-49 child was killed at the scheduler's 2 GiB cgroup boundary. Feature and
trade corpora are now streamed; required markout prices are held in a bounded,
temporary disk-backed index and the summary reports scan/index counts plus
temporary index size. The 2 GiB scheduler limit remains the acceptance
constraint, so the fix proves bounded behavior instead of hiding the defect by
widening the container limit. No toxicity rule, gate, sizing, or order path is
changed.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-50-decision-policy` on 2026-07-10. New CLI
`decision-policy` writes `outputs/maker_carry/decision_policy.json`,
patches the maker quote sheet with the registered action indication, rides
the VPS daily harvest, and surfaces the indicated-action badge in the maker
dashboard panel. The output remains advisory only and explicitly records
`paper_trading_invoked=false` and `live_trading_invoked=false`.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-51-resolution-risk` on 2026-07-10. `maker_carry_study.py`
now classifies candidate resolution risk, escalates low-risk classes to
medium when the resolved corpus shows insufficient clean settlement share,
excludes high-risk wording from the quote portfolio, and adds a
resolution-risk column plus standing rule 9 to the maker quote sheet.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-52-hourly-adverse` on 2026-07-10. New CLI
`hourly-adverse-study` writes
`outputs/maker_carry/hourly_adverse.json`, flags UTC hours with
concentrated pick-off charge after BH-FDR, reports a calm-hours advisory,
and patches the maker quote sheet with a study-only calm-hours line. It
does not modify maker charges, gates, sizing, or orders.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-53-intraday-maker-study` on 2026-07-10. The VPS ops scheduler
now has a `maker_study_intraday` stamp and runs a second
`maker-carry-study` only when the last `training_harvest` stamp is 11-13
hours old. The maker-carry M-A gate remains distinct-UTC-day based, so
same-day re-runs cannot fast-forward the verdict.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-54-backfill-trade-prints` on 2026-07-11. New CLI
`backfill-trade-prints` paginates public data-API `/trades` for the
maker-study candidates plus quote-sheet portfolio, dedups into
`outputs/polymarket_trade_prints/trade_prints.csv`, and records completed
markets in `backfill_completed_markets.txt` so reruns skip already backfilled
markets while new candidates are picked up. The VPS daily harvest runs it
after `maker-carry-study`.

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

Status: IMPLEMENTED by Codex in PR branch
`agent/wo-55-reconstructed-clv-study` on 2026-07-10. CLI
`reconstructed-clv-study` writes
`outputs/polymarket_model_governance/reconstructed_signal_clv.json` and
`reconstructed_signal_clv_positions.csv`, both stamped
`evidence_class: reconstructed_research`. It is explicitly non-verdict and
does not touch `profit_verdict.py` or any paper/live trading path.

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

# Codex batch 4 — filed 2026-07-11 (response to the published-share repricing)

Context: WO-46's published share model repriced the maker lane from
$58.99/day (legacy, 3 markets) to $0.93/day (1 market). The collapse is not
a bug - it is the honest number once complement-book competition is
counted. It exposes the binding variable: pot-weighted COMPETITION, not pot
size. The two orders below attack exactly that variable and complete the
picture the Jul 20 policy date and the post-WC funding judgment will read.
Constraints 1-8 bind. One WO per PR.

## WO-56 — Yield-first rewarded-universe scan (find under-competed pots)

Status: LANDED by Codex on 2026-07-11 in PR #134. Maker-carry discovery now
pre-screens rewarded markets by achievable gross at minimum quote size, records
pot/yield ranks and scan mode, and fails softly to pot ranking. The registered
$500 metric and all maker guards remain unchanged. Artifacts are
`outputs/maker_carry/maker_carry_study.json`, `maker_carry_candidates.csv`, and
`maker_carry_history.csv`.

The universe scan ranks by pot size and measures the top 40. Under the
published rule that ranking is adversarial to us: the biggest pots attract
proportionally the deepest maker competition (observed shares 0.01-0.3%).
The metric that pays is achievable gross = pot x share(min_size), which is
large exactly where the pool is thin relative to the pot.

Spec:
1. In `maker_carry_study.py::_rewarded_universe` collection stays as-is
   (pot floor `min_daily_pot_usd` unchanged). Add a cheap pre-screen pass
   over up to `yield_scan_max_markets` (default 200) rewarded markets:
   batch-fetch books via POST /books (reuse `_fetch_books`), compute the
   published-rule pool score and `expected_gross_at_min_size =
   pot x share(min_size quote)`, then select the top `max_book_candidates`
   by THAT ranking for the full (expensive) history/markout measurement.
2. Every existing guard is unchanged and still binds: thin-book trust
   (>5% share untrusted), band eligibility [0.10, 0.90], resolution-risk
   HIGH exclusion (WO-51), worst-of-three adverse charging, payout floor.
   A thin pool that is thin because nobody sane quotes there must still
   die at the guards, not sneak in through the ranking.
3. Registration integrity note (constraint 8): the gate METRIC is
   unchanged (net carry of the sized portfolio at the registered $500 cap,
   distinct-UTC-days counting). Only measurement COVERAGE widens - the
   registered study always intended to scan the rewarded universe; the
   pot-rank top-40 was a compute budget, not a registered choice. Stamp
   the change: summary assumptions gain `universe_scan_mode:
   "yield_first_v1"`, candidates CSV gains `pot_rank` and `yield_rank`,
   and the history CSV gains a `universe_scan_mode` column (same
   discontinuity discipline as `share_model`).
4. API budget: pre-screen adds only batched /books calls; respect
   `request_pause_seconds`; cap total pre-screen fetches at
   `yield_scan_max_markets`; fail-soft to the pot ranking when the
   pre-screen errors.
5. Tests: synthetic universe where a small-pot/empty-pool market
   out-yields a big-pot/crowded market and is selected; trust guards still
   exclude a degenerate empty book (share ~1 -> untrusted); scan-mode
   stamped in summary + history; fail-soft path returns pot ranking.

## WO-57 — Capital-to-target curve (what bankroll would the honest model need?)

Status: IMPLEMENTED by Codex on 2026-07-11. `maker_carry_study.py` now emits
the supplementary `capital_curve` and `capital_for_100_per_month`, and the
human quote sheet labels the curve as an uncounted planning aid. The registered
$500 portfolio metric and every M-gate remain unchanged.

The repriced lane returns ~5-6%/month on capital but cannot reach $100
absolute at a $500 cap. The funding judgment needs the curve, not my
linear guess.

Spec (constraint 8: supplementary reporting only):
1. In `maker_carry_study.py`, after the registered sized portfolio,
   re-run the greedy sizing at capital caps [250, 500, 1000, 2000, 5000]
   using the same concave share function share(k) = k*ours/(k*ours+pool)
   and the same guards/floors. Emit `capital_curve`: net_usd_per_day per
   cap and `capital_for_100_per_month` (smallest cap whose net >= target,
   null when none).
2. The registered metric `portfolio_net_carry_usd_per_day` (at the $500
   registered cap) and every M-gate stay byte-identical. The curve appears
   in the summary and as one quote-sheet line labelled
   "planning aid - uncounted, not a gate input".
3. Tests: curve monotone non-decreasing and concave (per-dollar yield
   falls with capital); registered metric identical with the feature on
   and off; `capital_for_100_per_month` solves correctly on synthetic
   books; null when even $5000 cannot reach target.

## WO-58 — Patch: wallet-intelligence leaderboard host (WO-37 follow-up)

Status: LANDED by Codex on 2026-07-11 in PR #131. Leaderboard collection now
probes public `/v1/leaderboard` before the legacy path, and empty websocket
tracking falls back through maker-carry candidates/study and trade-print
ledgers. Artifacts remain `outputs/wallet_intelligence/leaderboard_history.csv`,
`holders_history.csv`, and `wallet_intelligence_summary.json`.

`GET data-api.polymarket.com/leaderboard` 404s in production (see
wallet_intelligence_summary.json errors). Find the correct public
leaderboard endpoint (the docs assimilation catalogue lists a dedicated
leaderboard API host; probe it key-less), fix `_fetch_leaderboard`
probes, and investigate why `markets_polled` was 0 (the `_tracked_markets`
source came up empty on the VPS - trace what it reads and point it at a
populated ledger). Tests: probe fallback order, empty-tracked-markets
tolerance stays, parsed rows land in the ledger.

## WO-59 — Patch: WO-50 Kelly overlay tightening (dated)

Status: IMPLEMENTED by Codex on 2026-07-11. The advisory decision policy now
routes its existing inline quarter-Kelly ceiling through
`risk.shrunk_kelly_fraction`, shrinking toward no edge until 20 distinct daily
observations. The ladder, registered policy table, gates, and order paths are
unchanged.

`live_test_decision_policy._quarter_kelly_cap` inlines plain quarter-Kelly
(mean/std^2 x 0.25). The registered policy says the overlay uses the
EXISTING uncertainty-shrunk Kelly module (`risk.shrunk_kelly_fraction`
lineage) - which is strictly tighter on small samples. Swap the inline
computation for the shrunk module with a dated comment (tighten-only,
constraint: the binding cap may only get SMALLER for the same inputs).
Tests: shrunk cap <= inline cap on short histories; equality in the
large-sample limit; ladder still binds when smaller.

## WO-60 — Per-system performance factsheet (Sharpe, Sortino, investment stats)

Status: IMPLEMENTED by Codex on 2026-07-11. CLI `performance-factsheet`
writes `outputs/performance/performance_factsheet.json` and `.md`, classifies
paper/shadow/modeled/live-real-money evidence, enforces the daily sample floor,
and renders a dashboard section. Shadow cohort CSV evidence now accrues one
latest snapshot per cohort per UTC day. Reporting only; no gate or policy reads
these artifacts.

Fund-grade descriptive statistics for every money-shaped series the system
produces, honestly labelled by evidence class, and packaged so that IF the
live test ever earns real numbers, the factsheet is already accumulating
them in presentable form.

Spec:
1. Module `performance_factsheet.py`, CLI `performance-factsheet`, config
   `performance_factsheet:` (enabled, risk_free_rate_annual 0.0,
   min_daily_observations 20, bootstrap_iterations 2000,
   periods_per_year 365 - prediction markets trade every day).
2. Series registry - one factsheet section per system:
   a. `paper_portfolio` - last equity per UTC day from
      `outputs/polymarket_portfolio/portfolio_snapshots.csv`;
      evidence_class "paper".
   b. `shadow_signal_cohorts` - per-family daily PnL from
      `shadow_signal_cohort_pnl.csv` plus the aggregate;
      evidence_class "shadow".
   c. `maker_carry_model` - daily modelled net from
      `maker_carry_history.csv`; evidence_class "modeled" (explicitly NOT
      performance - it is a model output).
   d. `maker_live_test` - daily net_score from the live-test history once
      a wallet exists; evidence_class "live_real_money". The ONLY series
      ever eligible for external presentation.
3. Stats per series, daily granularity: n_days, span, cumulative return,
   mean/vol of daily returns, annualised Sharpe, Sortino (downside
   deviation vs 0), max drawdown depth AND duration, Calmar, hit rate,
   profit factor, best/worst day, bootstrap 90% CI on the Sharpe.
4. HONESTY GUARDS (non-negotiable):
   - annualised figures render null with a reason when
     n_days < min_daily_observations (a 10-day annualised Sharpe is
     marketing, not measurement);
   - every section stamps its evidence_class and the banner
     "SIMULATED - paper/model results do not represent live trading"
     on everything except live_real_money;
   - the bootstrap CI always renders next to the point estimate.
5. Outputs: `outputs/performance/performance_factsheet.json` +
   `performance_factsheet.md` (the packaging artifact), a dashboard
   section, riding the daily harvest. Constraint 8: reporting only - no
   gate, sizing rule, or policy reads it.
6. Tests (constraint 6): synthetic series with known Sharpe/Sortino
   recovered within tolerance; zero-edge synthetic series yields a CI
   covering 0; hand-built drawdown series returns exact depth/duration;
   sample floor suppresses annualisation; evidence-class banners present.

# Codex batch 5 — filed 2026-07-11 (investor-grade evidence infrastructure)

Purpose: make the record AUDITABLE, not just honest. Everything below is
reporting/infrastructure - no gate, sizing rule, or policy may read any of
it (constraint 8). These are preconditions for ever presenting results
externally; they are NOT preconditions for the $100/month verdict and must
not jump ahead of WO-56/57 in the queue.

## WO-61 — Tamper-evident ledger anchoring (hash chain)

Status: IMPLEMENTED by Codex on 2026-07-11. CLI commands `anchor-ledgers` and
`verify-ledger-chain` write the daily prefix-hash chain under
`outputs/performance/`; legitimately mutable state files use immutable dated
copies, and `scripts/push_vps_anchor.sh` preserves each head as a child commit
on the dedicated non-force `vps-anchor` branch. Reporting only; no trading or
governance path reads these artifacts.

An external reader must be able to verify the track record was not edited
after the fact.

Spec:
1. Module `ledger_anchor.py`, CLI `anchor-ledgers`, config `ledger_anchor:`
   (enabled, ledger globs: portfolio snapshots, cash ledger, paper fills,
   settlements, shadow positions/fills, maker_carry_history, live-test
   history, decision_policy.json).
2. Daily, PER LEDGER: record (byte_length, sha256 of the first
   byte_length bytes). Append-only ledgers legitimately grow, so whole-file
   hashes go stale on every append; prefix hashes stay verifiable forever:
   today's file's first N bytes must still hash to day N's prefix hash.
   Chain head H_today = sha256(H_yesterday || all (path, len, hash) tuples
   || date), appended to `outputs/performance/ledger_anchor_chain.csv`.
   (2026-07-11 pre-build amendment from the builder's audit.)
2b. External anchor: push each day's chain-head file as a CHILD commit to a
   dedicated `vps-anchor` branch (append-only history; commits are tiny).
   The telemetry branch is force-replaced by design and is NOT a durable
   anchor - do not use it for this. (Same amendment.)
2c. The ledger registry is config-extensible so WO-63's cost ledger (and
   future ledgers) enrol without code changes.
3. CLI `verify-ledger-chain` recomputes and reports the first broken link
   if any ledger changed retroactively.
4. Tests: chain verifies on untouched ledgers; single-byte edit in an old
   ledger is detected with the correct first-broken date; missing-file
   tolerance.

## WO-62 — Live-wallet reconciliation (three-way, on-chain)

Status: IMPLEMENTED by Codex on 2026-07-11. CLI `reconcile-wallet` performs
read-only internal/data-API/Polygon NAV comparison, resolves pUSD and CTF
addresses only from the official contracts page (with provenance-stamped
cache fallback), records the WO-63 gas-cost hook, and raises reporting-only
factsheet/quote-sheet alerts above the registered $1 tolerance. It remains
inert until `maker_live_test.wallet_address` is configured.

Paper stats are self-reported; live stats must reconcile against records
we do not control. Polygon is public - use it.

Spec:
1. Module `wallet_reconciliation.py`, CLI `reconcile-wallet`, inert until
   `maker_live_test.wallet_address` is set (mirrors WO-36's guard).
2. Daily three-way check, in LIKE-FOR-LIKE NAV terms (2026-07-11
   pre-build amendment, verified against docs.polymarket.com/concepts/pusd:
   trading collateral is pUSD, an ERC-20 on Polygon backed 1:1 by USDC.e
   via onramp 0x93070a847efEf7F70739046A929D47a521F5B8ee / offramp):
   (a) internal live-test scoreboard NAV (cash + marked positions + accrued
   rewards, adjusted for deposits/withdrawals);
   (b) data-api /activity + /positions NAV for the wallet;
   (c) on-chain NAV via key-less Polygon RPC: pUSD balanceOf + marked
   ERC-1155 position values. Collateral/CTF contract addresses read from
   the official contracts page at build time - never hardcoded from
   memory. Report per-day deltas and reconciliation_status
   (clean / explained / DISCREPANCY with $ size).
3. Any unexplained discrepancy > $1 renders a red banner in the factsheet
   and quote sheet (reporting only - humans act).
4. Tests: synthetic three-way agreement passes; planted $5 mismatch flags;
   missing RPC degrades to two-way with status "partial".

## WO-63 — True-net cost ledger

Status: IMPLEMENTED by Codex on 2026-07-11. The append-only
`outputs/performance/cost_ledger.csv` accepts idempotent manual costs through
CLI `add-cost`; scheduled `sync-cost-ledger` converts only investor-attributed
WO-62 gas observations through CoinGecko simple-price and fails soft to an
explicit manual-entry queue. WO-60 now reports gross, booked costs, and net-net
for live evidence, while simulated sections carry a clearly hypothetical
registered fee/haircut drag. Reporting only; no gate or trading path reads it.

Investor returns are net of EVERYTHING: gas, deposit/withdrawal rails,
data subscriptions.

Spec:
1. `outputs/performance/cost_ledger.csv` (append-only; date, category
   [gas|rail|subscription|other], usd, cost_ref, note). `cost_ref` is a
   unique idempotency key (e.g. tx hash) so re-scans never double-count.
   CLI `add-cost` for manual entries (rails, subscriptions); automatic gas
   capture is a HOOK filled by WO-62's on-chain scan (build order moved to
   after WO-62 accordingly - 2026-07-11 pre-build amendment). POL-to-USD
   conversion via a named key-less source (CoinGecko simple-price),
   fail-soft to manual entry when unavailable.
2. WO-60's factsheet gains a "net of all costs" line per live section:
   gross, costs, net-net. Paper/model sections show hypothetical cost
   drag using the registered fee/haircut stack.
3. Tests: cost aggregation windows, factsheet net-net arithmetic,
   append-only discipline.

## WO-64 — Investment policy statement generator (code-is-policy)

Status: IMPLEMENTED by Codex on 2026-07-11. CLI `render-ips` generates
`outputs/performance/investment_policy_statement.md` from the registered
action table, maker/verdict gates, amendments, kill criteria, and the same
structured quote rules used by the daily quote sheet. It embeds source-default
and effective-config hashes plus the current WO-12 risk and WO-57 capacity
annexes. Reporting only; no trading or governance path reads the IPS.

The risk limits shown to an external reader must be GENERATED from the
enforced constants, never hand-written.

Spec:
1. Module `ips_render.py`, CLI `render-ips`: reads the FROZEN WO-50 policy
   constants, maker gates registration, verdict gates + amendments 1-7,
   quote-sheet standing rules, and kill criteria, and renders
   `outputs/performance/investment_policy_statement.md` with TWO sha256
   hashes embedded: source defaults AND the effective VPS configuration
   (config overrides included) - the IPS must describe the policy as
   deployed, not just as coded. (2026-07-11 pre-build amendment.)
1b. Prerequisite refactor: extract the quote-sheet standing rules from the
   generated markdown into a structured constant (list of rule dicts) that
   BOTH the quote sheet and the IPS render from - never parse generated
   prose. (Same amendment.)
2. Includes the risk annex: current exposure/concentration from the
   portfolio ledgers and the WO-12 VaR machinery, capacity statement from
   WO-57's capital curve.
3. Tests: rendered constants match live module values (drift test fails
   if code and IPS diverge); hash stability; annex tolerates missing
   inputs.

## WO-65 — Disaster recovery: full state snapshot + tested restore

Status: IMPLEMENTED by Codex on 2026-07-11. CLI `snapshot-ledger-archive`
packages the complete historical WO-61 manifest set under a hard 50MB cap;
the host telemetry cadence force-replaces a one-commit `vps-archive` branch
only when the active RPO is due. `scripts/restore_from_archive.sh --dry-run`
validates archive digests and runs WO-61 prefix verification through the
snapshot date before any apply. Paper RPO is 168h, with a fail-closed <=24h
obligation when a live wallet/mode is configured. Failures are stamped into
telemetry-visible status; recovery instructions live in `docs/RESTORE.md`.

One VPS is one lightning strike away from an unprovable track record.

Spec:
1. Extend the telemetry push (or a sibling daily job) with a weekly FULL
   ledger snapshot: tar.gz of the WO-61 ledger set to a dedicated
   `vps-archive` branch (single-commit, force-pushed, same pattern as
   telemetry; size-capped, ledgers only - never the heavy corpora).
2. `scripts/restore_from_archive.sh --dry-run` verifies the archive
   unpacks and the WO-61 chain verifies AS OF the snapshot date (prefix
   hashes make this well-defined). Explicit parameters (2026-07-11
   pre-build amendment): RPO = 7 days at paper stage, MUST tighten to
   24h before live capital (dated change); archive size cap 50MB
   (ledgers only, incl. the WO-63 cost ledger); on failure the job exits
   nonzero, stamps status, and the miss is telemetry-visible.
3. Docs: a RESTORE.md runbook (fresh VPS to running stack, RTO target
   < 1 day).
4. Tests: snapshot round-trip in tmpdir; chain verification post-restore;
   dry-run exits nonzero on corrupt archive.

# Batch 6 — filed 2026-07-12 (the road to autonomous execution)

Context: the operator has confirmed the destination is fully autonomous
maker execution, entered via a human-executed live test first. WO-66 is
buildable NOW (decision support only). WO-67 is the full executor
architecture, specified today so the eventual build is mechanical, and
BLOCKED behind explicit preconditions - including a dated governance
amendment only the repo owner can make. Constraints 1-8 bind.

## WO-66 — Execution assistant (read-only decision support) — `done` (2026-07-12)

Shrinks the human execution role to near-zero clicks without touching an
order path.

Spec:
1. Quote sheet upgrade: each portfolio row renders an exact order ticket -
   market URL, outcome side, bid/ask prices, size in shares, capital -
   copy-paste ready for the Polymarket UI.
2. Module `requote_alerts.py`, CLI `requote-alerts`, riding the 15-min
   trade-prints cycle: evaluates the quote-sheet standing rules against
   live state (mid drifted beyond band, scheduled event within N hours,
   toxicity > 0.9, resolution proposal detected on a quoted market, any
   kill criterion tripped) and writes `outputs/maker_carry/
   requote_alerts.json` + a dashboard banner. Alert states: quotes_ok /
   requote_advised / pull_quotes_now / STOP.
3. Reuses the existing notifier lane (SuperBru score-change pattern) for
   an optional email ping on pull_quotes_now / STOP only.
4. Read-only, key-less, no auth, no order placement of any kind. Tests:
   each rule triggers on synthetic state; quiet state emits quotes_ok;
   inert without a quote sheet.

**Implemented 2026-07-12 by Codex:** maker-carry portfolio rows now retain a
public Polymarket URL, outcome/token, authoritative tick, reference book,
rounded-away-from-mid bid/ask, shares, and capital; the Markdown sheet renders
copy-ready human tickets. New module/CLI `requote_alerts.py` / `requote-alerts`
runs after the read-only live-test and decision-policy refresh on the 15-minute
trade-print cycle. It combines current websocket bid/ask, scheduled close,
flow toxicity, public Gamma `umaResolutionStatus`, authoritative websocket
resolution events, and registered kill criteria into exactly `quotes_ok`,
`requote_advised`, `pull_quotes_now`, or `STOP`. It writes
`outputs/maker_carry/requote_alerts.json`, patches the quote sheet, and drives
the dashboard banner; daily immutable copies are enrolled in the WO-61 anchor.
Optional notification output mirrors the existing
SuperBru state-digest/body-file contract only for new `pull_quotes_now`/`STOP`
states; the engine has no SMTP, key, auth, signing, placement, amendment, or
cancellation path. Synthetic tests cover every rule, quiet/inert states,
tighten-only overrides, one-shot notification, scheduler wiring, and the
no-order contract. WO-67 remains untouched and blocked.

## WO-67 — Autonomous maker executor (ARCHITECTURE REGISTERED; BLOCKED)

Registered design, not a build order. NOTHING in this WO may be
implemented - not even dark/flagged code - until EVERY precondition below
is met. Filing it now makes the eventual build a days-long mechanical job
with zero design debate.

PRECONDITIONS (all required, in order):
  P1. Maker gates M-A/M-B/M-C pass (registered metrics, no amendments).
  P2. The HUMAN live test completes WO-50 Stage 1: >= 7 consecutive
      positive real days with fills <= 2x model - the model must be
      verified by human-executed evidence first, so any later divergence
      is attributable to the executor, not the model.
  P3. The repo owner commits a dated amendment to the AGENTS.md Owner
      amendments section explicitly authorising a live order path with
      defined scope. CLAUDE.md may point to that authoritative section but
      is not a second signature surface. This is the owner's deliberate act
      in writing; a chat instruction never suffices, by prior registered rule.
  P4. Independent review control: execution-path code requires review by
      an agent/person other than its author before merge (closes the
      self-merge gap in SYSTEM_MAP.md).
  P5. Key custody design approved: scoped relayer API keys (trade-only,
      no withdrawal), stored ONLY in the VPS .env (never repo, never
      chat, never telemetry), rotation procedure documented.

ARCHITECTURE (registered 2026-07-12):
1. `maker_executor.py` consumes ONLY signed-off artifacts: the quote
   sheet (what to quote) and decision_policy.json (whether quoting is
   permitted at all). It acts only when indicated_action is a fund_*
   state AND kill_criteria_status is clear AND the four live gates are
   open. Any missing/stale input = flat (cancel all, hold).
2. Hard caps enforced in code, read from the FROZEN policy: per-market
   size, ladder-stage capital, quarter-Kelly bind; the executor can
   never exceed what the policy engine already computed.
3. Every action (place/cancel/refresh) appends to an execution ledger
   enrolled in the WO-61 anchor chain; WO-62 three-way reconciliation is
   the independent daily check that the executor's book matches the
   venue and the chain.
4. Kill wiring: policy kill criteria auto-flatten (cancel-all) within one
   cycle; the existing kill-switch file and env gates remain manual
   overrides; a dead-man switch flattens if the scheduler heartbeat
   stales > 30 min.
5. Rollout: (a) replay mode against recorded books; (b) canary - ONE
   market, minimum size, >= 7 days, reconciliation clean; (c) portfolio
   mode. Each promotion is a dated note in the execution ledger.
6. Scope forever excluded from this WO: taker orders, discretionary
   position-taking, any strategy beyond resting maker quotes from the
   sheet. New strategies need new registrations.

# Batch 7 — filed 2026-07-12 (external-audit corrections)

Source: first independent external LLM audit of the full repository. Its
two structural findings are accepted: (1) the operating-state documentation
had drifted three weeks behind the system; (2) merges rest on self-reported
local test runs with no independent gate. Constraints 1-8 bind.

## WO-68 — Generated operating state (kill documentation drift permanently)

Status: IMPLEMENTED by Codex on 2026-07-12. CLI `operating-state` now derives the
canonical state and WO-67 P1-P5 checks from effective config, governance/evidence
artifacts, execution ledgers, and the persisted host telemetry manifest. The daily
harvest writes `outputs/performance/operating_state.json` plus `.md`; the dashboard
consumes the same JSON, and tests reject front-door status prose or guessed missing
inputs. Reporting remains inert: paper/live invocation flags are always false.

**2026-07-15 deployment-freshness correction.** The telemetry cron and guarded
deployment now call one atomic host-manifest writer. After Compose starts and
the successful deployed marker is recorded, deployment refreshes source,
checkout, and deployed SHAs before the scheduler's fresh operating-state pass.
This removes the bounded false-DIVERGED window without guessing a SHA or
changing any trading/gate path.

Manual v1 exists at docs/OPERATING_STATE.md. Replace it with a generated
artifact so drift is impossible.

Spec:
1. Module `operating_state.py`, CLI `operating-state`, riding the daily
   harvest: derives every row of the state table FROM artifacts - config
   (paper/live flags, wallet address set), governance outputs
   (paper_allowed), WO-67 precondition checks, verdict and maker gate
   summaries, telemetry manifest SHA - and writes
   `outputs/performance/operating_state.md` plus `.json`.
2. Drift test (the teeth): a pytest asserts README and AGENTS.md contain
   no hard-coded status claims beyond pointers to the generated file -
   grep for the forbidden stale patterns ("not approved for paper",
   a dated "Last project state update", etc.).
3. Dashboard focus panel consumes the same JSON.
4. Tests: state derivation from synthetic artifacts; drift test trips on a
   planted stale claim; missing-artifact tolerance (fields render UNKNOWN,
   never guessed).

## WO-68b — Operating-state follow-up (filed 2026-07-12, AFTER the WO-68 build merged)

Status: IMPLEMENTED by Codex on 2026-07-12. Generated operating state now
contains the seven registered tighten-only reporting SLOs and a source / host
checkout / successfully deployed SHA comparison with divergence age. Target-
revision capacity preflight runs before checkout mutation or Compose
replacement and writes `outputs/performance/vps_capacity_preflight.json`.
Exit-75 restart forensics are preserved in the WO-61-enrolled
`outputs/performance/background_timeout_incidents.csv`; the duplicate in-loop
governance owner is disabled on VPS while the ops scheduler remains canonical.
No trading or governance gate consumes these artifacts.

Filed concurrently with the WO-68 implementation, so these three additive
items became a follow-up order rather than an amendment. All from the full
external-audit text (§3.3, P1):

1. SLO block in the generated state: for each of quote-sheet age,
   governance-refresh duration, skipped scheduler cycles, websocket gap,
   dashboard staleness, reconciliation age, and ledger-anchor age — a
   registered target, the measured value, and a breach flag. REPORTING
   ONLY: a breach alerts a human; it never gates or orders anything.
2. Report BOTH the source SHA (origin/main HEAD at generation) and the
   deployed SHA (telemetry `manifest.json`), plus divergence age, so
   "what is running" vs "what is merged" is one line, not an
   investigation. (Skip whatever the WO-68 build already covers via the
   telemetry manifest; additive only.)
3. Deploy preflight (the 2026-07-11 cpus:3.0 outage class): a tiny script
   run BEFORE `docker compose up` replaces a running service — checks
   nproc / memory / disk headroom against what the compose file asks for
   and refuses the deploy instead of killing a healthy container.

## WO-69 — Independent merge gate (external audit P0; runner built, enforcement blocked on GitHub plan)

Status: IMPLEMENTED TO THE PLATFORM BOUNDARY by Codex on 2026-07-12; NOT
ENFORCED and therefore NOT COMPLETE. Owner option (b) is installed: after the
2026-07-13 VPS upgrade, the repository-scoped Linux ARM64 runner is online and the minimal PR workflow, exact
protection payload, fail-closed audit, generated P4 artifact, tests, and runbook
are implemented. GitHub's protection and ruleset APIs both return HTTP 403 for
this private Free-plan repository. The repository must be upgraded to Pro/Team
and `scripts/audit_github_merge_gate.py --apply-protection` must report
`status=enforced` before live capital. Making the repository public is rejected.

Merges previously rested on the builder's self-reported local suite (hosted
Actions quota exhausted 2026-07-09; operational workflows dispatch-only).
Before ANY live capital beyond the operator pipe test, an independent gate
must exist.

1. OWNER CHOICE required first (this blocks the WO):
   (a) GitHub-hosted minutes: free quota resets monthly; the minimal gate
       below costs roughly 3-5 min per PR, well inside the free tier; or
   (b) a self-hosted runner on the VPS restricted to the minimal gate
       (the 1-vCPU host can afford ruff + guard tests, not the full
       suite); or
   (c) a runner on the Windows box.
2. Minimal mandatory PR gate (fast, deterministic): ruff check; the guard
   and invariant test subset (gate registrations, no-live-path tests,
   telemetry whitelist tests, docker/scheduler string tests); config
   validation; the WO-68 drift test. Full 1,000+ suite stays local or
   nightly dispatch.
3. Branch protection on main: require the gate check, forbid direct
   pushes, require one review not authored by the building agent
   (orchestrator reviews Codex; Codex pre-build-audits orchestrator
   specs - the bidirectional loop already demonstrated).
4. Until the WO-69 artifact reports `status=enforced`, the interim
   compensating controls stay: full local suite before merge, cross-agent
   audits, ledger anchoring, and the operating-state deployed-SHA check.

Implementation/runbook: `docs/WO69_CI_ENFORCEMENT.md`.

## WO-70 — Reproducibility and identity cleanup (accepted, deferred to post-proof phase)

External audit findings accepted but scheduled after the live proof phase:
lock files per layer (base/research/deploy/live), pin the live extra,
rename the package from superbru-score-engine to reflect the real system
(or split the two applications), progressively widen ruff rule classes,
single tested Python version documented. Nothing here changes numbers;
it changes reproducibility. Do not let it jump the evidence work.

# Batch 8 — filed 2026-07-12 (full external-audit text; deltas only)

The full audit text added four points the batch-7 triage had not covered:
an experiment registry (now docs/EXPERIMENT_REGISTRY.md — a doc, no build
needed), SLOs + dual-SHA + deploy preflight (WO-68b above, since the WO-68
build merged concurrently), coverage-driven spend suppression, and a
failure-injection drill suite. The audit's own warning binds: this is a
proof phase, not a construction sprint — batch 8 is two small orders and
both serve the value loop directly.

## WO-71 — Collection hygiene: spend suppression + corpus retention

Status: IMPLEMENTED by Codex on 2026-07-13. The paid sharp-odds fetcher now
derives a reviewable `sharp_fetch_suppression.json` plan from WO-31 coverage
history: a family at the registered zero-join threshold stops paid requests,
receives one slow probe per 24 hours, and returns to normal immediately when a
join succeeds. Config is never silently edited. Daily CLI `corpus-retention`
compacts expired trade prints, official-book rows, and immutable training
chunks into schema-safe daily gzip files before pruning; the live websocket
writer retains ownership of its atomic row roll-off. Archive age/size and raw
windows are tighten-only, stale incomplete atomic temp files are reclaimed,
and the diagnostic history carries disk-growth projection. Fixed writable
paths plus hard namespace and WO-61 registry checks prevent any decision
ledger or WO-65 investor archive from being touched. No model/gate/order path
changed. A write-enabled replay on a disposable copy of the 2026-07-13 VPS
corpus compacted 658,275 training rows, 10,739 trade prints, and 2,600 official
book rows in about one minute under a 3GB container limit; the planted
decision-ledger hash was unchanged. The preceding read-only production replay
identified 1.10GB across 21 abandoned temp files and projected roughly 21 days
to 90% host-disk use without this control.

Two small items, one theme: stop paying (API calls, disk) for data that
cannot change a decision.

1. Coverage-driven suppression (audit P2 delta): using the WO-31 coverage
   artifacts, auto-suppress API spend on persistently unmappable markets —
   a market family with N consecutive zero-join cycles drops to a slow
   probe cadence until a join succeeds. Suppression list is an output
   artifact (reviewable), never a silent config change.
2. Corpus retention (ops finding 2026-07-12): host disk went 72% -> 84%
   in ~1 day of collection. Add registered retention windows for the heavy
   corpora (training archive, websocket features, trade prints, official
   book snapshots): raw kept N days on host, daily compaction to the
   WO-65 bounded archive, then pruned. Retention must NEVER touch the
   append-only decision ledgers (anchored; WO-61 verifies exactly that).
3. Tests: suppression enters/exits on synthetic coverage histories;
   retention never deletes a ledger-anchored path; disk projection in the
   diagnostic log.

## WO-72 — Failure-injection drill suite (DEFERRED: pre-WO-67 requirement, post-ladder)

The audit is right that the risk controls "have only been exercised using
simulated or paper artifacts". Before the WO-67 executor could ever be
unblocked, each drill below must have a dated PASS artifact: stale quote,
missing anchor, inconsistent wallet balance, duplicated fill, partial
fill, delayed cancellation, ledger write failure, anchor push failure,
recovery from stale archive, configuration mismatch, wrong-chain RPC
response, host restart during reconciliation. Build AFTER the human
live-test ladder starts producing real artifacts to replay against; file
here now so it is sequenced as a WO-67 precondition input (P4 review
evidence), not an afterthought. Do not build during the proof phase.

# Batch 9 — filed 2026-07-13 (go-live infrastructure around the blocked executor)

Historical design principle: MINIMIZE THE POST-P3 DELTA. Everything keyless
and read-only around the executor is specced now and buildable now, so
the only code written after the owner signs P3 is `maker_executor.py`
itself plus its credential loading and binding hooks — the registration's
"days-long mechanical job". NOTHING in this batch adds a live order path;
WO-67 remains the only order-placing WO and stays BLOCKED behind P1-P5.
Micro-drill findings 1-7 (docs/MICRO_DRILL_RUNBOOK.md) bind as design
inputs: 5-share order minimum (size in multiples of 5; sub-5 tails ride
to resolution), orderbook clears at event start, symmetric ~2.9% taker
fees, reservations invisible in displayed buying power, resting orders
non-attributable from public data.

## WO-73 — Role-aware monitoring, credential guard, rotation drill (items 1–3 done; item 4 blocked)

Status: ITEMS 1–3 IMPLEMENTED by Codex on 2026-07-13 and reconciled to custody
Amendment A1 by WO-81 on 2026-07-14; item 4 remains BLOCKED with WO-67. The
single project wallet is authoritative and the historical
`executor_wallet_address` must stay empty. Role-aware maker/reconciliation
ledgers remain for mode/time attribution without silently summing accounts. A value-redacting
credential guard derives the actual telemetry whitelist, scans every eligible
file plus WO-65 archive manifests, and runs before either host telemetry or
archive push; any finding refuses both paths. The required ARM64 PR gate runs
the same scanner and planted-leak fixtures. A keyless rotation/revocation
harness exercises missing and invalid dummy credentials against a fail-flat
stub and requires nonzero exit, halt, zero orders/positions/exposure, and no
order action. Its artifact is telemetry-visible and WO-61 anchored. No real
credential name/value is loaded, no `.env` executor path exists, and no
paper/live executor, signing, cancellation, broker, gate, model, or sizing
path was added.

**2026-07-13 append-only correction:** role-aware fields now use versioned
maker-scoreboard and wallet-reconciliation ledgers. The pre-WO-73 files keep
their original schemas and are written with strict byte appends, preventing a
schema addition from invalidating historical WO-61 prefixes. See
`docs/incidents/2026-07-13-wo73-append-only-ledger-migration.md`.

1. Executor sub-account onboarding: config gains
   `maker_live_test.executor_wallet_address` (public identifier only,
   like the operator address). WO-62 three-way reconciliation, the
   maker-live-test scoreboard, and the operating state all become
   two-wallet aware (operator wallet vs executor wallet reported
   separately, never summed silently). Onboarding checklist for the
   owner at account creation: enable AUTO-REDEEM WINS in the
   sub-account's Trading settings (confirmed account-level on the
   operator account 2026-07-13; removes any redemption logic from
   WO-67's scope).
2. Credential guard test (promised in the approved P5 doc): a pytest
   that scans every telemetry-whitelisted directory and the archive
   manifest for credential-shaped strings (hex keys, api key/secret/
   passphrase patterns) and fails the suite on any hit. Runs in the
   WO-69 PR gate.
3. Rotation/revocation drill scripts (keyless scaffolding): a runbook +
   script skeleton that verifies the executor container fails FLAT when
   credentials are absent/invalid — testable pre-amendment with dummy
   env vars against a stub, because fail-flat is a property of the
   harness, not of real keys.
4. WO-67 AUTHORIZATION ONLY: actual `.env` credential loading in an executor
   process remains prohibited until every P1-P5 precondition passes, including
   the distinct dated P3 owner amendment authorizing a live order path.

## WO-74 — Executor replay-certification harness (buildable NOW; executor plugs in later)

Status: IMPLEMENTED by Codex on 2026-07-13. CLI
`executor-replay-certification` generates a versioned corpus from recorded
WO-44 official-book windows plus all registered synthetic stress families and
certifies an external candidate decision log/action ledger against an unchanged
contract. Exact quote-sheet membership, policy caps, 5-share multiples,
one-cycle pull/STOP cancellation, flat missing/stale behavior, heartbeat
dead-man flattening, and one-to-one action-ledger appends are independently
reported in `outputs/execution/replay_certification.json`. The keyless
reference stub proves only the harness; a missing official window or any
contract defect FAILS and blocks canary by registration. No executor,
credential, broker, signing, paper, live, cancellation, or order path was
introduced.

Rollout phase (a) of the registered WO-67 architecture, built as a
harness the executor must later pass rather than code inside it:
1. Scenario corpus: recorded official-book windows (WO-44 data) plus
   synthetic stress scenarios encoding the drill findings — event-start
   book clear, 5-share minimum, sub-5 tails, spread crossings, news gap
   through the quote, stale websocket, kill-criteria day.
2. Certification contract (the harness asserts, on a candidate
   executor's decision log): never quotes outside the sheet; never
   exceeds policy caps; sizes in multiples of 5 shares; cancels within
   one cycle of pull_quotes_now/STOP; goes flat on missing/stale input;
   dead-man flatten fires on heartbeat gap; ledger rows append for every
   action.
3. Output: `outputs/execution/replay_certification.json` — a dated PASS/
   FAIL artifact per candidate build; a FAIL blocks canary by
   registration.
4. Buildable now against a stub decision log; the real executor must
   pass unchanged post-amendment.

## WO-75 — Live-ops control plane (monitoring done; binding hooks blocked with WO-67)

Status: ITEMS 1, 3, AND 4 IMPLEMENTED by Codex on 2026-07-13. The independent
VPS scheduler now reads the registered future execution-ledger and heartbeat
contracts every five minutes, renders `ABSENT` until a ledger exists, and
publishes mode, orders, exposure/cap, action age, freshness, dead-man,
kill-scoreboard, reconciliation, and deduplicated owner-alert evidence to
`outputs/execution/executor_status.json`, generated operating state, and the
dashboard. The scheduler never writes the heartbeat. WO-75 item 2 remains
BLOCKED with WO-67 until every P1-P5 precondition passes and is machine-marked
false; no executor, credential, signer,
broker, cancellation, paper, or live order path was added.

1. Executor status surface: dashboard panel + operating-state rows —
   mode (absent/replay/canary/portfolio), open orders, exposure vs stage
   cap, last action age, dead-man countdown, kill-criteria scoreboard.
   Reads the (future) execution ledger; renders ABSENT until one exists.
2. STOP propagation contract: requote_alerts `pull_quotes_now`/`STOP`
   and decision-policy kill states become BINDING inputs for the
   executor (cancel-all within one cycle) while remaining advisory for
   the human lane. Registered here; wired post-amendment.
3. Owner alerting: kill trigger, dead-man trigger, reconciliation
   discrepancy > threshold, or SLO breach on executor freshness sends
   the existing notification path (email) — alert plumbing buildable now
   against synthetic events.
4. Heartbeat spec: executor writes a heartbeat file each cycle; the ops
   scheduler (already the watchdog for everything else) alarms on gap >
   30 min INDEPENDENTLY of the executor's own dead-man logic — two
   separate processes must both fail for a silent stall.

## WO-76 — Pre-registered canary promotion/demotion contract (REGISTRATION, effective on filing)

Registered now so no discretion exists later. Canary = ONE market,
minimum reward-eligible size, single project account under A1 sequencing.
1. PROMOTION canary -> portfolio requires ALL, measured over >= 7
   consecutive canary days: three-way reconciliation clean every day
   for the single project wallet in its executor mode/time window; fills <= 2x model; measured post-fill
   markout within the charged adverse-selection budget; reward receipts
   >= 0.5x model; zero uncontrolled states (every halt explained in the
   anchored ledger); replay certification still green on the deployed
   SHA.
2. DEMOTION canary -> halt + human review on ANY of: kill criterion
   fires; reconciliation discrepancy > $1 unexplained for 24h; two
   consecutive days fills > 2x model; any action outside the quote
   sheet (auto-halt, no threshold).
3. Portfolio mode inherits the same daily checks at WO-50 ladder
   capital; ladder progression stays owner-confirmed per the frozen
   policy.
4. Amendments to this contract: tighten-only before first canary day;
   any loosening requires a dated owner amendment.

## WO-77 — Requote-alert ticket completeness (BUG, blocks the $100 stage)

Status: IMPLEMENTED by Codex on 2026-07-13. Production diagnosis confirmed
the two current carrier condition IDs were absent from all 126 websocket
targets, while the persisted pre-WO-66 portfolio had blank token, URL,
outcome, tick, bid, and ask fields. Requote evaluation now repairs legacy
metadata from the matching public Gamma row, uses at most one bounded batch
`/books` request per cycle when websocket coverage is absent, and records the
source plus complete ticket fields in `requote_alerts.json`. Repaired/current
quote-sheet tokens receive first-priority websocket slots on every live-loop
asset refresh. A real missing Gamma/CLOB book remains fail-closed; no gate,
order, signing, credential, paper, or live path changed.

Observed 2026-07-13: `requote_alerts.json` stuck in `pull_quotes_now`
three hours after deploy with `incomplete_order_ticket` and
`missing_live_bid_ask` on every quote-sheet market. Hypothesis: the
websocket collector's tracked-market set is discovery-selected and does
not include the quote sheet's carrier markets (long-dated geopolitical),
so tickets can never complete and the evaluator fails closed forever.
1. Diagnose: confirm whether the quote-sheet condition_ids are in the
   websocket subscription set; report the actual gap in the WO record.
2. Fix: ensure every current quote-sheet/portfolio market has a live
   bid/ask source — subscribe the websocket to quote-sheet markets on
   sheet refresh, with a REST book snapshot fallback (rate-limited)
   when the socket lacks coverage. Populate the full ticket fields
   (URL, outcome, token, tick, bid, ask) from the same source.
3. Guard test: a synthetic quote sheet whose market is absent from the
   socket set must produce a complete ticket via the fallback, and
   `quotes_ok` must be reachable in test. The fail-closed behaviour on
   a REAL missing book stays (that part is correct).
4. Constraint: read-only as ever; no gate or order path touched. This
   must land before the $100 human stage starts — the requote loop is
   that stage's safety net.

## WO-89 — Self-anchored 24h activity window: phantom fills never expire (post-deploy telemetry read 2026-07-15; ROOT CAUSE CORRECTED by line audit same day)

**2026-07-15 — WO-89 implemented by Codex.** The maker live-test scoreboard now anchors its
24-hour reward and fill window to the run's wall-clock timestamp, never to the newest venue
activity. Current second-scale timestamps remain unchanged, future millisecond-scale timestamps
are normalized defensively, and invalid timestamps remain excluded without changing owner-
activity attribution or any alert threshold. ARM64/Python 3.11 verification passed: focused lint,
13 focused maker-live tests, repository lint, and the full 1,210-test suite.

OBSERVED IN PRODUCTION (vps-telemetry snapshot 2026-07-15T13:00Z, deployed
f5241c8): `maker_live_test_attribution_history.csv` shows
`fills_last_24h_raw=2` on EVERY refresh from 08:00Z through 12:55Z — the two
2026-07-13 operator drill fills, more than 36 hours old. At 08:50Z
`modelled_fills_per_day` dipped to 0.39 and the phantom count re-fired
`STOP_fills_outrunning_model`.

ROOT CAUSE (corrected 2026-07-15 by the orchestrator's line audit; the
initially filed millisecond-unit theory is WRONG and is disproven by the
same data — the two drill fills are hours apart, and a millisecond-scale
`- 86400` window spans only ~86 seconds, so at most one could have counted):
`maker_live_test._wallet_score` computes

    now_seconds = max(activity stamps);  day_ago = now_seconds - 86400

anchoring the "last 24h" window to the NEWEST VENUE ACTIVITY STAMP instead
of the wall clock. On a quiet account the newest fill IS "now" forever, so
the final day of activity never ages out. Consequences:
1. `fills_last_24h` never decays on a quiet account: permanent phantom fill
   counts, recurring false STOPs whenever the modelled rate dips, and during
   a live stage a broken WO-50 consecutive-ok-day ladder. On an active
   account the window slides with each new fill, also inflating the count.
2. `rewards_usd_last_24h` equals all-time rewards on a quiet account.
3. NOT affected (correcting the earlier filing): WO-88 owner-activity
   matching is sound — venue activity stamps are unix seconds and compare
   correctly against the anchored operator log inside the registered ±300 s
   window. No change to `owner_activity_attribution` is required.

REGISTERED FIX (single small change; thresholds and windows untouched):
- `day_ago` derives from the wall clock: `datetime.now(timezone.utc)`
  (or the run's `generated_at` stamp) minus 86400 — never from the maximum
  observed activity stamp. The 86400 s window, `fill_alert_multiple`, and
  every kill threshold stay byte-identical.
- Defensive-only hardening allowed alongside: normalize any stamp
  > 10_000_000_000 as milliseconds (the registered
  `corpus_retention._row_time` convention) before comparison, so a future
  venue unit change cannot silently re-freeze the window in either
  direction. This must not alter behaviour for current second-scale stamps.
- Fail-safe preserved exactly: an unparseable stamp remains 0.0 — never
  counted as recent, and (unchanged) unmatched fills stay maker_test.
- Registered consequence: with a wall-clock window the 2026-07-13 drill
  fills age out immediately, which resolves the 2026-07-14 STOP by the
  aging-out arm of WO-88's registered review note. NO retroactive
  operator-log entry may be created for those drills (append-only anchored
  log; a today-logged entry cannot honestly match a 2026-07-13 trade inside
  ±300 s). The runbook remains the human record for pre-WO-82 drills.
- Non-defect noted for the record: `operator_log_not_anchored` in the same
  snapshot is CORRECT behavior — `execution/stage_operator_log.csv` does not
  exist because the drills predate WO-82 usage. Future owner drills must be
  logged through the stage-operator path (`drill_trade`/`maintenance_trade`)
  before execution.

Tests: (a) fills/rewards older than 24h of WALL-CLOCK time are excluded even
when they are the newest activity on the account; (b) fresh fills within the
wall-clock day still count and still trip the alert against the modelled
rate; (c) second-scale stamps behave identically before/after the defensive
millisecond normalization; (d) millisecond-scale fixtures normalize and age
out on schedule; (e) unparseable stamps are excluded and remain maker_test.

## WO-92 — Make the WO-89 defect class mechanically catchable (recorded fixtures + clock-advance tests + boundary sweep) — done (2026-07-15, PR #234)

Registered 2026-07-15 together with `docs/ENGINEERING_STANDARDS.md` (binding
on all future WOs). Three same-week production defects (WO-89 window
anchoring, WO-90 non-atomic shared writes, WO-91 corpus starvation) shared
one cause: system invariants nobody specified, so no test could fail. This
WO retrofits the standards onto the EXISTING codebase so this class is
caught by machinery, not by post-deploy telemetry reads.

Scope (reporting/test infrastructure only; no gate, threshold, policy, or
order-path change; every item tighten-only):
1. RECORDED FIXTURE CORPUS: capture real (sanitized) payloads from the four
   external surfaces — data-api `/activity`, `/positions`, `/trades`; CLOB
   `/book(s)` and `/prices-history`; Gamma `/markets` — into
   `tests/fixtures/recorded/`, with a small README stating capture date and
   sanitization rule. Every existing parser of these payloads gains at least
   one test that replays the recorded fixture (S4). Hand-written fixtures
   may remain, but cannot be the only coverage for a parser.
2. CLOCK-ADVANCE PROPERTY TESTS: for every existing time-window computation
   (maker_live_test 24h fills/rewards, decision-policy kill-input freshness,
   watchdog completion freshness, requote staleness, candidate staleness,
   corpus retention ages): assert an event inside the window, advance the
   injected clock past the boundary, assert it leaves (S1). Code that cannot
   inject a clock gets the minimal seam to allow it (injection parameter
   defaulting to wall clock — no behavior change).
3. BOUNDARY NORMALIZATION SWEEP: one shared helper for external-timestamp
   normalization (ms/seconds/ISO, fail-safe on unparseable); every ingestion
   site of venue timestamps routes through it; a grep-style guard test pins
   that `maker_live_test`, `owner_activity_attribution`, `flow_toxicity`,
   `trade_print_collector`, and `wallet_reconciliation` contain no ad-hoc
   `safe_float(row.get("timestamp"))` pattern outside the helper.
4. SHARED-WRITE AUDIT TEST: a test enumerates writers of artifacts under
   `outputs/` reachable from two schedule paths (safety pulse + harvest) and
   asserts each uses an atomic write utility (S2), so the WO-90 class cannot
   silently return with the next new writer.
Day-after check: `outputs/ops_scheduler/training_harvest.json` shows the new
test files in a green full-suite VPS run recorded in the PR; subsequent
deploys keep `deployment_health` green with no new watchdog incident
class regressions.

**2026-07-15 — WO-92 implemented by Codex.** Sanitized payloads recorded from
Data API `/activity`, `/positions`, and `/trades`, CLOB `/book`, `/books`, and
`/prices-history`, and Gamma `/markets` now replay through the current parser
inventory. Venue seconds, milliseconds, and ISO strings enter through
`utils.normalize_external_timestamp`; malformed, negative, and non-finite
values return `None`. Clock-advance properties cover maker-live 24-hour
reward/fill counts, decision-policy freshness, scheduler-completion freshness,
requote age, candidate close age, corpus retention, and websocket feature
retention. The latter now anchors to the injected run clock rather than the
newest observed row. A static writer registry covers every current quote-sheet
writer reachable from the harvest/safety-pulse paths and the secondary hourly
patch; all publish complete text via `write_text_atomic`. No gate, threshold,
registered policy constant, signal, sizing, broker, credential, cancellation,
paper/live permission, or order path changed.

S7 written review (methods and bounded findings):
1. **S1 — static read + ARM64 tests:** `utils.normalize_external_timestamp`
   owns the `>10_000_000_000` millisecond rule; ingestion adapters call it,
   and `websocket_normaliser._retained_feature_rows(..., as_of=...)` derives
   its cutoff from the run clock. Seven boundary tests advance past each
   registered edge and observe the row/count becoming stale or excluded.
2. **S2 — static read + interleaving regression:** maker study, decision
   policy, requote, reconciliation, hourly-adverse, and requote-notification
   text writers call `write_text_atomic`; the audit test rejects plain
   `.write_text(` in this registry. JSON/CSV paths retain the existing atomic
   utilities. The worst remaining read-modify-write race is a complete but
   one-cycle-stale human patch, never a torn file.
3. **S3 — static dependency inventory:** recorded surface -> parser mappings
   are explicit in `RECORDED_PARSER_COVERAGE`; the only producer-side change
   is clock normalization/retention, and absent coverage remains empty,
   ungradeable, stale, or retained-unknown rather than promoted.
4. **S4 — recorded replay + property tests:** seven dated recorded fixture
   files preserve live response nesting/scalar types, and the parser suite
   replays them through Data API, CLOB, and Gamma consumers. Synthetic tests
   remain only as additional edge-case coverage.
5. **S5 — static path review + tests:** malformed timestamps become `None`;
   they cannot earn freshness or owner attribution, cannot form a valid book
   row, and are retained rather than silently deleted by corpus hygiene.
   Missing/malformed endpoint payloads keep their existing empty/pending or
   ungradeable behavior.
6. **S6 — spec/read:** the exact day-after artifacts remain
   `outputs/ops_scheduler/training_harvest.json` and deployment-health/
   watchdog telemetry as registered above; production verification follows
   the guarded main deployment.
7. **Frozen surfaces + executed verification:** diff review found no changes
   to gates, thresholds, policy constants, or order paths. On the ARM64 VPS
   (Python 3.11), repository Ruff passed; the new/retention suite passed 30/30,
   the touched-component suite passed 201/201, a concurrency/broker harness
   confirmation passed 45/45, and the full suite passed 1,233/1,233. These
   methods found no further defect in the reviewed scope; they do not prove
   absence of defects outside it.

## WO-91 — Pre-event CLV diagnostic starved: frozen crypto up/down floods the price-history collection cap (telemetry read 2026-07-15)

**2026-07-15 — WO-91 implemented by Codex.** The price-history collector now
requests every distinct final-ledger token first, labels that coverage
`focus_final`, and excludes frozen crypto up/down contracts from the general
newest-token fill. Missing, malformed, empty, and failed priority inputs remain
explicitly ungradeable. Verification on the ARM64 VPS passed focused lint, 62
collector/closing-line/verdict tests, repository lint, and the full 1,217-test
suite, including replay of a sanitized recorded CLOB `/prices-history` payload.

OBSERVED IN PRODUCTION (verdict artifact 2026-07-15T15:30Z on deployed
3419fd0): `pre_event_clv_diagnostic` reports 0/31 units gradeable — all 45
finals `no_official_in_band_observation_at_or_before_cutoff` — so the WO-87
diagnostic renders empty on every dashboard. Same snapshot,
`historical_price_history_quality.csv`: ALL 500 requested tokens are crypto
`*-updown-5m` markets (fetch_source `short_close_window`, ~4 points each,
2,146 snapshot rows total).

ROOT CAUSE: `price_history_collector._clean_resolution_rows` ranks clean
settlements by close_time DESCENDING and keeps `[:max_tokens=500]`. The
FROZEN crypto up/down diagnostic lane settles a market every five minutes,
so the newest-500 window contains nothing else; Gate-A focus finals never
enter the snapshots corpus, and the pre-event diagnostic is structurally
ungradeable. Secondary waste: the entire daily collection budget (500
fetches) is spent producing 5-point histories for a lane AGENTS.md freezes
against further collection priority.

REGISTERED FIX (collection-side only; no gate, threshold, or diagnostic
definition changes):
1. PRIORITY SLOTS: every distinct `token_id` present in
   `governance/closing_line_final_history.csv` (the Gate-A grading ledger)
   is ALWAYS included in the collector's requested token set, deduped,
   before any general slot is filled. The registered [0.05, 0.95] /
   close−6h diagnostic definition is untouched — this only guarantees its
   input coverage.
2. EXCLUDE the frozen crypto up/down family (updown slug/cohort family)
   from the general newest-500 fill. Their settlement artifacts already
   exist; the freeze forbids spending collection priority on them.
3. The corpus write stays overwrite-per-run; with priority tokens
   guaranteed each run, rotation is no longer a coverage risk.

Deadline note: must land before the 2026-07-19/20 final taker read — the
diagnostic's entire registered purpose is to stand beside the
settlement-return gate AT that read to separate pricing edge from outcome
luck.

Tests: (a) focus-final tokens are requested even when >500 updown
settlements are newer; (b) updown tokens cannot displace priority tokens;
(c) a synthetic focus final grades end-to-end (collector corpus ->
diagnostic `gradeable`) after the change; (d) the quality report labels
priority vs general tokens so starvation is visible if it ever recurs.

Engineering-standards contract (registered before implementation, 2026-07-15):
- **Clock and units (S1):** this change adds no recency window; priority is
  ledger membership. Existing close timestamps remain UTC-parsed and are used
  only to request the recorded close-relative history window.
- **Shared writes (S2):** the only written artifacts are
  `historical_price_snapshots.csv`, `historical_price_history_quality.csv`, and
  `historical_price_history_summary.json`; they retain `write_csv`/`write_json`
  atomic replacement. No cadence or second writer is added, so an interleaving
  reader sees one complete old or new artifact, never a partial file.
- **Dependency and recorded reality (S3/S4):** the producer is
  `price_history_collector`; the consumer is `profit_verdict`'s
  `pre_event_clv_diagnostic`; coverage is every distinct final-ledger token.
  Collector tests replay a sanitized recorded CLOB `/prices-history` payload in
  addition to the required synthetic end-to-end focus-final case.
- **Fail-safe direction:** when the final ledger is missing or a final token is
  malformed, no priority row is invented and the summary reports zero available
  priority tokens; when a priority fetch is missing, empty, or fails, its quality
  row says `empty_history`/`fetch_error` and the diagnostic remains
  `pre_event_clv_ungradeable`. Frozen up/down rows are never used to disguise
  either failure.

Day-after check: `outputs/polymarket_model_governance/historical_price_history_summary.json`
has `priority_tokens_missing=0`, `priority_tokens_requested=priority_tokens_available`,
and `general_updown_tokens_requested=0`; every priority token has a
`collection_priority=focus_final` row in
`historical_price_history_quality.csv`. The next
`outputs/polymarket_model_governance/profit_verdict.json` either increases
`pre_event_clv_diagnostic.finals_gradeable` or remains explicitly ungradeable
only where that priority quality row reports `empty_history`/`fetch_error`.

## WO-90 — Atomic quote-sheet writes (concurrent decision-policy under the WO-85 safety pulse)

**2026-07-15 — WO-90 implemented by Codex.** Both registered quote-sheet writers now publish a
fully written, flushed sibling temp file with `os.replace`, so concurrent decision-policy and
daily-harvest refreshes can produce a benign stale patch but never a torn human sheet. No sheet
content, threshold, policy, gate, or trading path changed. ARM64/Python 3.11 verification passed:
focused lint, 44 focused writer/policy/study tests, repository lint, and the full 1,211-test suite.

Small hardening found by the same line audit. The WO-85 safety pulse runs
`decision-policy` on a 15-minute cadence while the daily harvest may run its
own `decision_policy` step concurrently. All JSON artifacts already write
atomically (temp + `os.replace`), but `outputs/maker_carry/maker_quote_sheet.md`
does not: `maker_carry_study._write_quote_sheet` uses a plain `write_text`,
and `live_test_decision_policy._patch_quote_sheet` does a non-atomic
read-modify-write of the same file. A concurrent pair can produce a torn or
stale-patched human quote sheet for one cycle (reporting-only; self-heals on
the next refresh; no gate reads the sheet).

Fix: write the sheet via temp-file + `os.replace` in BOTH writers (the
`_patch_quote_sheet` read still races benignly; after this change the worst
case is a sheet missing the newest policy block for one cycle, never a torn
file). No content, threshold, or policy change. Tests: patching a sheet
mid-write cannot leave partial content; both writers produce byte-complete
files under interleaving.

## WO-88 — Attribution-aware kill scoreboard under A1 (live false-STOP 2026-07-14)

OBSERVED IN PRODUCTION 16:26Z: the kill scoreboard fired
`STOP_fills_outrunning_model` (fills_last_24h=2 vs modelled 0.85x2.0)
on the OWNER'S OWN micro-drill fills — the 2026-07-13 drill buy+sell on
the shared A1 account. Advisory-only today (no live stage, no resting
quotes), but during a funded stage the same mechanism would (a) fire a
false kill halting the stage and (b) break the WO-50 consecutive-ok-day
ladder every time the owner drills, sweeps, or redeems. This is exactly
the attribution consequence Amendment A1 registered ("sequencing for
attribution"; "mode labels distinguishing executor-era activity") — now
with its first live demonstration.

Registered fix (fail-safe direction preserved):
1. Fills are attributed against the ANCHORED operator log (the WO-82
   stage/drill log): a fill whose timestamp falls inside a registered,
   owner-recorded drill/maintenance window AND matches a logged owner
   action is classified `owner_activity` and excluded from the
   fills-outrunning-model kill count; it is still reported, separately.
2. FAIL-SAFE DEFAULT: any fill NOT matched to a logged owner action
   still counts as a maker-test fill — unknown fills must keep tripping
   the alarm. Exclusion is only ever earned by the anchored log entry,
   never inferred.
3. The 2026-07-14 STOP itself is adjudicated by this WO's registered
   review note: cause = drill fills, no quotes were live, no capital at
   risk; scoreboard may reset to ok once the drill fills age out of the
   window or are matched under (1). This note satisfies the registered
   "review before any resume" requirement for THIS event.
4. Tests: logged drill fill excluded; unlogged fill still trips;
   mixed window counts only unlogged; ladder consecutive-day logic
   ignores owner_activity days rather than breaking the streak.

**Implemented by Codex 2026-07-15.** The immutable WO-82 operator-log schema
now accepts explicit `drill_trade` and `maintenance_trade` action values. A
fixed five-minute matcher requires an exact anchored-prefix human row plus
time, condition, side, price, and cumulative-size agreement before classifying
a public activity-feed trade as `owner_activity`. Raw, owner, and maker-test
counts are reported separately in `outputs/maker_carry/maker_live_test.json`
and append to the new anchor-enrolled
`outputs/maker_carry/maker_live_test_attribution_history.csv`; all unmatched or
unanchored fills remain maker-test fills. Owner-only days neither earn nor
break ladder evidence. No normal quote action, gate threshold, sizing, broker,
credential, cancellation, paper/live permission, or order path changed.

Also appended to WO-85 (same incident family, observed again today):
the harvest interval stamp is touched at START (touch-before-run), so a
container restart mid-harvest consumes the day's slot and silently skips
the whole day; today's harvest has not COMPLETED since 2026-07-13 08:24
while every other job runs. WO-85's completion-freshness alarm must key
on successful COMPLETION stamps, and the interval check must re-arm when
a started run never completed.

## WO-87 — Gate A grades settlement return, not closing-line value (HEADLINE audit finding 2026-07-14; owner+governance decision required)

The most consequential finding of the deep audit. Empirical, from
`closing_line_final_history.csv` (61 finals):
- 51/61 finals grade against a NEAR-SETTLED price: 26 at line_price<0.10,
  25 at >0.90 (values like 0.001 and ~1.0).
- 43/61 have |clv|>0.3 (e.g., entry 0.47 -> line 1.0 -> clv +0.53; entry
  0.48 -> line 0.001 -> clv -0.48).

Mechanism: prediction markets trade THROUGH the event to resolution, and
`closing_line._fetch_price_history_close_line` grades against the last
tradeable price at/before `close_time` (the market's actual close, which
is near settlement). The `0 < price < 1` filter excludes only EXACTLY 0/1,
so a resolved-NO market at 0.001 passes. Result: the quantity registered,
documented, and interpreted as "closing line value / beat-close" is in
practice PER-DOLLAR SETTLEMENT RETURN, and `beat_close` is effectively
"was the position profitable at its entry price." The Gate A sign test is
therefore a WIN-RATE test, not a test of beating a sharp pre-event
closing consensus.

Why material (affects the 2026-07-19/20 verdict's MEANING, not plumbing):
1. Semantics: a position can settle profitably yet have had NEGATIVE true
   CLV (overpaid vs the pre-event line; underdog won) and vice versa. The
   current metric conflates "model has pricing edge" with "model picked
   the winner".
2. Statistics: settlement returns are high-variance/outcome-driven, not
   small line-moves; the registered sign-test threshold and the mean's
   interpretation were framed in CLV (line-move) terms and need review
   under settlement-return semantics.

NOT unilaterally a code bug -- grading actual profitability is arguably a
MORE honest $100/month test than sports-CLV. This is a
registration/semantics mismatch whose resolution is an owner + governance
call and CANNOT be changed silently (it moves the verdict). Options to
decide, dated and tighten-only per the amendment protocol:
  (A) Keep settlement grading but RELABEL honestly: it is "net settlement
      return per dollar", the gate is a win-rate + positive-return test;
      re-examine the sign-test alpha and the mean threshold for the
      higher-variance distribution; update docstring, registered rule,
      and payload field names so nothing is called "closing line value"
      that is not.
  (B) Restore true CLV: define a pre-convergence reference (e.g., the line
      at event-start / last price with the market still competitive, e.g.
      within [0.05,0.95] a registered N hours before close) and grade
      against THAT, so CLV measures pre-event line-move edge. Keep
      settlement return as a SEPARATE reported metric.
  (C) Report BOTH as distinct, separately-registered metrics and decide
      which one Gate A binds on.
Until decided, the interim read must state the caveat: the current
"mean CLV" (+0.038, ~48% of units positive) is a SETTLEMENT-RETURN
statistic near break-even, not a closing-line-edge statistic.
No code change ships under this WO without the dated owner+governance
decision; it never places or authorises an order.

**DECIDED 2026-07-14 (owner, adopting the orchestrator recommendation =
option C, tighten-only form). Now BUILDABLE with this exact spec:**
1. RELABEL, DON'T SWAP. Gate A continues to bind on the metric it has
   always computed — now honestly named. The registered rule text,
   module docstrings, payload field labels, dashboard rendering, and
   reports must call it what it is: unit mean NET SETTLEMENT RETURN per
   dollar (pre-fee), with `beat_close` renamed/labelled
   `settled_profitable`. JSON field names may keep one release of
   legacy aliases for downstream compatibility, but every displayed
   label and registered rule string changes. Binding metric, thresholds,
   alpha, floors: UNCHANGED (a mid-study metric swap would be data
   snooping under the registry's legacy-adjudication rule).
2. ADD the separately-registered diagnostic: TRUE PRE-EVENT CLV.
   Reference line = the last official price-history observation at or
   before (close_time − 6h), additionally required to lie within
   [0.05, 0.95]; if no qualifying observation exists the unit is
   `pre_event_clv_ungradeable` (never guessed, never defaulted).
   pre_event_clv = reference_line_price − entry_price, same token,
   same clustering into units as Gate A. Reported per unit and in
   aggregate ALONGSIDE the gate metric; feeds NO gate in this study;
   its purpose is to show whether pricing edge exists distinct from
   outcome luck, and to inform any future taker registration.
3. CAVEAT IS MANDATORY: every interim/final verdict rendering carries
   one registered sentence stating the gate metric is settlement
   return, not closing-line edge, with pre-event CLV shown beside it.
4. Tests: relabeled strings asserted; a synthetic near-settled final
   grades identically under the renamed gate metric; a synthetic
   pre-convergence history produces the correct pre_event_clv; a
   history with no qualifying pre-event observation yields
   ungradeable; gate thresholds byte-identical to the registration.

**Implemented by Codex 2026-07-15.** The binding arithmetic, thresholds,
alpha, sample floor, clustering, and order permissions remain unchanged.
`profit_verdict.json` now exposes honest settlement-return primary fields,
one-release legacy aliases, the mandatory caveat, and a same-unit non-binding
pre-event CLV diagnostic sourced only from official in-band history at or
before close minus six hours. The decision dashboard renders both metrics.

## WO-86 — Kill-switch staleness guard: stale safety data must STOP, never clear (decision-policy audit 2026-07-14)

MATERIAL, safety-critical for the live stage. Found by line-reading
`live_test_decision_policy.py::_kill_criteria`. The kill criteria evaluate
whatever `maker_live_test.json` / live-history data is present, with NO
freshness check. Pre-live (no data ever) it correctly reads `clear`. But
once a LIVE stage is active, if that scoreboard data goes stale (trade_prints
stall, container kill, harvest tail-starvation recurrence, API outage), then
`net_score` reads None, the daily list is empty, every criterion evaluates
`not triggered`, and kill silently returns `clear` — the money-protecting
kill switch disengages exactly when it is needed, reading old data that
predates the problem. Same silent-staleness class as WO-84/85, but this one
guards real capital.

Registered fix (fail-safe = STOP when blind):
1. Distinguish "never had data" (pre-live: clear is correct) from "had data,
   now stale" (live: must stop). Activate the guard once live trading is
   configured or a live stage is active (ladder_stage_permitted > 0 or a
   live-execution ledger/heartbeat exists).
2. When active, if the kill-input data (maker_live_test / live-history
   latest observation) is older than a registered freshness threshold,
   force `stop_quoting_review_before_resume` and set an explicit
   `kill_data_stale` flag with the measured age. The decision policy must
   NEVER emit a fund_* or continue action on stale safety data.
3. The freshness threshold is registered and tighten-only; the dead-man
   switch (30-min heartbeat) and requote STOP remain independent layers —
   this closes the gap that all three read data that could itself be stale.
4. Wire a WO-78 registration: kill-input staleness during a live stage is
   an immediate INCIDENT + owner alert.
Tests: (a) pre-live empty data still reads clear; (b) live stage + stale
kill-input forces stop with kill_data_stale; (c) fresh data preserves the
existing behaviour exactly (no change to a live, fresh evaluation).

**Implemented by Codex 2026-07-15.** A registered, tighten-only 30-minute
maximum now activates when a human maker-test/live configuration, positive
ladder stage, or executor ledger/heartbeat evidences live operation. Missing
or stale latest maker-live input sets `kill_data_stale`, triggers the registered
kill row, and forces `stop_quoting_review_before_resume`; fresh and genuinely
pre-live evaluations retain their prior decisions. WO-78 opens an immediate,
deduplicated owner-alert incident from this state. No executor or order path
was added.

## WO-85 — Harvest resilience + freshness alarm + Gate-A clustering guard (deep-dive audit 2026-07-14)

Status: IMPLEMENTED by Codex on 2026-07-15, including the completion-stamp
correction. CLI `training-harvest` now writes
`outputs/ops_scheduler/training_harvest.json` after every child, continues
after failures, applies a tighten-only six-hour start deadline, and always
attempts corpus retention and ledger anchoring as its final two steps. The
scheduler preserves `last_success_utc`; WO-78 opens an immediate owner-alert
incident when any registered periodic lane exceeds its completion ceiling
(25 hours for the daily harvest). Gate A now remains pending with
`insufficient_clustering_coverage` when more than 10% of eligible finals use
per-position fallback identities. No threshold was loosened and no paper/live
or order path was invoked.

The scheduler now keys the 24-hour harvest interval and the intraday offset on
the last successful completion, persisted in `last_success_training_harvest`
and migrated from `status.json` when necessary. A start writes only an attempt
stamp; a failed or interrupted run cannot consume the next daily slot.

**2026-07-15 completion correction.** Long scheduler jobs remain serialized,
but now execute as bounded child processes while the parent runs the registered
five-minute degraded-state/dashboard pulse. The read-only maker attribution,
kill-input, and requote pipeline has an independent 15-minute cadence, so a
multi-hour harvest cannot starve WO-86/WO-88 safety evidence. The trade-print
collector no longer duplicates that maker-safety writer. Failed or interrupted
harvests remain due from the last successful completion, but a registered
15-60 minute retry backoff (30 minutes by default) prevents a zero-delay heavy
retry loop.

Deep process audit found the daily `training_harvest` had not produced
fresh output in ~27h (factsheet, ledger anchor stale; disk climbed to
84%) while all short-cadence jobs were current. Root-cause CLASS, not
just the trigger:

1. `set -e` ALL-OR-NOTHING TAIL STARVATION. The harvest is one
   `( set -e; <20 timeout'd steps> )` subshell. The FIRST step that
   times out (exit 124) or errors aborts every remaining step. The two
   most safety-relevant steps run LAST — `corpus-retention` (step 19,
   disk) and `anchor-ledgers` (step 20, tamper-evidence) — so any early
   slowdown/failure silently starves disk cleanup AND anchoring. This
   exactly matches the observed disk-climb + stale-anchor signature.
   The immediate trigger (maker-fill-replay OOM in the 2g scheduler cap)
   is addressed by PR #207, but the fragility is independent of trigger.
   Fix: guarantee the disk/anchor tail runs regardless of earlier-step
   outcome — a `trap`/`finally` that always executes retention +
   anchor, OR make every step best-effort (drop `set -e`) with a
   recorded per-step sub-status so a failing step is VISIBLE, not
   silently swallowed by an aborted run. Record each step's exit in an
   artifact so a partial harvest is legible.

2. NO WHOLE-JOB TIME BUDGET. 20 x up-to-1800s is an unbounded wall
   clock that can overrun into the next daily cycle. Add an overall
   harvest deadline; steps beyond it are skipped-with-reason, not
   killed mid-write.

3. NO HARVEST-COMPLETION FRESHNESS ALARM (the monitoring gap that let
   this run ~27h unnoticed). WO-78 catches a job that RUNS and exits
   nonzero, never a scheduled job that silently stops COMPLETING. Add a
   registered freshness rule: `training_harvest` must record a
   successful completion within 25h, else INCIDENT + owner alert. Apply
   the same "expected-cadence job completed within N" check to every
   daily/periodic job, not just the harvest — the general fix for
   silently-stalled dispatch.

4. LATENT GATE-A CLUSTERING GUARD (profit_verdict `_clustered_focus_finals`).
   The unit key falls back `market_id -> market_slug -> shadow_position_id`.
   If `market_id`/`slug` ever stop populating, each final becomes its own
   unit and INFLATES the sign test (anti-conservative — could make the
   verdict pass too easily). Not currently active (29 units < 42 finals
   proves clustering works). Guard: if a material fraction of finals
   fall back to per-row position-id nodes, mark Gate A
   `insufficient_clustering_coverage` rather than counting inflated
   units. Tighten-only; reporting/gate-safety only.

Tests: (a) a harvest step failing mid-list still runs retention+anchor
and records the failure; (b) freshness rule trips at 25h; (c) planted
finals with missing market_id trip the Gate-A clustering guard.

## WO-84 — Reconciliation bridge-deposit blindness + watchdog coverage gap (audit 2026-07-14)

Status: IMPLEMENTED by Codex on 2026-07-14 in PR #205. The Data API leg now
retains its raw activity-only cash, normalizes NAV with the already registered
external-deposit baseline when the feed is short of it, and explicitly reports
`reconstruction_incomplete_external_deposit` rather than claiming the feed is
complete. Deploy acceptance requires that provenance. The WO-78 wallet
registration now uses a clean/explained allowlist, migrates the legacy partial
counter, and monitors discrepancy, unavailable, error, and unknown sibling
states under the existing tighten-only timing. A copied real-wallet replay
changed the false $13.059481 discrepancy to clean while preserving the raw
-$0.193 activity reconstruction and naming the $12.923349 baseline adjustment.
No gate, sizing, signing, funding, paper broker, live order, or cancellation
path changed.

Full-system gremlin audit found two linked reporting-only defects. Both
are the same class: a real not-clean state that is either false or
silently unmonitored. No gate/order/sizing impact.

FINDING 1 — data_api leg is structurally blind to bridge deposits.
After the internal NAV baseline was registered (#193), the three-way
reconciliation reads `DISCREPANCY` permanently: internal=$12.923349 and
onchain=$12.787217 agree within the ~$0.14 drill cost, but the data_api
leg reconstructs cash from the venue ACTIVITY FEED, which never shows a
Solana-bridge deposit, so it reports nav=-$0.14 with
`activity_complete=true` (it wrongly believes it is complete). A
permanent false-red (a) withholds the A1 sweep advisory forever and
(b) masks any FUTURE real discrepancy behind an always-red signal.
Fix: make the data_api leg deposit-aware — either apply the same
configured external-deposit baseline the internal leg uses, or, when a
configured external deposit exists that the activity feed did not
capture, classify the leg `reconstruction_incomplete_external_deposit`
(a known-incomplete leg) so the three-way reports CLEAN on
internal≈onchain with data_api explicitly flagged incomplete — never a
false DISCREPANCY between three "complete" legs. `discrepancy_note` must
state the cause instead of `None`.

FINDING 2 — the degraded-state watchdog only matches `partial`.
`degraded_state_watchdog.py:366` sets `degraded = status == "partial"`.
When the reconciliation moved partial -> DISCREPANCY, the watchdog went
`healthy_or_out_of_scope` — a genuinely not-clean reconciliation is now
UNMONITORED because the state transitioned out of the one watched value.
This is the general gremlin: monitoring keyed to one specific degraded
label misses sibling degraded labels. Fix: the reconciliation
registration (and a review of every WO-78 registration) must flag ANY
not-clean terminal state — `partial` OR `discrepancy` OR `unavailable`
OR `error` — not a single hard-coded string. Registered healthy states
should be an allowlist; anything outside it is degraded.

Tests: (a) synthetic reconciliation with a configured external deposit
the activity feed lacks reads CLEAN with data_api flagged incomplete;
(b) a DISCREPANCY reconciliation trips the watchdog; (c) the healthy
allowlist rejects an unknown status.

## WO-83 — Make Tier-0 maker validation functional (fill-replay coverage)

Status: IMPLEMENTED by Codex on 2026-07-14 in PR #203. CLI
`collect-maker-replay-data` now polls the documented official CLOB book and
public trade prints for exactly the current quote-sheet markets on the
15-minute monitoring cadence, recording matched point-in-time windows in
`outputs/maker_carry/maker_replay_collection_windows.csv`. CLI
`maker-fill-replay` reports per-market coverage, last-in-queue confirmed-fill
ratios, 5/15/60-minute markout distributions, a seven-day/prior regime cut,
and a reporting-only tighten-only haircut in
`outputs/maker_carry/maker_fill_replay.json`. Nonzero simulated opportunities
with no 5-minute coverage emit `insufficient_coverage`; persistent blindness
is registered with WO-78. M-A/M-B/M-C, sizing, paper/live permissions, and all
order paths are unchanged.

The whole maker case rests on simulated net carry that the honesty_clause
itself calls an UPPER BOUND. WO-40 `maker_fill_replay` exists to test that
bound against reality — but on 2026-07-13 it reported `realism_ratio=0.0`,
`markout_per_fill=None` across 108 simulated fills. Root cause diagnosed:
the carrier market churns ~5x/day while the official book poller covers
only ~2 markets sampled with lag, so the replay almost never has book/print
data for the same market+window the study simulated. The validation layer
is blind, not passing. This WO restores its sight. NO capital; diagnostic
only; the M-gates and study are untouched (reporting-only, per WO-40).

1. COVERAGE: continuously snapshot the official book + capture trade prints
   for exactly the current quote-sheet/portfolio markets, on the sheet's
   own refresh cadence, so every simulated-fill window has real book/print
   data to replay against. Record per-market coverage (windows covered /
   windows simulated) in the replay output.
2. REPLAY OUTPUT: with data present, emit the two numbers the maker case
   actually needs — confirmed-fill ratio (last-in-queue) and realized
   markout distribution at 5/15/60m — plus a `simulation_to_reality_haircut`
   = realized adverse cost / simulated adverse charge.
3. TIGHTEN-ONLY WIRING (registration, not silent): the haircut is REPORTED
   next to the gate, never auto-applied. If it shows the study understates
   adverse selection, that is a candidate M-B tightening requiring a dated
   amendment — it may only make the gate harder, never easier.
4. REGIME CUT: report the replay separately for the last 7 days vs prior,
   so the post-World-Cup regime question (is carry event-beta or edge?)
   gets a read from history without waiting.
5. GUARD: `realism_ratio=0.0` with nonzero simulated fills AND zero coverage
   must render as `insufficient_coverage`, never as a silent zero — the
   degraded-state watchdog (WO-78) alarms on persistent insufficient
   coverage so this blindness can never recur unnoticed.
6. Tests: synthetic book/print fixtures where a known fraction of simulated
   fills are corroborated; coverage accounting; haircut computed correctly;
   insufficient-coverage sentinel trips.

Registered maker validation protocol (recorded in EXPERIMENT_REGISTRY H1):
Tier 0 = this replay (free, history). Tier 1 = reward-receipt test (rest
minimum size on a CALM wide-band rewarded market across one reward epoch,
compare paid reward to predicted share; low adverse-selection exposure) —
the refined Drill E. Tier 2 = real fill markout via the $100 human stage
(P2). Each tier gates the next; a Tier-0 result showing the fill model is
wildly optimistic can retire the maker lane with zero capital.

## WO-80 — Candidate staleness guard (defect, small)

Status: IMPLEMENTED by Codex on 2026-07-14 in PR #194. The rewarded
maker-carry universe now excludes otherwise eligible markets when the venue
close timestamp or latest explicit title date is past, or UMA resolution is
proposed/disputed. The study summary reports `excluded_stale`, reason counts,
and bounded examples. Registered maker gates, sizing, and all paper/live
controls are unchanged. Ruff, config validation, the WO-80 regression suite,
the full 1,144-test ARM64/Python 3.11 suite, and the WO-69 PR gate passed.

Observed 2026-07-13 18:05 candidates: "Iran military action against a
Gulf State on July 9?" — an event four days past — still ranked in the
top-40 rewarded candidates with a $100/day pot, band_eligible=true,
resolution_risk_class="other". A past-dated or resolution-pending
market must never be a quoting candidate: its "carry" is fictional and
quoting into UMA limbo is pure resolution risk.
1. Add an event-date/close-time sanity filter to the yield-first scan:
   candidates whose title date or venue close time is in the past, or
   whose resolution is proposed/disputed, are excluded and counted in
   an `excluded_stale` diagnostic.
2. Regression test seeds a past-dated rewarded market and asserts
   exclusion.
3. Reporting-only change to candidate SELECTION inputs; the M-A/B/C
   gate definitions and their history are untouched (exclusion filters
   are part of the registered universe definition: "existing
   resolution ... exclusions" per the registry's H1 universe).

## WO-81 — Reconcile specs and runbooks to custody Amendment A1 (single account)

Status: IMPLEMENTED by Codex on 2026-07-14 in PR #197. The single project
wallet is now authoritative across the custody drill, live-ops control plane,
operating state, maker artifacts, and daily Stage-1 page. The ops lane emits a
human-only excess-balance sweep advisory and the exit rail has a registered
withdrawal/cost-capture runbook. Signed P3/P5 evidence, P4 explanation,
governance-document mounts, paper-fill attribution, fresh authorisation, and
intentional-versus-overrun scheduler accounting are fail-closed and covered by
deployment acceptance tests. No order, signing, credential, funding movement,
gate, paper broker, or live-executor path changed. Ruff, config validation, 62
focused tests, and the full 1,157-test ARM64/Python 3.11 suite passed.

The signed A1 amendment (2026-07-13) superseded the sub-account
structure. Documents and specs that still assume it must be reconciled
— the drift philosophy applies to our own governance docs first.
1. `EXECUTOR_SUB_ACCOUNT_AND_CREDENTIAL_DRILL.md`: sub-account
   onboarding steps superseded (note, don't delete — history);
   fail-flat drill, rotation procedure, and credential-guard steps
   remain binding verbatim for the single project account.
2. WO-76 canary contract wording: "executor account only" becomes
   "single project account under A1 sequencing" — thresholds and rules
   otherwise UNTOUCHED (tighten-only applies).
3. WO-75 control-plane doc + operating-state executor rows: the
   executor wallet IS the operator wallet post-amendment; rows label
   executor-era activity by mode/time window per A1 control 1.
4. SWEEP ADVISORY (A1 control 2, reporting-only): when account balance
   exceeds the active stage cap by a registered threshold, the ops
   monitor emits "sweep $X to VALR" advice in the daily artifacts and
   notification path. The human executes withdrawals; nothing
   automated moves funds.
5. WITHDRAWAL RUNBOOK + cost capture: an operator runbook for the exit
   rail (Polymarket -> Solana -> VALR -> ZAR) whose first execution is
   the registered exit-rail test; door-to-door costs land in the WO-63
   cost ledger like the inbound leg did.
6. PRECONDITION EVIDENCE PLUMBING (2026-07-14 finding: the operating
   state reports P5 UNKNOWN although the custody amendment is signed):
   the generator's P3/P5 checks read their signed Status/amendment
   lines from the repo checkout (custody doc Status lines for P5;
   the AGENTS.md owner-amendments section for P3), with the exact
   grep patterns registered and tested against planted signed/unsigned
   fixtures. P4's row explains that its artifact appears only when
   `audit_github_merge_gate.py` runs, instead of bare UNKNOWN.
   UNKNOWN must keep meaning "cannot determine", never "done but
   unreadable". 2026-07-14 addendum from the live row "AGENTS.md
   and/or CLAUDE.md is unavailable": the container evidently lacks
   read access to the governance docs, so P3 can NEVER resolve in
   production — mount the needed docs read-only (or evaluate the doc
   checks host-side) as part of this item, with a deploy-acceptance
   check that the mounts exist.
7. DASHBOARD ROW CLARITY (2026-07-14 owner-requested critical review):
   (a) the capability row's raw evidence string
   "APPROVED_FOR_PAPER_TRADING" (quoted from
   paper_trade_readiness.json) reads as an authorisation on a
   dashboard — rename the surfaced label to mechanical_readiness=ready
   and keep authorisation exclusively on the authorisation row;
   (b) "Governed paper authorisation" evidence was last verified
   2026-07-02 — twelve days stale on the most sensitive row; the
   verification must re-run in the harvest cycle so the date is never
   older than one day;
   (c) "Paper activity RECORDED_FILLS=49" next to NOT_GRANTED needs a
   lane annotation (which registered experiment produced the fills)
   so the juxtaposition cannot read as unauthorised activity.
8. SLO SKIP TAXONOMY: the skipped-cycles SLO (target 0) breaches
   permanently on DESIGNED quota skips — alarm fatigue is the exact
   failure mode SLOs exist to prevent. Split the counter:
   `skipped_intentional` (quota/preflight declines, reported
   informationally) vs `skipped_overrun` (due jobs missed for
   capacity/timing, the SLO input, target 0). Tighten-only: the
   overrun target stays 0.

## WO-82 — Human Stage-1 operator runbook + generated daily stage log

Status: IMPLEMENTED by Codex on 2026-07-14 in PR #196. CLI `stage-day` renders
a dated operator page from WO-66 tickets, requote state, kill criteria, prior-day
operator reconciliation, WO-63 cost deltas, and A1 reminders. Human-reported
actions append under a runtime lock to the anchor-enrolled
`execution/stage_operator_log.csv`; no order, signing, credential, broker, or
funding path is invoked. The daily harvest scheduler refreshes the page without
adding another interval. The exact human kill procedure is registered in
`docs/HUMAN_STAGE1_OPERATOR_RUNBOOK.md`. Ruff, config validation, all six
WO-82 regression tests, and the full 1,150-test ARM64/Python 3.11 suite passed.

If the gates pass on 2026-07-17..20, the $100 human stage starts within
days. The operator needs one page per day, generated, not improvised:
1. `stage-day` CLI riding the existing cycles: renders TODAY's page —
   order tickets (from WO-66), current requote/alert state, kill
   scoreboard, yesterday's reconciliation result, cost ledger delta,
   and the A1 sequencing/balance-discipline reminders.
2. A daily operator log artifact (append-only, anchored): the operator
   records actions taken (quotes placed/pulled, sizes, times) against
   the generated page; this is the P2 evidence record and the baseline
   the canary will later be compared to.
3. Kill-criteria response procedure written as numbered steps (what to
   click, in what order, what to record) — a kill event at 02:00 must
   be executable half-asleep.
4. Tests: page renders complete from synthetic artifacts; missing
   inputs render UNKNOWN; log ledger is anchor-enrolled.
Build BEFORE 2026-07-17 so the stage starts on rails, not on chat.

# Batch 10 — filed 2026-07-13 (never again: silent degraded states)

Encoded lesson: two incidents in 24h shared one shape — a component sat
in a failed or fail-closed state (governance exit-124 overnight;
requote alerts stuck pull_quotes_now for hours) and no machine noticed;
humans found both by reading artifacts. Fail-closed is correct;
SILENT, PERSISTENT fail-closed is a defect. Secondary lesson for the
orchestrator's own audit checklist: unit-green components can still
break at their data contracts — WO-66 was audited "clean" without
asserting that every quote-sheet market has a live book source.
Both WOs are reporting/alerting only; nothing here gates or orders.

## WO-78 — Degraded-state watchdog (runtime detection)

Status: IMPLEMENTED by Codex on 2026-07-13. CLI
`degraded-state-watchdog` evaluates distinct producer observations against a
fixed, tighten-only semantic-health table. It opens incidents on the fourth
consecutive missing-input requote cycle, the first non-zero scheduler exit,
the third consecutive partial wallet harvest, or a previously known operating
row becoming `UNKNOWN`. Incidents byte-append to
`performance/degraded_state_incidents.csv`, are enrolled in the WO-61 prefix
anchor, surface in generated operating state and the dashboard, and emit the
existing state-deduplicated owner-notification artifact. Risk-only quote
states remain valid, all source states remain fail-closed, and no gate,
broker, sizing, credential, cancellation, paper, or live path changed.

The SLO framework tracks AGE; it must also track SEMANTIC health.
1. A registered degraded-state table (config, tighten-only): artifact,
   its healthy reachable states, and the max consecutive
   cycles/duration it may report a degraded state before that becomes
   an INCIDENT. Initial registrations:
   - requote_alerts.alert_state == pull_quotes_now/STOP with
     missing-input reasons (not risk reasons) for > 3 cycles;
   - any ops_scheduler job last_exit_code != 0 (immediately — this
     alone would have caught exit-124 at 02:10 instead of 07:3x);
   - wallet_reconciliation.status == partial for > 2 consecutive
     harvests;
   - operating_state rows UNKNOWN that were known in the previous run.
2. Incidents append to an anchored incident ledger (extend
   background_timeout_incidents.csv or sibling), surface in the
   operating state and dashboard, and fire the owner notification
   path.
3. Crucial distinction preserved: a degraded state for a LEGITIMATE
   reason (real missing market data, real risk signal) stays a valid
   state — the watchdog alarms on PERSISTENCE, never overrides the
   fail-closed behaviour itself.
4. Tests: synthetic stuck states trip at exactly the registered
   thresholds; legitimate transient degradation does not; incident
   rows are anchored.

## WO-79 — Deploy acceptance + cross-component contract tests

Status: IMPLEMENTED by Codex on 2026-07-13. The self-hosted ARM64 VPS deploy
now captures the pre-deploy generated operating state before quiescing
writers, preserves the prior revision as the rollback reference, and after
restart runs a bounded real-data cycle of maker quote generation, decision
policy, requote evaluation, three-way wallet reconciliation, and operating
state. Producer exits are recorded independently so stale artifacts cannot
mask a failed command. `deploy_acceptance.json` compares every quote-sheet
condition with an exact URL/token/tick/bid/ask ticket, permits only a healthy
requote state or a named registered risk blocker, requires all three
reconciliation legs, and rejects new UNKNOWN operating rows. FAIL emits the
owner-notification artifact, appears in the operating-state/dashboard, and
fails deployment after the cockpit is rerendered; #170's preserved prior SHA
remains the rollback path. A deliberately limited producer/consumer registry
declares fields, freshness, and coverage for quote sheet -> requote,
scheduler status -> alerting, and reconciliation legs -> NAV. The required PR
gate exercises these contracts on synthetic fixtures, including a quote-sheet
market absent from the websocket set. This is acceptance/reporting only and
contains no paper/live broker, signing, order, gate, model, or sizing path.

**2026-07-15 acceptance-freshness correction.** The real-data deploy cycle now
runs the read-only maker scoreboard before decision policy. Acceptance fails
unless the target generation reports fresh WO-88 raw/owner/maker-test fill
accounting, internally reconciles those counts, and appends a fresh attribution
ledger row; the served dashboard must expose the same fields. This closes the
pre-target scoreboard gap without authorising or placing an order.

1. Post-deploy acceptance pass: the deploy workflow's final step (VPS
   runner) waits for/triggers one cycle of the critical paths on REAL
   current data and asserts completeness — quote-sheet tickets carry
   URL/token/tick/bid/ask; the requote evaluator reaches a
   non-fail-closed state OR reports a specific legitimate blocker;
   reconciliation runs all three legs; operating state introduces no
   new UNKNOWNs vs pre-deploy. Writes
   `outputs/ops_scheduler/deploy_acceptance.json` (PASS/FAIL + diffs);
   FAIL alerts the owner and is shown in the operating state. Deploys
   remain reversible per #170's restore path.
2. Producer/consumer contract registry: artifacts that feed other
   components declare their contract (fields, freshness, coverage);
   a PR-gate test asserts every registered consumer's requirements are
   satisfiable by its producer's declared output ON SYNTHETIC FIXTURES
   — the WO-77 class (sheet markets absent from the socket set)
   becomes a test failure, not a production discovery.
3. Scope control: start with the three contracts that already bit us
   (quote sheet -> requote alerts; scheduler jobs -> status/alerting;
   reconciliation legs -> NAV). Do not boil the ocean; add contracts
   only when a WO touches the pair.

## Current queue for Codex (reconciled 2026-07-15)

Every WO below and every future WO must comply with
`docs/ENGINEERING_STANDARDS.md` (S1-S7), including the mandatory
`Day-after check:` line. Reviews verify compliance item by item.

**No numbered work order is currently buildable.** WO-89 through WO-92 are
implemented as of 2026-07-15. Do not infer another build from WO-92's
retrospective engineering-standard retrofit or from the 2026-07-19/20 final-read
schedule.
WO-85, WO-87, WO-86, and
WO-88 are implemented on 2026-07-15; WO-83 is implemented in PR #203 and
WO-84 is implemented in PR #205. Do not infer follow-on capital, gate, model,
or executor work from their diagnostics; the queue below remains binding.

- **Pending review, not build permission:** WO-33. WO-34/35 model wiring shares
  its leakage-review dependency and must stay inside H1-H3. H2/H3 still need
  dedicated post-registration OOS evaluators, but no numbered build order for
  those evaluators is filed here.
- **Blocked:** WO-48 (maker evidence gates); WO-67 (all P1-P5); WO-73 item 4 and
  WO-75 item 2 (part of the blocked executor authorization path).
- **Deferred:** WO-70 until post-proof; WO-72 until the human ladder produces
  the registered drill inputs.
- **Registration only:** WO-76; no code build is attached to filing it.
- **Decision, not a WO:** WP13 venue expansion requires an explicit owner go.

Do not infer a new build from the superseded priority notes below or from an
existing diagnostic module. A newly filed WO or an explicit prerequisite
transition must change this queue first.

## Superseded priority note (2026-07-10, batch 3)

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
