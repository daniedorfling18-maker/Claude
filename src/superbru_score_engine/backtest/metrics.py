from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Mapping, Sequence


RESULT_ORDER = ("home", "draw", "away")
LOW_SCORELINES = ("0-0", "1-0", "0-1", "1-1", "2-1", "1-2")
FAVOURITE_BANDS = (0.0, 0.5, 0.6, 0.7, 0.8, 1.01)


def outcome_label(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def result_vector(home_goals: int, away_goals: int) -> tuple[float, float, float]:
    actual = outcome_label(home_goals, away_goals)
    return tuple(1.0 if label == actual else 0.0 for label in RESULT_ORDER)


def brier_score_1x2(model_probs: Sequence[float], actual_result: str | tuple[int, int]) -> float:
    actual = _actual_label(actual_result)
    observed = [1.0 if label == actual else 0.0 for label in RESULT_ORDER]
    probs = _normalise_probs(model_probs)
    return float(sum((prob - obs) ** 2 for prob, obs in zip(probs, observed)))


def log_loss_1x2(model_probs: Sequence[float], actual_result: str | tuple[int, int], epsilon: float = 1e-12) -> float:
    actual = _actual_label(actual_result)
    probs = _normalise_probs(model_probs)
    actual_idx = RESULT_ORDER.index(actual)
    return float(-math.log(max(epsilon, min(1.0, probs[actual_idx]))))


def scoreline_hit(actual_home: int, actual_away: int, pred_home: int, pred_away: int) -> bool:
    return actual_home == pred_home and actual_away == pred_away


def right_result_hit(actual_home: int, actual_away: int, pred_home: int, pred_away: int) -> bool:
    return outcome_label(actual_home, actual_away) == outcome_label(pred_home, pred_away)


def probability_band(value: float, bins: Sequence[float] = FAVOURITE_BANDS) -> str:
    value = float(value)
    for lower, upper in zip(bins, bins[1:]):
        if lower <= value < upper:
            return f"[{lower:.1f},{upper:.1f})"
    return f">={bins[-2]:.1f}"


def low_score_calibration(records: Iterable[Mapping[str, object]], scorelines: Sequence[str] = LOW_SCORELINES) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    records_list = list(records)
    for scoreline in scorelines:
        predicted_values: list[float] = []
        observed_values: list[float] = []
        prob_key = f"p_score_{scoreline.replace('-', '_')}"
        for record in records_list:
            predicted = record.get(prob_key)
            actual = record.get("actual_scoreline")
            if predicted is None or actual is None:
                continue
            predicted_values.append(float(predicted))
            observed_values.append(1.0 if str(actual) == scoreline else 0.0)
        rows.append(
            {
                "scoreline": scoreline,
                "count": len(predicted_values),
                "mean_predicted_probability": _mean(predicted_values),
                "observed_frequency": _mean(observed_values),
                "calibration_gap": _safe_subtract(_mean(observed_values), _mean(predicted_values)),
            }
        )
    return rows


def favourite_band_calibration(records: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    buckets: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for record in records:
        probs = _record_probs(record)
        if probs is None:
            continue
        favourite_idx = max(range(3), key=lambda idx: probs[idx])
        favourite_prob = probs[favourite_idx]
        actual = str(record.get("actual_result", ""))
        observed = 1.0 if actual == RESULT_ORDER[favourite_idx] else 0.0
        buckets[probability_band(favourite_prob)].append((favourite_prob, observed))
    return [
        {
            "band": band,
            "count": len(values),
            "mean_favourite_probability": _mean([prob for prob, _ in values]),
            "observed_favourite_win_rate": _mean([obs for _, obs in values]),
            "calibration_gap": _safe_subtract(_mean([obs for _, obs in values]), _mean([prob for prob, _ in values])),
        }
        for band, values in sorted(buckets.items())
    ]


def summarise_validation(records: Iterable[Mapping[str, object]]) -> dict[str, object]:
    records_list = list(records)
    model_points = _values(records_list, "model_points")
    naive_points = _values(records_list, "naive_points")
    expected_points = _values(records_list, "model_expected_points")
    actual_points = _values(records_list, "model_points")
    briers = _values(records_list, "brier_1x2")
    log_losses = _values(records_list, "log_loss_1x2")
    draw_probs = _values(records_list, "model_draw")
    actual_draws = [1.0 if str(record.get("actual_result")) == "draw" else 0.0 for record in records_list if record.get("actual_result") is not None]

    baselines = _baseline_summaries(records_list)
    return {
        "matches_scored": len(records_list),
        "average_model_points": _mean(model_points),
        "average_naive_points": _mean(naive_points),
        "model_edge_vs_naive": _safe_subtract(_mean(model_points), _mean(naive_points)),
        "exact_score_hit_rate": _mean(_values(records_list, "model_exact_hit")),
        "right_result_accuracy": _mean(_values(records_list, "model_right_result_hit")),
        "close_score_rate": _mean(_values(records_list, "model_close_hit")),
        "average_expected_points": _mean(expected_points),
        "average_actual_points": _mean(actual_points),
        "expected_vs_actual_points_gap": _safe_subtract(_mean(expected_points), _mean(actual_points)),
        "brier_score_1x2": _mean(briers),
        "log_loss_1x2": _mean(log_losses),
        "draw_calibration": {
            "mean_predicted_draw_probability": _mean(draw_probs),
            "observed_draw_frequency": _mean(actual_draws),
            "calibration_gap": _safe_subtract(_mean(actual_draws), _mean(draw_probs)),
        },
        "favourite_band_calibration": favourite_band_calibration(records_list),
        "low_score_calibration": low_score_calibration(records_list),
        "baseline_metrics": baselines,
    }


def _baseline_summaries(records: list[Mapping[str, object]]) -> dict[str, dict[str, object]]:
    baseline_names = sorted(
        {
            key[: -len("_points")]
            for record in records
            for key in record.keys()
            if str(key).endswith("_points") and key not in {"model_points"}
        }
    )
    baseline_names = ["model"] + baseline_names
    summaries: dict[str, dict[str, object]] = {}
    for name in baseline_names:
        summaries[name] = {
            "average_points": _mean(_values(records, f"{name}_points")),
            "exact_hit_rate": _mean(_values(records, f"{name}_exact_hit")),
            "right_result_accuracy": _mean(_values(records, f"{name}_right_result_hit")),
            "close_score_rate": _mean(_values(records, f"{name}_close_hit")),
        }
    return summaries


def _record_probs(record: Mapping[str, object]) -> tuple[float, float, float] | None:
    keys = ("model_home_win", "model_draw", "model_away_win")
    if any(record.get(key) is None for key in keys):
        return None
    return _normalise_probs([float(record[key]) for key in keys])


def _actual_label(actual_result: str | tuple[int, int]) -> str:
    if isinstance(actual_result, tuple):
        return outcome_label(int(actual_result[0]), int(actual_result[1]))
    actual = str(actual_result)
    if actual not in RESULT_ORDER:
        raise ValueError(f"Unknown actual result label: {actual_result!r}")
    return actual


def _normalise_probs(model_probs: Sequence[float]) -> tuple[float, float, float]:
    if len(model_probs) != 3:
        raise ValueError("Expected three 1X2 probabilities")
    probs = [max(0.0, float(prob)) for prob in model_probs]
    total = sum(probs)
    if total <= 0:
        return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    return tuple(prob / total for prob in probs)  # type: ignore[return-value]


def _values(records: Iterable[Mapping[str, object]], key: str) -> list[float]:
    values: list[float] = []
    for record in records:
        value = record.get(key)
        if value is None or value == "":
            continue
        values.append(float(value))
    return values


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _safe_subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left - right)
