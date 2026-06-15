from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

from superbru_score_engine.betting import BettingSuggestion, find_betting_suggestions
from superbru_score_engine.config import AppConfig, load_config
from superbru_score_engine.decision import Prediction, SuperbruDecisionEngine, score_prediction
from superbru_score_engine.ingest import MatchOdds, ProviderUnavailable
from superbru_score_engine.ingest.cache import JsonCache
from superbru_score_engine.ingest.offline import OfflineJsonProvider
from superbru_score_engine.ingest.oddspedia import OddspediaProvider
from superbru_score_engine.ingest.results import (
    OddspediaResultsProvider,
    load_results_csv,
    load_results_json,
    results_console_table,
    write_results_csv,
)
from superbru_score_engine.ingest.the_odds_api import TheOddsAPIProvider
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.devig import parse_scoreline_label
from superbru_score_engine.model.ratings import MatchResult, RatingsStore
from superbru_score_engine.model.team_names import canonical_team_key
from superbru_score_engine.report.outputs import betting_console_table, console_table, write_betting_outputs, write_outputs


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "predict":
        return run_predict(args, config)
    if args.command == "backtest":
        from superbru_score_engine.backtest.runner import run_backtest

        return run_backtest(args, config)
    if args.command == "results":
        return run_results(args, config)
    if args.command == "public-results-backtest":
        from superbru_score_engine.backtest.public_results import run_public_results_backtest

        return run_public_results_backtest(args, config)
    if args.command == "football-data-backtest":
        from superbru_score_engine.backtest.football_data import run_football_data_backtest

        return run_football_data_backtest(args, config)
    if args.command == "the-odds-api-historical-backtest":
        from superbru_score_engine.backtest.the_odds_api_historical import run_the_odds_api_historical_backtest

        return run_the_odds_api_historical_backtest(args, config)
    if args.command == "football-data-league-backtest":
        from superbru_score_engine.backtest.football_data_leagues import run_football_data_league_backtest

        return run_football_data_league_backtest(args, config)
    parser.print_help()
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="World Cup 2026 Superbru score-prediction engine")
    subparsers = parser.add_subparsers(dest="command", required=True)

    predict = subparsers.add_parser("predict", help="Generate Superbru expected-points score predictions")
    predict.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    predict.add_argument("--fixtures", help="Optional fixtures CSV with venue/stage/override metadata")
    predict.add_argument("--results", help="Optional completed results CSV for ratings updates")
    predict.add_argument("--results-json", help="Use a saved results JSON snapshot for ratings updates")
    predict.add_argument("--live-results", action="store_true", help="Fetch completed results with the configured results provider")
    predict.add_argument("--odds-json", help="Use a saved odds JSON snapshot instead of live providers")
    predict.add_argument("--out-dir", default="outputs", help="Directory for predictions.csv and predictions.json")
    predict.add_argument("--no-save-ratings", action="store_true", help="Do not persist ratings after ingesting results")
    predict.add_argument("--include-betting", action="store_true", help="Also emit the optional betting-value report")

    backtest = subparsers.add_parser("backtest", help="Run the historical scoring harness")
    backtest.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    backtest.add_argument("--fixtures", required=True, help="CSV with actual results and fixture metadata")
    backtest.add_argument("--odds-json", required=True, help="Historical/saved odds JSON snapshot")
    backtest.add_argument("--out-dir", default="outputs", help="Directory for backtest outputs")
    backtest.add_argument("--rho-grid", default="", help="Comma-separated Dixon-Coles rho values for calibration")
    backtest.add_argument("--ci-grid", default="", help="Comma-separated CI cutoffs for calibration")

    results = subparsers.add_parser("results", help="Fetch completed results without using odds quota")
    results.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    results.add_argument("--results-json", help="Use a saved results JSON snapshot instead of the live results provider")
    results.add_argument("--out-dir", default="outputs", help="Directory for results.csv")
    results.add_argument("--no-save-ratings", action="store_true", help="Do not persist ratings after ingesting results")

    public = subparsers.add_parser(
        "public-results-backtest",
        help="Run a ratings-only World Cup check from public historical results",
    )
    public.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    public.add_argument("--source-csv", default="", help="Existing public international-results CSV")
    public.add_argument(
        "--download-url",
        default="https://raw.githubusercontent.com/martj42/international_results/master/results.csv",
        help="Public CSV URL used when --source-csv is omitted or missing",
    )
    public.add_argument("--tournament", default="FIFA World Cup", help="Tournament name to backtest")
    public.add_argument("--years", default="2014,2018,2022", help="Comma-separated tournament years")
    public.add_argument("--out-dir", default="outputs", help="Directory for backtest outputs")
    public.add_argument("--rho-grid", default="", help="Comma-separated Dixon-Coles rho values for calibration")
    public.add_argument("--ci-grid", default="", help="Comma-separated CI cutoffs for calibration")

    football_data = subparsers.add_parser(
        "football-data-backtest",
        help="Run the odds-based World Cup check from Football-Data's workbook",
    )
    football_data.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    football_data.add_argument("--workbook", default="work/WorldCup2026.xlsx", help="Football-Data World Cup workbook")
    football_data.add_argument(
        "--download-url",
        default="https://www.football-data.co.uk/WorldCup2026.xlsx",
        help="Workbook URL used when --workbook is omitted or missing",
    )
    football_data.add_argument("--years", default="2014,2018,2022", help="Comma-separated World Cup years")
    football_data.add_argument("--out-dir", default="outputs", help="Directory for backtest outputs")
    football_data.add_argument(
        "--devig-method-grid",
        default="",
        help="Comma-separated de-vig methods: multiplicative,power,additive",
    )
    football_data.add_argument("--rho-grid", default="", help="Comma-separated Dixon-Coles rho values for calibration")
    football_data.add_argument("--ci-grid", default="", help="Comma-separated CI cutoffs for calibration")
    football_data.add_argument(
        "--odds-weight-grid",
        default="",
        help="Comma-separated odds weights; ratings weights default to 1 - odds weight",
    )
    football_data.add_argument(
        "--ratings-weight-grid",
        default="",
        help="Optional comma-separated ratings weights; when supplied, cross-joins with odds weights",
    )

    historical = subparsers.add_parser(
        "the-odds-api-historical-backtest",
        help="Compare historical h2h-only vs h2h+totals snapshots from The Odds API",
    )
    historical.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    historical.add_argument("--fixtures", required=True, help="CSV with actual results and fixture metadata")
    historical.add_argument("--snapshots-json", default="", help="Saved historical snapshots from The Odds API")
    historical.add_argument("--fetch", action="store_true", help="Download snapshots from The Odds API historical odds endpoint")
    historical.add_argument(
        "--date-grid",
        default="",
        help="Comma-separated ISO8601 snapshot timestamps; required with --fetch",
    )
    historical.add_argument("--sport-key", default="", help="The Odds API sport key; defaults to config")
    historical.add_argument("--regions", default="", help="Historical odds regions; defaults to config")
    historical.add_argument("--markets", default="", help="Historical markets; defaults to config, usually h2h,totals")
    historical.add_argument("--bookmakers", default="", help="Optional comma-separated bookmaker keys")
    historical.add_argument(
        "--market-mode-grid",
        default="h2h,h2h_totals",
        help="Comma-separated modes to compare: h2h,h2h_totals",
    )
    historical.add_argument(
        "--devig-method-grid",
        default="",
        help="Comma-separated de-vig methods: multiplicative,power,additive",
    )
    historical.add_argument("--rho-grid", default="", help="Comma-separated Dixon-Coles rho values for calibration")
    historical.add_argument("--ci-grid", default="", help="Comma-separated CI cutoffs for calibration")
    historical.add_argument("--out-dir", default="outputs", help="Directory for backtest outputs")

    league_data = subparsers.add_parser(
        "football-data-league-backtest",
        help="Free Football-Data league proxy test comparing h2h-only against h2h+totals",
    )
    league_data.add_argument("--config", default="config.yaml", help="Path to YAML/JSON config")
    league_data.add_argument("--seasons", default="2223", help="Comma-separated Football-Data season codes, e.g. 2122,2223")
    league_data.add_argument("--divisions", default="E0", help="Comma-separated division codes, e.g. E0,D1,SP1,I1,F1")
    league_data.add_argument("--data-dir", default="work/football-data-leagues", help="Directory for downloaded CSVs")
    league_data.add_argument("--download", action="store_true", help="Download or refresh CSVs from football-data.co.uk")
    league_data.add_argument("--odds-set", choices=("closing", "opening"), default="closing", help="Use closing or earlier average odds")
    league_data.add_argument("--max-workers", default="1", help="Parallel workers for odds-only calibration")
    league_data.add_argument("--skip-reliability", action="store_true", help="Skip reliability-cell output for very large runs")
    league_data.add_argument(
        "--market-mode-grid",
        default="h2h,h2h_totals",
        help="Comma-separated modes to compare: h2h,h2h_totals",
    )
    league_data.add_argument(
        "--devig-method-grid",
        default="",
        help="Comma-separated de-vig methods: multiplicative,power,additive",
    )
    league_data.add_argument("--rho-grid", default="", help="Comma-separated Dixon-Coles rho values for calibration")
    league_data.add_argument("--ci-grid", default="", help="Comma-separated CI cutoffs for calibration")
    league_data.add_argument("--out-dir", default="outputs", help="Directory for backtest outputs")
    return parser


