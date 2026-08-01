from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .dashboard import render_dashboard
from .decision_trace import write_agent_runtime_bundle
from .execution.paper import paper_trade
from .features_v2 import build_features_v2
from .longshot_bias import build_longshot_bias_scan
from .mispricing_alpha import apply_mispricing_alpha
from .models.calibrated import load_prediction_models, write_predictions
from .readiness import paper_trade_readiness
from .profit_target import write_profit_target_tracker
from .cohort_validation import write_signal_cohort_pnl
from .runtime_lock import runtime_lock
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


_SCOPES = {"full", "scoring_only"}


def _cycle_report_path(cfg: EngineConfig, scope: str) -> Path:
    """WO-143: a scoring-only cycle must never write (or be mistaken for)
    ``forward_paper_cycle.json``. The deployed live loop merges and re-stamps
    that artifact every ~30s tick (``run_polymarket_local_live_loop.py``
    :762-778) and ``readiness.py``:118-122 reads it as the forward-evidence
    summary; a scheduled write there would be republished as fresh forever.
    """
    filename = "forward_paper_cycle.json" if scope == "full" else "scheduled_paper_cycle_report.json"
    return cfg.governance_root / filename


def run_paper_cycle(
    cfg: EngineConfig,
    *,
    source: str = "raw_snapshot",
    scope: str = "full",
) -> dict[str, Any]:
    """Canonical forward paper cycle: features -> predictions -> signals -> broker.

    ``scope="full"`` (the default) is byte-identical to the pre-WO-143
    behaviour on every path. ``scope="scoring_only"`` (WO-143) is a scheduled
    owner slot that runs everything through signal generation but skips the
    writes the deployed live container already owns every tick -- shadow-
    cohort maintenance, the paper broker and profit-target tracker,
    signal-cohort P&L, the dashboard, and the agent-runtime bundle -- and
    redirects its cycle report to ``scheduled_paper_cycle_report.json``
    instead of ``forward_paper_cycle.json``.
    """
    if scope not in _SCOPES:
        # An unrecognised scope must never silently fall through to the full
        # cycle, so this is validated before the lock is even considered.
        raise ValueError(f"run_paper_cycle: unrecognised scope {scope!r}; expected one of {sorted(_SCOPES)}")

    report_path = _cycle_report_path(cfg, scope)
    scoring_only = scope == "scoring_only"
    report: dict[str, Any] = {
        "generated_at_utc": now_utc(),
        "source": source,
        "scope": scope,
        "live_trading": False,
    }
    if cfg.trading_mode not in {"paper", "backtest"}:
        report.update(
            {
                "status": "blocked",
                "blockers": [f"trading.mode is {cfg.trading_mode}, expected paper or backtest"],
            }
        )
        if scoring_only:
            report["agent_runtime"] = {"status": "skipped_scoring_only"}
            write_json(report_path, report)
        else:
            write_json(report_path, report)
            write_agent_runtime_bundle(cfg, cycle_report=report)
        return report

    lock_settings = cfg.raw.get("runtime_resource_guard", {}) or {}
    lock_stale_seconds = float(lock_settings.get("prediction_cycle_lock_stale_seconds", 1800) or 1800)
    with runtime_lock(cfg, "prediction_cycle", stale_after_seconds=lock_stale_seconds) as lock:
        if not lock.acquired:
            report.update(
                {
                    "status": "skipped_existing_prediction_cycle",
                    "blockers": ["prediction_cycle_lock_already_held"],
                    "runtime_lock": lock.as_dict(),
                    "paper_trading_invoked": False,
                    "live_trading_invoked": False,
                }
            )
            if scoring_only:
                report["agent_runtime"] = {"status": "skipped_scoring_only"}
                write_json(report_path, report)
            else:
                write_json(report_path, report)
                write_agent_runtime_bundle(cfg, cycle_report=report)
            return report
        return _run_paper_cycle_unlocked(cfg, source=source, scope=scope, report=report, report_path=report_path)


def _run_paper_cycle_unlocked(
    cfg: EngineConfig,
    *,
    source: str,
    scope: str,
    report: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    scoring_only = scope == "scoring_only"
    try:
        features = build_features_v2(cfg, source=source, require_clean_labels=False)
        global_model, category_models = load_prediction_models(cfg)
    except Exception as exc:
        report.update({"status": "blocked", "blockers": [str(exc)]})
        if scoring_only:
            report["agent_runtime"] = {"status": "skipped_scoring_only"}
            write_json(report_path, report)
        else:
            write_json(report_path, report)
            write_agent_runtime_bundle(cfg, cycle_report=report)
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
    longshot_scan = build_longshot_bias_scan(cfg, emit_shadow=False)
    longshot_candidates = [
        row for row in longshot_scan.get("candidate_rows", []) if isinstance(row, dict)
    ]
    if scoring_only:
        # WO-143: the deployed live container remains the SOLE writer of the
        # shadow ledgers. The naive design would add a second cross-container
        # writer to the anchor-enrolled shadow_fills.csv (see the WO-143
        # registration's provenance note; WO-143b independently serialises
        # update_shadow_cohort_evidence's own writes, but this scheduled slot
        # still must not forward candidates into it).
        shadow_cohort: dict[str, Any] = {"status": "skipped_scoring_only"}
    else:
        shadow_cohort = update_shadow_cohort_evidence(cfg, predictions + longshot_candidates)

    if scoring_only:
        # The live loop and broker recompute cohort P&L deterministically
        # every tick; a second writer here is redundant, not additive.
        cohort_pnl: dict[str, Any] = {"status": "skipped_scoring_only"}
    else:
        con = connect_db(cfg.database_path)
        try:
            cohort_pnl = write_signal_cohort_pnl(con, cfg)
        finally:
            con.close()

    gate = paper_trade_readiness(cfg)
    approved, rejected = generate_signals(cfg, readiness=gate)

    if scoring_only:
        # The live container remains the sole broker/profit-target writer;
        # a second scheduled writer would race paper_fills.csv. Never compute
        # a pace figure from an empty/absent broker dict.
        broker: dict[str, Any] = {"status": "skipped_scoring_only"}
        monthly_target: dict[str, Any] = {"status": "skipped_scoring_only"}
        actual_profit_target: dict[str, Any] = {"status": "skipped_scoring_only"}
    else:
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
            "longshot_bias": {
                "status": longshot_scan.get("status"),
                "candidates": longshot_scan.get("candidates"),
                "candidate_cohorts": longshot_scan.get("candidate_cohorts", {}),
                "decision_use": longshot_scan.get("decision_use"),
                "emit_shadow_positions": False,
                "shadow_candidates_forwarded": len(longshot_candidates),
            },
            "cohort_pnl": cohort_pnl,
            "readiness": gate,
            "broker": broker,
            "prediction_file": str(prediction_path),
        }
    )
    if scoring_only:
        report["agent_runtime"] = {"status": "skipped_scoring_only"}
    else:
        try:
            report["agent_runtime"] = write_agent_runtime_bundle(cfg, cycle_report=report, broker_summary=broker if isinstance(broker, dict) else {})
        except Exception as exc:  # noqa: BLE001 - observability failure must not stop paper trading
            report["agent_runtime"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    if scoring_only:
        report["dashboard"] = {"status": "skipped_scoring_only"}
    else:
        try:
            report["dashboard"] = render_dashboard(cfg, report)
        except Exception as exc:  # noqa: BLE001 - dashboard failure must not stop paper trading
            report["dashboard"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    write_json(report_path, report)
    return report
