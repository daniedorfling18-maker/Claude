from __future__ import annotations

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import urlretrieve

import pandas as pd

from superbru_score_engine.config import AppConfig
from superbru_score_engine.decision import SuperbruDecisionEngine
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.ingest.base import MatchOdds, MarketOdds, OutcomeOdds
from superbru_score_engine.model import DistributionResult, OddsToScorelineModel
from superbru_score_engine.model.ratings import MatchResult, RatingsStore

from .inference import bootstrap_mean_ci, paired_bootstrap_delta
from .metrics import (
    brier_score_1x2,
    exact_score_log_loss,
    log_loss_1x2,
    outcome_label,
    ranked_probability_score,
    total_goals_crps,
)
from .runner import naive_baseline_pick, reliability_cells
from .utils import mode_delta, parse_float_grid, parse_str_grid


FOOTBALL_DATA_BASE_URL = "https://www.football-data.co.uk/mmz4281"
BestMode = tuple[float, str, float, float, float, pd.DataFrame]


def run_football_data_league_backtest(args: argparse.Namespace, config: AppConfig) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    partial_calibration_path = out_dir / "football_data_league_partial_calibration.csv"
    progress_path = out_dir / "football_data_league_progress.json"

    seasons = parse_str_grid(args.seasons)
    divisions = parse_str_grid(args.divisions)
    csv_paths = ensure_league_csvs(seasons, divisions, Path(args.data_dir), args.download)
    raw_matches = load_league_matches(csv_paths, args.odds_set)

    fixtures_path = out_dir / "football_data_league_fixtures.csv"
    odds_path = out_dir / "football_data_league_odds.json"
    pd.DataFrame([fixture_row(match) for match in raw_matches]).to_csv(fixtures_path, index=False)
    odds_path.write_text(json.dumps([event_row(match) for match in raw_matches], indent=2), encoding="utf-8")

    market_modes = parse_str_grid(args.market_mode_grid) or ["h2h", "h2h_totals"]
    compare_on_totals_subset = any(_mode_uses_totals(mode) for mode in market_modes)
    compare_on_ah_subset = any(_mode_uses_asian_handicap(mode) for mode in market_modes)
    if compare_on_totals_subset:
        raw_matches = [match for match in raw_matches if match.get("over_25") and match.get("under_25")]
    if compare_on_ah_subset:
        raw_matches = [
            match
            for match in raw_matches
            if match.get("ah_line") is not None and match.get("ah_home_price") and match.get("ah_away_price")
        ]

    rho_grid = parse_float_grid(args.rho_grid) or [config.model.dixon_coles_rho]
    ci_grid = parse_float_grid(args.ci_grid) or [config.superbru.ci_cutoff]
    devig_methods = parse_str_grid(args.devig_method_grid) or [config.model.devig_method]
    ah_weight_grid = parse_float_grid(getattr(args, "asian_handicap_weight_grid", "")) or [config.model.asian_handicap_weight]
    total_combinations = sum(
        len(devig_methods) * len(rho_grid) * len(ci_grid) * (len(ah_weight_grid) if _mode_uses_asian_handicap(mode) else 1)
        for mode in market_modes
    )
    completed_combinations = 0
    _write_progress(
        progress_path,
        {
            "status": "running",
            "completed_combinations": completed_combinations,
            "total_combinations": total_combinations,
            "matches": int(len(raw_matches)),
            "last_completed": None,
        },
    )

    calibration_rows: list[dict[str, Any]] = []
    best_by_mode: dict[str, BestMode] = {}
    for market_mode in market_modes:
        mode_ah_weights = ah_weight_grid if _mode_uses_asian_handicap(market_mode) else [0.0]
        for devig_method in devig_methods:
            for rho in rho_grid:
                for ci_cutoff in ci_grid:
                    for ah_weight in mode_ah_weights:
                        trial_config = replace(
                            config,
                            model=replace(
                                config.model,
                                dixon_coles_rho=rho,
                                devig_method=devig_method,
                                asian_handicap_weight=ah_weight,
                            ),
                            superbru=replace(config.superbru, ci_cutoff=ci_cutoff),
                        )
                        frame = evaluate_league_matches(raw_matches, trial_config, market_mode, max_workers=int(args.max_workers))
                        avg_model = float(frame["model_points"].mean()) if not frame.empty else 0.0
                        avg_naive = float(frame["naive_points"].mean()) if not frame.empty else 0.0
                        row = {
                            "market_mode": market_mode,
                            "devig_method": devig_method,
                            "dixon_coles_rho": rho,
                            "ci_cutoff": ci_cutoff,
                            "asian_handicap_weight": ah_weight,
                            "matches": int(len(frame)),
                            "avg_model_points": avg_model,
                            "avg_naive_points": avg_naive,
                            "edge_vs_naive": avg_model - avg_naive,
                            "exact_hits": int((frame["model_points"] == 3.0).sum()) if not frame.empty else 0,
                            "close_hits": int((frame["model_points"] == 1.5).sum()) if not frame.empty else 0,
                            "outcome_only_hits": int((frame["model_points"] == 1.0).sum()) if not frame.empty else 0,
                            "misses": int((frame["model_points"] == 0.0).sum()) if not frame.empty else 0,
                            "has_asian_handicap": bool(_mode_uses_asian_handicap(market_mode)),
                        }
                        calibration_rows.append(row)
                        completed_combinations += 1
                        pd.DataFrame(calibration_rows).sort_values("avg_model_points", ascending=False).to_csv(
                            partial_calibration_path,
                            index=False,
                        )
                        _write_progress(
                            progress_path,
                            {
                                "status": "running",
                                "completed_combinations": completed_combinations,
                                "total_combinations": total_combinations,
                                "matches": int(len(frame)),
                                "last_completed": row,
                            },
                        )
                        current_best = best_by_mode.get(market_mode)
                        if current_best is None or avg_model > current_best[0]:
                            best_by_mode[market_mode] = (avg_model, devig_method, rho, ci_cutoff, ah_weight, frame)

    calibration = pd.DataFrame(calibration_rows).sort_values(["market_mode", "avg_model_points"], ascending=[True, False])
    calibration_path = out_dir / "football_data_league_calibration.csv"
    calibration.to_csv(calibration_path, index=False)

    frames: list[pd.DataFrame] = []
    summaries: dict[str, dict[str, Any]] = {}
    for market_mode, (_, devig_method, rho, ci_cutoff, ah_weight, frame) in best_by_mode.items():
        labelled = frame.copy()
        labelled.insert(0, "market_mode", market_mode)
        labelled["devig_method"] = devig_method
        labelled["dixon_coles_rho"] = rho
        labelled["ci_cutoff"] = ci_cutoff
        labelled["asian_handicap_weight"] = ah_weight
        frames.append(labelled)
        summaries[market_mode] = summarize_mode(market_mode, frame, devig_method, rho, ci_cutoff, ah_weight)

    details = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    details_path = out_dir / "football_data_league_backtest_results.csv"
    details.drop(columns=["distribution"], errors="ignore").to_csv(details_path, index=False)

    reliability_path = out_dir / "football_data_league_reliability_cells.csv"
    if args.skip_reliability:
        pd.DataFrame(columns=["bin", "count", "mean_predicted_probability", "observed_frequency"]).to_csv(reliability_path, index=False)
    else:
        reliability_source = frames[-1] if frames else pd.DataFrame()
        reliability_cells(reliability_source).to_csv(reliability_path, index=False)

    comparison_path = out_dir / "football_data_league_mode_comparison.csv"
    pd.DataFrame(summaries.values()).to_csv(comparison_path, index=False)

    summary = {
        "mode": "football_data_league_h2h_totals_backtest",
        "source": "football-data.co.uk league CSVs",
        "seasons": seasons,
        "divisions": divisions,
        "odds_set": args.odds_set,
        "matches_loaded": int(len(raw_matches)),
        "comparison_subset": _comparison_subset_label(compare_on_totals_subset, compare_on_ah_subset),
        "market_modes": summaries,
        "totals_delta_vs_h2h": mode_delta(summaries, "h2h_totals", "h2h"),
        "totals_delta_significance": paired_mode_significance(best_by_mode, "h2h_totals", "h2h"),
        "asian_handicap_delta_vs_h2h_totals": mode_delta(summaries, "h2h_totals_ah", "h2h_totals"),
        "asian_handicap_delta_significance": paired_mode_significance(best_by_mode, "h2h_totals_ah", "h2h_totals"),
        "fixtures": str(fixtures_path),
        "odds_json": str(odds_path),
        "calibration_results": str(calibration_path),
        "partial_calibration_results": str(partial_calibration_path),
        "progress": str(progress_path),
        "mode_comparison": str(comparison_path),
        "backtest_results": str(details_path),
        "reliability_cells": str(reliability_path),
        "reliability_skipped": bool(args.skip_reliability),
    }
    summary_path = out_dir / "football_data_league_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_progress(
        progress_path,
        {
            "status": "complete",
            "completed_combinations": completed_combinations,
            "total_combinations": total_combinations,
            "matches": int(len(raw_matches)),
            "summary": summary,
        },
    )
    print(json.dumps(summary, indent=2))
    return 0


