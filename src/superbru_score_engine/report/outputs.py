from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import pandas as pd

from superbru_score_engine.betting import BettingSuggestion
from superbru_score_engine.decision import Prediction


def prediction_rows(predictions: Iterable[Prediction]) -> list[dict]:
    rows: list[dict] = []
    for prediction in predictions:
        rec = prediction.recommended
        diagnostics = prediction.diagnostics
        row = {
            "match_id": prediction.match_id,
            "commence_time": prediction.commence_time,
            "home_team": prediction.home_team,
            "away_team": prediction.away_team,
            "recommended_scoreline": rec.scoreline,
            "expected_points": rec.expected_points,
            "adjusted_expected_points": rec.adjusted_expected_points,
            "ev_gap_to_second": diagnostics.get("ev_gap_to_second", _ev_gap(prediction)),
            "p_exact": rec.p_exact,
            "p_close": rec.p_close,
            "p_close_non_exact": rec.p_close_non_exact,
            "p_outcome": rec.p_outcome,
            "p_miss": max(0.0, 1.0 - rec.p_outcome),
            "p_zero_points": rec.p_zero_points,
            "variance_points": rec.variance_points,
            "confidence_tier": _confidence_tier(prediction),
            "risk_tier": _risk_tier(rec.p_outcome),
            "calibration_profile": diagnostics.get("calibration_profile"),
            "distribution_source": diagnostics.get("distribution_source"),
            "lambda_home": diagnostics.get("lambda_home"),
            "lambda_away": diagnostics.get("lambda_away"),
            "total_goals_mean": diagnostics.get("total_goals_mean"),
            "expected_goal_difference": diagnostics.get("expected_goal_difference"),
            "model_home_win": diagnostics.get("model_home_win"),
            "model_draw": diagnostics.get("model_draw"),
            "model_away_win": diagnostics.get("model_away_win"),
            "fair_home_win": diagnostics.get("fair_home_win"),
            "fair_draw": diagnostics.get("fair_draw"),
            "fair_away_win": diagnostics.get("fair_away_win"),
            "fair_total_line": diagnostics.get("fair_total_line"),
            "fair_over": diagnostics.get("fair_over"),
            "model_over": diagnostics.get("model_over"),
            "modal_scoreline": diagnostics.get("modal_scoreline"),
            "modal_scoreline_probability": diagnostics.get("modal_scoreline_probability"),
            "modal_scoreline_expected_points": diagnostics.get("modal_scoreline_expected_points"),
            "ev_gap_recommended_to_modal": diagnostics.get("ev_gap_recommended_to_modal"),
            "probability_mass_inside_candidate_grid": diagnostics.get("probability_mass_inside_candidate_grid"),
            "probability_mass_outside_candidate_grid": diagnostics.get("probability_mass_outside_candidate_grid"),
            "solver_loss": diagnostics.get("solver_loss"),
            "solver_success": diagnostics.get("solver_success"),
            "strategy_mode": prediction.strategy_mode,
            "raw_ev_scoreline": prediction.raw_ev_pick.scoreline,
            "conservative_scoreline": prediction.conservative_pick.scoreline,
            "exact_chase_scoreline": prediction.exact_chase_pick.scoreline,
            "contrarian_scoreline": prediction.contrarian_pick.scoreline,
            "risk_adjusted_scoreline": prediction.risk_adjusted_pick.scoreline,
            "private_chase_scoreline": prediction.private_chase_pick.scoreline,
            "private_chase_expected_points": prediction.private_chase_pick.expected_points,
            "private_chase_ev_loss": diagnostics.get("private_chase_ev_loss"),
            "private_chase_p_exact": prediction.private_chase_pick.p_exact,
            "private_chase_p_close": prediction.private_chase_pick.p_close,
            "private_chase_p_outcome": prediction.private_chase_pick.p_outcome,
            "private_chase_public_pick_share": diagnostics.get("private_chase_public_pick_share"),
            "recommended_public_pick_share": diagnostics.get("recommended_public_pick_share"),
            "recommended_ev_vs_field": diagnostics.get("recommended_ev_vs_field"),
            "recommended_risk_adjusted_score": diagnostics.get("recommended_risk_adjusted_score"),
            "sensitivity_enabled": diagnostics.get("sensitivity_enabled"),
            "sensitivity_scenario_count": diagnostics.get("sensitivity_scenario_count"),
            "sensitivity_changed_count": diagnostics.get("sensitivity_changed_count"),
            "sensitivity_stability": diagnostics.get("sensitivity_stability"),
            "sensitivity_warning": diagnostics.get("sensitivity_warning"),
            "sensitivity_most_common_alternative": diagnostics.get("sensitivity_most_common_alternative"),
            "ratings_source": diagnostics.get("ratings_source"),
            "ratings_updated_at": diagnostics.get("ratings_updated_at"),
            "ratings_number_of_applied_results": diagnostics.get("ratings_number_of_applied_results"),
            "ratings_use_as_fallback_only": diagnostics.get("ratings_use_as_fallback_only"),
            "ratings_weight_effective": diagnostics.get("ratings_weight_effective"),
        }
        for idx, candidate in enumerate(prediction.top_candidates, start=1):
            row[f"top{idx}_scoreline"] = candidate.scoreline
            row[f"top{idx}_ev"] = candidate.expected_points
            row[f"top{idx}_p_exact"] = candidate.p_exact
            row[f"top{idx}_p_close"] = candidate.p_close
            row[f"top{idx}_p_outcome"] = candidate.p_outcome
        rows.append(row)
    return rows


