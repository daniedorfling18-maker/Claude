from __future__ import annotations

from typing import Any

from ..config import EngineConfig
from ..utils import git_commit_hash, now_utc, read_json, safe_float, write_csv, write_json
from .baselines import clamp
from .calibration_v2 import calibrate_probability
from .optimized import predict_optimized_probability

MODEL_VERSION = "pm-calibrated-v1"
FEATURE_SET_VERSION = "pm-point-in-time-v1"


def shrink_probability(raw_probability: float, midpoint: float, shrinkage: float = 0.35) -> float:
    return clamp((1 - shrinkage) * raw_probability + shrinkage * midpoint)


def prediction_confidence(probability: float) -> float:
    """Certainty distance from 50%; 0 is maximally uncertain and 1 is near-certain."""
    return max(0.0, min(1.0, 2.0 * abs(probability - 0.5)))


def directional_confidence(probability: float) -> float:
    """Backward-compatible name for certainty distance from 50%."""
    return prediction_confidence(probability)


def uncertainty_centrality(probability: float) -> float:
    """How close the probability is to 0.5, scaled to [0,1]."""
    return max(0.0, min(1.0, 1.0 - prediction_confidence(probability)))


def _category_model(
    global_model: dict[str, Any] | None,
    category_models: dict[str, Any] | None,
    category: str,
) -> dict[str, Any] | None:
    if (global_model or {}).get("model_type") == "market_anchored_logistic":
        return global_model
    category_entry = ((category_models or {}).get("categories", {}) or {}).get(category, {})
    if category_entry.get("status") == "trained" and category_entry.get("bucket_mapping"):
        return category_entry
    return global_model


