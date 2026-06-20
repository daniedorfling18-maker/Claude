# Superbru Backtesting with Oddspedia Results

This adds a local backtesting layer to the daily Superbru workflow.

## What it does

The daily local automation now does three related jobs:

1. captures the current SmartBet correct-score grid;
2. backfills final scores for completed matches from the same match URL list;
3. joins those results to our locked picks and calculates backtest metrics.

The key output files are:

```text
outputs/backtesting/oddspedia_results_backfill.csv
outputs/backtesting/oddspedia_results_backfill_summary.json
outputs/backtesting/superbru_pick_backtest.csv
outputs/backtesting/backtest_summary.json
```

## Evidence classes

Final scores and historical probabilities should be treated separately:

- **Final scores/results:** suitable for backtesting once a match is complete.
- **Pre-match probability snapshots:** strongest when captured before kickoff by the daily process.
- **Recovered probability grids:** useful for reference, but weaker than a pre-kickoff snapshot.

## Manual commands

Backfill results from the match URL list:

```powershell
python scripts\scrape_oddspedia_results_cdp_session.py `
  --cdp-url "http://127.0.0.1:9222" `
  --urls-csv inputs\oddspedia_match_urls.csv `
  --out-csv outputs\backtesting\oddspedia_results_backfill.csv `
  --out-json outputs\backtesting\oddspedia_results_backfill_summary.json
```

Build the backtest:

```powershell
python scripts\build_superbru_backtest_from_results.py `
  --results-csv outputs\backtesting\oddspedia_results_backfill.csv `
  --picks-csv outputs\final_locked_picks\superbru_final_card.csv `
  --oddspedia-comparison-csv outputs\oddspedia_pick_validation\oddspedia_pick_comparison.csv `
  --out-csv outputs\backtesting\superbru_pick_backtest.csv `
  --out-summary-json outputs\backtesting\backtest_summary.json
```

## Scoring defaults

The backtest uses a configurable Superbru-style estimate:

```text
Exact score: 3.0
Correct result + correct goal difference: 1.5
Correct result only: 1.0
Wrong result: 0.0
```

Change those with:

```powershell
python scripts\build_superbru_backtest_from_results.py --exact-points 3 --margin-points 1.5 --result-points 1
```

## Daily automation

The local scheduled task runs this automatically through:

```powershell
scripts\run_daily_superbru_local.ps1
```

It commits/pushes `outputs/backtesting` when `-CommitAndPushOutputs` is used.

The hosted workflow does not fetch live match pages. It only rebuilds the backtest from committed result files.
