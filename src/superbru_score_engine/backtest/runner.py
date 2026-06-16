from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from superbru_score_engine.backtest.metrics import (
    brier_score_1x2,
    log_loss_1x2,
    outcome_label,
    right_result_hit,
    scoreline_hit,
    summarise_validation,
)
from superbru_score_engine.config import AppConfig
from superbru_score_engine.decision import SuperbruDecisionEngine
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.ingest.offline import OfflineJsonProvider
from superbru_score_engine.model import DistributionResult, OddsToScorelineModel
from superbru_score_engine.model.devig import extract_fair_1x2
from superbru_score_engine.model.ratings import RatingsStore


VALIDATION_TIMING_NOTE = (
    "This validation uses the supplied saved or historical odds snapshots. "
    "If those snapshots are closing odds or have unknown timing, it is not necessarily equivalent to early pre-match Superbru prediction."
)


def run_backtest(args: argparse.Namespace, config: AppConfig) -> int:
    rho_grid = _float_grid(args.rho_grid) or [config.model.dixon_coles_rho]
    ci_grid = _float_grid(args.ci_grid) or [config.superbru.ci_cutoff]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    calibration_rows: list[dict] = []
    best: tuple[float, float, float, pd.DataFrame] | None = None
    for rho in rho_grid:
        for ci_cutoff in ci_grid:
            trial_config = replace(
                config,
                model=replace(config.model, dixon_coles_rho=rho),
                superbru=replace(config.superbru, ci_cutoff=ci_cutoff),
            )
            frame = evaluate_backtest(args.fixtures, args.odds_json, trial_config)
            validation = _clean_validation_metrics(summarise_validation(frame.to_dict(orient="records"))) if not frame.empty else {}
            avg_model = float(frame["model_points"].mean()) if not frame.empty else 0.0
            avg_naive = float(frame["naive_points"].mean()) if not frame.empty else 0.0
            calibration_rows.append(
                {
                    "dixon_coles_rho": rho,
                    "ci_cutoff": ci_cutoff,
                    "matches": len(frame),
                    "avg_model_points": avg_model,
                    "avg_naive_points": avg_naive,
                    "edge_vs_naive": avg_model - avg_naive,
                    "exact_score_hit_rate": validation.get("exact_score_hit_rate"),
                    "right_result_accuracy": validation.get("right_result_accuracy"),
                    "brier_score_1x2": validation.get("brier_score_1x2"),
                    "log_loss_1x2": validation.get("log_loss_1x2"),
                }
            )
            if best is None or avg_model > best[0]:
                best = (avg_model, rho, ci_cutoff, frame)

    calibration = pd.DataFrame(calibration_rows).sort_values("avg_model_points", ascending=False)
    calibration_path = out_dir / "calibration_results.csv"
    calibration.to_csv(calibration_path, index=False)

    assert best is not None
    _, best_rho, best_ci, best_frame = best
    details_path = out_dir / "backtest_results.csv"
    best_frame.to_csv(details_path, index=False)
    reliability_path = out_dir / "reliability_cells.csv"
    reliability_cells(best_frame).to_csv(reliability_path, index=False)
    _try_plot_reliability(reliability_path, out_dir / "reliability_cells.png")

    validation_metrics = _clean_validation_metrics(summarise_validation(best_frame.to_dict(orient="records"))) if not best_frame.empty else {}
    low_score_path = out_dir / "low_score_calibration.csv"
    favourite_band_path = out_dir / "favourite_band_calibration.csv"
    baseline_path = out_dir / "baseline_metrics.json"
    pd.DataFrame(validation_metrics.get("low_score_calibration", [])).to_csv(low_score_path, index=False)
    pd.DataFrame(validation_metrics.get("favourite_band_calibration", [])).to_csv(favourite_band_path, index=False)
    baseline_path.write_text(json.dumps(validation_metrics.get("baseline_metrics", {}), indent=2), encoding="utf-8")

    summary = {
        "best_dixon_coles_rho": best_rho,
        "best_ci_cutoff": best_ci,
        "matches": int(len(best_frame)),
        "avg_model_points": float(best_frame["model_points"].mean()) if not best_frame.empty else None,
        "avg_naive_points": float(best_frame["naive_points"].mean()) if not best_frame.empty else None,
        "edge_vs_naive": float(best_frame["model_points"].mean() - best_frame["naive_points"].mean()) if not best_frame.empty else None,
        "validation_timing_note": VALIDATION_TIMING_NOTE,
        "validation_metrics": validation_metrics,
        "calibration_results": str(calibration_path),
        "backtest_results": str(details_path),
        "reliability_cells": str(reliability_path),
        "low_score_calibration": str(low_score_path),
        "favourite_band_calibration": str(favourite_band_path),
        "baseline_metrics": str(baseline_path),
    }
    summary_path = out_dir / "backtest_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    return 0


