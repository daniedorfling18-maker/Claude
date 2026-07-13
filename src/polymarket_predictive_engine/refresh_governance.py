from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import time
from typing import Any, BinaryIO, Callable, TypeVar

from .algo.sweep import run_algo_sweep
from .config import EngineConfig, load_config
from .cohort_validation import write_signal_cohort_pnl
from .closing_line import build_closing_line_value
from .collection_coverage import build_collection_coverage
from .dashboard import render_dashboard
from .edge_attribution import build_edge_attribution
from .evidence_history import append_evidence_history
from .family_calibration import build_family_calibration_scorecard
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
from .sharp_anchor_coverage import build_sharp_anchor_coverage
from .smart_flow_clv import build_smart_flow_clv
from .storage import connect_db
from .trade_signal_audit import build_trade_signal_audit
from .utils import now_utc, write_json


T = TypeVar("T")
LOCK_CONTENTION_EXIT_CODE = 75


class _GovernanceRefreshLock:
    """Non-blocking process lock released by the OS even after timeout/kill."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in {errno.EACCES, errno.EAGAIN, 13, 36}:
                return False
            raise
        self.handle = handle
        return True

    def release(self) -> None:
        handle = self.handle
        self.handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


class _GovernanceRefreshProgress:
    """Durable stage heartbeat for attribution and fail-closed freshness."""

    def __init__(self, cfg: EngineConfig):
        self.path = cfg.governance_root / "governance_refresh_status.json"
        self.started_at_utc = now_utc()
        self.started_monotonic = time.monotonic()
        self.current_stage = "starting"
        self.completed_stages: list[str] = []
        self.stage_durations_seconds: dict[str, float] = {}
        self._write("running")

    def _payload(self, status: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": status,
            "generated_at_utc": now_utc(),
            "started_at_utc": self.started_at_utc,
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "stage_durations_seconds": dict(self.stage_durations_seconds),
            "pid": os.getpid(),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
            **extra,
        }

    def _write(self, status: str, **extra: Any) -> None:
        write_json(self.path, self._payload(status, **extra))

    def run(self, name: str, callback: Callable[[], T]) -> T:
        self.current_stage = name
        self._write("running", stage_started_at_utc=now_utc())
        started = time.monotonic()
        try:
            result = callback()
        except Exception as exc:
            self.stage_durations_seconds[name] = round(time.monotonic() - started, 3)
            self._write(
                "failed",
                failed_stage=name,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self.stage_durations_seconds[name] = round(time.monotonic() - started, 3)
        self.completed_stages.append(name)
        self._write("running")
        return result

    def finish(self) -> None:
        self.current_stage = "complete"
        self._write("ok", completed_at_utc=now_utc())


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


def _refresh_governance_locked(
    cfg: EngineConfig,
    progress: _GovernanceRefreshProgress,
    *,
    refresh_dashboard: bool = True,
) -> dict[str, Any]:
    """Rebuild governance files in dependency order without changing trade gates.

    This command is intentionally conservative. It only recomputes governance and
    dashboard artefacts from existing paper/shadow evidence; it does not generate
    new trade signals, execute paper orders, change thresholds, or affect live
    trading settings. The model-critical path is published before slower reporting
    stages so a non-critical report cannot make executable model evidence stale.
    """
    # Critical freshness path: these are the only prerequisites of the strict
    # ask-in/bid-out model. Keep them ahead of slower attribution/report lanes.
    paper_round_trip = progress.run("paper_round_trip_evidence", lambda: build_paper_round_trip_evidence(cfg))
    price_action_scout = progress.run("price_action_scout", lambda: build_price_action_scout(cfg))
    price_action_microstructure = progress.run(
        "price_action_microstructure",
        lambda: build_microstructure_edge_lab(cfg),
    )
    price_action_model = progress.run("price_action_model", lambda: train_price_action_model(cfg))

    # Research/reporting path. These stages remain strict dependencies of the
    # final promotion decision, but cannot prevent the model timestamp above
    # from being refreshed and audited.
    closing_line = progress.run("closing_line_value", lambda: build_closing_line_value(cfg))
    smart_flow_clv = progress.run("smart_flow_clv", lambda: build_smart_flow_clv(cfg))
    edge_attribution = progress.run("edge_attribution", lambda: build_edge_attribution(cfg))
    algo_sweep = progress.run("algo_sweep", lambda: run_algo_sweep(cfg))
    family_calibration = progress.run("family_calibration", lambda: build_family_calibration_scorecard(cfg))
    collection_coverage = progress.run("collection_coverage", lambda: build_collection_coverage(cfg))
    sharp_anchor_coverage = progress.run("sharp_anchor_coverage", lambda: build_sharp_anchor_coverage(cfg))
    evidence_history = progress.run("evidence_history", lambda: append_evidence_history(cfg))

    con = connect_db(cfg.database_path)
    try:
        signal_cohort_pnl = progress.run("signal_cohort_pnl", lambda: write_signal_cohort_pnl(con, cfg))
    finally:
        con.close()

    price_action_feedback = progress.run("price_action_feedback", lambda: build_price_action_feedback(cfg))
    price_action_paper_signals = progress.run("price_action_paper_signals", lambda: build_price_action_paper_signals(cfg))
    quant_research_status = progress.run("quant_research_status", lambda: build_quant_research_status(cfg))
    trade_signal_audit = progress.run("trade_signal_audit", lambda: build_trade_signal_audit(cfg))
    promotion_review = progress.run("promotion_review", lambda: build_promotion_review(cfg))
    goal_plan = progress.run("goal_plan", lambda: build_goal_plan(cfg))
    profit_sprint = progress.run("profit_sprint", lambda: build_profit_sprint(cfg))
    research_focus = progress.run("research_focus", lambda: build_research_focus(cfg))
    promotion_gate = progress.run("promotion_gate", lambda: paper_live_promotion_gate(cfg))
    governance = progress.run("governance_report", lambda: governance_report(cfg))
    # render_dashboard builds the four proof questions into its single compact write and
    # writes the governance proof_questions.json artifact itself, so no separate apply
    # pass is needed here (a second pass re-wrote the payload pretty-printed, breaking
    # the compact/capped invariant until the live loop's next render).
    dashboard = (
        progress.run("dashboard", lambda: render_dashboard(cfg))
        if refresh_dashboard
        else {"status": "skipped"}
    )

    result = {
        "status": "ok",
        "generated_at_utc": now_utc(),
        "started_at_utc": progress.started_at_utc,
        "elapsed_seconds": round(time.monotonic() - progress.started_monotonic, 3),
        "completed_stages": list(progress.completed_stages),
        "stage_durations_seconds": dict(progress.stage_durations_seconds),
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
            "smart_flow_clv": True,
            "edge_attribution": True,
            "algo_sweep": True,
            "family_calibration": True,
            "collection_coverage": True,
            "sharp_anchor_coverage": True,
            "evidence_history": True,
            "price_action_feedback": True,
            "price_action_model": True,
            "price_action_paper_signals": True,
            "quant_research_status": True,
            "trade_signal_audit": True,
            "research_focus": True,
            "promotion_gate": True,
            "governance_report": True,
            "dashboard": bool(refresh_dashboard),
            "dashboard_proof_questions": bool(refresh_dashboard),
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
        "smart_flow_fills_scored": smart_flow_clv.get("fills_scored"),
        "smart_flow_positive_wallets": smart_flow_clv.get("positive_wallets", []),
        "edge_attribution_status": edge_attribution.get("status"),
        "edge_attribution_positions": edge_attribution.get("attributed_positions"),
        "edge_attribution_cohort_classes": {
            str(row.get("signal_cohort") or "unknown"): row.get("attribution_class")
            for row in edge_attribution.get("cohorts", [])
            if isinstance(row, dict)
        },
        "algo_sweep_decision": algo_sweep.get("decision"),
        "family_calibration_model_beats_market": family_calibration.get("model_beats_market_families", []),
        "collection_coverage_missing_pre_close": collection_coverage.get("positions_missing_pre_close_quote"),
        "sharp_anchor_coverage_status": sharp_anchor_coverage.get("status"),
        "sharp_anchor_coverage_flagged_no_mappable_market": sharp_anchor_coverage.get("flagged_no_mappable_market_count"),
        "sharp_anchor_coverage_total_rows_mapped": sharp_anchor_coverage.get("total_rows_mapped"),
        "evidence_history_appended_rows": evidence_history.get("appended_rows"),
        "evidence_history_appended_sources": evidence_history.get("appended_sources", []),
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
        "proof_questions_status": dashboard.get("proof_questions_status"),
        "proof_questions_html_status": dashboard.get("proof_questions_html_status"),
        "dashboard": dashboard,
        "notes": [
            "Governance refresh only: no order placement, no threshold changes, no live-trading opt-in.",
            "Metadata-blocked cohorts remain shadow-only and cannot become probationary/promoted from this command.",
            "Sharp-anchor coverage and proof questions are reporting-only; no config trimming or gate changes are automatic.",
        ],
    }
    write_json(cfg.governance_root / "governance_refresh.json", result)
    progress.finish()
    return result


def refresh_governance(cfg: EngineConfig, *, refresh_dashboard: bool = True) -> dict[str, Any]:
    """Run exactly one full governance refresh across local/VPS producers."""
    lock = _GovernanceRefreshLock(cfg.governance_root / "governance_refresh.lock")
    if not lock.acquire():
        payload = {
            "status": "skipped_already_running",
            "generated_at_utc": now_utc(),
            "reason": "another governance refresh owns the cross-process lock",
            "lock_file": str(lock.path),
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        write_json(cfg.governance_root / "governance_refresh_contention.json", payload)
        return payload
    try:
        progress = _GovernanceRefreshProgress(cfg)
        return _refresh_governance_locked(cfg, progress, refresh_dashboard=refresh_dashboard)
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh Polymarket governance artefacts in dependency order.")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    parser.add_argument("--skip-dashboard", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    result = refresh_governance(cfg, refresh_dashboard=not args.skip_dashboard)
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return LOCK_CONTENTION_EXIT_CODE if result.get("status") == "skipped_already_running" else 0


if __name__ == "__main__":
    raise SystemExit(main())
