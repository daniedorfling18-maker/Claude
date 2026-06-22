from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd


def _txt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _flt(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        text = str(value).strip()
        if not text or text.lower() == "nan":
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _score_tuple(scoreline: Any) -> tuple[int, int] | None:
    text = _txt(scoreline)
    if "-" not in text:
        return None

    left, right = text.split("-", 1)
    if not left.strip().isdigit() or not right.strip().isdigit():
        return None

    return int(left.strip()), int(right.strip())


def _outcome(scoreline: Any) -> str:
    score = _score_tuple(scoreline)
    if score is None:
        return ""

    if score[0] > score[1]:
        return "home"
    if score[0] < score[1]:
        return "away"
    return "draw"


def _add_weight(weights: dict[str, float], scoreline: Any, weight: float) -> None:
    scoreline = _txt(scoreline)
    if _score_tuple(scoreline) is None:
        return

    weights[scoreline] = weights.get(scoreline, 0.0) + weight


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, value) for value in weights.values())
    if total <= 0:
        return {}

    return {key: max(0.0, value) / total for key, value in weights.items()}


def _chalk_scores(recommended_scoreline: str) -> list[str]:
    result = _outcome(recommended_scoreline)

    if result == "home":
        return ["2-0", "2-1", "1-0", "3-0", "3-1"]

    if result == "away":
        return ["0-2", "1-2", "0-1", "0-3", "1-3"]

    return ["1-1", "0-0", "2-2"]


def build_chaser_distribution(row: dict[str, Any]) -> dict[str, float]:
    """
    Estimate what nearby chasers are likely to pick.

    The distribution intentionally differs from the model probability surface.
    Chasers are assumed to overweight top listed scorelines, obvious favourites,
    the modal pick, and the private chase suggestion.
    """
    risk = _txt(row.get("risk_tier")).lower()
    weights: dict[str, float] = {}

    _add_weight(weights, row.get("top1_scoreline"), 0.24)
    _add_weight(weights, row.get("recommended_scoreline"), 0.18)
    _add_weight(weights, row.get("private_chase_scoreline"), 0.16)
    _add_weight(weights, row.get("top2_scoreline"), 0.14)
    _add_weight(weights, row.get("modal_scoreline"), 0.12)
    _add_weight(weights, row.get("top3_scoreline"), 0.10)

    if risk == "high":
        _add_weight(weights, row.get("private_chase_scoreline"), 0.12)
        _add_weight(weights, row.get("top2_scoreline"), 0.06)
        _add_weight(weights, row.get("top3_scoreline"), 0.06)

    if risk == "low":
        _add_weight(weights, row.get("recommended_scoreline"), 0.08)
        _add_weight(weights, row.get("top1_scoreline"), 0.06)

    for scoreline in _chalk_scores(_txt(row.get("recommended_scoreline"))):
        _add_weight(weights, scoreline, 0.03)

    return _normalise(weights)


def build_leader_candidates(row: dict[str, Any]) -> list[str]:
    candidates: list[str] = []

    for field in [
        "recommended_scoreline",
        "top1_scoreline",
        "top2_scoreline",
        "top3_scoreline",
        "modal_scoreline",
        "private_chase_scoreline",
    ]:
        scoreline = _txt(row.get(field))
        if _score_tuple(scoreline) is not None and scoreline not in candidates:
            candidates.append(scoreline)

    for scoreline in _chalk_scores(_txt(row.get("recommended_scoreline"))):
        if scoreline not in candidates:
            candidates.append(scoreline)

    return candidates