def evaluate_backtest(fixtures_path: str | Path, odds_json: str | Path, config: AppConfig) -> pd.DataFrame:
    fixtures = pd.read_csv(fixtures_path).fillna("")
    actuals = {
        str(row["match_id"]): (int(row["home_goals"]), int(row["away_goals"]))
        for _, row in fixtures.iterrows()
        if str(row.get("home_goals", "")) != "" and str(row.get("away_goals", "")) != ""
    }
    matches = OfflineJsonProvider(odds_json).fetch_odds()
    match_meta = fixtures.set_index("match_id").to_dict(orient="index") if "match_id" in fixtures.columns else {}

    ratings = RatingsStore(config=config.ratings)
    model = OddsToScorelineModel(config.model, ratings)
    decision = SuperbruDecisionEngine(config.superbru, config.model.candidate_grid_goals, config.public_pick, config.sensitivity)
    rows: list[dict] = []
    distributions: dict[str, DistributionResult] = {}

    for match in matches:
        if match.match_id not in actuals:
            continue
        distribution = model.build_distribution(match)
        prediction = decision.predict(distribution)
        distributions[match.match_id] = distribution
        actual_home, actual_away = actuals[match.match_id]
        actual_result = outcome_label(actual_home, actual_away)
        actual_scoreline = f"{actual_home}-{actual_away}"
        model_probs = (
            float(distribution.diagnostics.get("model_home_win", 0.0)),
            float(distribution.diagnostics.get("model_draw", 0.0)),
            float(distribution.diagnostics.get("model_away_win", 0.0)),
        )
        naive_home, naive_away = naive_baseline_pick(match, distribution)
        baselines = baseline_picks(match, distribution, naive_home, naive_away)
        row = {
            "match_id": match.match_id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "actual_scoreline": actual_scoreline,
            "actual_result": actual_result,
            "model_scoreline": prediction.recommended.scoreline,
            "model_expected_points": prediction.recommended.expected_points,
            "model_home_win": model_probs[0],
            "model_draw": model_probs[1],
            "model_away_win": model_probs[2],
            "brier_1x2": brier_score_1x2(model_probs, actual_result),
            "log_loss_1x2": log_loss_1x2(model_probs, actual_result),
            "p_exact": prediction.recommended.p_exact,
            "p_close": prediction.recommended.p_close,
            "p_outcome": prediction.recommended.p_outcome,
            "distribution": distribution,
            "home_goals": actual_home,
            "away_goals": actual_away,
            "stage": match_meta.get(match.match_id, {}).get("stage", ""),
        }
        row.update(_score_pick("model", prediction.recommended.home_goals, prediction.recommended.away_goals, actual_home, actual_away, config.superbru.ci_cutoff))
        for name, (pred_home, pred_away) in baselines.items():
            row.update(_score_pick(name, pred_home, pred_away, actual_home, actual_away, config.superbru.ci_cutoff))
            row[f"{name}_scoreline"] = f"{pred_home}-{pred_away}"
        row.update(_low_score_probabilities(distribution))
        rows.append(row)

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame.attrs["distributions"] = distributions
        frame["distribution"] = frame["match_id"]
    return frame


def baseline_picks(match, distribution: DistributionResult, naive_home: int, naive_away: int) -> dict[str, tuple[int, int]]:
    favourite = _favourite_index(match, distribution)
    modal_home, modal_away = (int(value) for value in np.unravel_index(np.argmax(distribution.matrix), distribution.matrix.shape))
    return {
        "naive": (naive_home, naive_away),
        "favourite_1_0": _favourite_scoreline(favourite, 1),
        "favourite_2_0": _favourite_scoreline(favourite, 2),
        "draw_1_1": (1, 1),
        "modal_exact": (modal_home, modal_away),
    }


