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

## GLOBAL RULE — what "registered" means (2026-08-01, binding on every WO)

Registration-before-dispatch is only enforceable if "registered" has one
definition. It did not: PR #417 was acquitted using *merged to `main`*, while
dispatches against `f64416e` (PR #418, unmerged) relied on *present in my
branch*.

**Rule: the `origin/main` tip at dispatch must be an ancestor of the build
branch, and BOTH SHAs are recorded.**

```
git fetch origin main
git merge-base --is-ancestor <origin/main tip at dispatch> <build-branch-head>
```

The WO status line records
`registered-ancestry: <origin/main-sha> ancestor-of <build-branch-sha> PASS`,
**appended once per dispatch** — a WO sees several fix-round dispatches, so the
record is a list, not a single value.

**Two residuals this rule cannot close, recorded so they are not mistaken for
covered.** (i) It verifies the branch contains `main`; it does not verify the
BUILDER READ the WO. `91c35cd`'s commit message described §143.6 as "amended:
now unconditional" while its own tree carried the conditional text — the agent
learned it from the dispatch prompt, not the register. Mitigation, registered:
**the dispatch prompt cites the WO by section and does not restate its
content.** (ii) It cannot detect a builder that read stale text it had already
cached.

**Why `origin/main` tip rather than "the registering commit".** A previous
version said "the LATEST registering-or-amending commit". Nothing determines
"latest" mechanically — there is no way to enumerate a WO's registering commits
(`git log -L` on a section range breaks the moment a section moves, and sections
do move), so "latest" is human judgement. An honest dispatcher who believes the
wrong amendment is newest produces a green, recorded, audit-shaped token that is
wrong. The `origin/main` tip needs no judgement, strictly contains every
amendment to every WO, and is what rebase-before-dispatch produces anyway.

**Why BOTH SHAs.** The rule is evaluated by whoever runs it, whenever they run
it, so a stale dispatch followed by a rebase launders clean. Verified:
`git merge-base --is-ancestor 51ffb42 2a7c305` **passes** — `2a7c305` is
`claude/wo143-scheduled-cycle` after its rebase onto `51ffb42` at 10:14:16Z —
while the same test against its actual dispatch head `91c35cd` fails. Recording
only the registering SHA proves the check ran against *something*, not against
the branch state that was dispatched. **The build-branch SHA at dispatch is the
half that makes the record honest**, and a later rebase does not retroactively
make the earlier dispatch compliant.

**"Latest", not "the".** A first attempt at this rule said "the registering
commit", singular. Every WO gets amended, so that is defeated by citing the
ORIGINAL registration: for WO-143,
`git merge-base --is-ancestor 357519e 91c35cd` **passes** while
`git merge-base --is-ancestor 51ffb42 91c35cd` **fails** — a dispatcher citing
`357519e` gets a green check on the exact branch whose tree lacks §143.6 and
which produced Codex's P1. The rule must bind to the newest text the builder is
expected to have read.

**Which SHA — this repository squash-merges.** The registering commit is the
**squash-merge commit on `main`**, identified after merge, never the branch-side
SHA. `926fec9` (branch) and `51ffb42` (main) are the same registration; only
`51ffb42` is an ancestor of anything. Citing a branch-side SHA makes the test
return NO forever on every correctly-registered dispatch, and the natural
operator workaround — cherry-picking the register commit onto the build branch —
reintroduces the unmerged-registration hole this rule exists to close.

**Cherry-picked registrations correctly FAIL.** Both current build branches
carry `f64416e` by cherry-pick and both fail the test, because `f64416e` is not
merged. This is intended: the rule asserts BOTH that the builder read the right
text AND that the registration is merged. An operator watching a cherry-picked
branch fail must merge the registration, not re-cherry-pick.

**Why this test and not a timestamp comparison.** A first version of this rule
used two conditions — registering commit merged to `main`, and build branch cut
after that merge. **Both pass on the exact incident that produced Codex's P1,
and the incident is still real.** `claude/wo143-scheduled-cycle` was created at
09:16:32Z, after `51ffb42` merged at 09:12:15Z, but it was cut from a *stale
local `origin/main` ref* (`main` was not fast-forwarded until 10:09:35Z, 53
minutes later), so `git merge-base --is-ancestor 51ffb42 91c35cd` returns **NO**
— the registration is absent from the tree the builder reads. The timestamp rule
encoded the symptom; the ancestry test encodes the property.

The timestamp rule was also unverifiable: branch-creation time lives in a local
reflog that is never pushed, so no reviewer, CI job, or later auditor could check
it from the repository. The ancestry test is checkable by anyone from the pushed
refs.

Consequences accepted rather than quietly avoided:
- PR #416's initial dispatch was pre-registration (`51ffb42` is not an ancestor
  of its first head). Recorded in the WO-143b header.
- PR #417's dispatch fails the ancestry test for the same reason, which is what
  Codex's P1 correctly identified.
- The two fix-round dispatches made on 2026-08-01 against unmerged `f64416e`
  also fail it. Both builds are held until #418 merges, and both branches rebase
  onto the resulting `main` before resuming.

Timestamps are retained in the WO-143b provenance note as history, not as rule.

**Enforcement.** A test asserts that every WO whose status names a dispatched
build carries a `registered-ancestry: <sha> PASS` token, so this rule is
mechanically pinned like the register's other invariants rather than left to
honour. Until that test exists the rule is prose and should be treated as such.

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

## WO-106 — Reward-epoch time-series collector (DONE 2026-07-19; PR #265)

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

## WO-111 — Persist per-day portfolio membership + per-market markout in a NEW anchor-safe sidecar ledger — `done` (2026-07-20, PR #341)

**Register-correction note (2026-08-01).** Ground truth from the merge
history: the sidecar shipped in PR #341 (commit `e34ccb1`, 2026-07-20) and has
been live in `maker_carry_study.py` ever since, touched again by the
WO-118/119/120 lineage (PR #358, whose commit message references "the WO-111
members sidecar" as already existing). `maker_carry_portfolio_members.csv` is
enrolled `append_only` in `ledger_anchor.py`'s `DEFAULT_LEDGER_REGISTRY` and in
`polymarket_predictive_config.example.yaml`, and is read by
`maker_carry_study.py` today. The heading above is corrected from PROPOSAL to
`done` with PR attribution and no actor claim. The **"PROPOSAL — NOT authorized
to build"** paragraph immediately below is the original rev.2 registration
record, kept for history; it no longer describes the current authorization
state.

Priority: MEDIUM — auditability / anti-regression, not funding-gating.
Authorization status (historical, as originally registered): **PROPOSAL — NOT authorized to build.** This is a
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

## WO-108 — NaN fail-open residuals in WO-50 policy/kill surfaces (DONE 2026-07-19; PR #267, FROZEN; tighten-only)

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

## WO-107 — M-B.1: require the portfolio market's own Tier-0 coverage (DONE 2026-07-19; PR #262, FROZEN M-B; tighten-only)

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

PR #262's merge is the repository authorization record; no
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

## WO-114 — VPS ops hygiene: seasonal-job disable switch, ops-log rotation, dashboard-setup readiness wait — `done` (2026-07-21, PR #354)

Orchestrator-built. Non-frozen ops scripts only; no gate,
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

## WO-115 — Unbreak ledger anchoring: snapshot-enroll the rewritten carry history, fail loud on blocked chains — `done` (2026-07-26, PR #356 commit 6e04263; registered WO-61 surface)

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
- Chain re-genesis executed on the VPS 2026-07-26 (fresh genesis,
  2026-07-26): broken chain + head archived to
  `outputs/performance/ledger_anchor_retired/20260726T100457Z/` (historical
  anchors 2026-07-12..16 preserved there, on the `vps-anchor` branch, and in
  `ledger_anchor_snapshots/`). New chain verified:
  anchor_date 2026-07-26, chain_head 9fc5ff0a..., previous_chain_head all-zeros.

## WO-116 — Seed official-book collection for top-ranked candidates before selection — `done` (2026-07-26, PR #356 commit 31a3e95)

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
  the 2026-07-19 configured breadth posture). Runs even with an empty
  portfolio. Seeded files stay warm via the existing mtime tranche and season
  toward eligibility.
- Collection breadth ONLY: no gate, threshold, eligibility rule, sizing, or
  order path reads the setting; the collection-window ledger stays
  portfolio-only (coverage_ratio semantics unchanged); snapshot summary now
  reports per-tranche counts.

## WO-117 — Window-aware overrun classification for the harvest-gated maker study — `done` (2026-07-26, PR #356 commit 98f4033)

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

## WO-132 — Correct the work-order register and make unresolved review threads block merge — `done` (2026-08-01, PR #410; routed owner-merge, orchestrator self-merge prohibited; rebuilt from scratch on current main after the first attempt went stale, every claim re-derived from the merge history; the `AGENTS.md` unresolved-thread merge precondition is live, and the 35+20 legacy threads on #356-#363 remain an OPEN follow-up, recorded rather than claimed as triaged)

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

**Register-correction status (recorded 2026-08-01).** This pass re-derived
every claim below from `git log`/the GitHub API rather than porting any text
from a prior attempt at this WO.

- **Item 1.** The WO-121/122/124/125/126 entries named above no longer exist
  under those numbers. Ground truth: the combined branch this item describes
  was opened as PR #363 ("WO-121/122/124/125/126 combined (violates
  one-WO-per-PR)") and was closed superseded, never merged. WO-121 was later
  built and merged standalone as PR #385 (fix round PR #387); WO-122 split
  into WO-122a (PR #360) and WO-122b (PR #361); WO-123 merged alone as PR
  #362, exactly as this item already states. WO-124/125/126 were never
  rebuilt as standalone PRs and carry no register entry under those numbers
  today, so there is no surviving `done`/PR claim about them left to revert.
  Separately found in this pass, outside the original WO-121-126 scope: the
  WO-106, WO-107, WO-108, WO-114, WO-115, WO-116, and WO-117 headings (and, in
  WO-107/WO-114/WO-115/WO-116, matching body text) asserted
  "owner-merged"/"owner merge"/"orchestrator-merged"/"owner-approved" as a
  completed act — a claim about WHO merged or approved, which a GitHub
  squash-merge record never carries. Each PR (#265, #262, #267, #354, #356)
  is confirmed as a real merge on `main`, so those entries stay `done`/`DONE`;
  only the actor/approval claim was removed, leaving `(PR #NNN)` form. WO-111
  separately carried a stale **PROPOSAL** heading for a sidecar that in fact
  shipped in PR #341 (2026-07-20, commit `e34ccb1`) and has been live since;
  corrected to `done (2026-07-20, PR #341)`, original proposal text kept as
  history.
- **Item 3 — NOT completed in this pass.** Re-queried 2026-08-01 via the
  GitHub API: merged PRs #356, #357, #358, #359, #360, #361, and #362 carry
  5, 7, 9, 4, 3, 3, and 4 unresolved review threads respectively (35 total);
  the closed/superseded #363 carries 20 more (55 total across #356–#363).
  Every one of them is still marked unresolved on GitHub; none has been
  dismissed with a stated reason or matched to a WO above. Per the fail-safe
  direction below, all 55 count as UNRESOLVED. Per-thread triage into
  dismissals or WOs remains an OPEN follow-up, not resolved by this pass.

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


## WO-141 — A failed refresh must not erase collected price history — `done` (2026-08-01, PR #411; routed owner-merge after line-audit; registration rescoped 2026-07-30 after review disproved the original harm chain, then one audit fix round for the empty-string-exception misclassification; DEPLOY PENDING — the deployed revision predates this, so the collector on the VPS still erases prior rows for requested-but-failed tokens)

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

**Amendment (2026-08-01, after the Opus line-audit of the first build).**
Conformance findings registered into the fix round: (F1) failure
classification must key on an explicit final-attempt-raised boolean, never on
exception-message truthiness — `ConnectionError()`, `Timeout()`, and kin
stringify to empty, and the audit measured them misclassified as
`empty_history`, moving tokens from `error_count` into `empty_history_count`
on the exact fields the day-after check reads; pin with a test using an
exception whose `str()` is empty. (F2) `fetch_source` on `fetch_error` rows
stays `""` exactly as on main — the delivered `all_attempts_empty` value on
failed rows is an unregistered honesty-surface change and is reverted.
(F3) the fallback-union branch's carried-forward rows become visible via two
NEW summary fields, `fallback_union_token_count` and
`fallback_union_carried_row_count` (distinct from `preserved_*`, which keep
their strict preserve-branch meaning so a healthy run still reads
`preserved_token_count: 0`); the delivered test asserting zero for the union
case is corrected. (F4) registered tests (1) and (5) assert REAL byte
identity of the corpus file against the pre-fix output, not a three-field
projection. (F6) `attempt_errors` is a measurement everywhere — the
defensive per-token except path must record the actual count, not a
hardcoded 1, with a test. (F5) add the test pinning
reordered-but-complete header → untrusted. Findings F7/F8/F9 (whitespace
header edge, out-of-scope invalid-row counting, unstripped index key) are
recorded as fail-closed diagnostics-only observations, not required in this
round.

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


## WO-142 — Wire the volatility columns into prediction rows so the declared volatility penalty actually applies — `done` (2026-08-01, PR #412; routed owner-merge after line-audit; audited with zero deletions in the production diff, so the penalty arithmetic is provably untouched; DEPLOY PENDING, then dormant on the hot lane only — the deployed lane runs `--prediction-mode paper-bridge`, so the normal tick path never calls the full paper cycle, but the resource-guard degraded fallback (`scripts/run_polymarket_local_live_loop.py:1045`) still does, with no `prediction_mode` gate, and last fired 2026-07-19T22:22 — so this is live on that fallback today, not inert)

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

**Deployment caveat (added 2026-07-31; mechanism CORRECTED same day after
host verification — the first version of this caveat blamed a config
`enabled: false`, which was wrong: the tracked config has said `enabled:
true` since 2026-07-01 and the deployed stack reads that tracked file).**
Host ground truth: `mispricing_alpha_live_summary.json` last written
2026-07-19T22:22 while `forward_paper_cycle.json` refreshes every cycle.
Verified mechanism: the deployed live loop runs `_run_prediction_cycle` in
its default `paper-bridge` mode (`run_polymarket_local_live_loop.py:993-998`;
compose passes `--prediction-mode $POLYMARKET_PREDICTION_MODE`), and the
bridge cycle never calls `run_paper_cycle`, so `apply_mispricing_alpha`
never runs. The only in-production paths that invoke the full cycle are the
resource-guard degraded fallback (`:1037-1045`) and manual invocation; no
scheduler job owns it. The 2026-07-19 freeze therefore marks the last time
any full-cycle path fired (trigger unconfirmed: most plausibly the degraded
fallback ceasing as host health recovered). Consequences: this WO's
copy-through is correct and tighten-only but DORMANT in the deployed lane
until a full-cycle path runs it — the owner decides whether the taker alpha
lane should have a scheduled owner at all, given the registered maker-lane
priority. The build and its tests are unaffected. Candidate hardening item,
recorded: the full prediction/alpha lane can die silently — no freshness
consumer, watchdog registration, or heartbeat alarms when it stops; its
artifacts simply freeze while the bridge keeps the cycle report fresh.

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


## WO-144 — One degraded episode must notify once, not every cycle — `done` (2026-08-01, PR #409; built under the Codex-outage hierarchy, line-audited, one fix round applied for the two blockers below; DEPLOY PENDING — the storm continues on the VPS until the next Path B deploy carries this revision)

**Incident (measured).** Overnight 2026-07-31→08-01 the owner's phone received
an `operating_state_slo_breach` ntfy push roughly every 5 minutes (the
watchdog cadence); the incident ledger shows 39 `operating_state_slo`
incident-open events in 12.5 hours, every one with a distinct `incident_id`,
`consecutive_degraded_observations: 1`, and `detected_at_utc` equal to the
current cycle. False-alarm storms are how real alarms get ignored — the
WO-138 round-3 correction said exactly this, and the same defect class
shipped anyway.

**Root cause (verified by direct read).** `_incident_id(registration_id,
entity, episode_start)` (`degraded_state_watchdog.py:309-311`) is stable only
if `episode_start` is stable, but the operating-state SLO evaluator passes
`episode_start=token` (`:1029-1030`) where `token` is the CURRENT
observation token — the operating-state artifact's timestamp, which
refreshes every cycle. A persisting breach is therefore re-identified as a
brand-new incident each cycle; the prior id vanishes from the rebuilt
incident set; `state_changed` reads true; the state-change-gated notifier
(`:1271, :1338`) pushes every cycle. The underlying breach itself
(`scheduler_overrun_cycles`, ceiling 0) flaps nightly while the training
harvest legitimately overruns under the measured-slow upstream — flapping is
expected; the storm is the defect.

144.1 — **stable episode identity.** For every evaluator that opens
incidents, the identity anchor must be the FIRST breach observation of the
contiguous degraded episode, persisted in the watchdog state file keyed by
`(registration_id, entity)`, reused while the entity stays degraded, and
cleared only when the entity observes healthy (which ends the episode). The
`slo_first_unobserved` state pattern (`:1009-1022`) is the in-file precedent
to follow. Sweep ALL evaluators, not just the SLO one: any call site passing
a per-cycle-varying value as `episode_start` (audit each of the `_incident(`
call sites) is the same bug; fix each or record in the PR why its token is
already episode-stable (e.g. a last-success stamp that cannot change while
the job is failing).

144.2 — **per-entity push cooldown.** Even with stable identity, a flapping
condition legitimately starts new episodes (breach → healthy → breach), and
a nightly flapping window must not page more than once. Add a per
`(registration_id, entity)` notification cooldown persisted in the state
file: after a push for an entity, further pushes for that entity are
suppressed until a fixed floor elapses — 3600 seconds, fixed in code;
configuration may make the cooldown SHORTER (more pushes), never longer, so
the config surface cannot silence the channel. Fidelity guarantee: the
incident artifact, ledger CSV, and notification body file record every event
exactly as today — only the ntfy push channel is rate-bounded. A suppressed
push is recorded in the notification block (`pushes_suppressed_by_cooldown`
count and `next_eligible_push_utc`), so the suppression itself is visible
evidence, not silence.

**Explicitly out of scope:** the `scheduler_overrun_cycles` SLO ceiling (0)
and every other threshold — the nightly-harvest-overrun calibration question
is the owner's, recorded here as observed context only. No registration is
added or removed; no incident-opening logic changes beyond identity
anchoring; the WO-129 retry-debt mechanics for failed sends are untouched.

**Fail-safe sentence.** Nothing here suppresses, delays, or drops an
incident from the artifact or ledger; nothing marks a market measured or
touches any M-gate, threshold, or eligibility surface; the push cooldown can
only reduce notification frequency, its floor is fixed in code, and
suppression is always itself recorded. When the state file is absent or
malformed, identity anchoring falls back to today's behaviour (noisy, never
blind) and the cooldown treats the entity as never-notified (pushes
immediately).

**Tests (enumerated).** (1) a breach persisting across three cycles yields
ONE incident id, ONE push, and three ledger observations with
`consecutive_degraded_observations` incrementing; (2) the episode id is
reused while degraded and a NEW id is minted only after an intervening
healthy observation; (3) a flapping entity (breach/healthy/breach within the
cooldown) pushes once and records the suppression fields; (4) after the
cooldown elapses, the next new episode pushes again; (5) a DIFFERENT entity
breaching during another entity's cooldown pushes immediately (cooldown is
per-entity, never global); (6) absent/malformed state file: identity falls
back per-cycle (today's behaviour) and the cooldown does not raise; (7) the
sweep test: for every evaluator, a synthetic persisting condition observed
across two cycles produces identical incident ids (this is the regression
that pins 144.1 across ALL call sites); (8) ledger/artifact fidelity: with
the cooldown active, the ledger row count for a flapping entity equals
today's count exactly.

**Amendment (2026-08-01, after the Opus line-audit of the first build; evidence
contract reconciled).** The original tests (1) and (8) demanded per-cycle
ledger rows AND stable episode identity — contradictory, because the incident
ledger dedups on `incident_id`, so stable identity necessarily collapses a
persisting episode to ONE ledger row. Reconciled contract: the LEDGER records
episodes (one row per episode open); per-cycle observation depth is recorded
on the ACTIVE INCIDENT as an incrementing `consecutive_degraded_observations`
(persisted in the episode state — the SLO site must stop hardcoding
`count=1`), so no observation is hidden, it just lives in the artifact
instead of duplicate ledger rows. Tests (1) and (8) are re-registered
accordingly: (1) a breach persisting across three cycles yields ONE incident
id, ONE push, ONE ledger row, and an active incident whose
`consecutive_degraded_observations` reads 3 on the third cycle; (8) with the
cooldown active, ledger and notification-body outputs are byte-identical to
a cooldown-disabled run (the audit verified this already holds). Additional
items registered into the fix round from the audit: (F1) the cooldown
comparison and the prune must both treat a FUTURE-DATED stamp as expired-now
(floor the elapsed term at zero and drop future stamps in the prune) — a
clock artifact must never extend suppression beyond the floor; (F3) a
cooldown must also arm when an episode's first push succeeds only on RETRY:
persist `undelivered_incident_entities` alongside the existing bounded
ids/registrations debt, and stamp those entities on the delivery that clears
them; (F4) registered test (7) means EVERY evaluator — add a parametrized
id-stability sweep pinning all twelve call sites, including the nine whose
stability is pre-existing; (F5) the top-level `notify` now means "a push
will be attempted this cycle" (suppressed incidents make it False) — this
field meaning is hereby registered; `eligible` and `state_changed` keep
`bool(new)`; (F6) the settings parse for the cooldown key must survive
`.inf`/overflow inputs without raising (extend the guarded parse — the
watchdog dying in `_settings` is the most fail-open outcome there is);
(F7) the malformed-state test must also exercise the push path and pin
"malformed cooldown state pushes immediately"; (F8) `notified_entities` is
capped (newest 64 entries) and pruned on every cycle, not only on delivered.

**Day-after check:** the owner's ntfy history shows at most one
`operating_state_slo_breach` push per hour-long flapping window overnight,
while the incident ledger for the same window still records every
open/observation; the notification block shows nonzero
`pushes_suppressed_by_cooldown` during the harvest window.


## WO-143 — Give the full paper cycle a scheduled owner, as a scoring-only slot the live container cannot race — `queued` (owner-directed 2026-07-31; registered 2026-08-01 BEFORE dispatch; scheduler + registered watchdog surface + canonical-cycle signature → OWNER MERGE after line-audit; no gate, threshold, eligibility, or funding value changes)

**Provenance.** Drafted by the Opus-tier spec agent from the owner's 2026-07-31
direction, against the hardening item recorded in the WO-142 registration's
deployment caveat ("the full prediction/alpha lane can die silently"). Every
line number was re-verified against current `main` before drafting. The
drafting pass CORRECTED the orchestrator's brief in three material ways,
recorded per the deployed-configuration citation rule: (1) the live bridge
does NOT hold the `prediction_cycle` lock continuously — it acquires at
`run_polymarket_local_live_loop.py:956` and releases at `:986` once per 30s
cycle, so a scheduled cycle can run, and the starvation risk runs the OTHER
way (the scheduled cycle holding the lock makes the bridge skip its 30s
cycles, visibly); (2) there is NO bounded variant of `run_paper_cycle` to
reuse — `_run_degraded_prediction_cycle` calls it with identical arguments
(`:1045`) and the asset cap applies to the websocket collector (`:1111-1116`),
not the cycle; (3) the naive design would add a second cross-container writer
to `shadow_fills.csv` (append_only-enrolled) and the lock-free paper broker,
because `update_shadow_cohort_evidence`'s docstring requires the
`prediction_cycle` lock (`shadow_cohort.py:863-868`) while the live loop's
tick path calls it without one (`:759`, `:2040`). The scope below is
SCORING-ONLY for exactly that reason: the live container remains the sole
writer of every portfolio, shadow, profit-target, and dashboard artifact.

**Purpose.** The canonical forward paper cycle — `build_features_v2` →
`write_predictions` → `apply_mispricing_alpha` → `_persist_predictions` →
longshot scan → signals (`paper_cycle.py:110-169`) — has had no in-production
owner since 2026-07-19T22:22 (the last degraded-fallback firing). Host ground
truth: `mispricing_alpha_live_summary.json` frozen at that stamp; the trainer
artifacts frozen at 2026-07-13; `forward_paper_cycle.json` fresh from the
bridge, which is why nothing alarmed. Worse than dormancy: the live loop's
per-tick `_lightweight_shadow_maintenance` (`:707-723`) reads the frozen
`mispricing_alpha_scores.csv` and re-marks 12-day-old candidates at current
prices into the shadow cohort. This WO gives the lane a scheduled owner and a
watchdog that cannot be fooled by a fresh sibling artifact. It builds ONLY:
one scheduler job, one CLI entry point, one keyword-controlled scope on the
existing cycle, one watchdog freshness registration, and their tests. It does
NOT build: any change to what the live loop does per cycle; any new gate,
threshold, screen, filter, or eligibility rule; any change to a penalty, edge
field, or sizing rule; any config value change; any order, signer,
credential, or live surface; any new consumer of the revived artifacts; and
no change to `features_v2.py`, `mispricing_alpha.py`, `strategy.py`,
`readiness.py`, `risk.py`, `shadow_cohort.py`, `paper_broker.py`,
`runtime_lock.py`, or `docker-compose.vps-paper.yml`.

**Touch ONLY these files** (`git diff --stat` must show exactly these eleven —
count corrected 2026-08-01; §143.6 added the ninth and tenth, §143.7(b) the
eleventh):
- NEW `src/polymarket_predictive_engine/scheduled_paper_cycle.py`
- `src/polymarket_predictive_engine/paper_cycle.py` (one keyword-only `scope` parameter; `scope="full"` behaviour byte-identical **except for the two invocation flags — amended by §143.7(a)**)
- `src/polymarket_predictive_engine/cli.py` (one command, one import, one dispatch branch)
- `src/polymarket_predictive_engine/degraded_state_watchdog.py` (one entry in `REGISTERED_JOB_FRESHNESS_MAX_SECONDS`)
- `scripts/run_vps_ops_scheduler.sh` (one job function, one loop block, interval/timeout env with clamps)
- NEW `tests/polymarket_predictive_engine/test_scheduled_paper_cycle.py`
- `tests/polymarket_predictive_engine/test_degraded_state_watchdog.py` (extend)
- `tests/test_polymarket_vps_docker.py` (extend)
- `scripts/run_polymarket_local_live_loop.py` (§143.6 lock-clearer tightening)
- `tests/polymarket_predictive_engine/test_local_live_loop.py` (§143.6 tests)
- `polymarket_predictive_config.example.yaml` (repository ROOT) — §143.7(b)'s
  `scheduled_paper_cycle.max_websocket_observation_age_seconds: 1800`

Do NOT touch `docker-compose.vps-paper.yml` (the scheduler service already
loads `env_file: .env`, so `OPS_PAPER_CYCLE_*` overrides reach the container;
`VPS_OPS_MEM_LIMIT` stays 2g) or the governance docs. **"the example config"
was struck from this sentence 2026-08-01: §143.7(b) registers
`scheduled_paper_cycle.max_websocket_observation_age_seconds` (default 1800,
tighten-only), which must be documented in
`polymarket_predictive_config.example.yaml` at the repository ROOT — an
eleventh touched file.** For the avoidance of doubt against the "any config
value change" exclusion above: adding this NEW setting with its registered
default is in scope; changing any EXISTING config value is not.

### 143.1 — `paper_cycle.py`: one keyword-only scope, defaults byte-identical

Signature becomes exactly `def run_paper_cycle(cfg, *, source="raw_snapshot",
scope="full")`, threaded into `_run_paper_cycle_unlocked`. `scope` accepts
exactly `{"full", "scoring_only"}`; any other value raises `ValueError`
BEFORE the lock is taken (an unrecognised scope must never silently run the
full cycle). Naming hazard for the builder: `paper_cycle.py:8` imports
`render_dashboard` — do not shadow it with a parameter name.

`scope == "full"` is byte-identical to today on every path **except for the
two invocation flags, which every artifact must carry — amended by §143.7(a)**.
`scope ==
"scoring_only"` changes exactly six things and nothing else:
1. Every `write_json(cfg.governance_root / "forward_paper_cycle.json", ...)`
   (`:87`, `:104`, `:121`, `:199`) writes to
   `scheduled_paper_cycle_report.json` instead; `forward_paper_cycle.json`
   must not be created, read, or modified on this path (the live loop merges
   and re-stamps it every tick at `:762-778`, and `readiness.py:118-122`
   reads it — a scheduled write would be republished as fresh forever).
2. Skip `update_shadow_cohort_evidence` (`:139`);
   `report["shadow_cohort"] = {"status": "skipped_scoring_only"}`.
3. Skip `paper_trade` (`:147`) AND `write_profit_target_tracker` (`:166`);
   set `broker`, `actual_profit_target`, and `monthly_profit_target` each to
   `{"status": "skipped_scoring_only"}` (never compute pace from an empty
   broker dict).
4. Skip `write_signal_cohort_pnl` (`:140-144`);
   `report["cohort_pnl"] = {"status": "skipped_scoring_only"}` (the live
   loop and broker recompute it deterministically every tick).
5. Skip `render_dashboard` (`:196`);
   `report["dashboard"] = {"status": "skipped_scoring_only"}`.
6. Skip `write_agent_runtime_bundle` (`:192` and the three early-exit calls);
   `report["agent_runtime"] = {"status": "skipped_scoring_only"}`.
Everything else runs unchanged and in order: features, models, predictions,
mispricing alpha, `_persist_predictions`, `build_longshot_bias_scan(cfg,
emit_shadow=False)`, `paper_trade_readiness`, `generate_signals`.
`report["status"]` keeps its existing meaning; add `report["scope"] = scope`.

### 143.2 — NEW `scheduled_paper_cycle.py`

`def run_scheduled_paper_cycle(cfg, *, source="websocket") -> dict`, with
fixed module constants `LOCK_WAIT_MAX_SECONDS = 300.0`,
`LOCK_WAIT_SLEEP_SECONDS = 10.0`, and the contention exit code IMPORTED from
`refresh_governance` (75) — no second constant. Behaviour, in order: record
`started_at_utc`/monotonic start; install a SIGTERM handler raising
`SystemExit` (restored in `finally`) so the scheduler's `timeout` SIGTERM
unwinds the `runtime_lock` context manager instead of leaking the
`prediction_cycle` lock for the 1800s stale window — the scheduler must NOT
use `timeout -k` for this job; read the pre-run `generated_at_utc` of
`mispricing_alpha_live_summary.json`; bounded acquisition loop calling
`run_paper_cycle(cfg, source=source, scope="scoring_only")`, retrying on
`skipped_existing_prediction_cycle` every `LOCK_WAIT_SLEEP_SECONDS` up to
`LOCK_WAIT_MAX_SECONDS`, counting attempts (no second lock; do not reduce
`prediction_cycle_lock_stale_seconds`); read the post-run stamp;
`overlay_refreshed = bool(after and after != before)`. Classify with this
exact vocabulary and no other value:

| receipt `status` | predicate | exit |
|---|---|---|
| `ran` | `"predictions" in report` and `report["status"] == "ran"` and overlay_refreshed | 0 |
| `blocked_readiness` | `"predictions" in report` and `report["status"] == "blocked"` and overlay_refreshed | 0 |
| `blocked_overlay_disabled` | `"predictions" in report` and not overlay_refreshed | 1 |
| `blocked_inputs` | `"predictions" not in report` | 1 |
| `skipped_prediction_cycle_lock` | lock wait exhausted | 75 |

`"predictions" in report` is the exact predicate separating "the cycle
scored" from "never got there": the readiness-blocked path always populates
it (`:167-190`); the trading-mode and feature/model failure paths never do.
A readiness-blocked cycle is a legitimate observation, NOT a scheduler
failure. `blocked_overlay_disabled` exists because `apply_mispricing_alpha`
returns early without writing when the overlay is config-disabled
(`mispricing_alpha.py:542-543`, its only early return) — a green job that
did not refresh the summary is false health and must fail loud.

Write the receipt atomically to
`outputs/polymarket_model_governance/scheduled_paper_cycle.json` with these
keys in this exact order: `status, generated_at_utc, started_at_utc,
duration_seconds, source, scope, lock_attempts, lock_wait_seconds,
cycle_completed, features, predictions, signals_approved, signals_rejected,
paper_signals_published, mispricing_alpha_live_summary_generated_at_utc,
mispricing_alpha_overlay_refreshed, longshot_status, longshot_candidates,
peak_rss_bytes, exit_code, paper_trading_invoked, live_trading_invoked`.
`paper_signals_published = signals_approved or 0` is the honest disclosure
that this run published rows the live container's broker will fill.
`peak_rss_bytes` from `resource.getrusage(RUSAGE_SELF).ru_maxrss * 1024` —
the memory evidence that exists nowhere else in the repo. Both
`*_trading_invoked` are REQUIRED literal false (this process runs no
broker). The receipt is a snapshot artifact (atomic overwrite, no ledger, no
dedup); `_persist_predictions` stays idempotent via its existing
`INSERT OR IGNORE` hash key. Do NOT enrol anything in `ledger_anchor.py`.

**Fail-safe (verbatim and contiguous in the module docstring):** "Nothing
here marks a market measured, changes any M-A/M-B/M-C or maker threshold,
opens any order path, or changes what the live loop does per cycle; the
scheduled job is additive evidence accrual, paper-only, and a failed or
skipped scheduled cycle degrades only the taker alpha lane's evidence
freshness — loudly: a missing, stale, or malformed input leaves the cycle
report without a `predictions` key and exits nonzero, a held
`prediction_cycle` lock exits 75 after a bounded 300-second wait, a disabled
alpha overlay exits 1 rather than reporting success, and in every failure
case the scheduler's `last_success_utc` is not refreshed so
`scheduler_completion_freshness` trips at its registered ceiling."

### 143.3 — CLI

Add `"scheduled-paper-cycle"` to `COMMANDS` after `"paper-cycle"`; dispatch
branch mirroring the contention-exit precedent at `cli.py:613-614`; reuse the
existing `--paper-source` argument; the deployed job passes `websocket`.
The existing `paper-cycle` command stays byte-identical.

### 143.4 — Scheduler wiring

Pattern to match, named: `run_trade_prints`
(`scripts/run_vps_ops_scheduler.sh:620-645`) — job-local `*_STARTED_AT`,
`set -e` subshell backgrounded, `wait_with_safety_pulses`, `stamp_status`
with the started-at argument (do NOT copy `run_maker_study_intraday`, which
omits it and records no duration). Env with clamps near `:41-47`:
`OPS_PAPER_CYCLE_INTERVAL_SECONDS` default 14400 clamped into [3600, 14400];
`OPS_PAPER_CYCLE_TIMEOUT_SECONDS` default 1800 clamped <= 1800;
`OPS_PAPER_CYCLE_ENABLED` default 1. Job function `run_paper_cycle_job`
stamping `paper_cycle`, running `timeout "$PAPER_CYCLE_TIMEOUT" python -m
polymarket_predictive_engine.cli scheduled-paper-cycle --config
"$CONFIG_PATH" --paper-source websocket` (plain `timeout`, never `-k`: the
SIGTERM handler must be allowed to unwind the lock). Disabled path stamps an
intentional skip at exit 0 (WO-114 precedent; visible as
`skip_kind: "intentional"`). Loop block inserted AFTER the `trade_prints`
block and BEFORE `ledger_anchor`, so registered maker/collection lanes stay
ahead and anchoring stays last.

**Cadence justification (4h default).** Six runs/day refreshes the taker
evidence inside every 6h governance window; overrun headroom is wide (1800s
timeout vs 14400s+tick overrun boundary); bridge starvation is bounded and
visible (the bridge loses under ~3% of its 30s cycles while the scheduled
cycle holds the lock, each skip recorded in `live_paper_bridge_cycle.json`).
**Direction rule — deliberately two-sided, NOT collection-style:** config may
run the job more often (floor 3600) or less often (ceiling 14400) and cannot
leave that band. More-often is NOT always safer here: a heavier cadence
starves the bridge lane, raises the 2g cgroup's peak-concurrency risk, and
thrashes the host — the `HARVEST_RETRY_INTERVAL` [900, 3600] clamp is the
registered precedent for two-sided bounding of a heavy job.

**Memory budget — one choice, made explicitly.** The job runs in the
`vps-ops-scheduler` container at its existing 2g limit. The alternatives are
unavailable, not merely less attractive: no bounded cycle variant exists
(above), and routing through the paper-live container would require
`POLYMARKET_PREDICTION_MODE=full`, which changes what the live loop does
every 30s — exactly what the fail-safe forbids. Honest evidence statement:
NO memory measurement for this path exists anywhere in the repository, which
is why `peak_rss_bytes` is a required receipt field from run one. Failure
mode if the budget is exceeded: cgroup OOM SIGKILL → shell exit 137 →
`stamp_status` records the failure and does NOT refresh `last_success_utc` →
`scheduler_nonzero_exit` incident next watchdog tick and
`scheduler_completion_freshness` at the 5h ceiling; SIGKILL cannot be
trapped, so the `prediction_cycle` lock leaks for up to the registered
1800s stale window before `acquire_runtime_lock` reclaims it — a bounded,
self-healing, artifact-visible degradation of the bridge lane, recorded here
as the accepted worst case. NOT built here: a pre-flight memory guard (no
measured basis until `peak_rss_bytes` exists — WO-143c).

**Governance-cadence interaction: none.** `_governance_due_blocks_prediction`
returns False in the deployed `paper-bridge` mode; mutual exclusion with the
6h `governance_refresh` comes from the scheduler's serial job loop;
`refresh_governance` takes its own separate lock and recomputes no features,
predictions, or alpha. The only concurrent runner is the safety pulse, whose
members write no artifact this job writes.

### 143.5 — Watchdog coverage

Exactly one entry added to `REGISTERED_JOB_FRESHNESS_MAX_SECONDS`:
`"paper_cycle": 5 * 60 * 60` (interval + 1h, the house ratio: 6h→7h, 8h→9h,
12h→13h, 24h→25h). One missed run tolerated; two consecutive are an
incident. **An additional artifact-age registration on
`mispricing_alpha_live_summary.json` is NOT added — deliberately:** the
foolability hole (green job, config-disabled overlay, frozen artifact) is
closed at the source by 143.2's `blocked_overlay_disabled` exit-1 predicate,
so the job cannot be green while the artifact is frozen; a second surface
would fire spuriously the moment the owner deliberately quiesced the lane.
Fail-closed conventions per WO-121/129 preserved throughout (absent →
unobserved → stale_unobserved incident; malformed exit codes are failures,
never KeyError; torn status.json is never healthy). Disclosed laundering
path, accepted with the WO-114 precedent: `OPS_PAPER_CYCLE_ENABLED=0` reads
green with a visible intentional-skip trail; any OTHER route to a
green-but-dead lane is a defect.

### Interleaving (S2)

Sole production writer becomes the scheduled job (verify none is enrolled in
`ledger_anchor.DEFAULT_LEDGER_REGISTRY` yourself; if your grep disagrees,
STOP and report): the features_v2 websocket artifacts, `predictions.csv`,
`mispricing_alpha_scores.csv`, `near_miss_learning_candidates.csv`,
`mispricing_alpha_live_summary.json`, the longshot artifacts,
`trade_signals.csv`, `rejected_signals.csv`, plus the two new receipts — all
full-rewrite snapshots through the atomic writers, so the live loop's
concurrent readers see old or new, never torn. Shared and benign:
`paper_trade_readiness.json` (deterministic recompute, last-write-wins,
also written by the broker every 30s) and the SQLite `model_predictions`
table (`INSERT OR IGNORE` under WAL + busy_timeout; the scheduler container
already writes the same DB 6-hourly via refresh_governance). Explicitly NOT
written by the scheduled path: `forward_paper_cycle.json`, the dashboard,
every shadow and portfolio ledger, the profit-target tracker,
`cycle_decision_trace.json`, `agent_status.json`, `signal_cohort_pnl.*`.

### Direction disclosure

No threshold, gate, screen, filter, eligibility rule, or config value moves
in either direction — `risk.minimum_edge`, `edge_field_for_trading`,
`require_alpha_trade_candidate`, every `maker_min_*`, M-A/M-B/M-C, and the
near-miss band must be byte-identical in the diff. Two honest consequences:
(1) the near-miss learning and shadow candidate populations resume growing
(frozen since 07-19 — the stated purpose); (2) TAKER PAPER FILLS RESUME —
`generate_signals` writes `trade_signals.csv`, a default input of the
deployed broker, so approved rows will be filled into the append-only
`paper_fills.csv` and reach `readiness._forward_paper_evidence`, the
live-promotion evidence summary. A restoration of the canonical lane, not a
new path, and every fill stays paper-only — but it is the reason this WO
routes to OWNER merge.

### Tests (enumerated; offline, deterministic; float assertions
`pytest.approx(..., abs=1e-12)`)

