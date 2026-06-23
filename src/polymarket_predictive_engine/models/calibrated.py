from __future__ import annotations

from typing import Any

from ..utils import git_commit_hash, now_utc, read_csv_rows, safe_float, write_csv, write_json
from .baselines import clamp

MODEL_VERSION = "pm-calibrated-v1"
FEATURE_SET_VERSION = "pm-point-in-time-v1"


def shrink_probability(raw_probability: float, midpoint: float, shrinkage: float = 0.35) -> float:
    return clamp((1 - shrinkage) * raw_probability + shrinkage * midpoint)


def predict_from_features(features: list[dict[str, Any]], training_cutoff: str = "") -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    for row in features:
        midpoint = safe_float(row.get("midpoint")) or 0.5
        raw = midpoint
        calibrated = shrink_probability(raw, midpoint)
        exec_price = safe_float(row.get("executable_buy_price")) or midpoint
        edge = calibrated - exec_price
        predictions.append({
            "market_id": row.get("market_id", ""),
            "token_id": row.get("token_id", ""),
            "prediction_timestamp": row.get("prediction_timestamp", now_utc()),
            "raw_probability": raw,
            "calibrated_probability": calibrated,
            "market_midpoint": midpoint,
            "executable_price": exec_price,
            "edge": edge,
            "confidence": max(0.0, min(1.0, 1 - abs(calibrated - 0.5))),
            "uncertainty_low": max(0.0, calibrated - 0.08),
            "uncertainty_high": min(1.0, calibrated + 0.08),
            "model_version": MODEL_VERSION,
            "feature_set_version": FEATURE_SET_VERSION,
            "training_data_cutoff_timestamp": training_cutoff,
            "git_commit_hash": git_commit_hash(),
        })
    return predictions


def train_model(feature_path: str, label_path: str, output_dir: str, min_labels: int = 100) -> dict[str, Any]:
    labels = read_csv_rows(label_path)
    if len(labels) < min_labels:
        payload = {"status": "refused", "reason": f"insufficient labels: {len(labels)} < {min_labels}", "model_version": MODEL_VERSION}
        write_json(f"{output_dir}/model_train_status.json", payload)
        raise RuntimeError(payload["reason"])
    payload = {"status": "trained_baseline_calibrator", "label_count": len(labels), "model_version": MODEL_VERSION, "trained_at": now_utc(), "git_commit_hash": git_commit_hash()}
    write_json(f"{output_dir}/model_train_status.json", payload)
    return payload


def write_predictions(features: list[dict[str, Any]], output_path: str) -> list[dict[str, Any]]:
    preds = predict_from_features(features)
    write_csv(output_path, preds)
    return preds
