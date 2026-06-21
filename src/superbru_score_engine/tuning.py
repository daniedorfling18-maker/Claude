from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from superbru_score_engine.backtest.metrics import summarise_validation
from superbru_score_engine.backtest.runner import (
    VALIDATION_TIMING_NOTE,
    _clean_validation_metrics,
    evaluate_backtest,
)
from superbru_score_engine.backtest.utils import parse_float_grid, parse_str_grid
from superbru_score_engine.config import AppConfig


DEFAULT_RHO_GRID = "-0.12,-0.10,-0.08,-0.06,-0.04"
DEFAULT_CI_GRID = "1.0,1.25,1.5,1.75,2.0"
DEFAULT_ODDS_WEIGHT_GRID = "0.90,0.95,1.00"
DEFAULT_STRATEGY_MODE_GRID = "raw_ev"
DEFAULT_RISK_AVERSION_GRID = "0.0"
DEFAULT_VARIANCE_PENALTY_GRID = "0.0"


def run_tune(args, config: AppConfig) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rho_grid = parse_float_grid(args.rho_grid or DEFAULT_RHO_GRID)
    ci_grid = parse_float_grid(args.ci_grid or DEFAULT_CI_GRID)
    odds_weight_grid = parse_float_grid(args.odds_weight_grid or DEFAULT_ODDS_WEIGHT_GRID)
    ratings_weight_grid = _optional_float_grid(args.ratings_weight_grid)
    strategy_modes = parse_str_grid(args.strategy_mode_grid or DEFAULT_STRATEGY_MODE_GRID, lowercase=True)
    risk_aversion_grid = parse_float_grid(args.risk_aversion_grid or DEFAULT_RISK_AVERSION_GRID)
    variance_penalty_grid = parse_float_grid(args.variance_penalty_grid or DEFAULT_VARIANCE_PENALTY_GRID)

    rows: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None
    best_frame: pd.DataFrame | None = None

    for rho in rho_grid:
        for ci_cutoff in ci_grid:
            for odds_weight in odds_weight_grid:
                rating_weights = ratings_weight_grid or [max(0.0, 1.0 - odds_weight)]
                for ratings_weight in rating_weights:
                    for strategy_mode in strategy_modes:
                        for risk_aversion in risk_aversion_grid:
                            for variance_penalty in variance_penalty_grid:
                                trial_config = replace(
                                    config,
                                    model=replace(
                                        config.model,
                                        dixon_coles_rho=rho,
                                        odds_weight=odds_weight,
                                        ratings_weight=ratings_weight,
                                    ),
                                    superbru=replace(
                                        config.superbru,
                                        ci_cutoff=ci_cutoff,
                                        strategy_mode=strategy_mode,
                                        risk_aversion=risk_aversion,
                                        variance_penalty=variance_penalty,
                                    ),
                                )
                                frame = evaluate_backtest(args.fixtures, args.odds_json, trial_config)
                                metrics = _clean_validation_metrics(summarise_validation(frame.to_dict(orient="records"))) if not frame.empty else {}
                                row = _row_from_metrics(
                                    rho=rho,
                                    ci_cutoff=ci_cutoff,
                                    odds_weight=odds_weight,
                                    ratings_weight=ratings_weight,
                                    strategy_mode=strategy_mode,
                                    risk_aversion=risk_aversion,
                                    variance_penalty=variance_penalty,
                                    frame=frame,
                                    metrics=metrics,
                                )
                                rows.append(row)
                                if _is_better(row, best):
                                    best = row
                                    best_frame = frame

    results = pd.DataFrame(rows)
    if results.empty:
        summary = {
            "status": "no_trials_run",
            "message": "No tuning trials were run. Check grid arguments.",
        }
        _write_json(out_dir / "tuning_summary.json", summary)
        print(json.dumps(summary, indent=2))
        return 1

    sort_columns = ["avg_model_points", "edge_vs_naive", "brier_score_1x2", "log_loss_1x2"]
    ascending = [False, False, True, True]
    results = results.sort_values(sort_columns, ascending=ascending, na_position="last")
    results_path = out_dir / "tuning_results.csv"
    results.to_csv(results_path, index=False)

    best_frame_path = out_dir / "best_backtest_results.csv"
    if best_frame is not None:
        best_frame.to_csv(best_frame_path, index=False)

    assert best is not None
    summary = {
        "status": "ok" if int(best.get("matches", 0)) > 0 else "no_completed_matches",
        "validation_timing_note": VALIDATION_TIMING_NOTE,
        "selection_rule": "Highest average model points, then edge vs naive, then lower Brier/log-loss.",
        "trials": int(len(results)),
        "matches": int(best.get("matches", 0)),
        "best_parameters": {
            "model.dixon_coles_rho": best["dixon_coles_rho"],
            "model.odds_weight": best["odds_weight"],
            "model.ratings_weight": best["ratings_weight"],
            "superbru.ci_cutoff": best["ci_cutoff"],
            "superbru.strategy_mode": best["strategy_mode"],
            "superbru.risk_aversion": best["risk_aversion"],
            "superbru.variance_penalty": best["variance_penalty"],
        },
        "best_metrics": {
            "avg_model_points": best.get("avg_model_points"),
            "avg_naive_points": best.get("avg_naive_points"),
            "edge_vs_naive": best.get("edge_vs_naive"),
            "exact_score_hit_rate": best.get("exact_score_hit_rate"),
            "right_result_accuracy": best.get("right_result_accuracy"),
            "close_score_rate": best.get("close_score_rate"),
            "brier_score_1x2": best.get("brier_score_1x2"),
            "log_loss_1x2": best.get("log_loss_1x2"),
        },
        "recommended_config_patch": _recommended_config_patch(best),
        "outputs": {
            "tuning_results": str(results_path),
            "best_backtest_results": str(best_frame_path),
        },
    }
    if summary["status"] == "no_completed_matches":
        summary["message"] = (
            "Tuning ran but found no completed fixtures with home_goals and away_goals. "
            "Fill actual results into the fixtures CSV before using tuning output."
        )
    summary_path = out_dir / "tuning_summary.json"
    _write_json(summary_path, summary)
    print(json.dumps(summary, indent=2))
    return 0 if summary["status"] == "ok" else 1


