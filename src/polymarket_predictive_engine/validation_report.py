from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json


def _rows(path, limit: int | None = None) -> list[dict[str, str]]:
    try:
        return read_csv_rows(path, limit=limit)
    except Exception:
        return []


def _safe_log_loss(actual: float, probability: float) -> float:
    p = min(1.0 - 1e-9, max(1e-9, probability))
    return -(actual * math.log(p) + (1.0 - actual) * math.log(1.0 - p))


def _label_probability_metrics(cfg: EngineConfig) -> dict[str, Any]:
    labels = _rows(cfg.output_root / "polymarket_training" / "labels.csv")
    features = _rows(cfg.output_root / "polymarket_training" / "features_v2.csv")
    feature_index = {
        (
            str(row.get("market_id") or ""),
            str(row.get("token_id") or ""),
            str(row.get("prediction_timestamp") or ""),
        ): row
        for row in features
    }
    bucket_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"rows": 0.0, "brier": 0.0, "log_loss": 0.0})
    total = {"rows": 0.0, "brier": 0.0, "log_loss": 0.0}
    for label in labels:
        outcome = str(label.get("outcome") or label.get("target") or "").strip().lower()
        if outcome in {"1", "true", "yes", "win", "won"}:
            actual = 1.0
        elif outcome in {"0", "false", "no", "lose", "lost"}:
            actual = 0.0
        else:
            continue
        key = (
            str(label.get("market_id") or ""),
            str(label.get("token_id") or ""),
            str(label.get("prediction_timestamp") or ""),
        )
        feature = feature_index.get(key, {})
        probability = safe_float(feature.get("midpoint") or feature.get("market_probability") or feature.get("implied_probability"))
        if probability is None or probability <= 0 or probability >= 1:
            continue
        category = str(feature.get("category") or label.get("category") or "unknown").strip().lower() or "unknown"
        time_to_close = safe_float(feature.get("time_to_close_hours") or feature.get("hours_to_close"))
        time_bucket = "unknown"
        if time_to_close is not None:
            time_bucket = "0-1h" if time_to_close <= 1 else "1-6h" if time_to_close <= 6 else "6-24h" if time_to_close <= 24 else "1d+"
        bucket = f"{category}|{time_bucket}"
        brier = (probability - actual) ** 2
        log_loss = _safe_log_loss(actual, probability)
        for stat in (total, bucket_stats[bucket]):
            stat["rows"] += 1
            stat["brier"] += brier
            stat["log_loss"] += log_loss
    buckets = []
    for bucket, stat in sorted(bucket_stats.items()):
        rows = stat["rows"]
        if rows <= 0:
            continue
        buckets.append(
            {
                "bucket": bucket,
                "rows": int(rows),
                "brier": stat["brier"] / rows,
                "log_loss": stat["log_loss"] / rows,
                "sample_status": "thin" if rows < 30 else "usable",
            }
        )
    rows = total["rows"]
    return {
        "status": "ok" if rows else "no_joined_label_probability_rows",
        "rows": int(rows),
        "brier": (total["brier"] / rows) if rows else None,
        "log_loss": (total["log_loss"] / rows) if rows else None,
        "buckets": buckets[:50],
    }


