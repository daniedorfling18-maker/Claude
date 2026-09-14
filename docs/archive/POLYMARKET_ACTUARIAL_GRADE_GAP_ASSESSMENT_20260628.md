# Polymarket actuarial-grade gap assessment — 2026-06-28

This note answers whether the current Polymarket paper-trading/research system is as strong as it can reasonably be for the current waiting window, and whether it meets an actuarial-grade statistics/modelling bar.

## Short answer

The system is now **well-governed for paper research** and materially safer than it was earlier in the session. It is **not yet fully actuarial-grade** in the professional model-risk sense.

The current best choice is to keep the trading side unchanged and wait for active-window evidence. Further meaningful improvement before the next BTC window is mainly documentation, monitoring, and validation work, not looser trading logic.

## What is strong now

The current system has several actuarial-style controls already in place:

- Paper/research-only operating stance is explicit.
- Live trading remains disabled and guarded.
- No new paper entries are opened while approved signals remain zero.
- `profit-sprint` separates queued targets from actual paper-trade readiness.
- `WAIT_ACTIVE_WINDOW` prevents pre-window BTC 15m signals from being treated as trades.
- Cohort promotion requires evidence gates rather than one-off model confidence.
- Shadow evidence is tracked separately from paper-broker fills.
- Negative cohorts are quarantined or kept evidence-only.
- Liquidity/spread filters are explicit.
- Active-window planner gives deterministic SAST rescore times.
- Config normaliser removes duplicate YAML blocks created during emergency overrides.
- Dashboard/local live loop shows resource guard, exposure, approved signals, blocker, and evidence state.
- Runtime context and runbooks now exist in-repo.

These are good risk controls and are directionally aligned with actuarial model governance: assumptions are explicit, decisions are gated, and unsafe overrides are discouraged.

## Why it is not yet fully actuarial-grade

A professional actuarial-grade modelling process would normally require more than safe runtime controls. The biggest remaining gaps are below.

### 1. Independent validation is incomplete

Current validation is mostly internal. The system needs a reproducible independent validation pack that can be run without relying on chat context or ad hoc local state.

Needed:

- A single validation command that reproduces the latest model/data state from raw inputs.
- Independent train/validation/test/forward splits with documented cutoffs.
- Validation output that explicitly says which model version is admissible for paper trading and why.
- A reviewer checklist for model assumptions, data quality, leakage, calibration, and limitations.

### 2. Calibration evidence is not yet enough for live confidence

The system has calibration and edge-scoring concepts, but the current observed dashboard state still shows no approved signals and a blocker that lower-bound edge remains below threshold.

Needed:

- Reliability/calibration tables by market family and probability bucket.
- Brier/log-loss/expected calibration error by family and time-to-close bucket.
- Confidence intervals around calibration error and ROI.
- Explicit proof that the model beats baseline probabilities after costs.

### 3. Sample sizes are still thin for active cohorts

Several promising cohorts have too few fills or settled observations, even if their early ROI is positive.

Examples from current context:

- `near_miss_learning|unknown` is probationary but still short of full fill requirements.
- `exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down` has high ROI but too few fills/settled observations.
- `exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up` is near promotion but has not cleared ROI.

Needed:

- Minimum sample sizes by cohort and market family.
- Wider forward-testing windows.
- Explicit handling for multiple testing / selection bias across many candidate cohorts.

### 4. Multiple-testing and data-snooping risk remains

The system searches many cohorts, families, and rules. This creates a real risk of false positives.

Needed:

- Holdout discipline that is never reused for model selection.
- Multiple-comparison adjustments or conservative false-discovery controls for strategy search.
- Walk-forward validation over multiple calendar periods.
- Market-clustered resampling for ROI confidence intervals wherever signals share the same market or event.

### 5. Economic value after execution costs is not yet proven

The dashboard currently shows no paper entries being opened and actual P&L since clean baseline remains zero. Shadow evidence is useful, but it is not equivalent to live executable paper-broker evidence.

Needed:

- Real paper-broker fill evidence under current gates.
- Slippage, spread, and liquidity impact monitoring by family.
- Post-trade attribution: model edge, market movement, exit logic, settlement result.
- Run-rate calculations based on actual paper P&L, not only shadow or model EV.

### 6. Model documentation needs formalisation

The repo now has a runtime context document, but an actuarial-grade system should have model cards / assumption documents per model family.

Needed for each model family:

- Purpose and use limitations.
- Data sources and exclusions.
- Features and transformations.
- Assumptions and expert judgement.
- Validation metrics and thresholds.
- Known weaknesses.
- Change log and approval status.

### 7. Monitoring thresholds should be formalised

The dashboard is good operationally, but actuarial-grade monitoring should define what action each threshold triggers.

Needed:

- Alert levels for drawdown, calibration drift, data freshness, missing feeds, API errors, and model degradation.
- A model disablement rule for adverse experience.
- A documented process for re-enabling a cohort after quarantine.
- Daily/weekly governance reports.

## What is reasonable to improve right now

Before the next BTC active window, the valuable improvements are limited. The main technical/risk improvements already made were:

- active-window planner;
- runtime context/runbook;
- config normaliser;
- liquidity discovery diagnosis;
- market-family classification improvements;
- passing event context into liquidity classification.

The next high-value improvements should be scheduled, not rushed into the waiting period:

1. Add dashboard display for active-window plan status (`WAIT`, `RUN_NOW`, `EXPIRED`).
2. Add tests for market-family classification.
3. Add tests for config normalisation.
4. Add per-query handling for Gamma `public-search` 422s instead of disabling public search globally.
5. Add a validation report command that outputs calibration, Brier/log-loss, ROI confidence intervals, sample-size sufficiency, and leakage checks.
6. Add model cards for crypto up/down, mispricing alpha, near-miss learning, and sports/world-cup families.

## Practical conclusion

For the current time window, the system is as good as it needs to be operationally: safe, waiting correctly, and not forcing trades.

For an actuarial-grade bar, the honest answer is:

- **Operational readiness for paper research:** good.
- **Governance and safety controls:** strong and improving.
- **Actuarial-grade statistical proof of edge:** not yet.
- **Production/live trading readiness:** no.

The correct next action remains to collect forward evidence at the active BTC windows and only allow paper entries when existing gates explicitly move from `WAIT_ACTIVE_WINDOW` to `PROBATIONARY_PROBE_READY` or `PAPER_TRADE_READY`.

## Non-negotiable rule

Do not reduce statistical standards to create activity. The system should only become more active after the evidence base improves; otherwise, the safest actuarial judgement is no trade.