def load_prediction_models(cfg: EngineConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    model_root = cfg.output_root / "polymarket_models"
    global_model = read_json(model_root / "calibration_v2.json", default={}) or {}
    category_models = read_json(model_root / "category_calibration_v2.json", default={}) or {}
    optimized_model = read_json(model_root / "optimized_model_v1.json", default={}) or {}
    if (
        optimized_model.get("status") == "trained"
        and optimized_model.get("deployment_mode") == "champion"
        and optimized_model.get("model_type") == "market_anchored_logistic"
    ):
        return optimized_model, {}
    if not global_model:
        raise RuntimeError(
            "Missing deployed paper model. Run train-calibration or optimize-model until an optimized champion is available."
        )
    if (
        optimized_model.get("status") == "trained"
        and optimized_model.get("deployment_mode") == "shadow"
        and optimized_model.get("model_type") == "market_anchored_logistic"
    ):
        global_model["_shadow_optimized_model"] = optimized_model
    return global_model, category_models


def latest_feature_rows(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in features:
        key = (str(row.get("market_id", "")), str(row.get("token_id", "")))
        if not all(key):
            continue
        if key not in latest or str(row.get("prediction_timestamp", "")) >= str(
            latest[key].get("prediction_timestamp", "")
        ):
            latest[key] = row
    return sorted(
        latest.values(),
        key=lambda row: (
            str(row.get("prediction_timestamp", "")),
            str(row.get("market_id", "")),
            str(row.get("token_id", "")),
        ),
    )


def predict_from_features(
    features: list[dict[str, Any]],
    training_cutoff: str = "",
    *,
    model: dict[str, Any] | None = None,
    category_models: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for row in latest_feature_rows(features):
        midpoint = safe_float(row.get("midpoint"))
        if midpoint is None:
            midpoint = safe_float(row.get("implied_probability"))
        if midpoint is None or not 0 <= midpoint <= 1:
            continue
        category = str(row.get("category") or "unknown")
        selected_model = _category_model(model, category_models, category)
        if (selected_model or {}).get("model_type") == "market_anchored_logistic":
            calibrated = predict_optimized_probability(row, selected_model or {})
            model_source = "optimized_market_anchored_logistic"
        else:
            calibrated = (
                calibrate_probability(midpoint, selected_model)
                if selected_model
                else shrink_probability(midpoint, midpoint)
            )
            model_source = "calibration_v2"
        executable = safe_float(row.get("executable_buy_price"))
        if executable is None:
            executable = safe_float(row.get("best_ask"))
        if executable is None or not 0 < executable < 1:
            continue
        edge = calibrated - executable
        model_version = str((selected_model or {}).get("model_version") or MODEL_VERSION)
        feature_version = str((selected_model or {}).get("feature_set_version") or FEATURE_SET_VERSION)
        confidence = prediction_confidence(calibrated)
        selection_name = row.get("selection_name") or row.get("outcome") or "YES"
        prediction = {
            "market_id": row.get("market_id", ""),
            "market_slug": row.get("market_slug", ""),
            "token_id": row.get("token_id", ""),
            "question": row.get("question", ""),
            "category": category,
            "outcome": selection_name,
            "close_time": row.get("close_time", ""),
            "prediction_timestamp": row.get("prediction_timestamp", now_utc()),
            "raw_probability": midpoint,
            "calibrated_probability": calibrated,
            "model_probability": calibrated,
            "market_midpoint": midpoint,
            "executable_price": executable,
            "edge": edge,
            "confidence": confidence,
            "model_confidence": confidence,
            "uncertainty_centrality": uncertainty_centrality(calibrated),
            "uncertainty_low": max(0.0, calibrated - 0.08),
            "uncertainty_high": min(1.0, calibrated + 0.08),
            "liquidity": row.get("liquidity", ""),
            "spread": row.get("spread", ""),
            "time_to_close_hours": row.get("hours_to_close", row.get("time_to_close_hours", "")),
            "resolution_risk": row.get("resolution_risk", 0.0),
            "model_source": model_source,
            "model_version": model_version,
            "feature_set_version": feature_version,
            "training_data_cutoff_timestamp": training_cutoff
            or str((selected_model or {}).get("training_cutoff_timestamp") or (selected_model or {}).get("trained_at", "")),
            "git_commit_hash": git_commit_hash(),
        }
        shadow_model = (model or {}).get("_shadow_optimized_model") if isinstance(model, dict) else None
        if shadow_model:
            try:
                prediction["shadow_probability"] = predict_optimized_probability(row, shadow_model)
                prediction["shadow_model_version"] = shadow_model.get("model_version", "")
                prediction["shadow_model_source"] = "optimized_market_anchored_logistic"
            except Exception as exc:
                prediction["shadow_probability"] = ""
                prediction["shadow_model_error"] = str(exc)
        predictions.append(prediction)
    return predictions


def train_model(feature_path: str, label_path: str, output_dir: str, min_labels: int = 100) -> dict[str, Any]:
    from ..utils import read_csv_rows

    labels = read_csv_rows(label_path)
    if len(labels) < min_labels:
        payload = {
            "status": "refused",
            "reason": f"insufficient labels: {len(labels)} < {min_labels}",
            "model_version": MODEL_VERSION,
        }
        write_json(f"{output_dir}/model_train_status.json", payload)
        raise RuntimeError(payload["reason"])
    payload = {
        "status": "trained_baseline_calibrator",
        "label_count": len(labels),
        "model_version": MODEL_VERSION,
        "trained_at": now_utc(),
        "git_commit_hash": git_commit_hash(),
    }
    write_json(f"{output_dir}/model_train_status.json", payload)
    return payload


def write_predictions(
    features: list[dict[str, Any]],
    output_path: str,
    *,
    model: dict[str, Any] | None = None,
    category_models: dict[str, Any] | None = None,
    training_cutoff: str = "",
) -> list[dict[str, Any]]:
    predictions = predict_from_features(
        features,
        training_cutoff=training_cutoff,
        model=model,
        category_models=category_models,
    )
    write_csv(output_path, predictions)
    return predictions
