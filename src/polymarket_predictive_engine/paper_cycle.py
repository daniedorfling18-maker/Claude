from __future__ import annotations

import hashlib
import json
from typing import Any

from .config import EngineConfig
from .dashboard import render_dashboard
from .execution.paper import paper_trade
from .features_v2 import build_features_v2
from .mispricing_alpha import apply_mispricing_alpha
from .models.calibrated import load_prediction_models, write_predictions
from .readiness import paper_trade_readiness
from .profit_target import write_profit_target_tracker
from .cohort_validation import write_signal_cohort_pnl
from .shadow_cohort import update_shadow_cohort_evidence
from .storage import connect_db
from .strategy import generate_signals
from .utils import now_utc, safe_float, write_json


def _persist_predictions(cfg: EngineConfig, predictions: list[dict[str, Any]]) -> None:
    con = connect_db(cfg.database_path)
    try:
        with con:
            for prediction in predictions:
                key = "|".join(
                    [
                        str(prediction.get("market_id", "")),
                        str(prediction.get("token_id", "")),
                        str(prediction.get("prediction_timestamp", "")),
                        str(prediction.get("model_version", "")),
                    ]
                )
                prediction_id = "prediction_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
                con.execute(
                    """
                    INSERT OR IGNORE INTO model_predictions(
                        prediction_id, idempotency_key, created_at, market_id, token_id,
                        prediction_timestamp, model_probability, market_probability,
                        executable_price, edge, model_version, feature_set_version,
                        validation_status, raw_payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prediction_id,
                        key,
                        now_utc(),
                        prediction.get("market_id", ""),
                        prediction.get("token_id", ""),
                        prediction.get("prediction_timestamp", ""),
                        prediction.get("calibrated_probability", 0.0),
                        prediction.get("market_midpoint", 0.0),
                        prediction.get("executable_price", 0.0),
                        prediction.get("edge", 0.0),
                        prediction.get("model_version", ""),
                        prediction.get("feature_set_version", ""),
                        "paper_candidate",
                        json.dumps(prediction, sort_keys=True),
                    ),
                )
    finally:
        con.close()


def run_paper_cycle(
    cfg: EngineConfig,
    *,
    source: str = "raw_snapshot",
) -> dict[str, Any]:
    """Canonical forward paper cycle: features -> predictions -> signals -> broker."""
    report: dict[str, Any] = {
        "generated_at_utc": now_utc(),
        "source": source,
        "live_trading": False,
    }
    if cfg.trading_mode not in {"paper", "backtest"}:
        report.update(
            {
                "status": "blocked",
                "blockers": [f"trading.mode is {cfg.trading_mode}, expected paper or backtest"],
            }
        )
        write_json(cfg.governance_root / "forward_paper_cycle.json", report)
        return report

    try:
        features = build_features_v2(cfg, source=source, require_clean_labels=False)
        global_model, category_models = load_prediction_models(cfg)
    except Exception as exc:
        report.update({"status": "blocked", "blockers": [str(exc)]})
        write_json(cfg.governance_root / "forward_paper_cycle.json", report)
        return report

    prediction_path = cfg.output_root / "polymarket_predictions" / "predictions.csv"
    predictions = write_predictions(
        features,
        str(prediction_path),
        model=global_model,
        category_models=category_models,
        training_cutoff=str(global_model.get("trained_at", "")),
    )
    predictions = apply_mispricing_alpha(cfg, predictions, output_path=str(prediction_path))
    _persist_predictions(cfg, predictions)
    shadow_cohort = update_shadow_cohort_evidence(cfg, predictions)
    con = connect_db(cfg.database_path)
    try:
        cohort_pnl = write_signal_cohort_pnl(con, cfg)
    finally:
        con.close()
    gate = paper_trade_readiness(cfg)
    approved, rejected = generate_signals(cfg, readiness=gate)
    broker = paper_trade(cfg)
    target_monthly_profit = float(cfg.raw.get("mispricing_alpha", {}).get("target_monthly_profit_usdc", 100.0))
    filled_orders = broker.get("filled_orders", []) if isinstance(broker, dict) else []
    expected_cycle_profit = sum(
        safe_float(order.get("expected_lower_bound_profit_usdc")) or 0.0
        for order in filled_orders
        if isinstance(order, dict)
    )
    cycles_needed = target_monthly_profit / expected_cycle_profit if expected_cycle_profit > 0 else None
    monthly_target = {
        "target_monthly_profit_usdc": target_monthly_profit,
        "risk_approved_signals": len(approved),
        "paper_filled_orders": int(broker.get("orders_filled", 0)) if isinstance(broker, dict) else 0,
        "broker_rejected_orders": int(broker.get("orders_rejected", 0)) if isinstance(broker, dict) else 0,
        "expected_lower_bound_profit_per_cycle_usdc": expected_cycle_profit,
        "cycles_needed_for_target_at_current_approved_rate": cycles_needed,
        "on_pace_if_daily_cycle": bool(cycles_needed is not None and cycles_needed <= 30),
        "status": "on_pace" if cycles_needed is not None and cycles_needed <= 30 else "not_on_pace",
    }
    actual_profit_target = write_profit_target_tracker(cfg, broker if isinstance(broker, dict) else {})
    report.update(
        {
            "status": "ran" if gate.get("approved_for_paper_trading") else "blocked",
            "features": len(features),
            "predictions": len(predictions),
            "signals_approved": len(approved),
            "signals_rejected": len(rejected),
            "monthly_profit_target": monthly_target,
            "actual_profit_target": actual_profit_target,
            "shadow_cohort": shadow_cohort,
            "cohort_pnl": cohort_pnl,
            "readiness": gate,
            "broker": broker,
            "prediction_file": str(prediction_path),
        }
    )
    try:
        report["dashboard"] = render_dashboard(cfg, report)
    except Exception as exc:  # noqa: BLE001 - dashboard failure must not stop paper trading
        report["dashboard"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    write_json(cfg.governance_root / "forward_paper_cycle.json", report)
    return report
