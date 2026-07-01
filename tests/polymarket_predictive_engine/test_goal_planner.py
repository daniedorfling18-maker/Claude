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
