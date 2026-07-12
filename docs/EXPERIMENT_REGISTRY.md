# Experiment Registry

Created 2026-07-12 in response to the external audit (§4, modelling layer):
"Every additional research lane increases the chance that one looks positive
by chance." This file is the authoritative list of research hypotheses. A
lane that is not registered here with a gate CANNOT be promoted, funded, or
cited as evidence, regardless of how good its numbers look.

Honesty note: this registry consolidates registrations that already exist
in code and registered documents; it does not create new ones. Where a lane
was never formally registered, its status below says so and it is capped at
DIAGNOSTIC. Registration timestamps live in the referenced code constants —
this file points at them and never restates them from memory.

## Research-surface freeze (audit P6)

Two primary hypotheses only. Everything else is diagnostic or parked.
Adding a new primary lane requires: a written entry here BEFORE any of its
results are observed, an economic mechanism, a sample floor, a stopping
rule, and a registered promotion/abandonment gate. Tighten-only applies.

## H1 — PRIMARY: Sharp-anchor maker carry

- Economic mechanism: liquidity provision earns the venue's published
  reward pot; edge = pot share minus adverse selection, not prediction.
- Universe: rewarded markets passing the yield-first scan
  (`maker_carry_study.py`, share model `published_v2`, scan `yield_first_v1`).
- Primary metric: trusted net carry per day at the registered capital.
- Gates: M-A (7 distinct published_v2 UTC days at target incl. latest),
  M-B (measured markout on every portfolio market), M-C (payout floor) —
  constants and registration timestamp in `maker_carry_study.py`
  (`registered_at_utc` 2026-07-09T13:00:00Z).
- Stopping rule / decision date: WO-50 frozen decision policy, action
  taken at the registered policy date (2026-07-19/20); actions limited to
  the pre-registered table a–d in `live_test_decision_policy.py`.
- Abandonment: `maker_lane_not_supported_program_review`.
- Known threat being tracked: carrier-market churn (composition stability
  requires the most recurrent market in ≥4 of the window's runs).
- Status: LIVE STUDY — M-A day 2/7 as of 2026-07-12.

## H2 — PRIMARY: Taker CLV edge ($100/month verdict engine)

- Economic mechanism: model prices sports finals better than the closing
  line, monetised as taker entries before close.
- Gates: A (≥12 independent fixture-clustered units, mean final CLV > 0,
  one-sided sign test p ≤ 0.10), B (net of taker fee + 0.005 exit +
  0.005 adverse), C (turnover feasibility) — definitions and amendments
  1–7 registered in `profit_verdict.py`
  (`VERDICT_GATE_DEFINITIONS`, `REGISTERED_AMENDMENTS`,
  `REGISTERED_EXTENSION_PROTOCOL`).
- Stopping rule: final read 2026-07-19/20; one registered extension to
  2026-08-19/20 (regime `post_wc_2026`), then TERMINAL.
- Status: LIVE STUDY — 14 units, 6 beating close, p = 0.788, trending NO.

## Multiple-testing policy

The two primary lanes each have ONE pre-registered primary metric and
significance rule; nothing else in this repository is a hypothesis test.
Diagnostic lanes below produce descriptive numbers only. If a diagnostic
lane ever looks promising, promotion requires a fresh entry here, a fresh
out-of-sample window that starts AFTER the entry is written, and its own
registered gate — back-applying an observed window is prohibited.

## Diagnostic lanes (no promotion path without re-registration)

- D1 Implication / dutch-book consistency (`implication_consistency.py`,
  event-group sum constraints): shadow-only scanner. Never formally
  registered as a tradeable hypothesis; capped at DIAGNOSTIC.
- D2 Wallet-intelligence / smart-flow cohorts
  (`wallet_intelligence_collector.py`): collection and cohort marking
  only; capped at DIAGNOSTIC.
- D3 Calibration-bias, drift-scan, hourly-adverse, reconstructed-signal
  CLV studies: model-quality instrumentation, not strategies.

## Frozen (negative evidence — do not touch)

- F1 Crypto up/down family: frozen after negative evidence across shadow,
  CLV, replay and out-of-sample reads (see quant charter). Agents are
  prohibited from tuning or re-prioritising it. Unfreezing requires a new
  registry entry plus an owner decision.

## Parked

Any family not named above (politics, awards, long-dated macro, etc.) is
PARKED: collected passively where cheap, never analysed for promotion,
never allocated agent time without a new entry here.
