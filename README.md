# World Cup 2026 Superbru Score-Prediction Engine

Python engine that converts bookmaker odds into a calibrated scoreline distribution and selects the score prediction with the highest expected Superbru points.

The key insight: this is not a "most likely exact score" picker. The decision layer evaluates every candidate scoreline against the Superbru payoff function (3 pts exact, 1.5 pts close, 1 pt right result) and picks the scoreline that maximises expected points — not the single most probable score.

## Quick Start

Linux / macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
python -m superbru_score_engine config-check --config config.yaml --profiles calibration_profiles.yaml
python -m superbru_score_engine predict --config config.yaml --fixtures examples/fixtures.csv --odds-json examples/odds_snapshot.json --out-dir outputs
```

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
copy config.example.yaml config.yaml
python -m superbru_score_engine config-check --config config.yaml --profiles calibration_profiles.yaml
python -m superbru_score_engine predict --config config.yaml --fixtures examples/fixtures.csv --odds-json examples/odds_snapshot.json --out-dir outputs
```

`pip install -e .` also installs a `superbru-score` console script, so `superbru-score predict ...` is equivalent to `python -m superbru_score_engine predict ...`.

The sample command uses the included offline odds snapshot so the app can be smoke-tested without API credentials.

Always run `config-check` before production predictions — it validates that the active config matches the named calibration profile and exits non-zero when model settings have drifted.

## Architecture

```
ingest/          → Odds providers (The Odds API, Oddspedia, offline JSON)
model/           → Poisson + Dixon-Coles scoreline distribution
decision/        → EV maximisation, strategy modes, sensitivity analysis
backtest/        → Calibration sweeps and historical evaluation harness
game_theory/     → Leader/chaser defensive strategy overlay
betting/         → Optional conservative match-winner screen
report/          → CSV/JSON output formatting
```

**Active calibration profile:** `big5_h2h_totals` — Football-Data Big Five league proxy calibration using `h2h + totals` market structure, multiplicative devig, Dixon-Coles ρ = −0.08, odds weight = 1.0.

**Alternative profile:** `worldcup_1x2` — Football-Data 2014/2018/2022 World Cup 1X2 odds, power devig, ρ = −0.04.

## CLI Commands

| Command | Purpose |
|---------|---------|
| `config-check` | Validate config against calibration profile |
| `fetch-odds` | Fetch live odds, write fixtures/odds JSON |
| `predict` | Run decision engine, output predictions |
| `results` | Fetch completed scores, update ratings store |
| `backtest` | Score predictions against historical results |
| `tune` | Hyperparameter calibration sweep |
| `football-data-backtest` | World Cup proxy calibration via Football-Data |
| `football-data-league-backtest` | Big Five league calibration sweep |
| `the-odds-api-historical-backtest` | Historical odds snapshot comparison |
| `public-results-backtest` | Ratings-only backtest from public results CSV |

## Live Odds

Set API keys in `config.yaml` or as environment variables:

```
THE_ODDS_API_KEY          – The Odds API v4
ODDSPEDIA_API_TOKEN       – Oddspedia (optional)
```

The HTTP adapters use Python's standard-library `urllib` — no `requests` or `httpx` needed.

The Odds API v4 returns `h2h` and `totals` markets (correct-score availability depends on plan). The default config requests `h2h,totals`. When a match quotes multiple over/under lines (e.g. 1.5, 2.5, 3.5), the lambda solver fits all simultaneously, weighted by book count, constraining the goal distribution more tightly.

When a `correct_score` market is present (e.g. from an Oddspedia grid), set `model.correct_score_blend_weight` above 0 to blend it into the scoreline matrix (default 0 = off). The grid is de-vigged together with any "Any Other Score" bucket; unquoted scores are filled from the model prior.

The Odds API provider and the Oddspedia results provider are configured separately (`providers.the_odds_api` vs `providers.results.oddspedia`). Do not copy the Oddspedia fixture-widget URL into the odds block — the adapter will reject it because it carries no bookmaker markets.

## Results Without Odds Quota

Completed scores are fetched separately from odds. The default config uses Oddspedia fixture JSON (filtered from `2026-06-11T00:00:00Z`), so refreshing results does not spend Odds API quota:

```bash
python -m superbru_score_engine results --config config.yaml --out-dir outputs
```

Refresh results immediately before predicting while using a cached odds snapshot:

```bash
python -m superbru_score_engine predict --config config.yaml --live-results \
  --odds-json work/cache/the_odds_api_*.json --out-dir outputs
```

