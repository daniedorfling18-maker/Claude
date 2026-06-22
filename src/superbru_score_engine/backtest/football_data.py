from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

import pandas as pd

from superbru_score_engine.backtest.metrics import (
    brier_score_1x2,
    exact_score_log_loss,
    log_loss_1x2,
    outcome_label,
    ranked_probability_score,
    summarise_validation,
    total_goals_crps,
)
from superbru_score_engine.config import AppConfig
from superbru_score_engine.decision import SuperbruDecisionEngine
from superbru_score_engine.ingest.base import MatchOdds, MarketOdds, OutcomeOdds
from superbru_score_engine.model import DistributionResult, OddsToScorelineModel
from superbru_score_engine.model.ratings import MatchResult, RatingsStore
from superbru_score_engine.model.team_names import canonical_team_name, team_key

from .runner import (
    VALIDATION_TIMING_NOTE,
    _clean_validation_metrics,
    _low_score_probabilities,
    _score_pick,
    baseline_picks,
    naive_baseline_pick,
    reliability_cells,
)
from .utils import parse_float_grid, parse_int_grid, parse_str_grid


FOOTBALL_DATA_WORLDCUP_URL = "https://www.football-data.co.uk/WorldCup2026.xlsx"
SHEET_YEARS = {
    "WorldCup2014": 2014,
    "WorldCup2018": 2018,
    "WorldCup2022": 2022,
}
HOST_COUNTRIES = {
    2014: "Brazil",
    2018: "Russia",
    2022: "Qatar",
}


