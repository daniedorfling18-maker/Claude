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


def _status_for_pnl(
    *,
    pnl: float,
    target: float,
    elapsed_hours: float,
    monthly_run_rate: float | None,
    minimum_hours: float,
    verified_evidence_ready: bool,
) -> str:
    if pnl >= target and verified_evidence_ready:
        return "target_reached"
    if elapsed_hours < minimum_hours:
        return "collecting_forward_evidence"
    if not verified_evidence_ready and monthly_run_rate is not None and monthly_run_rate >= target:
        return "collecting_verified_paper_evidence"
    if monthly_run_rate is not None and monthly_run_rate >= target and verified_evidence_ready:
        return "on_pace"
    return "not_on_pace"


def _paper_round_trip_audit(cfg: EngineConfig) -> dict[str, Any]:
    payload = read_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        default={},
    )
    return payload if isinstance(payload, dict) else {}


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
    minimum_audited_round_trips = int(settings.get("minimum_audited_round_trips_for_on_pace", 5))
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
    raw_pnl = current_equity - baseline_equity
    raw_monthly_run_rate = (raw_pnl / elapsed_days * 30.0) if elapsed_days > 0 else None

    round_trip_audit = _paper_round_trip_audit(cfg)
    audited_pnl = safe_float(round_trip_audit.get("audited_baseline_realized_pnl_usdc"))
    audited_round_trips = int(safe_float(round_trip_audit.get("audited_baseline_closed_round_trips")) or 0)
    proof_fields_available = "baseline_proof_verified_realized_pnl_usdc" in round_trip_audit
    proof_pnl = safe_float(round_trip_audit.get("baseline_proof_verified_realized_pnl_usdc"))
    proof_round_trips = int(safe_float(round_trip_audit.get("baseline_proof_verified_closed_round_trips")) or 0)
    proof_entry_missing = int(safe_float(round_trip_audit.get("baseline_proof_entry_snapshot_missing_round_trips")) or 0)
    proof_exit_missing = int(safe_float(round_trip_audit.get("baseline_proof_exit_snapshot_missing_round_trips")) or 0)
    baseline_round_trips = int(safe_float(round_trip_audit.get("baseline_closed_round_trips")) or 0)
    all_time_quote_conflicts = int(safe_float(round_trip_audit.get("quote_conflict_round_trips")) or 0)
    all_time_quote_unverified = int(safe_float(round_trip_audit.get("quote_unverified_round_trips")) or 0)
    quote_conflicts = (
        int(safe_float(round_trip_audit.get("baseline_quote_conflict_round_trips")) or 0)
        if "baseline_quote_conflict_round_trips" in round_trip_audit
        else all_time_quote_conflicts
    )
    quote_unverified = (
        int(safe_float(round_trip_audit.get("baseline_quote_unverified_round_trips")) or 0)
        if "baseline_quote_unverified_round_trips" in round_trip_audit
        else all_time_quote_unverified
    )
    audited_available = audited_pnl is not None
    proof_available = proof_fields_available and proof_pnl is not None
    decision_pnl = float(proof_pnl) if proof_available else float(audited_pnl) if audited_available else raw_pnl
    decision_monthly_run_rate = (decision_pnl / elapsed_days * 30.0) if elapsed_days > 0 else None
    decision_pnl_source = (
        "proof_verified_entry_exit_quotes"
        if proof_available
        else "audited_quote_consistent_exits"
        if audited_available
        else "raw_account_equity"
    )
    evidence_round_trips = proof_round_trips if proof_available else audited_round_trips
    evidence_blocker_name = "proof_verified_round_trips" if proof_available else "audited_round_trips"
    pnl_audit_state = (
        "raw_pnl_contains_quote_conflicts"
        if quote_conflicts > 0
        else "raw_pnl_contains_unverified_quotes"
        if quote_unverified > 0
        else "quote_consistent_but_entry_exit_proof_missing"
        if proof_available and (proof_entry_missing > 0 or proof_exit_missing > 0)
        else "proof_verified_entry_exit_quotes"
        if proof_available
        else "quote_consistent"
        if audited_available
        else "audit_not_available"
    )
    verified_evidence_ready = bool(
        (proof_available or audited_available)
        and evidence_round_trips >= minimum_audited_round_trips
        and quote_conflicts == 0
        and quote_unverified == 0
        and (not proof_available or (proof_entry_missing == 0 and proof_exit_missing == 0))
    )
    if verified_evidence_ready:
        proof_status = "verified_entry_exit_quote_coverage" if proof_available else "verified_quote_consistent"
        proof_blockers: list[str] = []
    else:
        proof_blockers = []
        if not (proof_available or audited_available):
            proof_blockers.append("paper_round_trip_audit_missing")
        if evidence_round_trips < minimum_audited_round_trips:
            proof_blockers.append(
                f"needs_{minimum_audited_round_trips}_{evidence_blocker_name}_has_{evidence_round_trips}"
            )
        if quote_conflicts > 0:
            proof_blockers.append(f"quote_conflict_round_trips_{quote_conflicts}")
        if quote_unverified > 0:
            proof_blockers.append(f"quote_unverified_round_trips_{quote_unverified}")
        if proof_available and proof_entry_missing > 0:
            proof_blockers.append(f"entry_snapshot_missing_round_trips_{proof_entry_missing}")
        if proof_available and proof_exit_missing > 0:
            proof_blockers.append(f"exit_snapshot_missing_round_trips_{proof_exit_missing}")
        proof_status = "unverified_paper_run_rate"
    status = _status_for_pnl(
        pnl=decision_pnl,
        target=target,
        elapsed_hours=elapsed_hours,
        monthly_run_rate=decision_monthly_run_rate,
        minimum_hours=minimum_hours,
        verified_evidence_ready=verified_evidence_ready,
    )

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
        "actual_pnl_since_baseline_usdc": raw_pnl,
        "raw_account_pnl_since_baseline_usdc": raw_pnl,
        "raw_account_monthly_run_rate_usdc": raw_monthly_run_rate,
        "audited_pnl_since_baseline_usdc": audited_pnl,
        "audited_round_trips_since_baseline": audited_round_trips,
        "proof_verified_pnl_since_baseline_usdc": proof_pnl,
        "proof_verified_round_trips_since_baseline": proof_round_trips if proof_fields_available else None,
        "proof_entry_snapshot_missing_round_trips": proof_entry_missing if proof_fields_available else None,
        "proof_exit_snapshot_missing_round_trips": proof_exit_missing if proof_fields_available else None,
        "baseline_round_trips_since_baseline": baseline_round_trips,
        "minimum_audited_round_trips_for_on_pace": minimum_audited_round_trips,
        "decision_pnl_usdc": decision_pnl,
        "decision_pnl_source": decision_pnl_source,
        "elapsed_hours": elapsed_hours,
        "monthly_run_rate_usdc": decision_monthly_run_rate,
        "decision_monthly_run_rate_usdc": decision_monthly_run_rate,
        "pnl_audit_state": pnl_audit_state,
        "profit_target_proof_status": proof_status,
        "profit_target_proof_blockers": proof_blockers,
        "verified_evidence_ready": verified_evidence_ready,
        "quote_conflict_round_trips": quote_conflicts,
        "quote_unverified_round_trips": quote_unverified,
        "all_time_quote_conflict_round_trips": all_time_quote_conflicts,
        "all_time_quote_unverified_round_trips": all_time_quote_unverified,
        "on_pace_by_actual_pnl": bool(
            raw_monthly_run_rate is not None
            and raw_monthly_run_rate >= target
            and elapsed_hours >= minimum_hours
        ),
        "on_pace_by_decision_pnl": bool(
            decision_monthly_run_rate is not None
            and decision_monthly_run_rate >= target
            and elapsed_hours >= minimum_hours
            and verified_evidence_ready
        ),
        "completion_evidence": (
            "Goal is unproven until proof-verified paper P&L, not expected value or raw quote-conflicted ledger equity, "
            "reaches or sustains the configured monthly target with enough independent entry and exit quote-supported round trips."
        ),
    }
    write_json(tracker_path, payload)
    return payload
