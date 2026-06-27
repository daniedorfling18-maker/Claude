from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _cohorts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("cohorts", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _positive_actionable_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actionable: list[dict[str, Any]] = []
    for row in rows:
        pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
        roi = _num(row.get("roi") or row.get("shadow_roi"))
        settled = _num(row.get("settled_fills") or row.get("shadow_sell_fills") or row.get("sell_fills"))
        score = _num(row.get("promotion_ready_score"))
        checks = max(1.0, _num(row.get("promotion_ready_checks"), 6.0))
        if pnl > 0 and roi > 0 and settled >= 1 and score >= max(1.0, checks - 2):
            actionable.append(row)
    actionable.sort(
        key=lambda row: (
            _num(row.get("promotion_ready_score")),
            _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc")),
            _num(row.get("roi") or row.get("shadow_roi")),
        ),
        reverse=True,
    )
    return actionable


def build_goal_plan(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("profit_tracking", {}) or {}
    target_monthly = _num(settings.get("target_monthly_profit_usdc"), _num(cfg.raw.get("mispricing_alpha", {}).get("target_monthly_profit_usdc"), 100.0))
    target_daily = target_monthly / 30.0 if target_monthly else 0.0
    target_weekly = target_monthly * 7.0 / 30.0 if target_monthly else 0.0

    tracker = read_json(cfg.governance_root / str(settings.get("tracker_file", "paper_profit_target_tracker.json")), default={}) or {}
    broker = read_json(cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json", default={}) or {}
    cohort_payload = read_json(cfg.governance_root / "signal_cohort_pnl.json", default={}) or {}
    rows = _cohorts(cohort_payload)
    approved_signals = read_csv_rows(cfg.output_root / "polymarket_predictions" / "trade_signals.csv")
    rejected_signals = read_csv_rows(cfg.output_root / "polymarket_predictions" / "rejected_signals.csv")

    actual_pnl = _num(
        tracker.get("actual_pnl_since_baseline_usdc"),
        _num((tracker.get("current") or {}).get("equity_usdc"), _num(broker.get("equity"), 1000.0))
        - _num((tracker.get("baseline") or {}).get("baseline_equity_usdc"), 1000.0),
    )
    elapsed_hours = _num(tracker.get("elapsed_hours"))
    elapsed_days = elapsed_hours / 24.0 if elapsed_hours else 0.0
    prorated_target = target_daily * elapsed_days
    required_remaining_monthly = max(0.0, target_monthly - actual_pnl)
    required_daily_from_here = required_remaining_monthly / max(1.0, 30.0 - elapsed_days) if target_monthly else 0.0

    promoted = [row for row in rows if str(row.get("promoted")).lower() == "true" or row.get("promoted") is True]
    probationary = [row for row in rows if str(row.get("probationary")).lower() == "true" or row.get("probationary") is True]
    positive_watchlist = _positive_actionable_rows(rows)

    if approved_signals:
        main_gap = "approved paper signals exist; broker should process them unless duplicate/risk/pause controls intervene"
    elif probationary:
        main_gap = "probationary cohort exists, but current candidates still fail edge/liquidity/timing gates"
    elif positive_watchlist:
        main_gap = "positive evidence exists, but no current candidate has passed all paper-entry gates"
    else:
        main_gap = "no promoted/probationary edge cohort is ready to support the $100 paper-profit attempt"

    result = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "target_monthly_profit_usdc": target_monthly,
        "target_daily_profit_usdc": target_daily,
        "target_weekly_profit_usdc": target_weekly,
        "actual_pnl_since_baseline_usdc": actual_pnl,
        "elapsed_hours": elapsed_hours,
        "elapsed_days": elapsed_days,
        "prorated_target_usdc": prorated_target,
        "on_pace_by_actual_pnl": actual_pnl >= prorated_target if elapsed_days > 0 else False,
        "required_remaining_monthly_usdc": required_remaining_monthly,
        "required_daily_from_here_usdc": required_daily_from_here,
        "approved_signals": len(approved_signals),
        "rejected_signals": len(rejected_signals),
        "promoted_cohorts": [row.get("signal_cohort") for row in promoted],
        "probationary_cohorts": [row.get("signal_cohort") for row in probationary],
        "positive_watchlist": [
            {
                "cohort": row.get("signal_cohort"),
                "pnl": row.get("total_pnl_usdc", row.get("shadow_total_pnl_usdc")),
                "roi": row.get("roi", row.get("shadow_roi")),
                "run_rate": row.get("monthly_run_rate_usdc", row.get("shadow_monthly_run_rate_usdc")),
                "score": row.get("promotion_ready_score"),
                "checks": row.get("promotion_ready_checks"),
            }
            for row in positive_watchlist[:10]
        ],
        "main_gap": main_gap,
        "recommended_action": _recommended_action(approved_signals, probationary, positive_watchlist),
    }
    write_json(cfg.governance_root / "paper_profit_goal_plan.json", result)
    return result


def _recommended_action(approved_signals: list[dict[str, str]], probationary: list[dict[str, Any]], positive_watchlist: list[dict[str, Any]]) -> str:
    if approved_signals:
        return "Monitor paper broker fills, realised P&L, and duplicate/risk rejections before changing any gate."
    if probationary:
        return "Keep the probationary cap small; focus scanner cycles on liquid actionable windows for the probationary cohort."
    if positive_watchlist:
        return "Prioritise collection for the positive watchlist and improve labels/liquidity filtering before expecting paper entries."
    return "Keep collecting shadow evidence and build cleaner labels; do not lower trade gates to chase the target."