def run_predict(args: argparse.Namespace, config: AppConfig) -> int:
    ratings = RatingsStore(config.paths.ratings_store)
    completed_results = collect_results(args, config)
    if completed_results:
        applied, skipped = ratings.update_results(completed_results)
        if not args.no_save_ratings:
            ratings.save()
        print(f"Updated ratings with {applied} completed result(s); {skipped} already applied.")

    matches = fetch_matches(args, config)
    if args.fixtures:
        matches = merge_fixture_metadata(matches, args.fixtures)

    model = OddsToScorelineModel(config.model, ratings)
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)
    predictions: list[Prediction] = []
    betting_suggestions: list[BettingSuggestion] = []
    include_betting = args.include_betting or config.betting.enabled
    for match in matches:
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        prediction = apply_manual_override(prediction, match, distribution.matrix, config)
        predictions.append(prediction)
        if include_betting:
            betting_suggestions.extend(find_betting_suggestions(distribution, config.betting))

    predictions.sort(key=lambda item: item.commence_time)
    csv_path, json_path = write_outputs(predictions, args.out_dir)
    print(console_table(predictions))
    print(f"\nWrote {csv_path}")
    print(f"Wrote {json_path}")
    if include_betting:
        betting_suggestions.sort(key=lambda item: item.expected_return, reverse=True)
        betting_csv_path, betting_json_path = write_betting_outputs(betting_suggestions, args.out_dir)
        print("\nBetting suggestions")
        print(betting_console_table(betting_suggestions))
        print(f"\nWrote {betting_csv_path}")
        print(f"Wrote {betting_json_path}")
    return 0