def ensure_league_csvs(seasons: list[str], divisions: list[str], data_dir: Path, download: bool) -> list[Path]:
    data_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for season in seasons:
        for division in divisions:
            path = data_dir / f"{season}_{division}.csv"
            if download or not path.exists():
                try:
                    urlretrieve(f"{FOOTBALL_DATA_BASE_URL}/{season}/{division}.csv", path)
                except (HTTPError, URLError, OSError):
                    if not path.exists():
                        continue
            if not _looks_like_football_data_csv(path):
                continue
            if path.exists():
                paths.append(path)
    return paths


def load_league_matches(paths: list[Path], odds_set: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for path in paths:
        frame = _read_football_data_csv(path).fillna("")
        season, division = path.stem.split("_", 1)
        for idx, row in frame.iterrows():
            home_goals = _int_value(row.get("FTHG"))
            away_goals = _int_value(row.get("FTAG"))
            if home_goals is None or away_goals is None:
                continue
            prices = _odds_columns(row, odds_set)
            if prices is None:
                continue
            date = _date_value(row.get("Date"))
            if not date:
                continue
            match_id = f"fdl-{season}-{division}-{idx}"
            matches.append(
                {
                    "match_id": match_id,
                    "season": season,
                    "division": division,
                    "commence_time": f"{date}T00:00:00Z",
                    "home_team": str(row.get("HomeTeam")),
                    "away_team": str(row.get("AwayTeam")),
                    "home_goals": home_goals,
                    "away_goals": away_goals,
                    **prices,
                }
            )
    return sorted(matches, key=lambda item: (item["commence_time"], item["match_id"]))


def evaluate_league_matches(matches: list[dict[str, Any]], config: AppConfig, market_mode: str, max_workers: int = 1) -> pd.DataFrame:
    if max_workers > 1 and config.model.ratings_weight == 0:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            rows = list(executor.map(_evaluate_one_league_match, [(raw_match, config, market_mode) for raw_match in matches], chunksize=50))
        frame = pd.DataFrame([row for row in rows if row is not None])
        frame.attrs["distributions"] = {}
        return frame

    ratings = RatingsStore()
    model = OddsToScorelineModel(config.model, ratings)
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)
    rows: list[dict[str, Any]] = []
    distributions: dict[str, DistributionResult] = {}

    for raw_match in matches:
        match = match_odds(raw_match, include_totals=_mode_uses_totals(market_mode), include_asian_handicap=_mode_uses_asian_handicap(market_mode))
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        actual_home = int(raw_match["home_goals"])
        actual_away = int(raw_match["away_goals"])
        naive_home, naive_away = naive_baseline_pick(match, distribution)
        rows.append(_evaluation_row(raw_match, match, distribution, prediction, naive_home, naive_away, actual_home, actual_away, config))
        distributions[match.match_id] = distribution
        ratings.update_results(
            [
                MatchResult(
                    match_id=match.match_id,
                    commence_time=match.commence_time,
                    home_team=match.home_team,
                    away_team=match.away_team,
                    home_goals=actual_home,
                    away_goals=actual_away,
                )
            ]
        )

    frame = pd.DataFrame(rows)
    frame.attrs["distributions"] = distributions
    return frame