Repeated result refreshes are safe — the ratings store tracks applied match IDs and skips duplicates.

## Daily Production Workflow

Two workflows keep the committed card fresh:

- **`.github/workflows/refresh-locked-superbru-card.yml`** runs **twice daily** (06:05 and 14:05 UTC = 08:05 / 16:05 SAST). It rebuilds the committed locked-card CSV only, skipping the expensive final-leader simulation by default. This is the routine card refresh.
- **`.github/workflows/daily-superbru-robust.yml`** is the full robust pipeline (market-odds validation, Oddspedia overlay, score-change notification, final-leader simulation). Its schedule is disabled — run it on demand via `workflow_dispatch` when you need the complete card rebuild. Scheduled refreshes go through the lighter workflow above.

The full robust pipeline:

1. Fetches market odds from The Odds API (uses cached odds unless `refresh_market_odds=true`)
2. Builds the daily Superbru card
3. Runs the Oddspedia SmartBet overlay if a captured grid exists
4. Writes the score-change notification report
5. Creates a GitHub issue only when the recommended scoreline has changed
6. Uploads and commits daily outputs

Required secret: `THE_ODDS_API_KEY`

The full local Oddspedia pipeline must be run locally to refresh the grid. The GitHub Action consumes the latest committed grid files rather than scraping Oddspedia itself (Cloudflare protection prevents remote scraping).

## Match-Scoped Auto-Pick

