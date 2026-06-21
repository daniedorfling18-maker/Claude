# Daily Superbru Automation

This is the production operating flow for the World Cup Superbru engine.

The goal each day is:

1. refresh market odds and build the robust Superbru card;
2. run the full Oddspedia pipeline to refresh the correct-score grid, validate picks, and archive signals;
3. review any action items and decide whether to switch picks.

## Why the Oddspedia step is local

Oddspedia is Cloudflare-protected. The scraper (`scrape_oddspedia_curl.py`) bypasses Cloudflare
using `curl_cffi` with TLS fingerprint impersonation — no browser or Chrome CDP session required.
The scraper runs locally and commits the grid files; GitHub Actions consumes the committed files
rather than scraping Oddspedia itself.

## One-time setup

Install Python dependencies:

```bash
pip install -e .
pip install curl_cffi pandas numpy scipy
```

Verify Node.js is available (required for parsing `window.__NUXT__` from Oddspedia HTML):

```bash
node --version
```

Set the odds API key:

```bash
export THE_ODDS_API_KEY="YOUR_REAL_KEY"
```

For GitHub issue notifications, authenticate the GitHub CLI:

```bash
gh auth login
```

## Daily run

### Step 1 — Build the robust Superbru card

```bash
python scripts/run_daily_robust_pipeline.py
```

This produces `outputs/final_locked_picks/superbru_final_card.csv`, which is the input for the
Oddspedia pipeline.

### Step 2 — Run the full Oddspedia pipeline

```bash
python scripts/run_oddspedia_pipeline.py
```

This runs all 10 steps in order (~10–30 seconds depending on network speed):

| Step | What it does |
|------|-------------|
| 1 | Scrapes Oddspedia correct-score grids for all match URLs in `inputs/oddspedia_match_urls.csv` |
| 2 | Builds score-shape features (OU, BTTS, margins, market diffs) |
| 3 | Checks grid quality and coverage |
| 4 | Compares locked picks to the Oddspedia modal score |
| 5 | Calculates EV-ranked scorelines for each match |
| 6 | Scores picks against any completed results (backtest) |
| 7 | Classifies each match: grid independent from market or market-aligned |
| 8 | Estimates synthetic pool crowding (which scores pool players likely cluster on) |
| 9 | Runs pool intelligence: leaderboard leverage, chaser exposure |
| 10 | Archives today's signals to `outputs/backtesting/signal_archive_rolling.csv` |

If the grid was already scraped today, skip step 1:

```bash
python scripts/run_oddspedia_pipeline.py --skip-scrape
```

### Step 3 — Review action items

Check which picks the pipeline flags for review:

```bash
python -c "
import pandas as pd
df = pd.read_csv('outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv')
review = df[~df['action'].isin(['keep','no_grid'])].sort_values('probability_gap_vs_locked_pct', ascending=False)
print(review[['match_id','locked_pick','oddspedia_best_score','probability_gap_vs_locked_pct','action']].to_string(index=False))
"
```

Check EV gaps for the flagged matches:

```bash
python -c "
import pandas as pd
df = pd.read_csv('outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv')
flagged = df[df['review_flag'] == True]
print(flagged[['match_id','locked_pick','current_locked_pick_ev','best_ev_scoreline','best_ev_expected_points','ev_gap_vs_locked','review_level']].to_string(index=False))
"
```

Check model independence:

```bash
python -c "
import pandas as pd
df = pd.read_csv('outputs/oddspedia_pick_validation/oddspedia_model_independence.csv')
print(df[['match_id','independence_class','signal_consistency','locked_pick','pick_follows']].to_string(index=False))
"
```

Check synthetic crowding:

```bash
python -c "
import pandas as pd
df = pd.read_csv('outputs/superbru_pool/superbru_synthetic_crowding.csv')
flagged = df[df['crowding_signal'] != 'contrarian']
print(flagged[['match_id','locked_pick','crowding_signal','est_pool_pct_on_locked_pick']].to_string(index=False))
"
```

### Step 4 — Commit and push

```bash
git add -f outputs/backtesting/ outputs/oddspedia_pick_validation/ outputs/superbru_pool/ inputs/smartbet_grids/
git add outputs/final_locked_picks/ outputs/daily_notifications/
git commit -m "Daily pipeline run $(date -u +%Y-%m-%d)"
git push
```

## Important operating rule

The Oddspedia pipeline is a calibration and review layer. A higher-probability Oddspedia modal
score or higher-EV alternative does **not** automatically replace the locked pick. Any change
must still pass:

- Superbru expected-points logic (EV gap must be material);
- leader/chaser risk logic;
- robust-policy checks;
- manual judgement where market and model signals conflict.

## Key output files

```text
outputs/final_locked_picks/superbru_final_card.csv           — locked picks (input to pipeline)
inputs/smartbet_grids/oddspedia_probability_grids_auto.csv   — scraped CS grid (19 rows per match)
inputs/smartbet_grids/oddspedia_score_shape_features.csv     — OU/BTTS/margin features
outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv    — pick comparison + action flags
outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv — EV per match with review flags
outputs/oddspedia_pick_validation/oddspedia_model_independence.csv — independence class per match
outputs/superbru_pool/superbru_synthetic_crowding.csv        — crowding estimate per match
outputs/superbru_pool/superbru_remaining_fixture_leverage.csv — chaser/leaderboard leverage
outputs/backtesting/superbru_pick_backtest.csv               — scored picks vs completed results
outputs/backtesting/signal_archive_rolling.csv               — daily signal archive (rolling)
outputs/backtesting/snapshots/signal_archive_YYYY-MM-DD.csv  — point-in-time snapshot per run
outputs/daily_notifications/daily_superbru_action_items.md   — action digest
```

## Troubleshooting

### Scraper returns 0 matches

Check that `inputs/oddspedia_match_urls.csv` contains the current round's match URLs. The URLs
must point to the live match pages (e.g. `https://oddspedia.com/football/world/world-cup/...`).

Run a single-match test:

```bash
python scripts/run_oddspedia_pipeline.py --max-matches 1
```

Check the diagnostic files in `outputs/oddspedia_probability_extract/stealth_diagnostics/` if a
match fails — `_body.txt` contains the raw HTML response and `_state.json` the parsed Nuxt state.

### Node.js not found

`scrape_oddspedia_curl.py` writes a temporary JS file and runs `node` to evaluate `window.__NUXT__`.
Install Node.js from https://nodejs.org and ensure `node` is on your PATH.

### Grid count is lower than expected

Some matches may not yet have a SmartBet correct-score market. Check
`outputs/oddspedia_probability_extract/oddspedia_grid_quality.csv` for per-match diagnostics.
Matches without a grid still get processed for picks — they are tagged `no_grid` in the comparison
and EV outputs.
