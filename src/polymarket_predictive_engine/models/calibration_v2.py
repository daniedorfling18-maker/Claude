from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from ..config import EngineConfig, load_config
from ..utils import git_commit_hash, now_utc, read_csv_rows, safe_float, write_csv, write_json

MODEL_VERSION = "pm-bucket-calibration-v2"
FEATURE_SET_VERSION = "pm-point-in-time-v2"
EPS = 1e-15


def _clip_probability(value: float) -> float:
    return max(EPS, min(1 - EPS, value))


def brier_score(rows: list[tuple[float, int]]) -> float:
    if not rows:
        return 0.0
    return sum((p - y) ** 2 for p, y in rows) / len(rows)


def log_loss(rows: list[tuple[float, int]]) -> float:
    if not rows:
        return 0.0
    return -sum(y * math.log(_clip_probability(p)) + (1 - y) * math.log(_clip_probability(1 - p)) for p, y in rows) / len(rows)


def _label_index(labels: list[dict[str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    index: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in labels:
        if row.get("horizon", "all_valid") != "all_valid":
            continue
        key = (row.get("market_id", ""), row.get("token_id", ""), row.get("prediction_timestamp", ""))
        if all(key):
            index[key] = row
    return index


def joined_feature_label_rows(feature_path: str, label_path: str) -> list[dict[str, Any]]:
    labels = _label_index(read_csv_rows(label_path))
    joined: list[dict[str, Any]] = []
    for feature in read_csv_rows(feature_path):
        key = (feature.get("market_id", ""), feature.get("token_id", ""), feature.get("prediction_timestamp", ""))
        label = labels.get(key)
        if not label:
            continue
        p = safe_float(feature.get("implied_probability")) or safe_float(feature.get("midpoint"))
        y = safe_float(label.get("target"))
        if p is None or y is None:
            continue
        joined.append({**feature, "target": int(y), "predicted_probability": max(0.0, min(1.0, p))})
    return joined


def _bucket_id(probability: float, bucket_count: int) -> int:
    if probability >= 1:
        return bucket_count - 1
    if probability <= 0:
        return 0
    return min(bucket_count - 1, int(probability * bucket_count))


def fit_bucket_calibrator(
    rows: list[dict[str, Any]],
    *,
    bucket_count: int = 10,
    shrinkage_to_midpoint: float = 0.20,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_bucket: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bucket[_bucket_id(float(row["predicted_probability"]), bucket_count)].append(row)

    reliability: list[dict[str, Any]] = []
    mapping: dict[str, Any] = {}
    scored_raw: list[tuple[float, int]] = []
    scored_cal: list[tuple[float, int]] = []

    for bucket in range(bucket_count):
        lo = bucket / bucket_count
        hi = (bucket + 1) / bucket_count
        bucket_rows = by_bucket.get(bucket, [])
        row_count = len(bucket_rows)
        bucket_midpoint = (lo + hi) / 2

        if row_count:
            avg_pred = sum(float(r["predicted_probability"]) for r in bucket_rows) / row_count
            actual = sum(int(r["target"]) for r in bucket_rows) / row_count
            calibrated = (1 - shrinkage_to_midpoint) * actual + shrinkage_to_midpoint * bucket_midpoint
            raw_pairs = [(float(r["predicted_probability"]), int(r["target"])) for r in bucket_rows]
            cal_pairs = [(calibrated, int(r["target"])) for r in bucket_rows]
        else:
            avg_pred = bucket_midpoint
            actual = ""
            calibrated = bucket_midpoint
            raw_pairs = []
            cal_pairs = []

        scored_raw.extend(raw_pairs)
        scored_cal.extend(cal_pairs)
        reliability.append(
            {
                "bucket": bucket,
                "probability_min": lo,
                "probability_max": hi,
                "row_count": row_count,
                "avg_predicted_probability": avg_pred,
                "actual_win_rate": actual,
                "calibrated_probability": calibrated,
                "brier_score": brier_score(raw_pairs),
                "log_loss": log_loss(raw_pairs),
            }
        )
        mapping[str(bucket)] = {
            "probability_min": lo,
            "probability_max": hi,
            "calibrated_probability": calibrated,
            "row_count": row_count,
        }

    model = {
        "bucket_count": bucket_count,
        "bucket_mapping": mapping,
        "baseline_brier_score": brier_score(scored_raw),
        "baseline_log_loss": log_loss(scored_raw),
        "calibrated_brier_score": brier_score(scored_cal),
        "calibrated_log_loss": log_loss(scored_cal),
    }
    return reliability, model


def calibrate_probability(probability: float, model: dict[str, Any]) -> float:
    bucket_count = int(model.get("bucket_count", 10))
    bucket = _bucket_id(probability, bucket_count)
    entry = (model.get("bucket_mapping", {}) or {}).get(str(bucket), {})
    return float(entry.get("calibrated_probability", probability))


def train_calibration_model(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("calibration_v2", {})
    feature_path = cfg.output_root / "polymarket_training" / "features_v2.csv"
    label_path = cfg.output_root / "polymarket_training" / "labels.csv"
    output_dir = cfg.output_root / "polymarket_models"
    output_dir.mkdir(parents=True, exist_ok=True)

    minimum_rows = int(settings.get("minimum_training_rows", cfg.raw.get("governance_thresholds", {}).get("min_training_rows", 100)))
    bucket_count = int(settings.get("buckets", cfg.raw.get("calibration", {}).get("buckets", 10)))
    shrinkage = float(settings.get("bucket_shrinkage_to_midpoint", 0.20))
    rows = joined_feature_label_rows(str(feature_path), str(label_path))

    if len(rows) < minimum_rows:
        payload = {
            "status": "refused",
            "reason": f"insufficient joined labels: {len(rows)} < {minimum_rows}",
            "model_version": MODEL_VERSION,
            "feature_set_version": FEATURE_SET_VERSION,
            "training_rows": len(rows),
        }
        write_json(output_dir / "calibration_v2_status.json", payload)
        raise RuntimeError(payload["reason"])

    reliability, fitted = fit_bucket_calibrator(rows, bucket_count=bucket_count, shrinkage_to_midpoint=shrinkage)
    model = {
        "model_version": MODEL_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "training_rows": len(rows),
        "bucket_mapping": fitted["bucket_mapping"],
        "bucket_count": bucket_count,
        "baseline_brier_score": fitted["baseline_brier_score"],
        "baseline_log_loss": fitted["baseline_log_loss"],
        "calibrated_brier_score": fitted["calibrated_brier_score"],
        "calibrated_log_loss": fitted["calibrated_log_loss"],
        "trained_at": now_utc(),
        "git_commit_hash": git_commit_hash(),
    }
    write_json(output_dir / "calibration_v2.json", model)
    status = {"status": "trained", "training_rows": len(rows), "model_version": MODEL_VERSION}
    write_json(output_dir / "calibration_v2_status.json", status)
    write_csv(cfg.governance_root / "calibration_v2_reliability.csv", reliability)
    return status


def main(config_path: str) -> dict[str, Any]:
    return train_calibration_model(load_config(config_path))