`.github/workflows/auto_pick.yml` fires on a per-fixture cron schedule (~25 min before each kickoff, with a 40-minute window that absorbs GitHub's best-effort cron delays). For each match inside the window it:

1. Logs into Superbru and locates the unlocked match tab
2. Pulls **that match's** odds from The Odds API and **recomputes the recommended scoreline live** via the engine — so a stale committed card never drives the submission. The fresh pick is oriented to the Superbru home/away order before submitting.
3. Falls back to the committed locked-card pick only if the live recompute is unavailable (no odds, API error, unconfirmable team orientation)
4. Submits the pick via headless browser automation

The run summary records `pick_source` (`live_odds_recompute` vs `committed_card_fallback`) and `pick_changed_vs_card` per match.

Required secrets: `THE_ODDS_API_KEY`, `SUPERBRU_USERNAME`, `SUPERBRU_PASSWORD`.

## Superbru Fixture Checker

`.github/workflows/check_superbru_fixtures.yml` runs daily (06:00 UTC). It logs into Superbru, lists upcoming fixtures, and verifies each one inside the next 48 h is covered by an `auto_pick.yml` cron entry (a cron firing 1–45 min before kickoff). For any uncovered fixture it proposes a cron firing 25 min before kickoff and **opens a pull request** adding the entry to `auto_pick.yml` (de-duplicated). A full fixture/coverage report is always uploaded as an artifact.

Because the PR edits a workflow file, it needs a personal access token with `repo` + `workflow` scope, exposed as the `WORKFLOW_PAT` secret. Without it the job still produces the report and suggested cron lines but cannot open the PR.

Required secrets: `SUPERBRU_USERNAME`, `SUPERBRU_PASSWORD`, and `WORKFLOW_PAT` (for the auto-PR).

## Oddspedia SmartBet Pipeline

Run each day after confirming match URLs are up to date:

```bash
python scripts/run_oddspedia_pipeline.py
```

Skip the scrape step if the grid was already captured today:

```bash
python scripts/run_oddspedia_pipeline.py --skip-scrape
```

### Pipeline Steps

| Step | Script | Output |
|------|--------|--------|
| 1 | `scrape_oddspedia_curl.py` | `inputs/smartbet_grids/oddspedia_probability_grids_auto.csv` |
| 2 | `build_oddspedia_score_shape_features.py` | `inputs/smartbet_grids/oddspedia_score_shape_features.csv` |
| 3 | `check_oddspedia_grid_quality.py` | `outputs/oddspedia_probability_extract/oddspedia_grid_quality.csv` |
| 4 | `compare_locked_picks_to_oddspedia.py` | `outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv` |
| 5 | `build_oddspedia_superbru_ev.py` | `outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv` |
| 6 | `build_superbru_backtest_from_results.py` | `outputs/backtesting/superbru_pick_backtest.csv` |
| 7 | `build_oddspedia_model_independence.py` | `outputs/oddspedia_pick_validation/oddspedia_model_independence.csv` |
| 8 | `build_oddspedia_synthetic_pool_crowding.py` | `outputs/superbru_pool/superbru_synthetic_crowding.csv` |
| 9 | `build_superbru_pool_intelligence.py` | `outputs/superbru_pool/superbru_remaining_fixture_leverage.csv` |
| 10 | `build_oddspedia_signal_archive.py` | `outputs/backtesting/signal_archive_rolling.csv` |

### Scraper Dependencies

`scrape_oddspedia_curl.py` uses `curl_cffi` with `impersonate='chrome124'` to replicate Chrome's TLS fingerprint, bypassing Cloudflare without a real browser. Oddspedia's server-side rendered Nuxt state (`window.__NUXT__`) is embedded in the HTML and contains the full correct-score probability grid (market 800) and 1X2 probabilities (market 100). A Node.js subprocess evaluates the obfuscated JS to extract structured data.

Install dependencies:

```bash
pip install curl_cffi
node --version   # Node.js must be available
```

### Key Output Files

```
inputs/smartbet_grids/oddspedia_probability_grids_auto.csv   – raw CS probability grid (19 rows per match)
inputs/smartbet_grids/oddspedia_markets_summary_auto.csv     – de-vigged 1X2, BTTS, OU2.5 per match
inputs/smartbet_grids/oddspedia_score_shape_features.csv     – derived features + market diffs
outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv    – locked pick vs Oddspedia modal
outputs/oddspedia_pick_validation/oddspedia_ev_recommendations.csv – EV-ranked scorelines per match
outputs/oddspedia_pick_validation/oddspedia_model_independence.csv – grid vs market independence class
outputs/superbru_pool/superbru_synthetic_crowding.csv        – estimated pool pick crowding per match
outputs/superbru_pool/superbru_remaining_fixture_leverage.csv – leaderboard leverage per remaining match
outputs/backtesting/superbru_pick_backtest.csv               – scored picks vs completed results
outputs/backtesting/signal_archive_rolling.csv               – daily signal snapshots
outputs/backtesting/snapshots/signal_archive_YYYY-MM-DD.csv  – point-in-time snapshot per run
```

### Model Independence (Step 7)

Compares the Oddspedia CS grid against bookmaker market signals on three dimensions: 1X2 direction, OU2.5, and BTTS. Each match is classified as `market_aligned` (all diffs < 2pp), `mildly_independent` (any diff 2–5pp), or `strongly_independent` (any diff ≥ 5pp).

### Synthetic Pool Crowding (Step 8)

Estimates what fraction of the pool likely picks each scoreline based on Oddspedia probability rank and casual-player heuristics. Outputs a crowding risk flag and differentiation flag per match. Real pool picks are never visible before lock — this is a synthetic model only.

### Reviewing Step 4 Output

Review material differences between locked picks and Oddspedia recommendations:

```bash
python -c "
import pandas as pd
df = pd.read_csv('outputs/oddspedia_pick_validation/oddspedia_pick_comparison.csv')
review = df[~df['action'].isin(['keep','no_grid'])].sort_values('probability_gap_vs_locked_pct', ascending=False)
print(review[['match_id','locked_pick','locked_pick_probability_pct','oddspedia_best_score','oddspedia_best_probability_pct','probability_gap_vs_locked_pct','action']].to_string(index=False))
"
```

The comparison report is a review layer, not an automatic switch engine. A higher-probability Oddspedia modal does not automatically replace the locked pick — any change must still pass Superbru EV logic, leader/chaser risk, and robust-policy checks.

## Outputs

For each fixture the CLI writes `predictions.csv`, `predictions.json`, and a console table. Each candidate includes:

- `P(exact)`, `P(close)`, `P(outcome)` — raw probabilities
- Expected Superbru points — the optimisation target
- `sensitivity_stability` — fraction of perturbations that kept the same pick (low = fragile)
- `private_chase_scoreline` — best pick within `superbru.private_chase_max_ev_loss` of the raw EV pick that maximises upside and minimises synthetic crowding
- `fair_total_lines` / `solver_total_over_rmse` — multi-line totals fit diagnostics

## Strategy Modes

Set `superbru.strategy_mode` in `config.yaml`:

| Mode | Behaviour |
|------|-----------|
| `raw_ev` | Pure expected points (default) |
| `conservative` | Avoids low-probability exact scores |
| `contrarian` | Prefers under-picked scorelines |
| `exact_chase` | Maximises `P(exact)` |
| `risk_adjusted` | Applies variance penalty |
| `private_chase` | Uses the private chase scoreline as the main pick |

## Ratings

Elo-based team ratings are stored in `work/ratings.json` and used as a fallback when market odds are missing. Ratings metadata (`_metadata` key) records source, update method, k-factor, base rating, and applied-result count.

Ratings are lower-trust than market odds and are kept at `ratings_weight: 0.0` by default. The active profile does not blend them into market-backed fixtures.

Manual home/host advantage is applied only on the ratings-only fallback path. When 1X2 odds are available, the market is assumed to have already priced venue effects. For multi-country host matches (USA / Canada / Mexico), the host bump is venue-conditioned per country.

## Betting Report

Disabled by default because the odds-implied distribution is anchored to the same market prices you would bet into, making any edge screen circular. To emit the conservative match-winner screen anyway:

```bash
python -m superbru_score_engine predict --config config.yaml --out-dir outputs --include-betting
```

Uses `P(win) * decimal_odds - 1`, not Superbru expected points.

## Backtesting

Score the full decision pipeline against Superbru-style rules:

```bash
python -m superbru_score_engine backtest --config config.yaml \
  --fixtures examples/backtest_matches.csv \
  --odds-json examples/odds_snapshot.json
```

### World Cup Proxy (Football-Data)

```bash
python -m superbru_score_engine football-data-backtest --config config.yaml \
  --out-dir outputs/football-data-calibration
```

### Big Five League Calibration Sweep

```bash
python -m superbru_score_engine football-data-league-backtest --config config.yaml \
  --seasons 2122,2223,2324,2425 --divisions E0,D1,SP1,I1,F1 \
  --download --out-dir outputs/free-football-data-h2h-totals
```

### Historical Odds Comparison (The Odds API)

```bash
python -m superbru_score_engine the-odds-api-historical-backtest --config config.yaml \
  --fixtures outputs/football-data-calibration/football_data_worldcup_fixtures.csv \
  --snapshots-json work/the_odds_api_historical_snapshots.json \
  --out-dir outputs/the-odds-api-h2h-totals-backtest
```

Historical odds cost `10 × markets × regions` usage credits per snapshot. Use one region and a small date grid while testing.

### Calibration Sweep (Tune)

```bash
python -m superbru_score_engine tune --config config.yaml \
  --fixtures examples/backtest_matches.csv \
  --odds-json examples/odds_snapshot.json \
  --out-dir outputs/tuning
```

Before trusting production picks, verify:
- `config-check` passes against the intended calibration profile
- JSON diagnostics look sensible for lambdas, result probabilities, modal scoreline, EV gaps, sensitivity stability, and candidate-grid probability mass
- Historical odds coverage is large enough to treat calibration results as stable

## Superbru Scoring Rules

| Result | Points |
|--------|--------|
| Exact score | 3.0 |
| Close score (right outcome, off by ≤1 goal or ≤2 goals with correct goal difference) | 1.5 |
| Right outcome only | 1.0 |
| Wrong outcome | 0.0 |

`ci_cutoff: 1.5` in config corresponds to the close-score rule above. Knockout matches are scored on the 90-minute result unless drawn after regular time, in which case extra time is used (penalty shootouts remain draws).

## Team Name Aliases

The engine canonicalises common national-team aliases before joining odds, fixtures, and ratings:

- `USA` / `United States`
- `Korea Republic` / `South Korea`
- `IR Iran` / `Iran`
- `Czechia` / `Czech Republic`

Fixture metadata joins fail loudly when a row cannot be matched — a stale fixture row or missing alias should be fixed rather than silently degraded.

## Known Limitations

- Knockout result basis parsed but not applied — all matches scored on 90-minute result
- `ratings.low_data_prior_sigma` parsed but not yet applied to the model
- Asian handicap weight disabled until validated out-of-sample
- Correct-score grid blending disabled by default (`correct_score_blend_weight: 0`)
- Betting report is circular without an independent probability source
- Oddspedia SmartBet scraper must run locally (Cloudflare); GitHub Actions does not scrape

## Dependencies

| Package | Role |
|---------|------|
| `numpy` | Scoreline matrix operations |
| `pandas` | CSV I/O, backtest aggregation |
| `scipy` | Poisson PDF/CDF, Nelder-Mead optimisation |
| `PyYAML` | Config parsing |
| `openpyxl` | Football-Data Excel workbook |
| `curl_cffi` *(optional)* | Oddspedia scraper TLS fingerprinting |
| `playwright` *(optional)* | Browser automation fallback |
| `pytest` *(dev)* | Test runner |

HTTP requests use Python's standard-library `urllib` — no `requests` or `httpx` needed.