def run_football_data_backtest(args: argparse.Namespace, config: AppConfig) -> int:
    workbook_path = ensure_workbook(args.workbook, args.download_url)
    years = parse_int_grid(args.years) or [2014, 2018, 2022]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    matches = load_football_data_matches(workbook_path, years)
    fixtures = football_data_fixtures(matches)
    odds = football_data_odds_json(matches)

    fixtures_path = out_dir / "football_data_worldcup_fixtures.csv"
    odds_path = out_dir / "football_data_worldcup_odds.json"
    fixtures.to_csv(fixtures_path, index=False)
    odds_path.write_text(json.dumps(odds, indent=2), encoding="utf-8")

    rho_grid = parse_float_grid(args.rho_grid) or [config.model.dixon_coles_rho]
    ci_grid = parse_float_grid(args.ci_grid) or [config.superbru.ci_cutoff]
    weight_grid = _weight_grid(args.odds_weight_grid, args.ratings_weight_grid, config)
    devig_methods = parse_str_grid(args.devig_method_grid, lowercase=True) or [config.model.devig_method]
    calibration_rows: list[dict[str, Any]] = []
    best: tuple[float, str, float, float, float, float, pd.DataFrame] | None = None
    for devig_method in devig_methods:
        for rho in rho_grid:
            for ci_cutoff in ci_grid:
                for odds_weight, ratings_weight in weight_grid:
                    trial_config = replace(
                        config,
                        model=replace(
                            config.model,
                            dixon_coles_rho=rho,
                            devig_method=devig_method,
                            odds_weight=odds_weight,
                            ratings_weight=ratings_weight,
                        ),
                        superbru=replace(config.superbru, ci_cutoff=ci_cutoff),
                    )
                    frame = evaluate_football_data_matches(matches, trial_config)
                    validation = _clean_validation_metrics(summarise_validation(frame.to_dict(orient="records"))) if not frame.empty else {}
                    avg_model = float(frame["model_points"].mean()) if not frame.empty else 0.0
                    avg_naive = float(frame["naive_points"].mean()) if not frame.empty else 0.0
                    calibration_rows.append(
                        {
                            "devig_method": devig_method,
                            "dixon_coles_rho": rho,
                            "ci_cutoff": ci_cutoff,
                            "odds_weight": odds_weight,
                            "ratings_weight": ratings_weight,
                            "matches": int(len(frame)),
                            "avg_model_points": avg_model,
                            "avg_naive_points": avg_naive,
                            "edge_vs_naive": avg_model - avg_naive,
                            "exact_score_hit_rate": validation.get("exact_score_hit_rate"),
                            "right_result_accuracy": validation.get("right_result_accuracy"),
                            "brier_score_1x2": validation.get("brier_score_1x2"),
                            "log_loss_1x2": validation.get("log_loss_1x2"),
                            "ranked_probability_score_1x2": validation.get("ranked_probability_score_1x2"),
                            "exact_score_log_loss": validation.get("exact_score_log_loss"),
                            "total_goals_crps": validation.get("total_goals_crps"),
                            "exact_hits": int((frame["model_points"] == 3.0).sum()) if not frame.empty else 0,
                            "close_hits": int((frame["model_points"] == 1.5).sum()) if not frame.empty else 0,
                            "outcome_only_hits": int((frame["model_points"] == 1.0).sum()) if not frame.empty else 0,
                            "misses": int((frame["model_points"] == 0.0).sum()) if not frame.empty else 0,
                        }
                    )
                    if best is None or avg_model > best[0]:
                        best = (avg_model, devig_method, rho, ci_cutoff, odds_weight, ratings_weight, frame)

    calibration = pd.DataFrame(calibration_rows).sort_values("avg_model_points", ascending=False)
    calibration_path = out_dir / "football_data_calibration.csv"
    calibration.to_csv(calibration_path, index=False)

    assert best is not None
    _, best_devig_method, best_rho, best_ci, best_odds_weight, best_ratings_weight, best_frame = best
    details_path = out_dir / "football_data_backtest_results.csv"
    best_frame.drop(columns=["distribution"], errors="ignore").to_csv(details_path, index=False)
    reliability_path = out_dir / "football_data_reliability_cells.csv"
    reliability_cells(best_frame).to_csv(reliability_path, index=False)

    validation_metrics = _clean_validation_metrics(summarise_validation(best_frame.to_dict(orient="records"))) if not best_frame.empty else {}
    low_score_path = out_dir / "football_data_low_score_calibration.csv"
    favourite_band_path = out_dir / "football_data_favourite_band_calibration.csv"
    baseline_path = out_dir / "football_data_baseline_metrics.json"
    pd.DataFrame(validation_metrics.get("low_score_calibration", [])).to_csv(low_score_path, index=False)
    pd.DataFrame(validation_metrics.get("favourite_band_calibration", [])).to_csv(favourite_band_path, index=False)
    baseline_path.write_text(json.dumps(validation_metrics.get("baseline_metrics", {}), indent=2), encoding="utf-8")

    summary = summarize_backtest(best_frame, best_devig_method, best_rho, best_ci, best_odds_weight, best_ratings_weight)
    summary.update(
        {
            "mode": "football_data_1x2_odds_backtest",
            "workbook": str(workbook_path),
            "years": years,
            "odds_market": "H-Avg/D-Avg/A-Avg",
            "totals_market": "not_available",
            "validation_timing_note": VALIDATION_TIMING_NOTE,
            "validation_metrics": validation_metrics,
            "fixtures": str(fixtures_path),
            "odds_json": str(odds_path),
            "calibration_results": str(calibration_path),
            "backtest_results": str(details_path),
            "reliability_cells": str(reliability_path),
            "low_score_calibration": str(low_score_path),
            "favourite_band_calibration": str(favourite_band_path),
            "baseline_metrics": str(baseline_path),
        }
    )
    summary_path = out_dir / "football_data_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def ensure_workbook(workbook: str | None, download_url: str) -> Path:
    if workbook:
        path = Path(workbook)
        if path.exists():
            return path
    path = Path("work/WorldCup2026.xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(download_url, path)
    return path


def load_football_data_matches(workbook_path: str | Path, years: list[int]) -> list[dict[str, Any]]:
    selected_years = set(years)
    matches: list[dict[str, Any]] = []
    for sheet_name, year in SHEET_YEARS.items():
        if year not in selected_years:
            continue
        frame = pd.read_excel(workbook_path, sheet_name=sheet_name).fillna("")
        for _, row in frame.iterrows():
            home_price = _float_value(row.get("H-Avg"))
            draw_price = _float_value(row.get("D-Avg"))
            away_price = _float_value(row.get("A-Avg"))
            home_goals = _int_value(row.get("HGFT"))
            away_goals = _int_value(row.get("AGFT"))
            if None in (home_price, draw_price, away_price, home_goals, away_goals):
                continue
            date = pd.to_datetime(row["Date"], errors="coerce")
            if pd.isna(date):
                continue
            home_team = canonical_team_name(str(row["Home"]))
            away_team = canonical_team_name(str(row["Away"]))
            match_id = f"fd-{year}-{date.date().isoformat()}-{team_key(home_team).replace(' ', '-')}-{team_key(away_team).replace(' ', '-')}"
            matches.append(
                {
                    "match_id": match_id,
                    "year": year,
                    "commence_time": date.date().isoformat(),
                    "home_team": home_team,
                    "away_team": away_team,
                    "home_goals": int(home_goals),
                    "away_goals": int(away_goals),
                    "venue_country": HOST_COUNTRIES.get(year, ""),
                    "neutral": True,
                    "stage": str(row.get("Competition") or f"World Cup {year}"),
                    "finished": str(row.get("Finished") or ""),
                    "h_avg": float(home_price),
                    "d_avg": float(draw_price),
                    "a_avg": float(away_price),
                }
            )
    return sorted(matches, key=lambda item: (item["commence_time"], item["match_id"]))


def football_data_odds_json(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for match in matches:
        events.append(
            {
                "id": match["match_id"],
                "sport_key": "soccer_fifa_world_cup",
                "sport_title": "FIFA World Cup",
                "commence_time": match["commence_time"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "neutral": match["neutral"],
                "venue_country": match["venue_country"],
                "stage": match["stage"],
                "bookmakers": [
                    {
                        "key": "football_data_avg",
                        "title": "Football-Data Market Average",
                        "last_update": match["commence_time"],
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": match["home_team"], "price": match["h_avg"]},
                                    {"name": "Draw", "price": match["d_avg"]},
                                    {"name": match["away_team"], "price": match["a_avg"]},
                                ],
                            }
                        ],
                    }
                ],
            }
        )
    return events


