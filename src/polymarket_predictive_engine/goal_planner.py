from __future__ import annotations

from typing import Any

from .config import EngineConfig
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_json


def _num(value: Any, default: float = 0.0) -> float:
    parsed = safe_float(value)
    return default if parsed is None else float(parsed)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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


def _price_action_goal_state(payload: dict[str, Any], *, target_monthly: float) -> dict[str, Any]:
    """Summarise the settlement-independent route to the monthly paper-profit goal.

    Settlement evidence answers "did the final outcome model beat the market?".
    Price-action evidence answers the more tradeable question: "could we buy at
    the current ask and later sell or mark at an executable bid for profit?".
    The latter can move the $100/month paper objective forward before markets
    resolve, while still respecting the normal cohort-promotion gates.
    """
    if not payload:
        return {
            "status": "missing",
            "tradeable_price_change_path": True,
            "settlement_required_for_this_milestone": False,
            "evidence_type": "buy_at_ask_sell_or_mark_at_bid",
            "state": "waiting_for_price_action_feedback",
            "best_repricing_monthly_run_rate_usdc": 0.0,
            "repricing_goal_gap_usdc": target_monthly,
            "next_action": "Build price-action feedback from websocket bid/ask evidence before expecting paper entries.",
        }

    best_forward_paper = _num(payload.get("best_forward_paper_monthly_run_rate_usdc"))
    best_positive = _num(payload.get("best_positive_monthly_run_rate_usdc"))
    best_repricing = max(best_forward_paper, best_positive)
    forward_paper_positive = int(_num(payload.get("forward_paper_positive_cohorts")))
    forward_shadow_positive = int(_num(payload.get("forward_shadow_positive_cohorts")))
    paper_confirmation = int(_num(payload.get("paper_confirmation_candidates")))
    promotion_candidates = int(_num(payload.get("promotion_candidates")))
    positive_collect = int(_num(payload.get("positive_collect_candidates")))
    suppressed = int(_num(payload.get("suppressed_candidates")))

    if forward_paper_positive > 0 and best_forward_paper >= target_monthly:
        state = "forward_paper_on_monthly_target"
        next_action = "Keep entries paper-only, expand sample size carefully, and prepare promotion review if the cohort remains positive."
    elif forward_paper_positive > 0:
        state = "forward_paper_positive_below_target"
        next_action = "Collect more forward paper exits and improve selection until repricing run-rate can plausibly reach the monthly target."
    elif paper_confirmation > 0 or forward_shadow_positive > 0:
        state = "trusted_shadow_needs_paper_confirmation"
        next_action = "Route trusted positive shadow repricing cohorts into tiny governed paper-confirmation probes."
    elif promotion_candidates > 0:
        state = "candidate_needs_paper_bridge"
        next_action = "Use the governed price-action paper bridge to convert approved bid/ask cohorts into paper-only signals."
    elif positive_collect > 0:
        state = "collecting_positive_bid_ask_evidence"
        next_action = "Prioritise websocket collection for positive repricing cohorts until closed bid/ask gates clear."
    elif suppressed > 0:
        state = "negative_repricing_broaden_search"
        next_action = "Suppress negative price-action cohorts and broaden discovery instead of forcing paper trades."
    else:
        state = "collecting_initial_price_action_evidence"
        next_action = "Keep collecting websocket bid/ask marks; no cohort has enough executable repricing evidence yet."

    return {
        "status": payload.get("status", "unknown"),
        "tradeable_price_change_path": True,
        "settlement_required_for_this_milestone": False,
        "evidence_type": "buy_at_ask_sell_or_mark_at_bid",
        "state": state,
        "learning_state": payload.get("learning_state"),
        "next_action": next_action,
        "feedback_next_action": payload.get("next_action"),
        "target_monthly_profit_usdc": target_monthly,
        "best_repricing_monthly_run_rate_usdc": best_repricing,
        "repricing_goal_gap_usdc": max(target_monthly - best_repricing, 0.0),
        "best_forward_paper_monthly_run_rate_usdc": best_forward_paper,
        "forward_paper_goal_gap_usdc": max(target_monthly - best_forward_paper, 0.0),
        "forward_paper_positive_cohorts": forward_paper_positive,
        "forward_shadow_positive_cohorts": forward_shadow_positive,
        "paper_confirmation_candidates": paper_confirmation,
        "promotion_candidates": promotion_candidates,
        "positive_collect_candidates": positive_collect,
        "suppressed_candidates": suppressed,
        "collection_queries": payload.get("collection_queries", []),
        "top_cohorts": _price_action_goal_cohorts(payload),
    }


