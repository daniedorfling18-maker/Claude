from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .models.calibration_v2 import calibrate_probability
from .risk import risk_decision
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json


def _label_lookup(cfg: EngineConfig) -> dict[tuple[str, str, str], int]:
    lookup: dict[tuple[str, str, str], int] = {}
    for row in read_csv_rows(cfg.output_root / "polymarket_training" / "labels.csv"):
        if row.get("horizon", "all_valid") != "all_valid":
            continue
        y = safe_float(row.get("target"))
        key = (row.get("market_id", ""), row.get("token_id", ""), row.get("prediction_timestamp", ""))
        if y is not None and all(key):
            lookup[key] = int(y)
    return lookup


def simulate_paper_edge(cfg: EngineConfig) -> dict[str, Any]:
    model_path = cfg.output_root / "polymarket_models" / "calibration_v2.json"
    model = read_json(model_path, None)
    if not model:
        raise RuntimeError("Missing calibration model. Run train-calibration before simulate-paper-edge.")

    settings = cfg.raw.get("paper_edge_simulator", {})
    minimum_edge = float(settings.get("minimum_edge", cfg.raw.get("risk", {}).get("minimum_edge", 0.03)))
    features = read_csv_rows(cfg.output_root / "polymarket_training" / "features_v2.csv")
    labels = _label_lookup(cfg)

    orders: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    for feature in features:
        raw_probability = safe_float(feature.get("implied_probability")) or safe_float(feature.get("midpoint"))
        executable_price = safe_float(feature.get("executable_buy_price")) or raw_probability
        if raw_probability is None or executable_price is None:
            rejections.append({**feature, "rejection_reason": "missing probability or executable price"})
            continue

        calibrated_probability = calibrate_probability(raw_probability, model)
        edge = calibrated_probability - executable_price
        signal = {
            **feature,
            "calibrated_probability": calibrated_probability,
            "edge": edge,
            "executable_price": executable_price,
            "confidence": max(0.0, min(1.0, 1 - abs(calibrated_probability - 0.5))),
        }
        decision = risk_decision(cfg, signal)
        if edge < minimum_edge:
            decision = {"approved": False, "reason": "edge below paper simulator minimum", "size": 0.0}

        key = (feature.get("market_id", ""), feature.get("token_id", ""), feature.get("prediction_timestamp", ""))
        target = labels.get(key)
        realised_pnl = ""
        if target is not None:
            realised_pnl = (1 - executable_price) * decision.get("size", 0.0) if target == 1 else -executable_price * decision.get("size", 0.0)

        row = {
            "market_id": feature.get("market_id", ""),
            "market_slug": feature.get("market_slug", ""),
            "token_id": feature.get("token_id", ""),
            "prediction_timestamp": feature.get("prediction_timestamp", ""),
            "category": feature.get("category", ""),
            "raw_probability": raw_probability,
            "calibrated_probability": calibrated_probability,
            "executable_price": executable_price,
            "edge": edge,
            "approved": decision.get("approved", False),
            "reason": decision.get("reason", ""),
            "paper_size": decision.get("size", 0.0),
            "target": target if target is not None else "",
            "realised_pnl": realised_pnl,
        }
        if decision.get("approved"):
            orders.append(row)
        else:
            rejections.append(row)

    out_root = cfg.output_root / "polymarket_paper_edge"
    out_root.mkdir(parents=True, exist_ok=True)
    write_csv(out_root / "paper_edge_orders.csv", orders)
    write_csv(out_root / "paper_edge_rejections.csv", rejections)
    summary = {
        "status": "paper_only",
        "orders": len(orders),
        "rejections": len(rejections),
        "simulated_at": now_utc(),
        "orders_file": str(out_root / "paper_edge_orders.csv"),
        "rejections_file": str(out_root / "paper_edge_rejections.csv"),
    }
    write_json(out_root / "paper_edge_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return simulate_paper_edge(load_config(config_path))
