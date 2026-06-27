from __future__ import annotations

from typing import Any

from .utils import now_utc, read_json, safe_float, write_json

WATCHLIST_COHORTS = {
    "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up": "nearest_roi_threshold",
    "exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down": "high_roi_needs_more_samples",
    "exploratory_inverse_historical_rule|crypto_sol_updown_5m|outcome=up": "high_roi_tiny_sample",
}


def _num(value: Any) -> float:
    return float(safe_float(value) or 0.0)


def _readiness_gap(row: dict[str, Any]) -> dict[str, Any]:
    readiness = row.get("promotion_readiness")
    if not isinstance(readiness, dict):
        readiness = {}
    return {
        "fills_remaining": readiness.get("fills_remaining", ""),
        "settled_fills_remaining": readiness.get("settled_fills_remaining", ""),
        "pnl_remaining_usdc": readiness.get("pnl_remaining_usdc", ""),
        "roi_remaining": readiness.get("roi_remaining", ""),
        "monthly_run_rate_remaining_usdc": readiness.get("monthly_run_rate_remaining_usdc", ""),
        "tracking_hours_remaining": readiness.get("tracking_hours_remaining", ""),
    }


def _priority(row: dict[str, Any]) -> float:
    score = _num(row.get("promotion_ready_score"))
    pnl = _num(row.get("total_pnl_usdc"))
    roi = _num(row.get("roi"))
    monthly = _num(row.get("monthly_run_rate_usdc"))
    fills = _num(row.get("buy_fills"))
    settled = _num(row.get("settled_fills"))
    return score * 10.0 + min(roi, 2.0) * 4.0 + min(max(pnl, -10.0), 25.0) * 0.2 + min(monthly, 500.0) * 0.005 + fills * 0.2 + settled * 0.3


def build_research_focus(cfg) -> dict[str, Any]:
    governance = cfg.governance_root
    cohort_summary = read_json(governance / "signal_cohort_pnl.json", default={}) or {}
    cohorts = cohort_summary.get("cohorts", []) if isinstance(cohort_summary, dict) else []
    focus_rows: list[dict[str, Any]] = []
    if not isinstance(cohorts, list):
        cohorts = []

    for row in cohorts:
        if not isinstance(row, dict):
            continue
        cohort = str(row.get("signal_cohort") or "")
        if cohort in WATCHLIST_COHORTS:
            focus_rows.append(
                {
                    "cohort": cohort,
                    "thesis": WATCHLIST_COHORTS[cohort],
                    "status": "promoted" if row.get("promoted") else "probationary" if row.get("probationary") else "collecting_evidence",
                    "priority_score": round(_priority(row), 4),
                    "buy_fills": row.get("buy_fills"),
                    "settled_fills": row.get("settled_fills"),
                    "total_pnl_usdc": row.get("total_pnl_usdc"),
                    "roi": row.get("roi"),
                    "monthly_run_rate_usdc": row.get("monthly_run_rate_usdc"),
                    "promotion_ready_score": row.get("promotion_ready_score"),
                    "promotion_ready_checks": row.get("promotion_ready_checks"),
                    "gap": _readiness_gap(row),
                    "recommended_collection_query": "btc updown" if "btc" in cohort else "xrp updown" if "xrp" in cohort else "solana updown" if "sol" in cohort else "crypto",
                    "do_not_trade_reason": row.get("promotion_reason", "not promoted yet"),
                }
            )

    focus_rows.sort(key=lambda item: _num(item.get("priority_score")), reverse=True)
    if focus_rows:
        top = focus_rows[0]
        next_action = (
            f"Focus discovery on {top['recommended_collection_query']} for {top['cohort']}; "
            f"remaining gates: {top.get('gap', {})}."
        )
    else:
        next_action = "Keep collecting shadow evidence for BTC, XRP, and SOL Up/Down; no watchlist cohort has enough fresh evidence yet."

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "summary": next_action,
        "watchlist": focus_rows,
        "collection_queries": ["xrp updown", "solana updown", "btc updown"],
    }
    write_json(governance / "research_focus.json", payload)
    return payload