def naive_baseline_pick(match, distribution: DistributionResult) -> tuple[int, int]:
    favourite = _favourite_index(match, distribution)
    high_total = (distribution.lambda_home + distribution.lambda_away) >= 2.65
    if favourite == 0:
        return (2, 1) if high_total else (1, 0)
    if favourite == 2:
        return (1, 2) if high_total else (0, 1)
    return (1, 1) if high_total else (0, 0)


def _favourite_index(match, distribution: DistributionResult) -> int:
    fair = extract_fair_1x2(match)
    if fair is None:
        probs = distribution.diagnostics
        values = [float(probs["model_home_win"]), float(probs["model_draw"]), float(probs["model_away_win"])]
    else:
        values = [fair.home, fair.draw, fair.away]
    return max(range(3), key=lambda idx: values[idx])


def _favourite_scoreline(favourite: int, goals: int) -> tuple[int, int]:
    if favourite == 0:
        return goals, 0
    if favourite == 2:
        return 0, goals
    return 1, 1


def _score_pick(prefix: str, pred_home: int, pred_away: int, actual_home: int, actual_away: int, ci_cutoff: float) -> dict[str, object]:
    points = score_actual_prediction(pred_home, pred_away, actual_home, actual_away, ci_cutoff)
    return {
        f"{prefix}_points": points,
        f"{prefix}_exact_hit": float(scoreline_hit(actual_home, actual_away, pred_home, pred_away)),
        f"{prefix}_right_result_hit": float(right_result_hit(actual_home, actual_away, pred_home, pred_away)),
        f"{prefix}_close_hit": float(points == 1.5),
    }


def _low_score_probabilities(distribution: DistributionResult) -> dict[str, float]:
    matrix = distribution.matrix
    rows: dict[str, float] = {}
    for home, away in ((0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (1, 2)):
        rows[f"p_score_{home}_{away}"] = float(matrix[home, away]) if home < matrix.shape[0] and away < matrix.shape[1] else 0.0
    return rows


def reliability_cells(frame: pd.DataFrame, bins: int = 10) -> pd.DataFrame:
    distributions: dict[str, DistributionResult] = frame.attrs.get("distributions", {})
    rows: list[dict] = []
    for _, row in frame.iterrows():
        distribution = distributions.get(row["match_id"])
        if distribution is None:
            continue
        matrix = distribution.matrix
        actual = (int(row["home_goals"]), int(row["away_goals"]))
        for home_goals in range(matrix.shape[0]):
            for away_goals in range(matrix.shape[1]):
                rows.append(
                    {
                        "match_id": row["match_id"],
                        "scoreline": f"{home_goals}-{away_goals}",
                        "predicted_probability": float(matrix[home_goals, away_goals]),
                        "observed": 1 if (home_goals, away_goals) == actual else 0,
                    }
                )
    if not rows:
        return pd.DataFrame(columns=["bin", "count", "mean_predicted_probability", "observed_frequency"])
    cells = pd.DataFrame(rows)
    cells["bin"] = pd.cut(cells["predicted_probability"], bins=bins, duplicates="drop")
    return (
        cells.groupby("bin", observed=True)
        .agg(
            count=("observed", "size"),
            mean_predicted_probability=("predicted_probability", "mean"),
            observed_frequency=("observed", "mean"),
        )
        .reset_index()
        .assign(bin=lambda df: df["bin"].astype(str))
    )


def _clean_validation_metrics(metrics: dict) -> dict:
    baseline_metrics = dict(metrics.get("baseline_metrics") or {})
    baseline_metrics.pop("model_expected", None)
    cleaned = dict(metrics)
    cleaned["baseline_metrics"] = baseline_metrics
    return cleaned


def _try_plot_reliability(csv_path: Path, png_path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    frame = pd.read_csv(csv_path)
    if frame.empty:
        return
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.plot(frame["mean_predicted_probability"], frame["observed_frequency"], marker="o", label="scoreline cells")
    max_axis = max(frame["mean_predicted_probability"].max(), frame["observed_frequency"].max(), 0.01)
    ax.plot([0, max_axis], [0, max_axis], linestyle="--", color="0.4", label="perfect calibration")
    ax.set_xlabel("Mean predicted probability")
    ax.set_ylabel("Observed frequency")
    ax.legend()
    fig.tight_layout()
    fig.savefig(png_path, dpi=160)
    plt.close(fig)


def _float_grid(raw: str) -> list[float]:
    if not raw:
        return []
    return [float(item.strip()) for item in raw.split(",") if item.strip()]