def _evaluate_one_league_match(payload: tuple[dict[str, Any], AppConfig, str]) -> dict[str, Any] | None:
    raw_match, config, market_mode = payload
    model = OddsToScorelineModel(config.model, RatingsStore())
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)
    match = match_odds(raw_match, include_totals=_mode_uses_totals(market_mode), include_asian_handicap=_mode_uses_asian_handicap(market_mode))
    distribution = model.build_distribution(match)
    prediction = decision.predict(distribution)
    actual_home = int(raw_match["home_goals"])
    actual_away = int(raw_match["away_goals"])
    naive_home, naive_away = naive_baseline_pick(match, distribution)
    return _evaluation_row(raw_match, match, distribution, prediction, naive_home, naive_away, actual_home, actual_away, config)


def _evaluation_row(
    raw_match: dict[str, Any],
    match: MatchOdds,
    distribution: DistributionResult,
    prediction: Any,
    naive_home: int,
    naive_away: int,
    actual_home: int,
    actual_away: int,
    config: AppConfig,
) -> dict[str, Any]:
    diagnostics = distribution.diagnostics
    return {
        "match_id": match.match_id,
        "season": raw_match["season"],
        "division": raw_match["division"],
        "commence_time": match.commence_time,
        "home_team": match.home_team,
        "away_team": match.away_team,
        "actual_scoreline": f"{actual_home}-{actual_away}",
        "model_scoreline": prediction.recommended.scoreline,
        "model_expected_points": prediction.recommended.expected_points,
        "model_points": score_actual_prediction(
            prediction.recommended.home_goals,
            prediction.recommended.away_goals,
            actual_home,
            actual_away,
            config.superbru.ci_cutoff,
        ),
        "naive_scoreline": f"{naive_home}-{naive_away}",
        "naive_points": score_actual_prediction(naive_home, naive_away, actual_home, actual_away, config.superbru.ci_cutoff),
        "p_exact": prediction.recommended.p_exact,
        "p_close": prediction.recommended.p_close,
        "p_outcome": prediction.recommended.p_outcome,
        "lambda_home": distribution.lambda_home,
        "lambda_away": distribution.lambda_away,
        "has_totals": distribution.fair_total is not None,
        "fair_total_line": diagnostics.get("fair_total_line"),
        "fair_over": diagnostics.get("fair_over"),
        "model_over": diagnostics.get("model_over"),
        "has_asian_handicap": diagnostics.get("has_asian_handicap"),
        "fair_ah_line": diagnostics.get("fair_ah_line"),
        "fair_ah_home_cover": diagnostics.get("fair_ah_home_cover"),
        "model_ah_home_cover": diagnostics.get("model_ah_home_cover"),
        "model_ah_error": diagnostics.get("model_ah_error"),
        "asian_handicap_weight": diagnostics.get("asian_handicap_weight"),
        "raw_ah_line": raw_match.get("ah_line"),
        "home_goals": actual_home,
        "away_goals": actual_away,
        "distribution": match.match_id,
        **_scoring_fields(distribution, actual_home, actual_away),
    }