def _price_action_goal_cohorts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the cohorts most relevant to the profit goal, not the noisiest rows.

    The raw feedback priority can put large-sample suppressed microstructure
    failures above smaller positive candidates. That is useful for diagnostics
    but confusing in the goal panel, where the user needs the route forward.
    """
    rows: list[dict[str, Any]] = []

    def _rows(key: str) -> list[dict[str, Any]]:
        value = payload.get(key)
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    def _not_suppressed(row: dict[str, Any]) -> bool:
        return str(row.get("action") or "") != "suppress_until_new_thesis"

    rows.extend(
        row
        for row in _rows("forward_paper_preview")
        if _not_suppressed(row) and _num(row.get("forward_paper_pnl_usdc")) > 0 and _num(row.get("forward_paper_roi")) > 0
    )
    rows.extend(row for row in _rows("paper_confirmation_preview") if _not_suppressed(row))
    rows.extend(row for row in _rows("promotion_candidate_preview") if _not_suppressed(row))
    rows.extend(row for row in _rows("positive_collect_preview") if _not_suppressed(row))
    rows.extend(row for row in _rows("forward_paper_preview") if _not_suppressed(row))
    if not rows:
        top = payload.get("top_cohorts")
        if isinstance(top, list):
            rows.extend(
                row
                for row in top
                if isinstance(row, dict) and str(row.get("action") or "") != "suppress_until_new_thesis"
            )
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            str(row.get("cohort") or row.get("signal_cohort") or ""),
            str(row.get("source") or ""),
            str(row.get("rule_id") or ""),
            str(row.get("exit_policy_id") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped[:10]


def _price_action_gap(price_action_state: dict[str, Any]) -> str:
    state = str(price_action_state.get("state") or "")
    if state == "forward_paper_on_monthly_target":
        return "price-change forward paper P&L is currently on the $100/month run-rate; need more sample size before promotion"
    if state == "forward_paper_positive_below_target":
        return "price-change forward paper P&L is positive but still below the $100/month run-rate"
    if state == "trusted_shadow_needs_paper_confirmation":
        return "trusted shadow repricing evidence exists; it needs governed paper-confirmation probes"
    if state == "candidate_needs_paper_bridge":
        return "approved bid/ask price-action candidates exist; the paper bridge must convert them into tiny paper probes"
    if state == "collecting_positive_bid_ask_evidence":
        return "positive repricing evidence exists, but bid/ask cohorts still need more closed round trips"
    if state == "negative_repricing_broaden_search":
        return "recent repricing cohorts are negative; broaden discovery instead of forcing entries"
    return ""


def build_goal_plan(cfg: EngineConfig) -> dict[str, Any]:
    settings = cfg.raw.get("profit_tracking", {}) or {}
    target_monthly = _num(settings.get("target_monthly_profit_usdc"), _num(cfg.raw.get("mispricing_alpha", {}).get("target_monthly_profit_usdc"), 100.0))
    target_daily = target_monthly / 30.0 if target_monthly else 0.0
    target_weekly = target_monthly * 7.0 / 30.0 if target_monthly else 0.0

    tracker = _dict(read_json(cfg.governance_root / str(settings.get("tracker_file", "paper_profit_target_tracker.json")), default={}) or {})
    broker = _dict(read_json(cfg.output_root / "polymarket_portfolio" / "paper_trading_summary.json", default={}) or {})
    paper_round_trip = _dict(read_json(cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json", default={}) or {})
    cohort_payload = _dict(read_json(cfg.governance_root / "signal_cohort_pnl.json", default={}) or {})
    price_action_feedback = _dict(read_json(cfg.governance_root / "price_action_feedback.json", default={}) or {})
    price_action_state = _price_action_goal_state(price_action_feedback, target_monthly=target_monthly)
    rows = _cohorts(cohort_payload)
    approved_signals = read_csv_rows(cfg.output_root / "polymarket_predictions" / "trade_signals.csv")
    rejected_signals = read_csv_rows(cfg.output_root / "polymarket_predictions" / "rejected_signals.csv")

    current = _dict(tracker.get("current"))
    baseline = _dict(tracker.get("baseline"))
    actual_pnl = _num(
        tracker.get("actual_pnl_since_baseline_usdc"),
        _num(current.get("equity_usdc"), _num(broker.get("equity"), 1000.0))
        - _num(baseline.get("baseline_equity_usdc"), 1000.0),
    )
    elapsed_hours = _num(tracker.get("elapsed_hours"))
    elapsed_days = elapsed_hours / 24.0 if elapsed_hours else 0.0
    audited_pnl = safe_float(paper_round_trip.get("audited_baseline_realized_pnl_usdc"))
    audited_available = audited_pnl is not None
    proof_fields_available = "baseline_proof_verified_realized_pnl_usdc" in paper_round_trip
    proof_pnl = safe_float(paper_round_trip.get("baseline_proof_verified_realized_pnl_usdc"))
    proof_available = proof_fields_available and proof_pnl is not None
    decision_pnl = float(proof_pnl) if proof_available else float(audited_pnl) if audited_available else actual_pnl
    decision_monthly_run_rate = (decision_pnl / elapsed_days * 30.0) if elapsed_days > 0 else None
    minimum_audited_round_trips = int(_num(settings.get("minimum_audited_round_trips_for_on_pace"), 5.0))
    audited_round_trips = int(_num(paper_round_trip.get("audited_baseline_closed_round_trips")))
    proof_round_trips = int(_num(paper_round_trip.get("baseline_proof_verified_closed_round_trips")))
    proof_entry_missing = int(_num(paper_round_trip.get("baseline_proof_entry_snapshot_missing_round_trips")))
    proof_exit_missing = int(_num(paper_round_trip.get("baseline_proof_exit_snapshot_missing_round_trips")))
    evidence_round_trips = proof_round_trips if proof_available else audited_round_trips
    evidence_blocker_name = "proof_verified_round_trips" if proof_available else "audited_round_trips"
    decision_pnl_source = (
        "proof_verified_entry_exit_quotes"
        if proof_available
        else "audited_quote_consistent_exits"
        if audited_available
        else "raw_account_equity"
    )
    baseline_round_trips = int(_num(paper_round_trip.get("baseline_closed_round_trips")))
    all_time_quote_conflicts = int(_num(paper_round_trip.get("quote_conflict_round_trips")))
    all_time_quote_unverified = int(_num(paper_round_trip.get("quote_unverified_round_trips")))
    quote_conflicts = (
        int(_num(paper_round_trip.get("baseline_quote_conflict_round_trips")))
        if "baseline_quote_conflict_round_trips" in paper_round_trip
        else all_time_quote_conflicts
    )
    quote_unverified = (
        int(_num(paper_round_trip.get("baseline_quote_unverified_round_trips")))
        if "baseline_quote_unverified_round_trips" in paper_round_trip
        else all_time_quote_unverified
    )
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
    proof_blockers: list[str] = []
    if not verified_evidence_ready:
        if not (proof_available or audited_available):
            proof_blockers.append("paper_round_trip_audit_missing")
        if evidence_round_trips < minimum_audited_round_trips:
            proof_blockers.append(f"needs_{minimum_audited_round_trips}_{evidence_blocker_name}_has_{evidence_round_trips}")
        if quote_conflicts > 0:
            proof_blockers.append(f"quote_conflict_round_trips_{quote_conflicts}")
        if quote_unverified > 0:
            proof_blockers.append(f"quote_unverified_round_trips_{quote_unverified}")
        if proof_available and proof_entry_missing > 0:
            proof_blockers.append(f"entry_snapshot_missing_round_trips_{proof_entry_missing}")
        if proof_available and proof_exit_missing > 0:
            proof_blockers.append(f"exit_snapshot_missing_round_trips_{proof_exit_missing}")
    profit_target_proof_status = (
        "verified_entry_exit_quote_coverage"
        if verified_evidence_ready and proof_available
        else "verified_quote_consistent"
        if verified_evidence_ready
        else "unverified_paper_run_rate"
    )
    prorated_target = target_daily * elapsed_days
    required_remaining_monthly = max(0.0, target_monthly - decision_pnl)
    required_daily_from_here = required_remaining_monthly / max(1.0, 30.0 - elapsed_days) if target_monthly else 0.0

    promoted = [row for row in rows if str(row.get("promoted")).lower() == "true" or row.get("promoted") is True]
    probationary = [row for row in rows if str(row.get("probationary")).lower() == "true" or row.get("probationary") is True]
    positive_watchlist = _positive_actionable_rows(rows)

    price_action_gap = _price_action_gap(price_action_state)
    if audited_available and (quote_conflicts > 0 or quote_unverified > 0) and decision_pnl < actual_pnl:
        main_gap = "raw paper P&L contains quote-conflicted/unverified rows; the $100 route must use audited quote-consistent P&L"
    elif proof_available and (proof_entry_missing > 0 or proof_exit_missing > 0):
        main_gap = "quote-consistent paper P&L still lacks full entry/exit quote support; the $100 route must use proof-verified buy/sell prices"
    elif decision_monthly_run_rate is not None and decision_monthly_run_rate >= target_monthly and not verified_evidence_ready:
        main_gap = "paper run-rate is mathematically on pace, but the $100 route is not yet verified by enough quote-consistent paper round trips"
    elif approved_signals:
        main_gap = "approved paper signals exist; broker should process them unless duplicate/risk/pause controls intervene"
    elif price_action_gap:
        main_gap = price_action_gap
    elif probationary:
        main_gap = "probationary cohort exists, but current candidates still fail edge/liquidity/timing gates"
    elif positive_watchlist:
        main_gap = "positive evidence exists, but no current candidate has passed all paper-entry gates"
    else:
        main_gap = "no promoted/probationary edge cohort is ready to support the $100 paper-profit attempt"

    recommended_action = (
        "Keep the data, but reset trust to audited quote-consistent paper exits; collect fresh clean exits before counting progress toward $100/month."
        if audited_available and (quote_conflicts > 0 or quote_unverified > 0) and decision_pnl < actual_pnl
        else "Keep the fills as learning context, but collect entry and exit bid/ask snapshots before counting them as profit-target proof."
        if proof_available and (proof_entry_missing > 0 or proof_exit_missing > 0)
        else "Collect more quote-consistent paper exits before treating the run-rate as verified progress toward $100/month."
        if decision_monthly_run_rate is not None and decision_monthly_run_rate >= target_monthly and not verified_evidence_ready
        else _recommended_action(approved_signals, probationary, positive_watchlist, price_action_state)
    )

    result = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "target_monthly_profit_usdc": target_monthly,
        "target_daily_profit_usdc": target_daily,
        "target_weekly_profit_usdc": target_weekly,
        "actual_pnl_since_baseline_usdc": actual_pnl,
        "raw_account_pnl_since_baseline_usdc": actual_pnl,
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
        "decision_monthly_run_rate_usdc": decision_monthly_run_rate,
        "pnl_audit_state": pnl_audit_state,
        "profit_target_proof_status": profit_target_proof_status,
        "profit_target_proof_blockers": proof_blockers,
        "verified_evidence_ready": verified_evidence_ready,
        "quote_conflict_round_trips": quote_conflicts,
        "quote_unverified_round_trips": quote_unverified,
        "all_time_quote_conflict_round_trips": all_time_quote_conflicts,
        "all_time_quote_unverified_round_trips": all_time_quote_unverified,
        "elapsed_hours": elapsed_hours,
        "elapsed_days": elapsed_days,
        "prorated_target_usdc": prorated_target,
        "on_pace_by_actual_pnl": actual_pnl >= prorated_target if elapsed_days > 0 else False,
        "on_pace_by_decision_pnl": decision_pnl >= prorated_target and verified_evidence_ready if elapsed_days > 0 else False,
        "required_remaining_monthly_usdc": required_remaining_monthly,
        "required_daily_from_here_usdc": required_daily_from_here,
        "approved_signals": len(approved_signals),
        "rejected_signals": len(rejected_signals),
        "promoted_cohorts": [row.get("signal_cohort") for row in promoted],
        "probationary_cohorts": [row.get("signal_cohort") for row in probationary],
        "price_action_goal_state": price_action_state,
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
        "recommended_action": recommended_action,
    }
    write_json(cfg.governance_root / "paper_profit_goal_plan.json", result)
    return result


def _recommended_action(
    approved_signals: list[dict[str, str]],
    probationary: list[dict[str, Any]],
    positive_watchlist: list[dict[str, Any]],
    price_action_state: dict[str, Any] | None = None,
) -> str:
    if approved_signals:
        return "Monitor paper broker fills, realised P&L, and duplicate/risk rejections before changing any gate."
    state = str((price_action_state or {}).get("state") or "")
    if state == "forward_paper_on_monthly_target":
        return "Do not scale yet; keep paper probes tiny until the positive price-change cohort has enough independent forward exits."
    if state == "forward_paper_positive_below_target":
        return "Keep collecting forward paper price-change exits and use cohort evidence to tighten entry/exit selection."
    if state == "trusted_shadow_needs_paper_confirmation":
        return "Generate governed paper-confirmation probes for trusted positive shadow repricing cohorts; do not wait for final settlement."
    if state == "candidate_needs_paper_bridge":
        return "Run the price-action paper-signal bridge, then let the paper broker confirm fills/exits at tiny stake."
    if state == "collecting_positive_bid_ask_evidence":
        return "Prioritise websocket collection for positive bid/ask cohorts so they can graduate to paper-confirmation."
    if state == "negative_repricing_broaden_search":
        return "Suppress losing repricing cohorts and broaden discovery; lowering gates would move away from the profit goal."
    if probationary:
        return "Keep the probationary cap small; focus scanner cycles on liquid actionable windows for the probationary cohort."
    if positive_watchlist:
        return "Prioritise collection for the positive watchlist and improve labels/liquidity filtering before expecting paper entries."
    return "Keep collecting shadow evidence and build cleaner labels; do not lower trade gates to chase the target."
