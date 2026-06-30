from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_action_feedback import build_price_action_feedback
from polymarket_predictive_engine.utils import write_csv


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "profit_tracking": {"target_monthly_profit_usdc": 100},
            "price_action_feedback": {
                "min_closed_trades_for_suppression": 2,
                "min_validation_trades_for_suppression": 2,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def test_price_action_feedback_prioritises_positive_bid_ask_cohort(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_scout_cohort_evidence.csv",
        [
            {
                "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down",
                "family": "crypto_xrp_updown_5m",
                "candidates": "4",
                "closed_trades": "2",
                "open_trades": "1",
                "win_rate": "0.75",
                "realized_pnl_usdc": "3.5",
                "realized_roi": "0.175",
                "realized_monthly_run_rate_usdc": "42",
                "total_mark_pnl_usdc": "4.1",
                "mark_roi": "0.12",
                "price_action_review_candidate": "False",
                "status": "collect_more_closed_round_trips",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "collect_more_positive_price_action_evidence"
    assert payload["positive_collect_candidates"] == 1
    assert payload["collection_queries"] == ["xrp updown"]
    assert payload["monthly_goal_gap_usdc"] == approx(58.0)
    assert payload["top_cohorts"][0]["action"] == "collect_more_positive_price_action_evidence"
    assert payload["top_cohorts"][0]["evidence_type"] == "settlement_independent_bid_ask_round_trip"


def test_price_action_feedback_suppresses_negative_closed_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_strategy_v2" / "strategy_v2_round_trip_cohort_evidence.csv",
        [
            {
                "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
                "family": "crypto_btc_updown_5m",
                "candidates": "5",
                "closed_trades": "3",
                "open_trades": "0",
                "win_rate": "0.33",
                "realized_pnl_usdc": "-1.2",
                "realized_roi": "-0.04",
                "realized_monthly_run_rate_usdc": "-12",
                "total_mark_pnl_usdc": "-1.2",
                "mark_roi": "-0.04",
                "price_action_review_candidate": "False",
                "status": "negative_or_insufficient_realized_round_trip_pnl",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "suppress_negative_price_action_and_broaden"
    assert payload["suppressed_candidates"] == 1
    assert payload["suppressed_queries"] == ["btc updown"]
    assert payload["top_cohorts"][0]["action"] == "suppress_until_new_thesis"
    assert payload["warnings"]["does_not_authorise_live_trading"] is True


def test_price_action_feedback_promotes_passing_microstructure_to_shadow_candidate(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_price_action" / "microstructure_rule_evidence.csv",
        [
            {
                "rule_id": "btc_bid_momentum_tight",
                "rule_family": "crypto_btc_updown_bid_momentum_tight",
                "total_trades": "8",
                "train_trades": "4",
                "validation_trades": "4",
                "validation_pnl_usdc": "2.4",
                "validation_roi": "0.06",
                "validation_win_rate": "0.75",
                "validation_pass": "True",
                "status": "candidate_for_forward_shadow",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "price_action_candidates_ready_for_governed_paper_bridge"
    assert payload["promotion_candidates"] == 1
    assert payload["collection_queries"] == ["btc updown"]
    assert payload["top_cohorts"][0]["action"] == "candidate_for_forward_shadow_microstructure"
