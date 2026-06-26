from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .config import EngineConfig
from .utils import now_utc, read_json, safe_float, write_json


def _parse_utc(value: Any) -> datetime:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _tracker_settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("profit_tracking", {}) or {}


def write_profit_target_tracker(cfg: EngineConfig, broker: dict[str, Any]) -> dict[str, Any]:
    """Track actual paper P&L against the $100/month goal from a clean baseline.

    This intentionally measures equity change, not model expected value. If the
    baseline file is missing, the current broker equity becomes the clean
    forward-test starting point without deleting or rewriting the existing paper
    ledger.
    """
    settings = _tracker_settings(cfg)
    target = float(
        settings.get(
            "target_monthly_profit_usdc",
            cfg.raw.get("mispricing_alpha", {}).get("target_monthly_profit_usdc", 100.0),
        )
    )
    minimum_hours = float(settings.get("minimum_tracking_hours_for_on_pace", 24.0))
    baseline_path = cfg.governance_root / str(settings.get("baseline_file", "paper_profit_target_baseline.json"))
    tracker_path = cfg.governance_root / str(settings.get("tracker_file", "paper_profit_target_tracker.json"))

    current_equity = safe_float(broker.get("equity"))
    current_cash = safe_float(broker.get("cash"))
    current_exposure = safe_float(broker.get("total_exposure"))
    timestamp = str(broker.get("generated_at_utc") or now_utc())
    if current_equity is None:
        payload = {
            "status": "missing_equity",
            "generated_at_utc": now_utc(),
            "target_monthly_profit_usdc": target,
            "reason": "broker summary did not include equity",
        }
        write_json(tracker_path, payload)
        return payload

    baseline = read_json(baseline_path, default={}) or {}
    if not isinstance(baseline, dict) or not baseline:
        baseline = {
            "created_at_utc": timestamp,
            "baseline_equity_usdc": current_equity,
            "baseline_cash_usdc": current_cash,
            "baseline_total_exposure_usdc": current_exposure,
            "note": "Clean forward paper-profit baseline. Delete this file only if intentionally starting a new paper evidence window.",
        }
        write_json(baseline_path, baseline)

    baseline_equity = safe_float(baseline.get("baseline_equity_usdc")) or current_equity
    baseline_time = _parse_utc(baseline.get("created_at_utc"))
    current_time = _parse_utc(timestamp)
    elapsed_hours = max(0.0, (current_time - baseline_time).total_seconds() / 3600.0)
    elapsed_days = elapsed_hours / 24.0
    pnl = current_equity - baseline_equity
    monthly_run_rate = (pnl / elapsed_days * 30.0) if elapsed_days > 0 else None

    if pnl >= target:
        status = "target_reached"
    elif elapsed_hours < minimum_hours:
        status = "collecting_forward_evidence"
    elif monthly_run_rate is not None and monthly_run_rate >= target:
        status = "on_pace"
    else:
        status = "not_on_pace"

    payload = {
        "status": status,
        "generated_at_utc": now_utc(),
        "target_monthly_profit_usdc": target,
        "baseline": baseline,
        "current": {
            "timestamp_utc": timestamp,
            "equity_usdc": current_equity,
            "cash_usdc": current_cash,
            "total_exposure_usdc": current_exposure,
        },
        "actual_pnl_since_baseline_usdc": pnl,
        "elapsed_hours": elapsed_hours,
        "monthly_run_rate_usdc": monthly_run_rate,
        "on_pace_by_actual_pnl": bool(monthly_run_rate is not None and monthly_run_rate >= target and elapsed_hours >= minimum_hours),
        "completion_evidence": "Goal is unproven until actual equity P&L, not expected value, reaches or sustains the configured monthly target.",
    }
    write_json(tracker_path, payload)
    return payload
