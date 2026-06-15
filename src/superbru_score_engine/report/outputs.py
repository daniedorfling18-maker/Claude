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
        row = {
            "match_id": prediction.match_id,
            "commence_time": prediction.commence_time,
            "home_team": prediction.home_team,
            "away_team": prediction.away_team,
            "recommended_scoreline": rec.scoreline,
            "expected_points": rec.expected_points,
            "adjusted_expected_points": rec.adjusted_expected_points,
            "ev_gap_to_second": _ev_gap(prediction),
            "p_exact": rec.p_exact,
            "p_close": rec.p_close,
            "p_close_non_exact": rec.p_close_non_exact,
            "p_outcome": rec.p_outcome,
            "p_miss": max(0.0, 1.0 - rec.p_outcome),
            "confidence_tier": _confidence_tier(prediction),
            "risk_tier": _risk_tier(rec.p_outcome),
            "lambda_home": prediction.diagnostics.get("lambda_home"),
            "lambda_away": prediction.diagnostics.get("lambda_away"),
            "model_home_win": prediction.diagnostics.get("model_home_win"),
            "model_draw": prediction.diagnostics.get("model_draw"),
            "model_away_win": prediction.diagnostics.get("model_away_win"),
            "fair_home_win": prediction.diagnostics.get("fair_home_win"),
            "fair_draw": prediction.diagnostics.get("fair_draw"),
            "fair_away_win": prediction.diagnostics.get("fair_away_win"),
            "fair_total_line": prediction.diagnostics.get("fair_total_line"),
            "fair_over": prediction.diagnostics.get("fair_over"),
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
            "confidence_tier",
            "risk_tier",
            "top1_scoreline",
            "top2_scoreline",
            "top3_scoreline",
        ]
    ]
    numeric_cols = ["expected_points", "p_exact", "p_close", "p_outcome", "ev_gap_to_second"]
    for col in numeric_cols:
        table[col] = table[col].map(lambda value: f"{float(value):.3f}")
    return table.to_string(index=False)


def _prediction_payload(prediction: Prediction) -> dict:
    return {
        "match_id": prediction.match_id,
        "commence_time": prediction.commence_time,
        "home_team": prediction.home_team,
        "away_team": prediction.away_team,
        "recommended": asdict(prediction.recommended),
        "top_candidates": [asdict(candidate) for candidate in prediction.top_candidates],
        "diagnostics": prediction.diagnostics,
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
