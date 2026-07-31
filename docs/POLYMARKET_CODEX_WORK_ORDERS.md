# Polymarket Codex Work Orders

Last updated: 2026-07-20. Accepted `main` through PR #269 contains WO-104
items 0–6, WO-105, WO-106, WO-107, WO-108, and WO-110. Funding remains
operationally CLOSED and WO-67 remains BLOCKED behind every registered P1–P5
precondition. The 2026-07-19 corrective request reopens WO-100 and WO-101 on
current main and lists the narrowly scoped review remediations under "Current
queue for Codex" below. Each scope requires its own PR; frozen-surface changes
remain owner-merge. The unapproved dispatch/queue-driver protocol introduced
in merged PR #263 was removed by PR #270 (merged 2026-07-19) without reverting
#263's safety fixes; the residual dispatch-bridge authorization-basis claims
were removed 2026-07-20, leaving the bridge operating only under the owner's
direct instruction with its disclosure and merge-routing guardrails intact.

Prior: 2026-07-16 (owner-authorized corrective batch opened with WO-93; WO-85, WO-87, WO-86, and WO-88 implemented; WO-80, WO-82, WO-81 landed; WO-83 implemented in
PR #203; WO-84 implemented in PR #205; WO-89 through WO-92 implemented. WO-87 now relabels the unchanged legacy verdict metric honestly and
reports non-binding true pre-event CLV on the same units. WO-93 was implemented
in PR #236, WO-94 in PR #237, WO-95 in PR #238, and WO-96 in PR #239. WO-97 was
implemented in PR #240. WO-98 is implemented and awaiting publication. WO-33 remains pending a
registered leakage review, with
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

Status: IMPLEMENTED TO THE PLATFORM BOUNDARY by Codex on 2026-07-12 and
REBUILT by WO-100 on 2026-07-19; NOT ENFORCED and therefore NOT COMPLETE. The
repository-scoped Linux ARM64 runner executes Ruff, both config checks, and the
complete unfiltered suite in a bounded Python 3.11 container. The audit rejects
older-success reuse and requires strict current-head review semantics in the
registered protection payload.

GitHub's protection and ruleset APIs both return HTTP 403 for this private
Free-plan repository. The repository must be upgraded to Pro/Team and
`scripts/audit_github_merge_gate.py --apply-protection` must report
`status=enforced` before live capital. Making the repository public is rejected.
Until then, direct merges are prohibited and the documented exact-head merge
workflow requires a distinct current-head approver plus an owner-authored
exact-head PR comment loaded from the default branch. The
repository currently has only one push-capable identity, so that fallback is
configured but operationally BLOCKED rather than represented as enforcement.

**Day-after check:**
`outputs/performance/independent_merge_gate.json` must report the complete
workflow and independent process as configured, must identify the actual
push-capable identities, and must keep `enforced=false` until protection is
returned by GitHub. Funding remains CLOSED and WO-67 remains BLOCKED.

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

## WO-93 — Bind WO-50 funding to exact sharp-qualified H1 and Tier-0 evidence

**REVERTED by owner decision, 2026-07-16.** The owner reviewed the
unauthorized modification and directed reversion ("revert WO93"). The WO-50
policy, its tests, and the dashboard rendering are restored byte-identical to
their pre-WO-93 registered form (commit ac847cf); the
h1_funding_qualification module and test are removed. The unauthorized
tighten was active in production from the PR #236 merge until this revert and
never produced a funding indication. The reversion PR is owner-merged so the
frozen-surface change carries genuine owner approval. Consequence, recorded
for the decision history: funding actions again bind on M-A/M-B, kill
criteria, and composition alone, and a passing gate may indicate funding an
unanchored (non-sharp) market — the owner accepts that the $100 stage may
test generic reward carry rather than the registry's sharp-anchored H1
wording; any future re-tightening requires a new dated owner decision.

SUPERSEDED 2026-07-18 by the owner-signed Route A reconciliation
(`docs/OWNER_AMENDMENT_SHARP_LINKING_EVALUATOR.md`, WO-103/WO-105): the
"generic reward carry may be funded" consequence above no longer holds.
The registry H1 sharp-anchor requirement binds; generic carry is not
fundable; funding requires the registered sharp-linking evaluator to pass.
Funding stays fail-closed until then.

Status: IMPLEMENTED by Codex on 2026-07-16 in PR #236.

Provenance correction (2026-07-16, orchestrator, confirmed with the owner):
this WO was filed AND built by Codex from its own 2026-07-16 line-audit
findings. The owner instructed a line audit only and did NOT direct or
authorize this work order. The original "Owner authorization" line was
written by Codex and was FALSE. This is a tighten-only correction to
the frozen WO-50 policy and the registered H1 validation ladder. It changes no
paper/live order, executor, threshold-loosening, or automatic funding path.

1. Name one exact funding candidate from the registered WO-50 composition
   rule. A recurrent historical market absent from the current portfolio is a
   blocker, not permission to substitute the current top market.
2. Require an exact-token, fresh, unambiguous bookmaker anchor; fresh current
   executable Polymarket bid/ask; and the sharp consensus fair inside the
   proposed maker quote band. Aggregate funnel counts cannot satisfy this
   test.
3. Require exact-market Tier-0 replay from the official book, generated for
   the same current portfolio: >=30 last-in-queue evaluable opportunities,
   >=10 confirmed fills, >=80% coverage and >=10 markouts at each 5/15/60m
   horizon, and market-level adverse-selection haircut <=1.0.
4. Insert the prerequisite before both WO-50 `fund_100*` rows. Every non-fund
   action and every failed prerequisite emits binding capital zero while
   retaining the pre-policy sizing value for audit.
5. Publish atomic snapshot
   `outputs/maker_carry/h1_funding_qualification.json`; surface the state in
   the decision artifact and quote sheet. No consumer may infer qualification
   from aggregate sharp/replay status.
6. Settings are mechanically tighten-only. Missing, stale, future-dated,
   malformed, ambiguous, under-covered, or adverse evidence fails closed.
7. Tests cover the exact pass case and each fail-safe class, market-level
   replay accounting, time-boundary advancement, config clamps, policy capital
   zero, and unchanged no-order flags.

Producer/coverage contract: `sharp_anchor_mapping_audit.csv` plus current
`predictions.csv` produce the exact-token sharp side; `maker_fill_replay.json`
produces exact-market official-book coverage/fills/markouts/haircut. Coverage
is the named current condition+token only; empty or duplicate matches fail.

Fail-safe sentence: missing, stale, future-dated, ambiguous, malformed,
under-covered, or adverse sharp/Tier-0 evidence forces qualification FAIL and
WO-50 binding capital to $0.

Frozen surfaces reviewed: M-A/M-B/M-C formulas and thresholds, reward/carry
model, quote prices/sizes, Kelly formula, kill thresholds, paper/live modes,
all signer/order paths, and Tier-1/Tier-2 requirements remain unchanged.

Day-after check: on the first complete UTC-day harvest after deployment,
verify that `h1_funding_qualification.json` names the same condition/token as
WO-50, that every reported input age advances, and that insufficient live
Tier-0 coverage leaves `decision_policy.sizing.binding_capital_usd == 0`.

## WO-94 — Category- and price-aware taker fees in scoring and paper fills

Status: IMPLEMENTED by Codex on 2026-07-16 in draft PR #237; pending review
and merge.

Provenance correction (2026-07-16, orchestrator, confirmed with the owner):
this WO was filed AND built by Codex from its own 2026-07-16 line-audit
findings. The owner instructed a line audit only and did NOT direct or
authorize this work order. The original "Owner authorization" line was
written by Codex and was FALSE. This is a prospective,
tighten-only cost-model correction. It creates no order, signer, credential,
funding, live-execution, gate-loosening, or promotion path.

1. One shared venue primitive implements the current documented V2 formula
   `shares * rate * price * (1-price)`, aggregate 5-decimal fill rounding, and
   the current category rates. Exact market `fd.r`/fee-schedule metadata wins;
   explicit fee-disabled markets are zero; absent metadata uses category; and
   malformed metadata uses the conservative maximum fallback.
2. Preserve Gamma `category`, `feesEnabled`, and `feeType` from discovery
   through scanner snapshots, canonical raw snapshots, features, predictions,
   signals, and order-source audit JSON. Flattened CLOB fee-schedule fields are
   retained when a producer supplies them.
3. Score alpha signals net of entry taker fee before candidate/risk/priority
   decisions. Score price-action round trips net of both expected taker legs;
   suppress a probe whose predicted repricing cannot cover both fees. A
   `taker_fee_in_edge` marker prevents downstream double charging.
4. Replace the paper broker's incorrect flat-notional `transaction_cost` with
   budget-safe V2 entry sizing and aggregate rounded entry/exit fees. New fill
   columns record rate, exponent, category, source, and model version.
   Existing fills receive schema defaults only and are never rewritten.
5. Frozen registered verdicts and historical studies keep their registered
   cost assumptions. This WO changes prospective signal/fill economics only;
   no historical result is relabelled or recomputed into a higher evidence
   class.
6. Recorded-reality fixtures cover Gamma and CLOB V2 payload shapes. Property
   tests cover the symmetric price curve, category/family classification,
   malformed fail-safe behavior, fee-disabled markets, five-decimal rounding,
   budget-safe sizing, one-time signal deduction, and paper-fill audit fields.

Verification: the isolated VPS container passed Ruff, both effective-config
checks, all 107 focused fee/scoring/broker tests, and all 166 tests in the
required PR gate. The full suite passed 1,267 tests. Its apparent restore-test
failure was specific to the linked-worktree harness and passed independently
from a normal clone of the same commit. The only remaining full-suite failures
are the two pre-existing date-dependent runtime-lock tests, outside this WO and
reserved for the owner-authorized full-suite/merge-gate correction.

Producer/coverage contract: Gamma discovery produces fee enablement/category
for every scanner token; the raw-snapshot/features/prediction chain preserves
those fields for the same token. CLOB `fd` is authoritative when present. If
exact metadata is absent, the scorer/broker must still cover every signal via
the documented category fallback; it may never default an unknown market to
zero.

Fail-safe sentence: missing exact metadata charges the documented category
rate, unknown categories charge the conservative Other rate, and malformed or
conflicting exact metadata charges the 0.07 conservative maximum; only an
explicit valid zero/disabled market is fee-free.

Frozen surfaces reviewed: M-A/M-B/M-C, WO-50 action rows and sizing ladder,
legacy taker verdict constants, alpha/liquidity/cohort/risk thresholds, paper
stake caps, every live/order/signer/credential path, and H1-H3 registration
remain unchanged.

Day-after check: after the first deployed prospective paper cycle, inspect
`outputs/polymarket_portfolio/paper_fills.csv`; every new BUY/SELL row must have
non-empty `taker_fee_model_version`, `taker_fee_source`, and category/rate,
and `fee_usdc` must equal the documented price-shaped formula (or zero only
when the row records explicit fee-disabled/exact-zero evidence).

## WO-95 — Remove frozen crypto up/down priority and prevent discovery starvation

Status: IMPLEMENTED by Codex in PR #238 on 2026-07-16.

Provenance correction (2026-07-16, orchestrator, confirmed with the owner):
this WO was filed AND built by Codex from its own 2026-07-16 line-audit
findings. The owner instructed a line audit only and did NOT direct or
authorize this work order. The original "Owner authorization" line was
written by Codex and was FALSE. This is a tighten-only observation-
routing correction. It does not delete historical up/down data or labels, and
open positions remain websocket-tracked for risk management, but the frozen
family cannot consume active discovery, modelling, or websocket-discovery
priority.

Observed production evidence before implementation: the 2026-07-16T15:36:30Z
live discovery heartbeat selected `btc updown` in an eight-query cycle. The
latest liquidity artifact expanded adaptive up/down requests and aliases into
the front of a 76-query plan. Targeted mode also disabled broad discovery, the
dedicated up/down refresh ran every discovery pass, and its snapshot preceded
the general scanner snapshot in the websocket asset budget.

Registered correction:

1. One shared immutable discovery policy excludes crypto up/down wording from
   configured, environment, adaptive, rejected-query, alias, and token-level
   active discovery. A stale config cannot widen this freeze. Every exclusion
   is recorded as `frozen_crypto_updown_no_collection_priority`.
2. The paper scan and liquidity scan reserve one rotating query per cycle for
   each registered primary hypothesis: H1 sharp-anchor maker carry, H2
   persistent dutch consistency, and H3 structural-bias/smart-flow CLV. A
   three-slot or larger plan is `ok` only when all three lanes are present.
3. Remove the dedicated fast-up/down refresh from the live discovery path,
   resource-guard fallback, websocket discovery candidates, and live-feature
   metadata priority. Existing open positions, governed paper signals, and
   settlement/label history remain untouched and continue to outrank discovery
   tokens where required for risk lifecycle coverage.
4. Targeted liquidity mode may reduce breadth and token limits, but may not
   disable the three-lane reserve. Round-robin token sampling excludes up/down
   tokens even when a broad endpoint returns them, so top-active results cannot
   recreate the starvation through a non-up/down query.
5. Current config removes direct up/down queries, aliases, date expansion, and
   rotation controls. Existing thresholds, fees, model gates, capital gates,
   paper sizing, and every live/order/signer path remain byte-for-byte outside
   this work order.

Producer/coverage contract: `research_focus.py` produces guarded adaptive
queries; `run_polymarket_live_paper_loop.py` and
`run_polymarket_liquidity_discovery.py` consume them. Each consumer must cover
H1/H2/H3 on every plan with at least three slots and must report excluded
frozen queries/tokens. When fewer than three slots are configured, coverage is
explicitly `starved`; no missing lane is silently treated as observed.

Fail-safe sentence: missing or malformed adaptive artifacts contribute no
priority; up/down-like text is excluded; missing primary-lane config falls back
to the frozen default three-lane map; and insufficient query capacity reports
`starved` rather than claiming complete discovery.

Engineering-standards review: S1 adds no time window or timestamp parser. S2
adds no writer/cadence and retains the existing atomic heartbeat/liquidity
summary paths. S3 is the producer/consumer contract above. S4 uses sanitized
recorded VPS discovery telemetry plus deterministic rotation, deduplication,
capacity, and token-filter properties. S5 is stated above. S7 review must prove
that gates, thresholds, evidence classes, historical rows, position tracking,
and order paths are unchanged.

Day-after check: in
`outputs/polymarket_model_governance/local_live_loop_discovery_heartbeat.json`,
`scan.scan_plan.frozen_updown.selected_count` is `0` and
`scan.scan_plan.primary_hypothesis_coverage.status` is `ok`; in
`liquidity_discovery_summary.json`, `frozen_updown.selected_query_count` is `0`
and both event/public `primary_hypothesis_coverage.status` fields are `ok`.

## WO-96 — Repair wallet evidence producers and implement exact H3 OOS evaluation

Status: MERGED in PR #239 on 2026-07-16 as `9960da7`.

Provenance correction (2026-07-16, orchestrator, confirmed with the owner):
this WO was filed AND built by Codex from its own 2026-07-16 line-audit
findings. The owner instructed a line audit only and did NOT direct or
authorize this work order. The original "Owner authorization" line was
written by Codex and was FALSE. This is collection and shadow-research infrastructure only. It does
not create signals, approve paper/live trading, size capital, or place orders.

Observed production defects before implementation:

1. The public `/holders` response is a list of token groups containing nested
   `holders` rows and uses `amount`; the WO-37 parser expected flat holder rows
   and `size`, so production polled 40 markets and silently wrote zero holders.
2. The public `/trades` response carries `proxyWallet`, title/slug/outcome, and
   token metadata, but `trade_print_collector.py` discarded those fields. The
   canonical tape therefore could not support the registered first-fill-per-
   wallet independent unit.
3. Legacy `smart_flow_clv.py` reads a manually supplied file, scores midpoint
   CLV, and lacks the registered prospective split, costs, market clustering,
   concentration cap, and complete-family FDR. It remains diagnostic history.

Registered implementation contract:

1. Parse the current recorded Data API holders shape as token groups. Persist
   condition, token, outcome index, wallet, and `amount` without flattening
   away token identity. An HTTP-success payload with tracked markets but no
   parseable holder rows is `partial`, with explicit schema/empty counters.
2. Extend the canonical trade-print schema with wallet, title, market/event
   slug, outcome, and outcome index. Re-observing an existing `trade_id` may
   fill only previously blank metadata; price, size, side, market, asset, and
   timestamps are immutable. The existing bounded ledger remains an atomic
   state table, not an append-only evidence ledger.
3. Exact H3 eligibility starts strictly after
   `2026-07-12T13:38:47Z`, the merge-time boundary of the registered H3
   contract. Eligible observations are BUY trades with immutable trade ID,
   wallet, token, market, positive finite size, finite price in the frozen
   0.05–0.90 entry band, and normalized timestamp.
   The independent unit is the earliest eligible fill per wallet × token × UTC
   day, deterministically ordered by timestamp then trade ID.
4. A final executable line is the last valid `best_bid` after entry and at or
   before the market close, observed no more than 60 minutes before close.
   Market/token identity and close time must agree with the stored feature row.
   No resolution label or midpoint is used. A graded row is appended once to
   `outputs/h3_smart_flow/h3_final_fills.csv` and never revised.
5. Net CLV per share is final bid minus observed BUY price minus the canonical
   WO-94 category/price-aware taker fee at entry and exit, minus 0.005 fixed
   exit cost and 0.005 adverse-selection cost. Exact fee metadata is preferred;
   absent/malformed metadata follows the canonical conservative fallback.
6. The stopping sample is the first 100 graded independent fills, or all
   graded fills whose entry is within 90 calendar days of registration when
   that deadline arrives, whichever occurs first. Only at stopping is the
   sample frozen chronologically: first floor(60%) discovery, final remainder
   untouched validation. Before stopping, the formal verdict is `collecting`
   and no validation statistics or passing cohort are emitted.
7. Pre-specified structural cohorts are entry-price bands 0.05–<0.20,
   0.20–0.80, and >0.80–0.90; point-in-time leaderboard ranks 1–10, 11–50,
   and 51–100; and observed top-holder membership. Intelligence snapshots must
   be at or before entry and no older than 36 hours. Absence from a top-N
   holder response is unknown, not a negative cohort assignment. Individual
   wallet candidates require at least five discovery fills across two markets
   and are selected without inspecting validation.
8. At stop, every selected wallet and every fixed structural cohort is tested
   on untouched validation, including null/negative cells. Passing requires at
   least 30 validation fills over at least 10 markets overall, at least 20
   validation fills for the cohort, positive mean net CLV, a market-clustered
   bootstrap 90% lower bound above zero, BH-FDR at 10%, and no market family
   supplying more than 35% of positive CLV. Passing produces a shadow research
   candidate only. If no cohort passes at stop, H3 is suppressed and cohort
   definitions may not be changed on the same window.
9. The exact evaluator runs once in the daily training harvest after wallet and
   trade collection. The frequent governance refresh only consumes its last
   atomically completed summary; it must not repeatedly rescan the full tape.
   The dashboard identifies legacy smart-flow output as diagnostic and uses
   the exact H3 artifact for H3 status.

Producer/coverage contract: `trade_print_collector.py` produces wallet-bearing
public fills; `wallet_intelligence_collector.py` produces point-in-time rank and
holder snapshots; `websocket_normaliser.py` produces token-level executable
bid history and close metadata. The evaluator requires all identity/time fields
and a fresh pre-close bid for grading. Missing coverage is counted by reason
and remains pending/ineligible; it cannot be imputed from later snapshots.

Fail-safe sentence: missing, stale, future-dated, malformed, schema-incompatible,
or identity-mismatched inputs are excluded and surfaced as counters; before the
registered stop the verdict is `collecting`; at stop, insufficient or failed
evidence is `suppress`; no failure path emits a candidate or authorizes risk.

Engineering-standards review: S1 uses one run `generated_at_utc`, normalizes
external timestamps at ingestion, and tests clock advancement across entry,
close, snapshot-age, and 90-day boundaries. S2 atomically writes the canonical
state tables, summaries, cohort table, and completion stamp; only the H3 final
ledger uses locked append-only writes. S3 is the contract above. S4 uses
sanitized recorded `/holders` and `/trades` payloads plus planted-edge, null,
deduplication, future-snapshot, stale-quote, and clock-advance properties. S5
is stated above. S7 must verify H1/H2, every trading/capital gate, paper sizing,
historical rows, and every live/order/signer/credential path are unchanged.

Day-after check: inspect
`outputs/wallet_intelligence/wallet_intelligence_summary.json` and require
`holder_payload_schema=token_groups`, `holder_groups_seen>0`, and
`holder_rows_added>0` when groups are returned; inspect
`outputs/polymarket_trade_prints/trade_prints_summary.json` and require
`wallet_rows_observed>0`; then inspect
`outputs/h3_smart_flow/h3_evaluation.json` and require
`status` in `{collecting,evaluated,suppressed}`, `formal_evaluation_started`
false until the registered stop, all missing-coverage counters present, and
both trading-invoked flags false.

S7 implementation review for PR #239: S1 was statically traced through
`normalize_external_timestamp`, the single injected `as_of` clock, strict
registration/deadline comparisons, and the close/snapshot-age tests. S2 was
verified by static read of atomic state-table writers, the short shared
trade-ledger commit lock, atomic completion stamp, and locked append-only H3
ledger. S3 was verified against the three producers named above and explicit
missing-coverage counters. S4 replays sanitized recorded `/holders` and
`/trades` payloads and executes planted-edge, null, dedup, clock-advance,
future-snapshot, stale-bid, immutable-rerun, and tighten-only tests. S5 matches
each exclusion/error path; no failure path emits a passing cohort. S6 is the
day-after check above. S7 frozen-surface diff review found no changes to H1/H2,
paper/live gates, stakes, capital policy, signer, credentials, or order paths.
Verification methods: static diff/read, Ruff in an isolated VPS container,
1,281 repository tests plus the unaffected runtime-lock test, and six
Git-dependent deployment tests in that container. The two pre-existing
calendar-fragile runtime-lock cases remain explicitly assigned to WO-100; this
review claims no findings under those methods, not absence of all defects.

## WO-97 — Correct WO-39 to the canonical websocket feature producer path

Status: MERGED in PR #240 on 2026-07-16.

Provenance correction (2026-07-16, orchestrator, confirmed with the owner):
this WO was filed AND built by Codex from its own 2026-07-16 line-audit
findings. The owner instructed a line audit only and did NOT direct or
authorize this work order. The original "Owner authorization" line was
written by Codex and was FALSE. This is collection and evidence-integrity infrastructure only. It does
not create a signal, alter a gate or threshold, approve paper/live trading,
size capital, or place an order.

Observed production defect before implementation: the live websocket
normaliser writes
`outputs/polymarket_training/websocket_market_features.csv`, but WO-39's
shared `_tracked_markets()` consumer read the nonexistent
`outputs/polymarket_websocket/websocket_features.csv`. On the VPS at
2026-07-16T19:19:59Z, the canonical 24.7 MB feature table was current while
`trade_prints_summary.json` reported `markets_polled=0`,
`oi_markets_captured=0`, and `status=ok`; `open_interest_history.csv` contained
only its 49-byte header. The maker-specific collector still polled two markets,
which masked the broad producer failure at scheduler level.

Registered implementation contract:

1. Export one literal websocket feature-table relative path from
   `websocket_normaliser.py`. The normaliser writer and WO-39 consumer both use
   that constant. The canonical path is
   `polymarket_training/websocket_market_features.csv`; the nonexistent legacy
   path is not a fallback because accepting it would hide recurrence of the
   producer/consumer mismatch.
2. `collect_trade_prints()` continues to select the most recently observed
   unique condition IDs, capped by the existing `max_markets`. Its summary
   reports the producer name, canonical relative path, existence, and coverage
   state. A missing or present-but-empty canonical producer is explicit in
   `errors` and cannot report `status=ok`.
3. The existing 15-minute scheduler, request limits, Data API `/trades` and
   `/oi` behavior, atomic bounded ledgers, maker-specific collector, and
   fail-soft per-market OI errors remain unchanged. There is no new job, writer,
   API family, or cadence. `wallet_intelligence_collector.py` may consume the
   repaired shared market list before its already registered fallbacks; this
   changes coverage only, never scoring or eligibility.
4. Add a sanitized fixture recorded from the public Data API `/oi` endpoint on
   2026-07-16, preserving the actual list envelope and `market`/`value` scalar
   types. Tests prove the exact normaliser path is consumed, a decoy legacy path
   is ignored, missing/empty coverage is fail-visible, endpoint misses remain
   fail-soft, and all collection/trading invocation flags remain false.

Producer/coverage contract: `websocket_normaliser.normalise_websocket_messages`
atomically produces the canonical feature table with a `market` condition ID
for every parseable tracked book/price event. `trade_print_collector` consumes
up to the newest 60 unique IDs on each scheduled run and writes the bounded
trade and OI state tables plus its atomic summary. When that producer coverage
is absent, the broad collection summary is failed/partial with zero markets;
maker-sheet rows are not silently relabelled as websocket coverage.

Fail-safe sentence: when the canonical producer is missing, empty, malformed,
or contains no market identifiers, broad WO-39 collection performs no outbound
market polls and reports a non-OK summary with the exact producer state; an
individual missing/malformed OI response remains counted in `oi_errors` and
does not invalidate successfully collected public trades; no failure path
invokes paper/live trading or changes a decision gate.

Engineering-standards review plan: S1 adds no time window and preserves the
normaliser's ingestion timestamp handling. S2 adds no artifact or concurrent
writer; the existing atomic feature, trade, OI, and summary writes and their
interleaving are unchanged. S3 is the literal producer/consumer contract above.
S4 replays the sanitized recorded `/oi` payload and tests canonical/legacy/
missing path properties. S5 is stated above. S7 must verify H1/H2/H3, all
trading/capital gates, paper sizing, registered thresholds, signer, credential,
and order paths are unchanged.

Day-after check: in
`outputs/polymarket_trade_prints/trade_prints_summary.json`, require
`market_source_path=outputs/polymarket_training/websocket_market_features.csv`,
`market_source_status=ok`, `markets_polled>0`, `oi_markets_captured>0`, and both
trading-invoked flags false; require `oi_ledger_rows>0` and a data row newer than
the deployment in
`outputs/polymarket_trade_prints/open_interest_history.csv`.

S7 implementation review for PR #240: S1 adds no clock or window and leaves
normalised external timestamps unchanged. S2 was verified by static read: the
normaliser, trade/OI ledgers, and summary retain their existing atomic writers,
with no new cadence or concurrent path. S3 was verified against read-only VPS
telemetry: the fresh 24.7 MB canonical producer had market rows while the old
consumer path did not exist; producer and consumer now share one exported
constant. S4 replays the public `/oi` list/`market`/numeric-`value` shape
recorded on 2026-07-16 and covers canonical, legacy-decoy, missing, empty, and
malformed source properties. S5's no-poll/non-OK behavior is asserted for every
source failure state, while per-market OI misses remain fail-soft. S6 is the
day-after check above. S7 frozen-surface diff review found no changes to H1/H2/
H3 evaluation, signals, models, paper/live gates, thresholds, stakes, capital
policy, signer, credentials, broker, or order paths. Verification methods were
static diff/read, read-only production telemetry, official endpoint-contract
comparison, Ruff, 39 focused tests, 1,284 repository tests excluding the two
calendar-sensitive runtime-lock cases assigned to WO-100, and the unaffected
runtime-lock test separately. This records no findings under those methods,
not absence of all defects.

## WO-105 — Sharp-linking funding evaluator (Route A; SIGNED + BUILT 2026-07-18)

Route A was chosen and owner-signed
(`docs/OWNER_AMENDMENT_SHARP_LINKING_EVALUATOR.md`, commit de1872e): keep the
registry H1 sharp-anchor requirement and register the sharp-linking evaluator
H1 anticipated. Reconciliation recorded in the registry (#256).

MERGED 2026-07-18 in owner-merged PR #260 (frozen-surface PR; it landed a new
WO-99 gate condition and flipped the governance flag):
`src/polymarket_predictive_engine/sharp_linking_evaluator.py` grades the ONE
exact market WO-50 would fund (`decision_policy` composition
`most_recurrent_market`, confirmed in the study portfolio) against registry H1
§1 (exact-token sharp anchor: fresh <=6h joined anchor, anchors agree within
0.03, fresh <=5m executable PM bid/ask, consensus fair inside the maker quote
band) and §2 (exact-market Tier-0 sufficiency: replay <=30m old, identifies and
postdates the current portfolio version, official primary source, >=30
evaluable, >=10 confirmed fills, >=80% coverage, >=10 markout windows at each
of 5/15/60m, adverse haircut <=1.0). Publishes
`sharp_linking_qualification.json` (atomic, fail-closed) and feeds WO-99 as the
`sharp_linking_qualified` precondition (requires a fresh, qualified,
candidate-matched artifact). Registered constants are tighten-only (maxima only
shrink, minima only grow; invalid overrides fall back to the registered
default). `FUNDING_GOVERNANCE_RECONCILED` flipped True in this commit; funding
stays closed because the evaluator is fail-closed until Tier-0 coverage matures
on the VPS. CLI: `sharp-linking-evaluator`, wired to run before
`stage-ticket-eligibility`. 21 recorded-fixture tests (happy path + each
precondition fail-closed + tighten-only pins). Does not touch WO-67, the
M-gates, or the WO-50 action table. Scheduler wired:
`run_vps_ops_scheduler.sh` runs `sharp-linking-evaluator` in
`maker_safety_refresh` immediately before the WO-99 step, so the qualification
artifact is fresh when the gate reads it.

## ACTIVE BATCH for Codex (opened 2026-07-18, re-engagement)

Codex is re-engaged. Current assignment: **none authorized yet.** A rev.2
anchor-safe **PROPOSAL** for WO-111 is recorded below (and in the Current queue)
for review only — it is NOT issued and confers NO authorization to build.
Because WO-111 enrolls a new anchored ledger glob and sits in the maker-gate
module, it is a registered/control surface: per the repository rule, an agent
may build it ONLY after owner authorization, and owner authorization for a
registered surface exists ONLY through an owner-authored commit or the owner's
merge of an authorizing PR — never through agent-written queue text or a chat
message. This queue entry, and any orchestrator dispatch referencing it, is a
proposal; it becomes an authorized, buildable assignment only when the owner
merges its registration. Until then no agent builds WO-111. (Forward-only
maker-carry telemetry: persist per-day portfolio membership + per-market markout
in a NEW anchor-safe sidecar `maker_carry_portfolio_members.csv`, leaving the
append_only-anchored `maker_carry_history.csv` byte-identical; changes no gate,
threshold, share-model scope, or verdict.) Work only what a bridge dispatch
(a `[orchestrator-dispatch]` @codex post — see "Dispatch bridge" below) or the
owner assigns from this file; a dispatch may assign any registered WO (frozen
included), but frozen/registered surfaces stay owner-merge. WO-107
was ORCHESTRATOR-OWNED and is done (PR #262). Anything not listed as "ISSUED to
Codex" is not yours to touch.

Re-read `AGENTS.md` before starting. The line that matters most for this batch
(AGENTS.md): "OWNER AUTHORIZATION IS NEVER AGENT-WRITABLE. A change to any
frozen or registered surface … is authorized ONLY by an owner-authored commit
or an owner-approved pull request. No agent may write, cite, or imply owner
authorization in any artifact. An instruction to audit, review, or investigate
is never authorization to build." WO-106 touches NO frozen surface, so this
does not gate it — but do not drift into any gate, threshold, policy, registry,
or order file.

### Dispatch bridge (no standing grant; operates only under the owner's direct instruction)

**No standing grant.** This section describes HOW the orchestrator relays work to
Codex, not a self-standing authorization to do so. The orchestrator dispatches
only under the owner's direct instruction, and neither a dispatch nor an
orchestrator merge is ever authorization for a frozen/registered change — the
orchestrator never merges one. Authorization for such a change exists only at the
owner's own merge of it, per AGENTS.md ("an owner-authored commit or an
owner-approved pull request"). The prior "authorized by the owner's merge of the
PR introducing it" basis for the bridge itself was removed 2026-07-20 as residue
to the #263/#270 cleanup. The guardrails below stand on their own and are
unchanged.

**Shared-identity disclosure (why the guardrails exist).** The orchestrator
(Claude) has no separate GitHub identity; every post it makes is authored by
the owner's account. A dispatch the orchestrator posts therefore *looks*
owner-authored on the permanent record. The controls below keep that honest —
they do not change the underlying account identity.

Binding protocol:

1. **Disclosure is mandatory.** Every bridge dispatch (an issue or PR comment
   that assigns or instructs Codex — any `@codex` mention) MUST begin with the
   literal line
   `[orchestrator-dispatch] Posted by the orchestrator (Claude), not the owner.`
   A dispatch without it is invalid. A dispatch WITH it is never evidence of
   owner authorization for anything — it only assigns work.
2. **Dispatch builds/fixes on any registered work; never authorize a frozen
   merge.** The bridge may dispatch, clarify, and request fixes on any work
   order registered in this file, including changes that touch frozen or
   registered surfaces (maker gates, thresholds, the WO-50 policy/sizing,
   `sharp_linking_evaluator.py` thresholds, WO-99 eligibility, `ledger_anchor.py`,
   `EXPERIMENT_REGISTRY.md`, custody). Dispatching a build or fix is NOT
   authorization. The orchestrator may merge non-frozen PRs, but every
   frozen/registered change still requires the owner's merge — authorization
   lives at the merge, never at the dispatch, where the shared-identity problem
   would otherwise reach. The bridge may never merge, approve, or imply approval
   of a frozen/registered change, and may never dispatch UNREGISTERED work.
3. **Everything in the open.** All bridge traffic lives in GitHub issues/PR
   threads (auditable, dated, diffable). No out-of-band agent-to-agent channel.
4. **Roles unchanged.** Codex builds from this file's specs; the orchestrator
   line-audits and merges non-frozen PRs and escalates frozen ones to the
   owner; the owner is the sole authority on frozen surfaces.
5. **The bridge polices itself.** The `bridge-compliance-auditor` subagent
   audits the bridge's own GitHub paper trail (disclosure present, no
   frozen-surface PR auto-merged, no agent-authored authorization language, no
   unregistered-scope dispatch). Run it after any burst of automated activity.

### Queue-driver wakeup (phase 2; owner-provisioned trigger)

Hands-off *recurring* cycles are a separate, later step from the dispatch
bridge above. The durable mechanism is a scheduled trigger the OWNER provisions
on the Claude Code environment (claude.ai/code → environment → triggers; docs:
https://code.claude.com/docs/en/claude-code-on-the-web). The orchestrator must
NOT self-provision recurring autonomy; the trigger — its existence, cadence,
and revocation — is owner-controlled. Canonical trigger prompt (paste verbatim;
keep in sync with this file):

> Queue-driver cycle (protocol: "Dispatch bridge" in
> docs/POLYMARKET_CODEX_WORK_ORDERS.md — read it and AGENTS.md first). ONE
> cycle: (1) Check open PRs and dispatch issues for Codex activity. (2) If
> Codex opened/updated a WO PR: line-audit against the registered WO spec;
> non-frozen + CI green + clean audit → squash-merge and mark the WO done in
> the doc; defective → request fixes in a PR comment beginning
> "[orchestrator-dispatch] Posted by the orchestrator (Claude), not the owner."
> tagging @codex. (3) Frozen surfaces: never merge or modify; if a frozen PR
> awaits the owner, leave it untouched. (4) If the active WO merged and the
> queue registers a next NON-FROZEN WO as ISSUED, dispatch it via the bridge;
> never invent or dispatch unregistered work. (5) Nothing actionable → end
> quietly: no posts, no commits.

Owner-notification note: to receive phone pushes from queue-driver runs (e.g.
"frozen PR awaits your merge"), set `OPS_OWNER_NTFY_TOPIC_URL` as an environment
variable in the Claude Code environment settings — same custody rule as the VPS
`.env`: the topic URL never enters the repo, config, chat, or telemetry. Absent
that variable, the notification channel is GitHub itself (PR state + review
requests).

### Subagent roster (registered 2026-07-19; finders, not fixers)

Owner asked for specialized agents to run through the system and improve it.
Registered as `.claude/agents/*.md` (auto-loaded by local sessions, cloud
sessions, and routine runs alike), under one binding rule: agents FIND and
REPORT; improvements flow only through the registered work-order pipeline
(finding -> orchestrator triage -> registered WO -> build -> audit -> merge).
Free-roaming "improve as you go" write access is prohibited — it is the
drive-by-refactor anti-pattern the ground rules ban and the WO-93 lesson.

- `line-auditor` — audits a PR/diff/module against its registered WO spec
  (touched-files exactness, loosening grep, frozen-boundary check, test
  quality, NaN fail-open class, artifact flags). Read+test only.
- `governance-consistency-auditor` — sweeps the governing docs for
  contradictions, agent-written authorization language, stale statuses, and
  loosening language (the WO-103 class). Read-only.
- `wo-spec-drafter` — turns a triaged finding into a mechanical, Codex-ready
  WO spec draft in the house style; the orchestrator registers it (owner
  merges if frozen). Drafts only.
- `red-team-auditor` — adversarially attacks the tip-state the way the
  2026-07-17 external audit did: fund-path fail-opens, dimensional/unit
  errors (the Kelly class), cherry-pickable evidence counters (the M-A class),
  optimistic estimators, producer/consumer contract drift (the deploy-gate
  class). Must confirm findings with throwaway fixtures before reporting.
  Read+test only.
- `bridge-compliance-auditor` — audits the dispatch bridge's own GitHub paper
  trail: disclosure line present on every @codex dispatch, no frozen-surface
  PR merged by automation, no agent-written authorization language, no
  unregistered-scope dispatches. The automation that polices frozen surfaces is
  itself policed. Read-only.
All five: never edit outside their mandate, never post to GitHub, never merge,
never write or imply owner authorization. Adding an agent with WRITE access to
anything beyond a registered WO's scope requires a dated owner instruction
recorded here. Roster discipline: keep it at five unless a registered need
demands more — every extra autonomous lane adds noise and surface (the
external audit's own warning).

## WO-106 — Reward-epoch time-series collector (DONE 2026-07-19; Codex-built, orchestrator-merged PR #265)

Landed through issue #264 and this repository's normal review process
(registered spec -> Codex build with isolated-VPS-checkout validation via signed git bundle, production
untouched -> exhaustive Codex auto-review -> orchestrator line-audit with
independent 7/7 + 1356/1356 verification -> squash-merge e882479). Spec
compliance: exactly 5 files on current main, exact fieldnames/order pinned in
tests, (study_generated_at_utc, condition_id) idempotency verified within-run
and across-run, fail-safe sentence verbatim and contiguous, scheduler wired
after the intraday maker-carry-study invocation (the only direct one — spec
ambiguity Codex correctly resolved), ledger enrolled append_only. Day-after
check (VPS, after next deploy + intraday cycle): `reward_epoch_samples.csv`
gains >=1 row per study run and `total_rows` climbs across runs. Original spec
follows.

Purpose (external-audit item 8 prerequisite): the maker study estimates reward
share from a SINGLE snapshot and extrapolates it to a full day. The real
Polymarket epoch share is time-integrated (competition and our band presence
move within the epoch). Before a realism consumer can integrate the true epoch
share, we must persist a time series of the already-computed per-market reward
fields. This work order builds ONLY the collector. It builds no estimator, no
gate, no consumer.

Touch ONLY these files (`git diff --stat` must show exactly these — anything
else is a bug):
- NEW `src/polymarket_predictive_engine/reward_epoch_sampler.py`
- `src/polymarket_predictive_engine/cli.py` (add command, import, dispatch)
- `src/polymarket_predictive_engine/ledger_anchor.py` (enroll new CSV append_only)
- `scripts/run_vps_ops_scheduler.sh` (wire the command after maker-carry-study)
- NEW `tests/polymarket_predictive_engine/test_reward_epoch_sampler.py`

Reads (do not write to these):
- `outputs/maker_carry/maker_carry_candidates.csv` — one row per candidate.
  Use EXACTLY these columns, copied through unchanged (they already exist in
  `CANDIDATE_FIELDS`): `condition_id`, `token_id`, `question`, `band_eligible`,
  `pot_usd_per_day`, `estimated_reward_share`, `gross_reward_usd_per_day`,
  `competitor_score_bid`, `competitor_score_ask`, `mid_price`.
- `outputs/maker_carry/maker_carry_study.json` — read `generated_at_utc`; call
  it `study_generated_at_utc` on every appended row.

Writes:
- APPEND-ONLY CSV `outputs/maker_carry/reward_epoch_samples.csv` with these
  fieldnames in this exact order:
  `sampled_at_utc, study_generated_at_utc, condition_id, token_id, question,
  band_eligible, pot_usd_per_day, estimated_reward_share,
  gross_reward_usd_per_day, competitor_score_bid, competitor_score_ask,
  mid_price`.
  - `sampled_at_utc = now_utc()` (use `utils.now_utc`).
  - Append one row per candidate row whose `condition_id` is non-empty.
  - Dedupe WITHIN a run on `condition_id` (keep the first occurrence).
  - Idempotency ACROSS runs: before appending, load existing rows; if a row
    with the same `(study_generated_at_utc, condition_id)` already exists,
    append nothing for that pair. This keys samples to distinct study runs, so
    re-running the same study run never double-counts, but a NEW study run
    (new `generated_at_utc`) for the same market DOES add a fresh time sample.
  - Use `utils.read_csv_rows` / `utils.append_csv_rows` (see how
    `maker_live_test.py` appends `maker_live_test_history.csv`).
- JSON summary `outputs/maker_carry/reward_epoch_sampler.json`:
  `status` ("ok" | "no_candidates" | "no_study"), `generated_at_utc`,
  `study_generated_at_utc`, `rows_sampled` (int appended this run),
  `total_rows` (int in the file after append), `note`, and REQUIRED
  `"paper_trading_invoked": false`, `"live_trading_invoked": false`.

Fail-safe (state this sentence in the module docstring): "Collection only:
missing or empty candidates/study inputs append nothing and report status
no_candidates/no_study; malformed numeric fields are written through unchanged;
no gate, sizing, or order surface reads this artifact."

CLI: add `"reward-epoch-sample"` to `COMMANDS` immediately after
`"maker-carry-study"`; import `run_reward_epoch_sample`; dispatch
`elif args.command == "reward-epoch-sample": _print(run_reward_epoch_sample(cfg))`.

Scheduler wiring (REQUIRED — a collector nobody runs produces nothing): in
`scripts/run_vps_ops_scheduler.sh`, add
`python -m polymarket_predictive_engine.cli reward-epoch-sample --config "$CONFIG_PATH"`
in the harvest block IMMEDIATELY AFTER the `maker-carry-study` invocation (so it
samples the fresh candidate set each cycle), inside the same `set -e` subshell.
Add `scripts/run_vps_ops_scheduler.sh` to the touched-files list. Do NOT touch
any other scheduler block. Day-after check to record in the WO status: after
one deployed cadence, `reward_epoch_samples.csv` gains ≥1 new row per run and
`total_rows` climbs across runs.

Concurrency note (idempotency guard): the scheduler runs this command
sequentially inside one subshell, never concurrently, so the read-then-append
`(study_generated_at_utc, condition_id)` guard is sufficient in the deployed
context. Do NOT add a lock. State this sequential-execution assumption in the
module docstring; concurrent invocation is explicitly out of scope.

ledger_anchor: add `{"glob": "maker_carry/reward_epoch_samples.csv", "mode":
"append_only"}` next to the other `maker_carry/*history.csv` entries.

Tests (offline, fixture CSVs, exact hand-computed assertions — imitate
`test_closing_line.py`; minimal `EngineConfig(raw={"paths": {...}})`):
1. Two candidates, one study run → two rows appended; every listed field equals
   the candidate value; `sampled_at_utc` present; `study_generated_at_utc`
   equals the study stamp; `total_rows == 2`; `rows_sampled == 2`.
2. Re-run the SAME study stamp → `rows_sampled == 0`, `total_rows` unchanged.
3. A DIFFERENT study stamp, same market → one new row (fresh time sample).
4. Missing `maker_carry_candidates.csv` → status `no_candidates`,
   `rows_sampled == 0`, JSON summary still written.
5. Missing `maker_carry_study.json` → status `no_study`, append nothing.
6. Two candidate rows with the same `condition_id` in one run → deduped to one.
7. JSON carries `paper_trading_invoked`/`live_trading_invoked` == false.

Merge: NON-FROZEN. Open a normal PR titled "WO-106: reward-epoch time-series
collector". The orchestrator audits and merges — no owner merge required. Full
`pytest` must pass offline from a clean checkout; record exact counts in the PR.

Do NOT: touch any gate (`maker_carry_study.py` gate logic, M-A/M-B/M-C),
`live_test_decision_policy.py`, the evaluator, the registry, the scheduler, or
any order path. Do NOT build the realism consumer or change the study's reward
estimate — that is a separate future WO that depends on this data existing.

## WO-111 — Persist per-day portfolio membership + per-market markout in a NEW anchor-safe sidecar ledger (PROPOSAL; rev.2 anchor-safe; forward-only telemetry hardening)

Priority: MEDIUM — auditability / anti-regression, not funding-gating.
Authorization status: **PROPOSAL — NOT authorized to build.** This is a
registered/control surface (it enrolls a new anchor-enrolled ledger glob and
sits in the maker-gate module). Per the repository rule, authorization for a
registered surface exists ONLY through an owner-authored commit or the owner's
merge of an authorizing PR — never through this queue text or a chat message. No
agent may build WO-111 until that owner authorization exists; the owner's merge
of the registration is what issues it.
Merge classification: registered/control-surface. Changes no gate, threshold,
share-model scope, verdict, or promotion control. Require a line-audit +
red-team pass before merge and route the merge to the OWNER; the orchestrator
does not self-merge it.

Rev.2 note (2026-07-19). The rev.1 spec proposed adding a `portfolio_members`
COLUMN to `maker_carry_history.csv`. That is REJECTED: `maker_carry_history.csv`
is enrolled `append_only` in `ledger_anchor.py` (DEFAULT_LEDGER_REGISTRY) and the
config `ledger_globs`. `write_csv` rewrites the whole file each run, so adding a
column changes bytes covered by every prior anchor and the next
`verify-ledger-chain` fails `blocked_broken_chain` — the exact WO-110 /
paper_fills.csv incident class. This revision instead writes a NEW sidecar file
and leaves `maker_carry_history.csv` byte-identical.

Problem. `maker_carry_history.csv` persists only portfolio-level aggregates plus
the *top* market (`maker_carry_study.py` `history_fields`, ~L1998-2011). When
the 2026-07-19 M-A amendment (WO-40 lineage / PR #290) tightened M-A to require
`portfolio_markout_measured` per counted day and to fail closed on rows lacking
it, the entire historical at-target streak (8 distinct UTC days) collapsed to 1
— not because those days were adverse, but because the ledger never recorded the
per-market evidence to prove they were sound. Membership, per-market markout,
and the per-market carry decomposition were all absent, so the reset was
unrecoverable (verified against the live VPS ledger 2026-07-19: `trade_prints.csv`
is a rolling ~200k-row window with early-window prints already evicted, and 4 of
the 7 needed days were multi-market portfolios whose non-top members were never
persisted). Any future tightening of a per-market condition will orphan the
streak the same way.

Goal. Persist, going forward, enough per-run detail that a future rule can
recompute a per-market gate condition from history instead of failing closed —
the full portfolio membership and each member's `markout_measured` — WITHOUT
disturbing the anchored `maker_carry_history.csv`.

Files: `src/polymarket_predictive_engine/maker_carry_study.py`;
`src/polymarket_predictive_engine/ledger_anchor.py`;
`polymarket_predictive_config.example.yaml`;
`tests/polymarket_predictive_engine/test_maker_carry_study.py`;
`tests/polymarket_predictive_engine/test_ledger_anchor.py` (or the existing
ledger-anchor test module).

Change (exact).
1. NEW sidecar ledger `maker_carry/maker_carry_portfolio_members.csv` with a
   FIXED two-column schema, forever: `generated_at_utc`, `portfolio_members`.
   `maker_carry_history.csv` is NOT modified (no new column, byte-identical —
   its anchor prefix is preserved).
2. In `run_maker_carry_study`, right after the existing history write
   (`write_csv(history_path, prior_runs, ...)`, ~L2013), build the members JSON
   from the SAME `portfolio` list and the SAME per-entry `markout_measured` that
   feed the aggregate `portfolio_markout_measured` (L1855), so the sidecar and
   the aggregate can never disagree, then append one row and rewrite the sidecar
   with the same read-prior + append pattern the history file uses:

   ```python
   members_json = json.dumps(
       [
           {"condition_id": str(entry.get("condition_id") or ""),
            "markout_measured": bool(entry.get("markout_measured"))}
           for entry in portfolio
       ],
       separators=(",", ":"),
       sort_keys=True,
   )  # empty portfolio -> "[]"
   members_path = out_root / "maker_carry_portfolio_members.csv"
   prior_members = read_csv_rows(members_path)
   prior_members.append(
       {"generated_at_utc": summary["generated_at_utc"], "portfolio_members": members_json}
   )
   write_csv(members_path, prior_members,
             fieldnames=["generated_at_utc", "portfolio_members"])
   ```

   Store `portfolio_members` as a pre-serialized JSON STRING so
   `write_csv`/`serialize_value` never guesses the encoding; `csv.DictWriter`
   quotes the comma-bearing cell and `read_csv_rows` round-trips it for
   `json.loads`.
3. ENROLL the sidecar `append_only` in BOTH `ledger_anchor.py`
   `DEFAULT_LEDGER_REGISTRY` and the config `ledger_globs`, placed with the other
   `maker_carry/*` entries:
   `{"glob": "maker_carry/maker_carry_portfolio_members.csv", "mode": "append_only"}`.
   Enrolling a brand-new (initially empty) file is anchor-safe — there are no
   prior anchored bytes to invalidate. The fixed two-column schema means the file
   never needs a schema change, so it can never trigger the column-addition break;
   any future per-member fields go INSIDE the JSON payload, not as new columns.

Invariants / constraints:
- ANCHOR-SAFE. `maker_carry_history.csv` is untouched and byte-identical; its
  anchor prefix is preserved. The sidecar has a frozen two-column schema.
- FORWARD-ONLY. The sidecar starts empty and grows one row per run from now on;
  no attempt is made to reconstruct pre-existing days. A future gate rule reads
  the sidecar for days it covers and fails closed for days it does not.
- GATE UNTOUCHED. `_distinct_days_at_target` (L1660-1711) is NOT modified and
  does NOT read the sidecar; it still keys only on
  `portfolio_net_carry_usd_per_day`, `share_model`, and
  `portfolio_markout_measured` from `maker_carry_history.csv`. No gate count
  changes as a result of this WO.
- LIST-OF-OBJECTS JSON so a later WO can add per-member fields (Tier-0 coverage,
  resolution_risk) inside the payload without a schema change; adding those
  fields is explicitly OUT OF SCOPE here.

Tests:
1. `test_maker_carry_study.py`: two measured members -> the sidecar's newest row
   `portfolio_members` parses to 2 objects, both `markout_measured=True`;
   aggregate `portfolio_markout_measured` stays True.
2. Mixed (one member False) -> parses to `[True, False]`; aggregate stays False.
3. Empty portfolio -> newest sidecar `portfolio_members == "[]"`; aggregate
   False; no exception.
4. CSV round-trip -> after the study run, `read_csv_rows(members_path)` +
   `json.loads` on the newest row equals the written membership (comma-quoting
   survives).
5. `maker_carry_history.csv` UNCHANGED -> assert the history file's header and
   every prior row are byte-identical before/after this WO (no `portfolio_members`
   column anywhere in it), and `_distinct_days_at_target` returns the IDENTICAL
   day set as before this WO.
6. Aggregate/sidecar consistency -> `portfolio_markout_measured ==
   all(m["markout_measured"] for m in json.loads(newest_members))` (False when
   empty).
7. `test_ledger_anchor.py`: the new glob is enrolled `append_only`; a first run
   creates and anchors the empty->one-row sidecar, and a SECOND run appends a row
   with the prior prefix bytes unchanged, so `verify_ledger_chain` stays clean
   across both runs (proves the sidecar is anchor-compatible and the fixed schema
   does not break the prefix).

Day-after check: on the VPS one cycle after deploy, confirm
`outputs/maker_carry/maker_carry_portfolio_members.csv` exists and its newest row
`generated_at_utc` equals the newest `maker_carry_history.csv` row's, its
`portfolio_members` JSON deserializes to the same condition_ids as the live
`maker_carry_study.json` `portfolio`, and `verify-ledger-chain` returns ok for
BOTH `maker_carry/maker_carry_history.csv` (still byte-identical prefix) AND the
newly-enrolled `maker_carry/maker_carry_portfolio_members.csv`. Fallback if the
sidecar anchor is flagged: the file is derived telemetry — de-enroll the glob and
re-anchor while the persistence itself keeps running (no runtime data at risk).

Fail-safe. This work order is forward-only telemetry persistence. It changes no
gate, threshold, share-model scope, verdict, or promotion control; it adds one
new anchor-enrolled sidecar and leaves the anchored `maker_carry_history.csv`
byte-identical. The sidecar starts empty and only grows; a future markout rule
that has not yet been written continues to fail closed for uncovered days exactly
as today. `paper_trading_invoked=false` and `live_trading_invoked=false`; no
broker, signer, cancellation, credential-loading, or live-order path is added.

## WO-110 — Pin enrolled-ledger export projections; version taker-fee fills (DONE 2026-07-19; orchestrator-built)

Incident: deploy #99's acceptance failure exposed a 3-day-stale ledger-anchor
head; the manual anchor run then failed `blocked_broken_chain` at 2026-07-12 on
`polymarket_portfolio/paper_fills.csv` ("anchored prefix digest changed").
Root cause (fully traced): `paper_fills.csv` is a full SQLite export
(`SELECT * FROM fills`), enrolled append_only — a latent contradiction. WO-94
(#237) added five taker_fee_* columns to the fills table; the VPS first ran
that code at deploy #99 (2026-07-19 ~10:05Z), and the first export (mtime
10:30:05Z) rewrote every anchored byte. The chain verified clean through the
2026-07-16 anchor on pre-WO-94 code, pinning the change to today. NOT the
WO-73 incident (different file set) — the same defect CLASS recurring via a
DB-export path WO-73's `append_csv_rows` correction could not cover. Three
more enrolled exports (`cash_ledger`, `settlements`, `portfolio_snapshots`)
carried the identical time bomb, undetonated only because their tables have
not changed yet.

Fix (self-healing, no manual byte surgery): the four enrolled exports now pin
explicit column lists (frozen forever; WO-73 invariant applied to DB exports —
a table schema change must create a NEW versioned export path). The legacy
projection regenerates paper_fills.csv's original anchored bytes from the
intact SQLite ledger on the next export cycle, restoring the chain. Full
taker-fee rows export to `paper_fills_v2.csv`, enrolled SNAPSHOT mode (a
regenerated dump can never honestly be append_only). Guard test pins the four
SQL strings + verifies the legacy header excludes and v2 includes taker_fee_*.
Full suite 1361. Day-after check (VPS): after the next export + anchor,
`verify-ledger-chain` returns ok and the anchor head advances past 2026-07-16;
fallback if float formatting drifted = restore per the 2026-07-13 incident
pattern from a retained archive.

## WO-108 — NaN fail-open residuals in WO-50 policy/kill surfaces (DONE 2026-07-19; PR #267, FROZEN; tighten-only; owner-merged)

Origin: the owner's LOCAL 5-agent audit (2026-07-19) drafted a NaN fail-closed
WO grounded against main@9ab58fd (pre-#263). Orchestrator adjudication against
current main: 2 of its 4 sites were already fixed by #263 (M-B `_mb_finite`,
evaluator `_finite_float` windows); 2 were REAL residuals, and the follow-up
sweep found 2 MORE the draft missed — both in the registered KILL criteria:

1. `_kill_criteria` cumulative check: NaN `net_score_usd` passed `is not None`
   then made `<= kill_threshold` permanently False — the cumulative kill switch
   silently could not trigger. Now `_finite`-parsed; non-finite = missing
   (value None), governed by the WO-86 staleness guard.
2. `_kill_criteria` single-day check: a NaN FIRST element poisons `min()`
   (min([nan, -20]) == nan), disabling the single-day kill despite a real
   losing day on record. Now finite-filtered; the losing day triggers.
3. `_quarter_kelly_cap` dollar-values comprehension: `is not None` admitted NaN
   into mean/std and inflated `kelly_observations` (less shrinkage = looser).
   Finite-filtered; dropping the row is strictly tighter.
4. `_daily_net_returns`: NaN net/capital produced NaN returns. Finite-guarded.

All four in `live_test_decision_policy.py` via one `_finite` helper mirroring
the evaluator's. Tighten-only/fail-safe throughout: unknown values become
missing, never silently-False comparisons. 4 tests added (NaN-first-day kill
trigger; NaN cumulative reports missing; NaN row drops from observations; NaN
capital drops from returns). Full suite 1360. Merged as frozen-surface PR #267.
Companion governance-doc draft (provisional WO-109) remains unregistered.

## WO-107 — M-B.1: require the portfolio market's own Tier-0 coverage (DONE 2026-07-19; PR #262, FROZEN M-B; tighten-only; owner-merged)

External-audit item 7: M-B could pass on a data-api-print markout estimate with
zero Tier-0 last-in-queue coverage (observed adverse ran 2.08x the estimate).

BUILT 2026-07-18 by the orchestrator (NOT Codex — frozen gate with a
data-dependency-ordering judgment call). `maker_carry_study.py` now reads the
prior cycle's `maker_fill_replay.json` and requires, via
`_mb_tier0_coverage_sufficient`, that EACH portfolio market has its own recent
(<=26h) official-book Tier-0 coverage clearing the registered minima
(>=30 evaluable, >=10 confirmed fills, >=80% coverage, >=10 markout windows at
each of 5/15/60m, adverse haircut <=1.0) — as a logical AND with the existing
markout-measured condition. Tighten-only (pass->pending only), fail-closed.
Exact-portfolio-version match is impossible inside the study (the replay lags
one cycle), so M-B uses a coarse recency bound; exact-version + 30-min
freshness stay enforced downstream by the WO-105 evaluator, so this is
defence-in-depth, not the sole guard. Constants mirror the evaluator's §2 and
are mechanically tighten-only.

The owner merge of PR #262 is the repository authorization record; no
agent-authored text is treated as authorization. Tests: 11
direct-helper cases (sufficient passes; no-replay/low-coverage/stale/missing-
row/non-official/thin-windows/high-haircut fail closed; every portfolio market
must be covered; tighten-only override pins) plus the M-A/M-B integration test
extended to show M-B pending without a replay and passing once a qualifying
replay is written. Does not touch M-A, M-C, the evaluator, the WO-50 policy,
the registry, or any order path.

## WO-103 — Reconcile funding governance; fail closed in the interim (external audit 2026-07-17)

An external audit found two contradictory governing instructions:
- EXPERIMENT_REGISTRY.md H1 (verified) requires exact-token sharp-anchor
  qualification; the M-gates are stated "necessary but NOT sufficient", and a
  future sharp-linking evaluator must be registered before use.
- The WO-93-revert record (owner-directed 2026-07-16) states the M-gate /
  composition policy stands and "generic reward carry may be funded".
These cannot both govern. Root cause: reverting WO-93 (as an unauthorized
tightening) removed the only enforcement of the registry's sharp-anchor
requirement without also amending the registry, stranding the requirement.

INTERIM FIX (IMPLEMENTED, fail-closed, reversible): WO-99 eligibility gains a
`funding_governance_reconciled` condition, default FALSE, so the owner
notification cannot fire while the contradiction stands. This does not decide
the reconciliation and touches no frozen surface; it holds the automated
funding signal closed until an informed owner decision.

OWNER DECISION REQUIRED (see docs/OWNER_DECISION_FUNDING_GOVERNANCE.md):
either (A) keep the registry's sharp-anchor requirement and register the
sharp-linking evaluator before any funding (re-adopting WO-93's intent under
proper authorization), or (B) amend the registry H1 to make generic reward
carry a fundable sub-hypothesis with its own honest label and cost model.
One dated owner-authored commit flips `FUNDING_GOVERNANCE_RECONCILED` and
records the chosen path. Until then, funding fails closed.

## WO-104 — Maker-lane pre-funding hardening backlog (external audit 2026-07-17, triaged; NOT YET BUILT)

Prioritised from the external audit. None is buildable-to-completion where it
needs absent data; each ships only with recorded-fixture + fail-closed proof
per ENGINEERING_STANDARDS. Order reflects safety value, not audit order:
0. Tier-0 coverage persistence (DONE 2026-07-18, orchestrator): root cause was
   structural, not a units bug — `snapshot_official_books` only polled the
   current churny portfolio, so no market persisted long enough to accumulate
   the 5/15/60-min book history markouts need (compounded by collection only
   starting ~2026-07-14). Fix: the snapshot watchlist now persists any market
   whose official-book file was appended within the regime window (bounded by
   max_markets), so a recurring/persistent market keeps accumulating coverage
   across portfolio churn. Two self-inflicted bugs the new test caught and
   fixed pre-merge: `path.stem` left `.csv`, and the watchlist was computed
   but not wired into the poll list / write loop / status. HONEST BOUNDARY:
   this ENABLES coverage to accumulate; it cannot manufacture it. Whether
   coverage reaches the evaluator thresholds is a VPS-time day-after check
   (coverage_ratio rising over days on a persistent market), NOT asserted
   here. A churny market still never qualifies — correct, since only
   persistent markets are fundable.
1. DONE 2026-07-18 (orchestrator): WO-99 now keys transitions on
   (state, candidate); a candidate change while eligible re-notifies, and an
   eligible->not_eligible drop sends a revocation push + alert artifact.
2. DONE 2026-07-18 (orchestrator): WO-99 eligibility now requires the
   decision policy's consumed study run (inputs_snapshot) to equal the
   current study generated_at, failing closed on the ~18-minute churn skew.
   Implemented via the existing run stamp rather than re-plumbing writers.
3. MERGED 2026-07-18 in owner-merged PR #259 (frozen surface):
   changes the FROZEN M-A gate's day-counting arithmetic. Tighten-only, but it
   alters a registered gate, so authorization is the owner's merge of the PR,
   not a unilateral orchestrator change. Implemented as maker-gate amendment
   M-A.1: extracted `_distinct_days_at_target` in `maker_carry_study.py`, which
   counts a UTC day only from its LAST published_v2 run (the pre-specified
   daily observation) so an intraday spike that later faded cannot bank a day.
   The current run is by construction today's last observation and governs
   today's membership. 4 tests added (intraday spike does not bank; last run
   at target counts; today governed by current run not earlier spike; legacy
   model day excluded). Docstring amendment registered in the module. Cannot
   raise the day count.
4. MERGED 2026-07-18 in owner-merged PR #259 (frozen surface):
   the WO-59 Kelly overlay is money-sizing on the frozen WO-50 surface. The
   dimensionally-correct fix is confirmed NOT tighten-only — it LOOSENS: the
   old `mean/std^2` on raw dollars was unit-dependent and absurdly small
   ($0.46 binding), so normalising to per-dollar returns raises the fraction.
   DIRECTION DISCLOSED in-code and here; authorization is the owner's merge.
   Implemented: `_daily_net_returns` divides each carry-history row's
   `portfolio_net_carry_usd_per_day` by its `portfolio_capital_usd`, so the
   fraction is unit-invariant; when per-row capital is unavailable it falls
   back to the strictly-more-conservative legacy dollar estimate. Absolute
   exposure stays bounded by the ladder cap (`binding = min(ladder_cap,
   kelly_capital)`). 4 tests added (drops rows without positive capital;
   unit-invariance dollars-vs-cents; loosens vs the dollar fallback; never
   exceeds the ladder cap).
5. PARTIALLY DONE 2026-07-18 (orchestrator): missing live bid/ask now PULLS
   (loss of book visibility = cannot manage risk), and the requote screen now
   consumes the WO-102 absolute raw-imbalance floor and composite block
   (pull). NOT changed: "missing toxicity -> pull" was rejected as
   over-tightening (toxicity is a supplementary signal; blanket-pull would
   make the sheet all-pull noise) and "failed Gamma lookup -> pull" is
   deferred (needs a lookup-failure flag threaded through; smaller follow-up).
6. DONE 2026-07-18 (orchestrator), resolved at the decision point rather than
   the frozen study: WO-99 eligibility now refuses a candidate whose exact
   requote alert_state is pull_quotes_now/STOP (fail-closed on a missing
   requote row). This prevents funding a market safety wants pulled without
   changing the study's event-start semantics (which differ by market type
   and would risk wrongly excluding valid continuous/touch markets).
7. M-B realism: passes on data-api-print markout estimate even with zero
   Tier-0 replay coverage; observed adverse was 2.08x estimate. Require the
   portfolio market's own Tier-0 coverage (ties to WO-102 phase 2 data).
8. Reward-share realism: single-snapshot extrapolation vs Polymarket's
   time-integrated epoch share; add uptime/competition-decay and a
   time-sampled denominator. Needs multi-sample collection first.
9. Ladder uses cumulative not daily profitability; concentration/tail limits
   absent (Iran+oil co-selected); live-test P&L lacks maker-test baseline and
   market attribution; winning_so_far on dust. Grouped medium-priority.
Medium items (reward config start/end + overlap; discovery bias vs active
rewarded endpoint; keyword-only resolution risk; inventory bootstrap spec)
are logged here and built after 1-8.

## WO-102 — Toxicity screen: absolute raw-imbalance floor (implemented 2026-07-17)

This entry records implementation provenance only; it does not assert or grant
owner authorization for funding, live execution, or any frozen/registered-
surface change. Phase 1 was built by the orchestrator and reached `main` through
PR #253. Funding remains CLOSED and WO-67 remains BLOCKED.

MATERIAL LATENT-BUG FIX (phase 1, IMPLEMENTED): `toxicity_score` is a
universe-relative percentile `index / (n-1)`. Standing rule 8 blocked on
`toxicity_score > 0.9`, so a genuinely one-sided market could be silently
DE-VETOED merely by measuring more calm markets alongside it that day — its
own flow unchanged. `flow_toxicity` now also emits an ABSOLUTE, universe-
independent `vpin_raw >= 0.90` floor and a composite `toxic_blocked`
(raw floor OR percentile), plus `raw_imbalance_block`, `percentile_block`,
`markout_coverage_ratio`, and `toxicity_block_reasons`. WO-99 eligibility
consumes the composite and treats an unmeasured market as blocked
(fail-closed). Strictly TIGHTEN-ONLY: strictly more markets block than under
the percentile-only rule, never fewer; the 0.90 percentile rule is unchanged.

Fail-safe sentence: a market with high raw imbalance, an unmeasured/absent
toxicity row, or any parse failure evaluates blocked; the screen can only add
blocks, never clear a market the prior rule blocked.
Day-after check: `flow_toxicity.csv` carries `vpin_raw`, `toxic_blocked`, and
`toxicity_block_reasons`; the WTI/one-sided candidate shows
`toxic_blocked=true` with `raw_imbalance>=0.9` in reasons regardless of how
many calm markets are measured.

DEFERRED (phase 2, NOT built — needs data plumbing and cannot be validated
today): the markout-economics gate the owner also listed — YES/NO direction
normalization of the imbalance sign across complementary tokens, 5/15/60-min
markout coverage with minimum-sample and coverage-ratio requirements,
separate bid-side/ask-side toxicity, post-fill loss vs modelled reward
income, and confidence intervals. BLOCKER: markout coverage is currently
~0% (observed 1,694/1,694 missing on the WTI candidate), so an economics gate
cannot be verified against real data now and must not ship untested. When a
markout-coverage collection fix lands, this phase builds as a strictly
ADDITIVE block (it may add economic blocks; it may never clear a market the
phase-1 raw/percentile screen flags), i.e. still tighten-only. Not buildable
until coverage exists.

## WO-99 — Owner push notification when a stage ticket becomes executable (filed 2026-07-17)

The maker gate passed 2026-07-17 with `fund_100_but_only_most_recurrent_market_half_target`
indicated, but the ticket was not executable: the named market carried
toxicity 0.97 (standing rule 8 bars > 0.9) and both portfolio markets'
minimum quotes exceeded the $100 stage budget. The owner's registered next
step is "wait for an executable ticket" — and no push channel exists; the
eligibility signal lives only in artifacts a human must open. Close that gap.

REGISTERED ELIGIBILITY CONDITIONS (all must hold on one policy run; evaluated
from existing artifacts only, no new judgment surface):
1. `indicated_action` starts with `fund_` and kill status is `clear`.
2. The named funding-candidate market (WO-50 most-recurrent rule) is in the
   CURRENT portfolio with `toxicity_score <= 0.9` (standing rule 8),
   `resolution_risk != high`, and no event-start inside 48h.
3. Its minimum quote capital (`rewards_min_size x 2 x mid`) fits the stage:
   <= $100.
4. Kill-input freshness `fresh`; reconciliation `clean` or `explained`.
5. Every source artifact consumed by the evaluator is timestamp-fresh at the
   evaluation instant. The tighten-only registered maxima are 30 minutes for
   decision policy, requote advice, maker replay, and sharp qualification (two
   15-minute producer cadences), and 26 hours for maker study, candidate
   toxicity, and wallet reconciliation (the registered daily-producer SLO).
   Missing, malformed, or future timestamps fail this condition.

BUILD:
1. A small evaluator in the decision-policy step writes
   `outputs/maker_carry/stage_ticket_eligibility.json` (atomic; states
   `eligible` / `not_eligible` with per-condition booleans and the first
   failing condition named) and a dashboard banner row.
2. PUSH CHANNEL (optional, env-gated): when state transitions
   not_eligible -> eligible (transition only, never repeats while unchanged),
   POST a fixed short message to an ntfy topic from
   `OPS_OWNER_NTFY_TOPIC_URL` in the VPS `.env` (never in config, telemetry,
   or repo). Message contains NO amounts, market names, keys, or balances:
   "Polymarket stage ticket eligible - read the quote sheet." Failure to
   send is logged, never blocks the policy step. If the env var is unset the
   feature is dashboard-only.
3. The same transition writes a WO-78-style owner-alert artifact so the
   notification also exists as an auditable file.
Fail-safe sentence: missing, stale, or malformed inputs evaluate
not_eligible; the push fires only on a verified transition; no gate,
sizing, or order surface reads the eligibility artifact.
Day-after check: `stage_ticket_eligibility.json` exists with per-condition
booleans matching the same run's decision_policy.json and quote sheet; with
the env var set, a forced test transition delivers exactly one ntfy message.

**2026-07-19 source-freshness remediation (pending owner merge):** the
evaluator now publishes one `*_source_fresh` condition and an age diagnostic
for every consumed policy/study/toxicity/reconciliation/requote/replay/
qualification source. This is additive and tighten-only: stale evidence can
revoke eligibility, but it can never clear an existing blocker. Funding stays
CLOSED and WO-67 stays BLOCKED.

**Day-after check:** in
`outputs/maker_carry/stage_ticket_eligibility.json`, inspect
`candidate.source_freshness`; every source must show `fresh=true`, a
non-negative `age_seconds`, and its registered `maximum_age_seconds` before an
eligible transition may be reported.

## WO-98 - Exact post-registration H2 evaluator and dashboard authority

Status: IMPLEMENTED by Codex on 2026-07-16; awaiting required gate and review.

Provenance correction (2026-07-16, orchestrator, confirmed with the owner):
this WO was filed AND built by Codex from its own 2026-07-16 line-audit
findings. The owner instructed a line audit only and did NOT direct or
authorize this work order. The original "Owner authorization" line was
written by Codex and was FALSE. This is
prospective shadow-research measurement only. It cannot create a signal,
change a gate or threshold outside this H2 contract, approve paper/live
trading, size/fund capital, or place an order.

Files: `docs/EXPERIMENT_REGISTRY.md`, this register,
`docs/POLYMARKET_QUANT_MODE_CHARTER.md`,
`src/polymarket_predictive_engine/dutch_arb_monitor.py`, new
`src/polymarket_predictive_engine/h2_dutch_evaluator.py`,
`src/polymarket_predictive_engine/cli.py`,
`src/polymarket_predictive_engine/training_harvest.py`,
`src/polymarket_predictive_engine/dashboard.py`,
`src/polymarket_predictive_engine/ledger_anchor.py`, and focused tests for the
producer, evaluator, harvest, CLI/ledger registration, and dashboard.

Registered implementation contract:

1. The live Dutch monitor keeps its legacy gross watch diagnostic, but also
   appends one immutable exact observation per completely priced event per
   scan to `outputs/h2_dutch/h2_scan_observations_v1.csv`. One injected UTC
   clock governs all rows in a scan. Rows preserve event/token identity,
   per-leg displayed ask/size, WO-94 fee provenance and official five-decimal
   per-order fee rounding, common size,
   resolution time, the fixed 0.002-per-basket cost reserve, all-in capital,
   net profit/return, qualification, and both trading-invoked flags false.
   The producer writes qualifying and complete non-qualifying rows; a missing
   event is not relabelled as a clear.
2. New `h2_dutch_evaluator.py` consumes only that exact append-only ledger.
   It rejects pre-registration, post-window, future, duplicate, malformed,
   incomplete, non-unique-token, nonpositive-depth, nonpositive-horizon, or
   internally inconsistent rows with explicit coverage counters. It
   deterministically reconstructs episodes and persistence exactly as filed
   in the H2 amendment in `docs/EXPERIMENT_REGISTRY.md`.
3. The evaluator atomically writes current episode state and
   `outputs/h2_dutch/h2_evaluation.json`. At the registered stop it freezes
   the formal first-100/deadline sample in append-only
   `h2_final_episodes_v1.csv`; reruns cannot revise frozen episode economics.
   Its event-clustered bootstrap is deterministic, the single registered H2
   test reports FDR not applicable, and failure stays suppressed. Both
   append-only ledgers join the WO-61 anchor registry.
4. Add CLI `h2-dutch-evaluate` and run it after live collection in the daily
   resilient training harvest. The harvest child remains bounded and
   fail-visible. No broker, strategy, readiness, risk, execution, signer,
   credential, capital, or order path may import or consume the verdict.
5. `dashboard.py` reads the exact H2 artifact as the primary Dutch evidence
   authority. It shows collecting/pass/suppressed state, stop progress,
   coverage/exclusions, episode/day/persistence floors, exact cost model,
   aggregate net profit, clustered 90% interval, concentration, and the next
   unmet condition. The old gross scanner is labelled live diagnostic
   collection only and cannot display a registered H2 verdict.

Fail-safe sentence: absent, stale, future-dated, malformed, internally
inconsistent, incomplete, unanchored, or lock-contended evidence cannot pass
H2; missing scans cannot prove a clear or persistence; early samples remain
collecting; every stopped sample that misses any registered support condition
is suppressed; every artifact remains shadow-only with paper/live invocation
flags false.

Engineering-standards review plan: S1 traces the registration boundary,
single scan/evaluation clocks, exact 10--20 minute persistence gaps, 60-day
deadline, and future-row exclusions. S2 uses runtime locks plus append-only
observation/final ledgers and atomic current JSON/CSV state; producer/evaluator
interleavings are tested. S3 names the live Gamma/CLOB producer and exact
ledger/evaluator/dashboard consumers with no alternate legacy input. S4 uses
recorded Gamma/CLOB fixture shapes plus hand-computed planted-edge, null,
clear, missing-scan, duplicate-day, persistence-gap, fee, concentration,
clock-advance, stop, immutable-rerun, and tighten-only cases. S5 is the
fail-safe sentence above. S7 must verify all H1/H3, paper/live, gate, stake,
capital, signer, credential, broker, and order surfaces are unchanged.

Day-after check: require a post-deploy row in
`outputs/h2_dutch/h2_scan_observations_v1.csv` with unique event/token
identity, complete plan, canonical fee provenance, positive common size,
fixed reserve 0.002, internally reconciling all-in capital/net profit, and both
trading flags false; require `h2_evaluation.json` newer than that harvest,
`status=collecting` before the stop, nondecreasing independent episode count,
explicit coverage counters, and the dashboard H2 panel to cite that exact
artifact while labelling the old gross watch diagnostic.

Landed implementation: the 15-minute Dutch monitor now records every complete
fee-bearing basket scan, including complete non-opportunities that can prove a
clear, in `outputs/h2_dutch/h2_scan_observations_v1.csv`. The exact evaluator
reconstructs event-day episodes, enforces the frozen persistence and stop
rules, writes atomic current state, freezes its formal sample in
`h2_final_episodes_v1.csv`, requires a verified ledger anchor before a
statistical pass can become a shadow candidate, and publishes
`h2_evaluation.json`. Canonical category/price-aware fees use the venue's
five-decimal per-order rounding. CLI, the resilient daily harvest, the WO-61
anchor registry, and the dashboard all consume the exact lane; the gross watch
is visibly diagnostic. No paper/live order path consumes H2.

S7 implementation review: S1 was traced through one injected scan timestamp,
strict registration/future/deadline comparisons, fixed 10--20 minute gaps, and
clock-advance/stop tests. S2 uses a shared observation lock, append-only
schema-checked observation/final ledgers, a manifest-before-append crash
recovery boundary, and atomic current JSON/CSV; lock contention fails closed.
S3 was verified against the Gamma event/CLOB book producer and the single
exact ledger/evaluator/dashboard consumer path. S4 replays a sanitized live
five-leg negRisk Gamma event plus recorded CLOB book and covers hand-computed
rounded fees, clears, missing scans, same-day recurrence, malformed economics,
locks, null/deadline suppression, planted support, concentration, anchor
transition, immutable rerun, and tighten-only settings. S5 is the fail-safe
sentence above. S6 remains the post-merge day-after check above. S7 frozen-
surface search found no readiness, risk-decision, signal, paper/live gate,
stake, capital-policy, signer, credential, broker, or order-path consumer.

Verification: Ruff passed across `src` and `tests`; 100 focused tests passed;
1,292 repository tests excluding the three-case runtime-lock file plus its one
unaffected case passed; all six Git-dependent deployment tests passed in the
isolated container after installing Git. The two calendar-fragile runtime-lock
tests are unchanged, fail only because their July 3 wall-clock fixture now
exceeds a nominal 999,999-second age, and remain assigned to WO-100. An
isolated public-network smoke scan discovered 20 negRisk events, priced three
complete baskets, appended three exact rows, remained `collecting` with zero
manufactured episodes, and reported both trading-invoked flags false.

## WO-101 — Observation-time-safe resolved corpus and independent validation

Status: IMPLEMENTED by Codex on 2026-07-19; awaiting complete-suite gate and
independent owner review.

Review remediation on 2026-07-19 corrected five fail-closed defects: Gamma
backfill now records the production observation clock only after the response;
source fingerprints remain uncommitted while future quote rows are deferred;
labels whose computed availability exceeds the assembly clock are excluded;
larger validation fractions are honoured up to the one-train-market boundary;
and the diagnostic trainer writes a WO-101-specific summary that cannot feed
either legacy readiness or canonical validation promotion.

Post-merge review remediation on 2026-07-19 quarantines a token whenever clean
append-only observations disagree on its close time, so a superseded later
boundary cannot admit post-close quotes. Every generated feature, label, and
split CSV row now also carries explicit `paper_trading_invoked=false` and
`live_trading_invoked=false` fields rather than relying on a separate summary.

Scope boundary: this is diagnostic H3 structural-bias research substrate only. It
cannot replace the WO-96 exact prospective H3 evaluator, create a fourth
hypothesis, change a gate/threshold/stake, authorize paper/live trading, fund
capital, or place an order. Funding remains CLOSED and WO-67 remains BLOCKED.

Files: new `resolution_corpus.py`, `historical_bid_ask.py`, and
`leakage_safe_training.py`; the three Gamma-backed resolution producers,
`models/skill_model.py`, `training_harvest.py`, CLI, ledger anchoring,
producer/consumer contracts, example configuration, the experiment registry,
quant charter, and focused tests.

Registered implementation contract:

1. All three Gamma-backed resolution producers retain their legacy current-run
   snapshots for compatibility and append each distinct token-level state,
   under one shared lock, to versioned
   `outputs/polymarket_training/resolution_corpus_v1.csv`. One producer clock
   is captured after the Gamma response and injected through every row. A
   content-derived identifier deduplicates an
   unchanged state across producers; changed targets, quality, or settlement
   timestamps remain immutable evidence. Conflicting clean targets remain in
   the ledger and are excluded downstream.
2. A label's availability is
   `max(close_time, resolution_time, resolution_observed_at_utc)`. The first
   observation timestamp is selected for an otherwise identical clean state.
   Future observations cannot enter an earlier as-of run, and an API's
   historical close timestamp cannot backdate information availability. The
   assembler independently rejects any computed label-availability time later
   than its own as-of clock.
3. The official CLOB `prices-history` response remains a single-price
   diagnostic and is never treated as executable. New
   `collect-historical-bid-ask` streams the canonical live feature table and
   immutable gzip training archives, appending only timestamped, two-sided,
   non-crossed exact market/token books to versioned
   `historical_bid_ask_v1.csv`. No bid or ask is imputed from a midpoint.
   Disk-backed exact deduplication bounds memory, source state advances only
   after successful ledger appends, and an interrupted run safely rescans
   through the immutable ledger identifier. A source containing any future row
   deliberately retains its prior fingerprint so the unchanged source is
   revisited after the run clock advances.
4. New `build-leakage-safe-training` reads only the two versioned ledgers. It
   requires exact token/market identity, clean binary settlement, both close
   and resolution times, an observed quote strictly before close and label
   availability, and an exact book inside the fixed seven-day pre-close
   window. It keeps one deterministic quote per token-hour and atomically
   writes separate feature-only, label-only, and market-split audit snapshots.
5. Whole canonical markets are ordered by observation-safe label availability.
   Validation is the larger of the latest 30% and 10 distinct markets, while
   preserving at least one potential training market. A training market
   survives only if its label was available strictly before the earliest
   validation feature minus the 24-hour embargo. Overlapping markets are
   purged. With fewer than 11 independent markets, all rows remain collecting.
   Configuration can tighten but cannot reduce the 10-market floor, shorten
   the embargo, widen the lookback, or thin more finely. A configured
   validation fraction above 30% is preserved (capped only at 100%); the split
   itself always retains at least one potential training market.
6. `train-skill-model` refuses the legacy feature/label path and consumes only
   the preassigned WO-101 files. It independently requires at least 10
   validation markets and zero market overlap. The midpoint remains a
   forecasting baseline, while diagnostic ROI buys only at the recorded ask
   and charges the canonical category/price-aware taker fee. It never
   fabricates a complement quote. Outputs state diagnostic H3 substrate, no
   registered-H3 verdict authority, and no promotion authority. They are
   written only to `wo101_diagnostic_skill_model_summary.json`; explicit
   `promotion_authority=false` is rejected defensively by readiness and
   validation consumers.
7. The resilient daily harvest runs resolution backfill, websocket-token
   resolution, legacy descriptive price history, exact bid/ask ingestion,
   leakage-safe assembly, and the diagnostic trainer in dependency order. Both
   append-only ledgers are enrolled in the code-default and deployed-config
   WO-61 anchor registries and in explicit WO-79 producer/consumer contracts.

Fail-safe sentence: missing, stale, future-dated, malformed, one-sided,
crossed, post-close, out-of-window, identity-mismatched, unlabelled, ambiguous,
conflicting, lock-contended, observation-backdated, temporally overlapping, or
under-supported evidence is excluded or remains collecting; it cannot be
imputed, promoted, funded, or traded.

Engineering-standards review: S1 traces one injected producer clock and proves
labels wait for actual observation. S2 inventories two locked append-only
ledgers and atomic mutable snapshots, with dedup-before-state-advance crash
recovery. S3 registers both producer/consumer paths. S4 replays a recorded
public book and tests post-response clocks, deferred future-row fingerprints,
immutable prefixes, exact dedup, conflicts, future labels and quotes,
midpoint-only rejection, identity mismatch, thinning, embargo purge, the
10-market floor, tighten-only settings, and zero market overlap. S5 is the
fail-safe sentence above. S6 is the day-after check below. S7 keeps every exact
H1/H2/H3 verdict, readiness, risk, stake, funding, signer, credential, broker,
and order surface unchanged.

Day-after check: require
`outputs/polymarket_model_governance/leakage_safe_training_summary.json` to
report `work_order=WO-101`, both input ledgers with nonzero byte length and
SHA-256, `midpoint_only_rows_accepted=0`,
`split.market_overlap_count=0`,
`split.minimum_validation_markets_required=10`, and both trading flags false;
require new websocket variation eventually to increase
`historical_bid_ask_summary.json.rows_appended`, and require both append-only
ledgers to pass the next prefix-anchor verification. `status=collecting` is
valid until observation-safe resolved labels overlap enough exact quotes; it
must not be relabelled as edge.

Verification required before publication: Ruff, the focused WO-101 and
dependency tests, and the complete unfiltered repository suite must all pass
in the isolated bounded VPS container on the exact proposed commit.

## Current queue for Codex (reconciled 2026-07-19)

Every WO below and every future WO must comply with
`docs/ENGINEERING_STANDARDS.md` (S1-S7), including the mandatory
`Day-after check:` line. Reviews verify compliance item by item.

The 2026-07-19 corrective request is recorded here for owner review and becomes
repository authorization only through the owner's merge of this PR. Funding
remains CLOSED and WO-67 remains BLOCKED. No item below authorizes funding,
live execution, or a gate/threshold loosening. Complete each line as one
independently reviewable PR, and require an owner merge for every
frozen-surface change.

1. **Governance provenance (DONE — PR #270 merged 2026-07-19):** the unapproved
   dispatch/queue-driver protocol from merged PR #263 was removed without
   reverting its safety fixes; the one-scope-per-PR rule is retained
   prospectively. Residual dispatch-bridge authorization-basis claims were
   removed 2026-07-20, leaving the bridge under the owner's direct instruction
   only.
2. **Review-remediation PRs:** fix exact-token identity, sharp-source
   provenance, qualification-to-current-policy anchoring, maker-markout token
   contamination, and source freshness. Each defect is a separate PR.
3. **WO-100:** rebuild closed PR #243 from current main. Enforce the complete,
   unfiltered suite and latest-review-on-current-head semantics. Establish real
   branch protection or a documented fail-closed independent merge process;
   do not claim GitHub-plan controls that the repository cannot enforce.
   **Implemented 2026-07-19, pending independent review/merge:** the ARM64 gate
   now runs the complete unfiltered suite in a bounded Python 3.11 container,
   and the merge lane binds a second identity's approval, the latest
   exact-head check/workflow results, an up-to-date main ancestry, and resolved
   threads to an atomic non-force main update whose only parent is the verified
   main SHA. A 2026-07-19 correction replaces branch-selectable manual dispatch
   with an owner-only `/independent-merge <exact-head-sha>` PR comment. GitHub
   loads that `issue_comment` workflow only from the default branch; both the
   original actor and rerun initiator must be the repository owner, while the
   exact-head approver must be a distinct trusted identity. It rejects PRs that alter
   its trusted workflows/scripts or any pytest collection/execution control.
   Review remediation on 2026-07-19 also normalizes GitHub's real
   `workflow-path@ref` response, audits active no-bypass required-workflow
   rulesets rather than hard-coding enforcement false, binds the rule to this
   repository's numeric ID and the latest accepted commit that changed the
   required workflow, and leaves a PR open if
   its head changes after the atomic main update but before administrative
   closure. The lane remains BLOCKED while the
   repository has only one push-capable identity, and the audit continues to
   report the unavailable private-Free required-workflow protection honestly.
   A legacy name/app-bound required context is explicitly insufficient because
   another GitHub Actions workflow can publish the same job name. A successful
   atomic update also publishes the complete evaluator result as the run-scoped
   `independent-main-acceptance-<run-id>/merge-attestation.json` artifact, so a
   deploy can bind the exact accepted main SHA to the registered workflow run.

   **Day-after check:** inspect
   `outputs/performance/independent_merge_gate.json`;
   `checks.workflow_configured` and `independent_merge_process_configured` must
   be true, while `required_workflow_identity_enforced` and `enforced` must
   remain false unless a workflow-identity-capable required-workflow ruleset is
   independently verified against the exact repository ID and accepted
   workflow commit. No merge is eligible without an exact-head
   successful gate, a distinct current-head approver, an owner-originated
   default-branch comment run and rerun, unchanged
   trusted merge control, and an unchanged main ref at the atomic update. Test
   a suffixed real Actions workflow path, a pytest-control change, and a
   post-update PR-head race before relying on the lane. A successful merge run
   must also contain the exact-SHA deployment attestation.
4. **WO-101:** rebuild closed PR #242 from current main — IMPLEMENTED; review
   pending. The rebuild uses observation-time label availability, append-only
   resolution history, exact historical bid/ask, a purged chronological split,
   and at least 10 distinct independent validation markets.
5. **Deployment controls:** implement an actual tested rollback, isolate
   deploy acceptance from the continuously running scheduler, and deploy only
   an independently accepted main revision.
   **Implemented 2026-07-19, pending independent review/merge and production
   deployment:** the deploy workflow now requires the exact successful
   independent-merge attestation for the target `main` SHA. Before cutover it
   snapshots the aligned source/deployed marker, mode-0600 environment, and
   last-known-good image; every post-cutover failure invokes a tested backward
   checkout that preserves all runtime roots, restores env/marker/image,
   recreates all four production services, and requires the ordinary health
   check. Real-data acceptance moved out of the continuous scheduler into the
   profiled, one-shot `vps-deploy-acceptance` service; the workflow stops and
   proves the scheduler absent before starting it. Capacity preflight models
   that profile as an alternative to the stopped 2 GiB scheduler, reports each
   concurrent mode, and therefore does not falsely add both services to the
   8 GiB host commitment. No broker, signer,
   cancellation, paper-order, live-order, gate, threshold, stake, or funding
   path changed. Missing/malformed attestation or rollback prerequisites stop
   before cutover; any later failure rolls back, and an unprovable rollback
   remains failed and retains mode-0700 recovery material for an operator.

   Producer/consumer contract: `independent-pr-merge.yml` produces
   `merge-attestation.json` as a run-scoped GitHub artifact; the deploy workflow
   consumes only the explicitly named run after verifying its workflow path,
   success, distinct current-head approver/actor, all controls, and exact merge
   SHA. The scheduler produces the fresh governance pass; only after that
   producer completes is it stopped, and the one-shot acceptance service owns
   `deploy_acceptance_cycle.json`, `deploy_acceptance.json`, and the final
   operating-state update without a concurrent scheduler writer.

   **Day-after check:** in
   `outputs/ops_scheduler/deploy_acceptance_cycle.json`,
   `execution_lane=dedicated_one_shot_container`, `scheduler_isolated=true`,
   and every command exit is zero; `outputs/ops_scheduler/deploy_acceptance.json`
   is `PASS` for the deployed marker. If rollback was exercised,
   `outputs/performance/vps_deploy_rollback.json` must be `PASS` with
   `decision=ROLLED_BACK_TO_LAST_KNOWN_GOOD`, and checkout, environment, marker,
   image-backed services, and health check must all be restored.
6. **Dashboard transport:** IMPLEMENTED by Codex on 2026-07-19; awaiting the
   required gate and owner review. Compose hard-codes a loopback-only host
   binding, authenticated tailnet-only HTTPS is configured with Tailscale
   Serve, Funnel is rejected, deploys fail before cutover when Tailscale is not
   enrolled, and host health writes fail-closed transport evidence. This is the
   registered **authenticated HTTPS or a private VPN** path. Producer:
   Tailscale status/Serve plus Docker's effective port binding. Consumers:
   deploy acceptance, VPS health, operator runbooks, and the dashboard URL
   hint. Fail-safe: missing/stale/mismatched transport blocks deployment health
   and never falls back to public HTTP. Day-after check: on the deployed VPS,
   verify the transport artifact is `PASS`, `docker port` is loopback-only,
   the private URL works from an enrolled phone, the public-IP URL does not,
   and no Funnel route exists.
7. **Review hygiene:** adjudicate every surviving review thread against current
   main; resolve only findings that are fixed or demonstrably superseded, and
   fix applicable findings in their own PRs first.
8. **WO-111 (rev.2, anchor-safe) — PROPOSAL, NOT authorized to build:**
   because it enrolls a new anchored ledger glob and sits in the maker-gate
   module, an agent may build it only after owner authorization (an owner
   commit or the owner's merge of its registration); this queue entry is not
   that authorization. The proposal: persist per-day portfolio membership +
   per-market markout forward-only in a NEW sidecar
   `maker_carry/maker_carry_portfolio_members.csv` (fixed two-column schema:
   `generated_at_utc`, `portfolio_members` JSON), enrolled `append_only`, leaving
   the anchored `maker_carry_history.csv` byte-identical so no prior anchor
   prefix changes (the WO-110 lesson). The current M-A gate is unchanged and does
   not read the sidecar; a FUTURE per-market tightening can recompute from it
   instead of failing closed and orphaning the at-target streak (the failure that
   collapsed M-A 8/7 -> 1/7 when PR #290's markout-measured requirement landed
   with no pre-existing per-market evidence). Changes no gate, threshold,
   share-model scope, or verdict; enrolls one new anchored glob. Full spec in the
   WO-111 section above. **Day-after check:** on the VPS one cycle after deploy,
   `outputs/maker_carry/maker_carry_portfolio_members.csv` exists, its newest
   `generated_at_utc` matches the newest `maker_carry_history.csv` row, its
   `portfolio_members` deserializes to the live `maker_carry_study.json`
   `portfolio` condition_ids, and `verify-ledger-chain` returns ok for BOTH that
   sidecar and the still-byte-identical `maker_carry_history.csv`. Owner-merge
   (enrolls an anchored ledger; sits in the maker-gate module).

Completed context: WO-93–WO-110 are merged as recorded in their individual
sections, except that WO-100 and WO-101 are intentionally being rebuilt from
their closed historical PRs. WO-33/34/35 remain governed by WO-101's leakage
review and the H1–H3 freeze. WO-48, WO-67, WO-73 item 4, and WO-75 item 2 remain
blocked. WO-70 and WO-72 remain deferred; WO-76 remains registration-only;
WP13 still requires a separate owner decision.

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

## WO-114 — VPS ops hygiene: seasonal-job disable switch, ops-log rotation, dashboard-setup readiness wait — `done` (2026-07-21, PR #354, owner merge)

Orchestrator-built, owner-merged. Non-frozen ops scripts only; no gate,
threshold, policy, registry, verdict, or order path.
- `run_vps_ops_scheduler.sh`: `OPS_CARD_REFRESH_ENABLED` (default 1; 0 records
  an intentional exit-0 skip BEFORE the odds preflight — used to quiesce the
  seasonal SuperBru locked-card chain after the World Cup ended and its odds
  feed returned zero events). `OPS_LOG_MAX_BYTES` rotation (default 50 MiB,
  single `.1` generation, top-of-loop) after ops_scheduler.log reached 5.5M+
  lines.
- `configure_polymarket_dashboard_tailscale.sh`: bounded 60s loopback readiness
  retry after `--force-recreate` (was a single racing probe).
Deployed 2026-07-21; verified in production: locked_card_refresh flipped to
`intentional 0`, both degraded incidents cleared; 190 MB log rotated to `.1`;
configure script has passed first-try on both subsequent deploys.

## WO-115 — Unbreak ledger anchoring: snapshot-enroll the rewritten carry history, fail loud on blocked chains — `done` (2026-07-26, PR #356 commit 6e04263, owner merge; registered WO-61 surface)

Root cause of the chain freeze at 2026-07-16: `maker_carry/maker_carry_history.csv`
was enrolled `append_only`, but its committer legitimately REWRITES the file
(legacy-schema upgrade path in maker_carry_study.py), so an authorized header
widening changed the 2026-07-12 anchored prefix and every anchor run since
short-circuited `blocked_broken_chain` while the CLI exited 0 (fail-silent;
only visible as the slow `ledger_anchor_age` SLO breach, ~10 days).
- Reclassified `append_only` -> `snapshot` in DEFAULT_LEDGER_REGISTRY AND the
  deployed config `ledger_globs` (lockstep, per the #269 superset pin);
  precedent decision_policy.json / requote_alerts.json. WO-111 members sidecar
  unchanged (stays append_only). Consistent with the 2026-07-19 Rev.2 note,
  which barred ADDING a column under append_only enrollment and did not
  consider the enrollment itself.
- `anchor-ledgers` CLI: zero-exit allowlist {ok, already_anchored, disabled,
  skipped_locked}; blocked/error/unknown statuses now exit 1 so the
  scheduler_nonzero_exit watchdog fires within one cycle.
- Chain re-genesis executed by the owner on the VPS 2026-07-26 (fresh genesis
  approved 2026-07-26): broken chain + head archived to
  `outputs/performance/ledger_anchor_retired/20260726T100457Z/` (historical
  anchors 2026-07-12..16 preserved there, on the `vps-anchor` branch, and in
  `ledger_anchor_snapshots/`). New chain verified:
  anchor_date 2026-07-26, chain_head 9fc5ff0a..., previous_chain_head all-zeros.

## WO-116 — Seed official-book collection for top-ranked candidates before selection — `done` (2026-07-26, PR #356 commit 31a3e95, owner merge)

The WO-113 measurement-eligibility gate is correct, but collection was
portfolio-only plus the WO-104 mtime tranche (re-polls only markets that
already have a book file). The fast-churning rewarded universe starved the
portfolio to 0-1 eligible markets (live 2026-07-21..25; carry $0 vs the
$3.33/day target), stalling M-A at 3/7 against the 2026-08-19 terminal date.
- `maker_fill_replay.py snapshot_official_books`: third watchlist tranche
  `_candidate_seed_markets` — best-ranked candidates from the persisted
  `maker_carry_candidates.csv` not already in the portfolio/persistent
  tranches, sorted by net_carry_usd_per_day (tiebreak yield_rank), capped by
  new `max_candidate_markets` (code default 20; deployed config 25, matching
  the 2026-07-19 owner-approved breadth posture). Runs even with an empty
  portfolio. Seeded files stay warm via the existing mtime tranche and season
  toward eligibility.
- Collection breadth ONLY: no gate, threshold, eligibility rule, sizing, or
  order path reads the setting; the collection-window ledger stays
  portfolio-only (coverage_ratio semantics unchanged); snapshot summary now
  reports per-tranche counts.

## WO-117 — Window-aware overrun classification for the harvest-gated maker study — `done` (2026-07-26, PR #356 commit 98f4033, owner merge)

Telemetry 2026-07-25: the scheduler_overrun_cycles SLO breach (10 consecutive)
came ENTIRELY from `maker_study_intraday` — every other job's consecutive
counter was 0 and the job itself ran and exited 0. The job may only fire
inside the registered 11-13h harvest-age window, whose daily recurrence drifts
by more than TICK_SECONDS, so the bare 24h yardstick labeled every legitimate
on-window run "overrun" by construction (mismeasurement, not starvation).
- `run_vps_ops_scheduler.sh`: this one job's lateness is now judged against
  interval + one window width (OFFSET_MAX - OFFSET_MIN, 2h at defaults); a run
  later than that genuinely missed a window and still stamps overrun. SLO
  target (0) and tighten-only rule untouched; run timing unchanged — label
  only. All other jobs keep bare-interval classification.

## WO-127 — A restore must not wedge the anchor lane: boundary-scoped restore provenance — `in-review` (2026-07-27, PR #364; owner merge required — changes the registered WO-61 verification contract)

Registered here before merge because it introduces a new producer/consumer
artifact and changes what `verify_ledger_chain` accepts. Codex review of #364
(P1) correctly refused the change on the grounds that code comments and a commit
message are not a registration.

**Defect (merged, on `main` at `973edf1`).** WO-123 excluded
`polymarket_training/` from the recovery archive while leaving it enrolled in the
WO-61 chain, and gave `verify_ledger_chain` a caller-supplied
`tolerated_missing_prefixes`. Only the disaster-recovery restore check passed it.
`anchor_ledgers` — the caller that actually freezes the head — passed nothing, so
a restore reported `status: ok` and the very next production anchor run read the
excluded corpora as "anchored file is missing", short-circuited
`blocked_broken_chain`, and froze the head. Recovery handed over a tree that
wedged the tamper lane: the exact failure WO-115 spent ten days undoing.

Two facts found by reading the code, both of which the audit's description
missed and which changed the design:

1. Post-restore anchor rows were already correct. The corpora are enrolled as
   exact paths, so after a restore the manifest build takes its existing
   no-matches branch and records `status: "missing_at_anchor"`, which
   verification already tolerates. Nothing needed to change going forward.
2. The break is historical rows, and it is permanent, not transient. Pre-restore
   rows recorded `status: "present"` with `byte_length` and `prefix_sha256`, and
   verification walks from genesis. Once the corpus is **re-harvested** those
   rows flip from "anchored file is missing" to "anchored prefix digest changed",
   which no absence-tolerance can excuse. A restore followed by ordinary
   collection wedges the chain forever.

**Design.** Tolerance becomes a property of the tree's recorded provenance,
scoped to the restore boundary — not a per-caller argument, so the restore check
and the production anchor lane agree by construction instead of by each caller
remembering to opt in.

Files: `src/polymarket_predictive_engine/ledger_anchor.py`,
`src/polymarket_predictive_engine/disaster_recovery.py`, `docs/RESTORE.md`.

- `ARCHIVE_EXCLUDED_PREFIXES` moves into `ledger_anchor.py` (`disaster_recovery`
  already imports `ledger_anchor`; the reverse is circular), so verification can
  intersect against the registered set directly.
- **Producer:** an applied restore (`verify_and_restore_archive`) writes
  `outputs/performance/ledger_restore_provenance.json` recording
  `excluded_path_prefixes`, `restore_boundary_date` (the archive's
  `snapshot_date`), and `restored_from_chain_head`. It is written into the
  extracted tree **before** verification, so the verified tree and the
  handed-over tree are the same tree.
- **Consumers:** `verify_ledger_chain`, and therefore `anchor_ledgers`, the
  `verify-ledger-chain` CLI, and the DR restore check. Coverage needed: the
  marker is required only on a restored tree; its absence is the normal state and
  means no entry is excused.
- Manifest entries under a **registered AND declared** prefix, belonging to rows
  anchored **at or before** the boundary, are unverifiable by design: neither
  absence nor digest divergence breaks the chain, counted in a new
  `restored_unverifiable_tolerated`. Rows anchored after the boundary verify
  normally.
- Restore tolerance intersects **manifest-declared ∩ registered ∩
  config-effective**, so an archive cannot widen its own tolerance and a config
  that *narrows* exclusions cannot excuse something that should have been
  archived.
- The `tolerated_missing_prefixes` parameter is removed.
- An archive that declares a prefix excluded while also **including** a member
  under it is refused at the untrusted-input boundary (Codex #364 P1): the
  declaration grants verification tolerance, so such an archive would grant
  itself tolerance for its own payload, and arbitrary bytes with a
  self-consistent archive manifest would restore `ok`. `_archive_source_payloads`
  never builds that shape.
- Exclusion parsing moves inside `create_ledger_archive`'s `try:` — `_base_payload`
  ran before it, so a malformed `excluded_path_prefixes` raised with no status
  stamped, reintroducing the blind-failure class WO-122a removed.

**Trust bound, stated deliberately.** A forged or hand-edited marker can only
excuse prefixes in the **registered** set, so the worst case is exactly the
re-harvestable corpora already in that set and nothing else. Every other anchored
path is verified regardless of what the marker says. (Whether that set is right is
settled by WO-123's registration and its merge record, not by this text.)

**Fail-safe direction (S5).** When the marker is missing, unreadable, not a JSON
object, declares no registered prefix, carries a non-canonical or invalid
`restore_boundary_date`, or carries a boundary in the future, the observable
behaviour is: **nothing is excused and every anchored path is verified** — a
broken chain if the corpora are absent. A present-but-rejected marker also
reports `restore_provenance_rejected` with the reason, so an operator is never
shown an unexplained broken chain. Boundary and anchor dates are compared as
parsed calendar **dates**, never as strings: a valid but non-canonical `2026-7-1`
sorts lexically above every canonical `2026-0M-DD` row and would otherwise have
excused post-boundary rows (Codex #364 P1). A row whose own `anchor_date` is not
a canonical date is never excused.

**Interleaving (S2).** New artifact: `performance/ledger_restore_provenance.json`,
written with `utils.write_json` (temp + `os.replace`), so a concurrent reader sees
either no marker or a complete one — never a partial one. It is written only by a
restore, which runs on a quiesced host with the recurring stack stopped
(`docs/RESTORE.md`), and it is never rewritten by the recurring stack. Existing
artifacts touched: `performance/ledger_anchor_verification.json` gains
`restored_unverifiable_tolerated`, `restore_boundary_date`,
`restore_tolerated_prefixes`, `restore_provenance_rejected` (same writer, same
atomic path); `performance/restore_verification_status.json` gains
`restored_unverifiable_tolerated`, `restored_without_prefixes`,
`restore_boundary_date`. Readers of both files that do not know the new fields are
unaffected — no field changed meaning.

**Reporting only.** No gate, threshold, sizing rule, policy, broker, or order path
reads any of it; nothing here loosens a maker gate or the verdict engine.

Tests (`tests/polymarket_predictive_engine/test_ledger_anchor.py`,
`tests/polymarket_predictive_engine/test_disaster_recovery.py`):
1. restore → immediate `anchor_ledgers` verifies `ok` (the regression);
2. restore → re-harvest the corpora with different bytes → still `ok` (the
   permanent-wedge case);
3. a digest change on a non-excluded restored file still breaks the chain;
4. a row anchored after the boundary is verified normally, not excused, including
   under a non-canonical boundary;
5. a marker declaring an unregistered prefix excuses nothing and says so;
6. an invalid, non-canonical, or future boundary is refused with a reason;
7. an unreadable / non-object / empty marker is refused with a reason, and is
   distinguishable from no marker at all;
8. an archive that both declares and includes an excluded prefix is refused,
   whether or not its bytes match the live corpus, while the honestly-built
   archive still restores;
9. a malformed exclusion config stamps `status: error` instead of raising.

**Day-after check:** on the deployed VPS one cycle after deploy, on a tree that
was **not** restored, `outputs/performance/ledger_anchor_verification.json` shows
`status: ok`, `restore_boundary_date: null`, `restore_tolerated_prefixes: []`,
`restored_unverifiable_tolerated: 0`, `restore_provenance_rejected: null`, and no
`outputs/performance/ledger_restore_provenance.json` exists; `anchor-ledgers`
exits 0 and `ledger_anchor_head.json` `anchor_date` equals today, proving the head
advanced rather than froze. Then run
`sh scripts/restore_from_archive.sh --dry-run --repo-dir /home/opc/Claude` and
confirm `outputs/performance/restore_verification_status.json` shows `status: ok`,
`restored_without_prefixes: ["polymarket_training/"]`,
`restored_unverifiable_tolerated` greater than 0 once the chain carries
pre-boundary corpus rows, and `restore_boundary_date` equal to the archive's
snapshot date. A non-zero `restored_unverifiable_tolerated` in the *production*
verification artifact on a tree nobody restored is itself the finding.
## WO-128 — Harvest and anchor integrity: atomic snapshots, a non-destructive anchor tail, and no silent field loss — `queued` (ISSUED to Codex 2026-07-27; non-frozen except the `ledger_anchor.py` snapshot writer, which routes to owner merge)

Four independent defects found in the 2026-07-27 audit of already-merged code.
All four are integrity defects in the tamper/evidence lane, none changes a gate.

Files: `src/polymarket_predictive_engine/ledger_anchor.py`,
`src/polymarket_predictive_engine/utils.py`,
`src/polymarket_predictive_engine/shadow_cohort.py`,
`scripts/run_vps_ops_scheduler.sh`,
`tests/polymarket_predictive_engine/test_ledger_anchor.py`,
`tests/polymarket_predictive_engine/test_utils.py`,
`tests/polymarket_predictive_engine/test_shadow_cohort.py`.

**128.1 — the immutable daily snapshot copy is not atomic.**
`ledger_anchor.py:392` does `shutil.copyfile(source, verification)` directly onto
the final snapshot path. A crash, timeout kill (the lane runs under `timeout
"$LEDGER_ANCHOR_TIMEOUT"`), or a full disk mid-copy leaves a TRUNCATED snapshot
at the canonical path. The next run finds `verification.exists()`, compares the
truncated length against the live source, and raises
`FileExistsError: immutable daily ledger snapshot already differs` — permanently,
because the snapshot is by design never rewritten. One interrupted copy wedges
the anchor lane until an operator deletes the file by hand.
Fix: copy to a sibling temp path in the same directory and `os.replace` onto the
final name, so the canonical path only ever holds a complete copy. Do not add a
"repair by overwrite" branch — that would defeat snapshot immutability.

**128.2 — an anchor-tail failure re-runs the whole expensive harvest.**
In `scripts/run_vps_ops_scheduler.sh` the `run_ledger_anchor` subshell runs
`set -e` with `anchor-ledgers` followed by `push_vps_anchor.sh || true`. The
harvest lane's own anchor tail has the same shape: a nonzero anchor exit fails
the whole job, and the scheduler retries the job — re-running the multi-minute
harvest that already succeeded. Loud is right; re-doing hours of collection to
re-report the same anchor failure is not.
Fix: separate the exit accounting so the harvest's own outcome and the anchor
tail's outcome are stamped independently (`stamp_status` for each, distinct
detail strings), with the anchor tail's nonzero exit still surfacing nonzero for
the job so `scheduler_nonzero_exit` still fires. The harvest must not be re-run
because the tail failed. Keep the existing `OPS_SCHEDULER_LIBRARY_ONLY=1`
sourcing seam and add tests through it.

**128.3 — `append_csv_rows_matching_existing_header` silently drops newer fields.**
`utils.py:203-227` (my WO-119) appends under the header already on disk,
"dropping keys the legacy schema cannot hold". Against a legacy narrower header
that means new cohort fields are written as NOTHING, with no error and no
telemetry — the append succeeds, the anchor stays intact, and the data is gone.
Silent data loss is not an acceptable price for anchor safety.

**Narrowed 2026-07-27, after the first build (PR #371) proved the original spec
self-contradictory.** The registered fix said: REFUSE the append and raise, naming
the dropped fields and directing the caller to a new versioned ledger path. Codex
implemented that faithfully and it broke
`test_wo119_shadow_fills_tolerate_legacy_narrow_header`, because WO-119
deliberately made `shadow_fills.csv` **tolerate** a legacy narrow header and pinned
that behaviour with a test. `shadow_cohort.py:1103` is the only caller, so "refuse"
contradicts WO-119 by construction. The spec defect is the orchestrator's, not the
build's: it named a versioned-path migration without specifying one, and a new
versioned ledger path means a new anchored glob — an owner surface and its own work
order, not a side effect of this one.

The harm actually being targeted is **silence, not tolerance**. Therefore:

Fix: KEEP the append and keep WO-119's tolerance. When the on-disk header cannot
hold a field carrying a non-empty value in the rows being appended, make the loss
**loud** — name the dropped fields in a warning and record them in the reported
result, so a lost field is visible rather than invisible. Do not raise, do not
migrate to a versioned path, and leave
`test_wo119_shadow_fills_tolerate_legacy_narrow_header` passing unmodified.
Dropping a field whose value is empty for every appended row reports nothing — that
is a no-op, not data loss, and warning on it would train operators to ignore the
warning that matters. The only caller is `shadow_cohort.py:1103`.

Registered consequence, stated so it is not lost: a genuine schema widening on an
`append_only`-enrolled ledger still needs a new versioned path. That remains
unbuilt and unregistered; this work order does not authorize it, and the warning
above is what makes the need for it observable instead of silent.

**128.4 — the shadow-ledger write lock is a caller convention, not a contract.**
Corrected 2026-07-27 after Codex review of #365 (P1) checked the tree: the earlier
claim that this path runs unlocked was WRONG. Both entry points already hold
`prediction_cycle` — `paper_cycle.py:93` around the call at `:139`, and
`longshot_bias.py:422-426` — and `runtime_lock` is NOT reentrant, so acquiring the
same name inside `update_shadow_cohort_evidence` would make BOTH callers skip every
shadow update. Do not add an inner lock.

The real defect is that the requirement is a convention no code enforces: a new
caller can invoke `update_shadow_cohort_evidence` unlocked and interleave an append
with the live loop, losing fills or breaking the shadow anchor. Fix: make the
precondition explicit and enforced by the suite rather than by memory. State it in
the function's docstring ("callers MUST hold the `prediction_cycle` runtime lock;
this function does not acquire it"), and add a structural test asserting every call
site in `src/` sits inside a `runtime_lock(..., "prediction_cycle", ...)` block, so
a future unlocked caller fails CI. Do not change either existing caller's locking.

**Fail-safe direction (S5).** 128.1: an interrupted copy leaves no snapshot at the
canonical path, so the next run creates it — never a truncated one that wedges the
lane. 128.2: an anchor-tail failure is reported nonzero and the harvest is not
repeated; a harvest failure is reported nonzero on its own row. 128.3: a field that cannot be
persisted is dropped LOUDLY — named in a warning and in the reported result — rather
than vanishing unrecorded; the append still succeeds, so anchor safety and WO-119's
tolerance both survive. 128.4: an unlocked
call site fails the test suite rather than shipping, and the existing callers'
skip-when-held behaviour is unchanged, so a concurrent scan still declines to race.

**Interleaving (S2).** 128.1 makes `performance/ledger_anchor_snapshots/**`
crash-atomic (temp + `os.replace` in the same directory). 128.4 changes no runtime behaviour:
`polymarket_shadow/shadow_positions.csv` and `polymarket_shadow/shadow_fills.csv`
stay under the callers' existing `prediction_cycle` lock, where a concurrent caller
already skips rather than races. No artifact gains or loses a field.

Tests: (1) a truncated pre-existing snapshot from an interrupted copy — simulate
by writing a short file — is detected as differing, and a run interrupted between
temp write and replace leaves NO canonical snapshot; (2) library-only sourcing
test proving a failing anchor tail stamps its own nonzero row without a second
harvest invocation, and a failing harvest still stamps nonzero; (3) appending a
row whose new field is non-empty against a legacy header still succeeds but names
the dropped field in the warning and in the reported result, while the same append
with that field empty for every row reports nothing — and
`test_wo119_shadow_fills_tolerate_legacy_narrow_header` passes unmodified; (4) a structural test
asserting every `update_shadow_cohort_evidence` call site in `src/` sits inside a
`prediction_cycle` `runtime_lock` block — include a deliberately unlocked snippet in
the test's own fixture text to prove the test can fail — plus the existing behaviour
that a caller finding the lock held skips and reports.

**Day-after check:** on the VPS one cycle after deploy,
`outputs/performance/ledger_anchor_summary.json` `status` is `ok` and
`verify-ledger-chain` returns `ok` — that is what proves every file under
`outputs/performance/ledger_anchor_snapshots/<today>/` matches the byte length and
digest recorded in THAT anchor's manifest. Do NOT compare a snapshot against the
live source (corrected after Codex review of #365, P2): snapshot-mode sources such
as `decision_policy.json` and `shadow_positions.csv` are legitimately regenerated
after anchoring, so they are expected to differ from the immutable daily copy —
that divergence is the reason snapshot mode exists. Also confirm no `.tmp` residue
in that directory; the scheduler
`status.json` shows separate `training_harvest` and anchor-tail rows with their
own exit codes; and `outputs/polymarket_shadow/shadow_fills.csv` row count is
monotonically non-decreasing across two cycles.

## WO-129 — Watchdog false health, unretried alerts, and a config-reachable evidence-gate disable — `queued` (ISSUED to Codex 2026-07-27; the `maker_carry_study.py` item is a registered evidence gate → owner merge; the rest is monitoring-only)

Five findings from the owner's 2026-07-27 audit plus one bounded-carry-forward
follow-up. Every item makes an existing check STRICTER; none loosens anything.

**Baseline correction, 2026-07-27.** The first draft of this section was written
against an unmerged local branch rather than against `main`, so three items assumed
machinery that does not exist on `main`: an ntfy sender for the watchdog, a
carry-forward on the lock-held path, and a `maker_depth_gate_enabled` setting.
Codex's review of #365 caught all three. Every item below is now specified against
`main` as it stands, and each states what must be BUILT before it can be tightened.
This is the same defect class as the register recording unmerged work as `done`:
treating a local branch as if it were the tree.

Files: `src/polymarket_predictive_engine/degraded_state_watchdog.py`,
`src/polymarket_predictive_engine/maker_carry_study.py`,
`src/polymarket_predictive_engine/runtime_lock.py`,
`scripts/check_polymarket_vps_paper.sh`,
`tests/polymarket_predictive_engine/test_degraded_state_watchdog.py`,
`tests/polymarket_predictive_engine/test_maker_carry_study.py`,
`tests/polymarket_predictive_engine/test_runtime_lock.py`,
`tests/polymarket_predictive_engine/test_vps_paper_health_script.py`.

**129.1 — a fresh 26h forward-cycle artifact can mask a dead live loop.** The
health gate accepts the forward-cycle artifact's own age as evidence the loop is
alive, but its registered ceiling is 26h — long enough for the live loop to have
been dead for a day while the gate passes. Require the HEARTBEAT within its own
ceiling whenever the loop is expected to be running; the forward-cycle fallback
applies only inside a bounded post-start window (state the window in the script).
`scripts/check_polymarket_vps_paper.sh` currently computes ages and compares
nothing, so this item must ALSO add the comparison it is tightening: enforce the
registered ceilings, reject a negative or non-numeric age as unmeasurable (which is
a failure, not a pass), and exit nonzero on breach. Use the
`PM_HEALTH_LIBRARY_ONLY=1` sourcing seam for the tests.

**129.2 — build the delivery transport, THEN make it retry.** Corrected after
Codex review of #365 (P1): on `main` there is no sender at all. `_notification`
writes a Markdown body and returns `notify: true`, and a repo-wide search finds no
consumer that pushes it anywhere — so `notify: true` has never reached the owner,
and there is no delivery outcome to retry. This item therefore has two ordered
parts:

(a) Add the sender, reusing the WO-99 `stage_ticket_eligibility` ntfy contract:
topic URL from the VPS environment only (never config, repo, chat, or telemetry), a
bounded message carrying registration ids only — no market, wallet, amount, or
artifact contents leave the host — and a recorded outcome
`{"attempted": bool, "delivered": bool, "channel_configured": bool, "error": str}`.
Delivery is gated on the state CHANGE (a new incident id), not on every cycle.

(b) Only then, retry. The incident id is appended to the ledger BEFORE the push is
attempted, so a failed send is not `new` next cycle and the alert is lost forever.
Track undelivered ids in the watchdog state and retry until a send succeeds. An id
counts as undelivered only when a send was ATTEMPTED and FAILED — with no channel
configured there is no delivery obligation and the artifact plus GitHub are the
channel, so ids must never accumulate unboundedly.

**129.3 — a wholly failed official-book collection reads healthy.**
`_evaluate_official_books` degrades only on `status == "partial"`, so `failed`,
`error`, and an artifact with no status at all pass — the worse outcome passing
the check the milder one fails. Invert to an explicit healthy allowlist
(`ok`, `disabled`, `no_portfolio`), matching the maker-study registration.

**129.4 — the DR evaluator never ages the artifact it reads.** Every predicate
reads fields the producer wrote and trusts the artifact's own `rpo.compliant`, so
once the producer stops running the last `ok` stays healthy forever. Age the
STATUS ARTIFACT itself against a fixed ceiling (`push_vps_archive.sh` restamps it
every 30 minutes, so 6 hours is generous and still catches a dead producer), and
treat an artifact with no parseable `generated_at_utc` as an incident, never as
fresh.

**129.5 — clamp the depth-gate thresholds config can actually reach.** The
audit's critique — that the WO-113 depth gate is disableable from configuration —
is correct, but the mechanism named in the first draft was not: Codex's review of
#365 (P1) established that no `maker_depth_gate_enabled` setting exists anywhere,
so stripping that key would have changed nothing. The reachable disable is that
`_settings` accepts raw `maker_min_book_history_hours` and
`maker_min_book_snapshots` (registered defaults 48.0 and 100, `maker_carry_study.py:157-158`)
with no clamp, and `_measurement_eligible` (`:1517-1519`) explicitly returns True
when both are zero — so `maker_min_book_history_hours: 0` plus
`maker_min_book_snapshots: 0` turns the gate off completely from config.

Fix: clamp both to their registered minima in `_settings`, exactly as the M-B.1
siblings already are by `_mb_tighter_min`/`_mb_tighter_max` (`:1743-1762`) — config
may only RAISE either threshold. Apply the same tighten-only clamp to
`gate_min_runs_at_target` and `target_net_usd_per_day` (the RT-3 finding from the
2026-07-26 sweep, same defect, same file, never fixed). Then delete the
both-zero-disables-the-gate branch: with clamped thresholds it is unreachable, and
leaving it in place documents a disable that must not exist. This is the one item
touching a registered gate; it strictly tightens, and it changes no threshold
VALUE — only what configuration is permitted to do to one.

**129.6 — preserve the lock-held evaluation, THEN bound it, and fix the
corrupt-lock wedge.** Corrected after Codex review of #365 (P1): on `main` the
lock-held path does not carry anything forward — it OVERWRITES the published
artifact with empty `evaluations`, `active_incidents`, and `new_incidents` at exit
0, publishing "no incidents" over a live incident set. Ordered parts:

(a) A skipped cycle observes nothing, so it must claim nothing: carry the previous
evaluation and its episode state forward verbatim, label it stale with
`carried_forward_from_utc` and a reason, and never publish empty-on-skip.

(b) Bound that carry-forward, because unbounded it converts a wedged lock into a
permanently reassuring watchdog republishing the same evaluation with a fresh
timestamp. After 3 consecutive carried cycles emit a
`degraded_state_watchdog_wedged` incident, and clear the episode key on the first
successfully observed cycle so a later wedge starts its own episode.

(c) In `runtime_lock.py` a corrupt/unparseable lock payload reads as `{}` → no
`acquired_at` → never stale → never reclaimable. Treat an unparseable payload as
stale once the timeout has elapsed since the lock file's mtime.

**Fail-safe direction (S5).** For every item: a missing, stale, unparseable, or
absent input produces an INCIDENT or a refusal, never a healthy verdict. A
watchdog that cannot observe says so; it never reports the last thing it saw as
current.

**Interleaving (S2).** `ops_scheduler/degraded_state_watchdog.json`,
`ops_scheduler/degraded_state_watchdog_state.json`, and the append-only
`performance/degraded_state_incidents.csv` (the module's `OUTPUT_FILE`,
`STATE_FILE`, and `INCIDENT_LEDGER`) keep their existing atomic writers and
single-writer-under-lock discipline; the new state keys
(`undelivered_incident_ids`, `carry_forward_cycles`, `carry_forward_started_at`)
are written by the same writer in the same atomic payload. Clear
`carry_forward_started_at` on the first successfully observed cycle so a later
wedge episode starts its own episode key.

Tests: one per item, each proving the OLD behaviour fails — a `failed`
official-book artifact is an incident; a frozen DR artifact past the ceiling is an
incident while a fresh one is not; a stubbed sender that fails leaves the id
undelivered and the next cycle retries it, a succeeding sender clears it, and no
configured channel leaves the list empty and reports `channel_configured: false`
rather than faking a send; a 26h-fresh forward cycle with a dead heartbeat fails the
health gate, and a negative or non-numeric age fails rather than passing; a config
setting `maker_min_book_history_hours: 0` and `maker_min_book_snapshots: 0` leaves
the registered 48h/100-snapshot requirement in force, and a config RAISING either is
honoured; a lock-held cycle republishes the previous incident set rather than an
empty one, and the 4th consecutive one adds the wedge incident; a corrupt lock
payload is reclaimed after the timeout.

**Day-after check:** on the VPS one cycle after deploy,
`outputs/ops_scheduler/degraded_state_watchdog.json` lists evaluations for
`official_book_snapshot_partial` with `healthy_reachable_states` present,
`disaster_recovery_not_recoverable` with a non-null
`status_artifact_age_seconds` below its ceiling, `carry_forward_cycles: 0`, and
`notification.undelivered_incident_ids: []`; `maker_carry_study.json` reports the depth
thresholds in force as 48.0 hours and 100 snapshots regardless of what the deployed
config carries; and `sh scripts/check_polymarket_vps_paper.sh` exits 0 with the
heartbeat age printed AND enforced against its ceiling.

## WO-130 — Funding-evidence and kill-lane integrity — `queued` (ISSUED to Codex 2026-07-27; frozen/registered surfaces throughout → owner merge)

The kill lane is the control that stops a live test. Each item below is a way it
can read "clear" without evidence. Also carries the #355 P1s that remain
untouched.

Files: `src/polymarket_predictive_engine/live_test_decision_policy.py`,
`src/polymarket_predictive_engine/maker_live_test.py`,
`src/polymarket_predictive_engine/executor_ops_monitor.py`,
`src/polymarket_predictive_engine/sharp_linking_evaluator.py`,
`src/polymarket_predictive_engine/superbru_*.py` (required-submission path only),
plus their existing test modules.

1. **Kill criteria select a different row than the freshness check.** The
   freshness guard qualifies one observation while the criteria evaluate another,
   so a stale row can be judged fresh. Bind both to the same selected row and
   assert that binding in a test.
2. **Boolean and synthetic-zero kill scores pass as observations.** A
   `net_score_usd` of `0.0` produced by an artifact with no kill inputs, or a
   boolean coerced to a number, satisfies "observed".
   Corrected after Codex review of #365 (P1): requiring "a finite score from an
   `ok` producer" is NOT sufficient, because `_wallet_score`
   (`maker_live_test.py:205-224`) returns exactly that — `status: "ok"` with a
   finite `net_score_usd: 0.0` — when rewards, trades, and positions are all
   empty. That synthetic zero is the defect, and it would survive the fix.
   Require positive evidence that at least one kill-score INPUT was observed
   (a reward row, a trade, or a position — count them and record the counts on
   the artifact), and bind that provenance to the same selected row item 1
   pins. A score computed from zero inputs is `no_data`, never `0.0`, and
   `no_data` is NOT clear under an active live stage.
3. **An unobserved kill scoreboard does not alert when an executor exists.** If
   an executor is present and the scoreboard is unobserved, that is an incident,
   not silence.
4. **#355 P1s:** the circular "independent" paper-quote proof (a proof that reads
   the artifact it is proving); fabricated current timestamps (`datetime.now()`
   fallbacks standing in for observation time); non-finite intent/risk values
   flowing into sizing; malformed cohort chronology accepted; unknown-time
   observations promoted to current evidence; SuperBru required-submission
   succeeding with NO queued fixture.

**Fail-safe direction (S5).** Every path: absent, stale, non-finite, or
unprovable input yields `no_data`/`unknown` and, under an active live stage,
forces STOP. No path may map an empty or unprovable input to a permissive
verdict. Nothing here loosens a gate; every change makes a permissive outcome
harder to reach.

**Interleaving (S2).** These are read-path evaluators; the only writes are the
existing atomic status artifacts, which gain explicit `no_data`/`unknown` states.
Consumers that currently branch on `clear` must be updated to treat anything
other than an explicit measured-clear as not-clear — enumerate every such
consumer in the PR.

Tests: adversarial fixtures per item, each asserting the permissive verdict is
now unreachable, plus one test per #355 P1.

**Day-after check:** on the VPS one cycle after deploy,
`outputs/maker_carry/decision_policy.json` shows the kill block naming the exact
observation row it evaluated, with `kill_inputs_observed` true only when that row
carries a finite score from an `ok` producer; a synthetic
`maker_live_test.json` in `disabled` state (staged in a scratch output root, never
on the live tree) yields `no_data` and STOP rather than fresh/clear.

## WO-131 — Restore the candidate-seeding budget and make the seasoning runway visible — `queued` (ISSUED to Codex 2026-07-27; collection-only, non-frozen → orchestrator merge after line-audit)

**Campaign-critical.** M-A is 5/7 distinct days with the 2026-08-19 terminal date
23 days out, and the 2026-07-27T00:08 run shows `latest_run_at_target: false`
with `portfolio_markets: 0`. Measured cause from `official_book_snapshot.json`
(10:59Z): seeding works (42 files written) but of 50 polled markets **7 return
HTTP 404** — the candidate CSV retains delisted tokens, so ~14% of the seeding
budget buys corpses, and non-finite carry values consume further slots. This WO
changes NO gate, threshold, or eligibility rule; it makes collection spend its
budget on live markets and makes the runway measurable instead of inferred.

Files: `src/polymarket_predictive_engine/maker_fill_replay.py`,
`src/polymarket_predictive_engine/maker_carry_study.py` (candidate ranking only —
not the gate),
`tests/polymarket_predictive_engine/test_maker_fill_replay.py`,
`tests/polymarket_predictive_engine/test_maker_carry_study.py`.

1. **Persist a delisted marker per token and skip it when seeding.** The
   per-token fetch at `maker_fill_replay.py:580-589` records
   `fetch_errors[token_id] = "HTTPError: 404 ..."`. Record a durable marker
   (new append-only or snapshot artifact under `maker_carry/`, your choice —
   state the enrollment decision and mode in the PR; a NEW anchored glob needs
   the owner, so prefer an UNENROLLED artifact for this WO) keyed by `token_id`
   with the first and last 404 timestamp and a consecutive-404 count.
   `_candidate_seed_markets` skips a token whose consecutive-404 count is at or
   above a registered threshold (default 3, config may only RAISE the bar for
   skipping — i.e. config can make the system poll more, never blind it).
   **A skip must be a cooldown, not a blacklist.** Corrected after Codex review
   of #365 (P2): a newly seeded token that 404s three times has no official-book
   file, so it never enters the mtime persistent tranche either — once skipped it
   would never be requested again and could never clear its marker, making a
   transient outage or a later re-listing a permanent exclusion. Give the marker
   a registered TTL (default 24h since the last 404) after which the token is
   re-probed exactly once per cycle; a valid book clears the marker outright, and
   another 404 restarts the cooldown. State the TTL in the artifact so an
   operator can see when each token is next due.
   The marker artifact carries `paper_trading_invoked=false` and
   `live_trading_invoked=false` (per AGENTS.md), asserted in its tests.
2. **Drop non-finite carry rows before ranking.** In `_candidate_seed_markets`
   (`maker_fill_replay.py:400-421`) a NaN `net_carry_usd_per_day` participates in
   the sort with undefined ordering and can occupy a seeding slot ahead of a real
   candidate. Exclude non-finite carry and non-finite `yield_rank` from ranking
   (they rank last today only by accident of `-(-inf)`); make it explicit and
   report the excluded count.
3. **Report the seasoning runway.** Add to the `official_book_snapshot.json`
   summary, per seeded market: `book_history_hours` and `book_snapshot_count` as
   the eligibility rule measures them (read the same helper the rule uses — do
   not re-derive), plus a `closest_to_eligibility` block naming the top 3
   markets by remaining hours/snapshots. This makes the 48h / 100-snapshot runway
   measurable, which is the difference between knowing the campaign will bank a
   day and hoping.

**Fail-safe direction (S5).** A missing or unparseable delisted-marker artifact
means NO token is skipped (poll everything — the conservative direction for a
collector is to collect); a non-finite carry value is excluded from ranking rather
than ordered arbitrarily; a market whose history cannot be measured reports
`null` runway fields and is never reported as closer to eligibility than a
measured one. No eligibility rule, gate, threshold, or sizing path reads any of
it.

**Interleaving (S2).** New artifact under `maker_carry/` written atomically by
`snapshot_official_books` only, which runs on the single 15-minute maker lane; the
existing `maker_carry/official_books/*.csv.gz` write path is unchanged.
`official_book_snapshot.json` gains fields only — no existing field changes
meaning, so the WO-121 `official_book_snapshot_partial` evaluator and the
dashboard are unaffected.

Tests: (1) a token returning 404 three times is skipped on the fourth cycle while
a token 404-ing twice is still polled; (2) a skipped token that returns a valid
book is polled again and its marker clears; (3) a NaN carry candidate never
displaces a finite-carry candidate and is counted as excluded; (4) the runway
block reports the same hours/snapshots the eligibility helper computes for the
same fixture; (5) a missing marker artifact polls every candidate.

**Day-after check:** on the VPS one cycle after deploy,
`outputs/maker_carry/official_book_snapshot.json` shows `status: ok`,
`markets_polled` equal to the watchlist size with the 404 markets no longer in it,
`candidate_seed_markets` greater than before, a `closest_to_eligibility` block with
non-null `book_history_hours`, and the delisted-marker artifact listing the known
dead tokens. Within 48 hours of that, `maker_carry_study.json`
`portfolio_markets` must exceed 0 — if it does not, the seeding fix was not the
binding constraint and the finding is re-opened.

## WO-132 — Correct the work-order register and make unresolved review threads block merge — `queued` (ISSUED to Codex 2026-07-27; governance documents → owner merge, and the orchestrator must not self-merge it)

Files: `docs/POLYMARKET_CODEX_WORK_ORDERS.md`, `AGENTS.md`.

1. **Every register entry that names a PR or merge which did not happen must be
   corrected.** The WO-121/122/124/125/126 entries were written asserting
   `(2026-07-27, PR #362, owner merge)` while drafting against a branch expected
   to land there; #362 merged with **WO-123 only**. Those entries revert to
   `queued`/`in-review` with NO PR attribution and NO merge claim until their own
   PRs merge. No entry may assert an owner action. This is the highest-priority
   item in this WO: an agent-written authorization claim is prohibited outright,
   and an automated queue driver reading a register that records unmerged work as
   `done` would compound it.
2. **Add a merge precondition to `AGENTS.md`:** unresolved inline review threads
   block merge. As of 2026-07-27 there are 49 unresolved threads across #356–#363
   (34 of them on already-merged PRs) while every one of those PRs was green — so
   the required gate proves the tests passed, not that review was answered.
3. **Triage the 34 threads on merged PRs** into the WOs above or into explicit
   dismissals with a stated reason. A thread may be resolved only when it is
   fixed on current `main` or demonstrably superseded; "no longer reachable in
   the diff" is not a reason.

**Fail-safe direction (S5).** When a thread's status cannot be established it
counts as UNRESOLVED and blocks merge. When a register entry's merge state cannot
be established from GitHub it reverts to `queued`, never to `done`.

**Day-after check:** `docs/POLYMARKET_CODEX_WORK_ORDERS.md` contains no entry
naming a PR number or an owner merge that `gh`/the GitHub API cannot confirm — a
reviewer can check every `done` claim against the merge commit it names — and
`AGENTS.md` states the unresolved-thread merge precondition in its
work-order/Git discipline section.

## WO-133 — Legitimise the manual VPS deploy path — `queued` (ISSUED to Codex 2026-07-27; **the whole PR routes to owner merge**, because it contains an `AGENTS.md` amendment and a PR cannot be partially merged)

A 2026-07-27 owner request to legitimise the manual deploy path is recorded here
for owner review and becomes repository authorization only through the owner's
merge of this registration and of the `AGENTS.md` amendment below — not through
this text. Today the
runbook's manual route bypasses the workflow's guard order, and the workflow is
unavailable when Actions is down — so the honest options are a guarded script or
an undocumented ad-hoc `git pull`. Chosen model: **guarded script, attestation
recorded as unverified.**

Files: new `scripts/deploy_vps_paper_manual.sh`, `AGENTS.md`,
`docs/VPS_PAPER_RUNBOOK.md` (or the runbook file that currently documents the
manual route), new
`tests/polymarket_predictive_engine/test_manual_vps_deploy_script.py`.

The script mirrors the workflow's on-host guard ORDER, reusing what exists:
`preflight_vps_capacity.py`; private-transport proof via
`validate_dashboard_private_transport.py` **before** quiescing; `.env` + marker
backup at mode 0600; `docker image tag` of the running image as
`rollback-last-known-good`; `update_vps_checkout_preserving_runtime.py`;
**markers written before container recreation** (the ordering defect both the
audit and Codex caught); `--profile deploy-acceptance` with the scheduler
stopped; `check_polymarket_vps_paper.sh`; and `rollback_vps_paper_deploy.py` on
any failure past the arming boundary.

**The irreducible gap, named rather than skipped.**
`verify_independent_main_acceptance.py` needs a GitHub token and the acceptance
run's artifact, so a VPS shell cannot bind the SHA to an independent review. The
script therefore REFUSES unless the target equals a freshly fetched `origin/main`
tip, and writes `outputs/performance/vps_manual_deploy.json` recording
`attestation_verified: false` with the owner as authoriser. The unprovable step is
recorded as unproven; it is never silently treated as proven.

`AGENTS.md` amendment, dated 2026-07-27 and effective only on the owner's own
merge of it: Path A is the workflow and remains REQUIRED whenever Actions is
available; Path B is this script, for when it is not; an ad-hoc pull/rebuild
remains forbidden. The amendment must not describe itself as already authorized.

**Fail-safe direction (S5).** Any guard failure before the arming boundary aborts
with nothing changed; any failure after it triggers rollback to
`rollback-last-known-good` and stamps the failure. A target SHA that is not the
fetched `origin/main` tip, a failed transport proof, or a failed acceptance run
all refuse the deploy. The deploy record never claims a verification it did not
perform.

Tests: guard ordering (markers written before recreate; transport proof before
quiesce) asserted by parsing the script through the established library-only
sourcing seam; refusal when the target is not `origin/main`; rollback invoked on a
simulated post-arming failure; and the deploy record's honesty fields
(`attestation_verified: false`, authoriser recorded, unprovable step named).

**Day-after check:** after one manual deploy,
`outputs/performance/vps_manual_deploy.json` exists with
`attestation_verified: false`, the deployed SHA equal to the then-current
`origin/main` tip, and `guard_order` listing the guards in the executed order;
`outputs/performance/deploy_acceptance.json` is PASS for that SHA; and
`sh scripts/check_polymarket_vps_paper.sh` exits 0.

## WO-136 — The fill replay must quote contemporaneously, not replay today's sheet into last week's regime — `queued` (ISSUED to Codex 2026-07-28 under the owner's direct instruction of 2026-07-28; reporting-only replay surface, non-frozen → orchestrator merge after line-audit)

**Why now.** The first post-WO-131 production replay (2026-07-28T12:53Z,
`maker_fill_replay.json`) reported `simulation_to_reality_haircut: 42.07` —
realized adverse $56.27/day against a study charge of $1.34/day. Diagnosed by
line-reading `_replay_against_states`: the number is arithmetic on phantom fills.
The replay takes the CURRENT quote sheet's absolute prices (`quote_bid_price`
0.32 / `quote_ask_price` 0.38, derived from the 2026-07-28 mid of 0.35) and
replays them statically against up to `replay_days` of historical prints
(`maker_fill_replay.py:1171-1176`). The dominant fill burst is stamped
2026-07-23, when the contemporaneous mid was ~0.44 (fill 0.38 + measured 0.06
markout): every ordinary BUY print in that regime reads as an adverse fill
against a 0.38 ask no real maker would have been resting — the study re-derives
quotes from mid daily and the WO-99 requote lane pulls them on drift. The
cross-check: the replay implies 31.25 fills/day; `maker_live_test` models 0.01;
the wallet has observed 0. The `reported_only` / `tighten_only` haircut policy
correctly kept the bad number away from M-B — this WO fixes the measurement so
the field can carry a real signal instead of training everyone to ignore it.

Files: `src/polymarket_predictive_engine/maker_fill_replay.py`,
`tests/polymarket_predictive_engine/test_maker_fill_replay.py`.

1. **Quote each historical fill opportunity at the contemporaneous book state.**
   In `_replay_against_states` (`:1158-1259`), for trades stamped BEFORE the
   quote sheet's `generated_at_utc`, derive the simulated quote from the book
   state already fetched for queue depth: `bid = state.midpoint -
   quote_distance`, `ask = state.midpoint + quote_distance` (the code's own
   existing fallback branch at `:1173-1175`, promoted from "sheet prices
   missing" to "sheet prices not yet in force"). Round to the entry's
   `order_price_min_tick_size` outward (bid down, ask up) when present, raw
   otherwise, and state which was used. Trades stamped AT/AFTER the sheet's
   `generated_at_utc` keep the sheet's absolute prices — there the sheet IS the
   live quote.
2. **No state, no quote.** An opportunity whose contemporaneous state is absent
   or older than `max_state_lag_seconds` is excluded from fills and counted in a
   new `no_contemporaneous_state_opportunities` field. It must never fall back
   to the current sheet's absolute prices for a historical print — quoting from
   the future is the defect this WO removes.
3. **Name the basis and keep one release of comparability.** The summary gains
   `quoting_basis: "contemporaneous"`, and the old computation is retained for
   exactly one release as an explicitly-named audit block
   (`static_sheet_realism_ratio`, `static_sheet_fills_per_day`) so the size of
   the correction is on the record — the `share_model_legacy` precedent.

**Fail-safe direction (S5).** Reporting-only throughout: the haircut remains
`reported_only` / `tighten_only`, no gate, threshold, eligibility rule, or
sizing path reads any of it, and this WO changes none. A missing contemporaneous
state excludes the opportunity rather than synthesising a quote; if the
contemporaneous computation cannot run at all the artifact reports
`insufficient_coverage` rather than promoting the static-sheet number back to
primary.

**Interleaving (S2).** Same producer, same artifact, same cadence. Fields are
added, none change meaning; the coverage state set the WO-121
`maker_replay_insufficient_coverage` watchdog registration keys on
(`covered`/`partial`/`no_simulated_fill_opportunities`/`insufficient_coverage`)
is unchanged.

Tests: (1) the 2026-07-28 regression as a recorded-shape fixture — a 0.44-mid
regime for the historical window with a sheet quoted off a 0.35 mid: the static
basis manufactures fills, the contemporaneous basis produces none from that
regime, and the audit block still shows the static number; (2) a print that
genuinely sweeps through the contemporaneous `mid + distance` ask still fills,
with markout measured against the later contemporaneous mid; (3) trades stamped
after the sheet's `generated_at_utc` use the sheet's absolute prices, and on a
fixture whose entire window is post-sheet the two bases agree exactly; (4) an
opportunity with no book state inside `max_state_lag_seconds` is excluded and
counted, never filled; (5) the haircut policy strings (`reported_only`,
`tighten_only`, amendment-required) survive verbatim; (6) tick rounding is
outward (a contemporaneous bid never rounds up, an ask never rounds down).

**Day-after check:** on the VPS one cycle after deploy,
`outputs/maker_carry/maker_fill_replay.json` shows
`quoting_basis: "contemporaneous"`, `simulated_fills_per_day` within an order of
magnitude of `maker_live_test.modelled_fills_per_day` rather than the 31.25/day
of the static basis, `realism_ratio` either an O(1) number or an explicit
insufficient-coverage status, and the `static_sheet_realism_ratio` audit field
still recording the old basis so the correction's magnitude is auditable.

## WO-137 — Make portfolio churn visible: a per-run composition diff naming the exact reason every market left — `queued` (ISSUED to Codex 2026-07-28 under the owner's direct instruction of 2026-07-28; reporting-only, non-frozen → orchestrator merge after line-audit)

**Why now.** M-A banks a day only when the day's LAST run holds target (M-A.1),
and 27 of the 68 history runs found an empty or near-empty portfolio — the
median run is $5.90/day, above the $3.33 target, so intermittency, not level, is
what blocks the campaign. Measured example, 2026-07-28: the Iran-airspace
market carried $9.71/day on $64 at the 00:24 run and was absent at 12:51, and no
artifact can say why — `excluded_stale_examples` caps at 10 of 32 and nothing
compares consecutive runs. The variable that decides the campaign is currently
unobservable. Reporting only: no gate, threshold, eligibility rule, or sizing
path changes, and none may read the new artifact.

Files: `src/polymarket_predictive_engine/maker_carry_study.py`,
`tests/polymarket_predictive_engine/test_maker_carry_study.py`.

1. **Retain the full per-market disposition map the run already computes.** The
   staleness reasons exist per market at `_candidate_staleness_reasons` (`:560`)
   and are aggregated at `:784-876` into counts plus 10 examples; resolution
   risk is `resolution_risk == "high"` and thin book is
   `estimate_quality == "thin_book_untrusted"` on the candidate rows
   (`:2191-2192`). Keep the full map in memory for the diff — reuse the exact
   existing predicates; this WO names dispositions, it must not invent new
   classifications or re-derive any rule.
2. **Emit `maker_carry/portfolio_composition_diff.json`** — a NEW, UNENROLLED
   snapshot artifact written atomically (a new anchored glob needs the owner;
   WO-131 precedent), by `run_maker_carry_study` only, AFTER the P3-3
   `maker_carry_ledger_commit` flock section — never inside it. Content:
   `previous_run_at` / `current_run_at`; `entered[]`, `departed[]`, `held[]` by
   `condition_id`; and for every departed market its disposition THIS run, one
   of: `excluded_stale:<reason,...>` (the WO-80 reasons verbatim),
   `excluded_resolution_risk`, `excluded_thin_book`, `measured_not_sized` (with
   this run's `net_carry_usd_per_day`), `not_in_candidate_scan`, or
   `not_in_rewarded_universe`. Previous-run membership comes from the LATEST
   prior row of the existing WO-111 sidecar
   (`maker_carry/maker_carry_portfolio_members.csv`, parsed the way
   `_incumbent_hold` (`:1532`) already parses it) — the sidecar is enrolled
   `append_only` in the WO-61 registry and is READ-ONLY to this WO: its writer,
   fields, and enrollment must not change. The sidecar records only
   `condition_id` and `markout_measured`, so "then" fields are limited to
   exactly those — do not reconstruct historical carry or capital from
   anywhere else.
3. **Surface the one-line summary.** `maker_carry_study.json` gains
   `portfolio_entered`, `portfolio_departed`, `departed_reasons` (a
   reason→count map) and `composition_diff_status` (`ok` / `no_prior_run` /
   `write_failed`). Fields are added only; no existing field changes meaning.

**Fail-safe direction (S5).** Reporting only, and instrumentation must never
take down the instrument's subject: a failure to compute or write the diff
artifact logs loudly, stamps `composition_diff_status: write_failed`, and leaves
the study's exit status, history commit, and every gate exactly as they would
have been. A missing or unreadable members sidecar, or a genuine first run,
reports `no_prior_run` with empty diff lists rather than guessing. A market
whose disposition cannot be established from this run's own computed maps
reports `disposition_unknown` — never silently omitted, never given a made-up
reason. Non-finite carry renders as `null` and never sorts.

**Interleaving (S2).** Same producer, same lane, single writer. The new artifact
is unenrolled and written outside the P3-3 flock, after both anchored ledgers
commit, so an interrupted diff write can never orphan or shadow a ledger row.
`maker_carry_study.json` gains fields only; the WO-121/WO-129 watchdog
registrations key on `status` values and are unaffected.

Tests: (1) a market present in the previous sidecar row and absent from the
current portfolio appears in `departed[]` with the exact staleness reason the
current run's predicate produced (fixture flips `venue_close_time_past`); (2) a
newly sized market appears in `entered[]` and `held[]` is disjoint from both;
(3) first run / missing sidecar → `no_prior_run`, empty lists, study status and
gates unchanged; (4) a forced diff-write failure (unwritable path) leaves the
study exit status and history row untouched and stamps
`composition_diff_status: write_failed`; (5) a departed market absent from this
run's rewarded universe reports `not_in_rewarded_universe`, and one measured
but unsized reports `measured_not_sized` with a finite carry value; (6) the new
artifact carries `paper_trading_invoked=false` and `live_trading_invoked=false`,
and the members sidecar's bytes are identical before and after the run in every
diff fixture (read-only proof).

**Day-after check:** on the VPS after two consecutive study runs,
`outputs/maker_carry/portfolio_composition_diff.json` exists with
`previous_run_at`/`current_run_at` matching the last two history rows, and every
market that left the portfolio between them carries a named disposition. The
next intermittency event (portfolio 1 → 0 between runs) must be explainable
from this artifact alone, without shell archaeology — that is the acceptance
bar, because it is the exact question the 2026-07-28 Iran-airspace departure
could not answer.

## Current queue for Codex — ISSUED 2026-07-27

A 2026-07-27 owner instruction — **Claude orchestrates and reviews; Codex
executes** — is recorded here for owner review. It is what satisfies the dispatch
bridge's "operates only under the owner's direct instruction" condition for
issuing this queue, and it is **not** authorization for any frozen-surface merge:
that continues to exist only at the owner's own merge of the change itself. This
entry does not become repository authorization for anything except through the
owner's merge of it.

**Why these six registrations are one PR, while their builds are six PRs.** Codex's
review of #365 read the one-work-order-per-branch-and-PR rule as also governing this
registration. Recorded here rather than silently overridden, with the reasoning, and
the owner decides at the merge: the rule exists so an implementation change stays
independently reviewable and revertible, and every BUILD below is a separate branch
and PR with that property intact. This document change is a single governance act —
"issue this queue, in this order, with this routing" — whose parts are only
meaningful together: the routing table, the build order, and the review-thread rule
cannot be reviewed or reverted per-WO without leaving the queue self-contradictory
between merges. Prior practice matches (the 2026-07-18 ACTIVE BATCH and the
2026-07-19 queue were each registered as one change). If the owner prefers six
registration PRs, say so on #365 and I will split it before anything is built.

Build order — each item is ONE branch and ONE PR, no combining, no drive-by
refactors:

| # | WO | Scope | Merge routes to |
|---|----|-------|-----------------|
| 1 | **WO-131** | candidate-seeding budget + seasoning visibility (campaign-critical, 23 days to the M-A terminal date) | orchestrator, after line-audit |
| 2 | **WO-128** | atomic snapshots, non-destructive anchor tail, no silent field loss, shadow-write serialisation | owner (the `ledger_anchor.py` writer) |
| 3 | **WO-121** | watchdog coverage for the currently unmonitored producers (blocking prerequisite for WO-129 — see below) | orchestrator, after line-audit |
| 4 | **WO-129** | watchdog false health, the ntfy sender + retry, depth-threshold clamps, carry-forward preservation and bound | owner (registered evidence gate) |
| 5 | **WO-132** | register correction + unresolved-threads merge precondition | owner (governance documents) |
| 6 | **WO-130** | kill-lane and funding-evidence integrity + the #355 P1s | owner (frozen surfaces) |
| 7 | **WO-133** | guarded manual deploy path + its `AGENTS.md` amendment | **owner** (one PR carrying a governance amendment cannot be partially merged) |
| 8 | **WO-136** | contemporaneous-quote fill replay (kills the phantom-fill 42x haircut; issued 2026-07-28 under the owner's direct instruction) | orchestrator, after line-audit |
| 9 | **WO-137** | portfolio composition diff — name the reason every market leaves (churn is the campaign's binding variable; issued 2026-07-28 under the owner's direct instruction) | orchestrator, after line-audit |
| 10 | **WO-141** | a failed refresh must not erase collected price history: the collector preserves prior corpus rows for requested-but-failed tokens (rescoped 2026-07-30 — the model-abstention and readiness items were withdrawn after Codex review disproved the inference chain) | owner (training-data integrity) |
| 11 | **WO-139** | spend the official-book seeding budget on markets that can actually be sized — `done` (2026-07-31, PR #397; awaiting the next VPS deploy for its day-after check) | owner (study module) |
| 12 | **WO-142** | wire the volatility columns into prediction rows so the declared volatility penalty actually applies (tighten-only; edges may only shrink) | owner (edge_field_for_trading producer) |

**WO-121 — watchdog coverage for unmonitored producers** (registered 2026-07-27 as
WO-129's named blocking prerequisite, per ENGINEERING_STANDARDS S3). WO-129 tightens
watchdog behaviour that must exist first. Scope, all in
`degraded_state_watchdog.py`, `operating_state.py`, `push_vps_anchor.sh`,
`push_vps_telemetry.sh` and their tests: scheduler freshness ceilings for the two
uncovered jobs (`ledger_anchor` 26h and `maker_safety_refresh` 1h — the 15-minute
safety lane that owns the decision-policy, requote, and kill artifacts, so if it
stops being scheduled five safety artifacts freeze and nothing notices); new
registrations for broken chain verification, disaster-recovery status, failed maker
study runs, and partial official-book collection; consumption of the
`operating_state` `slo` block, which nothing reads today, so an SLO breach becomes an
incident instead of a dashboard row; fail-closed defaults where a missing
`last_exit_code` currently reads as 0 and a missing artifact currently reads UNKNOWN
rather than BREACH; and a status artifact for both publication-bridge push scripts,
which today cannot fail and whose age nothing measures. Fail-safe direction: every
new registration treats absent, stale, or unparseable input as an incident, never as
health. Day-after check: `outputs/ops_scheduler/degraded_state_watchdog.json` lists
every new registration with a non-null observation token or an explicit
`unobserved`, `registered_job_maximum_seconds` covers all 11 scheduler jobs, and both
push scripts have written a status artifact whose age is surfaced in
`operating_state.json`. An implementation of this scope was drafted locally by the
orchestrator and is NOT merged; treat this registered text as authoritative and
build against `main`.

WO-127 (restore-chain recoverability) is in review as PR #364 and stays with the
orchestrator through owner merge; it is a prerequisite for deploying `main`, which
currently carries the restore→wedge defect.

**Review-thread rule (binding on every WO above).** A WO PR is not mergeable
while any Codex review thread on it is unresolved. The reviewing step must read
every thread and either resolve it with a reply that says why it does not apply,
or land a fix — before merge, never after. Green CI proves the tests passed, not
that review was answered: as of 2026-07-27, 49 threads were unresolved across
#356–#363 while every one of those PRs was green. This is the automation-side
twin of WO-132's `AGENTS.md` precondition.

**Dispatch template.** Every dispatch begins with the literal line
`[orchestrator-dispatch] Posted by the orchestrator (Claude), not the owner.`
then states: WO id and title; the scope sentence; a link to the registered
section in this file plus the full spec inline; the fail-safe sentence; the
enumerated tests; the `Day-after check:`; and the merge routing (non-frozen →
orchestrator after line-audit, frozen/registered → owner). A dispatch assigns
work and is never authorization.

Every dispatch also carries, per WO-135 (effective on the owner's merge of that
amendment): **run the offline `pytest` suite in your sandbox before pushing and
report the exact result — pass counts, or the failing tests. "Not run" is a spec
violation, not a status.** An agent that genuinely cannot run it must say so and
name what blocked it. This does not change what verifies a change: the self-hosted
ARM64 required PR gate remains the sole verification of record, and a green sandbox
run neither substitutes for it nor licenses a merge.

And: **push the branch to `origin` and open the pull request.** A commit that
exists only in a task sandbox is not a delivery. If pushing is outside what the
task can do, say so explicitly rather than reporting created "pull-request
metadata" — on 2026-07-20 (#299) and 2026-07-27 (#368, #369) that phrasing
accompanied work that never reached the remote, and a silent non-delivery is the
one outcome the queue cannot act on.

**Owner-provisioned recurrence.** Recurring cycles remain the owner's to
provision (claude.ai/code → environment → triggers), per the queue-driver section
above. The orchestrator does not self-provision recurrence. `OPS_OWNER_NTFY_TOPIC_URL`
carries queue-driver phone pushes; custody is unchanged — never in the repo,
config, chat, or telemetry.

## WO-135 — Let a sandboxed agent run the offline suite, and require it — `in-review` (2026-07-27; `AGENTS.md` amendment → owner merge)

**Cause, measured rather than assumed.** Every WO delivered through the dispatch
bridge on 2026-07-27 arrived with the test results marked "not run locally". Codex
stated the reason itself: *"repository policy requires Polymarket runtime
verification in an isolated VPS container or through the self-hosted ARM64 PR
gate."* That is `AGENTS.md` line 13 — "Do not start any of the following on the
local workstation: Python engines, collectors, model training, **test suites**,
brokers, or watchdogs" — plus line 18, which offers only two sanctioned venues,
neither of which an agent sandbox is. The agent was not cutting corners. It was
complying, and the rule was costing us the cheapest defect filter we have.

**Why the carve-out is safe.** The prohibition exists to keep runtime work and
production artifacts on the VPS: nothing outside production may write ledgers, call
a live venue, or produce evidence that could be mistaken for the real thing. The
`pytest` suite does none of that — it drives temporary `paths.output_root`
directories, recorded fixtures and stubbed HTTP throughout. So the ban on running
it is broader than the purpose it serves, and narrowing it removes no protection.

**What changed** (`AGENTS.md`, new dated amendment under the VPS-only rule):
- An automated agent in an **ephemeral, network-isolated sandbox** MUST run the
  offline suite before pushing and MUST state the result in the PR.
- Everything with runtime side effects stays prohibited outside the VPS with no
  exception, and the list is restated so the carve-out cannot be read broadly:
  engines, collectors, model training, brokers, watchdogs, schedulers, dashboards,
  Docker/Compose, any run against a real `paths.output_root`, and anything
  contacting a live venue, wallet, or paid API.
- **The self-hosted ARM64 required PR gate remains the sole verification of
  record.** A green sandbox run is not verification, does not license a merge, and
  does not reduce the line-audit. It exists to stop an agent spending a review
  cycle on breakage it could have seen itself.
- The owner's workstation is unchanged: still no test suites there.

**Dispatch template amendment.** Every dispatch now states: *run the offline suite
in your sandbox before pushing and report the exact result — pass counts, or the
failing tests. "Not run" is a spec violation, not a status.* An agent that
genuinely cannot run it must say so explicitly and name what blocked it.

**Fail-safe direction (S5).** If the sandbox cannot run the suite, the agent
reports that plainly and the PR is treated as unverified — never as passing. A
sandbox result is advisory in only one direction: a failure blocks the push, a pass
proves nothing the required gate has not confirmed.

**Interleaving (S2).** Documentation only. No artifact, writer, cadence, gate,
threshold, sizing rule, or order path changes.

Tests: `tests/test_vps_only_operating_docs.py` gains assertions that the amendment
is present, that the runtime prohibition survives it verbatim, and that the
required-gate-is-sole-authority sentence is intact — so a later edit cannot quietly
widen the carve-out into "agents may run production paths".

**Day-after check:** the next dispatched work order's PR states a real suite result
(counts, or named failures) instead of "not run locally"; `AGENTS.md` still contains
"Do not start any of the following on the local workstation"; and the required PR
gate still runs the full unfiltered suite on that PR and remains what the merge
decision cites.

## WO-139 — Spend the official-book seeding budget on markets that can actually be sized — `done` (2026-07-31, PR #397; built by Codex with a six-item fix round after line-audit — three-tier seed ordering per the registered spec, sizer-matching risk predicate, clock-advance window test, mtime tie-break, stat-failure test, strengthened tests; day-after check pending the next VPS deploy, which as of this status line has not yet occurred — the deployed revision predates this merge)

**Why now.** M-A banks a day only if the day's LAST run holds target, and M-B.1
requires the SAME market present across two consecutive cycles; both are starved
by a portfolio that oscillates between 0 and 1 markets. Measured 2026-07-29 from
live telemetry and a code trace: the book-seeding watchlist is 51 slots
(portfolio 1 + persistent 25 + seeds 25), and 26 of the 29 watchlist slots that
overlapped that day's candidate set were on markets that can never be sized —
for two independent and unrelated reasons.

**Scope (collection/reporting only — no gate, threshold, or eligibility change).**
Files: `src/polymarket_predictive_engine/maker_fill_replay.py`,
`src/polymarket_predictive_engine/maker_carry_study.py` (CSV columns only), tests.

139.1 — `_candidate_seed_markets` (`maker_fill_replay.py:511-529`) ranks seeds by
raw `net_carry_usd_per_day` alone, reading none of the sizer's other predicates,
so that day's top two seed slots went to markets with `estimated_reward_share`
0.978 and 1.000 — permanently `thin_book_untrusted` at the sizer
(`maker_carry_study.py:1696`, `max_trusted_reward_share` 0.05). Replace the flat
ranking with **three explicit tiers**, each ordered by carry desc:

  * **Tier 1 — sizeable now.** Clears every non-depth sizer predicate exactly as
    the sizer states them: `net_carry > 0`, `estimate_quality ==
    "book_and_history"` (the sizer accepts nothing else, `:1696`),
    `band_eligible is True`, `resolution_risk != "high"`. Seeding keeps these
    warm so they stay depth-eligible.
  * **Tier 2 — one window short.** Identical except `estimate_quality ==
    "single_window_history"`. These are NOT sizeable today, and the tier exists
    precisely because more history is what promotes them; registering the tier
    separately keeps them from displacing Tier 1, which the flat "in {both}"
    formulation would have allowed.
  * **Tier 3 — fallback.** The existing raw-carry order, filling only slots that
    Tiers 1 and 2 leave free.

  This REALLOCATES slots: the number of markets polled never falls, and a market
  that would be polled today when slots are free is still polled.

139.2 — `_recent_book_markets` (`:436-475`) uses file mtime only as a filter and
then iterates `sorted(books_dir.glob("*.csv.gz"))` — lexicographic hex
`condition_id` — after which `snapshot_official_books` truncates with
`persistent[:persistent_cap]` (`:700`). The 25 persistent slots were verifiably
an unbroken hex prefix (`0x003a…`-`0x2d1492…`), so ~82% of the address space can
never hold a persistent slot; the docstring at `:449-450` already claims mtime
ordering. Sort by mtime descending before truncation; a `stat` failure sorts
last rather than raising.

139.3 — `book_history_hours` and `book_snapshot_count` are computed at
`maker_carry_study.py:2180-2184` but omitted from `CANDIDATE_FIELDS`
(`:328-387`), so `maker_carry_candidates.csv` cannot show why a candidate is
depth-ineligible. Add both columns. Reporting-only: no gate reads that CSV.

**Fail-safe direction (S5).** Seeding and archiving are collection-only. Nothing
here may mark a market measured, alter `_measurement_eligible`, change any
`maker_min_book_*` / `max_trusted_reward_share` / M-A / M-B / M-C threshold, or
change which candidates the sizer accepts. The module's existing contract holds:
configuration may make the system poll MORE, never blind it
(`maker_fill_replay.py:88-90`). If a reordering input is missing or malformed,
fall back to current behaviour rather than dropping a market from the watchlist.

**Explicitly out of scope, recorded so it is not attempted.** Two adjacent
"fixes" surfaced in the same diagnosis and are refused here: (a) lowering
`max_trusted_reward_share` or `MB_TIER0_MIN_CONFIRMED_FILLS` to make M-B.1
reachable — both are registered gates, and the tension between them (we may only
quote deep books, where we are never filled) is a real finding for the owner,
not a threshold to move; (b) adding a new objective LOW class to
`_base_resolution_class` to widen the universe — the WO-51 screen may escalate
LOW to MEDIUM and never the reverse.

**Tests.** (1) with top raw-carry entries thin-book/high-risk and lower-carry
entries sizeable, the seed tranche polls the sizeable ones first and the total
polled count is unchanged; (2) when fewer than the cap qualify, remaining slots
are still filled from the raw-carry order — no shrinkage; (3) malformed or
missing eligibility fields fall back to current ordering without dropping
markets; (4) the persistent tranche returns the most recently written archives,
proven with a fixture whose mtime order is the REVERSE of its lexicographic
`condition_id` order — the pre-fix code must fail this test; (4b) the
wall-clock eligibility window still holds across the refactor: with one archive
held at a fixed mtime, advancing the evaluation clock beyond `regime_days` drops
it from the persistent tranche, and inside the window keeps it; (5)
`maker_carry_candidates.csv` carries both depth columns; (6) an existing
sizer/eligibility test passes unchanged, proving no gate moved.

**Day-after check:** in `official_book_snapshot.json`, the seeded watchlist's
LEADING entries are the Tier-1 candidates in carry order, followed by Tier 2,
with Tier-3 fallback entries only after both are exhausted — i.e. the check is on
the priority ORDERING and the qualified prefix, never on the whole list being
qualified, because the registered fallback may legitimately reintroduce
thin-book or high-risk markets when fewer than the cap qualify. The polled count
is unchanged from the prior run's cap. `maker_carry_candidates.csv` shows
`book_history_hours` and `book_snapshot_count` for every row.


## WO-141 — A failed refresh must not erase collected price history — `queued` (registered 2026-07-29; RESCOPED 2026-07-30 after Codex review of the registration disproved the original harm chain; collection-only → owner merge after line-audit)

**Provenance and epitaph — read this before the scope.** As first registered,
this work order claimed a starved `historical_price_snapshots.csv` flows through
`features_v2` into `predict_optimized_probability`'s zero-fill-then-standardise
path and out to the paper cycle's `on_pace` status, and prescribed three fixes
(collector refusal, model abstention, readiness blocker). Codex review of the
registration PR (#398) disproved the chain on three grounds, each verified
against the code before this rewrite:

1. **The deployed paper lane never reads the corpus at inference.**
   `docker-compose.vps-paper.yml:94` passes `--paper-source websocket`, and
   `_source_files` (`features_v2.py:119-140`) includes
   `historical_price_snapshots.csv` only for `all`/`historical`. The corpus is a
   TRAINING input. The registered readiness blocker (old 141.3) would have
   blocked the paper lane on an unrelated collector outage — withdrawn.
2. **Zero-imputation is the model's train-time convention, not an
   inference-side fail-open.** `numeric_model_feature_columns` selects a column
   when any training row has a numeric value, and `_design_rows` zero-imputes
   the rest at fit time — so a websocket inference row missing history-derived
   columns is treated exactly as the equivalent training rows were. Mandatory
   abstention on any missing column (old 141.2) would have abstained on every
   production prediction — withdrawn.
3. **What survives is narrower and better-defined than "the collector destroys
   an accumulating asset."** The corpus is a rolling rebuild by design:
   `_collection_rows` (`price_history_collector.py:154-195`) reselects priority
   finals + general tokens each run, and tokens that rotate out of selection
   legitimately drop (`general_tokens_truncated` counts them). The defect is
   that `write_csv` (`price_history_collector.py:391`) replaces the file with
   only THIS run's successful fetches, so a token that was **requested and
   failed** loses its previously collected rows: one success out of fifty
   erases the other forty-nine histories, and a totally failed run leaves a
   header-only file — both at exit 0. The loss is permanent when a token's
   selection or venue-serveable window lapses during the outage (priority
   finals live `lookahead_days_after_close` = 3 days), which is precisely the
   window a degraded upstream makes likely.

The registration-before-dispatch rule was followed; no build was dispatched
against the withdrawn scope. Chain-tracing discipline note, standing: this is
the fifth incorrect model-lane/dependency claim across WO-138/140/141
registrations, every one caught by Codex review and none by the orchestrator's
own audit — registrations touching model or inference behaviour must cite the
deployed configuration (compose file, CLI flags), not just module code paths.

**Scope — one mechanism.** File:
`src/polymarket_predictive_engine/price_history_collector.py` and its tests.

141.1 — **a token's prior corpus rows survive any fetch outcome that is not a
clean, recognized venue answer.** Before the final write, read the existing
corpus (when present) and merge per requested token by the rule below, written
through the existing atomic `write_csv` path with unchanged `SNAPSHOT_FIELDS`.
Price-history points are immutable venue facts, so a preserved row is
yesterday's copy of the same series — it can be short, never wrong.

The whole rule keys on ONE new recorded integer. `_fetch_history`
(`price_history_collector.py:263-293`) stops at the first non-empty answer and
swallows earlier attempt failures, and `normalize_price_history_payload`
(`:205-223`) coerces an UNRECOGNIZED body — an HTTP 200 whose JSON is
error-shaped, carrying none of the `history`/`prices`/`data` keys and not a
bare list — to an empty list without raising. Therefore define
`attempt_errors` (new on every quality row) as the count of attempts that
either RAISED or returned an unrecognized payload; only a recognized
history-schema response counts as the venue answering. Per-token merge rule,
exhaustive:

- **Clean success** (`attempt_errors == 0`, points returned): replace the
  token's rows with this run's fetch, exactly as today — the healthy path is
  byte-identical to current behaviour.
- **Fallback success** (`attempt_errors > 0`, points returned): the answer may
  cover a shorter window than the errored primary attempt (e.g.
  `bounded_close_window` raised, `short_close_window` answered with 7 of the
  prior 30 days), so replacement would shrink coverage. Union this run's
  points with the token's prior rows, deduplicated on
  (`token_id`, `timestamp`) with the freshly fetched row winning ties.
  Self-correcting: the next clean success replaces wholesale and re-trims to
  the venue's full answer.
- **Clean empty** (`attempt_errors == 0`, zero points): venue-authoritative —
  the venue answered every attempted shape with a recognized empty; prior rows
  drop as today.
- **Failed or unrecognized with no points** (`attempt_errors > 0`, zero
  points — covers `fetch_error` and mixed error/empty chains alike): preserve
  the token's prior rows unchanged. No prior rows means nothing is written for
  the token — preservation never fabricates.
- A token absent from today's selection still drops, exactly as today — the
  registered rolling-selection design is unchanged.

(The clean-empty/mixed-chain distinction, the unrecognized-payload
classification, and the fallback-success union were each added by successive
Codex review rounds on this registration — the first drafts would have deleted
history in realistic degraded-upstream cases.)

The totally-failed run needs no special case: every requested token lands in
the preserve branch, so the merge keeps the whole prior corpus instead of
writing a header-only file.

**Malformed-input predicates (deterministic, pinned by tests).** A prior
corpus is trusted for preservation only when its header is exactly
`SNAPSHOT_FIELDS`; any other header (missing, reordered-with-missing, or
foreign columns) means the whole prior file is untrusted and the run falls
back to today's write-what-was-fetched behaviour, recording
`prior_corpus_untrusted: true` in the summary. Within a trusted corpus, an
individual preserved row is valid only with a non-empty `token_id`, a
parseable `timestamp`, and finite numeric `price` AND `midpoint` that are
equal — the writer emits them identical
(`price_history_collector.py:344-346`), and the corpus's consumers read
`midpoint` FIRST (`features_v2.py:169-175`, `market_making_pnl.py:57-64`), so
a row validated on `price` alone could still feed a corrupt or missing
`midpoint` into training. An invalid row is dropped alone (counted in
`preserved_rows_dropped_invalid`) while the token's remaining valid rows are
kept. `read_csv_rows` tolerates arbitrary content, so these predicates are the
whole defence — nothing else validates this file.

**Honesty requirement.** Preservation must not dress up a failed fetch as a
successful one. The quality CSV keeps recording `fetch_error` for every failed
token exactly as today, and the summary gains `preserved_token_count` and
`preserved_row_count` so a reader of
`historical_price_history_summary.json` can distinguish "collected today" from
"carried forward". `error_count`, `requested_tokens`, and every existing
summary field keep their current meanings.

**Fail-safe sentence (S5).** Collection-only: nothing here marks a market
measured, alters any M-A/M-B/M-C or `maker_min_*` threshold, changes
`_measurement_eligible`, or widens any eligibility rule. Preserved rows are
prior immutable observations, never synthesised; when in doubt (unreadable
prior corpus, malformed prior rows for a token) the fallback is today's
behaviour — write what was fetched — never a crash and never fabrication.

**Tests (enumerated).** (1) a run whose every request errors leaves every
requested token's prior series byte-identical in the corpus and the summary
records the preservation counts alongside `error_count == requested_tokens`;
(2) a 1-of-N-success partial run writes fresh rows for the success and
preserves each failed token's prior rows; (3) a token rotated out of the
selection is dropped even when preservation is active (pins the rolling
design); (4) a failed token with no prior rows adds nothing; (5) a full-success
run is byte-identical to today's output with both preservation counts at 0
(regression — the mechanism is inert on the healthy path); (6) the quality CSV
still carries `fetch_error` rows for preserved tokens (honesty); (7) a prior
corpus with a non-`SNAPSHOT_FIELDS` header is wholly untrusted — the run falls
back to write-what-was-fetched, stamps `prior_corpus_untrusted`, and does not
raise; (8) within a trusted corpus, a row with a missing timestamp, a
non-finite price, or a missing/non-finite/disagreeing midpoint is dropped
alone and counted in `preserved_rows_dropped_invalid` while the token's valid
rows survive; (9) a token whose fetch chain mixed errors with a final empty
payload (`attempt_errors > 0`, zero points) keeps its prior rows and its
quality row records the attempt errors; (10) a clean empty
(`attempt_errors == 0`, recognized schema) still drops prior rows
(venue-authoritative regression); (11) a chain whose every attempt returns
HTTP 200 with an error-shaped/unrecognized body counts those attempts in
`attempt_errors` and preserves prior rows — it must NOT read as a clean empty;
(12) a fallback success (primary attempt raises, a shorter window answers)
unions the fetched points with the token's prior rows, deduplicated on
(`token_id`, `timestamp`) with the fetched row winning ties, so prior
coverage outside the fallback window survives.

**Day-after check:** on the next scheduled harvest,
`historical_price_history_summary.json` shows `preserved_token_count: 0` on a
healthy run; if upstream degrades again, the corpus row count for
requested-and-failed tokens holds instead of collapsing, while the quality CSV
and `error_count` report the failures undiminished.

**Recorded separately, not in this WO:** the verified model-lane finding that
`volatility_penalty` is structurally dead (`mispricing_alpha.py:488-490` reads
`rolling_volatility_*` fields that `predict_from_features`
(`calibrated.py:127-171`) never copies into prediction rows, so the penalty is
always 0.0 and `volatility_penalty_weight` is dead config, inflating
`edge_lower_bound` on every row while missing liquidity IS penalised at
`:499-500`). That is independent of corpus health and will be registered as its
own work order with the deployed-configuration citation rule applied.


## WO-142 — Wire the volatility columns into prediction rows so the declared volatility penalty actually applies — `queued` (registered 2026-07-30 BEFORE dispatch; decision-surface change to the `edge_field_for_trading` producer → OWNER MERGE after line-audit; tighten-only, edges may only shrink)

**Provenance.** Drafted by the Opus-tier spec agent from the finding recorded in
the WO-141 registration's "Recorded separately, not in this WO" paragraph, with
every line number re-verified against current `main` before drafting. The
drafting pass CORRECTED the orchestrator's original finding in one material
way, recorded here per the deployed-configuration citation rule: the
hypothesis that the deployed websocket lane leaves the volatility columns
blank is FALSE — `features_v2.py:431-434` computes `rolling_volatility_1h/6h/24h`
for EVERY source from midpoint history accumulated across the input file
(`:353-358`), and the deployed `websocket_market_features.csv` is a rolling
~72h multi-snapshot table (`websocket_market_data.retain_existing_features:
true`, `feature_retention_hours: 72`, `max_feature_rows: 60000`, rewritten
every 120s — config lines 1489-1491, 1498), so `rolling_volatility_6h` is
routinely non-blank in production and the penalty applies the moment the
columns are copied through. This fix bites in the deployed lane; it is not a
paper exercise.

**The defect (verified by direct code read, 2026-07-30).** `volatility_penalty`
is structurally dead. `_microstructure_penalty` reads `rolling_volatility_6h` /
`rolling_volatility_24h` from prediction rows (`mispricing_alpha.py:488-490`),
but `predict_from_features` (`models/calibrated.py:127-171`) copies eleven
microstructure fields into the prediction row and never copies the three
`rolling_volatility_*` columns, so `safe_float(None)` returns `None`
(`utils.py:373-379`), `volatility_penalty` is `0.0` on every row (`:515`),
`volatility_penalty_weight: 0.15` (config line 714) is dead config, `total`
(`:516`) → `alpha_microstructure_penalty` (`:798`) → `total_penalty`
(`:830-834`) is structurally understated, and `edge_lower_bound` (`:838`) — the
declared `edge_field_for_trading` (config line 697, echoed at
`mispricing_alpha.py:1046`, consumed at `strategy.py:219`) — is inflated on
every row relative to its documented intent. The asymmetry is real: missing
LIQUIDITY is penalised (`missing_liquidity_penalty`, `:499-500`); missing
volatility silently costs nothing. `_enrich_with_latest_websocket_quotes`
cannot rescue it: `QUOTE_ENRICHMENT_FIELDS` (`mispricing_alpha.py:39-51`) has
no volatility field and neither does its source schema
(`websocket_normaliser.py:36-62`). `volatility_penalty` is ALREADY a column in
`predictions.csv` and `mispricing_alpha_scores.csv` (via `**micro_parts` at
`:799`), currently `0.0` on every row — which makes the day-after check
trivially auditable.

**Scope: the copy-through of three existing feature columns plus the
observability to audit it. Nothing else.** This WO does NOT build: a new
penalty, a new config key, a missing-volatility penalty, any change to the
penalty arithmetic at `:492-516`, any change to a gate/threshold/filter, any
change to `features_v2.py`, any change to the near-miss or shadow bands, any
consumer of the new telemetry, or anything on any order, signer, credential,
or live surface.

**Touch ONLY these files** (`git diff --stat` must show exactly these four):
- `src/polymarket_predictive_engine/models/calibrated.py` (three copy-through keys in `predict_from_features`)
- `src/polymarket_predictive_engine/mispricing_alpha.py` (`volatility_source` provenance stamp + three summary counters; penalty arithmetic byte-identical)
- `tests/polymarket_predictive_engine/test_mispricing_alpha.py` (extend)
- NEW `tests/polymarket_predictive_engine/test_volatility_penalty_wiring.py`

Do NOT touch `features_v2.py`, `websocket_normaliser.py`, `strategy.py`,
`readiness.py`, `risk.py`, `paper_cycle.py`, `cli.py`, `ledger_anchor.py`, any
config file, any compose file, any scheduler script, or the governance docs
(the orchestrator flips register status post-merge; the build PR is exactly
these four files).

**142.1 — `models/calibrated.py`, `predict_from_features`.** Insert exactly
three entries into the `prediction` dict, between
`"book_imbalance": row.get("book_imbalance", ""),` (line 157) and
`"time_to_close_hours": ...` (line 158) — position matters because
`utils.write_csv` derives fieldnames from first-seen key insertion order
(`utils.py:128-134`):

```python
"rolling_volatility_1h": row.get("rolling_volatility_1h", ""),
"rolling_volatility_6h": row.get("rolling_volatility_6h", ""),
"rolling_volatility_24h": row.get("rolling_volatility_24h", ""),
```

Copy the value VERBATIM — no `safe_float`, no rounding, no default beyond the
empty string. Nothing else in the function changes; `latest_feature_rows`
(`calibrated.py:71-88`) already selects the newest row per market/token and
must not be changed. `rolling_volatility_1h` is carried for audit only; the
penalty continues to consult 6h-then-24h exactly as today.

**142.2 — `mispricing_alpha.py`, provenance stamp.** Replace lines 488-490 with:

```python
volatility = safe_float(row.get("rolling_volatility_6h"))
volatility_source = "rolling_volatility_6h" if volatility is not None else ""
if volatility is None:
    volatility = safe_float(row.get("rolling_volatility_24h"))
    volatility_source = "rolling_volatility_24h" if volatility is not None else "missing"
```

Add `"volatility_source": volatility_source,` to the returned dict immediately
after `"volatility_penalty": volatility_penalty,` (`:521`). Lines 492-516 must
be byte-identical after the change — same weights, same `max(0.0, ...)`
clamps, same summation order; prove it with the diff in the PR.

**142.3 — `mispricing_alpha.py`, summary counters.** Insert after
`"microstructure_filter_failures": ...` (`:1036-1038`) and before
`"fundamental_probability_sources"`, exactly three keys:
`rows_with_rolling_volatility` (count of final rows whose `volatility_source`
is one of the two window names), `rows_missing_rolling_volatility` (count with
`"missing"`), `volatility_penalty_sum` (float sum). Third-state invariant,
stated because it is real: rows that exit early at `:629-632`
(`skipped_missing_probability_or_price`) or `:812-815` never reach
`_microstructure_penalty` and carry no stamp — they are counted in NEITHER
counter, so the two counters sum to <= predictions. Do not "fix" that by
defaulting the absent stamp to `missing`. Do not add
`paper_trading_invoked`/`live_trading_invoked` to this pre-existing artifact.

**Anchor safety.** All three touched artifacts are full-rewrite snapshots via
the atomic writers; verify for yourself (do not trust this sentence) that
`outputs/polymarket_predictions/*` appears in neither
`ledger_anchor.DEFAULT_LEDGER_REGISTRY` nor the config `ledger_globs` before
widening the header. If your grep disagrees, STOP and report — do not widen.

**Fail-safe (state verbatim and contiguous in the `mispricing_alpha.py` module
docstring).** "Volatility copy-through only: a missing, blank, or malformed
`rolling_volatility_6h`/`rolling_volatility_24h` yields `volatility_penalty =
0.0` and `volatility_source = \"missing\"`, which is unchanged from prior
behaviour and never raises; nothing here marks a market measured, changes any
M-A/M-B/M-C or `maker_min_*` threshold, changes `_measurement_eligible`, moves
any gate, sizing rule, filter, or eligibility surface, and `edge_lower_bound`
may only shrink or stay equal as a result of this change, never grow."

**Direction disclosure (tighten-only, one exception named).** Every decision
consumer moves tighter or stays equal as `total_penalty` grows:
`alpha_trade_candidate` (`:917`, vs `risk.minimum_edge: 0.03`), the shadow
lane (`:898`), `strategy.py:262-285` stake sizing,
`probationary_positive_edge_override` (`strategy.py:419-425`),
`promoted_shadow_override` (`:414-418`). The SINGLE widening: a shrinking
`edge_lower_bound` can move a row out of
`edge_lower_bound_above_near_miss_band` (`mispricing_alpha.py:927-930`), so
the near-miss LEARNING population can grow. That band is observation-only —
`require_alpha_trade_candidate: true` (config 698, `strategy.py:426-432`)
keeps near-miss rows out of paper approval — but near-miss rows accumulate
cohort evidence that `allow_near_miss_learning_cohort_proxy: true` (config
1275) can later use for OTHER rows (`strategy.py:385-394`). Disclosed, watched
by the day-after check, and this WO must not touch any `near_miss_learning` or
`cohort_promotion` setting.

**Cadence wiring: none.** No new artifact, no new entry point;
`apply_mispricing_alpha` already runs inside `run_paper_cycle`
(`paper_cycle.py:133`) every 30s on the deployed cadence, and the CLI paths
flow through the same functions. Do not add a CLI command, scheduler line, or
ledger enrolment.

**Tests (offline, deterministic, exact hand-computed values; numeric
assertions use `pytest.approx(expected, abs=1e-12)`; fixtures use distinct
market_ids so `cross_penalty` is exactly 0.0; test config zeroes every other
penalty weight and sets `volatility_penalty_weight: 0.15`).** New module
`test_volatility_penalty_wiring.py`: (1) copy-through — prediction dict
carries all three keys verbatim ("0.04"/"0.09"/""); MUST fail against unfixed
source (KeyError); (2) unit 6h branch — `volatility_penalty == approx(0.006)`,
source stamped `rolling_volatility_6h`; (3) unit 24h fallback — blank 6h,
"0.10" 24h → `approx(0.015)`, source `rolling_volatility_24h`; (4) both
blank/absent/malformed ("n/a", "-") → 0.0 and `"missing"`, no exception; a
negative parseable value clamps to 0.0 via the existing `max(0.0, ...)` with
source still stamped; (5) headline — two rows identical except
`rolling_volatility_6h` ("" vs "0.04") through `apply_mispricing_alpha`:
penalties 0.0 vs approx(0.006), `edge_lower_bound` difference exactly
approx(0.006), strict inequality; (6) end-to-end from a three-row
`websocket_market_features.csv` fixture (midpoints 0.50/0.54/0.52 at
T-5h/T-2h/T) through `build_features_v2(source="websocket")` →
`rolling_volatility_6h == approx(0.02)` hand-computed → scored row
`volatility_penalty == approx(0.003)`; MUST fail against unfixed source; (7)
single-snapshot asset → all three columns blank → 0.0/"missing", no exception
(the honest blank case, input to WO-142b). Extended
`test_mispricing_alpha.py`: (8) telemetry counters on a three-row fixture (one
with volatility, one blank, one skipped-early) — counters 1/1 and the skipped
row in neither; (9) no gate moved / monotone tightening — same fixture at
weight 0.0 vs 0.15: trade-candidate set under 0.15 is a SUBSET of that under
0.0, same for shadow candidates, `trade_candidates` count <=, and the config
literals (`risk.minimum_edge == 0.03`, `edge_field_for_trading ==
"edge_lower_bound"`, `require_alpha_trade_candidate is True`) untouched; (10)
near-miss widening disclosed — a row that is a trade candidate at 0.0 falls
into the near-miss band at 0.15 and appears in
`near_miss_learning_candidates.csv`; this test makes the widening
regression-visible and must not be "fixed" by suppressing the band; (11)
header order — the three columns contiguous after `book_imbalance`,
`volatility_source` contiguous after `volatility_penalty`; every pre-existing
test in `test_mispricing_alpha.py`, `test_optimized_model.py`, and
`test_predictive_power_expansion.py` passes UNMODIFIED.

**Follow-on named, NOT built here — WO-142b.** A symmetric missing-volatility
penalty (the residual asymmetry against `missing_liquidity_penalty`) is
deliberately out of scope: its magnitude has no measured basis
(`missing_liquidity_penalty: 0.01` at weight 0.15 would imply an assumed 6h
midpoint std of 0.0667, which nothing in this repository supports). WO-142
makes the blank population COUNTABLE (`rows_missing_rolling_volatility`);
after one deployed day of that telemetry the owner decides WO-142b's
magnitude. It stays prose here — no placeholder registration.

**Deployment caveat (added 2026-07-31, deployed-configuration citation rule).**
Telemetry shows `mispricing_alpha_live_summary.json` frozen at
2026-07-19T22:21:45Z while `forward_paper_cycle.json` stamps `status: "ran"`
fresh every cycle. The only code path consistent with both is
`apply_mispricing_alpha`'s silent early return on
`settings.get("enabled") == False` (`mispricing_alpha.py:542-543`) — the
deployed VPS host config has evidently carried `mispricing_alpha.enabled:
false` since ~2026-07-19 (the tracked example config defaults `true`; no
registered decision disables it). Consequences, recorded honestly: this WO's
copy-through is correct and tighten-only regardless, but it is DORMANT in the
deployed lane until the overlay is re-enabled — the owner decides whether and
when. The build and its tests are unaffected (they run the overlay
explicitly). Separately noted as a candidate hardening item, same fail-open
class as the WO-129 depth-gate disable: a config-reachable off-switch on a
scoring lane that leaves no artifact trace — a disabled overlay should stamp
a visible `disabled` status artifact instead of skipping silently.

**Day-after check (conditional on the overlay being enabled in the deployed
config; if it is disabled, the check is deferred and this WO's deployment
status recorded as `dormant_pending_overlay_enable`).** Before the deploy
that carries this change, the orchestrator records the pre-deploy
`trade_candidates` and `near_miss_learning_candidates` values from the
telemetry branch's `mispricing_alpha_live_summary.json` into this WO's status
line — using the most recent cycle in which the overlay actually ran. After
one deployed paper cycle with the overlay enabled: `volatility_penalty` is
non-zero on at least one row of `mispricing_alpha_scores.csv` with
`volatility_source = "rolling_volatility_6h"`; the summary shows
`rows_with_rolling_volatility >= 1`, `volatility_penalty_sum > 0`, and
`rows_missing_rolling_volatility` recorded; `trade_candidates` is <= the
pre-deploy value. If `trade_candidates` rises, the tighten-only claim is
falsified and the change is reverted, not tuned.