def estimate_candidate_ev(row: dict[str, Any], candidate: str) -> float:
    """
    Estimate EV for non-recommended candidates from existing diagnostics.

    The prediction engine already emits the recommended EV, second-place gap,
    private chase EV loss, and top scorelines. This overlay uses those fields
    rather than re-running the full scoreline probability surface.
    """
    recommended = _txt(row.get("recommended_scoreline"))
    private_chase = _txt(row.get("private_chase_scoreline"))
    top1 = _txt(row.get("top1_scoreline"))
    top2 = _txt(row.get("top2_scoreline"))
    top3 = _txt(row.get("top3_scoreline"))
    modal = _txt(row.get("modal_scoreline"))

    base_ev = _flt(row.get("expected_points"))
    ev_gap = _flt(row.get("ev_gap_to_second"))
    chase_loss = _flt(row.get("private_chase_ev_loss"))

    if candidate == recommended:
        return base_ev

    if candidate == private_chase:
        return max(0.0, base_ev - chase_loss)

    if candidate == top1:
        return max(0.0, base_ev - min(ev_gap, 0.03))

    if candidate == top2:
        return max(0.0, base_ev - max(ev_gap, 0.02))

    if candidate == top3:
        return max(0.0, base_ev - max(ev_gap + 0.03, 0.05))

    if candidate == modal:
        return max(0.0, base_ev - 0.06)

    return max(0.0, base_ev - 0.12)


def risk_penalty(row: dict[str, Any]) -> float:
    risk = _txt(row.get("risk_tier")).lower()
    confidence = _txt(row.get("confidence_tier")).lower()
    stability = _flt(row.get("sensitivity_stability"), 1.0)

    risk_cost = {
        "low": 0.00,
        "medium": 0.03,
        "high": 0.08,
    }.get(risk, 0.04)

    confidence_cost = {
        "strong": 0.00,
        "medium": 0.02,
        "fragile": 0.07,
    }.get(confidence, 0.03)

    stability_cost = max(0.0, 1.0 - stability) * 0.08

    return risk_cost + confidence_cost + stability_cost


def evaluate_match(row: dict[str, Any], leader_gap: float, chasers: int) -> dict[str, Any]:
    chaser_distribution = build_chaser_distribution(row)
    candidates = build_leader_candidates(row)

    recommended = _txt(row.get("recommended_scoreline"))
    raw_ev = _flt(row.get("expected_points"))

    crowd_pick = ""
    if chaser_distribution:
        crowd_pick = max(chaser_distribution.items(), key=lambda item: item[1])[0]

    best: dict[str, Any] | None = None

    for candidate in candidates:
        candidate_ev = estimate_candidate_ev(row, candidate)

        exact_block = chaser_distribution.get(candidate, 0.0)
        candidate_outcome = _outcome(candidate)

        outcome_block = sum(
            weight
            for scoreline, weight in chaser_distribution.items()
            if _outcome(scoreline) == candidate_outcome
        )

        risk_base = {
            "low": 0.10,
            "medium": 0.18,
            "high": 0.28,
        }.get(_txt(row.get("risk_tier")).lower(), 0.18)

        confidence_mult = {
            "strong": 0.80,
            "medium": 1.00,
            "fragile": 1.30,
        }.get(_txt(row.get("confidence_tier")).lower(), 1.00)

        estimated_chaser_gain = max(0.0, 1.0 - exact_block) * risk_base * confidence_mult

        gap_buffer = max(0.0, leader_gap)
        breach_pressure = max(0.0, estimated_chaser_gain - (gap_buffer * 0.08))
        any_chaser_pressure = 1.0 - ((1.0 - min(0.95, breach_pressure)) ** max(1, chasers))

        defensive_score = (
            candidate_ev
            + 0.24 * exact_block
            + 0.10 * outcome_block
            - 0.55 * estimated_chaser_gain
            - 0.35 * any_chaser_pressure
            - risk_penalty(row)
        )

        result = {
            "leader_defensive_scoreline": candidate,
            "leader_expected_points": round(candidate_ev, 3),
            "raw_ev_recommended": round(raw_ev, 3),
            "ev_cost_vs_recommended": round(max(0.0, raw_ev - candidate_ev), 3),
            "crowd_pick_estimate": crowd_pick,
            "exact_block_value": round(exact_block, 3),
            "outcome_block_value": round(outcome_block, 3),
            "estimated_chaser_gain_pressure": round(estimated_chaser_gain, 3),
            "p_any_chaser_pressure": round(any_chaser_pressure, 3),
            "defensive_score": round(defensive_score, 3),
        }

        if best is None or result["defensive_score"] > best["defensive_score"]:
            best = result

    if best is None:
        best = {
            "leader_defensive_scoreline": recommended,
            "leader_expected_points": round(raw_ev, 3),
            "raw_ev_recommended": round(raw_ev, 3),
            "ev_cost_vs_recommended": 0.0,
            "crowd_pick_estimate": crowd_pick,
            "exact_block_value": 0.0,
            "outcome_block_value": 0.0,
            "estimated_chaser_gain_pressure": 0.0,
            "p_any_chaser_pressure": 0.0,
            "defensive_score": 0.0,
        }

    defensive_pick = best["leader_defensive_scoreline"]
    if defensive_pick == recommended:
        reason = (
            f"Keep engine pick. It protects EV and blocks the likely chaser cluster around "
            f"{best['crowd_pick_estimate']}."
        )
    else:
        reason = (
            f"Switch from {recommended} to {defensive_pick}. Estimated EV cost is "
            f"{best['ev_cost_vs_recommended']:.3f}, but defensive block value improves."
        )

    return {
        "commence_time": _txt(row.get("commence_time")),
        "home_team": _txt(row.get("home_team")),
        "away_team": _txt(row.get("away_team")),
        "model_recommended_scoreline": recommended,
        "confidence_tier": _txt(row.get("confidence_tier")),
        "risk_tier": _txt(row.get("risk_tier")),
        **best,
        "defensive_reason": reason,
    }


