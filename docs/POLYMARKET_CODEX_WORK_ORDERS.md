# Polymarket Codex Work Orders

Last updated: 2026-07-02 (all six work orders landed)

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

## Sequencing

```text
WO-1, WO-2, WO-3   independent of each other — land in any order (WO-1 first is most useful)
WO-4               independent — can start immediately
WO-5               after WO-4
WO-6               after WO-5
```

After all six land: WP3 is done (flip it in the charter), the algo track (WP9–WP11) is done, and
the next charter priorities are WP4 (CLV-aware promotion review) and WP5 (depth-based execution
costs), which then plug straight into the replay harness's fill simulation.