def _row_from_metrics(
    *,
    rho: float,
    ci_cutoff: float,
    odds_weight: float,
    ratings_weight: float,
    strategy_mode: str,
    risk_aversion: float,
    variance_penalty: float,
    frame: pd.DataFrame,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    avg_model = float(frame["model_points"].mean()) if not frame.empty else 0.0
    avg_naive = float(frame["naive_points"].mean()) if not frame.empty else 0.0
    return {
        "dixon_coles_rho": rho,
        "ci_cutoff": ci_cutoff,
        "odds_weight": odds_weight,
        "ratings_weight": ratings_weight,
        "strategy_mode": strategy_mode,
        "risk_aversion": risk_aversion,
        "variance_penalty": variance_penalty,
        "matches": int(len(frame)),
        "avg_model_points": avg_model,
        "avg_naive_points": avg_naive,
        "edge_vs_naive": avg_model - avg_naive,
        "exact_score_hit_rate": metrics.get("exact_score_hit_rate"),
        "right_result_accuracy": metrics.get("right_result_accuracy"),
        "close_score_rate": metrics.get("close_score_rate"),
        "brier_score_1x2": metrics.get("brier_score_1x2"),
        "log_loss_1x2": metrics.get("log_loss_1x2"),
        "draw_calibration_gap": (metrics.get("draw_calibration") or {}).get("calibration_gap"),
    }


def _is_better(row: dict[str, Any], best: dict[str, Any] | None) -> bool:
    if best is None:
        return True
    row_key = (
        float(row.get("avg_model_points") or 0.0),
        float(row.get("edge_vs_naive") or 0.0),
        -float(row.get("brier_score_1x2") or 999.0),
        -float(row.get("log_loss_1x2") or 999.0),
    )
    best_key = (
        float(best.get("avg_model_points") or 0.0),
        float(best.get("edge_vs_naive") or 0.0),
        -float(best.get("brier_score_1x2") or 999.0),
        -float(best.get("log_loss_1x2") or 999.0),
    )
    return row_key > best_key


def _recommended_config_patch(best: dict[str, Any]) -> str:
    return "\n".join(
        [
            "model:",
            f"  dixon_coles_rho: {best['dixon_coles_rho']}",
            f"  odds_weight: {best['odds_weight']}",
            f"  ratings_weight: {best['ratings_weight']}",
            "superbru:",
            f"  ci_cutoff: {best['ci_cutoff']}",
            f"  strategy_mode: {best['strategy_mode']}",
            f"  risk_aversion: {best['risk_aversion']}",
            f"  variance_penalty: {best['variance_penalty']}",
        ]
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _optional_float_grid(raw: str) -> list[float]:
    return parse_float_grid(raw) if raw else []
