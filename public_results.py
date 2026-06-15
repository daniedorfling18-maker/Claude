from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from urllib.request import urlretrieve

import pandas as pd

from superbru_score_engine.config import AppConfig
from superbru_score_engine.decision import SuperbruDecisionEngine
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.ingest.base import MatchOdds
from superbru_score_engine.model import DistributionResult, OddsToScorelineModel
from superbru_score_engine.model.ratings import MatchResult, RatingsStore
from superbru_score_engine.model.team_names import canonical_team_name, team_key

from .runner import naive_baseline_pick, reliability_cells


PUBLIC_RESULTS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"


def run_public_results_backtest(args: argparse.Namespace, config: AppConfig) -> int:
    source_path = ensure_public_results_csv(args.source_csv, args.download_url)
    years = _int_grid(args.years) or [2014, 2018, 2022]
    tournament = str(args.tournament)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = load_public_results(source_path)
    world_cup = filter_results(rows, tournament=tournament, years=years)
    exported_results_path = out_dir / "public_worldcup_results.csv"
    world_cup.to_csv(exported_results_path, index=False)

    rho_grid = _float_grid(args.rho_grid) or [config.model.dixon_coles_rho]
    ci_grid = _float_grid(args.ci_grid) or [config.superbru.ci_cutoff]
    calibration_rows: list[dict] = []
    best: tuple[float, float, float, pd.DataFrame] | None = None
    for rho in rho_grid:
        for ci_cutoff in ci_grid:
            trial_config = replace(
                config,
                model=replace(config.model, dixon_coles_rho=rho),
                superbru=replace(config.superbru, ci_cutoff=ci_cutoff),
            )
            frame = evaluate_ratings_only_backtest(rows, tournament, years, trial_config)
            avg_model = float(frame["model_points"].mean()) if not frame.empty else 0.0
            avg_naive = float(frame["naive_points"].mean()) if not frame.empty else 0.0
            calibration_rows.append(
                {
                    "dixon_coles_rho": rho,
                    "ci_cutoff": ci_cutoff,
                    "matches": int(len(frame)),
                    "avg_model_points": avg_model,
                    "avg_naive_points": avg_naive,
                    "edge_vs_naive": avg_model - avg_naive,
                    "exact_hits": int((frame["model_points"] == 3.0).sum()) if not frame.empty else 0,
                    "close_hits": int((frame["model_points"] == 1.5).sum()) if not frame.empty else 0,
                    "outcome_only_hits": int((frame["model_points"] == 1.0).sum()) if not frame.empty else 0,
                    "misses": int((frame["model_points"] == 0.0).sum()) if not frame.empty else 0,
                }
            )
            if best is None or avg_model > best[0]:
                best = (avg_model, rho, ci_cutoff, frame)

    calibration = pd.DataFrame(calibration_rows).sort_values("avg_model_points", ascending=False)
    calibration_path = out_dir / "public_ratings_calibration.csv"
    calibration.to_csv(calibration_path, index=False)

    assert best is not None
    _, best_rho, best_ci, best_frame = best
    details_path = out_dir / "public_ratings_backtest_results.csv"
    best_frame.drop(columns=["distribution"], errors="ignore").to_csv(details_path, index=False)
    reliability_path = out_dir / "public_ratings_reliability_cells.csv"
    reliability_cells(best_frame).to_csv(reliability_path, index=False)

    summary = summarize_public_backtest(best_frame, best_rho, best_ci)
    summary.update(
        {
            "mode": "ratings_only_results_backtest",
            "source_csv": str(source_path),
            "tournament": tournament,
            "years": years,
            "exported_results": str(exported_results_path),
            "calibration_results": str(calibration_path),
            "backtest_results": str(details_path),
            "reliability_cells": str(reliability_path),
        }
    )
    summary_path = out_dir / "public_ratings_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def ensure_public_results_csv(source_csv: str | None, download_url: str) -> Path:
    if source_csv:
        path = Path(source_csv)
        if path.exists():
            return path
    path = Path("work/public_international_results.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(download_url, path)
    return path


def load_public_results(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path).fillna("")
    required = {"date", "home_team", "away_team", "home_score", "away_score", "tournament", "country", "neutral"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Public results CSV is missing required columns: {', '.join(sorted(missing))}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True)
    frame = frame.dropna(subset=["date"])
    frame["home_score"] = pd.to_numeric(frame["home_score"], errors="coerce")
    frame["away_score"] = pd.to_numeric(frame["away_score"], errors="coerce")
    frame = frame.dropna(subset=["home_score", "away_score"])
    frame["year"] = frame["date"].dt.year
    frame["home_team"] = frame["home_team"].map(canonical_team_name)
    frame["away_team"] = frame["away_team"].map(canonical_team_name)
    frame["home_goals"] = frame["home_score"].astype(int)
    frame["away_goals"] = frame["away_score"].astype(int)
    frame["is_neutral"] = frame["neutral"].map(_bool_value)
    frame["match_id"] = frame.apply(_public_match_id, axis=1)
    return frame.sort_values(["date", "match_id"]).reset_index(drop=True)


def filter_results(frame: pd.DataFrame, tournament: str, years: Iterable[int]) -> pd.DataFrame:
    year_set = set(int(year) for year in years)
    mask = (frame["tournament"] == tournament) & frame["year"].isin(year_set)
    columns = [
        "match_id",
        "date",
        "home_team",
        "away_team",
        "home_goals",
        "away_goals",
        "tournament",
        "country",
        "is_neutral",
        "year",
    ]
    return frame.loc[mask, columns].copy()


def evaluate_ratings_only_backtest(frame: pd.DataFrame, tournament: str, years: Iterable[int], config: AppConfig) -> pd.DataFrame:
    ratings = RatingsStore()
    model = OddsToScorelineModel(config.model, ratings)
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals)
    target_years = set(int(year) for year in years)
    rows: list[dict] = []
    distributions: dict[str, DistributionResult] = {}

    for _, row in frame.iterrows():
        result = _row_to_result(row)
        is_target = row["tournament"] == tournament and int(row["year"]) in target_years
        if is_target:
            match = _row_to_match(row)
            distribution = model.build_distribution(match)
            prediction = decision.predict(distribution)
            naive_home, naive_away = naive_baseline_pick(match, distribution)
            model_points = score_actual_prediction(
                prediction.recommended.home_goals,
                prediction.recommended.away_goals,
                result.home_goals,
                result.away_goals,
                config.superbru.ci_cutoff,
            )
            rows.append(
                {
                    "match_id": match.match_id,
                    "date": row["date"].date().isoformat(),
                    "year": int(row["year"]),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "actual_scoreline": f"{result.home_goals}-{result.away_goals}",
                    "model_scoreline": prediction.recommended.scoreline,
                    "model_expected_points": prediction.recommended.expected_points,
                    "model_points": model_points,
                    "naive_scoreline": f"{naive_home}-{naive_away}",
                    "naive_points": score_actual_prediction(
                        naive_home,
                        naive_away,
                        result.home_goals,
                        result.away_goals,
                        config.superbru.ci_cutoff,
                    ),
                    "p_exact": prediction.recommended.p_exact,
                    "p_close": prediction.recommended.p_close,
                    "p_outcome": prediction.recommended.p_outcome,
                    "lambda_home": distribution.lambda_home,
                    "lambda_away": distribution.lambda_away,
                    "home_goals": result.home_goals,
                    "away_goals": result.away_goals,
                    "distribution": match.match_id,
                }
            )
            distributions[match.match_id] = distribution
        ratings.update_results([result])

    result_frame = pd.DataFrame(rows)
    result_frame.attrs["distributions"] = distributions
    return result_frame


