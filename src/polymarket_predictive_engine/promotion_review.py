from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .utils import now_utc, read_json, safe_float, write_json


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "approved", "promoted"}


def _gap(current: float, required: float) -> float:
    return max(0.0, required - current)


def _policy(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("cohort_promotion", {}) or {}


def _review_row(cfg: EngineConfig, row: dict[str, Any]) -> dict[str, Any]:
    policy = _policy(cfg)
    fills = _num(row.get("buy_fills") or row.get("shadow_fills"))
    settled = _num(row.get("settled_fills") or row.get("shadow_sell_fills") or row.get("sell_fills"))
    pnl = _num(row.get("total_pnl_usdc") or row.get("shadow_total_pnl_usdc"))
    roi = _num(row.get("roi") or row.get("shadow_roi"))
    run_rate = _num(row.get("monthly_run_rate_usdc") or row.get("shadow_monthly_run_rate_usdc"))
    evidence_hours = _num(row.get("evidence_elapsed_hours"))
    cost_basis = _num(row.get("total_buy_cost_usdc") or row.get("shadow_total_buy_cost_usdc"))

    full_required = {
        "fills": _num(policy.get("minimum_filled_orders"), 5.0),
        "settled": _num(policy.get("minimum_settled_orders"), 3.0),
        "pnl": _num(policy.get("minimum_pnl_usdc"), 0.0),
        "roi": _num(policy.get("minimum_roi"), 0.03),
        "hours": _num(policy.get("minimum_tracking_hours_for_promotion"), 2.0),
        "run_rate": _num(policy.get("minimum_monthly_run_rate_usdc"), 20.0),
    }
    probation_required = {
        "fills": _num(policy.get("probationary_minimum_filled_orders"), 3.0),
        "settled": _num(policy.get("probationary_minimum_settled_orders"), 3.0),
        "pnl": _num(policy.get("probationary_minimum_pnl_usdc"), 0.0),
        "roi": _num(policy.get("probationary_minimum_roi"), 0.03),
        "hours": _num(policy.get("probationary_minimum_tracking_hours"), 1.0),
        "run_rate": _num(policy.get("probationary_minimum_monthly_run_rate_usdc"), 20.0),
    }

    full_gates = {
        "fills": fills >= full_required["fills"],
        "settled": settled >= full_required["settled"],
        "pnl": pnl > full_required["pnl"],
        "roi": roi >= full_required["roi"],
        "hours": evidence_hours >= full_required["hours"],
        "run_rate": run_rate >= full_required["run_rate"],
    }
    probation_gates = {
        "fills": fills >= probation_required["fills"],
        "settled": settled >= probation_required["settled"],
        "pnl": pnl > probation_required["pnl"],
        "roi": roi >= probation_required["roi"],
        "hours": evidence_hours >= probation_required["hours"],
        "run_rate": run_rate >= probation_required["run_rate"],
    }

    roi_pnl_needed = 0.0
    if cost_basis > 0:
        roi_pnl_needed = max(0.0, full_required["roi"] * cost_basis - pnl)
    else:
        readiness = row.get("promotion_readiness") if isinstance(row.get("promotion_readiness"), dict) else {}
        roi_pnl_needed = _num(readiness.get("pnl_remaining_usdc"), 0.0)

    status = "collecting_evidence"
    if _bool(row.get("promoted")):
        status = "promoted"
    elif _bool(row.get("probationary")):
        status = "probationary"
    elif all(probation_gates.values()):
        status = "eligible_for_probationary_review"
    elif sum(1 for passed in full_gates.values() if passed) >= max(1, len(full_gates) - 1):
        status = "near_full_promotion"
    elif sum(1 for passed in probation_gates.values() if passed) >= max(1, len(probation_gates) - 1):
        status = "near_probationary"

    missing_full = [name for name, passed in full_gates.items() if not passed]
    missing_probation = [name for name, passed in probation_gates.items() if not passed]

    return {
        "cohort": row.get("signal_cohort") or row.get("cohort") or "unknown",
        "status": status,
        "promoted": _bool(row.get("promoted")),
        "probationary": _bool(row.get("probationary")),
        "buy_fills": fills,
        "settled_fills": settled,
        "total_pnl_usdc": pnl,
        "roi": roi,
        "monthly_run_rate_usdc": run_rate,
        "evidence_elapsed_hours": evidence_hours,
        "promotion_ready_score": row.get("promotion_ready_score"),
        "promotion_ready_checks": row.get("promotion_ready_checks"),
        "full_gates_passed": sum(1 for passed in full_gates.values() if passed),
        "full_gates_total": len(full_gates),
        "probation_gates_passed": sum(1 for passed in probation_gates.values() if passed),
        "probation_gates_total": len(probation_gates),
        "missing_full_gates": missing_full,
        "missing_probation_gates": missing_probation,
        "gaps_to_full": {
            "fills_remaining": _gap(fills, full_required["fills"]),
            "settled_remaining": _gap(settled, full_required["settled"]),
            "pnl_remaining_usdc": max(0.0, full_required["pnl"] - pnl),
            "roi_pnl_equivalent_remaining_usdc": roi_pnl_needed,
            "tracking_hours_remaining": _gap(evidence_hours, full_required["hours"]),
            "monthly_run_rate_remaining_usdc": _gap(run_rate, full_required["run_rate"]),
        },
        "next_action": _next_action(missing_full, missing_probation, roi_pnl_needed),
    }


def _next_action(missing_full: list[str], missing_probation: list[str], roi_pnl_needed: float) -> str:
    if not missing_full:
        return "Eligible for full promotion review; keep gates intact and verify latest forward evidence."
    if not missing_probation:
        return "Eligible for probationary review; keep probe cap small until full promotion gates clear."
    if "roi" in missing_full and roi_pnl_needed > 0:
        return f"Needs about ${roi_pnl_needed:.2f} more net P&L at current evidence cost basis to clear full ROI gate."
    if "fills" in missing_full or "settled" in missing_full:
        return "Needs more settled forward/shadow evidence before promotion."
    if "run_rate" in missing_full:
        return "Positive evidence exists, but monthly run-rate is still below the promotion threshold."
    return "Keep collecting evidence without loosening promotion gates."


def build_promotion_review(cfg: EngineConfig) -> dict[str, Any]:
    payload = read_json(cfg.governance_root / "signal_cohort_pnl.json", default={}) or {}
    cohorts = payload.get("cohorts", []) if isinstance(payload, dict) else []
    if not isinstance(cohorts, list):
        cohorts = []
    rows = [_review_row(cfg, row) for row in cohorts if isinstance(row, dict)]
    rows.sort(
        key=lambda row: (
            1 if row["status"] in {"promoted", "probationary", "eligible_for_probationary_review", "near_full_promotion", "near_probationary"} else 0,
            _num(row.get("full_gates_passed")),
            _num(row.get("total_pnl_usdc")),
            _num(row.get("roi")),
        ),
        reverse=True,
    )
    result = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "policy": _policy(cfg),
        "review": rows,
        "top_actionable": rows[:10],
    }
    write_json(cfg.governance_root / "promotion_review.json", result)
    return result