def match_odds(raw_match: dict[str, Any], include_totals: bool, include_asian_handicap: bool = False) -> MatchOdds:
    markets = {
        "h2h": (
            MarketOdds(
                key="h2h",
                bookmaker="Football-Data Avg",
                outcomes=(
                    OutcomeOdds(name=raw_match["home_team"], price=float(raw_match["home_price"])),
                    OutcomeOdds(name="Draw", price=float(raw_match["draw_price"])),
                    OutcomeOdds(name=raw_match["away_team"], price=float(raw_match["away_price"])),
                ),
                last_update=str(raw_match["commence_time"]),
            ),
        )
    }
    if include_totals and raw_match.get("over_25") and raw_match.get("under_25"):
        markets["totals"] = (
            MarketOdds(
                key="totals",
                bookmaker="Football-Data Avg",
                outcomes=(
                    OutcomeOdds(name="Over", price=float(raw_match["over_25"]), point=2.5),
                    OutcomeOdds(name="Under", price=float(raw_match["under_25"]), point=2.5),
                ),
                last_update=str(raw_match["commence_time"]),
            ),
        )
    if (
        include_asian_handicap
        and raw_match.get("ah_line") is not None
        and raw_match.get("ah_home_price")
        and raw_match.get("ah_away_price")
    ):
        line = float(raw_match["ah_line"])
        markets["spreads"] = (
            MarketOdds(
                key="spreads",
                bookmaker="Football-Data Avg",
                outcomes=(
                    OutcomeOdds(name=raw_match["home_team"], price=float(raw_match["ah_home_price"]), point=line, description="home"),
                    OutcomeOdds(name=raw_match["away_team"], price=float(raw_match["ah_away_price"]), point=-line, description="away"),
                ),
                last_update=str(raw_match["commence_time"]),
            ),
        )
    return MatchOdds(
        match_id=str(raw_match["match_id"]),
        commence_time=str(raw_match["commence_time"]),
        home_team=str(raw_match["home_team"]),
        away_team=str(raw_match["away_team"]),
        markets=markets,
        neutral=False,
        raw=dict(raw_match),
    )