def summarize_public_backtest(frame: pd.DataFrame, best_rho: float, best_ci: float) -> dict:
    if frame.empty:
        return {
            "best_dixon_coles_rho": best_rho,
            "best_ci_cutoff": best_ci,
            "matches": 0,
            "avg_model_points": 0.0,
            "avg_naive_points": 0.0,
            "edge_vs_naive": 0.0,
        }
    return {
        "best_dixon_coles_rho": best_rho,
        "best_ci_cutoff": best_ci,
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


def _row_to_result(row) -> MatchResult:
    return MatchResult(
        match_id=str(row["match_id"]),
        commence_time=row["date"].date().isoformat(),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        home_goals=int(row["home_goals"]),
        away_goals=int(row["away_goals"]),
        venue_country=str(row.get("country") or "") or None,
    )


def _row_to_match(row) -> MatchOdds:
    return MatchOdds(
        match_id=str(row["match_id"]),
        commence_time=row["date"].date().isoformat(),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        markets={},
        neutral=bool(row["is_neutral"]),
        venue_country=str(row.get("country") or "") or None,
        stage=str(row.get("tournament") or ""),
        raw=row.to_dict(),
    )


def _public_match_id(row) -> str:
    date = row["date"].date().isoformat()
    home = team_key(row["home_team"]).replace(" ", "-")
    away = team_key(row["away_team"]).replace(" ", "-")
    return f"public-{date}-{home}-{away}"


def _bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _float_grid(raw: str) -> list[float]:
    if not raw:
        return []
    return [float(item.strip()) for item in raw.split(",") if item.strip()]


def _int_grid(raw: str) -> list[int]:
    if not raw:
        return []
    return [int(item.strip()) for item in raw.split(",") if item.strip()]