def should_exclude(row: dict[str, Any], exclude_matches: list[str]) -> bool:
    home = _txt(row.get("home_team")).lower()
    away = _txt(row.get("away_team")).lower()
    pair_pipe = f"{home}|{away}"
    pair_vs = f"{home} vs {away}"

    for item in exclude_matches:
        item = item.strip().lower()
        if not item:
            continue
        if item in pair_pipe or item in pair_vs:
            return True

    return False


def run_defensive_model(
    predictions_csv: str | Path,
    out_dir: str | Path = "outputs/defensive",
    leader_gap: float = 0.0,
    chasers: int = 5,
    exclude_matches: list[str] | None = None,
) -> pd.DataFrame:
    predictions_csv = Path(predictions_csv)
    out_dir = Path(out_dir)

    if not predictions_csv.exists():
        raise FileNotFoundError(f"Predictions CSV not found: {predictions_csv}")

    df = pd.read_csv(predictions_csv)
    rows = df.to_dict(orient="records")

    output_rows = []
    for row in rows:
        if should_exclude(row, exclude_matches or []):
            continue
        output_rows.append(evaluate_match(row, leader_gap=leader_gap, chasers=chasers))

    output = pd.DataFrame(output_rows)

    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "defensive_picks.csv"
    json_path = out_dir / "defensive_picks.json"
    summary_path = out_dir / "defensive_summary.json"

    output.to_csv(csv_path, index=False)
    json_path.write_text(output.to_json(orient="records", indent=2), encoding="utf-8")

    summary = {
        "matches": int(len(output)),
        "leader_gap": leader_gap,
        "chasers": chasers,
        "total_leader_expected_points": round(float(output["leader_expected_points"].sum()), 3) if len(output) else 0.0,
        "total_raw_recommended_expected_points": round(float(output["raw_ev_recommended"].sum()), 3) if len(output) else 0.0,
        "total_ev_cost": round(float(output["ev_cost_vs_recommended"].sum()), 3) if len(output) else 0.0,
        "csv_path": str(csv_path),
        "json_path": str(json_path),
        "summary_path": str(summary_path),
    }

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return output