In `test_scheduled_paper_cycle.py`: (1) default `scope="full"` byte-identical
behaviour — writes forward_paper_cycle.json, dashboard, broker, shadow, and
every pre-existing `test_paper_broker_foundation.py` test passes UNMODIFIED;
(2) receipt isolation — a sentinel `forward_paper_cycle.json` is
byte-identical after a scoring-only run while both new artifacts exist;
(3) dashboard sentinel untouched; (4) shadow ledger sentinels untouched;
(5) portfolio sentinel untouched and all five skipped blocks read
`skipped_scoring_only`; (6) scoring artifacts written from a websocket
fixture, `model_predictions` idempotent at exactly 2 rows across a re-run;
(7) held foreign lock → `skipped_prediction_cycle_lock`, exit 75,
`lock_attempts >= 2`, sentinel predictions.csv untouched; (8) lock absent
after a happy run; (9) SIGTERM/SystemExit unwinds and releases the lock, and
the previous handler is restored; (10) overlay disabled →
`blocked_overlay_disabled`, exit 1, stale artifact byte-identical;
(11) readiness-blocked → `blocked_readiness`, exit 0, `cycle_completed`
true; (12) inputs missing → `blocked_inputs`, exit 1, no predictions.csv;
(13) `trading.mode: live` → `blocked_inputs`, exit 1, no lock file, no
scoring artifact; (14) receipt key order equals the registered 22-key list
with both invocation literals false and `peak_rss_bytes > 0`; (15) invalid
scope raises ValueError with no lock file; (16) CLI returns 0/75/1 on the
happy/held-lock/overlay-disabled fixtures. In
`test_polymarket_vps_docker.py` (library-only sourcing): (17) clamp band
999999→14400, 60→3600, abc→14400, timeout 99999→1800; (18) static wiring
(command line, `wait_with_safety_pulses "$JOB_PID" paper_cycle`,
`stamp_status` with started-at, loop position after trade_prints and before
ledger_anchor); (19) failure accounting — exit 75 leaves `last_success_utc`
empty with a numeric duration, a following 0 refreshes it, a following 124
records `skip_kind: "overrun"`; (20) `OPS_PAPER_CYCLE_ENABLED=0` stamps an
intentional skip. In `test_degraded_state_watchdog.py`: (21) registration
surface `== 18000`; (22) stale at 18001s opens the incident with
`entity: "paper_cycle"`; (23) fresh at 17999s does not; (24) never-observed
goes unobserved → stale_unobserved incident on the persisted-state second
evaluation; (25) malformed job records fail closed without raising.

### Merge routing

FROZEN → OWNER MERGE after line-audit: the deployed ops scheduler, a
registered watchdog surface, and the canonical cycle's signature whose
`trade_signals.csv` output reaches the live broker and the append-only
`paper_fills.csv` and thence live-promotion evidence. WO-133's indivisibility
rule applies to the whole PR. Tighten-only statement: no gate, threshold,
screen, eligibility rule, or config value changes in either direction; the
only widening is that two observation-only populations resume growing from
zero, which is the stated purpose and is watched by the day-after check.

### 143.6 — Lock-clearer tightening (amended 2026-08-01: now UNCONDITIONAL, and the pre-dispatch host check is withdrawn)

The original registration gated dispatch on a host command
(`docker exec polymarket-paper-live ps -o pid,comm`) to learn whether the
live loop's python is PID 1, because
`_clear_orphaned_same_process_prediction_lock`
(`run_polymarket_local_live_loop.py:784-806`) unlinks any `prediction_cycle`
lock whose payload `pid` equals `os.getpid()`, PID namespaces are
per-container, and the lock file lives on the shared `./outputs` mount — so a
PID collision would let the live container delete a lock the scheduler
container genuinely holds. Verified 2026-08-01: that function checks the lock
NAME and the PID and nothing else, while `runtime_lock.acquire` already
writes `process_started_at_utc` into every payload
(`runtime_lock.py:133`).

The tightening is therefore made UNCONDITIONAL and part of this build, and
the host check is withdrawn as a dispatch gate: before unlinking, the
clearer must ALSO require `payload.get("process_started_at_utc")` to equal
`runtime_lock._PROCESS_STARTED_AT_UTC` (import the module and read the
constant; do not re-derive it). Rationale for making it unconditional rather
than conditional: it is strictly tighter on every host, it removes a
dispatch dependency on a one-off manual observation that nothing re-checks
afterwards (a container restart can change the answer), and it costs
nothing — a genuinely orphaned same-PID lock from a PREVIOUS process is
still reclaimed at acquisition time by
`_same_pid_lock_predates_current_process` (`runtime_lock.py:66-86`), which
is the mechanism that actually owns that case. Same-process orphans, the
only case this clearer legitimately serves, always carry the current
process's stamp and so still clear.
`scripts/run_polymarket_local_live_loop.py` is therefore ADDED to this WO's
touched-file list (one predicate plus its test).

Tests: (a) a lock payload with the current PID and the current
`process_started_at_utc` is still cleared (the legitimate same-process
orphan); (b) a payload with the current PID but a DIFFERENT
`process_started_at_utc` — the cross-container collision — is NOT cleared
and reports a distinct status; (c) a payload missing the field entirely is
NOT cleared (fail-closed); (d) the existing foreign-name, malformed-pid, and
other-pid paths are unchanged.

### Day-after check

Pre-deploy, the orchestrator records in this status line the current
`generated_at_utc` of `mispricing_alpha_live_summary.json` (expected
2026-07-19T22:22) and the row count of `mispricing_alpha_scores.csv`. After
one deployed cadence (<= 4h), from the telemetry branch: (1)
`jobs.paper_cycle` exists with exit 0, fresh `last_success_utc`, numeric
duration, `skip_kind: "none"`; (2) `scheduled_paper_cycle.json` status
`ran`/`blocked_readiness`, `cycle_completed: true`,
`mispricing_alpha_overlay_refreshed: true`, `predictions >= 1`, both
invocation literals false, and `peak_rss_bytes` recorded (copied into this
status line as the WO-143c input); (3) the live summary's stamp has advanced
past 2026-07-19T22:22; (4) the freshness evaluation lists `paper_cycle`
fresh at ceiling 18000 with no incident; (5) bridge health: the heartbeat
and `live_paper_bridge_cycle.json` younger than 2 minutes — if
`skipped_existing_prediction_cycle` persists longer than the cycle duration,
the lock leaked and the WO is REVERTED, not tuned; (6) `lock_attempts` stays
small (1-2) from the second cycle onward — a sustained rise means the
cadence or wait budget is re-registered from measurement.

### Orchestrator resolutions (2026-08-01, recorded at registration)

Scoring-only scope adopted (shadow forwarding deferred to WO-143b); 4h
cadence with the two-sided [1h, 4h] clamp adopted; both receipt artifacts
kept (scheduler receipt + redirected cycle report — distinct consumers);
`OPS_PAPER_CYCLE_ENABLED=0` keeps the WO-114 intentional-skip precedent;
owner-merge routing confirmed on the taker-fills-resume consequence; the
PID-1 pre-dispatch check stands as registered above; the bridge duty cycle
self-measures via `lock_attempts` plus day-after check (6); WO-142's
day-after check is re-armed against the artifacts this job refreshes once
this WO deploys.

### 143.7 — Review-round amendment (registered 2026-08-01; **the fix-round dispatch made against this text preceded its merge and therefore fails the ancestry test in the GLOBAL RULE above — retracted claim, corrected 2026-08-01**)

Codex's review of PR #417 (head `91c35cd`) raised seven findings. Each was
re-verified against the code by the orchestrator before being registered
here; findings are adopted on the evidence, not on the reviewer's say-so.

**Not adopted — "unregistered live-loop amendment" (P1).** The finding is
correct about the tree it reviewed and wrong about the repository. §143.6 was
amended to UNCONDITIONAL and the PID-1 pre-dispatch check withdrawn in
`51ffb42` (PR #414, merged). PR #417's head is based on `ab16ee9`, which
predates that merge, so the registered text Codex read out of the PR tree
still carried the conditional wording. **The required action is a rebase onto
`main`, not a code change**: the branch must carry the registration that
authorises its own diff. No file is removed from the commit.

The remaining six are CONFIRMED and in scope for the fix round.

**(a) The new artifact must carry the invocation flags.** `AGENTS.md` L131-135
requires every new artifact to state `paper_trading_invoked=false` and
`live_trading_invoked=false`. `scheduled_paper_cycle_report.json` is a new
artifact. Today those two literals are set only inside the
`skipped_existing_prediction_cycle` branch; every other path that writes the
artifact — the success path, the `trading_mode` block, and the
feature/model-load block — omits them. Add both to the initial `report` dict
at construction so every writing path carries them, leaving the lock-skip
branch's explicit re-statement in place.

**Registered exception to §143.1 (added 2026-08-01 after review).** That dict is
shared by BOTH scopes, and `write_json` sorts keys, so this addition also puts
two new keys into `forward_paper_cycle.json` on every full-scope path — which
§143.1's "byte-identical on every path" clause otherwise forbids. The two
requirements cannot both hold literally. **`AGENTS.md` L131-135 wins**, and
§143.1 is hereby amended to read "byte-identical except for the two invocation
flags, which every artifact must carry." The build must not resolve this by
skipping the flags on the full path. The 22-key scheduler receipt already
carries both; that is a different artifact and does not discharge this
requirement.

**(b) A zero-prediction cycle must not be classified as success.**
`scheduled_paper_cycle.py` classifies on `has_predictions = "predictions" in
report` — a KEY-PRESENCE test. `build_features_v2` reads its inputs through
`read_csv_rows`, which returns `[]` for a missing or empty
`websocket_market_features.csv` rather than raising (a MALFORMED file is a
separate case: `csv.DictReader` with `errors="replace"` yields garbage-keyed
rows rather than an empty list, and must be handled as its own input class),
so `predictions` is `0`,
the key is present, and the run is classified `ran`/`blocked_readiness` at
exit 0. `apply_mispricing_alpha` compounds it: line 554 is its ONLY early
return between the overlay-enabled check and the live-summary write at :1068,
so a zero-row run still stamps a FRESH `mispricing_alpha_live_summary.json`
and `overlay_refreshed` reads true. The job therefore refreshes
`last_success_utc` while the scoring lane has no current observations — the
same fail-open class as the odds-preflight "intentional skip" (OPS-5).
Require a POSITIVE prediction count for the `ran`/`blocked_readiness`
classifications, and validate the websocket observation age before classifying
completion; otherwise classify `blocked_inputs` at exit 1.

**The ceiling, named (threshold derived and registered 2026-08-01; basis
below).** Authorization for this value is the owner's merge of the pull request
carrying this registration — not any statement attributed to the owner here. Register a NEW setting
`scheduled_paper_cycle.max_websocket_observation_age_seconds`, **default
1800**, tighten-only via config. **This adds an eleventh file to WO-143's
touched list — `polymarket_predictive_config.example.yaml` (repository ROOT, not
`config/`) — and strikes "the example config" from the preamble's do-not-touch
sentence.** A setting absent from the example config is not configurable in any
documented way, so "tighten-only via config" would otherwise be false. Basis: 6x the 300s `websocket_max_gap_seconds`
reporting target, and well inside the job's 4h default cadence. The build must
NOT reuse `operating_state_slos.websocket_max_gap_seconds` —
`REGISTERED_SLO_TARGETS` (`operating_state.py:44-52`) is annotated "never read
by a gate, broker, sizing rule, or order path", so wiring it in would make a
reporting-only block gate-bearing. It must NOT reuse
`mispricing_alpha.max_websocket_quote_enrichment_age_seconds` either — that
bounds per-row quote/prediction pairing (`mispricing_alpha.py:363`), not feed
freshness, and is unset in config.

**Fail-closed on bad inputs (added after review).** The age comparison is the
repo's known fail-open shape: `safe_float("nan")` returns NaN
(`utils.py:373-379`) and `nan > ceiling` is `False`, so a corrupt timestamp
would classify as FRESH — the exact hole this item exists to close. A missing,
empty, unparseable, or **non-finite** observation timestamp classifies
`blocked_inputs` at exit 1, never fresh. One test per input class.

**(c) Detect the disabled overlay BEFORE signals are published.**
`_run_paper_cycle_unlocked` runs `generate_signals` unconditionally — it is
not skipped under `scoring_only` — and `generate_signals` writes
`trade_signals.csv` (`strategy.py:513`). When `mispricing_alpha.enabled` is
false, `apply_mispricing_alpha` returns at :554 without writing the summary,
so the wrapper only learns the overlay was disabled AFTER the signal file is
already on disk, and its exit 1 cannot retract it. With the overlay absent
`generate_signals` drops THREE named alpha-dependent gates, so the published
rows can include raw `predictive_directional` candidates that the live
container's broker subsequently fills: `alpha_edge` is None (no
`edge_lower_bound` column from `write_predictions`) so the alpha-candidate
rejection at `strategy.py:426-438` cannot fire; `require_same_category_labels`
(`:221-224`) and `require_positive_cohort` (`:228-230`) are both
`alpha_enabled and ...` -> False, disabling the gates at `:456-470` and
`:471-485`. Only `risk_decision` survives, and rows are stamped
`"predictive_directional"` at `:303`. Everything remains paper — no
live path exists or is added — but this is a scheduled job publishing signals
past gates that would otherwise have rejected them, which the "do not loosen
... controls to manufacture activity" rule forbids. Detect the
disabled-or-unrefreshed overlay BEFORE `generate_signals` is called, and
publish no approved-signal file on that path.

**(d) Clamp the paper-cycle timeout above zero.** `run_vps_ops_scheduler.sh`
validates `OPS_PAPER_CYCLE_TIMEOUT_SECONDS` with a digits check and an upper
bound (`-le 1800`) only. `0` passes both and reaches `timeout`, where GNU
coreutils documents "A duration of 0 disables the associated timeout" — an
unbounded scoring cycle holding the `prediction_cycle` lock and stalling the
serial scheduler behind it. This is inconsistent within the same commit:
`PAPER_CYCLE_INTERVAL` is clamped two-sided (`-ge 3600`, `-le 14400`). Add a
positive lower clamp on the timeout, matching the interval's shape.

**(e) `lock_wait_seconds` must measure lock waiting only.** It is computed as
`time.monotonic() - started_monotonic` AFTER the retry loop exits, so on
every first-attempt acquisition it contains the whole feature/model/scoring
runtime and lands within rounding of `duration_seconds`. The field exists to
evidence bridge contention; as written it reports a lock wait on runs where
none occurred. Accumulate only time spent on attempts that returned
`skipped_existing_prediction_cycle` — a first-attempt acquisition reports
approximately zero.

**(f) `shadow_candidates_forwarded` must not claim a forward that did not
happen.** Under `scoring_only` the shadow update is deliberately skipped
(`shadow_cohort = {"status": "skipped_scoring_only"}`) while the shared
`report.update` still reports `len(longshot_candidates)`. This is the SAME
defect Codex raised independently as F4 on PR #416, and it is registered
once, for all callers, in §143b.1. WO-143's fix round implements the
`scoring_only` case of that contract; it does not define it.

**Fail-safe sentence.** Nothing in this amendment marks a market measured,
changes any M-A/M-B/M-C or `maker_min_*` threshold, opens or enables any
order path, or loosens any gate; every item is strictly tightening — fewer
runs classified successful, fewer signals published, a bounded timeout where
one could be disabled, and telemetry that claims less than it does today
rather than more.

**Tests (enumerated, additive to §143.2's set).** (1) the artifact written on
the success path contains literal `paper_trading_invoked=false` and
`live_trading_invoked=false`, and so does the artifact written on each
blocked path; (2) an absent `websocket_market_features.csv` yields
`blocked_inputs` at exit 1 and does NOT refresh `last_success_utc`; (3) an
empty-but-present websocket file yields the same; (4) a websocket file older
than the registered ceiling yields the same even when it parses and scores;
(5) with `mispricing_alpha.enabled: false`, `trade_signals.csv` is NOT
written and its pre-existing on-disk content is unchanged, and the status is
`blocked_overlay_disabled` at exit 1; (6) `OPS_PAPER_CYCLE_TIMEOUT_SECONDS=0`
resolves to the positive default, asserted by sourcing the scheduler under
`OPS_SCHEDULER_LIBRARY_ONLY`; (7) a first-attempt acquisition reports
`lock_wait_seconds` under one second while `duration_seconds` reflects the
real runtime; (8) a contended run that succeeds on its second attempt reports
`lock_wait_seconds` at approximately one sleep interval, not the full
duration.

**Day-after check:** on the first deployed day `scheduled_paper_cycle.json`
shows `lock_wait_seconds` near zero on uncontended runs while
`duration_seconds` carries the real cycle cost; no run is recorded `ran` with
`predictions: 0`; and `trade_signals.csv`'s modification time advances only
from the live container, never from a `blocked_overlay_disabled` scheduled
run.

### Named follow-ons, NOT built here

- **WO-143b — serialise the shadow-cohort writer.** `update_shadow_cohort_evidence`
  requires the `prediction_cycle` lock by its own docstring
  (`shadow_cohort.py:863-868`) but the live loop's tick path calls it without
  one — a pre-existing same-process race today, and the reason the scheduled
  slot must not forward candidates into the shadow updater until this lands.
- **WO-143c — memory decision.** After one deployed day of `peak_rss_bytes`,
  the owner decides: keep 2g, add a pre-flight guard, or raise
  `VPS_OPS_MEM_LIMIT`. Stays prose until the number exists.


## WO-143b — Serialise the shadow-cohort writer against the live tick path — `queued` (registered 2026-08-01 09:12 UTC; **the first build branch was cut at 09:06 UTC, BEFORE that registration merged — registration-before-dispatch was NOT satisfied for the initial dispatch**, corrected 2026-08-01 by independent review; shadow ledger is anchor-enrolled → OWNER MERGE after line-audit)

**Provenance correction (2026-08-01).** This header previously read "registered
2026-08-01 BEFORE dispatch". That claim is **not supported** and is retracted.
`claude/wo143b-shadow-lock` was created at 09:06:05Z from `ab16ee9`; `51ffb42`,
the commit registering this WO, merged at 09:12:15Z — 6m10s later. At `ab16ee9`
the only mention of WO-143b in this document is a single line under *"Named
follow-ons, NOT built here"*, carrying no scope, no fail-safe sentence and no
test list. The branch therefore requires a rebase onto a `main` containing its
own registration, for the same reason PR #417 did.

**The defect exists today, independent of WO-143.**
`update_shadow_cohort_evidence` states its own contract in its docstring
(`shadow_cohort.py:863-868`): *"Callers MUST hold the `prediction_cycle`
runtime lock; this function does not acquire it because that lock is
intentionally non-reentrant."* The full paper cycle honours that
(`paper_cycle.py:139`, inside the lock). The deployed live loop does NOT:
its per-tick path reaches the same function through
`mark_portfolio_and_render_dashboard` → `_lightweight_shadow_maintenance`
(`run_polymarket_local_live_loop.py:759`, `:707-723`, invoked `:2040-2045`)
without holding that lock, every ~30 seconds. Today the two writers collide
only during a resource-guard degraded fallback (same process, different
thread), which is why it has not yet corrupted anything. It is nonetheless a
documented-contract violation on a writer of `shadow_fills.csv`, which is
enrolled **append_only** in `ledger_anchor.DEFAULT_LEDGER_REGISTRY`
(`ledger_anchor.py:54-55`) — a lost update there breaks the tamper chain and
costs a re-genesis, the exact failure WO-115 spent days undoing.

**Why it is registered now.** WO-143 gives the full paper cycle a scheduled
owner in a DIFFERENT container. Its registered scope is scoring-only
precisely so it never calls this function — but that restriction is a
workaround for this defect, and it is the reason WO-143 cannot restore the
full cycle's forwarding of longshot candidates into the shadow updater.
Fixing this unblocks that.

**Scope.** *(Amended 2026-08-01 — see §143b.1. The original single-file scope
below is SUPERSEDED: F4's caller-honesty contract requires edits at five call
sites across four further files, three of them under `scripts/`, and F1's
reversal adds a settlement wall-clock budget. The authoritative touched-file
list for a line audit is §143b.1's, not this paragraph's. In particular the
sentence "Do NOT ... touch either caller" below no longer holds.)*

File: `src/polymarket_predictive_engine/shadow_cohort.py` and its
tests. Give `update_shadow_cohort_evidence` its OWN internal lock
(`shadow_cohort`, distinct from `prediction_cycle` so the non-reentrancy
note stays true and no caller deadlocks), acquired at entry and released at
exit. When the lock is held by another writer the function performs NO
writes and returns `{"status": "skipped_shadow_lock_held", ...}` — the
existing runtime-lock skip pattern, fail-closed: a skipped update loses one
tick of evidence, never a ledger row. Update the docstring so the contract
it states is the contract it enforces. Do NOT change what the function
computes, do NOT touch either caller, and do NOT change the ledger
enrolment.

**Fail-safe sentence.** Nothing here marks a market measured, changes any
M-A/M-B/M-C or `maker_min_*` threshold, opens any order path, or alters what
either caller does per cycle; the only behavioural change is that a
concurrent second writer now declines to write instead of racing, and a
declined write is recorded in the returned status rather than being silent.

**Tests (enumerated).** (1) an uncontended call writes exactly as today
(regression: byte-identical `shadow_fills.csv` and `shadow_positions.csv`
against the pre-fix build on the same fixture); (2) with the `shadow_cohort`
lock held by a foreign payload, the call writes NOTHING and returns
`skipped_shadow_lock_held`; (3) the lock is released on the happy path;
(4) the lock is released when the body raises (the exception still
propagates); (5) holding the `prediction_cycle` lock does NOT block this
function — the two locks are independent, so the full paper cycle's existing
in-lock call still succeeds; (6) a malformed/stale `shadow_cohort` lock
payload is reclaimed per the existing `runtime_lock` stale-timeout rules
rather than wedging the lane permanently.

**Day-after check:** after deploy, `shadow_fills.csv` continues to grow on
the live tick path with no `blocked_broken_chain` from `anchor_ledgers`, and
any `skipped_shadow_lock_held` occurrences appear in the cycle artifacts
rather than as silent gaps.

### 143b.1 — Review-round amendment (registered 2026-08-01; **the fix-round dispatch made against this text preceded its merge and therefore fails the ancestry test in the GLOBAL RULE above — retracted claim, corrected 2026-08-01**)

Codex's review of PR #416 raised four findings. Each was re-verified against
the code by the orchestrator before being registered here.

**F1 — settlement reclaiming the lock while a writer is active (P1):
REACHABLE under shipped defaults. REVERSED 2026-08-01 by independent review;
this item IS scheduled.**

The original dismissal is retracted. It bounded a fully-timing-out settlement
pass at `settlement_max_positions_per_cycle: 25` x
`settlement_request_timeout_seconds: 20` = ~500s against the 1800s stale
window and called that a 3.6x margin. **That assumed one HTTP request per
position.** `shadow_cohort.py:494-522` issues up to three:

- crypto path — `shadow_cohort.py:504` reaches
  `crypto_updown_settlement.py:249`, which loops over TWO providers, each in
  its own `try/except` so both are attempted, each an
  `urlopen(..., timeout=timeout_seconds)` (`:175`, `:215`). Worst case 2x20 =
  40s/position -> 25x40 = **1000s**.
- non-crypto path — `shadow_cohort.py:509` `_fetch_resolution_market` calls
  `fetch_gamma_market` (`resolution_collector.py:49`, 20s) then
  `_public_search_market_by_slug` (`shadow_cohort.py:410-433`), which iterates
  2 unique queries at 20s each -> 3x20 = 60s/position -> 25x60 = **1500s
  against the 1800s window, a 1.2x margin.**

The bound is also wrong **in kind**: `urllib.request.urlopen(timeout=N)` is a
per-socket-operation timeout, not a total deadline, so `response.read()`
against a trickling server never trips it and a single call is unbounded in
wall-clock terms. The WO-143b lock wraps the WHOLE function, not just
`_settle_due_positions`, so the read/rewrite of the anchor-enrolled ledgers is
inside the protected region, and it passes no `stale_after_seconds`, taking
`runtime_lock.py:203`'s 1800.0 default.

Failure scenario: one degraded Gamma/Binance day, 25 due positions at ~55s
each, the pass exceeds 1800s, a second writer reclaims the lock.
`shadow_positions.csv` is a full `write_csv` rewrite from a stale in-memory
snapshot (lost update) and both writers append `SELL_SHADOW` rows to the
append-only, anchor-enrolled `shadow_fills.csv` — precisely the re-genesis
harm WO-143b exists to prevent.

**Scope for this item (corrected 2026-08-01 after second review — the first
version of this scope did not close the race it describes).**

Two gaps in the naive fix, both real:

1. **A between-iterations budget cannot bound an unbounded single call.** A
   monotonic budget on `_settle_due_positions` is only checked between loop
   iterations (`shadow_cohort.py:546-548`), but this same item establishes that
   `urlopen(timeout=N)` is per-socket, so ONE position can stall past the whole
   budget and the loop never regains control to notice.
2. **The lock covers the whole function, not just settlement.** After
   `_settle_due_positions` returns (`shadow_cohort.py:890`) the mark-to-market
   loop, `_candidate_rows`, the full `write_csv` rewrite of
   `shadow_positions.csv`, the `shadow_fills.csv` append and
   `_write_shadow_pnl_history` all still run inside the lock, unbudgeted.

Therefore the registered invariant is **`stale_after_seconds` > a budgeted
WHOLE-FUNCTION pass**, not a budgeted settlement pass. Implement all three:

- a monotonic wall-clock budget on `_settle_due_positions` that abandons
  remaining positions with a partial, fail-closed status and NO partial write;
- **a progress-derived heartbeat** (specified below) so a live holder is never
  judged stale — the only mechanism that survives a genuinely unbounded single
  read, and therefore the primary one;
- an explicit `shadow_cohort_stale_after_seconds` per the named constants and
  registered ordering above.

Budget and stale window are registered together so the relationship is
auditable; neither is derived from the other at runtime.

**Heartbeat specification (added 2026-08-01 after second review — the first
wording traded reclaim-too-early for wedge-forever).** "Re-stamp while the
function is alive" is not sufficient: a *crashed* holder is safe (the thread
dies, the stamp ages, reclaim works), but a *hung* holder — blocked in exactly
the unbounded `urlopen` read this item establishes as possible — would be
heartbeaten by a timer thread forever and the lane would stop silently, which is
the failure WO-143 exists to prevent. All four are registered:

**Named constants (A1 — corrected 2026-08-01; the first version named none and
would have been REJECTED by the S8 checklist this same change registers).** All
four are literals with stated bases, all tighten-only:

- `settlement_budget_seconds: 900` (15 min). Basis: measured worst case for 25
  positions at up to 3 calls x 20s is 1500s, so 900s guarantees the pass
  abandons well before the stale window.
- `remainder_budget_seconds: 300` (5 min) for the post-settlement phase.
  Basis: a 17 MB rewrite plus appends on a loaded 2-core VPS, with headroom.
- `heartbeat_margin_seconds: 120`.
- `heartbeat_cap_seconds: 1800` (30 min). Basis: strictly greater than
  `900 + 300 + 120 = 1320` (the sum of the phase budgets, so a legitimately slow
  pass is never cut off) and strictly less than the 2400s stale window (so the
  beat always stops before the lock could be reclaimed under it).
  **Added 2026-08-01 after review: the previous text used `heartbeat_cap` in the
  ordering while requirement 2 separately DEFINED it as `budget + remainder +
  margin`, i.e. exactly 1320 — which made the registered ordering
  `1320 < heartbeat_cap` unsatisfiable and would have failed test (10) against
  the registered values themselves. It was also a fifth constant with no literal
  and no basis, so F1 still failed A1 after the fix that was supposed to close
  A1.**
- `critical_section_max_seconds: 120`. Basis: with the temp-file requirement
  below, the critical section is `os.replace` calls only — milliseconds — so
  120s is pure headroom for a stalled filesystem, and a bounded wedge beats an
  unbounded one.
- `shadow_cohort_stale_after_seconds: 2400` (40 min). Basis: strictly greater
  than `heartbeat_cap_seconds` so the cap can never sit at or above the stale
  window.

**Registered ordering, validated in full by test (10) — FOUR relations:**

1. `settlement_budget_seconds + remainder_budget_seconds + heartbeat_margin_seconds  <  heartbeat_cap_seconds`  — i.e. `1320 < 1800`
2. `heartbeat_cap_seconds  <  shadow_cohort_stale_after_seconds`  — i.e. `1800 < 2400`
3. **`heartbeat_cap_seconds + critical_section_max_seconds  <  shadow_cohort_stale_after_seconds`  — i.e. `1800 + 120 = 1920 < 2400`**
4. every constant is positive and finite.

**Relation 3 was added 2026-08-01 after review and is the one a later edit would
silently break.** The effective maximum hold is `heartbeat_cap +
critical_section_max`, because the cap does not fire inside the critical
section. A subsequent edit setting `critical_section_max_seconds: 700` gives
`1800 + 700 = 2500 > 2400` and **re-opens the exact reclaim-mid-critical-section
hole that the critical section exists to close — while a three-relation test (10)
would still pass.** The registered values already satisfy it; the relation is
registered so they cannot drift apart unnoticed.

**Naming note:** `settlement_budget_seconds` ABANDONS remaining work when
exceeded; `remainder_budget_seconds` is advisory and only sizes the constants
above. Two constants named "budget" with opposite enforcement is a trap, so the
advisory one is documented as `remainder_budget_seconds (sizing only, never
enforced at runtime)`.

1. **Progress-derived, never timer-derived, and defined across ALL phases.**
   Beat only when a monotonically increasing progress counter advanced since the
   previous beat. **The counter is NOT the settlement position counter**
   (`shadow_cohort.py:543`) — that stops advancing permanently once
   `_settle_due_positions` returns at `:890`, which would silence the heartbeat
   for exactly the remainder phase that performs the ledger writes, and a
   reclaim there is the lost-update corruption F1 exists to prevent. The counter
   advances at every phase boundary and every ledger-write step.
2. **Heartbeat lifetime cap, CONDITIONAL.** Past `heartbeat_cap_seconds` the
   heartbeat stops and normal stale reclaim resumes — **except while inside the
   ledger-write critical section**, subject to 2b. An unconditional cap fires
   mid-`write_csv` if `remainder_budget_seconds` was estimated low, causing the
   corruption being prevented.
2b. **The ledger-write critical section is BOUNDED and SHRUNK** (corrected
   2026-08-01 after review: an unconditional never-abandon carve-out simply moved
   the wedge from a hung `urlopen` to a hung `write_csv`, and `./outputs` is a
   shared bind mount, so a volume stall or full disk would beat forever and wedge
   the lane permanently with no bound at all):
   - **Shrink it.** The ledger rewrites build their content in a temp file
     **outside** the section; the section is the `os.replace` calls only. This is
     the same temp-plus-`os.replace` discipline already mandated for the
     heartbeat write, and it makes the section near-instantaneous by
     construction, dissolving the problem rather than capping it.
   - **Bound it.** Past `critical_section_max_seconds` the beat stops with a
     loudly recorded overrun. A bounded wedge beats an unbounded one.
3. **Do NOT re-stamp `acquired_at_utc`.** It is read by `_lock_age_seconds`
   (`runtime_lock.py:44-49`) and validated by `_valid_lock_payload` (`:52-63`);
   re-stamping makes the field's name false for every other reader. Add a
   separate `heartbeat_at_utc`, and have the stale check use
   `max(acquired_at_utc, heartbeat_at_utc)`.
4. **Record `heartbeat_count` and `last_progress_at_utc`** in the payload so a
   wedge is diagnosable rather than looking like a normal long hold.

**Mechanical constraint.** `_try_acquire` (`runtime_lock.py:89-101`) publishes
via `os.link`, which cannot overwrite an existing lock, so there is no API today
for updating a held lock's payload. The heartbeat write MUST be temp-file plus
`os.replace`. **Unlink-then-recreate is explicitly forbidden** — it opens a
window in which another process can legitimately acquire.

**Settlement starvation — registered 2026-08-01 after review.** A 900s budget
at ~60s/position reaches roughly the first 15 of 25 due positions. There is no
rotation: `_settle_due_positions` iterates in file order every pass and
`_should_check_settlement` (`shadow_cohort.py:402-407`) gates only on close time
plus grace, storing no last-checked timestamp. On a degraded day the tail is
therefore **never reached on any pass** — those positions age past
`maximum_holding_hours` and close as `shadow_time_exit` at a stale mark instead
of `shadow_clean_settlement` at the true 0/1 outcome. That is a systematic
distortion of shadow P&L, not deferred work, and "fail-closed" covers only the
write. **Required: a per-position `last_settlement_check_utc` with oldest-first
ordering (or an equivalent rotating offset), plus a
`settlement_positions_abandoned` count emitted into the artifact and named in
the day-after check.**

**Tests for F1 (enumerated; additive to the six below).** (7) with a stubbed
provider that blocks past the budget, the pass returns the partial fail-closed
status, leaves remaining positions unprocessed, and writes NO partial ledger
row; (8) the lock is released after a budget-expired pass; (9) a progressing worker is
heartbeaten such that a concurrent reclaim attempt during a long-running pass
does NOT acquire; **(9b) its mirror — a HUNG worker whose progress counter stops
advancing ceases to be heartbeaten and becomes reclaimable on schedule; this is
the test that distinguishes the design from a permanent wedge; (9c) the
heartbeat write never unlinks the lock file (assert no window in which the path
is absent);** (10) a load-time validator rejects any
configuration violating the FULL registered ordering — all three relations, not
only the outer one — rather than silently inverting; **(10b) the heartbeat
continues through the ledger-write critical section after `heartbeat_cap_seconds`
would otherwise have fired, but stops at `critical_section_max_seconds` with a
recorded overrun; (10c) the ledger content is built in a temp file outside the
critical section, so the section contains only `os.replace` calls; (10d) with a
budget that reaches only the first N of M due positions, the NEXT pass starts
with the previously unreached ones (no starvation), and
`settlement_positions_abandoned` is emitted;** (11) a settlement pass shorter than the budget
is byte-identical to today.

Citation correction: config line 1311 is `settlement_request_timeout_seconds:
20`; line 1312 is `settlement_max_positions_per_cycle: 25`. The original text
transposed them.

**F2 — the obsolete structural test (P2): CONFIRMED.**
`tests/polymarket_predictive_engine/test_shadow_cohort.py:127`
(`test_all_source_shadow_update_callers_hold_prediction_cycle_lock`)
structurally enforces the caller-holds-`prediction_cycle` contract that
WO-143b deliberately replaces with an internal lock. It passes today only
because no caller yet exercises the new contract; a valid new caller relying
on the internal lock would be rejected by a test asserting a rule the WO
retired. Retarget it: the invariant worth keeping is that no caller performs
an UNGUARDED write, and the internal `shadow_cohort` lock is now one of the
accepted guards. Do not simply delete it — that scan is the only structural
defence against a future unguarded caller.

**Widen the scan root too (added 2026-08-01 after review).** The scan currently
roots at `Path("src")` (`test_shadow_cohort.py:162` on `b3ecf0b`) — which is
exactly WHY it never caught `scripts/run_polymarket_local_live_loop.py:723`,
the unguarded call that motivated this entire work order. **Three of the five
call sites in F4's list live under `scripts/` and would stay invisible after a
retarget alone.** `Path("src")` is also CWD-relative, so it silently covers
zero files if pytest runs from elsewhere — the same hermeticity family as F3.
Root the scan at `Path(__file__).resolve().parents[2]` over BOTH `src/` and
`scripts/`, and assert the scan visited a non-zero file count.