def event_row(raw_match: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw_match["match_id"],
        "sport_key": "soccer",
        "commence_time": raw_match["commence_time"],
        "home_team": raw_match["home_team"],
        "away_team": raw_match["away_team"],
        "bookmakers": [
            {
                "key": "football_data_avg",
                "title": "Football-Data Avg",
                "markets": [
                    market_to_json(market)
                    for markets in match_odds(raw_match, include_totals=True, include_asian_handicap=True).markets.values()
                    for market in markets
                ],
            }
        ],
    }


def market_to_json(market: MarketOdds) -> dict[str, Any]:
    return {
        "key": market.key,
        "outcomes": [
            {"name": outcome.name, "price": outcome.price, **({"point": outcome.point} if outcome.point is not None else {})}
            for outcome in market.outcomes
        ],
    }


def fixture_row(raw_match: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": raw_match["match_id"],
        "commence_time": raw_match["commence_time"],
        "home_team": raw_match["home_team"],
        "away_team": raw_match["away_team"],
        "home_goals": raw_match["home_goals"],
        "away_goals": raw_match["away_goals"],
        "season": raw_match["season"],
        "division": raw_match["division"],
        "ah_line": raw_match.get("ah_line"),
    }


def _scoring_fields(distribution: DistributionResult, actual_home: int, actual_away: int) -> dict[str, Any]:
    """Per-match probabilistic scoring columns (1X2 + full-distribution proper scores)."""
    model_probs = (
        float(distribution.diagnostics.get("model_home_win", 0.0)),
        float(distribution.diagnostics.get("model_draw", 0.0)),
        float(distribution.diagnostics.get("model_away_win", 0.0)),
    )
    actual_result = outcome_label(actual_home, actual_away)
    return {
        "actual_result": actual_result,
        "model_home_win": model_probs[0],
        "model_draw": model_probs[1],
        "model_away_win": model_probs[2],
        "brier_1x2": brier_score_1x2(model_probs, actual_result),
        "log_loss_1x2": log_loss_1x2(model_probs, actual_result),
        "rps_1x2": ranked_probability_score(model_probs, actual_result),
        "exact_log_loss": exact_score_log_loss(distribution.matrix, actual_home, actual_away),
        "total_goals_crps": total_goals_crps(distribution.matrix, actual_home + actual_away),
    }


def _mean_or_none(frame: pd.DataFrame, column: str) -> float | None:
    return float(frame[column].mean()) if column in frame.columns and not frame.empty else None


def summarize_mode(market_mode: str, frame: pd.DataFrame, devig_method: str, rho: float, ci_cutoff: float, asian_handicap_weight: float) -> dict[str, Any]:
    if frame.empty:
        return {
            "market_mode": market_mode,
            "devig_method": devig_method,
            "dixon_coles_rho": rho,
            "ci_cutoff": ci_cutoff,
            "asian_handicap_weight": asian_handicap_weight,
            "matches": 0,
            "avg_model_points": 0.0,
            "avg_naive_points": 0.0,
            "edge_vs_naive": 0.0,
        }
    model_points = frame["model_points"].to_numpy(dtype=float)
    naive_points = frame["naive_points"].to_numpy(dtype=float)
    points_ci = bootstrap_mean_ci(model_points)
    vs_naive = paired_bootstrap_delta(model_points, naive_points)
    return {
        "market_mode": market_mode,
        "devig_method": devig_method,
        "dixon_coles_rho": rho,
        "ci_cutoff": ci_cutoff,
        "asian_handicap_weight": asian_handicap_weight,
        "matches": int(len(frame)),
        "avg_model_points": float(model_points.mean()),
        "avg_model_points_ci_low": points_ci["ci_low"],
        "avg_model_points_ci_high": points_ci["ci_high"],
        "avg_naive_points": float(naive_points.mean()),
        "edge_vs_naive": float(model_points.mean() - naive_points.mean()),
        "edge_vs_naive_ci_low": vs_naive["ci_low"],
        "edge_vs_naive_ci_high": vs_naive["ci_high"],
        "edge_vs_naive_p_value": vs_naive["p_value"],
        "edge_vs_naive_significant": vs_naive["significant"],
        "avg_brier_1x2": _mean_or_none(frame, "brier_1x2"),
        "avg_log_loss_1x2": _mean_or_none(frame, "log_loss_1x2"),
        "avg_rps_1x2": _mean_or_none(frame, "rps_1x2"),
        "avg_exact_log_loss": _mean_or_none(frame, "exact_log_loss"),
        "avg_total_goals_crps": _mean_or_none(frame, "total_goals_crps"),
        "avg_model_ah_error": _mean_or_none(frame, "model_ah_error"),
        "exact_hits": int((frame["model_points"] == 3.0).sum()),
        "close_hits": int((frame["model_points"] == 1.5).sum()),
        "outcome_only_hits": int((frame["model_points"] == 1.0).sum()),
        "misses": int((frame["model_points"] == 0.0).sum()),
        "outcome_accuracy": float((frame["model_points"] > 0.0).mean()),
        "exact_rate": float((frame["model_points"] == 3.0).mean()),
    }


