# World Cup 2026 Superbru Score-Prediction Engine

Python engine for converting bookmaker odds into a calibrated scoreline distribution and choosing the score prediction that maximises expected Superbru points.

The important bit: this is not a "most likely exact score" picker. The decision layer evaluates every candidate scoreline against the Superbru payoff function and chooses the scoreline with the highest expected points.

## Quick Start

Linux / macOS (bash):

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

Run `python -m superbru_score_engine config-check --config config.yaml --profiles calibration_profiles.yaml` before production predictions. It validates that the active flat config matches the named calibration profile and exits non-zero when model settings have drifted.

## Live Odds

Set API keys in `config.yaml` or environment variables:

- `THE_ODDS_API_KEY`
- `ODDSPEDIA_API_TOKEN`

The live HTTP adapters use Python's standard-library `urllib`, so no separate `requests` or `httpx` dependency is required. The Oddspedia adapter verifies that the configured endpoint returns structured odds JSON. If it gets HTML, widget content, or fixture-only data, the CLI falls back to The Odds API when that provider is configured.

Oddspedia's `getIdsFromMatchList` style responses are useful for fixture discovery and historical/completed results. They include fields such as `league_id`, `id`, `md`, `ht`, `at`, `hscore`, and `ascore`. They do not include bookmaker markets, so they are not enough for EV score prediction by themselves. For live predictions, use an Oddspedia endpoint that returns bookmaker odds, or use The Odds API as the odds source.

Because of that split, `providers.oddspedia` (the odds source) and `providers.results.oddspedia` (the fixtures/results source) are configured separately. The shipped `config.yaml` leaves the odds `base_url` empty and uses The Odds API for odds, while pointing the results block at the Oddspedia fixtures widget. Do not copy the widget URL into the odds block: the adapter will reject it because it carries no markets.

The Odds API v4 returns JSON bookmaker/event/market structures for sport odds, including `h2h` and `totals`; correct-score availability depends on plan/sport/market coverage. The default config requests `h2h,totals` only, so correct-score blending is disabled unless you explicitly request and receive a `correct_score`-style market. See:

- https://the-odds-api.com/liveapi/guides/v4/
- https://the-odds-api.com/sports-odds-data/betting-markets.html

## Results Without Odds Quota

Results are handled separately from odds. The default config uses Oddspedia fixture/result JSON for completed scores, filtered from `2026-06-11T00:00:00Z`, so refreshing tournament state does not spend The Odds API quota.

Fetch and save completed results, then update the ratings store:

```powershell
python -m superbru_score_engine results --config config.yaml --out-dir outputs
```

Refresh results immediately before predicting while still using a cached odds snapshot:

```powershell
python -m superbru_score_engine predict --config config.yaml --live-results --odds-json work/cache/the_odds_api_54c50997058cf99e79adc2d73bba184f.json --out-dir outputs
```

Repeated result refreshes are safe: the ratings store tracks applied match IDs/timestamps and skips results it has already applied.

## Outputs

For each fixture the CLI writes:

- `predictions.csv`
- `predictions.json`
- a console table with the recommended pick, expected points, and top candidates

Each candidate includes:

- `P(exact)`
- `P(close)`
- `P(outcome)`
- expected Superbru points

The JSON diagnostics also include the active calibration profile, lambdas, model result probabilities, modal exact scoreline, EV gaps, candidate-grid probability mass, low-score probabilities for common scorelines, synthetic public-pick estimates, sensitivity stability, and ratings provenance.

When a match quotes more than one over/under line (for example 1.5, 2.5 and 3.5), the lambda solver fits all of them simultaneously instead of only the main 2.5 line, which constrains the shape of the goal distribution more tightly. The lines are weighted by book count and share a fixed totals-fit budget, so a match that quotes a single line behaves exactly as before. The relevant diagnostics are `fair_total_lines` / `fair_total_lines_count` (lines used), `solver_total_lines_used`, and the fit-quality fields `solver_total_over_rmse` and `model_over_rmse_across_lines` (root-mean-square gap between model and fair over-probabilities across every quoted line).

The CSV output surfaces the main sensitivity fields: `sensitivity_stability`, `sensitivity_changed_count`, `sensitivity_warning`, and `sensitivity_most_common_alternative`. A low stability value means the recommended scoreline changes under small lambda/rho/total-goals/public-pick perturbations, so treat the pick as fragile.

