from __future__ import annotations

from typing import Any

from .goal_planner import build_goal_plan
from .promotion_review import build_promotion_review
from .utils import now_utc, read_json, safe_float, write_json

CORE_WATCHLIST_COHORTS = {
    "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up": "nearest_roi_threshold",
    "exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down": "high_roi_needs_more_samples",
    "exploratory_inverse_historical_rule|crypto_sol_updown_5m|outcome=up": "high_roi_tiny_sample",
}


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved", "promoted"}


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


def _cohort_query(cohort: str) -> str:
    text = cohort.lower()
    if "btc" in text or "bitcoin" in text:
        return "btc updown" if "updown" in text else "bitcoin"
    if "xrp" in text or "ripple" in text:
        return "xrp updown" if "updown" in text else "xrp"
    if "sol" in text or "solana" in text:
        return "solana updown" if "updown" in text else "solana"
    if "eth" in text or "ethereum" in text:
        return "eth updown" if "updown" in text else "ethereum"
    if "tennis" in text:
        return "tennis"
    if "worldcup" in text or "world_cup" in text or "world cup" in text:
        return "world cup"
    return "crypto" if "crypto" in text else "research"


def _thesis(cohort: str, row: dict[str, Any]) -> str:
    if cohort in CORE_WATCHLIST_COHORTS:
        return CORE_WATCHLIST_COHORTS[cohort]
    if cohort.startswith("near_miss_learning|unknown"):
        return "probationary_near_miss_unknown_family_needs_resolution"
    if cohort.startswith("near_miss_learning|"):
        return "near_miss_learning_needs_more_forward_evidence"
    if _bool(row.get("probationary")):
        return "probationary_paper_probe_candidate"
    if _bool(row.get("promoted")):
        return "promoted_candidate_monitor_actual_pnl"
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    if pnl > 0 and roi > 0:
        return "positive_evidence_needs_more_samples_or_gate_clearance"
    return "collecting_evidence"


def _priority(row: dict[str, Any]) -> float:
    score = _num(row.get("promotion_ready_score"))
    checks = max(1.0, _num(row.get("promotion_ready_checks"), 6.0))
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    monthly = _num(row.get("monthly_run_rate_usdc") or row.get("shadow_monthly_run_rate_usdc"))
    fills = _num(row.get("buy_fills") or row.get("shadow_fills"))
    settled = _num(row.get("settled_fills") or row.get("shadow_sell_fills") or row.get("sell_fills"))
    value = 20.0 * (score / checks) + min(roi, 2.0) * 4.0 + min(max(pnl, -10.0), 25.0) * 0.2 + min(monthly, 500.0) * 0.005 + fills * 0.2 + settled * 0.3
    if _bool(row.get("probationary")):
        value += 25.0
    if _bool(row.get("promoted")):
        value += 35.0
    if pnl <= 0 or roi <= 0:
        value *= 0.4
    return value


def _include_focus_row(cohort: str, row: dict[str, Any]) -> bool:
    if cohort in CORE_WATCHLIST_COHORTS:
        return True
    if cohort.startswith("near_miss_learning|"):
        return True
    if _bool(row.get("probationary")) or _bool(row.get("promoted")):
        return True
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    settled = _num(row.get("settled_fills") or row.get("shadow_sell_fills") or row.get("sell_fills"))
    score = _num(row.get("promotion_ready_score"))
    return pnl > 0 and roi > 0 and (settled >= 1 or score >= 4)


def _focus_row(row: dict[str, Any]) -> dict[str, Any]:
    cohort = str(row.get("signal_cohort") or row.get("cohort") or "unknown")
    return {
        "cohort": cohort,
        "thesis": _thesis(cohort, row),
        "status": "promoted" if _bool(row.get("promoted")) else "probationary" if _bool(row.get("probationary")) else "collecting_evidence",
        "priority_score": round(_priority(row), 4),
        "buy_fills": row.get("buy_fills", row.get("shadow_fills")),
        "settled_fills": row.get("settled_fills", row.get("shadow_sell_fills", row.get("sell_fills"))),
        "total_pnl_usdc": row.get("total_pnl_usdc", row.get("shadow_total_pnl_usdc")),
        "roi": row.get("roi", row.get("shadow_roi")),
        "monthly_run_rate_usdc": row.get("monthly_run_rate_usdc", row.get("shadow_monthly_run_rate_usdc")),
        "promotion_ready_score": row.get("promotion_ready_score"),
        "promotion_ready_checks": row.get("promotion_ready_checks"),
        "gap": _readiness_gap(row),
        "recommended_collection_query": _cohort_query(cohort),
        "do_not_trade_reason": row.get("promotion_reason", "not promoted yet"),
    }


def _load_cohort_rows(cfg) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for filename in ("signal_cohort_pnl.json", "shadow_signal_cohort_pnl.json"):
        payload = read_json(cfg.governance_root / filename, default={}) or {}
        cohorts = payload.get("cohorts", []) if isinstance(payload, dict) else []
        if isinstance(cohorts, list):
            rows.extend(row for row in cohorts if isinstance(row, dict))
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        cohort = str(row.get("signal_cohort") or row.get("cohort") or "unknown")
        existing = deduped.get(cohort)
        if existing is None or _priority(row) > _priority(existing):
            deduped[cohort] = row
    return list(deduped.values())


def build_research_focus(cfg) -> dict[str, Any]:
    governance = cfg.governance_root
    focus_rows = [_focus_row(row) for row in _load_cohort_rows(cfg) if _include_focus_row(str(row.get("signal_cohort") or row.get("cohort") or "unknown"), row)]
    focus_rows.sort(key=lambda item: _num(item.get("priority_score")), reverse=True)

    promotion_review = build_promotion_review(cfg)
    goal_plan = build_goal_plan(cfg)

    if focus_rows:
        top = focus_rows[0]
        next_action = (
            f"Focus discovery on {top['recommended_collection_query']} for {top['cohort']}; "
            f"thesis={top['thesis']}; remaining gates={top.get('gap', {})}."
        )
    else:
        next_action = "Keep collecting shadow evidence; no positive or probationary cohort has enough fresh evidence yet."

    collection_queries = []
    for row in focus_rows:
        query = str(row.get("recommended_collection_query") or "").strip()
        if query and query not in collection_queries:
            collection_queries.append(query)
    if not collection_queries:
        collection_queries = ["btc updown", "xrp updown", "solana updown"]

    payload = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "summary": next_action,
        "watchlist": focus_rows,
        "collection_queries": collection_queries,
        "promotion_review": {
            "status": promotion_review.get("status"),
            "top_actionable": promotion_review.get("top_actionable", [])[:10],
        },
        "goal_plan": {
            "status": goal_plan.get("status"),
            "main_gap": goal_plan.get("main_gap"),
            "recommended_action": goal_plan.get("recommended_action"),
            "target_monthly_profit_usdc": goal_plan.get("target_monthly_profit_usdc"),
            "actual_pnl_since_baseline_usdc": goal_plan.get("actual_pnl_since_baseline_usdc"),
            "required_daily_from_here_usdc": goal_plan.get("required_daily_from_here_usdc"),
        },
    }
    write_json(governance / "research_focus.json", payload)
    return payload
