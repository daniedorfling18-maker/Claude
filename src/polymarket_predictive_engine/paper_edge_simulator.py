from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .models.calibration_v2 import calibrate_probability, fit_bucket_calibrator, joined_feature_label_rows
from .risk import risk_decision
from .utils import now_utc, read_json, safe_float, write_csv, write_json


def _temporal_market_split(rows: list[dict[str, Any]], test_fraction: float) -> tuple[set[str], set[str]]:
    """Split markets so the earliest train and the latest test, with no market in both."""
    first_seen: dict[str, str] = {}
    for row in rows:
        key = str(row.get("market_id") or row.get("market_slug") or "")
        ts = str(row.get("prediction_timestamp") or "")
        if key not in first_seen or ts < first_seen[key]:
            first_seen[key] = ts
    ordered = [m for m, _ in sorted(first_seen.items(), key=lambda kv: (kv[1], kv[0]))]
    if len(ordered) < 2:
        return set(ordered), set()
    cut = max(1, int(round(len(ordered) * (1 - test_fraction))))
    return set(ordered[:cut]), set(ordered[cut:])


def simulate_paper_edge(cfg: EngineConfig, test_fraction: float = 0.3) -> dict[str, Any]:
    """Honest, out-of-sample paper-trading simulation.

    The calibrator is fit on earlier markets and the paper P&L is settled only on later,
    held-out markets. Evaluating the calibrator on its own training rows (the previous
    behaviour) memorises each bucket's realised rate and reports illusory edge, so this is
    deliberately forward-style. Still requires a trained pipeline (``train-calibration``).
    """
    model_path = cfg.output_root / "polymarket_models" / "calibration_v2.json"
    if not read_json(model_path, None):
        raise RuntimeError("Missing calibration model. Run train-calibration before simulate-paper-edge.")

    settings = cfg.raw.get("paper_edge_simulator", {})
    minimum_edge = float(settings.get("minimum_edge", cfg.raw.get("risk", {}).get("minimum_edge", 0.03)))
    test_fraction = float(settings.get("oos_test_fraction", test_fraction))
    bucket_count = int(settings.get("buckets", cfg.raw.get("calibration", {}).get("buckets", 10)))
    shrinkage = float(settings.get("bucket_shrinkage_to_midpoint", 0.20))

    train_root = cfg.output_root / "polymarket_training"
    rows = joined_feature_label_rows(str(train_root / "features_v2.csv"), str(train_root / "labels.csv"))
    train_markets, test_markets = _temporal_market_split(rows, test_fraction)
    train_rows = [r for r in rows if str(r.get("market_id")) in train_markets]
    test_rows = [r for r in rows if str(r.get("market_id")) in test_markets]

    out_root = cfg.output_root / "polymarket_paper_edge"
    out_root.mkdir(parents=True, exist_ok=True)

    if len(train_rows) < 50 or not test_rows:
        summary = {
            "status": "insufficient_data_for_oos",
            "reason": "need >=50 train rows and a held-out market test set",
            "train_rows": len(train_rows), "test_rows": len(test_rows), "simulated_at": now_utc(),
        }
        write_json(out_root / "paper_edge_summary.json", summary)
        return summary

    # Calibrator fit on TRAIN markets only; paper P&L settled on held-out TEST markets.
    _, oos_model = fit_bucket_calibrator(train_rows, bucket_count=bucket_count, shrinkage_to_midpoint=shrinkage)

    orders: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    settled: list[tuple[float, float, int]] = []
    for feature in test_rows:
        raw_probability = safe_float(feature.get("predicted_probability"))
        if raw_probability is None:
            raw_probability = safe_float(feature.get("implied_probability")) or safe_float(feature.get("midpoint"))
        executable_price = safe_float(feature.get("executable_buy_price")) or raw_probability
        if raw_probability is None or executable_price is None:
            rejections.append({**feature, "rejection_reason": "missing probability or executable price"})
            continue

        calibrated_probability = calibrate_probability(raw_probability, oos_model)
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

        target_f = safe_float(feature.get("target"))
        target = int(target_f) if target_f is not None else None
        realised_pnl = ""
        if target is not None:
            settled.append((edge, executable_price, target))
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
        (orders if decision.get("approved") else rejections).append(row)

    # Settled unit-stake track record by edge threshold: would "buy when edge >= t, settle
    # at resolution" have made money on the held-out markets?
    def _curve(threshold: float) -> dict[str, Any]:
        picks = [(price, t) for edge, price, t in settled if edge >= threshold]
        staked = sum(price for price, _ in picks)
        pnl = sum(((1 - price) if t == 1 else -price) for price, t in picks)
        return {
            "edge_threshold": threshold,
            "bets": len(picks),
            "total_realised_pnl": round(pnl, 4),
            "staked": round(staked, 4),
            "roi_on_staked": round(pnl / staked, 4) if staked else None,
            "win_rate": round(sum(t for _, t in picks) / len(picks), 4) if picks else None,
        }

    edges = [edge for edge, _, _ in settled]
    realised_orders = [safe_float(o.get("realised_pnl")) for o in orders if safe_float(o.get("realised_pnl")) is not None]

    write_csv(out_root / "paper_edge_orders.csv", orders)
    write_csv(out_root / "paper_edge_rejections.csv", rejections)
    summary = {
        "status": "paper_only",
        "evaluation": "out_of_sample_by_market",
        "in_sample": False,
        "train_markets": len(train_markets),
        "test_markets": len(test_markets),
        "train_rows": len(train_rows),
        "test_rows": len(test_rows),
        "orders": len(orders),
        "rejections": len(rejections),
        "settled_rows": len(settled),
        "minimum_edge": minimum_edge,
        "approved_orders_realised_pnl": round(sum(realised_orders), 4) if realised_orders else 0.0,
        "edge_distribution": {
            "max_edge": round(max(edges), 4) if edges else None,
            "mean_edge": round(sum(edges) / len(edges), 4) if edges else None,
            "fraction_positive_edge": round(sum(1 for e in edges if e > 0) / len(edges), 4) if edges else None,
            "fraction_above_minimum": round(sum(1 for e in edges if e >= minimum_edge) / len(edges), 4) if edges else None,
        },
        "paper_pnl_by_edge_threshold": [_curve(t) for t in (0.0, 0.01, 0.02, 0.03, 0.05)],
        "note": "Calibrator fit on earlier markets and settled on later held-out markets; "
                "in-sample evaluation overstates edge, so this is the forward-style paper P&L.",
        "simulated_at": now_utc(),
        "orders_file": str(out_root / "paper_edge_orders.csv"),
        "rejections_file": str(out_root / "paper_edge_rejections.csv"),
    }
    write_json(out_root / "paper_edge_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return simulate_paper_edge(load_config(config_path))