def football_data_fixtures(matches: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "match_id": match["match_id"],
                "commence_time": match["commence_time"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
                "home_goals": match["home_goals"],
                "away_goals": match["away_goals"],
                "neutral": match["neutral"],
                "venue_country": match["venue_country"],
                "stage": match["stage"],
                "year": match["year"],
            }
            for match in matches
        ]
    )


def evaluate_football_data_matches(matches: list[dict[str, Any]], config: AppConfig) -> pd.DataFrame:
    ratings = RatingsStore(config=config.ratings)
    model = OddsToScorelineModel(config.model, ratings)
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals, config.public_pick, config.sensitivity)
    rows: list[dict[str, Any]] = []
    distributions: dict[str, DistributionResult] = {}

    for raw_match in matches:
        match = _match_odds(raw_match)
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        actual_home = int(raw_match["home_goals"])
        actual_away = int(raw_match["away_goals"])
        actual_result = outcome_label(actual_home, actual_away)
        model_probs = (
            float(distribution.diagnostics.get("model_home_win", 0.0)),
            float(distribution.diagnostics.get("model_draw", 0.0)),
            float(distribution.diagnostics.get("model_away_win", 0.0)),
        )
        naive_home, naive_away = naive_baseline_pick(match, distribution)
        baselines = baseline_picks(match, distribution, naive_home, naive_away)
        row = {
            "match_id": match.match_id,
            "year": raw_match["year"],
            "commence_time": match.commence_time,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "actual_scoreline": f"{actual_home}-{actual_away}",
            "actual_result": actual_result,
            "model_scoreline": prediction.recommended.scoreline,
            "model_expected_points": prediction.recommended.expected_points,
            "model_home_win": model_probs[0],
            "model_draw": model_probs[1],
            "model_away_win": model_probs[2],
            "brier_1x2": brier_score_1x2(model_probs, actual_result),
            "log_loss_1x2": log_loss_1x2(model_probs, actual_result),
            "rps_1x2": ranked_probability_score(model_probs, actual_result),
            "exact_log_loss": exact_score_log_loss(distribution.matrix, actual_home, actual_away),
            "total_goals_crps": total_goals_crps(distribution.matrix, actual_home + actual_away),
            "p_exact": prediction.recommended.p_exact,
            "p_close": prediction.recommended.p_close,
            "p_outcome": prediction.recommended.p_outcome,
            "lambda_home": distribution.lambda_home,
            "lambda_away": distribution.lambda_away,
            "fair_home_win": distribution.diagnostics.get("fair_home_win"),
            "fair_draw": distribution.diagnostics.get("fair_draw"),
            "fair_away_win": distribution.diagnostics.get("fair_away_win"),
            "home_goals": actual_home,
            "away_goals": actual_away,
            "distribution": match.match_id,
        }
        row.update(_score_pick("model", prediction.recommended.home_goals, prediction.recommended.away_goals, actual_home, actual_away, config.superbru.ci_cutoff))
        for name, (pred_home, pred_away) in baselines.items():
            row.update(_score_pick(name, pred_home, pred_away, actual_home, actual_away, config.superbru.ci_cutoff))
            row[f"{name}_scoreline"] = f"{pred_home}-{pred_away}"
        row.update(_low_score_probabilities(distribution))
        rows.append(row)
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
                    venue_country=match.venue_country,
                )
            ]
        )

    frame = pd.DataFrame(rows)
    frame.attrs["distributions"] = distributions
    return frame


