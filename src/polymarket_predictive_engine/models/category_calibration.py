from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..config import EngineConfig, load_config
from ..utils import now_utc, read_json, write_csv, write_json
from .calibration_v2 import FEATURE_SET_VERSION, MODEL_VERSION, fit_bucket_calibrator, joined_feature_label_rows


def train_category_calibration(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("calibration_v2", {})
    feature_path = cfg.output_root / "polymarket_training" / "features_v2.csv"
    label_path = cfg.output_root / "polymarket_training" / "labels.csv"
    output_dir = cfg.output_root / "polymarket_models"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not read_json(output_dir / "calibration_v2.json", None):
        raise RuntimeError("Missing global calibration model. Run train-calibration first.")

    rows = joined_feature_label_rows(str(feature_path), str(label_path))
    min_rows = int(settings.get("min_rows_per_category", 50))
    bucket_count = int(settings.get("buckets", cfg.raw.get("calibration", {}).get("buckets", 10)))
    shrinkage = float(settings.get("bucket_shrinkage_to_midpoint", 0.20))

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[str(row.get("category") or "unknown")].append(row)

    trained: dict[str, Any] = {}
    fallback: dict[str, Any] = {}
    reliability_rows: list[dict[str, Any]] = []

    for category, category_rows in sorted(by_category.items()):
        if len(category_rows) < min_rows:
            fallback[category] = {
                "status": "fallback_global",
                "row_count": len(category_rows),
                "minimum_rows": min_rows,
            }
            continue

        reliability, fitted = fit_bucket_calibrator(
            category_rows,
            bucket_count=bucket_count,
            shrinkage_to_midpoint=shrinkage,
        )
        trained[category] = {
            "status": "trained",
            "row_count": len(category_rows),
            "bucket_count": bucket_count,
            "bucket_mapping": fitted["bucket_mapping"],
            "baseline_brier_score": fitted["baseline_brier_score"],
            "baseline_log_loss": fitted["baseline_log_loss"],
            "calibrated_brier_score": fitted["calibrated_brier_score"],
            "calibrated_log_loss": fitted["calibrated_log_loss"],
        }
        for row in reliability:
            reliability_rows.append({"category": category, **row})

    model = {
        "model_version": MODEL_VERSION,
        "feature_set_version": FEATURE_SET_VERSION,
        "trained_at": now_utc(),
        "min_rows_per_category": min_rows,
        "fallback": "global_calibration_v2",
        "categories": trained,
        "fallback_categories": fallback,
    }
    write_json(output_dir / "category_calibration_v2.json", model)
    write_csv(cfg.governance_root / "category_calibration_v2_reliability.csv", reliability_rows)
    return {
        "status": "trained_with_global_fallback",
        "trained_categories": len(trained),
        "fallback_categories": len(fallback),
        "min_rows_per_category": min_rows,
    }


def main(config_path: str) -> dict[str, Any]:
    return train_category_calibration(load_config(config_path))
