from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .readiness import readiness_decision
from .risk import risk_decision
from .utils import read_csv_rows, safe_float, write_csv


def generate_signals(cfg: EngineConfig) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    readiness = readiness_decision(cfg)
    preds = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    allow_prediction = bool(readiness.get("approved_for_paper_trading"))
    readiness_reason = "; ".join(readiness.get("paper_trading_blockers", []) or []) or "readiness gate is not approved"
    for pred in preds:
        edge = safe_float(pred.get("edge")) or 0.0
        signal = {
            "market_id": pred.get("market_id", ""),
            "market_slug": pred.get("market_slug", ""),
            "question": pred.get("question", ""),
            "category": pred.get("category", ""),
            "outcome": pred.get("outcome", "YES"),
            "token_id": pred.get("token_id", ""),
            "side": "BUY_YES",
            "market_price": pred.get("market_midpoint", ""),
            "executable_price": pred.get("executable_price", ""),
            "model_probability": pred.get("raw_probability", ""),
            "calibrated_probability": pred.get("calibrated_probability", ""),
            "uncertainty_low": pred.get("uncertainty_low", ""),
            "uncertainty_high": pred.get("uncertainty_high", ""),
            "edge": edge,
            "expected_value_per_share": edge,
            "liquidity": pred.get("liquidity", 1000),
            "spread": pred.get("spread", 0.01),
            "time_to_close": pred.get("time_to_close_hours", ""),
            "confidence": pred.get("confidence", ""),
            "model_version": pred.get("model_version", ""),
            "data_snapshot_timestamp": pred.get("prediction_timestamp", ""),
        }
        if not allow_prediction:
            rejected.append({**signal, "rejection_reason": readiness_reason})
            continue
        decision = risk_decision(cfg, signal)
        signal["risk_decision"] = decision["reason"]
        signal["sizing_decision"] = decision["size"]
        if decision["approved"]:
            signal["approval_reason"] = "edge and risk controls approved"
            approved.append(signal)
        else:
            rejected.append({**signal, "rejection_reason": decision["reason"]})
    out = cfg.output_root / "polymarket_predictions"
    write_csv(out / "trade_signals.csv", approved)
    write_csv(out / "rejected_signals.csv", rejected)
    return approved, rejected


def main(config_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return generate_signals(load_config(config_path))