Ratings metadata is saved in `work/ratings.json` under `_metadata`, including source, source URL, cutoff date, update method, k-factor, base rating, Elo goal scale, confidence threshold, and applied-result count. Ratings are lower-trust than market odds and should remain fallback-only unless a proper backtest validates blending them into market-backed fixtures.

## Betting Report

The optional betting report is disabled by default because this project is a Superbru decision engine, not an independent bookmaker-beating model. The odds-implied distribution is anchored to the same market prices you would bet into, so any betting screen is circular unless you add an independent probability source or compare against a different bookmaker universe.

To emit the conservative match-winner screen anyway:

```powershell
python -m superbru_score_engine predict --config config.yaml --out-dir outputs --include-betting
```

The report uses `P(win) * decimal_odds - 1`, not Superbru expected points.

## Backtesting

The backtest harness expects historical fixtures/results plus either historical odds JSON or a configured The Odds API historical endpoint. It scores the full decision pipeline against Superbru-style rules and compares it with the naive low-score favourite baseline.

```powershell
python -m superbru_score_engine backtest --config config.yaml --fixtures examples/backtest_matches.csv --odds-json examples/odds_snapshot.json
```

Calibration sweeps are supported for Dixon-Coles `rho` and the closeness-index cutoff.

To answer the specific live-model question, use the The Odds API historical harness to compare `h2h` against `h2h,totals` on the same matched fixtures:

```powershell
python -m superbru_score_engine the-odds-api-historical-backtest --config config.yaml --fixtures outputs/football-data-devig-calibration/football_data_worldcup_fixtures.csv --snapshots-json work/the_odds_api_historical_snapshots.json --out-dir outputs/the-odds-api-h2h-totals-backtest
```

The command is cache/offline first. It only spends The Odds API quota when you explicitly pass `--fetch` and `--date-grid`. Historical odds are paid-plan data, and The Odds API prices historical odds at `10 x markets x regions` usage credits per snapshot, so use one region and a small date grid while testing.

If historical World Cup totals are unavailable, Football-Data's free league CSVs can be used as a proxy check because they include 1X2 and total-goals odds for major leagues:

```powershell
python -m superbru_score_engine football-data-league-backtest --config config.yaml --seasons 2122,2223,2324,2425 --divisions E0,D1,SP1,I1,F1 --download --out-dir outputs/free-football-data-h2h-totals
```

This does not replace a World Cup-specific historical test, but it does answer whether the current scoreline inversion tends to benefit from adding an over/under 2.5 market on a large free football sample.

Before trusting production picks, verify:

- Historical odds coverage is large enough before treating any backtest calibration as stable.
- `config-check` passes against the calibration profile you intend to use.
- The JSON diagnostics look sensible for lambdas, result probabilities, modal scoreline, EV gaps, sensitivity stability, and candidate-grid probability mass.

Superbru's World Cup scoring is configured as 3 points for an exact score, 1.5 for a close score with the right outcome, 1 for the right outcome only, and 0 for the wrong outcome. Its close rule is equivalent to `ci_cutoff: 1.5`: the pick must have the right outcome and be either one goal out, or two goals out with the correct goal difference. Knockout scoring uses the regular-time score unless the match is drawn after regular time; in that case it is scored after extra time, and penalty shootouts remain draws.

The app canonicalizes common national-team aliases such as `USA`/`United States`, `Korea Republic`/`South Korea`, `IR Iran`/`Iran`, and `Czechia`/`Czech Republic` before joining odds, fixtures, and ratings.

Fixture metadata joins fail loudly when a row cannot be matched to an odds event by match ID or canonical team pair. This is intentional: a stale fixture row or missing alias should be fixed instead of silently degrading ratings, overrides, or venue handling.

Manual home/host advantage is applied only on the ratings-only fallback path. When bookmaker 1X2 odds are available, the market is assumed to have already priced venue and host effects, so the manual `home_advantage_goals` term is not blended into the odds-derived rates. For host fallback matches, the bump is venue-conditioned: the United States only receives the host bump in the United States, Canada in Canada, and Mexico in Mexico.

The default `big5_h2h_totals` profile uses `devig_method: multiplicative`, `dixon_coles_rho: -0.08`, `odds_weight: 1.0`, and `ratings_weight: 0.0`. It is a Football-Data Big Five league proxy calibration for the live `h2h,totals` market structure. The alternative `worldcup_1x2` profile uses `power`, `-0.04`, and odds-only, based on Football-Data 2014/2018/2022 World Cup 1X2 odds. Treat the two configs as alternative calibration anchors and pick whichever evidence base you trust more for the fixtures you are predicting.