def paired_mode_significance(
    best_by_mode: dict[str, BestMode], test_mode: str, baseline_mode: str, column: str = "model_points"
) -> dict[str, object] | None:
    """Paired bootstrap of one mode's per-match points against another, matched on match_id."""
    if test_mode not in best_by_mode or baseline_mode not in best_by_mode:
        return None
    test_frame = best_by_mode[test_mode][5][["match_id", column]].rename(columns={column: "test"})
    base_frame = best_by_mode[baseline_mode][5][["match_id", column]].rename(columns={column: "base"})
    merged = test_frame.merge(base_frame, on="match_id", how="inner")
    if merged.empty:
        return None
    result = paired_bootstrap_delta(merged["test"].to_numpy(float), merged["base"].to_numpy(float))
    result["test_mode"] = test_mode
    result["baseline_mode"] = baseline_mode
    return result


def _odds_columns(row: pd.Series, odds_set: str) -> dict[str, float] | None:
    prefixes = ["AvgC", "Avg"] if odds_set == "closing" else ["Avg", "AvgC"]
    for prefix in prefixes:
        home = _float_value(row.get(f"{prefix}H"))
        draw = _float_value(row.get(f"{prefix}D"))
        away = _float_value(row.get(f"{prefix}A"))
        over = _float_value(row.get(f"{prefix}>2.5"))
        under = _float_value(row.get(f"{prefix}<2.5"))
        if home and draw and away:
            prices = {
                "home_price": home,
                "draw_price": draw,
                "away_price": away,
                "over_25": over,
                "under_25": under,
            }
            prices.update(_asian_handicap_columns(row, odds_set))
            return prices
    return None


def _asian_handicap_columns(row: pd.Series, odds_set: str) -> dict[str, float]:
    candidates = (
        [("AHCh", "AvgCAHH", "AvgCAHA"), ("AHh", "AvgAHH", "AvgAHA")]
        if odds_set == "closing"
        else [("AHh", "AvgAHH", "AvgAHA"), ("AHCh", "AvgCAHH", "AvgCAHA")]
    )
    for line_key, home_key, away_key in candidates:
        line = _number_value(row.get(line_key))
        home = _float_value(row.get(home_key))
        away = _float_value(row.get(away_key))
        if line is not None and home and away:
            return {"ah_line": line, "ah_home_price": home, "ah_away_price": away}
    return {"ah_line": None, "ah_home_price": None, "ah_away_price": None}


def _looks_like_football_data_csv(path: Path) -> bool:
    try:
        header = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return False
    return "HomeTeam" in header and "AwayTeam" in header and "FTHG" in header


def _read_football_data_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _comparison_subset_label(requires_totals: bool, requires_asian_handicap: bool) -> str:
    parts = ["h2h"]
    if requires_totals:
        parts.append("over/under 2.5")
    if requires_asian_handicap:
        parts.append("Asian handicap")
    return "matches with " + " and ".join(parts) + " odds"


def _mode_uses_totals(mode: str) -> bool:
    return "totals" in (mode or "").lower()


def _mode_uses_asian_handicap(mode: str) -> bool:
    normalised = (mode or "").lower().replace("-", "_").replace("+", "_").replace(",", "_")
    tokens = {token for token in normalised.split("_") if token}
    return "ah" in tokens or "asian" in tokens or "handicap" in tokens


def _float_value(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 1.0 else None


def _number_value(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_value(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _date_value(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return ""
    return parsed.date().isoformat()
