from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.goal_planner import build_goal_plan
from polymarket_predictive_engine.utils import write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "profit_tracking": {"target_monthly_profit_usdc": 100},
        },
        path=tmp_path / "cfg.yaml",
    )


def test_goal_plan_counts_forward_paper_repricing_without_waiting_for_settlement(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "best_positive_monthly_run_rate_usdc": 125.0,
            "best_forward_paper_monthly_run_rate_usdc": 125.0,
            "forward_paper_positive_cohorts": 1,
            "forward_shadow_positive_cohorts": 0,
            "paper_confirmation_candidates": 0,
            "promotion_candidates": 0,
            "positive_collect_candidates": 1,
            "suppressed_candidates": 0,
            "collection_queries": ["world cup"],
            "top_cohorts": [
                {
                    "cohort": "price_action_microstructure|sports_other|bid_momentum_tight",
                    "evidence_type": "forward_paper_bid_ask_trade_pnl",
                    "forward_paper_pnl_usdc": 6.5,
                    "forward_paper_roi": 0.13,
                }
            ],
        },
    )

    payload = build_goal_plan(cfg)

    state = payload["price_action_goal_state"]
    assert state["state"] == "forward_paper_on_monthly_target"
    assert state["tradeable_price_change_path"] is True
    assert state["settlement_required_for_this_milestone"] is False
    assert state["evidence_type"] == "buy_at_ask_sell_or_mark_at_bid"
    assert state["best_forward_paper_monthly_run_rate_usdc"] == approx(125.0)
    assert state["forward_paper_goal_gap_usdc"] == approx(0.0)
    assert "price-change forward paper P&L" in payload["main_gap"]
    assert "Do not scale yet" in payload["recommended_action"]


def test_goal_plan_uses_audited_pnl_when_raw_account_contains_quote_conflicts(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "actual_pnl_since_baseline_usdc": 54.66,
            "elapsed_hours": 3.0,
            "current": {"equity_usdc": 1054.66},
            "baseline": {"baseline_equity_usdc": 1000.0},
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 0.22,
            "audited_baseline_closed_round_trips": 5,
            "baseline_closed_round_trips": 10,
            "quote_conflict_round_trips": 5,
            "quote_unverified_round_trips": 5,
        },
    )

    payload = build_goal_plan(cfg)

    assert payload["raw_account_pnl_since_baseline_usdc"] == approx(54.66)
    assert payload["audited_pnl_since_baseline_usdc"] == approx(0.22)
    assert payload["decision_pnl_usdc"] == approx(0.22)
    assert payload["pnl_audit_state"] == "raw_pnl_contains_quote_conflicts"
    assert payload["profit_target_proof_status"] == "unverified_paper_run_rate"
    assert "quote_conflict_round_trips_5" in payload["profit_target_proof_blockers"]
    assert payload["quote_conflict_round_trips"] == 5
    assert "raw paper P&L contains quote-conflicted" in payload["main_gap"]


def test_goal_plan_uses_baseline_quote_audit_window_not_all_history(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "actual_pnl_since_baseline_usdc": 10.0,
            "elapsed_hours": 48.0,
            "current": {"equity_usdc": 1010.0},
            "baseline": {"baseline_equity_usdc": 1000.0},
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 10.0,
            "audited_baseline_closed_round_trips": 5,
            "baseline_closed_round_trips": 5,
            "baseline_quote_conflict_round_trips": 0,
            "baseline_quote_unverified_round_trips": 0,
            "quote_conflict_round_trips": 7,
            "quote_unverified_round_trips": 2,
        },
    )

    payload = build_goal_plan(cfg)

    assert payload["profit_target_proof_status"] == "verified_quote_consistent"
    assert payload["profit_target_proof_blockers"] == []
    assert payload["pnl_audit_state"] == "quote_consistent"
    assert payload["quote_conflict_round_trips"] == 0
    assert payload["quote_unverified_round_trips"] == 0
    assert payload["all_time_quote_conflict_round_trips"] == 7
    assert payload["all_time_quote_unverified_round_trips"] == 2
    assert payload["on_pace_by_decision_pnl"] is True
    assert payload["decision_monthly_run_rate_usdc"] > 100


def test_goal_plan_does_not_certify_on_pace_without_enough_audited_round_trips(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "paper_profit_target_tracker.json",
        {
            "actual_pnl_since_baseline_usdc": 12.0,
            "elapsed_hours": 48.0,
            "current": {"equity_usdc": 1012.0},
            "baseline": {"baseline_equity_usdc": 1000.0},
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "audited_baseline_realized_pnl_usdc": 12.0,
            "audited_baseline_closed_round_trips": 2,
            "baseline_closed_round_trips": 2,
            "quote_conflict_round_trips": 0,
            "quote_unverified_round_trips": 0,
        },
    )

    payload = build_goal_plan(cfg)

    assert payload["decision_monthly_run_rate_usdc"] > 100
    assert payload["on_pace_by_actual_pnl"] is True
    assert payload["on_pace_by_decision_pnl"] is False
    assert payload["profit_target_proof_status"] == "unverified_paper_run_rate"
    assert payload["minimum_audited_round_trips_for_on_pace"] == 5
    assert "needs_5_audited_round_trips_has_2" in payload["profit_target_proof_blockers"]
    assert "not yet verified" in payload["main_gap"]


def test_goal_plan_routes_positive_shadow_repricing_to_paper_confirmation(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "best_positive_monthly_run_rate_usdc": 136.0,
            "best_forward_paper_monthly_run_rate_usdc": 0.0,
            "forward_paper_positive_cohorts": 0,
            "forward_shadow_positive_cohorts": 1,
            "paper_confirmation_candidates": 1,
            "promotion_candidates": 0,
            "positive_collect_candidates": 1,
            "suppressed_candidates": 0,
            "collection_queries": ["tennis"],
            "paper_confirmation_preview": [
                {
                    "cohort": "tennis_match_winner",
                    "source": "shadow_forward",
                    "action": "collect_more_positive_price_action_evidence",
                    "evidence_type": "forward_shadow_bid_ask_trade_pnl",
                    "forward_shadow_pnl_usdc": 21.0,
                    "forward_shadow_roi": 1.05,
                }
            ],
            "top_cohorts": [
                {
                    "cohort": "price_action_microstructure|negative_large_sample",
                    "action": "suppress_until_new_thesis",
                    "evidence_type": "settlement_independent_microstructure_validation",
                    "validation_pnl_usdc": -80.0,
                },
                {
                    "cohort": "tennis_match_winner",
                    "evidence_type": "forward_shadow_bid_ask_trade_pnl",
                    "forward_shadow_pnl_usdc": 21.0,
                    "forward_shadow_roi": 1.05,
                }
            ],
        },
    )

    payload = build_goal_plan(cfg)

    state = payload["price_action_goal_state"]
    assert state["state"] == "trusted_shadow_needs_paper_confirmation"
    assert state["settlement_required_for_this_milestone"] is False
    assert state["paper_confirmation_candidates"] == 1
    assert state["repricing_goal_gap_usdc"] == approx(0.0)
    assert state["top_cohorts"][0]["cohort"] == "tennis_match_winner"
    assert "paper-confirmation probes" in payload["main_gap"]
    assert "do not wait for final settlement" in payload["recommended_action"]