def summarize_backtest(
    frame: pd.DataFrame,
    best_devig_method: str,
    best_rho: float,
    best_ci: float,
    best_odds_weight: float,
    best_ratings_weight: float,
) -> dict[str, Any]:
    if frame.empty:
        return {
            "best_devig_method": best_devig_method,
            "best_dixon_coles_rho": best_rho,
            "best_ci_cutoff": best_ci,
            "best_odds_weight": best_odds_weight,
            "best_ratings_weight": best_ratings_weight,
            "matches": 0,
            "avg_model_points": 0.0,
            "avg_naive_points": 0.0,
            "edge_vs_naive": 0.0,
        }
    return {
        "best_devig_method": best_devig_method,
        "best_dixon_coles_rho": best_rho,
        "best_ci_cutoff": best_ci,
        "best_odds_weight": best_odds_weight,
        "best_ratings_weight": best_ratings_weight,
        "matches": int(len(frame)),
        "avg_model_points": float(frame["model_points"].mean()),
        "avg_naive_points": float(frame["naive_points"].mean()),
        "edge_vs_naive": float(frame["model_points"].mean() - frame["naive_points"].mean()),
        "exact_hits": int((frame["model_points"] == 3.0).sum()),
        "close_hits": int((frame["model_points"] == 1.5).sum()),
        "outcome_only_hits": int((frame["model_points"] == 1.0).sum()),
        "misses": int((frame["model_points"] == 0.0).sum()),
        "outcome_accuracy": float((frame["model_points"] > 0.0).mean()),
        "exact_rate": float((frame["model_points"] == 3.0).mean()),
    }


def _match_odds(raw_match: dict[str, Any]) -> MatchOdds:
    market = MarketOdds(
        key="h2h",
        bookmaker="Football-Data Market Average",
        outcomes=(
            OutcomeOdds(name=raw_match["home_team"], price=float(raw_match["h_avg"])),
            OutcomeOdds(name="Draw", price=float(raw_match["d_avg"])),
            OutcomeOdds(name=raw_match["away_team"], price=float(raw_match["a_avg"])),
        ),
        last_update=str(raw_match["commence_time"]),
    )
    return MatchOdds(
        match_id=str(raw_match["match_id"]),
        commence_time=str(raw_match["commence_time"]),
        home_team=str(raw_match["home_team"]),
        away_team=str(raw_match["away_team"]),
        markets={"h2h": (market,)},
        neutral=bool(raw_match["neutral"]),
        venue_country=str(raw_match["venue_country"] or "") or None,
        stage=str(raw_match["stage"] or ""),
        raw=dict(raw_match),
    )


def _float_value(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 1.0:
        return None
    return parsed


def _int_value(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _weight_grid(odds_raw: str, ratings_raw: str, config: AppConfig) -> list[tuple[float, float]]:
    odds_values = parse_float_grid(odds_raw)
    ratings_values = parse_float_grid(ratings_raw)
    if odds_values and not ratings_values:
        return [(odds, max(0.0, 1.0 - odds)) for odds in odds_values]
    if odds_values and ratings_values:
        return [(odds, ratings) for odds in odds_values for ratings in ratings_values if odds + ratings > 0]
    if ratings_values:
        return [(max(0.0, 1.0 - ratings), ratings) for ratings in ratings_values]
    return [(config.model.odds_weight, config.model.ratings_weight)]
