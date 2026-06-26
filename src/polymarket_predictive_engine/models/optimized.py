"""Leakage-safe deployable Polymarket prediction model.

This module turns the existing "does the model beat the market?" research idea
into a paper-trading artifact with a strict deployment contract:

* features are market-anchored but do not reuse current executable prices as
  model inputs;
* tuning is walk-forward and market-disjoint;
* the latest markets are reserved as an untouched final holdout;
* the JSON artifact is deployed as ``champion`` only when it beats the market
  on that holdout. Otherwise it remains ``shadow`` for forward telemetry.
"""
from __future__ import annotations

import math
import random
from typing import Any

import numpy as np

from ..config import EngineConfig, load_config
from ..utils import git_commit_hash, now_utc, safe_float, write_csv, write_json
from .calibration_v2 import joined_feature_label_rows, numeric_model_feature_columns

MODEL_VERSION = "pm-optimized-market-logit-v1"
FEATURE_SET_VERSION = "pm-point-in-time-v2"
ARTIFACT_NAME = "optimized_model_v1.json"

MARKET_PROB_FIELDS = ("implied_probability", "midpoint")

# The market probability is deliberately added once as ``market_logit``.  These
# aliases either duplicate that anchor or represent the execution price used for
# the edge decision, so they are not allowed to become independent ML features.
EXCLUDED_EXACT_FEATURES = {
    "implied_probability",
    "midpoint",
    "logit_midpoint",
    "predicted_probability",
    "raw_probability",
    "calibrated_probability",
    "model_probability",
    "market_probability",
    "market_midpoint",
    "executable_buy_price",
    "executable_price",
    "best_bid",
    "best_ask",
    "last_trade_price",
    "price",
    "price_change_price",
    "target",
}


def _clamp_probability(value: float) -> float:
    return max(1e-6, min(1 - 1e-6, float(value)))


def _market_probability(row: dict[str, Any]) -> float | None:
    for field in MARKET_PROB_FIELDS:
        value = safe_float(row.get(field))
        if value is not None and 0 <= value <= 1:
            return _clamp_probability(value)
    return None


def _logit(probability: float) -> float:
    p = _clamp_probability(probability)
    return math.log(p / (1 - p))


def _sigmoid(value: float) -> float:
    value = max(-40.0, min(40.0, value))
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _market_key(row: dict[str, Any]) -> str:
    return str(row.get("market_id") or row.get("condition_id") or row.get("market_slug") or "")


