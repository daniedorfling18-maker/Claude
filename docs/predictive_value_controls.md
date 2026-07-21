# Predictive Value Controls

> Historical SuperBru command examples below may run only in an isolated VPS
> environment. They are not the current scheduler. Selection-conditioned or
> post-kickoff odds comparisons are exploratory, not confirmatory evidence.

This repo is a Superbru decision engine. It should not claim live predictive edge unless the evidence is joined to trusted completed results and evaluated by signal source.

## WC 2026 validation status

`outputs/calibration/wc2026-domain-validation/wc2026_domain_validation_summary.json` has been invalidated. The previous artifact used `outputs/market_odds_history/market_odds_history.csv` `home_goals` and `away_goals` fields as actual results. Those fields can represent prediction-card scorelines, so using them as actuals leaks the pick into the validation target.

Use `scripts/wc2026_domain_validation.py` only in its leakage-safe form. It must join pre-match market snapshots to a trusted completed-results CSV, normally:

```powershell
python scripts\wc2026_domain_validation.py `
  --history-csv outputs\market_odds_history\market_odds_history.csv `
  --results-csv outputs\superbru_pool\superbru_match_results_auto.csv
```

## Live rolling backtest ledger

Use the rolling signal backtest to evaluate completed matches against each policy separately:

```powershell
python scripts\build_live_signal_backtest.py
```

The ledger writes:

- `outputs/backtesting/live_signal_backtest_rolling.csv`
- `outputs/backtesting/live_signal_backtest_summary.json`

Each row is policy-specific and includes the trusted actual score, pre-match market snapshot id, raw EV pick, naive favourite-low-score pick, Oddspedia modal pick, Oddspedia EV pick, daily robust card pick, final leader-defence pick, points earned, and whether an Oddspedia review was followed.

## Policy separation

Do not blend policy performance when assessing predictive value. Compare at least these policies independently:

- `raw_ev_model`
- `naive_favourite_low_score`
- `oddspedia_modal`
- `oddspedia_ev`
- `daily_robust_card`
- `final_leader_defence`

## Oddspedia promotion gate

Oddspedia remains advisory unless both conditions pass:

1. Grid quality coverage is high enough, default `ok_match_count / match_count >= 0.85`.
2. Trusted completed-match backtest volume is large enough, default at least 30 Oddspedia-evaluable completed matches.

The gate is reported in `live_signal_backtest_summary.json` under `oddspedia_promotion_gate`.

## Chaser profiles

Chaser profiles are unvalidated priors unless they are calibrated from observed Superbru pool picks. Monte Carlo output that depends on synthetic chaser profiles should be read as scenario analysis, not measured predictive evidence. When visible pool picks are captured, update `inputs/chaser_profiles.csv` from those observations before relying on leader-defence probabilities.