def run_results(args: argparse.Namespace, config: AppConfig) -> int:
    ratings = RatingsStore(config.paths.ratings_store)
    completed_results = fetch_results_snapshot(args, config)
    applied, skipped = ratings.update_results(completed_results)
    if not args.no_save_ratings:
        ratings.save()
    csv_path = write_results_csv(completed_results, args.out_dir)
    print(results_console_table(completed_results))
    print(f"\nUpdated ratings with {applied} completed result(s); {skipped} already applied.")
    print(f"Wrote {csv_path}")
    return 0


def fetch_matches(args: argparse.Namespace, config: AppConfig) -> list[MatchOdds]:
    if args.odds_json:
        return OfflineJsonProvider(args.odds_json).fetch_odds()

    cache = JsonCache(config.paths.cache_dir)
    oddspedia = OddspediaProvider(config.providers.oddspedia, cache)
    the_odds_api = TheOddsAPIProvider(config.providers.the_odds_api, cache)
    providers = [oddspedia, the_odds_api] if config.providers.preferred == "oddspedia" else [the_odds_api, oddspedia]

    failures: list[str] = []
    for provider in providers:
        try:
            return provider.fetch_odds()
        except ProviderUnavailable as exc:
            failures.append(f"{provider.name}: {exc}")
            continue

    raise ProviderUnavailable("No odds provider could supply structured odds. " + " | ".join(failures))


def collect_results(args: argparse.Namespace, config: AppConfig) -> list[MatchResult]:
    completed_results: list[MatchResult] = []
    if args.results:
        completed_results.extend(load_results_csv(args.results))
    if args.results_json:
        completed_results.extend(load_results_json(args.results_json, result_filter_config(config)))
    if args.live_results:
        completed_results.extend(fetch_live_results(config))
    return completed_results