def select_optimized_feature_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Return numeric, leakage-screened feature columns.

    The market price itself is represented only by the explicit market-logit
    anchor. Microstructure features such as spread/liquidity remain eligible,
    but executable/current price aliases are excluded.
    """
    selected: list[str] = []
    for column in numeric_model_feature_columns(rows):
        lower = column.lower()
        if lower in EXCLUDED_EXACT_FEATURES:
            continue
        if lower.endswith("_target") or lower.endswith("_label"):
            continue
        selected.append(column)
    return selected


def _design_rows(rows: list[dict[str, Any]], feature_columns: list[str]) -> list[dict[str, Any]]:
    design: list[dict[str, Any]] = []
    for row in rows:
        market_probability = _market_probability(row)
        market = _market_key(row)
        target = safe_float(row.get("target"))
        if market_probability is None or target is None or not market:
            continue
        features: list[float] = []
        for column in feature_columns:
            value = safe_float(row.get(column))
            features.append(0.0 if value is None else value)
        design.append(
            {
                **row,
                "_market": market,
                "_market_p": market_probability,
                "_x": [1.0, _logit(market_probability), *features],
                "_y": int(target),
            }
        )
    return design


def _thin_by_token(design: list[dict[str, Any]], max_per_token: int) -> list[dict[str, Any]]:
    if max_per_token <= 0:
        return design
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in design:
        by_key.setdefault((str(row.get("_market")), str(row.get("token_id", ""))), []).append(row)
    out: list[dict[str, Any]] = []
    for rows in by_key.values():
        rows.sort(key=lambda row: str(row.get("prediction_timestamp", "")))
        if len(rows) <= max_per_token:
            out.extend(rows)
            continue
        indices = sorted(set(np.linspace(0, len(rows) - 1, max_per_token).round().astype(int).tolist()))
        out.extend(rows[i] for i in indices)
    return out


def _ordered_markets(design: list[dict[str, Any]]) -> list[str]:
    first_seen: dict[str, str] = {}
    for row in design:
        market = str(row["_market"])
        timestamp = str(row.get("prediction_timestamp", ""))
        if market not in first_seen or timestamp < first_seen[market]:
            first_seen[market] = timestamp
    return [market for market, _ in sorted(first_seen.items(), key=lambda item: (item[1], item[0]))]


def _rows_for_markets(design: list[dict[str, Any]], markets: set[str]) -> list[dict[str, Any]]:
    return [row for row in design if str(row["_market"]) in markets]


def _standardize(matrix: np.ndarray) -> tuple[np.ndarray, list[float], list[float]]:
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    mean[0] = 0.0
    std[0] = 1.0
    std = np.where((~np.isfinite(std)) | (std < 1e-12), 1.0, std)
    out = (matrix - mean) / std
    out[:, 0] = 1.0
    return out, mean.astype(float).tolist(), std.astype(float).tolist()


def _fit_logistic(matrix: np.ndarray, target: np.ndarray, l2: float, iters: int = 100) -> np.ndarray:
    n_rows, n_cols = matrix.shape
    weights = np.zeros(n_cols)
    reg = np.eye(n_cols) * float(l2)
    reg[0, 0] = 0.0
    for _ in range(iters):
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(matrix @ weights, -40, 40)))
        diag = np.clip(probabilities * (1 - probabilities), 1e-7, None)
        gradient = matrix.T @ (probabilities - target) + reg @ weights
        hessian = (matrix.T * diag) @ matrix + reg
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(hessian, gradient, rcond=None)[0]
        weights = weights - step
        if np.max(np.abs(step)) < 1e-8:
            break
    return weights


def _fit_model(rows: list[dict[str, Any]], feature_columns: list[str], l2: float) -> dict[str, Any]:
    if len({int(row["_y"]) for row in rows}) < 2:
        raise ValueError("training rows contain only one target class")
    matrix = np.array([row["_x"] for row in rows], dtype=float)
    target = np.array([row["_y"] for row in rows], dtype=float)
    standardised, mean, std = _standardize(matrix)
    weights = _fit_logistic(standardised, target, l2=l2)
    return {
        "feature_columns": feature_columns,
        "weight_names": ["intercept", "market_logit", *feature_columns],
        "weights": weights.astype(float).tolist(),
        "standardization_mean": mean,
        "standardization_std": std,
        "l2": float(l2),
    }


def _predict_design_rows(rows: list[dict[str, Any]], fitted: dict[str, Any]) -> list[float]:
    if not rows:
        return []
    matrix = np.array([row["_x"] for row in rows], dtype=float)
    mean = np.array(fitted["standardization_mean"], dtype=float)
    std = np.array(fitted["standardization_std"], dtype=float)
    weights = np.array(fitted["weights"], dtype=float)
    standardised = (matrix - mean) / std
    standardised[:, 0] = 1.0
    return [float(_clamp_probability(p)) for p in (1.0 / (1.0 + np.exp(-np.clip(standardised @ weights, -40, 40)))).tolist()]


def _brier(probabilities: list[float], target: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probabilities, target)) / len(target) if target else float("nan")


def _log_loss(probabilities: list[float], target: list[int]) -> float:
    if not target:
        return float("nan")
    total = 0.0
    for p, y in zip(probabilities, target):
        p = _clamp_probability(p)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(target)


def _score(rows: list[dict[str, Any]], model_probabilities: list[float]) -> dict[str, Any]:
    target = [int(row["_y"]) for row in rows]
    market_probabilities = [float(row["_market_p"]) for row in rows]
    model_brier = _brier(model_probabilities, target)
    market_brier = _brier(market_probabilities, target)
    model_log_loss = _log_loss(model_probabilities, target)
    market_log_loss = _log_loss(market_probabilities, target)
    brier_gain = market_brier - model_brier
    return {
        "rows": len(rows),
        "markets": len({str(row["_market"]) for row in rows}),
        "base_rate": sum(target) / len(target) if target else None,
        "market_brier": market_brier,
        "model_brier": model_brier,
        "brier_gain_vs_market": brier_gain,
        "brier_skill_vs_market": 1 - model_brier / market_brier if market_brier else None,
        "market_log_loss": market_log_loss,
        "model_log_loss": model_log_loss,
        "log_loss_gain_vs_market": market_log_loss - model_log_loss,
    }


def _bootstrap_gain_ci(rows: list[dict[str, Any]], model_probabilities: list[float], iterations: int, seed: int) -> list[float | None]:
    if not rows:
        return [None, None]
    by_market: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        by_market.setdefault(str(row["_market"]), []).append(i)
    markets = list(by_market)
    if not markets:
        return [None, None]
    target = [int(row["_y"]) for row in rows]
    market_probability = [float(row["_market_p"]) for row in rows]
    diffs = [(market_probability[i] - target[i]) ** 2 - (model_probabilities[i] - target[i]) ** 2 for i in range(len(rows))]
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(max(1, iterations)):
        total = 0.0
        count = 0
        for _ in markets:
            for idx in by_market[markets[rng.randrange(len(markets))]]:
                total += diffs[idx]
                count += 1
        means.append(total / count if count else 0.0)
    means.sort()
    lo = means[int(0.025 * (len(means) - 1))]
    hi = means[int(0.975 * (len(means) - 1))]
    return [lo, hi]


def _walk_forward_splits(markets: list[str], min_train_markets: int, requested_splits: int) -> list[tuple[list[str], list[str]]]:
    if len(markets) <= min_train_markets:
        return []
    remaining = len(markets) - min_train_markets
    validation_size = max(1, math.ceil(remaining / max(1, requested_splits)))
    splits: list[tuple[list[str], list[str]]] = []
    start = min_train_markets
    while start < len(markets) and len(splits) < max(1, requested_splits):
        end = min(len(markets), start + validation_size)
        train_markets = markets[:start]
        validation_markets = markets[start:end]
        if validation_markets:
            splits.append((train_markets, validation_markets))
        start = end
    return splits


def _write_blocked_artifact(
    cfg: EngineConfig,
    *,
    reason: str,
    rows: int = 0,
    markets: int = 0,
    feature_columns: list[str] | None = None,
) -> dict[str, Any]:
    output_dir = cfg.output_root / "polymarket_models"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "insufficient_data",
        "deployment_mode": "blocked",
        "reason": reason,
        "model_type": "market_anchored_logistic",
        "model_version": MODEL_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "joined_rows": rows,
        "markets": markets,
        "feature_columns": feature_columns or [],
        "generated_at_utc": now_utc(),
        "git_commit_hash": git_commit_hash(),
    }
    write_json(output_dir / ARTIFACT_NAME, payload)
    write_json(output_dir / "optimized_model_status.json", payload)
    write_json(cfg.governance_root / "optimized_model_summary.json", payload)
    write_csv(cfg.governance_root / "optimized_model_tuning.csv", [])
    write_csv(cfg.governance_root / "optimized_model_holdout_predictions.csv", [])
    return payload


def train_optimized_model(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("ml_optimization", {})
    train_root = cfg.output_root / "polymarket_training"
    output_dir = cfg.output_root / "polymarket_models"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = joined_feature_label_rows(str(train_root / "features_v2.csv"), str(train_root / "labels.csv"))
    feature_columns = select_optimized_feature_columns(rows) if rows else []
    design = _thin_by_token(
        _design_rows(rows, feature_columns),
        int(settings.get("max_rows_per_token", 12)),
    )
    markets = _ordered_markets(design)
    market_count = len(markets)

    minimum_rows = int(settings.get("minimum_rows", cfg.raw.get("governance_thresholds", {}).get("min_training_rows", 100)))
    minimum_markets = int(settings.get("minimum_markets", cfg.raw.get("governance_thresholds", {}).get("min_validation_markets", 20)))
    min_train_markets = int(settings.get("minimum_train_markets", 5))
    if len(design) < minimum_rows or market_count < minimum_markets:
        return _write_blocked_artifact(
            cfg,
            reason=f"insufficient joined rows/markets: {len(design)} rows, {market_count} markets",
            rows=len(design),
            markets=market_count,
            feature_columns=feature_columns,
        )

    final_holdout_fraction = float(settings.get("final_holdout_fraction", cfg.raw.get("holdout", {}).get("untouched_final_holdout_fraction", 0.20)))
    configured_holdout_markets = int(math.ceil(market_count * final_holdout_fraction))
    min_holdout_markets = int(settings.get("minimum_holdout_markets", 3))
    holdout_market_count = max(min_holdout_markets, configured_holdout_markets)
    holdout_market_count = min(holdout_market_count, market_count - min_train_markets)
    if holdout_market_count <= 0:
        return _write_blocked_artifact(
            cfg,
            reason="not enough markets to keep an untouched final holdout",
            rows=len(design),
            markets=market_count,
            feature_columns=feature_columns,
        )

    development_markets = markets[:-holdout_market_count]
    holdout_markets = markets[-holdout_market_count:]
    development_rows = _rows_for_markets(design, set(development_markets))
    holdout_rows = _rows_for_markets(design, set(holdout_markets))
    if len({int(row["_y"]) for row in development_rows}) < 2:
        return _write_blocked_artifact(
            cfg,
            reason="development rows contain only one target class",
            rows=len(design),
            markets=market_count,
            feature_columns=feature_columns,
        )

    candidate_l2 = [float(v) for v in settings.get("candidate_l2", [0.1, 0.3, 1.0, 3.0, 10.0])]
    requested_splits = int(settings.get("walk_forward_splits", 4))
    folds = _walk_forward_splits(development_markets, min_train_markets, requested_splits)
    if not folds:
        return _write_blocked_artifact(
            cfg,
            reason="not enough development markets for walk-forward tuning",
            rows=len(design),
            markets=market_count,
            feature_columns=feature_columns,
        )

    tuning_rows: list[dict[str, Any]] = []
    candidate_scores: list[dict[str, Any]] = []
    for l2 in candidate_l2:
        gains: list[float] = []
        for fold_number, (train_markets, validation_markets) in enumerate(folds, start=1):
            train_rows = _rows_for_markets(design, set(train_markets))
            validation_rows = _rows_for_markets(design, set(validation_markets))
            base_row = {
                "fold": fold_number,
                "l2": l2,
                "train_markets": len(set(train_markets)),
                "validation_markets": len(set(validation_markets)),
                "train_rows": len(train_rows),
                "validation_rows": len(validation_rows),
            }
            if len({int(row["_y"]) for row in train_rows}) < 2 or not validation_rows:
                tuning_rows.append({**base_row, "status": "skipped_single_class_or_empty"})
                continue
            fitted = _fit_model(train_rows, feature_columns, l2)
            probabilities = _predict_design_rows(validation_rows, fitted)
            score = _score(validation_rows, probabilities)
            gains.append(float(score["brier_gain_vs_market"]))
            tuning_rows.append({**base_row, "status": "ok", **score})
        if gains:
            candidate_scores.append(
                {
                    "l2": l2,
                    "valid_folds": len(gains),
                    "mean_brier_gain_vs_market": sum(gains) / len(gains),
                }
            )
    write_csv(cfg.governance_root / "optimized_model_tuning.csv", tuning_rows)

    if not candidate_scores:
        return _write_blocked_artifact(
            cfg,
            reason="walk-forward tuning produced no valid folds",
            rows=len(design),
            markets=market_count,
            feature_columns=feature_columns,
        )

    best = sorted(candidate_scores, key=lambda row: (row["mean_brier_gain_vs_market"], -row["l2"]), reverse=True)[0]
    selected_l2 = float(best["l2"])
    fitted = _fit_model(development_rows, feature_columns, selected_l2)
    holdout_probabilities = _predict_design_rows(holdout_rows, fitted)
    holdout_score = _score(holdout_rows, holdout_probabilities)
    ci = _bootstrap_gain_ci(
        holdout_rows,
        holdout_probabilities,
        int(settings.get("bootstrap_iterations", 1000)),
        int(settings.get("random_seed", cfg.raw.get("model", {}).get("random_seed", 20260623))),
    )
    holdout_score["brier_gain_ci95"] = ci

    min_holdout_rows = int(settings.get("minimum_holdout_rows", cfg.raw.get("holdout", {}).get("minimum_holdout_rows", 100)))
    min_brier_gain = float(settings.get("min_brier_gain", 0.0))
    ci_floor_raw = settings.get("min_brier_gain_ci_low")
    ci_floor = None if ci_floor_raw in (None, "") else float(ci_floor_raw)
    champion_reasons: list[str] = []
    if int(holdout_score["rows"]) < min_holdout_rows:
        champion_reasons.append(f"holdout rows {holdout_score['rows']} < {min_holdout_rows}")
    if int(holdout_score["markets"]) < min_holdout_markets:
        champion_reasons.append(f"holdout markets {holdout_score['markets']} < {min_holdout_markets}")
    if float(holdout_score["brier_gain_vs_market"]) <= min_brier_gain:
        champion_reasons.append(
            f"holdout brier gain {holdout_score['brier_gain_vs_market']} <= {min_brier_gain}"
        )
    if ci_floor is not None and (ci[0] is None or float(ci[0]) < ci_floor):
        champion_reasons.append(f"holdout brier gain CI low {ci[0]} < {ci_floor}")

    deployment_mode = "champion" if not champion_reasons else "shadow"
    training_cutoff = max((str(row.get("prediction_timestamp", "")) for row in development_rows), default="")

    holdout_prediction_rows = []
    for row, probability in zip(holdout_rows, holdout_probabilities):
        holdout_prediction_rows.append(
            {
                "market_id": row.get("market_id", row.get("condition_id", "")),
                "market_slug": row.get("market_slug", ""),
                "token_id": row.get("token_id", ""),
                "prediction_timestamp": row.get("prediction_timestamp", ""),
                "market_probability": row["_market_p"],
                "optimized_probability": probability,
                "target": row["_y"],
                "brier_gain": (float(row["_market_p"]) - int(row["_y"])) ** 2 - (probability - int(row["_y"])) ** 2,
            }
        )
    write_csv(cfg.governance_root / "optimized_model_holdout_predictions.csv", holdout_prediction_rows)

    artifact = {
        "status": "trained",
        "deployment_mode": deployment_mode,
        "champion_blockers": champion_reasons,
        "model_type": "market_anchored_logistic",
        "model_version": MODEL_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "trained_at": now_utc(),
        "training_cutoff_timestamp": training_cutoff,
        "git_commit_hash": git_commit_hash(),
        "joined_rows": len(design),
        "markets": market_count,
        "development_rows": len(development_rows),
        "development_markets": len(set(development_markets)),
        "holdout_rows": len(holdout_rows),
        "holdout_markets": len(set(holdout_markets)),
        "selected_l2": selected_l2,
        "candidate_scores": candidate_scores,
        "holdout": holdout_score,
        "leakage_policy": {
            "market_anchor": "market_logit from implied_probability/midpoint",
            "excluded_exact_features": sorted(EXCLUDED_EXACT_FEATURES),
            "split": "market-disjoint walk-forward tuning plus latest-market final holdout",
        },
        **fitted,
    }
    write_json(output_dir / ARTIFACT_NAME, artifact)
    write_json(output_dir / "optimized_model_status.json", artifact)
    write_json(cfg.governance_root / "optimized_model_summary.json", artifact)
    return artifact


def predict_optimized_probability(feature: dict[str, Any], model: dict[str, Any]) -> float:
    if model.get("model_type") != "market_anchored_logistic":
        raise ValueError("optimized model artifact is not a market_anchored_logistic model")
    market_probability = _market_probability(feature)
    if market_probability is None:
        raise ValueError("feature row has no valid market probability anchor")
    vector = [1.0, _logit(market_probability)]
    for column in model.get("feature_columns", []):
        value = safe_float(feature.get(column))
        vector.append(0.0 if value is None else value)
    weights = [float(value) for value in model.get("weights", [])]
    mean = [float(value) for value in model.get("standardization_mean", [])]
    std = [float(value) if abs(float(value)) > 1e-12 else 1.0 for value in model.get("standardization_std", [])]
    if not (len(vector) == len(weights) == len(mean) == len(std)):
        raise ValueError("optimized model artifact dimensions do not match the feature row")
    standardised = [1.0]
    for value, avg, scale in zip(vector[1:], mean[1:], std[1:]):
        standardised.append((value - avg) / scale)
    return _clamp_probability(_sigmoid(sum(weight * value for weight, value in zip(weights, standardised))))


def main(config_path: str) -> dict[str, Any]:
    return train_optimized_model(load_config(config_path))
