from __future__ import annotations

import argparse
import json
from typing import Any

from .config import EngineConfig, load_config
from .cohort_validation import write_signal_cohort_pnl
from .closing_line import build_closing_line_value
from .dashboard import render_dashboard
from .goal_planner import build_goal_plan
from .governance import governance_report
from .paper_round_trip import build_paper_round_trip_evidence
from .price_action_feedback import build_price_action_feedback
from .price_action_microstructure import build_microstructure_edge_lab
from .price_action_model import train_price_action_model
from .price_action_scout import build_price_action_scout
from .price_action_signals import build_price_action_paper_signals
from .profit_sprint import build_profit_sprint
from .promotion_review import build_promotion_review
from .quant_research_status import build_quant_research_status
from .readiness import paper_live_promotion_gate
from .research_focus import build_research_focus
from .storage import connect_db
from .trade_signal_audit import build_trade_signal_audit
from .utils import now_utc, write_json


def _cohort_counts(signal_cohort_pnl: dict[str, Any]) -> dict[str, Any]:
    cohorts = signal_cohort_pnl.get("cohorts", []) if isinstance(signal_cohort_pnl, dict) else []
    if not isinstance(cohorts, list):
        cohorts = []
    return {
        "total": len(cohorts),
        "promoted": sum(1 for row in cohorts if isinstance(row, dict) and row.get("promoted") is True),
        "probationary": sum(1 for row in cohorts if isinstance(row, dict) and row.get("probationary") is True),
        "metadata_blocked": sum(1 for row in cohorts if isinstance(row, dict) and str(row.get("metadata_blocker") or "").strip()),
        "positive_pnl": sum(1 for row in cohorts if isinstance(row, dict) and float(row.get("total_pnl_usdc") or 0.0) > 0),
    }


def refresh_governance(cfg: EngineConfig, *, refresh_dashboard: bool = True) -> dict[str, Any]:
    """Rebuild governance files in dependency order without changing trade gates.

    This command is intentionally conservative. It only recomputes governance and
    dashboard artefacts from existing paper/shadow evidence; it does not generate
    new trade signals, execute paper orders, change thresholds, or affect live
    trading settings.
    """
    paper_round_trip = build_paper_round_trip_evidence(cfg)
    closing_line = build_closing_line_value(cfg)

    con = connect_db(cfg.database_path)
    try:
        signal_cohort_pnl = write_signal_cohort_pnl(con, cfg)
    finally:
        con.close()

    price_action_scout = build_price_action_scout(cfg)
    price_action_microstructure = build_microstructure_edge_lab(cfg)
    price_action_model = train_price_action_model(cfg)
    price_action_feedback = build_price_action_feedback(cfg)
    price_action_paper_signals = build_price_action_paper_signals(cfg)
    quant_research_status = build_quant_research_status(cfg)
    trade_signal_audit = build_trade_signal_audit(cfg)
    promotion_review = build_promotion_review(cfg)
    goal_plan = build_goal_plan(cfg)
    profit_sprint = build_profit_sprint(cfg)
    research_focus = build_research_focus(cfg)
    promotion_gate = paper_live_promotion_gate(cfg)
    governance = governance_report(cfg)
    dashboard = render_dashboard(cfg) if refresh_dashboard else {"status": "skipped"}

    result = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "database_path": str(cfg.database_path),
        "refreshed": {
            "signal_cohort_pnl": True,
            "promotion_review": True,
            "goal_plan": True,
            "profit_sprint": True,
            "price_action_scout": True,
            "price_action_microstructure": True,
            "paper_round_trip_evidence": True,
            "closing_line_value": True,
            "price_action_feedback": True,
            "price_action_model": True,
            "price_action_paper_signals": True,
            "quant_research_status": True,
            "trade_signal_audit": True,
            "research_focus": True,
            "promotion_gate": True,
            "governance_report": True,
            "dashboard": bool(refresh_dashboard),
        },
        "cohort_counts": _cohort_counts(signal_cohort_pnl),
        "promotion_review_status": promotion_review.get("status"),
        "goal_plan_status": goal_plan.get("status"),
        "profit_sprint_decision": profit_sprint.get("decision"),
        "price_action_scout_decision": price_action_scout.get("decision"),
        "price_action_scout_closed_trades": price_action_scout.get("closed_trades"),
        "price_action_microstructure_decision": price_action_microstructure.get("decision"),
        "price_action_microstructure_training_events": price_action_microstructure.get("trade_events"),
        "paper_round_trip_closed_trades": paper_round_trip.get("closed_round_trips"),
        "paper_round_trip_positive_trades": paper_round_trip.get("positive_round_trips"),
        "paper_round_trip_realized_pnl_usdc": paper_round_trip.get("realized_pnl_usdc"),
        "closing_line_positions_scored": closing_line.get("positions_scored"),
        "closing_line_final_positions": closing_line.get("final_line_positions"),
        "closing_line_mean_final_clv": closing_line.get("mean_final_clv"),
        "closing_line_positive_cohorts": closing_line.get("positive_clv_cohorts"),
        "price_action_feedback_state": price_action_feedback.get("learning_state"),
        "price_action_model_decision": price_action_model.get("decision"),
        "price_action_model_promotion_ready": price_action_model.get("promotion_ready"),
        "price_action_paper_signal_count": price_action_paper_signals.get("signals"),
        "price_action_paper_rejection_count": price_action_paper_signals.get("rejections"),
        "price_action_paper_decision": price_action_paper_signals.get("decision"),
        "quant_research_implementation_complete": quant_research_status.get("implementation_complete"),
        "trade_signal_verdict": trade_signal_audit.get("verdict"),
        "research_focus_status": research_focus.get("status"),
        "approved_for_paper_trading": promotion_gate.get("approved_for_paper_trading"),
        "approved_for_live_trading": promotion_gate.get("approved_for_live_trading"),
        "paper_blockers": promotion_gate.get("paper_blockers", []),
        "live_blockers": promotion_gate.get("live_blockers", []),
        "governance_report_status": governance.get("status") if isinstance(governance, dict) else "unknown",
        "dashboard": dashboard,
        "notes": [
            "Governance refresh only: no order placement, no threshold changes, no live-trading opt-in.",
            "Metadata-blocked cohorts remain shadow-only and cannot become probationary/promoted from this command.",
        ],
    }
    write_json(cfg.governance_root / "governance_refresh.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Polymarket governance artefacts in dependency order.")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--skip-dashboard", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    print(json.dumps(refresh_governance(cfg, refresh_dashboard=not args.skip_dashboard), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