def fetch_results_snapshot(args: argparse.Namespace, config: AppConfig) -> list[MatchResult]:
    if args.results_json:
        return load_results_json(args.results_json, result_filter_config(config))
    return fetch_live_results(config)


def fetch_live_results(config: AppConfig) -> list[MatchResult]:
    results_config = configured_results_provider_config(config)
    cache = JsonCache(config.paths.cache_dir)
    provider = OddspediaResultsProvider(results_config, cache)
    return provider.fetch_results()


def configured_results_provider_config(config: AppConfig) -> dict:
    results_config = dict(config.providers.results)
    provider_name = str(results_config.get("preferred", "oddspedia")).lower()
    if provider_name != "oddspedia":
        raise ProviderUnavailable(f"Unsupported results provider: {provider_name}")
    provider_config = dict(results_config.get("oddspedia") or config.providers.oddspedia)
    for key in ("min_commence_time", "max_commence_time", "completed_statuses"):
        if key in results_config and key not in provider_config:
            provider_config[key] = results_config[key]
    return provider_config


def result_filter_config(config: AppConfig) -> dict:
    results_config = dict(config.providers.results)
    provider_config = dict(results_config.get("oddspedia") or {})
    for key in ("min_commence_time", "max_commence_time", "completed_statuses"):
        if key in results_config and key not in provider_config:
            provider_config[key] = results_config[key]
    return provider_config


def merge_fixture_metadata(matches: list[MatchOdds], fixtures_path: str | Path) -> list[MatchOdds]:
    fixtures = pd.read_csv(fixtures_path).fillna("")
    by_id = {str(row["match_id"]): row for _, row in fixtures.iterrows() if "match_id" in row and str(row["match_id"])}
    by_teams = {
        (canonical_team_key(str(row.get("home_team", ""))), canonical_team_key(str(row.get("away_team", "")))): row
        for _, row in fixtures.iterrows()
    }
    matched_fixture_rows: set[int] = set()
    merged: list[MatchOdds] = []
    for match in matches:
        row = by_id.get(match.match_id)
        if row is None:
            row = by_teams.get((canonical_team_key(match.home_team), canonical_team_key(match.away_team)))
        if row is None:
            merged.append(match)
            continue
        matched_fixture_rows.add(int(row.name))
        raw = dict(match.raw)
        for column in row.index:
            raw[column] = row[column]
        merged.append(
            replace(
                match,
                commence_time=str(row.get("commence_time") or match.commence_time),
                neutral=_bool_value(row.get("neutral"), match.neutral),
                venue_country=str(row.get("venue_country") or match.venue_country or ""),
                stage=str(row.get("stage") or match.stage or ""),
                raw=raw,
            )
        )
    unmatched = fixtures.loc[[idx for idx in fixtures.index if int(idx) not in matched_fixture_rows]]
    if not unmatched.empty:
        sample = unmatched.head(10)
        rendered = "; ".join(
            f"{row.get('match_id', '') or '?'} {row.get('home_team', '')} vs {row.get('away_team', '')}"
            for _, row in sample.iterrows()
        )
        raise ValueError(
            "Fixture metadata did not match any odds event. "
            "Check team-name aliases, match IDs, or remove stale fixture rows. "
            f"Unmatched rows: {rendered}"
        )
    return merged


def apply_manual_override(prediction: Prediction, match: MatchOdds, matrix, config: AppConfig) -> Prediction:
    override = str(match.raw.get("manual_override_scoreline") or "").strip()
    if not override:
        return prediction
    score = parse_scoreline_label(override)
    if score is None:
        return prediction
    home_goals, away_goals = score
    candidate = score_prediction(matrix, home_goals, away_goals, config.superbru.ci_cutoff)
    diagnostics = dict(prediction.diagnostics)
    diagnostics["manual_override_scoreline"] = candidate.scoreline
    diagnostics["dead_rubber_flag"] = bool(_bool_value(match.raw.get("dead_rubber_flag"), False))
    top = (candidate,) + tuple(item for item in prediction.top_candidates if item.scoreline != candidate.scoreline)
    return replace(prediction, recommended=candidate, top_candidates=top[:3], diagnostics=diagnostics)


def _bool_value(value, default: bool) -> bool:
    if value in ("", None):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


if __name__ == "__main__":
    sys.exit(main())