def _cohort_validation_summary(cfg: EngineConfig) -> dict[str, Any]:
    summary = read_json(cfg.governance_root / "signal_cohort_pnl.json", default={}) or {}
    cohorts = summary.get("cohorts", []) if isinstance(summary, dict) else []
    if not isinstance(cohorts, list):
        cohorts = []
    counts = Counter()
    watchlist: list[dict[str, Any]] = []
    for row in cohorts:
        if not isinstance(row, dict):
            continue
        counts["total"] += 1
        if row.get("promoted") is True:
            counts["promoted"] += 1
        if row.get("probationary") is True:
            counts["probationary"] += 1
        if str(row.get("metadata_blocker") or "").strip():
            counts["metadata_blocked"] += 1
        pnl = safe_float(row.get("total_pnl_usdc")) or 0.0
        roi = safe_float(row.get("roi")) or 0.0
        fills = safe_float(row.get("buy_fills")) or 0.0
        settled = safe_float(row.get("settled_fills")) or 0.0
        if pnl > 0:
            counts["positive_pnl"] += 1
        if fills < 5 or settled < 3:
            counts["thin_sample"] += 1
        if pnl > 0 or roi > 0 or str(row.get("metadata_blocker") or "").strip():
            watchlist.append(
                {
                    "cohort": row.get("signal_cohort"),
                    "promoted": row.get("promoted"),
                    "probationary": row.get("probationary"),
                    "metadata_blocker": row.get("metadata_blocker", ""),
                    "buy_fills": fills,
                    "settled_fills": settled,
                    "pnl_usdc": pnl,
                    "roi": roi,
                    "sample_status": "thin" if fills < 5 or settled < 3 else "minimum_count_met",
                }
            )
    watchlist.sort(key=lambda row: (bool(row.get("metadata_blocker")), row.get("pnl_usdc") or 0.0, row.get("roi") or 0.0), reverse=True)
    return {"counts": dict(counts), "watchlist": watchlist[:25]}


def build_validation_report(cfg: EngineConfig) -> dict[str, Any]:
    promotion_gate = read_json(cfg.governance_root / "promotion_gate.json", default={}) or {}
    promotion_review = read_json(cfg.governance_root / "promotion_review.json", default={}) or {}
    predictions = _rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    approved = _rows(cfg.output_root / "polymarket_predictions" / "trade_signals.csv")
    rejected = _rows(cfg.output_root / "polymarket_predictions" / "rejected_signals.csv")
    rejection_counts = Counter(str(row.get("rejection_reason") or "unknown") for row in rejected)
    calibration = _label_probability_metrics(cfg)
    cohort_summary = _cohort_validation_summary(cfg)
    approved_for_paper = bool(promotion_gate.get("approved_for_paper_trading"))
    approved_for_live = bool(promotion_gate.get("approved_for_live_trading"))
    oos_skill = bool(promotion_gate.get("oos_beats_market_significantly"))
    model_admissibility = "paper_admissible" if approved_for_paper and oos_skill else "not_paper_admissible"
    result = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "model_admissibility": model_admissibility,
        "approved_for_paper_trading": approved_for_paper,
        "approved_for_live_trading": approved_for_live,
        "promotion_gate": {
            "resolved_labels": promotion_gate.get("resolved_labels"),
            "target_resolved_labels": promotion_gate.get("target_resolved_labels"),
            "oos_brier_skill_vs_market": promotion_gate.get("oos_brier_skill_vs_market"),
            "oos_beats_market_significantly": promotion_gate.get("oos_beats_market_significantly"),
            "paper_blockers": promotion_gate.get("paper_blockers", []),
            "live_blockers": promotion_gate.get("live_blockers", []),
        },
        "current_signal_counts": {
            "predictions": len(predictions),
            "approved_signals": len(approved),
            "rejected_signals": len(rejected),
            "top_rejection_reasons": [
                {"reason": reason, "count": count} for reason, count in rejection_counts.most_common(10)
            ],
        },
        "cohort_validation": cohort_summary,
        "calibration_proxy_metrics": calibration,
        "promotion_review_status": promotion_review.get("status"),
        "limitations": [
            "This report is governance/readiness evidence only; it is not permission to trade live.",
            "Calibration metrics are only as good as the currently joined label/probability rows.",
            "Thin cohorts remain vulnerable to selection bias and multiple-testing false positives.",
        ],
        "required_before_live": [
            "Statistically significant out-of-sample skill over market probabilities.",
            "Clean metadata for all promoted/probationary cohorts.",
            "Actual paper-broker fills with positive post-cost forward P&L.",
            "Human approval file and live-trading environment gates, if live trading is ever considered.",
        ],
    }
    write_json(cfg.governance_root / "validation_report.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a conservative Polymarket validation report.")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    print(json.dumps(build_validation_report(cfg), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
