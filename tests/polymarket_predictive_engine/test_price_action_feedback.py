from __future__ import annotations

from pathlib import Path

from pytest import approx

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.price_action_feedback import build_price_action_feedback
from polymarket_predictive_engine.utils import write_csv, write_json


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


def test_price_action_feedback_prioritises_model_validation_gap_queries(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "generated_at_utc": "2026-07-01T08:00:00Z",
            "validation_gap": {
                "state": "needs_positive_validation_examples",
                "collection_queries": ["fed", "ethereum", "esports"],
                "reason": "Positive train examples exist, but validation has no positive executable bid repricing.",
            },
        },
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "collect_model_validation_gap_price_action_evidence"
    assert payload["collection_queries"][:3] == ["fed", "ethereum", "esports"]
    assert payload["model_validation_gap_active"] is True
    assert payload["model_validation_gap_queries"] == ["fed", "ethereum", "esports"]
    assert payload["price_action_model_decision"] == "collect_more_bid_ask_price_action_model_evidence"


def test_price_action_feedback_puts_model_gap_before_positive_cohort_queries(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "validation_gap": {
                "state": "needs_positive_validation_examples",
                "collection_queries": ["fed", "ethereum"],
            },
        },
    )
    write_csv(
        cfg.output_root / "polymarket_price_action" / "price_action_scout_cohort_evidence.csv",
        [
            {
                "signal_cohort": "price_action_scout|fast_liquidity|crypto_btc_updown_daily",
                "family": "crypto_btc_updown_daily",
                "closed_trades": "1",
                "open_trades": "0",
                "realized_pnl_usdc": "1.5",
                "realized_roi": "0.15",
                "realized_monthly_run_rate_usdc": "24",
                "price_action_review_candidate": "False",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["positive_collect_candidates"] == 1
    assert payload["collection_queries"][:3] == ["fed", "ethereum", "btc updown"]
    assert payload["model_validation_gap_active"] is True


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


def test_price_action_feedback_prioritises_family_microstructure_candidate(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_price_action" / "microstructure_family_rule_evidence.csv",
        [
            {
                "market_family": "sports_other",
                "signal_cohort": "price_action_microstructure|sports_other|bid_momentum_tight",
                "rule_id": "bid_momentum_tight|move>=0.02|spread<=0.01",
                "rule_family": "bid_momentum_tight",
                "total_trades": "8",
                "train_trades": "4",
                "validation_trades": "4",
                "validation_pnl_usdc": "3.1",
                "validation_roi": "0.0775",
                "validation_win_rate": "0.75",
                "validation_pass": "True",
                "status": "candidate_for_forward_shadow",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "price_action_candidates_ready_for_governed_paper_bridge"
    assert payload["promotion_candidates"] == 1
    assert payload["collection_queries"] == ["world cup"]
    assert payload["top_cohorts"][0]["source"] == "microstructure_family"
    assert payload["top_cohorts"][0]["family"] == "sports_other"
    assert payload["top_cohorts"][0]["cohort"] == "price_action_microstructure|sports_other|bid_momentum_tight"


def test_price_action_feedback_prioritises_exit_policy_microstructure_candidate(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_price_action" / "microstructure_exit_policy_evidence.csv",
        [
            {
                "exit_policy_id": "quick_3pct_1obs",
                "take_profit_return": "0.03",
                "stop_loss_return": "0.20",
                "max_forward_observations": "1",
                "min_profit_usdc": "0.01",
                "market_family": "sports_other",
                "signal_cohort": "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs",
                "rule_id": "bid_momentum_tight|move>=0.035|spread<=0.01",
                "rule_family": "bid_momentum_tight",
                "total_trades": "8",
                "train_trades": "4",
                "validation_trades": "4",
                "validation_pnl_usdc": "2.6",
                "validation_roi": "0.065",
                "validation_win_rate": "0.75",
                "validation_pass": "True",
                "status": "candidate_for_forward_shadow",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "price_action_candidates_ready_for_governed_paper_bridge"
    assert payload["promotion_candidates"] == 1
    assert payload["collection_queries"] == ["world cup"]
    assert payload["top_cohorts"][0]["source"] == "microstructure_exit_policy"
    assert payload["top_cohorts"][0]["exit_policy_id"] == "quick_3pct_1obs"
    assert payload["top_cohorts"][0]["take_profit_return"] == approx(0.03)


def test_price_action_feedback_ingests_forward_paper_pnl_by_signal_cohort(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.governance_root / "signal_cohort_pnl.csv",
        [
            {
                "signal_cohort": "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs",
                "buy_fills": "4",
                "settled_fills": "2",
                "sell_fills": "2",
                "open_positions": "1",
                "paper_buy_fills": "4",
                "paper_total_pnl_usdc": "6.5",
                "paper_monthly_run_rate_usdc": "125",
                "total_buy_cost_usdc": "50",
                "total_pnl_usdc": "6.5",
                "roi": "0.13",
                "monthly_run_rate_usdc": "125",
                "promoted": "False",
                "probationary": "False",
                "promotion_ready_score": "4",
                "promotion_ready_checks": "6",
                "promotion_evidence_source": "paper",
                "promotion_reason": "needs more forward paper trades",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "collect_more_positive_price_action_evidence"
    assert payload["forward_paper_cohorts"] == 1
    assert payload["forward_paper_positive_cohorts"] == 1
    assert payload["best_forward_paper_monthly_run_rate_usdc"] == approx(125.0)
    assert payload["forward_paper_goal_gap_usdc"] == approx(0.0)
    assert payload["collection_queries"] == ["world cup"]
    assert payload["top_cohorts"][0]["source"] == "paper_broker_forward"
    assert payload["top_cohorts"][0]["evidence_type"] == "forward_paper_bid_ask_trade_pnl"
    assert payload["forward_paper_preview"][0]["forward_paper_pnl_usdc"] == approx(6.5)


def test_price_action_feedback_demotes_probationary_paper_when_audited_pnl_is_negative(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.governance_root / "signal_cohort_pnl.csv",
        [
            {
                "signal_cohort": "price_action_microstructure|sports_other|bid_momentum_tight|exit=quick_3pct_1obs",
                "buy_fills": "4",
                "sell_fills": "2",
                "open_positions": "0",
                "paper_buy_fills": "4",
                "paper_total_pnl_usdc": "-0.08",
                "paper_monthly_run_rate_usdc": "-28.39",
                "total_buy_cost_usdc": "4.0",
                "total_pnl_usdc": "-0.08",
                "roi": "-0.02",
                "monthly_run_rate_usdc": "-28.39",
                "promoted": "False",
                "probationary": "True",
                "promotion_ready_score": "4",
                "promotion_ready_checks": "6",
                "promotion_evidence_source": "paper",
                "promotion_reason": "Forward paper evidence is positive enough for probationary paper sizing.",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["learning_state"] == "suppress_negative_price_action_and_broaden"
    assert payload["promotion_candidates"] == 0
    assert payload["forward_paper_positive_cohorts"] == 0
    row = payload["forward_paper_preview"][0]
    assert row["source_probationary"] is True
    assert row["probationary"] is False
    assert row["promotion_ready"] is False
    assert row["promotion_flag_demoted"] is True
    assert row["trusted_for_goal"] is False
    assert row["source_status"] == "demoted_promotion_flag"
    assert row["action"] == "suppress_until_new_thesis"
    assert "ignored" in row["reason"]


def test_price_action_feedback_uses_audited_paper_pnl_over_raw_conflicted_pnl(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.governance_root / "signal_cohort_pnl.csv",
        [
            {
                "signal_cohort": "near_miss_learning|crypto",
                "buy_fills": "4",
                "settled_fills": "3",
                "sell_fills": "0",
                "open_positions": "0",
                "paper_buy_fills": "0",
                "paper_total_pnl_usdc": "0.22",
                "paper_monthly_run_rate_usdc": "12",
                "total_buy_cost_usdc": "4",
                "raw_paper_buy_fills": "4",
                "raw_paper_total_pnl_usdc": "53.88",
                "raw_paper_monthly_run_rate_usdc": "2188134.72",
                "paper_audit_round_trips": "4",
                "paper_audited_round_trips": "2",
                "paper_quote_conflict_round_trips": "2",
                "paper_audit_blocker": "paper_quote_conflicts_excluded_from_edge",
                "total_pnl_usdc": "0.22",
                "roi": "0.055",
                "monthly_run_rate_usdc": "12",
                "promoted": "False",
                "probationary": "False",
                "metadata_blocker": "paper_quote_conflicts_excluded_from_edge",
                "promotion_ready_score": "1",
                "promotion_ready_checks": "6",
                "promotion_evidence_source": "paper",
                "promotion_reason": "metadata/audit invalid: paper_quote_conflicts_excluded_from_edge",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["forward_paper_positive_cohorts"] == 0
    assert payload["best_forward_paper_monthly_run_rate_usdc"] == approx(0.0)
    row = payload["forward_paper_preview"][0]
    assert row["forward_paper_pnl_usdc"] == approx(0.22)
    assert row["raw_paper_total_pnl_usdc"] == approx(53.88)
    assert row["trusted_for_goal"] is False
    assert row["forward_edge_blocker"] == "paper_quote_conflicts_excluded_from_edge"


def test_price_action_feedback_keeps_shadow_only_pnl_out_of_forward_paper_bucket(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.governance_root / "signal_cohort_pnl.csv",
        [
            {
                "signal_cohort": "macro_economy",
                "buy_fills": "2",
                "sell_fills": "0",
                "paper_buy_fills": "0",
                "paper_total_pnl_usdc": "0",
                "shadow_fills": "2",
                "shadow_sell_fills": "2",
                "shadow_total_pnl_usdc": "21.0",
                "shadow_roi": "1.05",
                "shadow_monthly_run_rate_usdc": "136",
                "total_pnl_usdc": "21.0",
                "roi": "1.05",
                "monthly_run_rate_usdc": "136",
                "promoted": "False",
                "probationary": "False",
                "promotion_evidence_source": "shadow",
                "promotion_reason": "needs broker paper confirmation",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["forward_paper_cohorts"] == 0
    assert payload["forward_paper_positive_cohorts"] == 0
    assert payload["best_forward_paper_monthly_run_rate_usdc"] == approx(0.0)
    assert payload["forward_shadow_cohorts"] == 1
    assert payload["forward_shadow_positive_cohorts"] == 1
    assert payload["paper_confirmation_candidates"] == 1
    assert payload["top_cohorts"][0]["source"] == "shadow_forward"
    assert payload["top_cohorts"][0]["trusted_for_goal"] is True
    assert payload["top_cohorts"][0]["evidence_type"] == "forward_shadow_bid_ask_trade_pnl"
    assert payload["paper_confirmation_preview"][0]["cohort"] == "macro_economy"


def test_price_action_feedback_quarantines_fast_crypto_updown_shadow_pnl(tmp_path):
    cfg = _cfg(tmp_path)
    write_csv(
        cfg.governance_root / "signal_cohort_pnl.csv",
        [
            {
                "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down",
                "buy_fills": "2",
                "sell_fills": "0",
                "paper_buy_fills": "0",
                "paper_total_pnl_usdc": "0",
                "shadow_fills": "2",
                "shadow_sell_fills": "2",
                "shadow_total_pnl_usdc": "21.0",
                "shadow_roi": "1.05",
                "shadow_monthly_run_rate_usdc": "136",
                "total_pnl_usdc": "21.0",
                "roi": "1.05",
                "monthly_run_rate_usdc": "136",
                "promoted": "False",
                "probationary": "False",
                "promotion_evidence_source": "shadow",
                "promotion_reason": "needs broker paper confirmation",
            }
        ],
    )

    payload = build_price_action_feedback(cfg)

    assert payload["forward_paper_positive_cohorts"] == 0
    assert payload["forward_shadow_positive_cohorts"] == 0
    assert payload["paper_confirmation_candidates"] == 0
    assert payload["suppressed_candidates"] == 1
    assert payload["top_cohorts"][0]["source"] == "shadow_forward"
    assert payload["top_cohorts"][0]["trusted_for_goal"] is False
    assert payload["top_cohorts"][0]["forward_edge_blocker"] == "quarantined_fast_crypto_updown_family"
    assert payload["top_cohorts"][0]["action"] == "suppress_until_new_thesis"
