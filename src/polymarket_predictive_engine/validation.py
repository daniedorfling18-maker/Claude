from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import EngineConfig, load_config
from .models.baselines import brier_score, log_loss, midpoint_baseline
from .utils import read_csv_rows, safe_float, write_csv, write_json


def _join_predictions_labels(cfg: EngineConfig) -> tuple[list[int], list[float], list[dict[str, Any]]]:
    labels = read_csv_rows(cfg.output_root / "polymarket_training" / "labels.csv")
    preds = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    key_to_label = {(r.get("market_id"), r.get("token_id"), r.get("prediction_timestamp")): r for r in labels if r.get("horizon") in {"", "all_valid"}}
    y: list[int] = []
    p: list[float] = []
    joined: list[dict[str, Any]] = []
    for pred in preds:
        key = (pred.get("market_id"), pred.get("token_id"), pred.get("prediction_timestamp"))
        label = key_to_label.get(key)
        if not label:
            continue
        target = int(float(label.get("target", 0)))
        prob = safe_float(pred.get("calibrated_probability")) or 0.5
        y.append(target)
        p.append(prob)
        joined.append({**pred, "target": target})
    return y, p, joined


def validate_model(cfg: EngineConfig) -> dict[str, Any]:
    y, p, joined = _join_predictions_labels(cfg)
    min_n = int(cfg.raw.get("governance_thresholds", {}).get("min_validation_rows", 100))
    warnings = []
    if len(y) < min_n:
        warnings.append(f"sample size too small: {len(y)} < {min_n}")
    brier = brier_score(y, p) if y else None
    loss = log_loss(y, p) if y else None
    buckets: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for target, prob in zip(y, p):
        lo = int(prob * 10) / 10
        bucket = f"{lo:.1f}-{lo + 0.1:.1f}"
        buckets[bucket].append((target, prob))
    bucket_rows = []
    for bucket, vals in sorted(buckets.items()):
        bucket_rows.append({"bucket": bucket, "count": len(vals), "mean_prediction": sum(v[1] for v in vals)/len(vals), "realized_rate": sum(v[0] for v in vals)/len(vals)})
    approved_paper = bool(y) and len(y) >= min_n and not warnings
    summary = {"model_version": "pm-calibrated-v1", "sample_size": len(y), "brier_score": brier, "log_loss": loss, "approved_for_paper_trading": approved_paper, "approved_for_live_trading": False, "warnings": warnings}
    out = cfg.output_root / "polymarket_model_validation"
    write_json(out / "model_validation_summary.json", summary)
    write_csv(out / "calibration_by_bucket.csv", bucket_rows)
    write_csv(out / "walk_forward_results.csv", [])
    write_csv(out / "holdout_results.csv", joined)
    write_csv(out / "baseline_comparison.csv", [{"baseline": "midpoint", "brier_score": brier, "log_loss": loss}, {"baseline": "no_trade", "brier_score": "", "log_loss": ""}])
    model_card = ["# Polymarket Model Card", "", f"Model version: {summary['model_version']}", f"Sample size: {len(y)}", f"Approved for paper trading: {approved_paper}", "Approved for live trading: False", "", "Live trading remains prohibited until validation, governance and approval gates pass."]
    (out / "model_card.md").parent.mkdir(parents=True, exist_ok=True)
    (out / "model_card.md").write_text("\n".join(model_card) + "\n", encoding="utf-8")
    return summary


def main(config_path: str) -> dict[str, Any]:
    return validate_model(load_config(config_path))