Citation note: `test_shadow_cohort.py:127` resolves to the test definition on
`b3ecf0b` (PR #416's head); on `main` the same def is at line 118.

**F3 — test hermeticity (P2): CONFIRMED.** The byte-identity regression test
(WO-143b test 1) resolves its baseline through git object `ab16ee9` and
silently SKIPS when that object is absent, which is the normal state of a
shallow CI checkout or a worktree. A regression test that skips itself on the
machines that run it is not a regression test. Replace the git-object
dependency with a committed fixture so the assertion is hermetic and offline,
per `docs/ENGINEERING_STANDARDS.md`'s recorded-reality fixture rule.

**F4 — a skipped shadow update must not be reported as a forward (P1):
CONFIRMED, and broader than the PR.** WO-143b adds a
`skipped_shadow_lock_held` return in which the function writes nothing. Every
caller that reports a forwarded-candidate COUNT computes it from its own
input list and never consults the returned status, so a contended call is
recorded as a successful forward. Codex raised the same defect independently
against WO-143's `scoring_only` skip (§143.7(f)); that is the second
instance, so the contract is registered once, here, for all callers.

**The caller-honesty contract.** A caller that reports how many candidates
reached the shadow updater MUST derive that number from the update's outcome,
not from the size of what it passed in. Concretely: **when the update is
skipped — whether because the callee returned a `skipped_*` status OR because
the caller elected not to call it at all** — the reported forwarded count is
`0`, and the caller's artifact additionally carries the skip status verbatim so
the skip is visible rather than inferred from a zero.

**Wording corrected 2026-08-01 after review.** The original said only "when the
returned status is any `skipped_*` value". That antecedent is never satisfied on
the very path §143.7(f) targets: under `scoring_only`,
`update_shadow_cohort_evidence` is NEVER CALLED and
`{"status": "skipped_scoring_only"}` is synthesized locally, so there is no
returned status and a literal implementation would leave
`shadow_candidates_forwarded` at the full input count — the exact defect (f)
exists to fix. Call sites in scope, all
verified present at registration:

- `src/polymarket_predictive_engine/paper_cycle.py:183` (`main`) — the
  `longshot_bias.shadow_candidates_forwarded` field, covering both the
  lock-held skip and WO-143's `scoring_only` skip;
- `src/polymarket_predictive_engine/longshot_bias.py:426`;
- `scripts/run_polymarket_local_live_loop.py:723`;
- `scripts/run_alpha_candidate_shadow_evidence.py:120`;
- `scripts/run_promoted_rule_shadow_scan.py:769`.

A call site that reports no count needs no change beyond surfacing the
returned status.

**Fail-safe sentence.** Nothing in this amendment marks a market measured,
changes any M-A/M-B/M-C or `maker_min_*` threshold, opens any order path, or
changes what `update_shadow_cohort_evidence` computes; the only behavioural
change is that a skipped update is reported as a skip instead of as a
forward, which strictly reduces what the artifacts claim.

**Tests (enumerated, additive to WO-143b's set).** (1) with the
`shadow_cohort` lock held, `run_paper_cycle` reports
`shadow_candidates_forwarded: 0` and surfaces `skipped_shadow_lock_held`,
asserted with a NON-EMPTY candidate list so the count is not incidentally
zero; (2) the same assertion for each remaining call site listed above;
(3) the uncontended path still reports the full count, so the honesty fix
does not zero a real forward; (4) the retargeted structural scan still
REJECTS a caller that writes with neither the `prediction_cycle` guard nor
the internal lock; (5) the retargeted scan ACCEPTS a caller relying solely on
the internal `shadow_cohort` lock; (6) the byte-identity regression runs from
a committed fixture and does not skip when git history is unavailable —
assert explicitly that it did not skip.

**Touch ONLY these files** (`git diff --stat` must show exactly these fourteen —
thirteen if the runtime-lock tests fold into `test_shadow_cohort.py`).
This list is authoritative for a line audit and SUPERSEDES the single-file
Scope paragraph in the parent WO:

- `src/polymarket_predictive_engine/shadow_cohort.py` (internal lock, F1 budget
  + heartbeat + explicit `stale_after_seconds`, F4 status surfacing)
- `src/polymarket_predictive_engine/paper_cycle.py` (F4 count only)
- `src/polymarket_predictive_engine/longshot_bias.py` (F4)
- `scripts/run_polymarket_local_live_loop.py` (F4)
- `scripts/run_alpha_candidate_shadow_evidence.py` (F4)
- `scripts/run_promoted_rule_shadow_scan.py` (F4 — status surfacing only; its
  `candidates` count is rule-scan output, not a forwarded-count claim, and does
  not change)
- `polymarket_predictive_config.example.yaml` (**repository ROOT — there is no
  `config/polymarket_predictive_config.example.yaml`; `config/` holds only the
  live-approval files**) — F1 budget and `stale_after_seconds` settings
- `src/polymarket_predictive_engine/runtime_lock.py` (heartbeat support).
  **Shared surface:** `runtime_lock` backs `prediction_cycle`, `shadow_cohort`
  and every other lane, so a heartbeat defect wedges all of them. The heartbeat
  is therefore **opt-in per lock** — only `shadow_cohort` enables it — and is
  NOT a default behaviour change. *Fail-safe for this file: a lock that does not
  opt in behaves byte-identically to today, and an opted-in lock whose heartbeat
  fails degrades to today's plain stale-timeout behaviour rather than to an
  unreclaimable one.* (WO-143's preamble forbids touching `runtime_lock.py`;
  that constraint binds WO-143, not WO-143b.)
- `tests/polymarket_predictive_engine/test_shadow_cohort.py` (F2 retarget +
  widen, F3 fixture, F1 tests)
- NEW `tests/polymarket_predictive_engine/fixtures/` byte-identity fixture (F3)
- `tests/polymarket_predictive_engine/test_paper_broker_foundation.py` (F4)
- `tests/polymarket_predictive_engine/test_longshot_bias.py` (F4)
- NEW test file(s) covering the two `scripts/` call sites that have none

**Day-after check:** after deploy, any `skipped_shadow_lock_held` in the
cycle artifacts is accompanied by `shadow_candidates_forwarded: 0` in the
same artifact, `shadow_fills.csv` row growth continues to match the
uncontended forward counts, and no settlement pass records a budget expiry
without a corresponding partial status.


## WO-145 — Trigger the VPS deploy from GitHub Actions instead of a laptop — `queued` (owner-requested 2026-08-01; REWRITTEN 2026-08-01 after independent review found two honesty fields silently invalidated; workflow + secrets + `AGENTS.md` surface → OWNER MERGE after line-audit; no gate or threshold change)

**The requirement.** The owner should not need a laptop shell to deploy. The
laptop dependency and the attestation deadlock are **two separate problems**;
this WO solves the first and explicitly refuses to launder the second.

**Scope.** `scripts/deploy_vps_paper_manual.sh` (Path B) already performs the
full guarded deploy: preflight capacity check, private-transport proof BEFORE
quiescing, `.env` and marker backup at mode 0600, `rollback-last-known-good`
image tag, checkout update preserving runtime state, markers written before
container recreation, `--profile deploy-acceptance` with the scheduler stopped,
`check_polymarket_vps_paper.sh`, and `rollback_vps_paper_deploy.py` on any
failure past the arming boundary. Add
`.github/workflows/deploy_vps_paper_dispatch.yml`: **two jobs — an un-gated
guard job and an `environment:`-gated deploy job on `needs:` + `if:`** — that
run that script on the VPS over SSH. **Triggers are `push` on `main` and
`workflow_dispatch` (amended by §145.1 2026-08-02; this paragraph previously
said "a `workflow_dispatch` job", singular and dispatch-only).** The two-job
split is not cosmetic: `environment:` gates a job's START, so a guard inside the
gated job cannot suppress the approval request — see §145.1(a).

### The two honesty fields this WO must NOT silently invalidate

**1. `authorised_by` must stop being a hardcoded literal.**
`scripts/deploy_vps_paper_manual.sh:391` writes `"authorised_by": "owner"`
unconditionally. A workflow-triggered run would therefore stamp an owner
authorization claim that no owner made — an agent-writable owner-authorization
claim landing in a runtime artifact on every deploy, which `AGENTS.md` forbids
outright, and worse than a prose slip because it is machine-generated.
**Register: the deploy record records the MECHANISM, not a claimed identity, on
BOTH paths.** "Preserve the literal for a genuine owner shell" was wrong —
nothing can establish "a genuine owner shell"; anyone with SSH to the VPS
produces one, and that unprovability is the very reason `attestation_verified`
is `false`. So: `authorised_by` is replaced by `trigger_mechanism`
(`workflow_dispatch` | **`push`** | `vps_shell` — **`push` added in place by
§145.1 2026-08-02**) plus, on the workflow path, `github.actor`
and **the `environment:` approval actor**.

**Register the inversion this creates, because it is the honest reading.** With
an `environment:` gate carrying required reviewers — which in a single-owner
repository means the owner — the WORKFLOW path carries *stronger* owner evidence
than the shell path ever did. Capturing the environment-approval actor is what
makes that true rather than merely arguable.

**2. `attestation_unverifiable_reason` becomes materially misleading.** It
currently reads that verification "requires GitHub API credentials and the
acceptance run artifact; neither is available from the VPS shell". True of a
shell — but the Actions context demonstrably has both: Path A's own workflow
downloads the acceptance artifact with `github.token` and runs
`verify_independent_main_acceptance.py`
(`.github/workflows/deploy-polymarket-vps-paper.yml:47-55, :65`). Copying the
string unchanged converts *an unprovable step recorded as unproven* into *an
unperformed step recorded as unprovable*. **Option (b) is chosen for this WO; (a) is a named follow-on requiring owner
authorization. Note that "(a)/(b)" here are the two ATTESTATION-STRING options,
distinct from the two Path-A exits resolved above.** (b): replace the reason string with one naming the ACTUAL
blocker — no eligible independent reviewer can exist — rather than a capability
the Actions context possesses. (a) — running the verifier on the runner — could
make `attestation_verified` legitimately `true`, which is a gate change and is
therefore out of scope here; it also contradicts test 7 below, which pins
`attestation_verified: false` on this route. Choosing (b) keeps the two
consistent.

### `AGENTS.md` conflict — must be registered, not left implicit

`AGENTS.md` states two paths are permitted and that Path A is **REQUIRED
whenever GitHub Actions can run it**. WO-145's premise is that Path A is
unavailable — but the blocker is a **policy** deadlock, not a capability one:
`merge_independently_reviewed_pr.py:340-347` requires an `APPROVED` review on the
exact head from `{COLLABORATOR, MEMBER, OWNER}` excluding both the PR author and
the repository owner, so in a single-owner repo with agent authors **no eligible
reviewer can exist**. Giving Actions an SSH route strengthens the factual
predicate "Actions can run it" while leaving that text untouched, and
`tests/test_vps_only_operating_docs.py:65-77` pins the text. **This WO includes
the dated `AGENTS.md` amendment recording the policy-vs-capability distinction.
Owner merge.**

**Resolved by owner direction 2026-08-01.** Two exits were put to the owner:
(a) add a second human collaborator, making Path A work as designed; or
(b) permit an independent ARM64 machine acceptance run in Actions to bind the
SHA in place of a human approval. **The owner directed neither — Path B is the
permanent route** (see the sunset section above, which records the standing
consequence). Exit (b) remains a named follow-on requiring its own work order
and merge, because it changes what an attestation MEANS and must not be adopted
as a side effect of a convenience change. The `AGENTS.md` amendment is therefore
written as a standing qualification rather than a temporary one, and its text is
registered verbatim below rather than delegated.

### Trigger, concurrency and credential controls (all previously missing)

- **`environment:` with required reviewers** on the **`deploy` job**
  (*amended by §145.1 2026-08-02: this read "the `workflow_dispatch` job", which
  named a job that no longer exists — §145.1 registers a `guard` job and a
  `deploy` job, both fed by both triggers. The gate belongs on `deploy` and must
  NOT be placed on `guard`, or check 1 could never suppress an approval
  request*). Without it, anyone with write access — including the orchestrator, which posts
  from the owner's account — can deploy production unreviewed. This is the most
  dangerous omission in the first draft.
- **Share Path A's concurrency group exactly**: `group:
  deploy-polymarket-vps-paper`, `cancel-in-progress: false`. A separate group
  would let Path A and Path B deploy simultaneously — a duplicate writer.
- **`permissions:` least privilege**, matching Path A's `actions: read,
  contents: read`. (The "unless the verifier requires more" clause was struck
  2026-08-01: option (b) means the verifier never runs on the runner, so it was
  dead text widening a security control.)
- **Credentials already exist; NO new secret is required** (confirmed
  2026-08-01). Actions already carries `PM_VPS_HOST`, `PM_VPS_PORT`,
  `PM_VPS_USER` and `PM_VPS_SSH_PRIVATE_KEY`, consumed today by three workflows:
  `deploy-polymarket-vps-paper.yml:73-75, :100-102, :129-131`,
  `polymarket-vps-proof-health.yml`, and
  `polymarket-vps-governance-refresh.yml`. The new workflow **reuses those exact
  names and adds none**, so the SSH-to-VPS surface this WO relies on already
  exists in Actions and is not widened by it. The credential is a VPS user's SSH
  private key — not a GitHub "deploy key" (a repo SSH key), which an earlier
  draft of this entry wrongly called it.
- **Reuse the existing key-handling block verbatim** from
  `deploy-polymarket-vps-paper.yml:100-127`: the presence check at `:84`, the
  loadable-private-key validation at `:110-120` (which distinguishes a real
  private key from a `.pub` or a PuTTY `.ppk`), and the `known_hosts`
  construction at `:124`. **Named honestly: that block uses `ssh-keyscan`
  (`:124`), which is trust-on-first-use, NOT host-key pinning.** Matching the
  existing pattern is the registered requirement; upgrading it to a pinned key
  touches a shared block used by three workflows and is a named follow-on, not
  bundled here. Forbid `StrictHostKeyChecking=no` regardless.
- **Bound the key at the host** with a forced-command / `restrict` entry in the
  VPS `authorized_keys` so it can only invoke the deploy script and never
  obtains an interactive shell. This is the control that makes the difference
  between "Actions can deploy" and "Actions has a shell on production", and it
  is owner-side.
- Forbid `pull_request_target`, fork, and `schedule` triggers.
- No agent creates, reads, or transports a credential value.

### The `tmux` exit-code fail-open

Running inside `tmux` correctly prevents a dropped connection from leaving a
half-recreated container — an observed failure. But `tmux new-session -d`
returns 0 immediately, so **a failed deploy would report a green workflow run**.
Register the mechanism by which the script's exit status reaches the job — a
sentinel file plus poll, or `tmux wait-for` — and test it.

**Fail-safe sentence — corrected 2026-08-01; the first version was false.**
Nothing here marks a market measured, changes any M-A/M-B/M-C or `maker_min_*`
threshold, opens or enables any order path, or upgrades any attestation, and a
failed workflow leaves the VPS on its previous image via the existing rollback
path. **But this WO is NOT free of gate change and must not claim to be:** the
registered `AGENTS.md` amendment converts Path A's "REQUIRED whenever GitHub
Actions can run it" from a CAPABILITY test into a POLICY test, and by this WO's
own analysis the policy blocker is structural and permanent. Path A therefore
becomes never-required and the un-attested Path B becomes the permanent route —
a change to the binding condition of the strongest deploy gate, in the
loosening direction.

**No sunset — Path B is PERMANENT by owner direction (2026-08-01), and this
entry states the consequence plainly.** A sunset lapsing on "a second eligible
collaborator exists" was drafted; the owner directed instead that Path B be the
standing route. The loosening is therefore **permanent, not temporary**:
`attestation_verified: false` becomes the steady state of every deploy this
repository makes, and the independent-review predicate at
`merge_independently_reviewed_pr.py:340-347` will never be satisfiable. That is a
durable reduction in the strength of the deploy gate; it is recorded here rather
than buried in a lapsed footnote, and authorization is the owner's merge.

**Two controls replace the sunset, because "permanent" must not mean
"unobserved".** (i) The workflow still runs the existing eligibility query and
emits a **warning** whenever an eligible independent reviewer does exist — not as
a lapse trigger, but so a change in the collaborator set is never silently
ignored. (ii) The Path-B usage counter in the day-after check is retained and
reported, so reliance on the un-attested route stays measured. Under a permanent
Path B the honesty field is the only standing record of what is not proven,
which makes it MORE load-bearing than it was under a sunset, not less.

**Touch ONLY these files** (`git diff --stat` must show exactly these seven —
**count corrected 2026-08-02 by §145.1: the list said six and omitted
`tests/test_required_pr_gate.py`, whose pinned workflow inventory the new
workflow file fails until it is updated. As registered, the build could not have
passed its own suite**). The register is deliberately EXCLUDED — a build PR does
not edit its own WO status, matching WO-143's list:
- NEW `.github/workflows/deploy_vps_paper_dispatch.yml`
- `scripts/deploy_vps_paper_manual.sh` (`trigger_mechanism` and actor fields
  replacing the hardcoded `"authorised_by": "owner"` at `:391`; attestation
  reason string; **and, added by §145.1(a1b) 2026-08-02, removal of the
  `:-$remote_sha` tip-defaulting fallback at `:99` so an unset
  `PM_DEPLOY_TARGET_SHA` is rejected instead of silently retargeting**)
- `AGENTS.md` (dated policy-vs-capability amendment, text registered below)
- NEW `tests/test_deploy_vps_paper_dispatch_workflow.py`
- `tests/test_vps_only_operating_docs.py` (extend for the amended text)
- `tests/polymarket_predictive_engine/test_manual_vps_deploy_script.py` —
  **`:337` asserts `record["authorised_by"] == "owner"` and this WO changes that
  field**; omitting it would force the build to either fail the suite or edit
  outside its own contract
- `tests/test_required_pr_gate.py` (added by §145.1) —
  `test_workflow_inventory_has_only_registered_triggers` (`:259`) pins the exact
  set of workflow filenames and each one's trigger set. The new workflow must be
  registered there as
  `"deploy_vps_paper_dispatch.yml": {"push", "workflow_dispatch"}`. **Nothing
  else in this file may be touched** — the parametrisation at `:837-859` names `conftest.py`,
  `tests/integration/conftest.py`, `pytest.ini` and `pyproject.toml` as protected
  merge controls, and this bullet
  does not extend to them

**Register cross-reference to update in a later docs pass (not this build):**
WO-133's acceptance list names *"authoriser recorded"* as a deploy-record
honesty field; that phrasing is superseded by `trigger_mechanism`.

**The `AGENTS.md` amendment text is registered here, not delegated.** `AGENTS.md`
is exactly the surface that governance protects, and
`tests/test_vps_only_operating_docs.py:65-77` pins the sentences being changed,
so a build agent must not draft it. **The existing Path A/Path B sentences are QUALIFIED, never deleted** — a
builder must not rewrite `AGENTS.md:85-90`, which would break six assertions
pinned at `tests/test_vps_only_operating_docs.py:65-77`; this amendment is
additive and dated. It states: Path A remains
required whenever an eligible independent reviewer exists; where the independent
review requirement cannot be satisfied because no eligible reviewer exists — a
POLICY limit, not a capability limit — Path B is permitted, records
`attestation_verified: false`, and **this permission is PERMANENT, not
time-limited** — see the "No sunset" section above, which records the owner's
2026-08-01 direction and the durable gate reduction it carries.

**§145.2 — corrected 2026-08-02, escalated by the WO-145 builder.** This
verbatim string previously ended *"and this permission lapses under the sunset
above."* **There is no sunset.** The section ~60 lines above is headed "**No
sunset — Path B is PERMANENT by owner direction**" and states that a lapsing
sunset "was drafted" and the owner directed permanence instead. The registered
verbatim text therefore instructed the builder to write a lapse condition into
`AGENTS.md` that this same work order says does not exist — and because the
string is registered **verbatim**, a faithful builder would have put a false
statement into the governance surface `AGENTS.md` exists to protect. That is the
artifact-lies class, reached through the one mechanism that forbids the builder
from exercising judgment.

The builder escalated with file:line evidence and built against the permanence
language rather than the stale fragment. **That was the correct call** and this
correction ratifies it: the registered string now matches the registered
decision. Recorded rather than silently fixed, because a "verbatim" clause is
the highest-leverage place in a work order to be wrong — it is the one a builder
is explicitly told not to think about.

**Added by §145.1(a1b) 2026-08-02 — the Path B invocation form, registered here
because (5c) makes the bare form refuse.** The amendment additionally states
that a `vps_shell` deploy is invoked as:

```
PM_DEPLOY_TARGET_SHA=<40-hex commit sha> scripts/deploy_vps_paper_manual.sh
```

**A LITERAL forty-hex SHA, read from the merged pull request on GitHub. NOT a
shell substitution.** `PM_DEPLOY_TARGET_SHA=$(git rev-parse origin/main)` was
offered in an earlier draft of (a1b) and is **withdrawn as defective** — proven
by execution against the real `assert_target_is_origin_main`
(`scripts/deploy_vps_paper_manual.sh:96-105`):

- The script's own `git fetch` (`:97`) runs **after** the operator's
  substitution has already been evaluated, so on a VPS whose local `origin/main`
  ref is stale — the normal state, and the state Path B exists to serve when
  Actions is down — the substitution yields the *old* SHA and the deploy
  **refuses**.
- On a freshly fetched ref it yields the tip, which is **byte-for-byte the
  `:-$remote_sha` behaviour (5c) exists to delete**: the guard compares the tip
  to itself and can never fail.

**The justification is corrected too.** An earlier draft said "an operator
typing the command is present and deciding". That is true only of a literal
SHA. Typing a command substitution decides nothing — it delegates the choice
back to whatever the ref happens to be. The registered property is that the
operator **names the commit being deployed**, which is the same property (a1)
restores on the workflow path.

**Tests (enumerated; rewritten — the first draft's set mostly asserted
pre-existing script behaviour, and its "no secret appears in the log" test had
no offline implementation and would have required holding the secret).**
Structural assertions against the workflow YAML plus behavioural tests of the
new fields:
1. every secret is consumed via `env:` from `secrets.*` and **never
   interpolated into a `run:` string** (the actual injection/leak vector);
   no `set -x`; `persist-credentials: false`.
2. the workflow declares `environment:`, the shared concurrency group
   `deploy-polymarket-vps-paper` with `cancel-in-progress: false`, and a
   `permissions:` block no broader than Path A's.
3. **AMENDED IN PLACE by §145.1 2026-08-02 — this previously read "only
   `workflow_dispatch` is declared", which §145.1's own test (1) contradicts.**
   As amended: exactly `push` (restricted to `main`) and `workflow_dispatch` are
   declared — no `pull_request`, no `pull_request_target`, no fork trigger, no
   `schedule`.
4. `StrictHostKeyChecking` is not disabled anywhere in the workflow.
5. a nonzero script exit inside `tmux` yields a nonzero job (exercise the
   sentinel/poll contract directly, not through a real deploy).
6. the deploy record carries `trigger_mechanism` on BOTH paths, the workflow
   path additionally carries `github.actor` and the environment-approval actor,
   and the literal `authorised_by: "owner"` appears in **no** path — there is no
   "unless a real owner shell produced it" escape, because nothing can establish
   that.
7. `attestation_verified` remains `false` on the Path B route and the reason
   string does not claim a capability the Actions context possesses.

**Day-after check:** a dispatch-triggered deploy produces a
`vps_manual_deploy.json` byte-comparable to a laptop-triggered one except for
the actor and trigger-provenance fields; `source_vs_deployed_sha` reads
`ALIGNED`; and the count of `deploy_path: B_manual_guarded_script` records is
surfaced so Path-B reliance is **measured rather than invisible** — making the
un-attested route the ergonomic one is a de facto loosening of the deploy
control even though no threshold moves, and it must be visible.

### 145.1 — Auto-trigger the deploy on merge to `main`, holding at the approval gate (registered 2026-08-02, owner-directed)

**What changes.** WO-145 registers the new workflow as `workflow_dispatch` only.
The owner directed 2026-08-02 that it additionally trigger on merge to `main`.
The `environment:` required-reviewer gate is **unchanged** — the deploy still
stops there and waits for a human. What is removed is the owner having to
*remember to start it*, which is the remaining laptop-shaped step.

**Registered trigger:**

```yaml
on:
  push:
    branches: [main]
  workflow_dispatch:
```

`pull_request_target`, fork triggers and `schedule` remain **forbidden**, as
WO-145 already registers.

**No `paths:` filter — stated so a builder does not add one as an
"optimisation".** A docs-only merge still moves the `main` SHA, and the VPS
refuses to deploy unless its checkout equals `origin/main`
(`deploy-polymarket-vps-paper.yml:263-269`; `scripts/deploy_vps_paper_manual.sh`
enforces the same *property* by a different and weaker mechanism — see (a1),
which corrects an earlier version of this sentence that called them "the same
guard"). Skipping deploys for docs-only merges
would therefore leave `source_vs_deployed_sha` reading `DIVERGED` until some
later code merge, which is exactly the invisible-drift condition WO-145 exists
to end. Every merge deploys.

**(a) The stale-SHA guard, which auto-trigger makes necessary**

Under `push`, two merges in quick succession queue two runs. WO-145 registers
`concurrency: group: deploy-polymarket-vps-paper, cancel-in-progress: false`,
so they run in order — and by the time the **first** runs, `origin/main` has
moved past its `github.sha`. The VPS-side check then errors, producing a red run
for a condition that is not a fault.

**Required — TWO CHECKS at two different times, because supersession happens in
two different places (corrected 2026-08-02 after the second registration gate,
which showed the first two-job design could not produce its own registered
outcome).**

**Check 1 — before pickup, un-gated.** A `guard` job resolves the current
`origin/main` tip and sets an output. The `deploy` job carries the
`environment:` gate and runs only on `needs: [guard]` with an `if:` on that
output. When `github.sha` is already not the tip, the deploy job is
**`skipped`** — it never starts, so no SSH runs, no `vps_manual_deploy.json` is
written, and **no approval request is emitted**. This catches back-to-back
merges landing within seconds of each other.

**Check 2 — after approval, inside the gated job.** The deploy job re-resolves
`origin/main` as its first step and, when `github.sha` is no longer the tip,
**fails** with the literal message `superseded: approved <github.sha>, main is
now <tip>; approve the newer run`. It does **not** deploy and does **not**
write a deploy record.

**Why check 1 alone is insufficient — this is the flaw the second gate found.**
A job's outputs are **frozen when that job completes**. The guard is un-gated,
so it finishes seconds after the merge and the entire approval wait happens
*downstream* of it. In §145.1(a1)'s own scenario — merge A, park at the gate,
merge B lands, the owner approves the run labelled A — the guard had already
passed, because A **was** the tip when it ran. The deploy job is therefore not
skipped. The first version of this clause registered `skipped` as the outcome
for exactly the case it cannot produce.

**The unavoidable trade-off, stated rather than engineered around.** Check 2
runs *after* the approval, so a run superseded **during** the wait **does** page
the owner. That cannot be fixed: the approval wait is precisely the window in
which supersession occurs, and no check placed before the wait can observe an
event that happens during it. What is bounded is the harm — check 2 plus the
(a1) pinning make a wrong deploy impossible; the cost is one approval request
that resolves to a red run.

**Check 2's outcome is `failure`, and that is deliberate.** It cannot be
`skipped` — the job has already started. It must not be `success`: a green run
that deployed nothing is indistinguishable in the run list from a real deploy,
which is the defect this section already rejected once. A red run reading
`superseded: approved A, main is now B; approve the newer run` is accurate and
actionable.

**Why check 1 cannot be a step inside the gated job** (this concerns CHECK 1
only — check 2 *is* a step inside the gated job, deliberately, and the reasoning
below is why the two cannot be the same step; heading corrected 2026-08-02 after
a gate found the previous wording read as an instruction against check 2): `environment:` is a *job-level*
property and its protection rules gate the job's **start**. The reviewer
notification fires before any step of that job executes, so a guard step could
not suppress it — every superseded run would page the owner, which is the exact
noise this amendment exists to remove.

**No registered outcome is ever "neutral".** An earlier version of this clause
required the run "terminate NEUTRAL". **GitHub Actions has no producible neutral
outcome** — the `exit 78` neutral exit was removed at GA in 2019, and a job ends
`success`, `failure`, `cancelled`, `skipped` or `timed_out`. A builder
implementing "neutral" would substitute `success`, producing a **green run that
deployed nothing** — indistinguishable in the run list from a real deploy. The
two registered outcomes are `skipped` (check 1) and `failure` (check 2), both
producible and both visibly distinct from a deploy.

**(a1) The deploy target is PINNED to `github.sha`. This is the most important
requirement in §145.1 and the first version omitted it entirely.**

`scripts/deploy_vps_paper_manual.sh:99` reads
`target_sha="${PM_DEPLOY_TARGET_SHA:-$remote_sha}"` — **with the variable unset
it retargets to whatever `origin/main` is at that moment**, and the guard on the
next line then compares the tip to itself and can never fail. Under
`workflow_dispatch` the exposure was seconds and human-attended. **This
amendment makes the owner absent by design, so it converts a theoretical window
into the normal case:** merge A creates a run; the run parks at the gate; merge
B lands; the owner approves the run *labelled A*; the VPS deploys **B**, and
`vps_manual_deploy.json` records B's SHA against A's approval actor with
`source_vs_deployed_sha: ALIGNED`. Nothing anywhere flags it.

**Required:** the SSH invocation passes `PM_DEPLOY_TARGET_SHA: ${{ github.sha }}`
explicitly. An invocation that does not pin it is a defect, not a default. The
approval and the deployed commit must be the same commit.

**This also corrects a mis-citation.** An earlier version of this section called
the manual script's check "the same guard" as
`deploy-polymarket-vps-paper.yml:263-269`. They are not the same: Path A
compares a *caller-supplied* SHA to the tip and fails closed on drift; the
manual script *defaults the target to the tip* and therefore cannot detect
drift. One fails closed, the other silently retargets.

**Fail-closed direction, stated for BOTH checks because "skipped" is the
permissive-sounding branch (reconciled to the two-check design 2026-08-02; the
previous wording spoke only of "the guard" and predated check 2).** Neither
check can ever *cause* a deploy: check 1 may only skip, check 2 may only fail.
**Both resolve the tip, so both carry the same A2 branches** — if the tip cannot
be resolved for network failure, ref read error, empty result, or an unparseable
value, the job **fails** rather than proceeding, because an unresolvable tip
means the precondition is unknown and an unknown precondition must not deploy.
Missing, empty and unparseable take the fail branch in check 1 and in check 2
alike.

**(a1b) Deleting the fallback changes the `vps_shell` route, and that must be
registered rather than fall out (added 2026-08-02, second gate).** Test (5c) is
satisfiable only by removing the `:-$remote_sha` default at
`scripts/deploy_vps_paper_manual.sh:99`. Three consequences the first version
did not account for:

- `AGENTS.md:92` names Path B as `scripts/deploy_vps_paper_manual.sh` in a
  **prose heading** — *"Path B — `scripts/deploy_vps_paper_manual.sh`, for when
  Path A is not available"* — not as a command-line invocation, and `AGENTS.md`
  carries no invocation of the script anywhere. *(Citation corrected 2026-08-02:
  an earlier draft of this bullet called it "a bare invocation".)* The gap is
  therefore that **no registered text shows how to invoke Path B at all**, and
  after (5c) the obvious bare form refuses. **The explicit form is now
  registered in the `AGENTS.md` amendment block above, with a literal SHA.**
  `AGENTS.md` is already file 3 of the seven, so this is in scope.
- WO-145's enumerated test 6 requires `trigger_mechanism` "on BOTH paths" and
  §145.1(b) keeps `vps_shell` in the closed set. A `vps_shell` deploy therefore
  still works, but the operator supplies a **literal** target SHA. **It must not
  be a shell substitution** — see the amendment block above, where
  `$(git rev-parse origin/main)` is withdrawn as defective in both directions.
  The registered property is that the operator names the commit being deployed.
- The touched-file bullet for `scripts/deploy_vps_paper_manual.sh` scopes the
  edit to the `trigger_mechanism`/actor fields at `:391` and the attestation
  reason string. **It is widened here to name the `:99` fallback removal.**
  Without that, the build could not satisfy (5c) inside its own contract.

**(a2) `timeout-minutes` literal and basis (A1).** The new workflow registers
**`timeout-minutes: 70`**, the same literal Path A carries. Basis: it is not a
fresh estimate — Path A's value has three dated derivations in its own header
(20 → 30 → 40 → 70) driven by measured deploy durations, a 20-minute governance
wait and a 25-minute acceptance ceiling, and **this workflow runs the same
guarded deploy on the same host**, so the same worst case applies. A smaller
value would reintroduce the cancelled-mid-rollout failure that history
documents twice. The guard job registers **`timeout-minutes: 5`** — it performs
one `git ls-remote` and nothing else.

**Explicitly NOT the fix: `cancel-in-progress: true`.** It would abort a
*running* deploy mid-rollout, which is the unverified-deploy failure
`deploy-polymarket-vps-paper.yml`'s own timeout history documents twice. WO-145
registers `cancel-in-progress: false` to prevent two concurrent writers, and
that stands.

**(b) `trigger_mechanism` gains a third value**

WO-145 registers `trigger_mechanism` as `workflow_dispatch | vps_shell`. It
becomes **`workflow_dispatch | push | vps_shell`**. A `push`-triggered run
records `push` plus `github.actor` and the environment-approval actor — never a
claimed identity, and never the literal `authorised_by: "owner"` this WO exists
to remove. Nothing here upgrades `attestation_verified`, which stays `false`.

**(c) Touched-file list: six → SEVEN, and the edit is performed here**

Adding a workflow file fails
`tests/test_required_pr_gate.py::test_workflow_inventory_has_only_registered_triggers`
(`:259`), which pins the exact set of workflow filenames **and** each one's
trigger set, and asserts every workflow carries `timeout-minutes:`,
`concurrency:` and `permissions:`. WO-145's six-file list omits it, so the build
as registered could not pass its own suite. The list above now reads **seven**
with `tests/test_required_pr_gate.py` named, registering
`deploy_vps_paper_dispatch.yml: {"push", "workflow_dispatch"}`.

*(This reconciliation is performed in the parent list in this same diff, not
merely stated here. An amendment that declares a list change and does not make
it is the defect that cost PR #421 four review rounds.)*

**Tests (additive to WO-145's set).** (1) the workflow's `on:` block contains
exactly `push` and `workflow_dispatch`, and no `schedule`,
`pull_request_target`, or `pull_request`; (2) `push` is restricted to `main`;
(3) the inventory test recognises the new file with that exact trigger set;
(4) with `github.sha` behind the resolved tip, the deploy job is **`skipped`**
and no SSH step runs, asserted by a sentinel the SSH step would have written;
(4b) the guard job carries **no** `environment:` key and the deploy job carries
it, so a superseded run emits no approval request;
(5) with `github.sha` equal to the tip, the run proceeds to the gate;
(5b) **the SSH invocation passes `PM_DEPLOY_TARGET_SHA` set to `github.sha`**,
asserted against the workflow text; (2c) with the approvals response stubbed **empty**, the deploy job fails and
**no SSH step runs**, asserted by a sentinel the SSH step would have written;
(2d) with an entry whose state is `approved` it proceeds, and with an entry
whose state is **`rejected`** it **fails** — the case that distinguishes
`any(state == "approved")` from a non-empty check; (2e) with the query
erroring, timing out, or returning non-JSON it fails. All three are executed
offline against the step's `run:` block with the API call shimmed, the harness
(4f) and (4g) already require. **(2f) the query names THIS run**: the URL
contains `${{ github.repository }}` and `${{ github.run_id }}`, asserted against
the workflow text — *added 2026-08-02 after a gate showed a step with a
hardcoded or wrong `run_id` behaves identically under every stubbed response,
so the shimmed tests alone cannot see which run is being queried*;
(4c) **CHECK 2 EXISTS** — the deploy job's **first** step re-resolves
`origin/main`, asserted against the workflow text by position, not merely by
presence: no step may precede it in that job. **This test was absent from the
first version of the check-2 design, and a gate demonstrated that a workflow
omitting check 2 entirely passed every other registered test with a green
suite** — reproducing the exact structural defect check 2 was added to fix,
undetectably; (4d) that step emits the message
`superseded: approved <github.sha>, main is now <tip>; approve the newer run`,
where **`<github.sha>` and `<tip>` are SUBSTITUTED, not literal**. The test
pins the three invariant fragments — `superseded: approved `, `, main is now `,
`; approve the newer run` — **and asserts each placeholder position holds an
interpolation rather than the literal angle-bracket text**. *(Corrected
2026-08-02 after a gate demonstrated both failure directions: a test pinning the
whole line as a string FAILS on a correct build, which is the very defect this
clause claims to prevent and the same one already corrected once for
`divergence_started_at_utc`; and a build that echoes `<github.sha>` verbatim
PASSES every other registered test while emitting a message containing no SHAs
at all.)*;
(4e) that step's failure branch is `exit 1` — **not** `exit 0`, and not a
`continue-on-error` step, so a superseded run reads `failure` and never
`success`; (4f) check 2's own tip resolution has the same A2 branches as check
1's: a missing, empty, or unparseable tip **fails** the job rather than
proceeding to deploy. **(4f) is executed offline**, by extracting the step's
`run:` block and shimming `git` on `PATH` — no Actions harness is required;
(4g) **check 2's comparison is exercised behaviourally on the same harness**:
with the resolved tip **differing** from `github.sha` the step exits **nonzero**,
and with it **equal** the step exits **zero**. *(Added 2026-08-02: (4c)-(4f) are
text assertions plus one unresolvable-tip case, and a gate showed that a check 2
whose comparison is wrapped `( ... ) || true` — or whose polarity is inverted —
passes all of them. The harm is bounded by (a1)'s pinning, since the VPS guard
refuses a stale SHA before touching the host, but the registered `superseded:`
message would be lost and only the day-after check would notice.)*; (5c) an invocation with
`PM_DEPLOY_TARGET_SHA` unset or empty is **rejected** — the registered pinning
must not be satisfiable by the script's tip-defaulting fallback at
`scripts/deploy_vps_paper_manual.sh:99`; (5d) the remote process actually
receives the value — `PM_DEPLOY_TARGET_SHA: ${{ github.sha }}` as workflow
`env:` does **not** cross the `ssh` boundary on its own, so the test asserts the
variable is present in the command string sent to the host, not merely declared
in the workflow;
(6) with the tip unresolvable (stubbed to fail, and separately to return empty),
the run **fails** and does not deploy — the fail-closed branch;
(7) `concurrency` is byte-identical to Path A's group with
`cancel-in-progress: false`; (8) a `push`-triggered record carries
`trigger_mechanism: push` and no `authorised_by` key at all.

**Fail-safe sentence for §145.1.** Nothing here marks a market measured, changes
any M-A/M-B/M-C or `maker_min_*` threshold, opens or enables any order path, or
upgrades any attestation, and the `environment:` approval gate itself is
unchanged.

**(a3) The approval gate must be ENFORCED AT RUNTIME, not assumed from
configuration (added 2026-08-02, fourth gate).** An earlier version of this
sentence asserted "every deploy still requires a human approval and none can
proceed without one". **No registered artifact can establish that.** Enumerated
test 2 asserts only that the workflow *declares* `environment:`; whether that
environment carries a required-reviewer protection rule lives in GitHub
repository settings, which no test in this repo can read and no reviewer here
can inspect.

Under WO-145's dispatch-only design that gap was tolerable — an unconfigured
environment still had a human in the loop by construction, because the owner
started the run. **§145.1 removes that human.** With `push` and no `paths:`
filter, an environment lacking its protection rule means **every merge to `main`
deploys the VPS unattended**, and this section's fail-safe sentence would be
false. That is the single largest blast-radius change in this amendment and it
was previously left to a post-hoc day-after check, which by construction only
fires *after* the first unattended deploy.

**Required:** the deploy job's SSH step is preceded by a step that queries
`/repos/{owner}/{repo}/actions/runs/{run_id}/approvals` and **fails the job
unless that response contains an entry whose state is `approved`**, naming the
missing protection rule. *(Corrected 2026-08-02: the first wording was "fails
when the list is empty", and a gate showed a `state: "rejected"` entry makes the
list non-empty, so a correct implementation would PROCEED on a rejection. The
stated intent is "did a human approve this"; `any(state == "approved")` matches
that intent by construction, `length > 0` does not.)* This converts
an unpinnable repository setting into a runtime-enforced precondition. It is
belt-and-braces when the environment IS configured — the query simply returns
the approver — and it is the only thing standing between an unconfigured
environment and an unattended production deploy.

**Fail-closed, per A2:** an approvals query that errors, times out, returns
non-JSON, returns an empty list, or returns entries none of which is `approved`
**fails the job**. It never proceeds on doubt, because the doubt in question is
"did a human approve this".

**The (a3) tests are (2c)-(2f), and they live in the enumerated list ABOVE** —
*numbering corrected 2026-08-02: a first draft called this "test (2b)", which
collides with WO-145's own parent test 2 and appeared only in this prose, so a
builder enumerating from the registered list would have missed it entirely. It
belongs in `tests/test_deploy_vps_paper_dispatch_workflow.py` with the rest.
Two further slips in that correction, caught by the same gate and fixed here:
the roll-up read "(2c)-(2e)" and omitted **(2f)**, which is registered in the
same insertion and is unambiguously an (a3) test since it constrains the
approvals query URL; and it said the list was "below" when it is 108 lines
above. Noted for what it is — a correction that needed correcting twice is the
duplicate-set failure mode this PR has now hit in five separate rounds.*

*Naming caveat, recorded rather than renumbered a fourth time:* under §145.1's
own suffix convention — (4b) extends (4), (5b)-(5d) extend (5) — the label
`(2c)-(2f)` reads as an extension of §145.1's test (2), which is "`push` is
restricted to `main`", not the approval gate. The parent number is topically
wrong. **No functional consequence** — each label is defined exactly once and
collides with nothing — and a fourth renumbering has its own track record in
this PR, so the label stands and the mismatch is recorded instead.

**Owner precondition, stated because it is not mine to do:** the `environment`
must carry a required-reviewer protection rule **before** WO-145's build merges.
(a3) enforces it at runtime; it does not create it.

**Related owner setting, recorded rather than assumed.** GitHub permits
**self-approval** by default — "prevent self-review" is a separate rule that is
**off** unless enabled. Under the `push` trigger `github.actor` is whoever
merged, so the owner can merge and then approve their own deploy. That is still
a human approval and (a3)'s registered claim holds, but §145.1's honest story is
**"one human, two clicks"**, not "two humans". If the stronger property is
wanted, enabling "prevent self-review" belongs beside the precondition above —
it is an owner setting and this amendment does not assume it.

**But the set of deploys a human must approve is NOT identical, and an earlier
version of this sentence claimed it was (corrected 2026-08-02, second gate).**
The population goes from *{deploys the owner chose to start}* to *{every merge
to `main`}*, with no `paths:` filter, docs-only merges included. That is a
strict increase in approval events and it is the approval-fatigue direction —
the same de-facto pressure WO-145's parent insists be made visible rather than
asserted away. **It is recorded here as an operational loosening: the gate is
unchanged, the frequency at which it is exercised increases.** The mitigation is
that check 1 suppresses the request entirely for runs superseded before pickup,
so the increase is bounded by the merge rate, not by the merge rate plus
retries.

Neither new check can cause a deploy that would not otherwise happen: check 1
can only skip, check 2 can only fail, and an unresolvable tip fails rather than
proceeding.

**Day-after check for §145.1.** After the first auto-triggered deploy:
`vps_manual_deploy.json` records `trigger_mechanism: push` with a non-empty
approval actor and **no `authorised_by` key**; `source_vs_deployed_sha` reads
`ALIGNED` and `divergence_started_at_utc` is the **empty string** in
`outputs/performance/vps_telemetry_manifest.json` (*corrected 2026-08-02: the
first version named `telemetry/manifest.json`, which is the path on the
`vps-telemetry` publication branch rather than the artifact the producer writes,
and asserted the key was ABSENT — `build_manifest` emits it unconditionally at
`write_vps_telemetry_manifest.py:122`, empty when aligned, so the original check
would have failed on the success case*); **the deployed SHA equals the SHA the
approval was issued against**; and after two merges landed inside one deploy
window, exactly one run reached the VPS while the superseded run's **deploy
job** shows **`skipped`** (superseded before pickup, check 1), **`failure`
carrying the registered `superseded:` message** (superseded during the approval
wait, check 2), or **`cancelled`** (a third merge entered the concurrency group
while one run was pending). *(Corrected twice: the first version asserted
`skipped` for both cases, which the design cannot produce for the
during-approval one; the second omitted the level and the `cancelled` outcome —
a check-1 suppression leaves the **run** green even though the **deploy job**
is skipped, so an operator checking the run list would have read a correct build
as a defect.)* **The deploy job of a superseded run must never read
`success`.**

## S8 admission records (2026-08-01)

S8 was registered in the same change as WO-143's §143.7, WO-143b's §143b.1 and
WO-145, so all three were authored before the checklist existed. Running S8
against them retroactively, and recording the result rather than assuming it:

| WO | A1 | A2 | A3 | A4 | A5 | A6 | A7 | A8 | A9 | A10 | result |
|---|---|---|---|---|---|---|---|---|---|---|---|
| WO-143 §143.7 | PASS (1800 with basis, two alternatives rejected) | PASS (non-finite → `blocked_inputs`) | n/a | PASS | PASS (§143.1 exception recorded) | PASS | **FAILED, now FIXED** — the count said ten and the config file was absent after its prohibition was struck; now eleven with the file listed | n/a | n/a | PASS | **ADMITTED after fix** |
| WO-143b §143b.1 | **FAILED TWICE, now FIXED** — F1 first named four constants with no literals; the fix then introduced a FIFTH (`heartbeat_cap`) with no literal and an unsatisfiable ordering (`1320 < 1320`). Six constants are now literals with bases and the ordering is `1320 < 1800 < 2400` | PASS | PASS (roots widened to `src/`+`scripts/` off `__file__`) | PASS (Scope reconciled) | PASS | PASS (F4 antecedent) | PASS (fourteen) | PASS (F1 fan-out shown) | n/a | PASS | **ADMITTED after fix** |
| WO-145 | PASS | n/a | n/a | n/a | PASS | n/a | **A7 PASS RETRACTED 2026-08-02** — the cell read "PASS (six→seven)" while the list itself declared **six** and enumerated six bullets, so the record and the list disagreed at the moment the PASS was granted. The list also omitted `tests/test_required_pr_gate.py`, whose pinned workflow inventory the registered new workflow file fails. Corrected to **seven** by §145.1, with the bullet added. **Second A7 escape; the first is WO-143 §143.7 on the row above — WO-143b §143b.1's failure was A1, not A7, and an earlier version of this cell miscounted it** | n/a | PASS (both deploy paths enumerated) | PASS | **A7 RE-OPENED 2026-08-02; re-closure NOT granted here.** The correction was authored by §145.1's drafter, and S8 forbids the same agent producing and approving an artifact. The A7 cell above is a defect record, not a pass. §145.1 itself carries **no S8 row** for the same reason — an independent admission pass must add both |

The A1 failure on F1 is the checklist working on its first use, against text
written by the same agent that registered the checklist. It was found by the
independent reviewer, not by the author — which is the structural rule in Part 0
doing its job.

## Calibration log

One row per WO, appended at close, per `docs/ENGINEERING_STANDARDS.md` S8 and
`.claude/skills/wo-lifecycle/SKILL.md` Part 4. A WO is not `done` without its
row — **applying to work orders closed after 2026-08-01 only.** WOs closed
before that date predate this log and are not retroactively reopened. Seeded 2026-08-01 with the cycle that produced the standard.

| WO | class | tiers used | subagent tokens | spec-review defects | build-review defects | escaped to deploy | day-after |
|---|---|---|---|---|---|---|---|
| WO-143.7 / 143b.1 amendment round | D | Opus draft, Opus review x3 | ~570k reviewer + ~390k engineers (parked) | 7 (round 1), 4 blockers (round 2), 5 blockers (round 3) | not yet built | — | pending |
| WO-149 | M | Opus spec, Sonnet build, Opus review x2 (disjoint halves) | ~250k | 0 (registered clean in #419) | 0 blocking, 6 informational | **1 — an `F821` the offline suite could not see**, because `from __future__ import annotations` leaves the annotation unevaluated; caught by the required PR gate's `ruff`, not by pytest or either audit | pending deploy |
| WO-150 | F | Opus spec, Sonnet build, Opus review + delta re-verify | ~230k | 0 | 1 — a corrupt `maker_live_test` block regressed to the PERMISSIVE branch, where `main` had raised | 0 | pending deploy |
| WO-146 | F | Opus spec, Sonnet build, Opus review + delta re-verify | ~360k | 0 | 2 fixed (`AttributeError` escaping the shell fallback; a test comment misstating what `main` produces), 3 recorded (§146.5, unreachable `isfinite` guards, a dead test branch) | 0 | pending deploy |
| WO-145.1 registration | D | Opus draft, Opus registration gate **x6** | ~700k reviewer | **7 → 6 → 2 → 0 blockers**, then 3 delta rounds | not yet built | — | pending |

**Reading of the 2026-08-02 rows — the tiering held, the review shape did not.**
Three class-F/M builds went Sonnet-built and Opus-reviewed and produced **zero
blocking build defects between them**; every defect found was a fail-closed
regression or an inaccuracy, and each was caught by an independent audit rather
than by the author. That is Part 3's split working as designed.

**The one escape is the informative row.** WO-149's `F821` reached the required
PR gate because *three* checks could not see it: pytest cannot (the annotation is
never evaluated under `from __future__ import annotations`), and neither line
audit ran a linter because both were auditing code against registered spec.
**A green offline suite is not the gate's definition of green**, and the
orchestrator's PR body said "full suite green" while the gate disagreed. Cheap
fix already adopted: `ruff check .` runs before every push, not after a red CI.

**WO-145.1 is the outlier and its shape is the lesson.** Six gate rounds on a
~300-line docs diff, converging 7 → 6 → 2 → 0. Rounds 1-3 were substantive
reasoning defects; rounds 4-6 were almost entirely **a correction applied in one
place and not its duplicates** — a withdrawn instruction surviving in the
touched-file list that scoped it, a file count corrected in the preamble and
stale in three cross-references, a test list renumbered and not updated where it
was cited. A4, A7 and the proposed A-new all target that class and none caught
it, because each is stated per-claim while the failure is per-*duplicate*. The
cheaper remedy is structural and is now standing practice: **keep an amendment
small enough that its own duplicate set is enumerable by inspection**, and run
the mechanical count checks before dispatching a gate rather than after.

Reading of the seed row: spec-review defect count did not fall between rounds,
which by Part 4's rule means the admission checklist gains the rule that would
have caught the recurring one. Rounds 2 and 3 were both dominated by
**contradiction-with-existing-clause** and **incomplete-file-list** findings —
already A4, A5 and A7. Those rules were written after this cycle, so the next
cycle is the first real test of whether they bite.

## WO-146 — The DR archive build trigger IS its own RPO ceiling — `done` (2026-08-02, PR #426; registered 2026-08-01; `disaster_recovery` tighten-only settings block + a registered watchdog-read artifact, routed owner-merge after line-audit; build cadence 24.0h → 20.0h, no ceiling changes — the line audit confirmed `compliant` bit-identical to `main` across a 13-point age sweep, so only the DUE decision moves; two audit findings fixed in the build (an `AttributeError` escaping the shell fallback, and a test comment that misstated what `main` produces), three recorded — see §146.5; **DEPLOY PENDING** — the daily `disaster_recovery_not_recoverable` incident keeps firing on the VPS until a deploy carries this revision, because the deployed build cadence is still 24.0h)

**Provenance.** 2026-08-01 telemetry: `disaster_recovery_status.json` reads
`status: ok`, archive built 13:00:11Z, `remote_push_status: ok` — yet
`rpo.observed_archive_age_hours: 24.4986` against `rpo.active_rpo_hours: 24.0`,
so `rpo.compliant: false` and the `disaster_recovery_not_recoverable` incident
opened. The mechanism is narrower and more damning than "the archive is late":

1. `create_ledger_archive` decides the build is due at `disaster_recovery.py:402`
   via `_snapshot_due(previous, rpo_hours=float(rpo["active_rpo_hours"]))`, and
   `_snapshot_due` (`:248-255`) returns `age_hours >= rpo_hours`. **The build
   trigger and the compliance ceiling are the same number**, so the archive
   cannot be rebuilt until its age has ALREADY reached the ceiling.
2. Compliance is then recomputed from that same pre-build age at `:404`, where
   `observed_within = observed_age_hours <= active` (`:217`). On every
   successful build day the recorded age is `>= 24.0` by construction.
3. `scripts/push_vps_archive.sh:76-85` restamps the file on success and sets
   `last_remote_archive_age_hours = 0.0` (`:79`) without recomputing the `rpo`
   sub-object, so one artifact asserts the archive is 0 hours and 24.5 hours old
   simultaneously.
4. `degraded_state_watchdog.py:958` reads `rpo.compliant is True`; the
   registration carries `incident_on_observation: 1`, so one observation fires.
5. The excess is one poll period: host cron `*/30` (`push_vps_telemetry.sh:19`),
   and `24.4986 - 24.0 = 0.4986 h = 29.92 min` is 99.7% of 1800s. **Measured, not
   estimated.**

**Purpose.** Make the archive BUILD more often than the RPO ceiling so
scheduling latency no longer guarantees a daily breach. Preserves WO-122's
intent (`:211-216`) that `compliant` reflect observed age. Does **NOT** build:
any change to `active_rpo_hours`, `pre_live_max_rpo_hours`,
`paper_stage_max_rpo_hours`, `size_cap_mb`, `source_cap_mb`,
`lock_stale_seconds`, `ARCHIVE_EXCLUDED_PREFIXES`, the watchdog registration,
`DR_STATUS_MAX_AGE_SECONDS`, `_live_capital_context`, or any restore,
credential, order, signer, or live surface. No new artifact.

**Touch ONLY these files** (`git diff --stat` must show exactly these four; any
amendment adding a file updates this count and list, per S8/A7):
- `src/polymarket_predictive_engine/disaster_recovery.py`
- `scripts/push_vps_archive.sh`
- `polymarket_predictive_config.example.yaml`
- `tests/polymarket_predictive_engine/test_disaster_recovery.py`

### 146.1 — One new registered setting, tighten-only

In `_settings` (`:55-92`) add `"archive_build_interval_hours": 20.0` after
`"lock_stale_seconds": 3600,`, clamped `max(6.0, min(20.0, value))`.

| Literal | Basis |
|---|---|
| `20.0` default | `24.0` ceiling minus `0.5` measured latency leaves **3.5h** margin = seven consecutive missed 30-min cycles. Today the same arithmetic is `24.0 - 24.0 - 0.5 = -0.5h`: breach by construction. |
| `0.5` latency | Host cron `*/30` (`push_vps_telemetry.sh:19`); corroborated by the observed 0.4986h excess. |
| `6.0` floor | Six times `lock_stale_seconds: 3600` (`:75`) — the repo's own registered bound on one build — so a build can never still run when the next is due. Also bounds push volume at `4 x 240 MB = 960 MB/day` against `size_cap_mb: 240`. |
| `20.0` ceiling | Equals the default, so config may only make the archive MORE frequent — the same tighten-only direction as `min(240.0, ...)` at `:88`. |

**A2 — every input branch, fail-closed.** Absent/`None` → registered default,
`archive_build_interval_source: "registered_default"`. Present and
`safe_float` returns `None` → `ValueError`. Present and `math.isfinite` is False
(`nan`/`inf`) → `ValueError`; **this branch is mandatory and separately tested
because `safe_float("nan")` returns NaN with no guard (`utils.py:373-379`) and
`nan > ceiling` is `False`**. Present, finite, `<= 0` → `ValueError`. Outside
`[6.0, 20.0]` → clamped, source `"clamped_to_floor"`/`"clamped_to_ceiling"`.
Inside → source `"config"`. Every `ValueError` is raised inside
`create_ledger_archive`'s `try` (`:401`) so it stamps `status: "error"` and the
watchdog fires next tick — the registered precedent at `:201-202`.

### 146.2 — Non-finite guard on the three existing RPO values (accepted into this WO)

`_validated_rpo`'s check at `:201-202` is `active <= 0 or ...`, which is `False`
for `nan`; so is `active > allowed` at `:205`. A `nan` `active_rpo_hours` passes
validation today. Add `math.isfinite` to all three and raise the existing
`ValueError`. **Direction: strictly tightening** — it can only convert a
currently-accepted malformed config into a stamped error, never accept anything
rejected today. No ceiling value moves.

### 146.3 — Use the interval for the DUE decision only

At `:402` replace `rpo_hours=float(rpo["active_rpo_hours"])` with
`rpo_hours=float(rpo["archive_build_interval_hours"])`. Nothing else changes.
`_validated_rpo` gains three keys: `archive_build_interval_hours` (float),
`archive_build_interval_source` (string, closed domain of exactly the four
values above), `archive_build_margin_hours` (float,
`round(active - interval - 0.5, 4)`, = `3.5` at defaults). **`observed_within`
(`:217`) still compares against `active`, never the interval — the ceiling is
unchanged and still binds.** Note honestly: `write_json` sorts keys
(`utils.py:286`), so on-disk order is alphabetical; field NAMES and TYPES are
registered, not order.

### 146.4 — `push_vps_archive.sh` advisory due-time

In `stamp_remote` (`:76-85`) read `archive_build_interval_hours` instead of
`active_rpo_hours`, coerced inside a `try`; on missing/non-numeric/non-finite/
`<= 0` use the literal `6.0` (the clamp floor) so the displayed
`next_archive_due_at_utc` can only ever be EARLIER than the truth. The real due
decision is made in Python; an advisory that says "sooner" only makes an
operator look earlier.

### 146.5 — Config documentation

Insert a commented `archive_build_interval_hours: 20` after `active_rpo_hours:
24` (`polymarket_predictive_config.example.yaml:439`) naming the ceiling, the
measured latency, and the margin. `active_rpo_hours: 24`,
`paper_stage_max_rpo_hours: 168`, `pre_live_max_rpo_hours: 24`,
`size_cap_mb: 240`, `lock_stale_seconds: 3600` byte-identical in the diff.

**Reads/Writes.** Reads `disaster_recovery_status.json` (`last_remote_success_at_utc`
only, `read_json` default `{}` at `:393`) and the `disaster_recovery` config
block. Writes `disaster_recovery_status.json` only, atomically, full-rewrite
snapshot. Verified **not** in `ledger_anchor.DEFAULT_LEDGER_REGISTRY`
(`ledger_anchor.py:46-90`) — enrol nothing. Re-running inside the interval
writes `status: "not_due"` and rebuilds nothing (`:413-415`). Concurrent writer
`push_vps_archive.sh` already uses temp + `os.replace` (`:90-92`); both are
atomic and serialised by the `LOCK_DIR` mkdir lock (`:33-36`) plus the
`ledger_archive` runtime lock (`:417-424`).

**Fail-safe sentence.** A missing `archive_build_interval_hours` uses the
registered 20.0-hour default; a present-but-empty, non-numeric, non-finite,
zero, or negative value raises before any archive is built, which stamps
`status: "error"` and opens the registered `disaster_recovery_not_recoverable`
incident on the next watchdog tick; a missing or unparseable
`last_remote_success_at_utc` leaves `observed_archive_age_hours` null and
`compliant` false, so a never-archived host still alarms; the RPO ceiling
`active_rpo_hours` is unchanged and remains the sole compliance bound; and no
gate, sizing, or order surface reads this artifact.

**Cadence.** No new job. Host cron every 30 min → `push_vps_telemetry.sh:73-76`
→ `push_vps_archive.sh` → `snapshot-ledger-archive`. Only the interval at which
that existing driver answers "due" changes. **If the deployed crontab is not
`*/30 * * * *`, this WO's entire numeric basis changes and it returns to the
drafter** — see Open questions.

**Tests (enumerated; offline, deterministic; `pytest.approx(abs=1e-12)`).**
(1) no config → `20.0`. (2) `12.0` → `12.0`, source `"config"`, margin `11.5`.
(3) `2.0` → `6.0`, `"clamped_to_floor"`, margin `17.5`. (4) `48.0` → `20.0`,
`"clamped_to_ceiling"`, margin `3.5`. (5) `"abc"` → `status == "error"`, no
tarball. (6) `float("nan")` → `"error"`, and the test also asserts
`math.isnan(safe_float("nan"))` so it proves the guard, not the parser.
(7) `0` and `-1.0` → `"error"` each. (8) `last = now - 19.9h`, interval `20.0` →
`"not_due"`, age `19.9`, `compliant True`, `next_due == last + 20h`.
(9) **regression:** `last = now - 20.5h` → due, age `20.5`, `compliant True`
(under current `main` the same fixture at 24.0h yields `24.5`/`False`).
(10) **the alarm still works:** `last = now - 24.5h` → due, age `24.5`,
`compliant False`. (11) `last_remote_success_at_utc` absent → age `None`,
`compliant False`, due True. (12) `active_rpo_hours: nan` → `"error"` (146.2).
(13) `active_rpo_hours: 200` with live context → existing `:205-210` `ValueError`
fires, message unchanged. (14) static: the example config still contains
`active_rpo_hours: 24`, `paper_stage_max_rpo_hours: 168`,
`pre_live_max_rpo_hours: 24`, `size_cap_mb: 240`. (15) static (A3): the string
`archive_build_interval_hours` appears in exactly the four touched files, scan
rooted `ROOT = Path(__file__).resolve().parents[2]` over `ROOT/"src"`,
`"scripts"`, `"tests"`, `"docs"`, `".github"` and root `*.yaml`/`*.yml`/`*.toml`,
excluding `ROOT/".claude"`, asserting `visited_files > 0`. (16) static:
`push_vps_archive.sh` contains the `6.0` fallback. **Coverage limit stated
honestly: the shell script is not executed by the offline suite (it force-pushes
a Git branch and is VPS-only), so (16) is a text assertion, not behavioural.**

**Scope: FROZEN → OWNER MERGE after line-audit.** Two registered surfaces: the
`disaster_recovery` settings block (registered tighten-only at `:78-87`) and the
`rpo` sub-object the watchdog reads at `degraded_state_watchdog.py:958`.
**Tighten-only:** the only movement is build cadence 24.0h → 20.0h — strictly
more frequent, strictly more recovery coverage. `active_rpo_hours` (24.0),
`pre_live_max_rpo_hours` (24.0), `paper_stage_max_rpo_hours` (168.0),
`size_cap_mb` (240), `source_cap_mb` (2048), `lock_stale_seconds` (3600),
`ARCHIVE_EXCLUDED_PREFIXES`, `DR_STATUS_MAX_AGE_SECONDS` (21600), and the
watchdog healthy allowlist `{"ok","not_due"}` byte-identical in the diff. No
agent may cite this text or any dispatch of it as authorization; authorization
is the owner's merge.

### Named follow-on — record, do NOT build: `rpo.live_capital_context` is `true`

`_live_capital_context` (`:187-189`) returns `True` when `cfg.trading_mode ==
"live"` **or** a non-empty `maker_live_test.wallet_address` is configured. The
deployed config sets a wallet address documented in-file as a "public
identifier, read-only monitoring" (`polymarket_predictive_config.example.yaml:477`).
**That single field is why `allowed = pre_live_max` at `:204` and the applied
ceiling is 24h rather than the 168h paper-stage ceiling.** The system is
paper/dry-run with binding capital of exactly zero, so `true` appears to
describe a posture the system does not have — but correcting it would relax the
applied ceiling 24h → 168h, a sevenfold loosening of a safety bound, and is
therefore forbidden in this WO and in any WO not authored and merged by the
owner. The code already anticipates the question at `:223-226`. The question is
stated, not answered: *should a read-only monitored wallet address, with zero
binding capital, continue to select the pre-live RPO ceiling?*

**Provenance correction (orchestrator, 2026-08-01):** an earlier orchestrator
statement attributed this finding to a prior sweep identified as "TS-4". **That
citation could not be verified — `TS-4` appears nowhere in this repository — and
is withdrawn.** The finding stands on the code reading above, which is
reproducible; the identifier does not.

**Day-after check.** After one deployed cadence (<= 20h), from the telemetry
branch, without reading code: (1) `rpo.archive_build_interval_hours == 20.0`,
`..._source == "registered_default"`, `..._margin_hours == 3.5`. (2)
`active_rpo_hours == 24.0`, `paper_stage_max_rpo_hours == 168.0`,
`pre_live_max_rpo_hours == 24.0` — unchanged. (3) on the observation after a
successful build, `observed_archive_age_hours` is in `[20.0, 20.5]` and
`compliant is true`. (4) the `disaster_recovery_not_recoverable` evaluation
reads healthy and no new incident row appears. (5) `next_archive_due_at_utc ==
last_remote_success_at_utc + 20h`. (6) **failure signature:** if `compliant` is
still false immediately after a successful push while
`observed_archive_age_hours < 24.0`, the watchdog is reading a field this WO did
not update and the WO is REVERTED, not tuned.

**Open questions (orchestrator resolutions recorded inline).**
(1) **RESOLVED 2026-08-01 — the deployed crontab is confirmed.** The owner
supplied the live entry:
`*/30 * * * * /home/opc/Claude/scripts/push_vps_telemetry.sh >> $HOME/vps_telemetry_push.log 2>&1`.
That is exactly the schedule `push_vps_telemetry.sh:19` and
`POLYMARKET_QUANT_MODE_CHARTER.md:346` document, so the 0.5h worst-case
scheduling latency is now a confirmed deployed fact rather than a documented
intention, and the `20.0` / `6.0` / `3.5` literals stand as derived. Independent
corroboration remains the measured `24.4986 - 24.0 = 0.4986 h` excess, which is
99.7% of one 1800-second period. **No re-derivation is required.** Operator note
for the day-after check: the driver's log is `$HOME/vps_telemetry_push.log` on
the VPS. (2) 20.0h adopted over 18.0h — 18.0 gives 5.5h
margin at the cost of an extra build every ninth day; 20.0 keeps one build per
day. (3) shell fallback `6.0` ("displays sooner") adopted over omitting the
field. (4) 146.2 accepted into this WO: strictly tightening, and A2 compels it
once `_validated_rpo` is edited. (5) TS-4 provenance resolved above by
withdrawal.

### 146.5 — The build interval is not coupled to the ceiling it protects (registered 2026-08-02 from PR #426's line audit; NOT built in #426)

**The gap.** 146.1 clamps `archive_build_interval_hours` to `[6.0, 20.0]` with
**no coupling to `active_rpo_hours`**. Before WO-146 the build trigger *was*
`active_rpo_hours`, so tightening the ceiling automatically tightened the
cadence. It no longer does. Measured by the line audit:

| `active_rpo_hours` | build trigger before WO-146 | after | margin |
|---|---|---|---|
| 6 | 6.0h | **20.0h** | -14.5 |
| 12 | 12.0h | **20.0h** | -8.5 |
| 24 | 24.0h | 20.0h | +3.5 |

**Failure scenario.** An operator responds to a DR incident by tightening
`active_rpo_hours: 24 → 12`, expecting more frequent archives. The rebuild
cadence stays at 20h — the real recovery point degrades to **1.67× worse than
before WO-146**, `archive_build_margin_hours` publishes `-8.5`, **nothing reads
that field** (the audit found zero readers of it in `src` or `scripts`), no
error is raised, and the incident does not clear.

**Why this is a registration gap, not a build defect, and why #426 shipped
anyway.** 146.1's ceiling basis argues only that config may make the archive
*more* frequent than the 20.0 default; it never considers a ceiling **below**
20.0. The #426 build is faithful to the clamp as registered, and changing it
there would have deviated from the registration. The condition is **not
reachable under the deployed config** (`active_rpo_hours: 24.0`) and the
resulting state is loudly alarmed (`compliant: false`) both before and after
WO-146 — so this degrades real archive freshness, never the alarm.

**Corrected requirement.** The resolved interval is
`min(20.0, active_rpo_hours - 0.5)`, then clamped up to the `6.0` floor. The
`0.5` is the same measured scheduling-latency literal 146.1 already registers
and derives. Restated: the interval may never sit at or above the ceiling it
exists to stay under.

**A2.** `active_rpo_hours` reaches this expression only after 146.2's
`math.isfinite` guard, so a NaN or infinite ceiling has already raised. If the
subtraction yields a value below the `6.0` floor, the floor wins and the
resulting interval is **recorded as `clamped_to_floor`** — it is not an error,
because a tighter cadence is always safe. A resolved interval that would still
be `>= active_rpo_hours` after clamping is a **hard error**: it means the floor
and the ceiling are irreconcilable and no cadence can satisfy the registration.

**Touch ONLY these files** (`git diff --stat` must show exactly these two):

- `src/polymarket_predictive_engine/disaster_recovery.py` — the resolver only.
  `_validated_rpo`'s compliance comparison, the three ceiling literals, and
  `_live_capital_context` stay byte-identical.
- `tests/polymarket_predictive_engine/test_disaster_recovery.py`

**Tests (enumerated).** (1) with `active_rpo_hours: 24.0` the resolved interval
is **20.0**, unchanged from #426, and every WO-146 test still passes;
(2) with `12.0` it is **11.5**, not 20.0; (3) with `6.0` it is the `6.0` floor
with source `clamped_to_floor`; (4) with `6.4` the subtraction gives `5.9`,
below the floor, so the result is `6.0` and is **not** an error; (5) with a
ceiling low enough that even the floor exceeds it — `active_rpo_hours: 5.0` —
the resolver **raises** and no archive is built; (6) `archive_build_margin_hours`
is `>= 0` for every `active_rpo_hours` the clamps admit, which is the invariant
this amendment exists to restore; (7) the compliance comparison is byte-identical
to #426's across the same 13-point age sweep the line audit used.

**Fail-safe sentence for §146.5.** Nothing here marks a market measured, changes
any M-A/M-B/M-C or `maker_min_*` threshold, opens or enables any order path, or
loosens any gate; the change strictly **shortens** the build interval for every
ceiling below 20.5h and leaves it unchanged at the deployed ceiling, so the
archive can only become fresher, never staler.

**Day-after check.** `disaster_recovery_status.json` reads
`archive_build_margin_hours >= 0` on every run, and at the deployed
`active_rpo_hours: 24.0` the interval still reads `20.0` — this amendment is
invisible at the deployed configuration and only binds if the ceiling is ever
tightened.


## WO-147 — Expired markets on the official-book watchlist: measure first, exclude on positive evidence — `queued` (registered 2026-08-01; collection-side hygiene and observability only; **RE-SCOPED at drafting — the original framing was disproved, see Provenance**; no gate, threshold, eligibility rule, or funding value changes → OWNER MERGE after line-audit)

**Provenance — the framing that opened this WO was disproved and is corrected.**
The orchestrator's brief framed `excluded_stale: 62` (`venue_close_time_past: 61`,
`resolution_disputed: 1`, `title_date_past: 1`) as expired markets "consuming
candidate slots", connected to `portfolio_markets: 1`. **That is wrong and this
WO does not inherit it.** `_candidate_staleness_reasons`
(`maker_carry_study.py:582-605`) is called in the universe loop at `:842` and a
stale market hits `continue` at `:858` — **before** `universe.append` at `:859`.
A stale market therefore structurally cannot reach the yield scan,
`maker_carry_candidates.csv`, the depth check, or the sized portfolio. The 62 are
a disjoint population consuming no candidate slot and no seeding budget. A
parallel funnel analysis established the real constraint: of 14 depth-eligible
markets, 13 fail `_size_portfolio`'s first predicate
`(row.get("net_carry_usd_per_day") or 0) > 0` alone (`:1710-1718`). **WO-147 is
hygiene and observability, NOT a fix for the portfolio-of-one, and must not be
resourced as if it were.**

What survives verification is narrower and still worth building: **one of the
three collection tranches applies no staleness check at all.** `_portfolio`
(`maker_fill_replay.py:421-433`) inherits the study's filter. Seed
(`:487-571`) inherits it as of the last study run (up to 24h stale against a
900s collector). **Persistent (`_recent_book_markets`, `:436-484`) has no
close-time, title-date, or resolution check of any kind** — membership is
`books_dir.glob("*.csv.gz")` filtered only by mtime against
`regime_days: 14` — and the caller rewrites that same file on every successful
poll (`:862`), refreshing its mtime. **The recency criterion is refreshed by the
act of using it**, so a market that keeps returning a book payload keeps its slot
indefinitely regardless of whether its venue close time has passed. **How many
persistent-tranche markets are currently expired is unmeasured; no artifact
records it. That is what this WO builds first.**

**Purpose.** Make expired/past-close watchlist membership visible per tranche and
exclude it where exclusion is safe, reusing the study's registered staleness
predicate rather than a second implementation. Does **NOT** build: any change to
`maker_min_book_history_hours` (48.0), `maker_min_book_snapshots` (100),
`target_net_usd_per_day` (3.33), `max_trusted_reward_share` (0.05),
`_measurement_eligible`, `_size_portfolio`, `max_markets`,
`max_persistent_markets`, `max_candidate_markets`, `regime_days`,
`delisted_skip_threshold`, `delisted_cooldown_hours`, the seed tier ordering,
`maker_replay_collection_windows.csv` or `coverage_ratio` semantics, any watchdog
registration, or any order/signer/credential surface. No new artifact.

**Touch ONLY these files** (`git diff --stat` must show exactly these four):
- `src/polymarket_predictive_engine/maker_carry_study.py`
- `src/polymarket_predictive_engine/maker_fill_replay.py`
- `tests/polymarket_predictive_engine/test_maker_carry_study.py`
- `tests/polymarket_predictive_engine/test_maker_fill_replay.py`

### 147.1 — Producer side (S3): persist the per-condition stale map the study already computes

`_rewarded_universe` already builds `excluded_stale_reasons` at `:844-846` and
returns it at `:903`, but it reaches only `_portfolio_composition_diff` (`:2488`)
and is never written to disk. The collector cannot read what is never persisted.
At `:2311-2313` add exactly two keys: `excluded_stale_condition_ids`
(`dict[str, list[str]]`, sorted by condition id ascending, capped at the first
**200**) and `excluded_stale_condition_ids_truncated` (bool).

**Basis for 200:** the study scans at most `universe_pages: 5` x
`page_size: 100` = 500 markets per run (`:399-400`), so 200 bounds the field at
40% of the maximum scanned universe while covering the observed 62 with 3.2x
headroom; worst case is under 20 KB against the 300 KB telemetry file cap
(`push_vps_telemetry.sh:28`). `maker_carry_study.json` is verified **not** in
`ledger_anchor.DEFAULT_LEDGER_REGISTRY`, so adding keys carries no anchor risk.
`maker_carry_history.csv` and `maker_carry_portfolio_members.csv` byte-identical.

### 147.2 — Collector side: one shared predicate, two tranches

Add exactly one helper `_watchlist_expired_reasons(row, stale_map, *, as_of)`
which calls `maker_carry_study._candidate_staleness_reasons` **verbatim**,
imported inside the function — the precedent registered at
`maker_fill_replay.py:580-585` ("a second implementation would drift from the
rule it is meant to describe"). Do not reimplement date parsing. Adapter:
`end_date_utc` → `endDateIso`, `question` → `question`,
`uma_resolution_status` → `umaResolutionStatus` (all in `CANDIDATE_FIELDS`).
`as_of` is `parse_timestamp(generated_at) or datetime.now(timezone.utc)` — the
run's own clock per S1, never the max of observed data timestamps.

Applied to the **persistent** tranche (before `watchlist.append` at `:483`) and
the **seed** tranche (in the existing skip loop at `:510-530`, incrementing
`excluded["expired"]` so it surfaces through `candidate_seed_exclusions`).
**Explicitly NOT applied to the portfolio tranche** — `_portfolio` feeds
`maker_replay_collection_windows.csv`, whose `covered` flag drives
`coverage_ratio`; dropping a portfolio market on the strength of a possibly-stale
study file would blank a measurement denominator. WO-116's registration binds
here (`docs/POLYMARKET_CODEX_WORK_ORDERS.md:5514-5516`). Count it instead:
`portfolio_observed_not_excluded`.

**A2 — every branch, and every one fails OPEN toward collecting**, which is the
registered conservative direction for a collector (`maker_fill_replay.py:129-133`:
"For a collector the conservative direction is to collect"). Exclusion fires only
on positively parsed evidence of pastness. `end_date_utc` missing/empty/
unparseable/non-finite → `normalize_external_timestamp` returns `None`
(`utils.py:46-60`) → market **kept**, `close_time_unparseable` incremented.
Missing `question` → `_title_dates("")` returns `[]` → kept. Missing
`uma_resolution_status` → not in `STALE_RESOLUTION_STATUSES` → kept.
`maker_carry_study.json` missing/unreadable/not-a-dict → empty stale map,
`stale_map_status: "unavailable"`. Present but malformed → `"malformed"`. Its
`generated_at_utc` absent/unparseable/future/older than **48.0h** → ignored
entirely, `"stale_ignored"`. **Basis for 48.0h:** two times the registered study
interval `OPS_MAKER_STUDY_INTRADAY_INTERVAL_SECONDS` default 86400s
(`run_vps_ops_scheduler.sh:38`) — one missed study run tolerated, two is not
evidence. In the map AND parsing clean → excluded (union; the study's own
exclusion is the stronger evidence).

### 147.3 — Diagnostics

Two keys on `official_book_snapshot.json` (snapshot, not anchor-enrolled):
`watchlist_excluded_expired` (dict with exactly `persistent`, `seed`,
`portfolio_observed_not_excluded`, `close_time_unparseable`, `stale_map_status`
∈ `{"ok","unavailable","malformed","stale_ignored"}`) and
`watchlist_excluded_expired_examples` (at most **10**, sorted by condition id —
the literal matches the existing `excluded_stale_examples` cap at
`maker_carry_study.py:850`).

**Fail-safe sentence.** An expired-market exclusion fires only on positively
parsed evidence that the venue close time, title date, or UMA resolution status
is past; a missing, empty, unparseable, or non-finite `end_date_utc`, a missing
`question` or `uma_resolution_status`, and a missing, malformed, or
more-than-48-hour-old `maker_carry_study.json` all leave the market ON the
watchlist and increment a visible counter, because for a collector the
conservative direction is to keep collecting; the portfolio tranche is never
excluded, only counted; and no gate, sizing, eligibility, or order surface reads
this artifact.

**Cadence.** No new job or CLI command. `snapshot_official_books` already runs
every cycle via `collect_maker_replay_data` (`:957`, `:968`) on the existing
900s `run_trade_prints` cadence.

**Tests (enumerated).** In `test_maker_carry_study.py`: (1) 3 stale + 2 clean →
`excluded_stale == 3`, map has exactly the 3 ids sorted ascending, truncated
False. (2) 201 stale → exactly 200 keys, truncated True, the 200
lexicographically smallest. (3) `maker_carry_candidates.csv` contains none of the
stale ids — re-asserts the disjointness this WO's provenance depends on.
(4) `maker_carry_history.csv` and `maker_carry_portfolio_members.csv`
byte-identical. In `test_maker_fill_replay.py` (**S4: at least one Gamma
`/markets` payload sanitised from the real endpoint with a real `endDateIso` and
`umaResolutionStatus`**): (5) persistent, mtime `now-1h`, `end_date_utc =
now-2h` → excluded, `persistent == 1`, absent from `market_polls`. (6)
`now+2h` → kept, counter 0. (7) `""` → kept, `close_time_unparseable == 1`.
(8) `"not-a-date"` → same. (9) `"nan"` → same. (10) **persistent market ABSENT
from the candidates CSV but present in `excluded_stale_condition_ids` → excluded;
this is the 61-market case and the only path that catches it.** (11) study JSON
missing → `"unavailable"`, watchlist equals pre-change baseline. (12) map a list
not a dict → `"malformed"`. (13) study `generated_at_utc` at `now-47.9h` →
`"ok"` and source B applies; advance the run clock to `now-48.1h` →
`"stale_ignored"` — the S1-mandated clock-advance pair. (14) seed tranche with
one expired candidate → `candidate_seed_exclusions["expired"] == 1`, remaining
tier ordering unchanged against a golden list. (15) portfolio tranche with an
expired market → **still polled**, `portfolio_observed_not_excluded == 1`,
collection-windows row count unchanged. (16) examples capped at 10, sorted, on a
15-exclusion fixture. (17) byte-identity: the four `MAKER_POLICY_DEFAULTS`
thresholds above. (18) static (A3): `_candidate_staleness_reasons` called from
exactly two sites, scan rooted `Path(__file__).resolve().parents[2]` over
`ROOT/"src"`, `"scripts"`, `"tests"`, `".github"`, excluding `ROOT/".claude"`,
asserting `visited_files > 0`.

**Honest consequence, stated rather than discovered later.** The three tranche
budgets are independent (`persistent_cap` at `:733`, seed `cap` at `:751`), so
freeing a persistent slot **reduces the number of markets polled**; it does not
reallocate that slot to seeding. This WO lowers API and disk spend and removes
noise from `seasoning_runway`; it does **not** increase seeding breadth. Any
reallocation would be a `max_candidate_markets` change and is deliberately not
bundled.

**Scope: OWNER MERGE after line-audit.** Collection breadth and two diagnostic
fields only; no gate, threshold, eligibility rule, screen, sizing rule, funding
value, or config value moves in either direction. Tighten-only in the collection
sense: fewer markets may be polled, never more, and only on positively parsed
evidence of pastness. **Routing note:** the register previously contradicted
itself on collection-only WOs (WO-131 at `:6017` "non-frozen → orchestrator
merge" vs WO-141 at `:6644` "collection-only → owner merge"). **Orchestrator
resolution: all four of WO-146 through WO-149 route to OWNER MERGE.** The
stricter reading is adopted deliberately — this session's review rounds found
defects in the orchestrator's own registered text at every round, so orchestrator
self-merge is not the safe default here.

**Day-after check.** After one collector cycle (<= 15 min): (1)
`excluded_stale_condition_ids` non-empty with key count `min(excluded_stale,
200)`. (2) `watchlist_excluded_expired.stale_map_status == "ok"`. (3) **record
the numbers** — `persistent`, `seed`, `portfolio_observed_not_excluded`,
`close_time_unparseable`. **A result of `persistent: 0` is a valid and
informative outcome** meaning the tranche is clean today and this WO bought
observability rather than hygiene; it is not a build failure. (4)
`markets_polled` drops by exactly `persistent + seed`. (5) `seasoning_runway`
contains no id listed in `excluded_stale_condition_ids`. (6)
`maker_replay_collection_windows.csv` cadence per portfolio market unchanged.

**Open questions.** (1) Should the persistent exclusion also require absence from
the current portfolio as belt-and-braces? `_recent_book_markets` already receives
`exclude={portfolio ids}` at `:728`, but that guarantee lives in the caller.
(2) Is 48.0h right if the study effectively runs twice daily? If the deployed
effective interval is ~12h, 24.0h is the better-matched literal.
(3) **Priority: parked behind WO-149** per the drafter's recommendation and the
funnel finding.

## WO-148 — Make seed-to-eligible conversion measurable: a tier-assignment event ledger — `queued` (registered 2026-08-01; measurement-only sidecar; changes no selection behaviour; enrolment deliberately deferred, see 148.4 → OWNER MERGE after line-audit)

**Provenance.** An analyst could not compute the seed-to-eligible conversion rate
at all. Verified cause: tier assignment is recomputed from scratch every
collector cycle inside `snapshot_official_books` — `portfolio` at
`maker_fill_replay.py:723`, `persistent` at `:727-734`, `seeds` at `:747-753` —
and only the three current COUNTS are persisted (`:770-772`). Nothing records
which market was in which tranche at which time, and `_candidate_seed_markets`
(`:487-571`) re-ranks the whole candidate set every cycle with no memory. A
market that falls out of the portfolio, keeps accruing history through the
mtime-driven persistent tranche, and later re-enters the seed ranking is
therefore indistinguishable from an organic graduate.

**One concrete case makes this worth having even though tier throughput is NOT
the dominant constraint.** "Anthropic IPO" sits at 26.0h of book history and 97
snapshots against floors of 48.0h and 100, with `net_carry_usd_per_day: 0.4156`
and `estimate_quality: book_and_history` — **2 snapshots and 22 hours short of
eligibility with positive modelled carry and good estimate quality**, the one
current case where depth eligibility genuinely binds on a market that would
otherwise qualify. **This is recorded as evidence that seasoning throughput
matters at the margin. It is NOT an argument for relaxing either floor, and this
WO proposes no change to either.** Both are tighten-only by registration
(`maker_carry_study.py:1539-1541`).

**Purpose.** Persist a tier-assignment EVENT history so conversion rate and lead
time become computable from artifacts, and a re-entrant market is distinguishable
from an organic graduate. Does **NOT** build: any change to which markets are
selected for any tranche; to `_candidate_seed_markets`'s ranking, tiering, or
exclusions; to `_recent_book_markets`, `_portfolio`, `_candidate_map`,
`_seasoning_runway`, `_measurement_eligible`, or `_size_portfolio`; to
`max_markets`, `max_persistent_markets`, `max_candidate_markets`, `regime_days`,
`maker_min_book_history_hours`, `maker_min_book_snapshots`, or any other
threshold, gate, eligibility rule, or funding value; any enrolment in
`ledger_anchor.DEFAULT_LEDGER_REGISTRY` or the deployed `ledger_globs`; any new
scheduler job, CLI command, or config setting; any order/signer/credential
surface.

**Touch ONLY these files** (`git diff --stat` must show exactly these two):
- `src/polymarket_predictive_engine/maker_fill_replay.py`
- `tests/polymarket_predictive_engine/test_maker_fill_replay.py`

### 148.1 — The event ledger

NEW: `outputs/maker_carry/maker_watchlist_tier_events.csv`. **Immutable schema —
exactly these five columns in this order, forever:**
`event_utc, condition_id, token_id, previous_tier, tier`. The header may never
widen; a schema change takes a new versioned path (`utils.py:172-177`: "changing
an existing header would invalidate every historical prefix anchor"). **This
constraint is adopted even though the file is not enrolled — see 148.4.**

Closed domains: `tier` ∈ `{"portfolio","persistent","seed","absent"}`;
`previous_tier` ∈ the same plus `"unknown"`. **`"unknown"` means the prior state
could not be established and the event MUST NOT be counted as a conversion.**
`event_utc` is the run's `generated_at_utc` (`:708`).

**Writer: `utils.append_csv_rows` only.** Never `write_csv`, never truncation,
row-capping, sorting, or rewriting. **The WO-115 incident is the reason:** a
full-rewrite writer on an append-only-shaped ledger re-serialised historical rows
and blocked every anchor run for ten days, costing this repository a chain
re-genesis on 2026-07-26 (`docs/POLYMARKET_CODEX_WORK_ORDERS.md:5474-5497`).

**Write site:** immediately after `summary["candidate_seed_markets"] = len(seeds)`
at `:772`, **before** the batch POST at `:780` — the tier assignment is a fact
regardless of whether the HTTP polls succeed, and writing pre-network keeps a
heartbeat when polling fails.

### 148.2 — Liveness state file

NEW: `outputs/maker_carry/maker_watchlist_tier_state.json`, `write_json`
(atomic full-rewrite), written AFTER the events append, with exactly:
`generated_at_utc`, `work_order`, `watchlist_size`, `tier_counts` (keys
`portfolio`, `persistent`, `seed`), `reporting_only: true`,
`paper_trading_invoked: false`, `live_trading_invoked: false`. Its only job is
liveness; the diff is computed from the events ledger, so the two artifacts
cannot disagree about membership.

### 148.3 — The diff and the idempotency rule

(1) Read the events CSV with `read_csv_rows`; `last_tier` = the `tier` of the
**last row in file order** per condition id; absent → `"absent"`. (2) Build
`current_tier` from this cycle's three lists; multi-membership is impossible by
construction (`exclude` at `:728`, `:749-750`) but if it occurs precedence is
`portfolio > persistent > seed` and `tier_precedence_conflicts` increments.
(3) Read the state file; `state_age_seconds` via `parse_timestamp`, anchored to
the run clock per S1. (4) `resync = True` when the state file is missing,
unreadable, not a dict, `generated_at_utc` absent/unparseable, age negative, or
age > **21600.0** (6.0h). (5) Emit one row per condition id whose current tier
differs from its last tier, with `previous_tier = "unknown"` if `resync`.
(6) **Idempotency:** `previous_tier` derives from the ledger's own last row, so
re-running with no tier change emits **zero** rows, and a crash between the
append and the state write leaves a stale state file that the next cycle
re-diffs harmlessly. There is no dedup key and none is needed — the emit
condition IS the dedup.

**Basis for 6.0h:** `OPS_TRADE_PRINTS_INTERVAL_SECONDS` default 900s
(`run_vps_ops_scheduler.sh:41`), so 6.0h = 24 consecutive missed cycles. The
largest inter-poll gap observed in `maker_replay_collection_windows.csv` is 637
minutes (10.62h); 6.0h sits below it, so a gap of that class is correctly
stamped `"unknown"` rather than silently counted as a conversion. **Measured
from the deployed collection ledger, not chosen for roundness.**

**Basis for 200 (burst detection only, never truncation):** deployed caps are
`max_markets: 25`, `max_persistent_markets: 25`, `max_candidate_markets: 25`, so
the maximum watchlist is 75 and a total-churn cycle produces at most 75
departures + 75 arrivals = 150 rows. 200 is 150 + 33%. A cycle exceeding 200 is
structurally impossible under the deployed caps and indicates a defect: **the
rows are still written — never dropped —** and `tier_event_burst` is set true.

**A2 — every branch.** Events CSV missing/empty → genesis, every member emits
from `"absent"`. Unreadable (`OSError`/`csv.Error`) → **no rows appended**,
`tier_events_status = "read_failed"`, state file NOT written — the ledger is
never appended to from an unknown baseline. A row with empty `condition_id` or a
`tier` outside the closed domain → ignored for `last_tier`,
`tier_events_malformed_rows` increments, **never repaired or rewritten**. State
missing/not-a-dict/unparseable/negative age/over 21600.0 → `previous_tier =
"unknown"`, removing those events from every conversion computation — **fail-closed
for a measurement artifact means marking the uncertainty, not fabricating a
transition.** Every numeric read uses `safe_float` followed by an explicit
`math.isfinite` check (`safe_float("nan")` returns NaN unguarded,
`utils.py:373-379`). `append_csv_rows` raising → caught,
`tier_events_status = "write_failed"`, state not written, and
`snapshot_official_books` continues — **a measurement sidecar must never stop the
collector.**

### 148.4 — Ledger enrolment: the choice, made deliberately and disclosed

**Decision: do NOT enrol `maker_watchlist_tier_events.csv` in this WO.**
(1) The append-only writer discipline is adopted unconditionally regardless of
enrolment, so the file is anchor-safe by construction from row one.
(2) Enrolling makes this a WO-61 frozen-surface change requiring lockstep edits
to `ledger_anchor.py:46-90` and the deployed `ledger_globs`, converting a
measurement sidecar into a governance change. (3) This repository has already
paid a full chain re-genesis for one append-only mis-enrolment (WO-115). (4) No
gate, threshold, eligibility rule, or sizing path reads this artifact, so
tamper-evidence over it is not load-bearing. (5) Because the discipline is
append-only from day one, later enrolment is a one-line registry addition that
cannot break the chain — the WO-111 precedent at `ledger_anchor.py:63-65`.

**Named follow-on, recorded not built:** *should this file be enrolled
`append_only` once it has a week of rows and the discipline is proven in
production?* If yes, that is a separate WO-61 frozen-surface WO routing to owner
merge, adding the glob to `DEFAULT_LEDGER_REGISTRY` and the deployed
`ledger_globs` in lockstep. The state JSON would enrol `"snapshot"` if at all.

### 148.5 — Summary keys

`tier_events_status` (∈ `{"ok","read_failed","write_failed"}`),
`tier_events_written` (int), `tier_events_resync` (bool),
`tier_events_malformed_rows` (int), `tier_event_burst` (bool),
`tier_precedence_conflicts` (int).

**Reads/Writes.** Reads its own two artifacts only — no cross-producer
dependency, so S3 is satisfied trivially. Writes the events CSV (append-only) and
the state JSON (atomic snapshot), plus six keys on `official_book_snapshot.json`.
**Interleaving (S2):** sole writer is `snapshot_official_books`, reached only via
`collect_maker_replay_data` (`:957`, `:968`) and the `snapshot-official-books`
CLI command (`cli.py:430-439`). The scheduler runs jobs **serially**, each
blocking on `wait_with_safety_pulses`, and the concurrent safety-pulse members
(`run_vps_ops_scheduler.sh:655-665`) write none of these paths.
**`snapshot-official-books` is registered in the CLI but scheduled by nothing —
verified, no match for `official` in the scheduler. The builder must re-verify
this caller set with its own grep and STOP and report if it disagrees.**

**Fail-safe sentence.** A missing or empty tier-event ledger records this cycle's
whole watchlist as arrivals from `absent`; an unreadable ledger appends nothing
and records `tier_events_status: "read_failed"` rather than appending from an
unknown baseline; a missing, malformed, or more-than-six-hour-old state file
stamps every event this cycle `previous_tier: "unknown"`, which excludes it from
every conversion computation, because a measurement artifact fails closed by
marking uncertainty rather than fabricating a transition; a malformed historical
row is ignored and counted, never repaired or rewritten; a write failure is
swallowed and reported so a measurement sidecar can never stop the collector; and
nothing here changes which markets are collected, no gate, threshold, eligibility
rule, or sizing path reads this artifact, and no order surface exists.

**Cadence.** No new job, CLI command, or config setting. Growth: transitions
only — ~40 rows/day at ~170 B ≈ 7 KB/day (~2.5 MB/year); structural worst case
150 rows/cycle x 96 = 2.4 MB/day, which `tier_event_burst` makes visible on first
occurrence. **No truncation is ever applied — that is what makes the append-only
discipline hold.**

**Tests (enumerated).** (1) Genesis: 2+1+3 watchlist → exactly 6 rows, header
exactly as registered, every `previous_tier == "absent"`. (2) **Idempotency:**
re-run identical → **0** rows, file bytes byte-identical. (3) Promotion seed →
portfolio → exactly 1 row. (4) Departure → 1 row, `tier == "absent"`.
(5) **Re-entry** seed → absent → seed → three rows; a conversion computation sees
two distinct seed entries — the exact capability this WO creates. (6) State
missing → all rows `"unknown"`, `tier_events_resync True`. (7) State at
`now-21599s` → real prior tier; advance the clock to `now-21601s` → `"unknown"`
(S1 clock-advance pair). (8) State unparseable, absent, and future-by-60s →
`"unknown"` in all three. (9) Unreadable ledger → 0 rows, `"read_failed"`, state
not written, collector still returns normally. (10) `append_csv_rows`
monkeypatched to raise → `"write_failed"`, state not written, return otherwise
unchanged. (11) Malformed historical rows → ignored, counted,
**and the file prefix preceding the append is byte-identical — the WO-115
regression guard.** (12) 201 transitions → all 201 written, `tier_event_burst
True`. (13) Precedence conflict → registered precedence applied,
`tier_precedence_conflicts == 1`. (14) **Selection invariance:** the exact
`watchlist` list (order and membership) and `market_polls` identical before and
after on the same fixture — the proof no selection behaviour moved.
(15) **Non-enrolment guard:** neither new path appears in
`DEFAULT_LEDGER_REGISTRY` nor in the example config's `ledger_globs` — this makes
148.4's decision mechanical rather than aspirational. (16) Static (A3/A9):
`maker_watchlist_tier_events.csv` appears in exactly one non-test source file at
one call site, and `snapshot_official_books(` is called from exactly `:957`,
`:968`, and `cli.py:431`; scan rooted `Path(__file__).resolve().parents[2]` over
`ROOT/"src"`, `"scripts"`, `"tests"`, `".github"`, excluding `ROOT/".claude"`,
asserting `visited_files > 0`.

**Scope: OWNER MERGE after line-audit** (routing per WO-147's resolution). Adds
two unenrolled measurement artifacts and six diagnostic keys; changes no
selection behaviour, gate, threshold, eligibility rule, or funding value.
Tighten-only statement: nothing moves in either direction; the watchlist computed
at `:754` must be provably identical (test 14).

**Day-after check.** (1) `tier_events_status == "ok"`, `tier_events_written >= 1`
on the first cycle (genesis), `malformed_rows == 0`, `burst false`,
`precedence_conflicts == 0`. (2) **On the SECOND cycle: `tier_events_written == 0`
if the watchlist did not change, and `resync false`. A `tier_events_written` that
equals the watchlist size on every cycle means the diff is not working and the WO
is REVERTED, not tuned.** (3) State file exists, `generated_at_utc` younger than
30 min, `tier_counts` matching the sibling summary exactly. (4) Events header
exactly as registered, every `tier` inside the closed domain. (5)
`portfolio_markets`, `persistent_markets`, `candidate_seed_markets`,
`markets_polled` unchanged in distribution. (6) After seven days the ledger
answers, without reading code, "how many markets entered `seed` and later reached
`portfolio`, and how many hours elapsed" — excluding every pair touching an
`"unknown"` row. The "Anthropic IPO" case is the first expected datapoint.

**Open questions.** (1) **Non-enrolment decision accepted by the orchestrator**
as drafted; if enrolment is wanted inside this WO it becomes FROZEN, the
touched-file list becomes four, and A7 requires the count updated in the same
edit. (2) Is per-cycle event granularity enough, or does the analyst also need
periodic full-composition snapshots? Events alone require replay-from-genesis to
answer "who was in seed on 2026-07-20" — exact, but not a one-line query.
(3) Is 6.0h the right resync ceiling given the observed 637-minute worst gap?
**(4) Dependency on WO-151, stated so it is not discovered later:** this
ledger's resolution is bounded by the study's cadence, which WO-151 documents as
broken — 3.91 runs/day with ~39% of due checks skipped. A market that moves
seed → eligible → seed BETWEEN two runs leaves no event, so **the conversion
metric this WO enables is a LOWER BOUND on transitions, not a count.** It
becomes a count only to the extent WO-151 gives the study a real cadence.

## WO-149 — The replay join has no contemporaneous book state for 23% of prints, so every maker economic number is unvalidated model output — `done` (2026-08-02, PR #422; registered 2026-08-01; new scheduler job + registered watchdog freshness entry + a keyword-only scope on the sole official-book collector, routed owner-merge after two independent line-audits covering disjoint halves; **`max_book_state_lag_seconds` stays 1800 — no tolerance is loosened**; one lint fix round for an `F821` the offline suite could not see, because `from __future__ import annotations` makes the annotation unevaluated; **DEPLOY PENDING, and this is the binding one for the campaign** — `run_book_pulse` never fires on the VPS, so no `official_book_pulse.json` is produced and `M-B`'s `mb1_tier0_coverage_sufficient` stays `false`. Merging this WO did not move M-B; deploying it is what will)

**Provenance — why this is the highest-value item in the maker lane.**
`_size_portfolio` builds its pool from five ANDed predicates
(`maker_carry_study.py:1710-1718`), the first being
`(row.get("net_carry_usd_per_day") or 0) > 0`. A funnel analysis
(`origin/vps-telemetry`, 2026-08-01) established that of 14 depth-eligible
markets **13 fail on that predicate alone**, with values from −0.33 to −51.64.
Exactly one — NVIDIA "largest company" — clears all five, at $0.7363/day against
the registered $3.33/day target. Across all 40 measured candidates only 7 have
any positive net carry, and 5 of those are thin-book rows nowhere near the
floors. Capital is not binding ($452 of $500, $48 headroom); **no market-count
cap exists in `maker_carry_study.py`**; and `_measurement_eligible` (`:1538-1548`)
is the same depth predicate, not a second stage. **The binding constraint on the
maker campaign is negative modelled carry — not seasoning, depth, capital, or any
cap.**

That number is modelled end to end: `net_k = gross_k - k *
row["adverse_selection_usd_per_day"]` (`:1746`), where
`adverse_selection_usd_per_day = max(charges)` (`:1504`) — the **maximum** of up
to three modelled estimates (`:1499-1501`).

The only thing that can validate that charge against reality is the fill replay.
`realism_ratio` IS the calibration factor: `haircut = round(realized_adverse /
study_charge, 6)` (`maker_fill_replay.py:1460`), published at `:1519` and
`:1716`. And current telemetry reads `realism_ratio: "insufficient_coverage"`,
`confirmed_fills: 0`, `markout_per_fill` null at all three horizons.

**So the number rejecting 13 of 14 markets and holding the campaign at a
portfolio of one has never been validated against a single real fill.** This WO
is not evidence hygiene — it is the only way to learn whether the carry model is
correct to reject everything, or is systematically over-charging adverse
selection and discarding a viable book. With 18 days to the M-A terminal date
that is the highest-value question in the system.

**Mechanism, verified line by line.** Tolerance `max_book_state_lag_seconds:
1800` (`:109-111`), deployed identically at the example config `:190`. Join at
`:1207`, rejected stale at `:1208-1209`, miss counted at `:1219-1224` ending in
`continue` at `:1224` — **before** the crossing test at `:1254-1261`, so **a
missed join is never even tested for a fill**. The same tolerance gates the
markout leg at `:1282-1283`. Consequence: `insufficient` (`:1444-1450`) becomes
true and `realism_ratio` is set to the string at `:1453`. Observed: 116 of 501
prints (23.2%) find no contemporaneous state; median inter-poll gap 17.4-17.7 min
with gaps to 637 min; the portfolio market's archive-derived cadence is 37.15
min/snapshot — **above the 30-minute tolerance**. The registered comment
("Twice the 15-minute collection cadence") is now inaccurate at ~1.7x; correcting
the comment is in scope and is not a threshold change.

**The two remedies, directions stated honestly.**
**(i) Raise book-capture frequency for the portfolio — TIGHTENING. Recommended,
and built here.** More frequent observation strictly increases evidence
available to the join. It costs API calls and disk. It loosens nothing.
**(ii) Widen `max_book_state_lag_seconds` above 1800 — LOOSENING, and this WO
does NOT do it.** Widening admits staler book state as "contemporaneous", so
`realism_ratio` and every markout would be computed against book state as old as
the new bound — while that same metric is the calibration factor for the charge
now rejecting 13 of 14 markets. **An unmeasured widening would manufacture the
appearance of validation.** Remedy (ii) is honest under exactly one condition,
and that condition is what is built here: the staleness must be MEASURED and
propagated, so a future widening can be argued from a distribution rather than a
hope. **No widened literal is named, and the refusal is deliberate: A1 requires a
stated basis and none exists yet.** If after one week the measured p90 entry lag
is still above 1800s, that distribution — and nothing less — is the basis a
future WO would need, and that WO is a loosening of a realism-measurement bound
routing to owner merge with the direction disclosed.

**Purpose.** Restore an empirical anchor under the maker lane's economics by
raising book-capture frequency for the current portfolio, and by instrumenting
the actual join lag. Does **NOT** build: any change to
`max_book_state_lag_seconds` (stays 1800), `maker_min_book_history_hours`
(48.0), `maker_min_book_snapshots` (100), `target_net_usd_per_day` (3.33),
`max_trusted_reward_share` (0.05), `_measurement_eligible`, `_size_portfolio`,
the adverse-charge computation, `net_carry_usd_per_day`,
`maker_replay_collection_windows.csv` or `coverage_ratio` semantics, the
`maker_replay_insufficient_coverage` registration, `OPS_TRADE_PRINTS_INTERVAL_SECONDS`,
`run_trade_prints`, or any order/signer/credential surface.

**Touch ONLY these files** (`git diff --stat` must show exactly these seven):
- `src/polymarket_predictive_engine/maker_fill_replay.py`
- `src/polymarket_predictive_engine/cli.py`
- `scripts/run_vps_ops_scheduler.sh`
- `src/polymarket_predictive_engine/degraded_state_watchdog.py` (one entry in `REGISTERED_JOB_FRESHNESS_MAX_SECONDS`)
- `tests/polymarket_predictive_engine/test_maker_fill_replay.py`
- `tests/test_polymarket_vps_docker.py`
- `tests/polymarket_predictive_engine/test_degraded_state_watchdog.py`

Do NOT touch the example config (all new knobs are scheduler env with in-script
clamps — the WO-143 §143.4 pattern), `maker_carry_study.py`, `ledger_anchor.py`,
or `docker-compose.vps-paper.yml`.

### 149.1 — `snapshot_official_books` gains one keyword-only scope

`def snapshot_official_books(cfg, *, scope="watchlist")`. `scope` ∈
`{"watchlist","portfolio"}`; any other value raises `ValueError` **before any
network call or file write**. `scope == "watchlist"` is byte-identical to today
on every path. `scope == "portfolio"` changes exactly four things:

1. `watchlist = portfolio`; persistent and seed are neither computed nor polled;
   `persistent_markets = 0`, `candidate_seed_markets = 0`.
2. The summary writes to `outputs/maker_carry/official_book_pulse.json`, **never**
   `official_book_snapshot.json`. **Load-bearing:** `collect_maker_replay_data`
   reads the latter's `market_polls` at `:968-973` to build
   `maker_replay_collection_windows.csv`; a pulse overwriting it would corrupt
   the collection-window ledger and thence `coverage_ratio`, which WO-116's
   registration forbids (`docs/POLYMARKET_CODEX_WORK_ORDERS.md:5514-5516`).
3. `_seasoning_runway` (`:903`) skipped; `seasoning_runway` and
   `closest_to_eligibility` are `[]`. It is a full-watchlist report and would
   mislead over a portfolio-only slice.
4. The WO-131 delisted-marker file is **read** (so a cooling-down token is still
   skipped) but **not written** (`:881-899` skipped): the pulse polls a subset up
   to 3x as often, and letting it drive the 404 cooldown would change
   `delisted_skip_threshold: 3` from "three collector cycles" to "three cycles of
   a different job". `delisted_marker_write = "skipped_portfolio_scope"`.

Everything else — the batch POST, serial fallback, `_official_row`, the
`(observation_timestamp, hash)` dedup at `:838-852`, `max_official_book_rows`,
`_write_gzip_csv` — unchanged. Both scopes append to the same
`official_books/<condition_id>.csv.gz`; that is the entire point.

### 149.2 — Staleness accounting (measurement only, no tolerance change)

In `_replay_fills` (`:1150-1521`): after the join, when `state is not None`,
compute `entry_state_lag_seconds = trade["stamp"] - state["stamp"]` onto each
fill row; per covered horizon record `later_state_lag_seconds[f"{horizon}m"]`.
Add to the returned dict, alongside `no_contemporaneous_state_opportunities`
(`:1479`): `entry_state_lag_seconds_p50`/`_p90`/`_max` (floats or **`null`** when
there are no evaluable fills), `fills_beyond_legacy_lag` (int, count exceeding
**1800.0** — necessarily 0 under the unchanged tolerance; it exists so a future
widening is already counted by the same code), and
`no_contemporaneous_state_rate` (`round(misses / len(relevant_trades), 6)` or
`null`) — the 23.2% figure published rather than hand-computed. Percentiles use
nearest-rank with interpolation disabled so tests can hand-compute them. Correct
the inaccurate comment at `:109-111`.

**A2.** `trade["stamp"]`/`state["stamp"]` come from `normalize_external_timestamp`,
which returns `None` for missing, negative, non-finite, or unparseable values
(`utils.py:46-60`). Any lag that is `None` or fails `math.isfinite` is **excluded
from the percentile population and counted in `entry_state_lag_unmeasurable`**,
never coerced to zero and never counted as within tolerance. **An empty
population yields `null`, not `0.0` — a `0.0` p90 would read as perfect
freshness.** `fills_beyond_legacy_lag` counts only finite lags strictly greater
than 1800.0; a non-finite lag can never satisfy `> 1800.0` in Python, **which is
exactly why the isfinite guard is mandatory and separately tested**.

### 149.3 — CLI

Add `"snapshot-official-books-pulse"` after `"snapshot-official-books"`,
dispatching `scope="portfolio"` with the same zero-exit allowlist
(`{"ok","partial","no_portfolio","disabled"}`, `cli.py:433-439`). The existing
command stays byte-identical and continues to default to `scope="watchlist"`.

### 149.4 — Scheduler wiring

Pattern to match, named: `run_trade_prints` (`run_vps_ops_scheduler.sh:620-645`)
— job-local `*_STARTED_AT`, `set -e` subshell backgrounded,
`wait_with_safety_pulses`, `stamp_status` **with** the started-at argument. Do
NOT copy `run_maker_study_intraday` (`:600-618`), which omits it and records no
duration.

| Variable | Default | Clamp | Basis |
|---|---|---|---|
| `OPS_BOOK_PULSE_INTERVAL_SECONDS` | `300` | `[300, 900]` | Floor = one `TICK_SECONDS` (`:22`); the loop cannot fire faster than its tick, so smaller buys nothing and misrepresents cadence. Ceiling = the existing `PRINTS_INTERVAL` (`:41`); above it the pulse adds nothing. |
| `OPS_BOOK_PULSE_TIMEOUT_SECONDS` | `240` | `<= 240` | Strictly below the 300s interval so a pulse can never still run when the next is due. |
| `OPS_BOOK_PULSE_ENABLED` | `1` | — | `0` stamps an intentional skip at exit 0 (WO-114 precedent). |

Job `run_book_pulse` stamping `book_pulse`; loop block inserted **after**
`trade_prints` and **before** `ledger_anchor`.

**Expected cadence, derived not guessed.** `TICK_SECONDS` is 300 and jobs run
serially, so a 300s job fires at best once per tick. Measured drag: the 900s
`trade_prints` job shows an observed start-to-start median of 17.4-17.7 min =
**1.16-1.18x nominal**. Applying the same measured factor, a 300s job lands at
≈348-354s. Combined with the existing 900s poll, the expected worst-case
portfolio inter-snapshot gap falls from ≈1062s to ≈354s — comfortably inside the
unchanged 1800s tolerance, against the current 37.15 min (2229s) archive-derived
cadence this is aimed at.

**A8 — worst-case fan-out, shown.** Per pulse: one batch `POST /books` at
`request_timeout_seconds` 20s (`:105`). On batch failure the serial fallback
(`:794-806`) issues one `GET /book` per missing token at 20s each with 0.1s
between (`:106`, `:805-806`). At `max_markets: 25`:
`20 + 25 x 20 + 24 x 0.1 = 522.4s` worst case against a 240s wall-clock
`timeout`. **`timeout` is wall-clock over the process; `requests`' `timeout=20`
is a per-socket connect/read timeout, not a deadline, so a trickling server is
unbounded per call.** Accepted failure mode, stated rather than avoided: on batch
failure the pulse is SIGTERM'd mid-fetch and writes nothing that cycle — the
fetch loop (`:792-806`) completes entirely before the first file write
(`:836-865`), **so a killed pulse is all-or-nothing and no partial or torn book
file can result**. Evidence is `stamp_status` exit 124 with `skip_kind:
"overrun"` and an unrefreshed `last_success_utc`; the next pulse retries 300s
later. **API cost:** 288 batch calls/day added against 96 today, on the
unauthenticated public endpoint; <= 25 x 288 = 7,200 rows/day across 25 files,
against `max_official_book_rows: 200000` per file = 694 days of headroom, so the
truncation at `:859-861` cannot engage.

### 149.5 — Watchdog coverage

Exactly one entry in `REGISTERED_JOB_FRESHNESS_MAX_SECONDS` (`:56-67`):
`"book_pulse": 15 * 60`. **Basis for 900s:** two consecutive missed pulses
tolerated at the measured 1.16-1.18x drag (`2 x 354 = 708s`) with 192s headroom;
the third is an incident. This ratio is deliberately looser than the neighbouring
`"trade_prints": 20 * 60` (1.33 intervals) precisely because the measured drag is
a much larger FRACTION of a 300s interval than of a 900s one — stated so the
difference reads as derivation rather than inconsistency. No change to
`maker_replay_insufficient_coverage` (`:226-232`).

**Reads/Writes.** Pulse reads `maker_carry_study.json` (`portfolio`),
`maker_carry_candidates.csv`, and `delisted_token_markers.json` (read-only).
Pulse writes `official_books/<condition_id>.csv.gz` for portfolio markets only
(existing gzip read-modify-write, deduped on `(observation_timestamp, hash)`) and
`official_book_pulse.json` (new atomic snapshot). Verified: neither
`official_book_snapshot.json` nor `official_book_pulse.json` nor
`official_books/*` appears in `DEFAULT_LEDGER_REGISTRY` — enrol nothing.
**Explicitly NOT written by the pulse:** `official_book_snapshot.json`,
`maker_replay_collection_windows.csv`, `maker_replay_collection.json`,
`delisted_token_markers.json`, `maker_carry_study.json`,
`maker_carry_candidates.csv`, and every anchored ledger.

**Interleaving (S2) — the one real hazard and why it does not bite.** The
per-market gzip write at `:837-862` is a read-modify-write with no lock, and
`_write_gzip_csv` (`:227-233`) is a plain `gzip.open` — **not atomic**. Two
concurrent writers of the same file would lose rows. They cannot be concurrent
here: the scheduler runs job blocks serially in one loop, each blocking on
`wait_with_safety_pulses "$JOB_PID"`, and the concurrent safety-pulse members
(`:655-665`) write none of these paths. The pulse is a new serial loop block, so
it is mutually exclusive with `run_trade_prints` and `run_maker_study_intraday`
by the same mechanism that already protects them from each other. **The builder
must verify this with its own reading of the loop and STOP and report if the two
blocks can ever overlap.**

**Fail-safe sentence.** Nothing here marks a market measured, changes any
M-A/M-B/M-C or maker threshold, changes `max_book_state_lag_seconds`, opens any
order path, or changes what the full-watchlist collector does; the pulse is
additive book-observation frequency, paper-only. A missing or empty portfolio
yields status `no_portfolio` with no network call and no file write; an invalid
`scope` raises before any network call or write; a failed batch and failed serial
fetch record `invalid_or_empty_book` per market and write no row for it; a
wall-clock timeout kills the pulse before the first file write, so no partial or
torn book file can result, and `last_success_utc` is not refreshed so
`scheduler_completion_freshness` trips at the registered 900-second `book_pulse`
ceiling; a missing, unparseable, or non-finite book or trade timestamp is
excluded from the lag population and counted in `entry_state_lag_unmeasurable`
rather than coerced to zero; an empty lag population publishes `null`, never
`0.0`; and no gate, sizing, eligibility, or order surface reads
`official_book_pulse.json` or any of the new lag fields.

**Tests (enumerated).** In `test_maker_fill_replay.py` (**S4: a sanitised real
`POST /books` response and a sanitised real `GET /book` response**):
(1) `scope="watchlist"` byte-identical — `official_book_snapshot.json` and every
`official_books/*.csv.gz` byte-identical to pre-change on the same fixture, and
**every existing test in the file passes UNMODIFIED**. (2) `scope="portfolio"`
with 2+3+4 → exactly 2 polled, pulse JSON written, sentinel snapshot JSON
byte-identical. (3) sentinel collection-windows CSV byte-identical.
(4) sentinel delisted markers byte-identical when a portfolio token 404s;
`delisted_marker_write == "skipped_portfolio_scope"`. (5) a token in cooldown is
still skipped under portfolio scope. (6) `scope="garbage"` raises, no file
written, no HTTP call. (7) empty portfolio → `no_portfolio`, zero HTTP calls,
snapshot untouched. (8) dedup: pulse then collector at the same
`generated_at_utc` adds exactly one row; at different stamps, two.
(9) **hand-computed lags:** trades at t=0,100,400s; states at t=−50,−200,−350s →
lags `[50,300,750]`; `p50 == 300.0`, `p90 == 750.0`, `max == 750.0`,
`fills_beyond_legacy_lag == 0`. (10) tolerance monkeypatched to 3600 with one
fill at 2000s → `1` — proves the counter works against the 1800 literal without
this WO changing the deployed value. (11) a book row whose timestamp parses to
`nan` → excluded, `entry_state_lag_unmeasurable == 1`, p90 over the finite
remainder. (12) **empty population → p50/p90/max are `null`, not `0.0`.**
(13) `no_contemporaneous_state_rate`: 4 relevant trades, 1 miss → `0.25`; zero
relevant trades → `null`. (14) tolerance byte-identity: `_settings(cfg)
["max_book_state_lag_seconds"] == 1800` and the example config still contains
`max_book_state_lag_seconds: 1800`. (15) threshold byte-identity: the four
`MAKER_POLICY_DEFAULTS` values. (16) static (A3/A9): `snapshot_official_books(`
called from exactly `:957`, `:968`, and the two `cli.py` branches; scan rooted
`Path(__file__).resolve().parents[2]` over `ROOT/"src"`, `"scripts"`, `"tests"`,
`".github"`, excluding `ROOT/".claude"`, asserting `visited_files > 0`.
In `test_polymarket_vps_docker.py` (library-only sourcing): (17) clamps
`999999→900`, `60→300`, `abc→300`, timeout `99999→240`. (18) static wiring: the
exact command line, `wait_with_safety_pulses "$JOB_PID" book_pulse`,
`stamp_status` with started-at, loop block after `trade_prints` and before
`ledger_anchor`. (19) exit 124 records `skip_kind: "overrun"` and leaves
`last_success_utc` empty with a numeric duration; a following exit 0 refreshes
it. (20) `OPS_BOOK_PULSE_ENABLED=0` stamps an intentional skip at exit 0.
In `test_degraded_state_watchdog.py`: (21)
`REGISTERED_JOB_FRESHNESS_MAX_SECONDS["book_pulse"] == 900`. (22) stale at 901s
opens the incident with `entity: "book_pulse"`; (23) fresh at 899s does not.
(24) never-observed → `unobserved` then `stale_unobserved` on the second
evaluation. (25) `maker_replay_insufficient_coverage`'s registration dict
unchanged field-for-field.

**Scope: FROZEN → OWNER MERGE after line-audit.** Three registered surfaces: the
deployed ops scheduler (a new job in the production loop), a registered watchdog
surface, and the signature of the sole official-book collector whose output feeds
`maker_fill_replay.json` and thence the M-B evidence lane. WO-133's
indivisibility rule applies to the whole PR. **Direction: `max_book_state_lag_seconds`
stays 1800 — no tolerance is loosened. No gate, threshold, screen, eligibility
rule, or funding value moves in either direction. The only widening is
observation frequency for markets already in the portfolio, which can only add
evidence.** No agent may cite this text, or any dispatch of it, as owner
authorization; authorization is the owner's merge.

### Named follow-on — WO-149b, record do NOT build: the full-watchlist collector's fan-out exceeds its own timeout

The same A8 derivation applied to the existing `collect-maker-replay-data` at the
deployed watchlist cap of `25 + 25 + 25 = 75` tokens gives
`20 + 75 x 20 + 74 x 0.1 = 1527.4s` worst case against `PRINTS_TIMEOUT` of
**300s** (`run_vps_ops_scheduler.sh:42`). Because the entire fetch loop
(`:792-806`) completes before the first file write (`:812-865`), a timeout during
fetch loses **every market's** rows for that cycle. This is a mechanically
derived candidate explanation for the 637-minute gaps observed in
`maker_replay_collection_windows.csv`. **It is NOT confirmed** — that needs the
scheduler's exit-code history for `trade_prints`, which is not in this
repository. Registered for the orchestrator to scope; the fix is to write each
market's rows as they are fetched, or to bound the serial fallback, and either
changes the collector's failure semantics. Not bundled here.

**Day-after check.** Pre-deploy, the orchestrator records in this status line the
current `realism_ratio`, `confirmed_fills`,
`no_contemporaneous_state_opportunities`, `book_states`, and the portfolio
market's archive-derived cadence (37.15 min/snapshot). After one deployed day:
(1) `jobs.book_pulse` exists with exit 0, fresh `last_success_utc`, numeric
duration, `skip_kind: "none"`, fresh at ceiling 900 with no incident.
(2) `official_book_pulse.json` → `status: "ok"`, `markets_polled ==
portfolio_markets`, `persistent_markets: 0`, `candidate_seed_markets: 0`, both
invocation literals false. (3) `official_book_snapshot.json` → `portfolio_markets`,
`persistent_markets`, `candidate_seed_markets`, `markets_polled` unchanged — the
pulse must not have touched the full-watchlist lane. (4) `maker_fill_replay.json`
→ `no_contemporaneous_state_rate` has fallen from 0.232,
`entry_state_lag_seconds_p90` present and numeric, `fills_beyond_legacy_lag == 0`.
(5) **The decisive one: `confirmed_fills >= 1` and `realism_ratio` is a NUMBER
rather than the string `"insufficient_coverage"`. That number, whatever it is, is
the first empirical check ever run on the adverse-selection charge currently
rejecting 13 of 14 depth-eligible markets. Record it in this status line.**
(6) `markout_per_fill` non-null at the 5m horizon. (7)
`max_book_state_lag_seconds` still reads 1800 in the deployed config.
(8) **Failure signature:** if `entry_state_lag_seconds_p90` is still above 1800
after seven days, that measured distribution — and nothing less — is the basis a
future tolerance WO would need, and that WO is a loosening routing to owner
merge.

**Open questions (orchestrator resolutions inline).** (1) Portfolio-only adopted
over portfolio-plus-depth-eligible: extending to ~40 tokens raises the worst-case
serial fallback to `20 + 40 x 20 + 39 x 0.1 = 823.9s`, which no longer fits under
any timeout below the 300s interval; that extension requires WO-149b first.
(2) 300s interval adopted over 600s (which would still fit at ~708s expected
gap) to leave margin for the observed 637-minute outage class. (3) Job-freshness
entry only, no separate producer registration — the WO-143 §143.5 reasoning
(a producer registration fires spuriously when the owner quiesces a lane).
(4) **Dispatched FIRST of WO-146..149.** (5) **No tolerance literal proposed, and
the orchestrator accepts that** — A1 forbids naming one without a basis, and the
basis does not exist until the pulse has run.

## WO-150 — `live_capital_context` must reflect binding capital, not the presence of a read-only monitoring address — `done` (2026-08-02, PR #425; owner-directed 2026-08-01, registered same day; **LATENT LOOSENING: the RPO config clamp widens 24h → 168h; the applied compliance bound does NOT move** (an earlier version of this heading claimed the applied bound moves — retracted, see Direction) — `disaster_recovery` registered surface, routed owner-merge after line-audit, which verified the non-movement by differential execution and enumerated every consumer: none reads `maximum_rpo_hours_for_context` or `live_capital_context`, so the widened clamp reaches no gate; one fix round after the audit found a corrupt `maker_live_test` block regressing to the permissive branch; **DEPLOY PENDING** — the VPS still reads `live_capital_context: true` off a read-only monitoring address. Note this WO alone would never have silenced the DR incident: `compliant` compares against `active_rpo_hours`, which it deliberately does not move. WO-146 is that fix)

**Direction — CORRECTED 2026-08-01 after independent review. The first version
of this paragraph was factually wrong and is retracted.**

It claimed this WO produces "a sevenfold widening of the tolerated archive age
for the configuration deployed today". **It does not.** Read
`_validated_rpo` (`disaster_recovery.py:196-234`): `allowed = pre_live_max if
live_context else paper_max` (`:204`) is used for exactly two things — a
validation guard on the CONFIGURED value (`if active > allowed: raise`, `:205`)
and a reported field (`maximum_rpo_hours_for_context`, `:220`). **Compliance is
computed against `active`, never against `allowed`:**

```python
observed_within = observed_age_hours is not None and observed_age_hours <= active   # :217
```

This WO explicitly forbids touching `active_rpo_hours`, which stays 24.0.
**Therefore the tolerated archive age does not move at all.** An archive 30 hours
old is non-compliant before this change and non-compliant after it.

**What this WO actually is, stated accurately:**
1. **A reporting correction.** `live_capital_context` and
   `maximum_rpo_hours_for_context` stop describing a live-capital posture the
   system does not have. No behaviour changes.
2. **A LATENT clamp widening.** The guard at `:205` currently rejects any
   configured `active_rpo_hours` above 24.0; afterwards it rejects only above
   168.0. **Nobody's archive tolerance moves today, but a future config edit
   setting `active_rpo_hours: 100` would newly validate where it is rejected
   now.** That is a real loosening, it is latent rather than applied, and it is
   the honest thing for an owner to be weighing at merge.

3. **A soft effect worth naming, since no machine bound moves.**
   `maximum_rpo_hours_for_context` is a PUBLISHED field. Flipping it 24 → 168
   changes what a human operator reads even though nothing enforced changed —
   "we have a week of slack" is a plausible and wrong misreading of an artifact
   whose actual enforced bound is still 24 hours. The day-after check below
   requires `active_rpo_hours == 24.0` to be observed alongside it precisely so
   the two are read together.

The correction matters in both directions: **an inaccurate registration is a
defect whether it under-declares or over-declares, and the retracted version
would have misled the owner making the merge decision.** Authorization is the
owner's merge of the pull request carrying this registration; no agent may cite
this text as authorization.

**Provenance.** Surfaced as a named follow-on in WO-146, which recorded the
question and explicitly declined to answer it. The mechanism: the predicate
returns `True` when `cfg.trading_mode == "live"` **or** a non-empty
`maker_live_test.wallet_address` is configured. The deployed config carries such
an address, documented in-file as a "public identifier, read-only monitoring"
(`polymarket_predictive_config.example.yaml:477`). The system is paper/dry-run
with binding capital of exactly zero, and `AGENTS.md` records that funding is
closed and WO-67 blocked, so `live_capital_context: true` describes a posture the
system does not have. The code itself anticipates the question at `:223-226`
("deliberately left conservative rather than renamed to match the paper-only
posture"). The owner directed the correction on 2026-08-01.

**Design — the correction must not remove the guard for FUTURE wallets.** The
naive fix (drop the `wallet_address` term, leaving `trading_mode == "live"`)
would mean that any wallet configured later — including one that does hold
capital — no longer selects the conservative ceiling until someone also flips
`trading_mode`. That trades one wrong answer for a worse one. Instead:

- Add one registered boolean to the `maker_live_test` block:
  `wallet_address_read_only_monitoring`, **default `false`**.
- `_live_capital_context` returns `True` **unless** `cfg.trading_mode` is in the
  allowlist `{"paper", "backtest"}` AND (no non-empty `wallet_address` is
  configured OR `wallet_address_read_only_monitoring` is exactly `True`).
  **Allowlist, not denylist — corrected 2026-08-01 after review.** A first draft
  keyed the conservative branch on `trading_mode == "live"`, which silently
  treated `trading_mode: "off"` — a valid value (`config.py:95-96`) — as
  non-live-capital, and would have tripped this WO's own registered revert
  signature on a legitimate configuration. An allowlist closes `off`
  conservatively at zero cost and makes the predicate and the failure signature
  agree by construction rather than by two lists that can drift.
- **The default is the conservative branch.** An address added without the flag
  still selects the 24h ceiling, exactly as today. Only an explicit, dated
  declaration that a specific address is read-only downgrades it.
- The deployed config sets the flag `true` for the currently configured address,
  with an inline comment naming the date and the reason.

**A2 — every input branch, and the ambiguous ones fail CONSERVATIVE (24h).**
Flag absent → `False` → live context → 24h. Flag present but not a bool
(string `"true"`, `1`, `None`, empty) → **treated as `False`** → 24h; only a
genuine boolean `True` downgrades, and `boolish` coercion is explicitly NOT used
here because a safety-bound selector must not be flipped by a loose string.
`wallet_address` empty or whitespace-only → the term is inert regardless of the
flag. `trading_mode == "live"` → `True` unconditionally, flag ignored — **this
branch can never be downgraded by any config value** and has its own test.

**Touch ONLY these files** (`git diff --stat` must show exactly these three):
- `src/polymarket_predictive_engine/disaster_recovery.py`
- `polymarket_predictive_config.example.yaml`
- `tests/polymarket_predictive_engine/test_disaster_recovery.py`

Do NOT touch `active_rpo_hours` (24.0), `paper_stage_max_rpo_hours` (168.0),
`pre_live_max_rpo_hours` (24.0), the clamps at `:90-91`, `_validated_rpo`'s
comparison at `:217`, the watchdog registration, or WO-146's
`archive_build_interval_hours`. **The ceiling VALUES do not move; only which one
is selected.** If WO-146 has merged first, `active_rpo_hours` stays 24.0 and the
20.0h build cadence stays 20.0h — this WO widens the compliance bound, not the
build interval, and the two are deliberately independent.

**Fail-safe sentence.** A missing `wallet_address_read_only_monitoring`, a
non-boolean value of any type, or any doubt about its meaning selects the
conservative 24-hour pre-live ceiling, which is today's behaviour; only an
explicit boolean `True` beside a configured address selects the 168-hour
paper-stage ceiling; `cfg.trading_mode == "live"` selects the conservative
ceiling unconditionally and cannot be downgraded by any config value; the three
ceiling literals are unchanged and their tighten-only clamps still apply; and no
gate, sizing, eligibility, or order surface reads this predicate.

**Tests (enumerated; rewritten 2026-08-01 — the first set contained one test
that would FAIL against the corrected mechanism and one that was vacuous).**

(1) no flag, address configured → `live_capital_context is True`,
`maximum_rpo_hours_for_context == 24.0` — today's behaviour preserved by default.
(2) flag `true`, address configured, `trading_mode: paper` → `False`, `== 168.0`.
(3) flag `true` but `trading_mode: live` → `True`, `== 24.0` — **the branch that
can never be downgraded.** (4) flag `true` but `trading_mode: off` → `True`,
`== 24.0` — the allowlist case; `off` is valid and must close conservatively.
(5) flag as the string `"true"` → `True`/24.0 (not coerced). (6) flag as `1` →
`True`/24.0. (7) flag `None` → `True`/24.0. (8) empty `wallet_address` with flag
`true`, `trading_mode: paper` → `False`/168.0 (term inert either way).

**(9) THE CENTREPIECE — the only test that exercises what actually changes.**
With flag `true` and `trading_mode: paper`, a config setting
`active_rpo_hours: 100` **validates** (no `ValueError`), where the identical
config raises today at `:205-210`. And with the flag absent, the same config
still raises. This is the latent clamp widening, asserted directly.

**(10) The applied bound does NOT move.** With flag `true` and a 30-hour-old
archive, `rpo.compliant is False` — **identical to today**, because compliance
compares against `active` (24.0), not against `maximum_rpo_hours_for_context`.
*(The retracted test 8 asserted the opposite and would have failed; the retracted
test 9 asserted a 200h archive is non-compliant, which is true against any
ceiling ≤200h and therefore proved nothing.)*

(11) Boundary pair on the reported field only: `maximum_rpo_hours_for_context`
reads `168.0` with the flag and `24.0` without it, on otherwise identical config.
(12) byte-identity: `active_rpo_hours == 24.0`, `paper_stage_max_rpo_hours ==
168.0`, `pre_live_max_rpo_hours == 24.0` in both code defaults and the example
config.

**Day-after check.** After deploy: `disaster_recovery_status.json` →
`rpo.live_capital_context is false`, `rpo.maximum_rpo_hours_for_context ==
168.0`, `rpo.paper_stage_max_rpo_hours == 168.0`, `rpo.pre_live_max_rpo_hours ==
24.0`, and `rpo.active_rpo_hours == 24.0` — **the last one is the important
observation: the applied bound must be unchanged.** `rpo.compliant` must behave
exactly as it did the day before for the same observed age.

**Failure signature:** if `live_capital_context` reads `false` while
`cfg.trading_mode` is outside the allowlist `{"paper", "backtest"}`, the
predicate has been over-loosened and the WO is REVERTED, not tuned. *(The
allowlist here and the predicate's allowlist are the same set by construction —
an earlier draft used a `live`-only denylist in the predicate against a
`paper`/`backtest` allowlist here, which would have declared a REVERT on a
legitimate `trading_mode: off` configuration.)*

### Named follow-on — a guard that contributes zero tests when a dependency is missing

`tests/test_polymarket_vps_docker.py:10` is a module-level `import yaml`, so a
missing module is a **collection error**: the file contributes zero tests and
pytest still exits green on everything else. That is the same fail-open shape
A2 exists to close, applied to the test suite itself rather than to a threshold.
**It is also a dispatch precondition, not a nuisance:** that file appears in
BOTH WO-143's eleven-file list and WO-149's seven-file list, so if it cannot
collect in the build container, neither WO's registered scheduler and compose
tests can produce evidence there. Register a collection-error gate — a
`--strict` collection failure, or an assertion that the expected test count
actually ran — so a silently-absent guard cannot read as a pass. Not built here.

**Interaction with WO-146, stated explicitly.** WO-146 fixes the build cadence so
the archive rebuilds before its age reaches the ceiling. WO-150 widens which
ceiling applies. **They are independent and WO-146 is not made redundant:** with
a 168h ceiling and a 24h build trigger the archive would still be rebuilt only
after 24h, and the `disaster_recovery_not_recoverable` incident would stop firing
for the wrong reason — because the bound moved, not because recovery improved.
WO-146 is the fix; WO-150 is a separate correction of what the bound describes.
Both should land.

## WO-151 — The maker study has no cadence of its own: two trigger paths, one gated behind a 43.75%-failing harvest — `queued` (registered 2026-08-02; scheduler + harvest-step surface → OWNER MERGE after line-audit; observability and scheduling only, no gate/threshold/eligibility change; registered-ancestry: b6b9b88 ancestor-of 18a0fec PASS — first dispatch; registered-ancestry: b6b9b88 ancestor-of 27e401e PASS — respin)

**Provenance — measured, 2026-08-02.** M-A banks a day only if the day's LAST
run holds target, so *when* the study runs is load-bearing for the campaign. It
turns out the study does not have a schedule; it has two unsynchronised
triggers and a third accidental one:

1. **The dedicated job.** `run_maker_study_intraday` fires only when
   `seconds_since_stamp maker_study_intraday >= MAKER_STUDY_INTRADAY_INTERVAL`
   (86400s default, `run_vps_ops_scheduler.sh:38`) **and** `TRAINING_AGE` — the
   age of the last SUCCESSFUL `training_harvest` — sits inside
   `[39600, 46800]` seconds, i.e. an 11-13h post-harvest window
   (`run_vps_ops_scheduler.sh:796-814`, offsets at `:39-40`).
2. **The harvest's own embedded step.** `training_harvest.py:75` runs
   `maker-carry-study` as step 10 of 27, so the study also fires whenever the
   harvest runs, including retries. Verified end to end: the harvest step ran
   `2026-08-01T22:17:55Z`→`22:18:46Z` and the `maker_carry_history.csv` row
   stamped `22:17:57Z` lines up to the second.
3. **Scheduler restarts.** `status.json`'s `scheduler` job records
   `last_run_utc: 2026-08-01T09:53:55Z` — a process restart ~2 minutes after
   that day's second study row. Correlation only, but it is a third path by
   which a study run appears outside either registered cadence.

**Measured consequences.** Observed cadence is 3.91 runs/day (range 1-14, median
inter-run gap 4.7h, max 23.6h) — consistent with neither an 86400s interval nor
any single schedule. `status.json` shows `maker_study_intraday` with
`runs_total: 19` and `skipped_cycles_total: 12`: **~39% of due checks are
skipped by the window or overrun guard.** And the gate depends on a harvest with
`failed_cycles_total: 14` against `runs_total: 32` — **43.75% failed cycles**.
Two ways the window closes and never opens: the harvest keeps failing so
`TRAINING_AGE` climbs past 46800s, or the harvest succeeds again before the
window opens and resets the clock near zero. The latter is what produced the
~20h-stale `maker_study_intraday` stamp observed on 2026-08-02 while the study
itself was demonstrably running.

This is the "harvest-window availability trap" an earlier sweep named. **A
search of the tree found no code implementing a fix and no work order resolving
it** — only the WO-117 comment at `run_vps_ops_scheduler.sh:799-810` explaining
the overrun-tolerance workaround, which addresses a different failure.

**Purpose.** Give the study a cadence that does not depend on a 43.75%-failing
upstream job, and make its trigger path observable. Does **NOT** build: any
change to `maker_min_book_history_hours` (48.0), `maker_min_book_snapshots`
(100), `target_net_usd_per_day` (3.33), `max_trusted_reward_share` (0.05),
`_measurement_eligible`, `_size_portfolio`, the five sizing predicates, the
rewarded-universe pull, `training_harvest`'s step list or ordering, or any
order/signer/credential surface. **No gate, threshold, or eligibility rule moves
in either direction, and this WO does not attempt to make any day bank.**

**Scope.** (a) Record which trigger produced each study run: a
`study_trigger` field on `maker_carry_study.json` and on each
`maker_carry_history.csv` row, with a closed domain of exactly
`{"intraday_job", "harvest_step", "cli", "unknown"}` — `"unknown"` when the
producer cannot establish it, never guessed. (b) Decouple the dedicated job's
window from harvest SUCCESS so a failing harvest cannot starve it indefinitely,
**without** changing what the study computes. (c) Surface
`skipped_cycles_total` and the window state in the artifact so starvation is
visible rather than inferred from a stale stamp.

**A1/A2 obligations for the builder.** Any interval, window, or age bound
introduced carries a literal and a stated basis; every comparison states what a
missing, empty, unparseable, or non-finite stamp does. A due-stamp read through
the shared `seconds_since_stamp` convention follows WO-120's registered
fail-**open** reading — missing or corrupt reads as immediately due, so the job
fires rather than being permanently starved by one corrupt cadence stamp
(ratified at §151.2, performed inline here and at §151.1) — while a study that
cannot establish its own trigger still records `"unknown"` and does not claim a
cadence it did not have.

**Fail-safe sentence.** *(The job-stamp half of the clause below is SUPERSEDED
by §151.2, added 2026-08-04 — see that section for the adjudication. The
harvest-stamp half, per test (8), is unchanged.)* A missing, empty,
unparseable, or non-finite harvest stamp never blocks the decoupled dedicated
job from firing, per test (8); a missing, empty, unparseable, or non-finite
job due-stamp (`maker_study_intraday`) reads as due and the job fires, per
§151.2's WO-120 fail-open ratification; either path records the window state
rather than assuming it silently; a study run whose trigger cannot be
established records `study_trigger: "unknown"` rather than guessing; nothing here
changes what the study computes, which markets it measures, or any gate,
threshold, or eligibility rule; and a failure of this work order's machinery
degrades only the visibility of when the study ran, never the study itself.

### 151.1 — Touched-file list and enumerated tests (added 2026-08-02; WO-151 was registered without either and was therefore NOT dispatchable)

**Why this amendment exists.** A pre-dispatch A10 sweep found WO-151 carried a
Day-after check and a Fail-safe sentence but **no touched-file list and no
enumerated tests**, so it failed A10 and could not be built. It was caught
before a builder was dispatched against it, which is the check working; it is
recorded here rather than quietly fixed.

**Touch ONLY these files** (`git diff --stat` must show exactly these six):

- `src/polymarket_predictive_engine/maker_carry_study.py` — scope (a) the
  `study_trigger` field on `maker_carry_study.json` and on each
  `maker_carry_history.csv` row, and scope (c) the `skipped_cycles_total` and
  window-state surfacing. **The five `_size_portfolio` predicates,
  `_measurement_eligible`, the rewarded-universe pull and every threshold in
  this module are OUT of scope and must be byte-identical.**
- `src/polymarket_predictive_engine/training_harvest.py` — the embedded
  `maker-carry-study` step at `:75` must pass `study_trigger="harvest_step"`.
  **The step list and its ordering do not change** — the parent WO's exclusion
  stands; only the argument passed to the existing step is added.
- `scripts/run_vps_ops_scheduler.sh` — scope (b), decoupling
  `run_maker_study_intraday`'s window from harvest SUCCESS. The relevant lines
  today are the interval at `:38`, the window guard at `:841-856`, and the
  status stamp at `:635`.
- `tests/polymarket_predictive_engine/test_maker_carry_study.py`
- `tests/polymarket_predictive_engine/test_training_harvest.py`
- `tests/test_polymarket_vps_docker.py` — added 2026-08-04 (§432 round-1, review-driven
  scope expansion). The pinned `stamp_status` detail-string literal at `:621` must be
  updated to match the accurate standalone-cadence prose written into
  `scripts/run_vps_ops_scheduler.sh:635` (replacing the stale *"...11-13h offset guard"*
  wording left over from the removed offset guard). The scheduler detail string and this
  test's pinned literal change together, in the same build, or neither does.

**Do NOT touch** `maker_min_book_history_hours` (48.0), `maker_min_book_snapshots`
(100), `target_net_usd_per_day` (3.33), `max_trusted_reward_share` (0.05),
`_measurement_eligible`, `_size_portfolio`, the rewarded-universe pull, or any
order/signer/credential surface. **No gate, threshold or eligibility rule moves
in either direction, and this WO does not attempt to make any day bank.**

**The decoupling literal, with its basis (A1).** The dedicated job's window
condition changes from *"`TRAINING_AGE` inside `[39600, 46800]`"* to *"the
`maker_study_intraday` stamp is at least `MAKER_STUDY_INTRADAY_INTERVAL` old"*
alone — the harvest-age offset guard is **removed as a precondition**, not
widened. Basis: the offset guard exists to place the study after a fresh
harvest, but the harvest fails 43.75% of cycles (`runs_total: 32`,
`failed_cycles_total: 14`), and a precondition on a job that fails almost half
the time is a starvation source, not a freshness guarantee. The study reads
whatever corpus exists; it does not require a harvest to have just succeeded.
**The interval literal `86400` is unchanged.** The observed harvest age is
**recorded in the artifact** rather than gating on it, so the relationship stays
measurable.

**A2 for every new comparison.** A missing, empty, unparseable, or non-finite
`maker_study_intraday` stamp reads as **due** — the shared `seconds_since_stamp`
reader's registered WO-120 convention (a missing stamp reads `999999999`s old, a
corrupt or empty stamp reads epoch `0`, both `>= interval`) applies to this
stamp exactly as it does to every other stamp in the file, so the job fires
rather than being starved forever by one corrupt due-stamp (ratified at
§151.2) — and the window state is recorded on every path rather than assumed
silently. A run whose trigger cannot be established records
`study_trigger: "unknown"` and never guesses.

**Tests (enumerated).** (1) a study run invoked by the dedicated job records
`study_trigger: "intraday_job"`; (2) invoked as harvest step 10, it records
`"harvest_step"`; (3) invoked directly via the CLI, `"cli"`; (4) a run whose
trigger cannot be established records `"unknown"` and does **not** guess;
(5) every value written is inside the closed domain
`{"intraday_job", "harvest_step", "cli", "unknown"}` — a value outside it is a
test failure, not a new domain member; (6) the field appears on **both**
`maker_carry_study.json` and every new `maker_carry_history.csv` row;
(7) pre-existing `maker_carry_history.csv` rows without the column still parse —
the reader tolerates the legacy header; (8) with the harvest stamp absent,
unparseable, non-finite, and 200000s old, the dedicated job still fires once its
own interval has elapsed — the decoupling, and the test that proves starvation
is closed; (9) with the `maker_study_intraday` stamp absent or unparseable the job
**fires** — WO-120's fail-open convention applied to the job's own due-stamp,
ratified at §151.2 — and the window state is recorded (A2 fail-open);
(10) `skipped_cycles_total` and the window state appear in the artifact and match
the scheduler's own counter; (11) the observed harvest age is recorded in the
artifact on every path; (12) byte-identity — a study run over a fixed fixture
produces identical `net_carry_usd_per_day`, `portfolio_markets` and
`portfolio_net_carry_usd_per_day` before and after this WO, proving the study's
economics are untouched.

**Day-after check.** Every `maker_carry_history.csv` row written after deploy
carries a `study_trigger` inside the closed domain; the distribution of
`study_trigger` over one week shows whether the harvest step or the dedicated
job is the dominant producer; and `maker_study_intraday`'s `last_success_utc`
no longer goes stale while study artifacts are being produced by another path.
**Failure signature:** if `study_trigger` reads `"unknown"` on more than a small
minority of rows, the producer cannot see its own trigger and the WO is
REVERTED, not tuned.

### Recorded diagnosis, NOT actioned — the 2026-07-21 regime break

`maker_carry_history.csv`, all 90 rows: `portfolio_markets` distribution
`{0: 17, 1: 42, 2: 21, 3: 10}`. Split by date:

- rows **before 2026-07-21** (53 rows, from 2026-07-10): **0 of 53 at zero (0%)**
- rows **from 2026-07-21 onward** (37 rows): **17 of 37 at zero (45.9%)**

That break falls one day after commit `62227f6`, *"WO-113: measurability-aware
maker portfolio (book-history eligibility + stickiness + coverage-window
alignment)"*, merged 2026-07-20. **This is a raw correlation and is recorded as
one.** It is entirely consistent with WO-113's registered gate working exactly
as designed — excluding markets whose carry cannot be honestly measured — in
which case the 46% zero rate is the gate telling the truth about the universe,
not a defect. **No change to WO-113 is proposed, and none may be made on the
strength of a date correlation.** It is registered here so that the single
largest structural feature of the series is on the record rather than
rediscovered.

### Recorded observation — the current universe contains nothing that qualifies

From the 2026-08-01T22:17:57Z `maker_carry_candidates.csv` (40 measured
candidates): **zero of 40 pass all five `_size_portfolio` predicates.** 31 of 40
have `net_carry_usd_per_day <= 0`; 30 of 40 fail `_measurement_eligible`. The 8
positive-carry rows are **all** either `thin_book_untrusted` or carry
`book_history_hours: 0.0`. **Every seasoned candidate has negative carry, and
every positive-carry candidate is too new to be trusted.** That is a property of
the current universe, independent of time of day or trigger path, and no
scheduling change in this WO can alter it.

### What the data could not settle, recorded so it is not re-asked

The oscillation is **both** patterns in different markets, and the artifacts
cannot collapse them to one. NVIDIA left via `not_in_rewarded_universe`
(`maker_carry_study.py:1666-1667`) — excluded upstream of all five predicates,
not a predicate flip — and `universe_rewarded_markets` moved 130→124→121 across
three runs 43 minutes apart, so the upstream pull is intrinsically volatile. But
the Fed-rate market on 2026-07-29 cycled IN (10:53), IN (11:24), OUT (14:13), IN
(16:40), IN (17:37), OUT (18:39) inside one UTC day — razor-thin intraday
margins. And a continuous 8-run zero streak from 2026-07-29T20:22Z through
2026-07-31T22:02Z spans both morning and evening timestamps, which argues
against a clean hour-of-day story for that stretch. **Blocking gap:** no
historical per-run `maker_carry_candidates.csv` exists — only the current
snapshot — so predicate-level "why did X leave" is answerable for the latest
departure only. WO-148's tier-event ledger is the closest existing remedy;
a per-run candidate archive would be its complement and is not proposed here.

### 151.2 — Test (9)'s fail-direction was wrong; the builder's inversion is ratified (added 2026-08-04 from the WO-151 build escalation and its independent line audit)

**The contradiction, as adjudicated.** §151.1's A2 paragraph — *"A missing,
empty, unparseable, or non-finite `maker_study_intraday` stamp leaves the job
**un-fired** and records the window state; it never reads as 'due'"* — and
test (9) — *"with the `maker_study_intraday` stamp absent or unparseable the
job does **not** fire and the window state is recorded (A2 fail-closed)"* —
registered a fail-**closed** direction for the job's own due-stamp. Verified
against the tree, that direction contradicts three things: (i) **test (8)'s
opposite fail-direction for the harvest stamp in the same enumerated list** —
*"with the harvest stamp absent, unparseable, non-finite, and 200000s old, the
dedicated job still fires once its own interval has elapsed"*, i.e. fail-open;
(ii) **the §151.1 decoupling literal's sole surviving condition** — the window
condition becomes *"the `maker_study_intraday` stamp is at least
`MAKER_STUDY_INTRADAY_INTERVAL` old"* alone, the offset guard removed as a
precondition. That single comparison is evaluated by the shared
`seconds_since_stamp` reader (`scripts/run_vps_ops_scheduler.sh:208-222`),
whose registered WO-120 convention treats a missing stamp as `999999999`
seconds old and a corrupt/empty stamp as epoch `0` — both of which satisfy the
`>= interval` test, i.e. both read as **immediately due**. That is exactly the
fail-open outcome test (9) forbade, produced by the one condition §151.1 left
standing; and (iii) **the parent WO's fail-safe sentence**, which paired
"harvest or job stamp" under a single "leaves the dedicated job un-fired"
clause even though test (8) already required the harvest half of that same
clause to be false. A literal test (9) would require `maker_study_intraday`'s
own call to `seconds_since_stamp` to behave oppositely to every other call in
the file — `governance_refresh`, `clv_snapshot`, `locked_card_refresh`,
`trade_prints`, `book_pulse`, `ledger_anchor`, `maker_safety_refresh` — which
is exactly the uniform convention WO-120 exists to guarantee.

**The starvation argument.** The only writer of the `maker_study_intraday`
stamp is `touch_stamp maker_study_intraday` (`scripts/run_vps_ops_scheduler.sh:856`),
inside the job's own fire branch — there is no independent repair path. Under
a literal test (9), one corrupt stamp file permanently disables the job: it
can never again read as due, so it can never fire, so it can never rewrite its
own stamp. That is precisely the stall class WO-120 was registered to close.
Firing a read-only study on a corrupt cadence stamp risks at worst one extra
run; it moves no gate, threshold, or eligibility rule.

**RATIFY.** Test (9) is re-registered with the WO-120 fail-open direction —
missing or corrupt `maker_study_intraday` due-stamp reads as due, and the job
fires — matching what the builder built and what the independent line audit
verified. The parent fail-safe sentence's clause *"A missing … or job stamp
leaves the dedicated job un-fired"* is SUPERSEDED for the job's own due-stamp;
the harvest stamp's handling per test (8) is unchanged. That sentence is
edited inline, in this same commit, within WO-151's own preamble above.
**§432 round-1 (2026-08-04):** describing a supersession is not
re-registration — §151.1's A2 paragraph and test (9)'s own text, and the
parent A1/A2 obligation sentence above, are likewise edited inline (not merely
narrated as superseded) in this same commit, within WO-151's own preamble and
§151.1. This section stands as the historical record of why the direction
changed; the live text now states the ratified direction directly.

**Precedent.** Same class as §145.2: the registered string now matches the
registered decision, recorded rather than silently fixed.

**Why this does not violate S8 A2, and what would.** A2 (`docs/ENGINEERING_STANDARDS.md`
S8) requires that every threshold comparison state what a missing, empty,
unparseable, or non-finite input does, and that the stated answer be the
fail-**closed** branch. §151.2's ratification of test (9) does not weaken that
rule; it fixes what the rule's object is for this comparison. A2's fail-closed
rule governs threshold comparisons on **measurement and health paths** — an
unverifiable input must never read as **healthy**, and must never mark
anything as **measured**. The `maker_study_intraday` due-stamp is neither: it
is a **scheduler-liveness** input, and for a liveness input "fail closed"
means something structurally different — the job never fires again after one
corrupt stamp write, i.e. permanent, silent disablement of the very
measurement A2 exists to protect, because the stamp's only writer is the job's
own fire branch (`scripts/run_vps_ops_scheduler.sh:856`) and there is no
independent repair path. The WO-120 convention — an unreadable stamp reads as
immediately due — is the established, registered, tested convention for
exactly this input class, applied uniformly to every other due-stamp in the
same file (`governance_refresh`, `clv_snapshot`, `locked_card_refresh`,
`trade_prints`, `book_pulse`, `ledger_anchor`, `maker_safety_refresh`).
Firing a read-only study early on a corrupt cadence stamp marks nothing
measured and moves no gate, threshold, or eligibility rule.

Stated explicitly: this is a **scope clarification of A2's object**, not a
weakening of A2. A fail-open reading of any **measurement** input remains
forbidden — what would violate A2 is a fail-open reading applied to a
measurement or health comparison itself (for example, a corrupt
`book_history_hours` reading as fresh, or a corrupt safety-refresh stamp
reading as healthy), because there an unverifiable input would be allowed to
claim something was measured or something is safe. §151.2 ratifies neither of
those; it ratifies a scheduling-liveness due-stamp reading as due.

A clarifying note for `docs/ENGINEERING_STANDARDS.md` S8 A2 — "A2 binds
measurement/health comparisons; scheduler liveness stamps follow the WO-120
convention" — was **proposed here but not edited into the standard by this
PR**; that binding-standard text change goes through its own owner-merge PR
(the governance path), so this section registered the proposal without
pre-empting that process. **That PR now exists: #434** (`claude/register-s8-a2-liveness-clarification`),
appending the clarification to S8 A2 itself. **Merge order: #434 → #432 → #433**
— the standard's own text change lands first, this section's ratification
next, then the WO-151 build.

**Folded into §151.1's touched-file list, not deferred (§432 round-1,
2026-08-04).** `run_maker_study_intraday`'s `stamp_status` detail string
(`scripts/run_vps_ops_scheduler.sh:635`) still reads *"...11-13h offset
guard"* — stale prose left over from the removed offset guard, pinned as a
literal by `tests/test_polymarket_vps_docker.py:621`. Rather than leave this to
a follow-up WO, §151.1's touched-file list is extended to six files (adding
that test file): the detail string and the pinned docker-test literal are
updated together, in the same build, or neither is. Recorded as a scope
expansion of this same WO, review-driven.

**The same sixth-file scope also covers the WO-117 overrun-classification
test, missed by the reconciliation above.**
`test_wo117_maker_study_overrun_classification_is_window_aware`
(`tests/test_polymarket_vps_docker.py:965-980`) asserts that
`MAKER_STUDY_WINDOW_TOLERANCE=$((MAKER_STUDY_INTRADAY_OFFSET_MAX -
MAKER_STUDY_INTRADAY_OFFSET_MIN))` is defined in
`scripts/run_vps_ops_scheduler.sh`, that
`schedule_skip_kind maker_study_intraday $((MAKER_STUDY_INTRADAY_INTERVAL +
MAKER_STUDY_WINDOW_TOLERANCE))` is the call made there, and it forbids the
bare-interval call `schedule_skip_kind maker_study_intraday
"$MAKER_STUDY_INTRADAY_INTERVAL"`. §151.1's decoupling literal removes the
harvest-age offset window as a precondition entirely, so once built there is
no offset width left to derive an interval-plus-window tolerance from, and the
overrun classification collapses to exactly the bare-interval call this test
currently forbids. These assertions must be **replaced**, not left standing,
to pin the new standalone-interval `schedule_skip_kind` call form, and
`MAKER_STUDY_WINDOW_TOLERANCE` is removed or repurposed to whatever the
standalone-interval build actually needs. This is part of the same sixth-file
scope above, not a seventh file.

**The same sixth-file scope also covers `test_wo117_window_tolerance_boundary_semantics`
(`tests/test_polymarket_vps_docker.py:984-1015`), missed by the same
reconciliation.** This test pins the tolerance-widened boundary directly:
`kind(90000, 86400) == "overrun"` (bare interval mislabels an on-window run),
`kind(90000, 86400 + 7200) == ""` (interval + tolerance reads it correctly as
on-schedule), and `kind(86400 + 7200 + 300 + 60, 86400 + 7200) == "overrun"`
(genuine starvation past the widened bound still stamps). Once
`MAKER_STUDY_WINDOW_TOLERANCE` is removed per the reconciliation above, there
is no widened bound left for the middle two calls to exercise — the
`effective_interval` argument `schedule_skip_kind` receives collapses to the
bare interval alone. These three boundary assertions must be **replaced**,
not left standing, with the standalone-interval build's own boundary
expectations: on-schedule at and immediately after the bare interval, and
`"overrun"` only once a tick past it — the two-region (bare-interval-overrun
vs. widened-on-schedule) shape this test currently proves is retired along
with the tolerance it measures. This is part of the same sixth-file scope
above, not a seventh file.

## WO-154 — Current maker-carry portfolio members must always be MEASURED, not merely yield-ranked, by `_yield_first_shortlist` — `queued` (registered 2026-08-05; touches the maker-carry study's selection surface → OWNER MERGE after line-audit; measurement-coverage fix that nonetheless changes WHICH markets can occupy the sized portfolio versus today — owner must ratify this framing at registration, not only the code; v3 — the v1 draft's S8 admission gate returned NOT ADMISSIBLE with 5 blockers (B1-B5) and 6 non-blocking findings (N1-N6); the v2 redraft's second S8 admission pass returned NOT ADMISSIBLE on exactly two items (B-v2-A, B-v2-B) plus 3 non-blocking findings (N-v2-1..3); all fixed here per both gates' rulings)

**Provenance.** A class-X trace (2026-08-05) found the maker-carry portfolio
empty at 2026-08-04T23:02:13Z not because its member — the Hormuz-traffic
market, $3.06/day net carry, `maker_carry_history.csv` rows 97-98
(verified-from-mirror, `origin/vps-telemetry`, this session) — failed a
predicate, but because it was never measured. `_yield_first_shortlist`
(`maker_carry_study.py:1096-1193`) ranks the rewarded universe by expected
gross reward at min size and passes only the top `max_book_candidates` (40,
`polymarket_predictive_config.example.yaml:114`) into the expensive
history/markout measurement loop. An incumbent portfolio member that ranks
outside that top 40 is never measured this run and is evicted with
disposition `not_in_candidate_scan` (`maker_carry_study.py:1665`, inside
`_portfolio_composition_diff`, `maker_carry_study.py:1611-1684`) — a
disposition that, by construction, means "we don't know if it still
qualifies," not "it failed."

**Two corrections to the triaging trace, recorded rather than silently
folded in.** (1) The trace's claim that "the same mechanism evicted NVIDIA
on 2026-08-01" does not match the register: WO-151's own recorded text
states NVIDIA left via `not_in_rewarded_universe`
(`docs/POLYMARKET_CODEX_WORK_ORDERS.md:10416-10417` — corrected from
`10395-10396` in the 2026-08-05 rebase over main's §151.2 merge, which
shifted WO-151's internal lines by +21; text at the cited lines is
unchanged) — excluded upstream of all five predicates, in `_rewarded_universe`,
which this WO does not touch.
NVIDIA is not evidence for this WO and must not be cited as such at
registration. (2) The trace's claim that the union "may exceed 40 only by
the portfolio size, itself capped by `max_markets=25`" is not grounded:
`max_markets` does not appear anywhere in `maker_carry_study.py`, and the
only `max_markets: 25` in config lives under the unrelated
`maker_fill_replay` block (`polymarket_predictive_config.example.yaml:175`,
official-book collection breadth, WO-116), never read by `_size_portfolio`
or `_yield_first_shortlist`. There is no configured ceiling on portfolio
member count (A1 below).

**Purpose.** Give `_yield_first_shortlist` full measurement coverage of the
CURRENT portfolio, so a paying incumbent can never be evicted with
`not_in_candidate_scan` while it is still present in the rewarded universe.
This is a measurement-coverage fix, not an eligibility change: it does not
touch, weaken, or strengthen any of the five `_size_portfolio` predicates,
the $500 capital cap, the $1.00 payout floor, `maker_min_book_history_hours`
(48), `maker_min_book_snapshots` (100), `maker_switch_margin_frac` (0.25),
`maker_max_hold_days` (30), or the rewarded-universe pull. It builds no new
gate, no new order surface, no new collector, and no new cadence — the
study keeps the cadence WO-151 already gives it. **Disclosed effect the
owner must ratify at registration:** because a previously scan-evicted
incumbent is now measured instead of skipped, a market that would
previously have silently lost its portfolio slot (unmeasured, hence unable
to pass any predicate) can now be RETAINED if it still passes all five
predicates. The set of markets that can benefit is bounded to the
intersection of (still in this run's rewarded pull) ∩ (a member of either
of the last two readings of `maker_carry_portfolio_members.csv` — see B3
below) — no market that was not both currently rewarded AND a recent
portfolio member can benefit — but within that intersection this is a real
change in which markets can hold the sized portfolio, not a pure
observability fix. It is NOT a tighten-only amendment and must be flagged
FROZEN with this direction disclosed.

### Touch ONLY these files (`git diff --stat` must show exactly these two)

- `src/polymarket_predictive_engine/maker_carry_study.py`
- `tests/polymarket_predictive_engine/test_maker_carry_study.py`

**Explicitly untouched, and why (S8 B3 — corrected).**
`_portfolio_composition_diff` (`maker_carry_study.py:1611-1684`) needs NO
code change. Its existing branch order resolves to `measured_not_sized` /
`excluded_stale:*` / `excluded_resolution_risk` / `excluded_thin_book` for
anything present in `candidate_by_id`, and reaches `not_in_candidate_scan`
only when a departed condition_id is in `rewarded_ids` but NOT in
`measured_ids` (`:1664-1665`). The v1 draft claimed this becomes
unreachable once `_incumbent_hold`'s output alone is unioned into
`measurement_universe` — the S8 gate (B3) proved that FALSE with three
executed fixtures where `_incumbent_hold`'s `incumbents` and the diff's own
`prior` set (built from a DIFFERENT reader, `_latest_portfolio_members`, of
the SAME file) disagree, leaving a genuine prior member unprotected. This
redraft closes the gap by construction instead of by hoping the two readers
agree: `protected_condition_ids` is the UNION of BOTH readers' outputs (see
Change item 3), so `prior ⊆ protected_condition_ids` on every path — the
diff's `prior` variable is literally one of the union's two inputs, not a
third, independently-drifting set. Therefore, for any condition_id in
`prior` that is also in `rewarded_ids` this run (i.e. present in `universe`,
the same object as `rewarded_universe` at the diff call, `:2486`), the union
step in `_yield_first_shortlist` is guaranteed to add it to
`measurement_universe`, so the `not_in_candidate_scan` branch's own
condition (`in rewarded_ids and not in measured_ids`) is false. No edit to
`_portfolio_composition_diff` itself is needed for this to hold.
`ledger_anchor.py` and the config example are NOT touched: no new artifact,
no new schema, no new cadence.

### Reads (do not write to these)

- `outputs/maker_carry/maker_carry_portfolio_members.csv` — via TWO EXISTING
  readers of the same file, both already used elsewhere in
  `run_maker_carry_study`; this WO adds no new parser, it only threads their
  already-computed return values one call earlier and combines them:
  - `_incumbent_hold(out_root)` (`maker_carry_study.py:1551-1588`) →
    `(incumbents: set[str], incumbent_hold_days: dict[str, int])`.
  - `_latest_portfolio_members(out_root)` (`maker_carry_study.py:1591-1608`)
    → `tuple[str, dict[str, bool]] | None`: `(generated_at_utc,
    {condition_id: markout_measured})`. Already wrapped today (`:2206-2211`)
    in a `try/except Exception` that sets `previous_portfolio = None` and
    records `previous_portfolio_error`; this WO moves that block, unchanged,
    to run earlier and reuses both variables at their existing use-sites.
  - `protected_condition_ids = set(incumbents) | (set(previous_portfolio[1])
    if previous_portfolio else set())` — the fail-closed UNION (S8 B3).

### Writes

- `outputs/maker_carry/maker_carry_study.json` — ADD exactly two new keys,
  placed immediately after the existing `"yield_scan_fallback"` key
  (`maker_carry_study.py:2317`): `"yield_scan_protected_members_considered"`
  (int — `len(protected_condition_ids)` passed into
  `_yield_first_shortlist` this run) and
  `"yield_scan_protected_members_added"` (int — count of protected members
  appended to the shortlist that were NOT already present via yield-rank
  selection or the existing pot-rank backfill). This file is rewritten whole
  every run via `write_json` and carries no `ledger_anchor.py` entry
  (verified: no `maker_carry_study.json` glob anywhere in
  `ledger_anchor.py`) — adding keys here is anchor-safe.
- **Do NOT add either field to `maker_carry_history.csv`.** That file is
  registered `snapshot` in `ledger_anchor.py:62`, and WO-111's own comment in
  this module (`maker_carry_study.py:2407-2419`) deliberately rejected
  adding any column to it, choosing a separate sidecar instead, "so its
  ledger-anchor prefix never changes." This WO follows the same discipline:
  no column change to `maker_carry_history.csv`, `maker_carry_candidates.csv`,
  or `maker_carry_portfolio_members.csv`.

### Change (exact)

1. `_yield_first_shortlist` gains one new keyword-only parameter:
   ```python
   def _yield_first_shortlist(
       settings: dict[str, Any],
       universe: list[dict[str, Any]],
       fractions: list[float],
       protected_condition_ids: set[str] | frozenset[str] | None = None,
   ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
   ```
   Default `None` (treated as empty). The two existing call sites in
   `tests/polymarket_predictive_engine/test_maker_carry_study.py:279` and
   `:303` call this function with exactly 3 positional args and must keep
   passing unmodified — the byte-identity regression proof for the default
   (no-protection) path.

2. **Restructure ALL FOUR return points through one shared helper (S8
   B5/N6 — this is not optional style; a partial copy-paste of the union
   step into only some returns is exactly the defect class B5 found).**
   Immediately after normalizing `protected_condition_ids`, define a nested
   closure that captures `universe`/`protected_condition_ids` and is the
   ONLY place the union logic exists:
   ```python
   protected_condition_ids = protected_condition_ids or frozenset()

   def _apply_protection(
       selected: list[dict[str, Any]],
       selected_keys: set[str],
       diagnostics: dict[str, Any],
   ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
       # Additive union, applied ONCE per call, after selected/selected_keys
       # are final. Iterates the FULL universe (never scan_universe), the
       # same condition_id-or-token_id fallback shape the existing backfill
       # loop uses, PLUS a .strip() the backfill loop does not have (WO-154
       # Change item 6 — this closure only; the shipped backfill loop is
       # untouched). Does NOT count against max_book_candidates.
       # yield_scan_selected_markets keeps its
       # EXISTING meaning (computed by the caller BEFORE this call) - the
       # protection count is reported ONLY via the two new keys below, never
       # folded into the existing one, so it stays byte-identical when
       # protected_condition_ids is empty.
       protected_added = 0
       for market in universe:
           key = str(market.get("condition_id") or market.get("token_id") or "").strip()
           if key and key in protected_condition_ids and key not in selected_keys:
               row = dict(market)
               # Not selected via yield rank this run (never scanned/scored,
               # OR scored but ranked outside max_book_candidates) - record
               # this honestly, never fabricate a rank or gross figure.
               row["yield_rank"] = None
               row["expected_gross_at_min_size"] = None
               selected.append(row)
               selected_keys.add(key)
               protected_added += 1
       diagnostics["yield_scan_protected_members_considered"] = len(protected_condition_ids)
       diagnostics["yield_scan_protected_members_added"] = protected_added
       return selected, diagnostics
   ```
   Then route every return through it:
   - Empty-scan-universe early return (`:1104-1111`):
     `return _apply_protection([], set(), {"universe_scan_mode": "yield_first_v1", "yield_scan_considered_markets": 0, "yield_scan_scored_markets": 0, "yield_scan_selected_markets": 0, "yield_scan_fallback": False})`.
   - Book-fetch-exception fallback (`:1145-1154`) and no-scored fallback
     (`:1156-1165`) — BOTH currently build `selected = [dict(row) for row in
     universe[:max_candidates]]` with no companion key set; add one line
     before each return: `selected_keys = {str(row.get("condition_id") or
     row.get("token_id") or "") for row in selected}`, then
     `return _apply_protection(selected, selected_keys, {... unchanged dict ...})`.
   - Final `yield_first_v1` return (`:1187-1193`): `selected`/`selected_keys`
     are already fully built by the existing top-N + backfill loops;
     `return _apply_protection(selected, selected_keys, {... unchanged dict ...})`.

3. **Move BOTH membership reads earlier and compute the union (S8 B3).** In
   `run_maker_carry_study`, move the existing `_incumbent_hold(out_root)`
   call (currently `:2203`) AND the existing `_latest_portfolio_members`
   try/except block (currently `:2206-2211`) from their current position to
   immediately after `universe, errors, stale_diagnostic =
   _rewarded_universe(settings)` (`:2095`), before the `_yield_first_shortlist`
   call (currently `:2100`):
   ```python
   universe, errors, stale_diagnostic = _rewarded_universe(settings)
   incumbents, incumbent_hold_days = _incumbent_hold(out_root)
   # WO-137 reads the latest membership evidence before this run appends its
   # row. The append-only sidecar remains owned solely by the WO-111 writer.
   previous_portfolio_error: Exception | None = None
   try:
       previous_portfolio = _latest_portfolio_members(out_root)
   except Exception as exc:  # noqa: BLE001 - reporting must not take down the study
       previous_portfolio = None
       previous_portfolio_error = exc
   protected_condition_ids = set(incumbents) | (
       set(previous_portfolio[1]) if previous_portfolio else set()
   )
   ```
   The existing WO-137 comment at the old `:2204-2205` position (documenting
   the `_latest_portfolio_members` read, immediately above) travels with it
   to the new position, unchanged (N-v2-3).
   Pass `protected_condition_ids=protected_condition_ids` into
   `_yield_first_shortlist`. DELETE the now-duplicate calls at the old
   `:2203` / `:2206-2211` positions. Reuse the single earlier-computed
   `incumbents`/`incumbent_hold_days` unchanged at the existing
   `_size_portfolio(..., incumbents=incumbents,
   incumbent_hold_days=incumbent_hold_days)` call (`:2216-2218`), and reuse
   `previous_portfolio`/`previous_portfolio_error` unchanged at the existing
   composition-diff block (`:2478-2482`, `if previous_portfolio_error is not
   None: raise previous_portfolio_error` / `previous=previous_portfolio`).
   Moving both reads earlier changes neither reader's output — both are pure
   reads of an already-written CSV, and no write to
   `maker_carry_portfolio_members.csv` happens before either read; the
   append happens later, under flock, at `:2454-2468` (unaffected by this
   move — confirmed both call sites have exactly one production caller each).

4. **Fail-closed tightening inside `_incumbent_hold` (S8 B1).** Immediately
   after the existing `try/except (ValueError, TypeError)` around
   `json.loads` (`maker_carry_study.py:1564-1567`), add:
   ```python
   if not isinstance(members, list):
       members = []
   ```
   before the `cids = {...}` comprehension (`:1568-1572`). Without this, a
   `portfolio_members` cell that is VALID JSON but not a list (`"null"`,
   `"123"`, `"true"`, `"1.5"`) parses successfully and then the un-guarded
   `for member in members` raises an unhandled `TypeError`, aborting the
   entire study run — confirmed by execution against all four scalar
   fixtures. This mirrors the guard `_latest_portfolio_members` already has
   (`:1600`, `not isinstance(members, list): continue`). This is a
   fail-closed tightening of a read path inside a file already on this WO's
   touched list — nothing is loosened; the file crashes less, not more.

5. Add the two new `summary` keys as specified in "Writes" above.

6. **`.strip()` the union closure's key derivation, and nowhere else (S8
   v2 B-v2-B).** In `_apply_protection` (item 2's closure) only, change
   `key = str(market.get("condition_id") or market.get("token_id") or "")`
   to `key = str(market.get("condition_id") or market.get("token_id") or
   "").strip()`. Basis: `_portfolio_composition_diff`'s `rewarded_ids` and
   `measured_ids` derivations (`maker_carry_study.py:1646-1651`) are already
   `.strip()`'d; the closure's key was not. A `condition_id` carrying
   leading/trailing whitespace would then land in `measured_ids` under a
   different string than the diff's `rewarded_ids`/`prior` comparisons use,
   so the union step would silently fail to protect it and the
   `not_in_candidate_scan` REVERT signature (Day-after check below) could
   fire on correctly built, owner-merged code — exactly the failure mode
   Change item 3's union guarantee exists to close. This is a fail-closed
   tightening of the NEW closure introduced by this WO only: the
   already-shipped backfill loop's un-stripped key derivation
   (`maker_carry_study.py:1182`) is explicitly NOT touched by this item —
   no established, unrelated code changes.

**Docstring updates (part of item 3/4's diff, not a separate file).**
`_incumbent_hold`'s existing docstring gains one sentence stating the
corrected behavior (see Fail-safe sentence below, same wording).
`_yield_first_shortlist` currently has NO docstring — add one describing
the WO-56 prescreen plus the WO-154 `protected_condition_ids` addition and
its own fail-safe clause (same wording as the Fail-safe sentence below).

**A1 — literal thresholds, with basis.** `max_book_candidates` (40) is
UNCHANGED. **Structural per-run bound (S8 N5):** `len(measurement_universe)
<= max_book_candidates + len(protected_condition_ids)`, and since
`candidates` gains at most one row per `measurement_universe` market,
`len(candidates) <= len(measurement_universe) <=
max_book_candidates + len(protected_condition_ids)`.
`protected_condition_ids` is itself the UNION of `_incumbent_hold`'s and
`_latest_portfolio_members`'s outputs (item 3) — bounded by the size of the
last two portfolio-membership snapshots combined, never by a registered
ceiling. **There is no registered ceiling on this count** — verified: no
`max_markets` key exists in `maker_carry_study.py` or in the
`maker_carry_study` config block; `_size_portfolio` enforces no count cap,
only `capital_cap_usd` ($500, `polymarket_predictive_config.example.yaml:122`)
divided by each admitted market's data-dependent `capital_usd`, and
`min_daily_payout_usd` ($1.00, `:133`). State the true, unbounded-by-config
shape in the module docstring, and record separately, as an empirical
observation only (not a registered bound): WO-151's provenance note reports
`maker_carry_history.csv`'s 90-row `portfolio_markets` distribution never
exceeded 3 (`docs/POLYMARKET_CODEX_WORK_ORDERS.md:10385-10386` — corrected
from `10364-10365` in the 2026-08-05 rebase over main's §151.2 merge, which
shifted WO-151's internal lines by +21; text at the cited lines is
unchanged). Do not invent a ceiling that isn't there (A8).

**A2 — fail-closed for the new comparison, corrected (S8 B1).**
`_incumbent_hold`'s existing per-row `json.loads` failure already yields
`members = []` (`:1564-1567`) — but that `except` clause does NOT catch the
class where `portfolio_members` is VALID JSON that is not a list: `json.loads`
succeeds and the un-guarded set comprehension at `:1568-1572` then raises
`TypeError`, aborting `run_maker_carry_study` entirely for that run — NOT
"yielding an empty protected-member set" as the v1 draft incorrectly stated.
Item 4 above adds the missing `isinstance(members, list)` guard, closing
this. Separately: `utils.read_csv_rows` (`:98-109`) guarantees `[]` ONLY for
a MISSING or ZERO-BYTE file; an existing-but-unreadable file (a permission
error, a malformed row raising `csv.Error`) propagates exactly as it does
today — the v1 draft's "never raises" claim was overbroad and is corrected
here rather than carried forward; this WO does not change that behavior.
When both readers' contributions to the union are empty (missing sidecar,
zero-byte sidecar, or every latest row unparseable/non-list on both sides),
`protected_condition_ids == set()`, the union step adds nothing on any of
the four return points, and `_yield_first_shortlist`'s output is
byte-identical to today's pure top-N/pot-rank behavior. **No fabricated
member list is ever constructed.**

**Known, pre-existing residual (not created by this WO, not fixed by it;
referenced by the Day-after check below).** If a protected incumbent is
added to `measurement_universe` but its own `_book_competition` call fails
this run (e.g. a transient book-fetch error), it never reaches `candidates`,
and `_portfolio_composition_diff` falls through to `disposition_unknown`
rather than a specific predicate disposition — because it is in
`measured_ids` (blocking `not_in_candidate_scan`) but absent from
`candidate_by_id`. This exact gap already exists today for any member
reached via the current top-N/backfill scan; this WO does not introduce it
and does not close it. Confirmed reachable by execution against
`_portfolio_composition_diff` directly.

**Fail-safe sentence (S5 form, module docstring — corrected per S8 B1).**
"A missing or zero-byte `maker_carry_portfolio_members.csv` yields
`_incumbent_hold(out_root) == (set(), {})` and `_latest_portfolio_members
(out_root) is None` (via `utils.read_csv_rows`, which guarantees `[]` ONLY
for a missing/zero-byte file — an existing-but-unreadable file propagates,
unchanged from before this WO, and is out of scope here). A latest row
whose `portfolio_members` cell fails `json.loads`, OR parses to valid JSON
that is not a list (`null`, a number, a boolean, a bare string), now yields
an empty member set for that row on that reader (this WO's added
`isinstance(members, list)` guard in `_incumbent_hold` — without it, the
scalar-JSON case raised an unhandled `TypeError` that aborted the entire
study run, confirmed by execution; `_latest_portfolio_members` already
guarded this and simply skips such a row). When both readers' contributions
are empty, `protected_condition_ids` is the empty set, the union step in
`_yield_first_shortlist` adds nothing on any of its four return points, and
its output is byte-identical to today's pure top-N/pot-rank behavior. A
protected condition_id absent from the current `universe` is never
fabricated into the shortlist. No gate, sizing predicate, or order surface
reads this artifact differently than before; `_size_portfolio`'s five
predicates are unchanged, and a measured member that fails them is
excluded exactly as today."

### CLI/scheduler/ledger wiring

None required. This WO adds no new CLI command, no new collector, and no
new cadence — `run_maker_carry_study` already runs on the cadence WO-151
registers (dedicated job / harvest step / CLI). `ledger_anchor.py` is not
touched: no new file, no new column on an anchor-enrolled file. If this
section were skipped entirely the change would still take effect on every
existing study run, because it modifies a function already on the hot path
— there is no "collector nobody runs" risk here.

### Tests (offline, fixture-based, exact hand-computed expectations; extend
`tests/polymarket_predictive_engine/test_maker_carry_study.py`, reusing the
existing `_scan_market`, `_deep_book`, `_config`, `_wo113_settings`,
`_wo113_candidate` helpers; add `_latest_portfolio_members` to the existing
`maker_carry_study` import block at the top of the file — it is not
currently imported there)

0. **Regression.** `test_yield_first_scan_selects_smaller_under_competed_pot`
   and `test_yield_first_scan_fails_soft_to_pot_rank`
   (`tests/polymarket_predictive_engine/test_maker_carry_study.py:259-309`)
   pass UNMODIFIED — proof the default (`protected_condition_ids=None`) path
   is byte-identical to pre-WO-154 behavior.
1. **A2, missing sidecar.** `_incumbent_hold(out_root)` on a `tmp_path` with
   no `maker_carry_portfolio_members.csv` returns `(set(), {})`.
2. **A2, malformed-JSON latest row.** A sidecar whose only row has
   `portfolio_members="{not json"` → `_incumbent_hold` returns
   `(set(), {})` (per the existing `json.loads` try/except).
3. **B1 NEW — A2, valid-but-non-list JSON latest row.** Parametrize over
   `portfolio_members` in `("null", "123", "true", "1.5")`: for each,
   `_incumbent_hold` does NOT raise, and returns `(set(), {})`. Against
   unamended code this raises `TypeError` for all four values (confirmed by
   execution) — this test fails unamended by construction.
4. **Measured anyway, outside top-N.** `max_book_candidates=1`,
   `yield_scan_max_markets=3`; universe = `_scan_market("big",1000.0,1)`,
   `_scan_market("mid",500.0,2)`, `_scan_market("member",10.0,3)`, all books
   `_deep_book()` (identical competition so ranking is pure pot order).
   `protected_condition_ids={"0xmember"}` → `selected` contains exactly
   `{"0xbig","0xmember"}` (NOT `"0xmid"`); `"0xmember"`'s row has
   `yield_rank is None` and `expected_gross_at_min_size is None`;
   `scan["yield_scan_protected_members_added"] == 1`;
   `scan["yield_scan_protected_members_considered"] == 1`.
5. **Differential byte-identity, union adds nothing.** Same universe as #4
   but `protected_condition_ids={"0xbig"}` (already top-ranked) → `selected`
   is IDENTICAL (equal list) to calling with `protected_condition_ids=None`;
   `yield_scan_protected_members_added == 0`.
6. **Union bound / worst case.** `max_book_candidates=2`, 5 markets
   (`"big"`=1000, `"mid"`=500 rank top-2; `"m1"`=30, `"m2"`=20, `"m3"`=10
   rank outside), `protected_condition_ids={"0xm1","0xm2","0xm3"}` →
   `len(selected) == 5`; `yield_scan_protected_members_added == 3` — hand
   proof that growth over 40 equals exactly `len(protected_condition_ids)`,
   not a fixed ceiling.
7. **Never fabricated.** Same universe as #4,
   `protected_condition_ids={"0xghost"}` (absent from `universe`) →
   `selected` identical to the unprotected run;
   `yield_scan_protected_members_added == 0`;
   `yield_scan_protected_members_considered == 1`.
8. **Fallback path (book-fetch exception) also protects (N1 reworded).**
   Reuse `test_yield_first_scan_fails_soft_to_pot_rank`'s exact fixture
   (`universe=[_scan_market("large",1000.0,1), _scan_market("small",100.0,2)]`,
   `max_book_candidates=1`, `_fetch_books` raises), with
   `protected_condition_ids={"0xsmall"}` — `"0xsmall"` is already that
   fixture's second market and is already outside the
   `universe[:max_book_candidates]` = `universe[:1]` fallback slice, no new
   market needed → `selected` contains `"0xlarge"` (fallback top pick) AND
   `"0xsmall"` (protected); `scan["yield_scan_selected_markets"] == 1`
   (unchanged meaning — computed before protection); `len(selected) == 2`;
   pre-fix code would have produced only `["0xlarge"]`.
9. **B5 NEW — no-scored fallback also protects.** `max_book_candidates=1`,
   `yield_scan_max_markets=2`; universe = `_scan_market("large",1000.0,1)`,
   `_scan_market("nsprotect",5.0,2)`; monkeypatch `_fetch_books` to return
   `{}` (no exception, but every `_book_competition_from_books` call then
   returns `None` since `books.get(token_id)` is `None` — this is the
   OTHER fallback, `scan["universe_scan_mode"]=="pot_rank_fallback"`,
   `scan["yield_scan_error"]=="no prescreen books scored"`, distinct from
   test 8's book-fetch-exception path). `protected_condition_ids=
   {"0xnsprotect"}` → `selected` contains `"0xlarge"` AND `"0xnsprotect"`;
   `scan["yield_scan_selected_markets"] == 1` (unchanged); `len(selected)
   == 2`; `yield_scan_protected_members_added == 1`. This is the exact
   return point B5 found unenforced by the v1 test set — a build that
   restructures items 1/2/4 above but omits this one passes tests 0-8 and
   fails only here.
10. **B4 REDESIGNED — Hormuz shape, true departure, derived through the
    real union (must fail unamended).** `settings = _wo113_settings(
    max_book_candidates=1, yield_scan_max_markets=2, share_model_c=3.0,
    share_model_mid_band_min=0.10, share_model_mid_band_max=0.90)`;
    `universe = [_scan_market("other",1000.0,1), _scan_market("member",
    10.0,2)]`; monkeypatch `_fetch_books` to return `_deep_book()` for both
    tokens and both complements (identical competition, pure pot order).
    Call `measurement_universe, _scan =
    maker_carry_study._yield_first_shortlist(settings, universe, [0.5],
    protected_condition_ids={"0xmember"})` — AGAINST UNAMENDED CODE this
    raises `TypeError` immediately (no such kwarg), so the test fails
    unamended by construction. Then build `measured_ids =
    {row["condition_id"] for row in measurement_universe}` and gate
    candidate construction ON IT (this is the substantive fix — do NOT
    hand-build the candidate list independent of `measurement_universe`,
    which is exactly what made the v1 test 8 pass on unamended code):
    `candidates = [c for cid, c in {"0xmember": _wo113_candidate("0xmember",
    carry=-1.0, hours=100, snaps=200), "0xother": _wo113_candidate(
    "0xother", carry=2.0, hours=100, snaps=200)}.items() if cid in
    measured_ids]`. `portfolio, _, _ = _size_portfolio(settings, candidates,
    500)` → `"0xmember"` absent (fails predicate 1, `carry>0`; `"0xother"`
    passes). `_portfolio_composition_diff` returns a 2-tuple
    (`maker_carry_study.py:1620`, return at `:1678-1684`) — unpack it, do NOT
    subscript the call directly: `diff, _status =
    _portfolio_composition_diff(previous=("2026-08-04T12:00:00Z",
    {"0xmember": True}), current_run_at="2026-08-05T12:00:00Z",
    portfolio=portfolio, candidates=candidates, rewarded_universe=universe,
    measurement_universe=measurement_universe, stale_reasons={})`; then
    `diff["departed"]` equals exactly `[{"condition_id":"0xmember",
    "markout_measured":True,"disposition":"measured_not_sized",
    "net_carry_usd_per_day":-1.0}]` — NEVER `not_in_candidate_scan`.
    **Second, substantive discrimination (beyond the TypeError):** a
    hypothetical build that accepts the kwarg
    but does not actually apply the union would leave `measured_ids ==
    {"0xother"}` only, so `"0xmember"` is excluded from `candidates`
    entirely, `candidate_by_id.get("0xmember")` is `None` inside the diff,
    and the branch falls through to `disposition="not_in_candidate_scan"`
    (still in `rewarded_ids` via `universe`, not in `measured_ids`) — the
    assertion above then fails on the `disposition` VALUE, not merely on a
    raised exception.
11. **Hormuz shape — true retention (N2 reworded).** Same 3-market universe
    as test 4 (`big`/`mid`/`member`, `max_book_candidates=1`, `"0xmember"`
    protected and outside top-1, `"0xmid"` excluded by the cap) →
    `_yield_first_shortlist` measures `"0xbig"` and `"0xmember"` (proven by
    calling it directly with the kwarg — fails `TypeError` unamended, as in
    test 10); build two passing `_wo113_candidate` rows for
    `"0xbig"`/`"0xmember"` and call `_size_portfolio(settings, [row_big,
    row_member], 500, incumbents={"0xmember"}, incumbent_hold_days=
    {"0xmember": 5})` → BOTH appear in `portfolio` — proof `"0xmember"` is
    retained via measurement where the pre-fix code could never have
    reached it at all.
12. **B3 NEW — reader divergence fixture 1 (cross-day malformed latest
    row).** Sidecar rows: `("2026-08-03T12:00:00Z",
    '[{"condition_id":"0xhormuz","markout_measured":true}]')`,
    `("2026-08-04T12:00:00Z", "{not json")`. `_incumbent_hold` → `incumbents
    == set()` (the malformed latest day's empty cids win the day). `
    _latest_portfolio_members` → `("2026-08-03T12:00:00Z",
    {"0xhormuz": True})` (skips the malformed row). Assert the UNION —
    `incumbents | set(prior[1])` — equals `{"0xhormuz"}`: the fail-closed
    superset recovers what `_incumbent_hold` alone would have lost.
13. **B3 NEW — reader divergence fixture 2 (same-day malformed latest
    row).** Sidecar rows: `("2026-08-04T10:00:00Z",
    '[{"condition_id":"0xhormuz","markout_measured":true}]')`,
    `("2026-08-04T12:00:00Z", "{not json")` (same day). `_incumbent_hold` →
    `incumbents == set()` (the `>=` same-day comparison lets the later,
    malformed row's empty cids overwrite the valid ones).
    `_latest_portfolio_members` → `("2026-08-04T10:00:00Z",
    {"0xhormuz": True})`. Union == `{"0xhormuz"}`.
14. **B3 NEW — reader divergence fixture 3 (out-of-order / clock skew).**
    Sidecar rows in FILE order: `("2026-08-05T01:00:00Z",
    '[{"condition_id":"0xnew","markout_measured":true}]')`,
    `("2026-08-04T23:59:00Z", '[{"condition_id":"0xold",
    "markout_measured":true}]')` (a later-appended row carrying an EARLIER
    embedded timestamp). `_incumbent_hold` → `incumbents == {"0xnew"}` (max
    by embedded day-string). `_latest_portfolio_members` → `
    ("2026-08-04T23:59:00Z", {"0xold": True})` (last-valid-in-file-order).
    Union == `{"0xnew", "0xold"}` — both are protected even though the two
    readers disagree on which one is "the" latest member.

### Scope classification

**FROZEN → OWNER MERGE after line-audit.** This touches
`maker_carry_study.py`'s selection surface, which is registered/control (it
feeds the M-A/M-B maker gates' `candidates`/`portfolio` inputs). Per
AGENTS.md's owner-authorization rule ("A change to any frozen or registered
surface... is authorized ONLY by an owner-authored commit or an
owner-approved pull request") and `docs/ENGINEERING_STANDARDS.md` S7 item 7,
this is not the orchestrator's to self-merge. **This is NOT a tighten-only
amendment** — say so explicitly at registration: it can cause a market to
hold a portfolio slot that it could not have held before (a previously
scan-evicted, still-qualifying incumbent), even though it never loosens any
of the five `_size_portfolio` predicates or any other threshold. The owner
must ratify at registration whether "measurement coverage" is the correct
frame for a change with that outcome, per the standing rule that any
loosening-shaped effect on a frozen surface is disclosed, not built quietly.

### Day-after check

On `outputs/maker_carry/maker_carry_study.json`, after the next deployed
study run: (1) `yield_scan_protected_members_considered` and
`yield_scan_protected_members_added` are both present and numeric on every
ENABLED run (the disabled early return at `:2091-2093` writes a summary
without either key — expected, per N4). (2) Cross-reference
`outputs/maker_carry/portfolio_composition_diff.json`'s `departed` rows
against the immediately preceding run's `maker_carry_portfolio_members.csv`
row — but apply this signature ONLY when the same run's
`composition_diff_status == "ok"` and the diff's `current_run_at` matches
that run's `generated_at_utc` (N-v2-1: on the diff-write-failure path,
`composition_diff_status` is set to `"write_failed"`, `maker_carry_study.py:2504`,
and `write_json` for `portfolio_composition_diff.json` is skipped, so the
file on disk can be a stale pre-deploy artifact whose `departed` rows
legitimately still show `not_in_candidate_scan` from before this WO
shipped — checking that stale file would misfire a revert on correct code):
for any condition_id that appears in BOTH (a member last run and
departed this run), its `disposition` may legitimately read ANY of
`measured_not_sized`, `excluded_stale:*`, `excluded_resolution_risk`,
`excluded_thin_book`, `not_in_rewarded_universe` (left the rewarded pull
upstream of every predicate — WO-151's own NVIDIA-shape mechanism, untouched
by this WO), or `disposition_unknown` (measured this run but its own
`_book_competition` call failed — the pre-existing, explicitly out-of-scope
residual noted above). **`not_in_candidate_scan` is the ONLY disposition
that must NEVER appear** for such a condition_id post-deploy — S8 B2/B3
make it unconditionally unreachable for a genuine prior member, because
`protected_condition_ids` is a superset of the diff's own `prior` set by
construction, not merely of `_incumbent_hold`'s output. **Failure
signature:** `not_in_candidate_scan` appearing for a condition_id that was
a prior member means the union step did not wire through correctly, and the
WO is REVERTED, not tuned.