def write_outputs(predictions: list[Prediction], out_dir: str | Path) -> tuple[Path, Path]:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = prediction_rows(predictions)
    csv_path = output_dir / "predictions.csv"
    json_path = output_dir / "predictions.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    payload = [_prediction_payload(prediction) for prediction in predictions]
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return csv_path, json_path


def betting_rows(suggestions: Iterable[BettingSuggestion]) -> list[dict]:
    rows: list[dict] = []
    for suggestion in suggestions:
        rows.append(
            {
                "match_id": suggestion.match_id,
                "commence_time": suggestion.commence_time,
                "home_team": suggestion.home_team,
                "away_team": suggestion.away_team,
                "market": suggestion.market,
                "selection": suggestion.selection,
                "bookmaker": suggestion.bookmaker,
                "decimal_odds": suggestion.decimal_odds,
                "model_probability": suggestion.model_probability,
                "expected_return": suggestion.expected_return,
                "break_even_odds": suggestion.break_even_odds,
                "kelly_fraction": suggestion.kelly_fraction,
            }
        )
    return rows


def write_betting_outputs(suggestions: list[BettingSuggestion], out_dir: str | Path) -> tuple[Path, Path]:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = betting_rows(suggestions)
    csv_path = output_dir / "betting_suggestions.csv"
    json_path = output_dir / "betting_suggestions.json"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return csv_path, json_path


def betting_console_table(suggestions: list[BettingSuggestion], limit: int = 20) -> str:
    rows = betting_rows(suggestions[:limit])
    if not rows:
        return "No betting suggestions passed the configured edge threshold."
    table = pd.DataFrame(rows)[
        [
            "commence_time",
            "home_team",
            "away_team",
            "market",
            "selection",
            "bookmaker",
            "decimal_odds",
            "model_probability",
            "expected_return",
            "kelly_fraction",
        ]
    ]
    for col in ["decimal_odds", "model_probability", "expected_return", "kelly_fraction"]:
        table[col] = table[col].map(lambda value: f"{float(value):.3f}")
    return table.to_string(index=False)


def console_table(predictions: list[Prediction]) -> str:
    rows = prediction_rows(predictions)
    if not rows:
        return "No predictions."
    table = pd.DataFrame(rows)[
        [
            "commence_time",
            "home_team",
            "away_team",
            "recommended_scoreline",
            "expected_points",
            "p_exact",
            "p_close",
            "p_outcome",
            "ev_gap_to_second",
            "sensitivity_stability",
            "modal_scoreline",
            "confidence_tier",
            "risk_tier",
            "private_chase_scoreline",
            "private_chase_ev_loss",
            "top1_scoreline",
            "top2_scoreline",
            "top3_scoreline",
        ]
    ]
    numeric_cols = [
        "expected_points",
        "p_exact",
        "p_close",
        "p_outcome",
        "ev_gap_to_second",
        "sensitivity_stability",
        "private_chase_ev_loss",
    ]
    for col in numeric_cols:
        table[col] = table[col].map(lambda value: "" if pd.isna(value) else f"{float(value):.3f}")
    return table.to_string(index=False)


def _prediction_payload(prediction: Prediction) -> dict:
    return {
        "match_id": prediction.match_id,
        "commence_time": prediction.commence_time,
        "home_team": prediction.home_team,
        "away_team": prediction.away_team,
        "strategy_mode": prediction.strategy_mode,
        "recommended": asdict(prediction.recommended),
        "picks": {
            "raw_ev": asdict(prediction.raw_ev_pick),
            "modal_score": asdict(prediction.modal_score_pick),
            "conservative": asdict(prediction.conservative_pick),
            "exact_chase": asdict(prediction.exact_chase_pick),
            "contrarian": asdict(prediction.contrarian_pick),
            "risk_adjusted": asdict(prediction.risk_adjusted_pick),
            "private_chase": asdict(prediction.private_chase_pick),
        },
        "top_candidates": [asdict(candidate) for candidate in prediction.top_candidates],
        "diagnostics": prediction.diagnostics,
        "public_pick_note": "public_pick_share is a SYNTHETIC estimate, not real Superbru pool data",
    }


def _ev_gap(prediction: Prediction) -> float:
    if len(prediction.top_candidates) < 2:
        return 0.0
    return max(0.0, prediction.top_candidates[0].expected_points - prediction.top_candidates[1].expected_points)


def _confidence_tier(prediction: Prediction) -> str:
    rec = prediction.recommended
    gap = _ev_gap(prediction)
    if rec.p_outcome >= 0.65 and gap >= 0.025:
        return "strong"
    if rec.p_outcome >= 0.55 or gap >= 0.015:
        return "medium"
    return "fragile"


def _risk_tier(p_outcome: float) -> str:
    if p_outcome >= 0.65:
        return "low"
    if p_outcome >= 0.55:
        return "medium"
    return "high"
