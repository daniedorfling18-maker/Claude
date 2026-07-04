from __future__ import annotations

import json
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.research_focus import build_research_focus
from polymarket_predictive_engine.utils import read_json, write_csv, write_json


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "actual_profit_target": {"target_monthly_profit_usdc": 100},
        },
        path=tmp_path / "cfg.yaml",
    )


def test_research_focus_uses_price_action_model_blocker_to_prioritise_feedback_queries(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "insufficient_data",
            "decision": "collect_more_bid_ask_price_action_training_events",
            "promotion_ready": False,
            "training_events": 1730,
            "train_rows": 1038,
            "validation_rows": 692,
            "blockers": ["training split does not contain both profitable and unprofitable repricing examples"],
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "paper_confirmation_candidates": 2,
            "collection_queries": ["btc updown", "solana updown"],
        },
    )
    write_json(
        cfg.governance_root / "signal_cohort_pnl.json",
        {
            "cohorts": [
                {
                    "signal_cohort": "macro_economy",
                    "total_pnl_usdc": 1.0,
                    "roi": 0.01,
                    "promotion_ready_score": 5,
                    "promotion_ready_checks": 6,
                }
            ]
        },
    )

    payload = build_research_focus(cfg)
    saved = read_json(cfg.governance_root / "research_focus.json")

    assert payload["raw_collection_queries"][:2] == ["btc updown", "solana updown"]
    assert payload["collection_queries"][0] == "btc updown"
    assert "solana updown" not in payload["collection_queries"]
    assert payload["collection_query_guard"]["updown_query_count"] == 1
    assert payload["collection_query_guard"]["distinct_families"] >= 4
    assert payload["collection_query_guard"]["rejected_queries"][0] == {
        "query": "solana updown",
        "family": "crypto_updown",
        "reason": "max_updown_queries",
    }
    assert payload["price_action_model"]["model_needs_repricing_data"] is True
    assert "Strict price-action model needs profitable ask-to-future-bid" in payload["summary"]
    assert saved["collection_queries"] == payload["collection_queries"]


def test_research_focus_does_not_map_macro_cohort_to_btc_updown(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "signal_cohort_pnl.json",
        {
            "cohorts": [
                {
                    "signal_cohort": "macro_economy",
                    "total_pnl_usdc": 1.0,
                    "roi": 0.01,
                    "promotion_ready_score": 5,
                    "promotion_ready_checks": 6,
                }
            ]
        },
    )

    payload = build_research_focus(cfg)

    assert "btc updown" not in payload["collection_queries"]
    assert payload["watchlist"][0]["recommended_collection_query"] == "economy"


def test_research_focus_prioritises_model_validation_gap_queries(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "training_events": 1747,
            "train_rows": 1055,
            "validation_rows": 692,
            "train_positive_targets": 4,
            "validation_positive_targets": 0,
            "validation_blockers": ["validation positive repricing targets 0 < 1"],
            "validation_gap": {
                "state": "needs_positive_validation_examples",
                "collection_queries": ["fed", "ethereum", "esports"],
                "reason": "Positive repricing examples exist in train, but validation has not yet seen one.",
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["btc updown", "solana updown"],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][:3] == ["fed", "ethereum", "esports"]
    assert payload["collection_queries"][3] == "btc updown"
    assert "solana updown" not in payload["collection_queries"]
    assert payload["collection_query_guard"]["raw_collection_queries"][:5] == [
        "fed",
        "ethereum",
        "esports",
        "btc updown",
        "solana updown",
    ]
    assert payload["price_action_model"]["validation_gap_needs_collection"] is True
    assert "positive train repricing examples but no positive validation examples" in payload["summary"]


def test_research_focus_prioritises_near_positive_historical_breadth_queries(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "validation_blockers": ["no probability threshold cleared the training split trade gates"],
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "historical_breadth_scan": {
                "state": "positive_validation_pockets_not_robust",
                "recommended_collection_queries": ["xrp updown"],
                "top_near_positive_buckets": [
                    {
                        "recommended_collection_query": "xrp updown",
                        "key": "crypto_xrp_updown_event|60c+|<=1c|down0.5-2c|sell_pressure",
                        "blockers": ["train_rows<3", "positive_pnl_concentrated_in_one_token"],
                    }
                ],
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["btc updown", "ethereum"],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][:3] == ["xrp updown", "ethereum", "world cup"]
    assert "btc updown" not in payload["collection_queries"]
    assert payload["collection_query_guard"]["rejected_queries"][0]["reason"] == "max_updown_queries"
    assert payload["price_action_model"]["historical_breadth_queries"] == ["xrp updown"]
    assert "near-positive historical buckets" in payload["summary"]


def test_research_focus_prioritises_paper_confirmation_blocker_query(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "paper_confirmation_current_historical_analogue": {
                "state": "current_candidates_blocked_by_entry_price_band",
                "fresh_matches": 2,
                "selected": 0,
                "approved": 0,
                "blocked": 2,
                "blocked_preview": [
                    {
                        "market_slug": "solana-up-or-down-july-4-2026-3am-et",
                        "question": "Solana Up or Down - July 4, 3AM ET",
                        "family": "crypto_sol_updown_event",
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_sol_updown_15m|outcome=up",
                        "latest_bid": 0.98,
                        "latest_ask": 0.99,
                        "candidate_gate": "entry_price_outside_risk_band",
                    },
                    {
                        "market_slug": "btc-updown-15m-1783130400",
                        "question": "Bitcoin Up or Down - July 4, 3AM ET",
                        "family": "crypto_btc_updown_15m",
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_15m|outcome=up",
                        "latest_bid": 0.50,
                        "latest_ask": 0.51,
                        "candidate_gate": "insufficient_historical_analogue_rows",
                    },
                ],
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["btc updown", "ethereum"],
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "baseline_quote_audit_by_cohort": [
                {
                    "cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
                    "family": "crypto_btc_updown_daily",
                    "round_trips": 3,
                    "quote_conflict_round_trips": 1,
                    "quote_unverified_round_trips": 0,
                }
            ]
        },
    )

    payload = build_research_focus(cfg)

    assert payload["raw_collection_queries"][:3] == ["solana updown", "btc updown", "ethereum"]
    assert payload["collection_queries"][0] == "solana updown"
    assert "btc updown" not in payload["collection_queries"]
    assert payload["price_action_model"]["paper_confirmation_blocker_queries"] == [
        "solana updown",
        "btc updown",
    ]
    assert payload["quote_audit_priority_queries"] == ["btc updown"]
    assert payload["proof_priority_queries"][:2] == ["solana updown", "btc updown"]
    assert payload["collection_query_guard"]["rejected_queries"][0] == {
        "query": "btc updown",
        "family": "crypto_updown",
        "reason": "max_updown_queries",
    }


def test_research_focus_broadens_to_near_miss_board_when_current_analogues_are_negative(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "current_historical_analogue_scan": {
                "current_rows": 93,
                "positive_matches": 0,
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["eth updown", "xrp updown"],
        },
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv",
        [
            {
                "market_slug": "will-spain-reach-the-quarterfinals-at-the-2026-fifa-world-cup",
                "question": "Will Spain reach the Quarterfinals at the 2026 FIFA World Cup?",
                "category": "worldcup",
                "signal_cohort": "worldcup",
                "near_miss_priority_score": "0.100",
                "edge_lower_bound": "0.024",
                "liquidity": "249",
            },
            {
                "market_slug": "will-no-fed-rate-cuts-happen-in-2026",
                "question": "Will no Fed rate cuts happen in 2026?",
                "category": "unknown",
                "signal_cohort": "unknown",
                "near_miss_priority_score": "0.096",
                "edge_lower_bound": "0.029",
                "liquidity": "5667",
            },
            {
                "market_slug": "val-vit-kc3-2026-07-02-map-handicap-away-1pt5",
                "question": "Map Handicap: VIT (-1.5) vs Karmine Corp (+1.5)",
                "category": "worldcup",
                "signal_cohort": "worldcup",
                "near_miss_priority_score": "0.093",
                "edge_lower_bound": "0.024",
                "liquidity": "165",
            },
            {
                "market_slug": "will-bitcoin-reach-85000-by-december-31-2026",
                "question": "Will Bitcoin reach $85,000 by December 31, 2026?",
                "category": "crypto",
                "signal_cohort": "crypto",
                "near_miss_priority_score": "0.073",
                "edge_lower_bound": "0.020",
                "liquidity": "2105",
            },
        ],
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][:4] == ["fed", "world cup", "esports", "bitcoin"]
    assert payload["collection_queries"][4:6] == ["eth updown", "tennis"]
    assert "xrp updown" not in payload["collection_queries"]
    assert payload["price_action_model"]["analogue_scan_needs_breadth"] is True
    assert payload["price_action_model"]["near_miss_candidate_queries"][:4] == ["fed", "world cup", "esports", "bitcoin"]
    assert "broaden evidence collection into near-miss markets" in payload["summary"]


def test_research_focus_guard_prevents_updown_query_collapse(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["research_focus"] = {
        "max_queries_per_family": 2,
        "min_distinct_families": 4,
        "max_updown_queries": 1,
        "broad_base_queries": ["world cup", "tennis", "fed", "economy", "esports", "ai", "politics"],
    }
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "insufficient_data",
            "decision": "collect_more_bid_ask_price_action_training_events",
            "promotion_ready": False,
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": [
                "btc updown",
                "solana updown",
                "xrp updown",
                "eth updown",
                "bitcoin up or down",
                "ethereum up or down",
                "solana up or down",
                "xrp up or down",
            ],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["raw_collection_queries"] == [
        "btc updown",
        "solana updown",
        "xrp updown",
        "eth updown",
        "bitcoin up or down",
        "ethereum up or down",
        "solana up or down",
        "xrp up or down",
    ]
    assert payload["collection_queries"] == [
        "btc updown",
        "world cup",
        "tennis",
        "fed",
        "economy",
        "esports",
        "ai",
        "politics",
    ]
    guard = payload["collection_query_guard"]
    assert guard["updown_query_count"] == 1
    assert guard["distinct_families"] >= 4
    assert guard["broad_fill_queries"] == ["world cup", "tennis", "fed", "economy", "esports", "ai", "politics"]
    assert [row["reason"] for row in guard["rejected_queries"]] == ["max_updown_queries"] * 7


def test_research_focus_default_broad_base_collects_sharp_sports_families(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "insufficient_data",
            "decision": "collect_more_bid_ask_price_action_training_events",
            "promotion_ready": False,
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": [
                "btc updown",
                "solana updown",
                "xrp updown",
                "eth updown",
                "bitcoin up or down",
                "ethereum up or down",
                "solana up or down",
                "xrp up or down",
            ],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"] == [
        "btc updown",
        "world cup",
        "tennis",
        "nba",
        "mlb",
        "mma",
        "fed",
        "economy",
    ]
    guard = payload["collection_query_guard"]
    assert guard["broad_fill_queries"] == ["world cup", "tennis", "nba", "mlb", "mma", "fed", "economy"]
    assert guard["family_counts"]["basketball_nba_match"] == 1
    assert guard["family_counts"]["baseball_mlb_match"] == 1
    assert guard["family_counts"]["mma_match"] == 1
    assert guard["updown_query_count"] == 1
    assert [row["reason"] for row in guard["rejected_queries"]] == ["max_updown_queries"] * 7


def test_research_focus_prioritises_current_positive_analogue_as_learning_target(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "validation_blockers": ["selected validation ROI 0.0000 < 0.0300"],
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "current_historical_analogue_scan": {
                "state": "current_positive_historical_analogue_available",
                "current_rows": 12,
                "positive_matches": 1,
                "minimum_robust_validation_roi": 0.03,
                "positive_preview": [
                    {
                        "market_slug": "will-the-fed-increase-interest-rates-by-50-bps-after-the-july-2026-meeting",
                        "question": "Will the Fed increase interest rates by 50+ bps after the July 2026 meeting?",
                        "family": "macro_rates",
                        "outcome": "Yes",
                        "token_id": "fed-token",
                        "latest_bid": 0.899,
                        "latest_ask": 0.900,
                        "latest_spread": 0.001,
                        "historical_analogue_key": "macro_rates|ask=60c+|spread=<=0.1c|side=BUY",
                        "historical_analogue_validation_rows": 70,
                        "historical_analogue_positive_rows": 15,
                        "historical_analogue_validation_roi": 0.0026,
                        "historical_analogue_win_rate": 0.214,
                    }
                ],
            },
            "historical_breadth_scan": {
                "state": "positive_validation_pockets_not_robust",
                "recommended_collection_queries": ["ethereum"],
                "thresholds": {"min_validation_roi": 0.03},
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["btc updown"],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][:3] == ["fed", "ethereum", "btc updown"]
    assert payload["price_action_model"]["current_positive_analogue_queries"] == ["fed"]
    assert payload["price_action_current_positive_analogues"]["state"] == "learning_targets_available"
    target = payload["price_action_current_positive_analogues"]["targets"][0]
    assert target["recommended_collection_query"] == "fed"
    assert target["decision_use"] == "forward_shadow_learning_target_not_trade_authorisation"
    assert target["can_clear_model_label_hurdle"] is True
    assert target["robust_validation_roi_gap"] > 0
    assert "not a trade approval" in payload["summary"]


def test_research_focus_routes_side_missing_analogues_to_context_collection(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "validation_blockers": ["current side context is missing"],
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "current_historical_analogue_scan": {
                "state": "no_current_positive_historical_analogue",
                "current_rows": 8,
                "positive_matches": 0,
                "blocked_preview": [
                    {
                        "market_slug": "will-the-fed-cut-rates-after-the-july-2026-meeting",
                        "question": "Will the Fed cut rates after the July 2026 meeting?",
                        "family": "macro_rates",
                        "outcome": "Yes",
                        "token_id": "fed-side-missing-token",
                        "latest_bid": 0.49,
                        "latest_ask": 0.50,
                        "latest_spread": 0.01,
                        "historical_analogue_key": "macro_rates|ask=40-60c|spread=<=1c|side=",
                        "historical_analogue_gate": "side_missing_positive_historical_analogue_shadow_only",
                        "side_agnostic_historical_analogue_key": "macro_rates|ask=40-60c|spread=<=1c|side=ANY",
                        "side_agnostic_historical_analogue_gate": "positive_historical_analogue",
                        "side_agnostic_historical_analogue_validation_rows": 12,
                        "side_agnostic_historical_analogue_positive_rows": 5,
                        "side_agnostic_historical_analogue_validation_roi": 0.041,
                        "side_agnostic_historical_analogue_win_rate": 0.4167,
                    }
                ],
                "blocked_by_state": {"side_missing_positive_historical_analogue_shadow_only": 1},
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["btc updown"],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][0] == "fed"
    assert payload["price_action_model"]["side_missing_analogue_queries"] == ["fed"]
    side_missing = payload["price_action_side_missing_analogues"]
    assert side_missing["state"] == "needs_trade_flow_side_context"
    assert side_missing["trade_authorisation"] == "no_trade_side_agnostic_history_is_shadow_only"
    target = side_missing["targets"][0]
    assert target["recommended_collection_query"] == "fed"
    assert target["decision_use"] == "collect_trade_flow_side_context_only_not_trade_authorisation"
    assert target["side_agnostic_validation_roi"] == 0.041
    assert "missing BUY/SELL side context keeps paper gates closed" in payload["summary"]


def test_research_focus_blocks_current_positive_analogue_without_label_headroom(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "validation_blockers": ["selected validation ROI 0.0000 < 0.0300"],
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_paper_signal_summary.json",
        {
            "status": "computed",
            "current_historical_analogue_scan": {
                "state": "current_positive_historical_analogue_available",
                "current_rows": 12,
                "positive_matches": 1,
                "minimum_robust_validation_roi": 0.03,
                "positive_preview": [
                    {
                        "market_slug": "will-the-fed-increase-interest-rates-by-50-bps-after-the-july-2026-meeting",
                        "question": "Will the Fed increase interest rates by 50+ bps after the July 2026 meeting?",
                        "family": "macro_rates",
                        "outcome": "Yes",
                        "token_id": "fed-token",
                        "latest_bid": 0.996,
                        "latest_ask": 0.997,
                        "latest_spread": 0.001,
                        "historical_analogue_key": "macro_rates|ask=60c+|spread=<=0.1c|side=BUY",
                        "historical_analogue_validation_rows": 70,
                        "historical_analogue_positive_rows": 15,
                        "historical_analogue_validation_roi": 0.0026,
                        "historical_analogue_win_rate": 0.214,
                    }
                ],
            },
            "historical_breadth_scan": {
                "state": "positive_validation_pockets_not_robust",
                "recommended_collection_queries": ["ethereum"],
                "thresholds": {"min_validation_roi": 0.03},
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_more_positive_price_action_evidence",
            "collection_queries": ["btc updown"],
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][:2] == ["ethereum", "btc updown"]
    assert payload["price_action_model"]["current_positive_analogue_queries"] == []
    analogue = payload["price_action_current_positive_analogues"]
    assert analogue["state"] == "blocked_by_model_label_headroom"
    assert analogue["targets"] == []
    blocked = analogue["blocked_targets"][0]
    assert blocked["recommended_collection_query"] == "fed"
    assert blocked["can_clear_model_label_hurdle"] is False
    assert blocked["max_possible_return"] < blocked["model_label_minimum_return"]
    assert "too high to ever clear" in payload["summary"]


def test_research_focus_uses_edge_attribution_to_separate_cost_and_model_drags(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "edge_attribution.json",
        {
            "status": "ok",
            "primary_drag_counts": {"spread_slippage": 1, "model_edge_failed_to_transfer": 1},
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "family": "macro_rates",
                    "decision_pnl_usdc": -0.2,
                    "entry_edge_usdc": 0.7,
                    "spread_slippage_cost_usdc": 0.4,
                    "line_movement_usdc": -0.1,
                    "mean_final_clv": -0.005,
                    "primary_drag": "spread_slippage",
                    "recommended_action": "tighten_liquidity_spread_or_reduce_size",
                },
                {
                    "signal_cohort": "tennis_tennis_winner",
                    "family": "tennis_tennis_winner",
                    "decision_pnl_usdc": -2.0,
                    "entry_edge_usdc": 0.5,
                    "spread_slippage_cost_usdc": 0.01,
                    "line_movement_usdc": -1.2,
                    "mean_final_clv": -0.06,
                    "primary_drag": "model_edge_failed_to_transfer",
                    "recommended_action": "collect_more_or_retrain_before_promotion",
                },
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][0] == "fed"
    assert payload["edge_attribution"]["collection_queries"] == ["fed"]
    assert payload["edge_attribution"]["cost_driven"][0]["cohort"] == "macro_rates"
    assert payload["edge_attribution"]["model_driven"][0]["cohort"] == "tennis_tennis_winner"
    assert "cost/quote-quality constrained" in payload["summary"]


def test_research_focus_consumes_attribution_clv_and_sweep_as_collection_only(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "signal_cohort_pnl.json",
        {
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "total_pnl_usdc": 1.0,
                    "roi": 0.08,
                    "promotion_ready_score": 4,
                    "promotion_ready_checks": 6,
                },
                {
                    "signal_cohort": "tennis_tennis_winner",
                    "total_pnl_usdc": 1.0,
                    "roi": 0.08,
                    "promotion_ready_score": 4,
                    "promotion_ready_checks": 6,
                },
            ]
        },
    )
    write_json(
        cfg.governance_root / "edge_attribution.json",
        {
            "status": "ok",
            "cohorts": [
                {
                    "signal_cohort": "macro_rates",
                    "attribution_class": "cost_dominated",
                    "total_pnl_usdc": -0.2,
                    "execution_cost_usdc": 0.7,
                    "line_movement_usdc": 0.4,
                    "recommended_action": "work passive entries and tighter quote collection",
                },
                {
                    "signal_cohort": "tennis_tennis_winner",
                    "attribution_class": "model_direction_not_confirmed",
                    "total_pnl_usdc": -1.1,
                    "execution_cost_usdc": 0.01,
                    "line_movement_usdc": -0.8,
                    "recommended_action": "re-model before promotion",
                },
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_json(
        cfg.governance_root / "closing_line_value.json",
        {
            "status": "ok",
            "positive_clv_cohorts": ["macro_rates", "esports_valorant_match"],
            "cohorts": [
                {"signal_cohort": "macro_rates", "clv_evidence": "positive_clv_evidence"},
                {"signal_cohort": "tennis_tennis_winner", "clv_evidence": "negative_clv_evidence"},
                {"signal_cohort": "esports_valorant_match", "clv_evidence": "positive_clv_evidence"},
            ],
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )
    write_json(
        cfg.output_root / "polymarket_algo" / "algo_sweep_summary.json",
        {
            "status": "ok",
            "decision": "sweep_candidate_validated_shadow_only",
            "selected": {
                "strategy": "tight_spread_join_bid_shadow",
                "tight_spread_maximum": 0.02,
                "minimum_book_imbalance": 0.4,
                "validation_fills": 4,
                "validation_pnl_usdc": 1.25,
            },
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][:2] == ["fed", "esports"]
    assert payload["watchlist"][0]["cohort"] == "macro_rates"
    assert payload["watchlist"][1]["cohort"] == "tennis_tennis_winner"
    assert payload["watchlist"][0]["priority_score"] > payload["watchlist"][1]["priority_score"]
    evidence = payload["evidence_inputs"]
    assert evidence["collection_queries_added"] == ["fed", "esports"]
    macro = next(row for row in evidence["priority_adjustments"] if row["cohort"] == "macro_rates")
    tennis = next(row for row in evidence["priority_adjustments"] if row["cohort"] == "tennis_tennis_winner")
    esports = next(row for row in evidence["priority_adjustments"] if row["cohort"] == "esports_valorant_match")
    assert macro["movement"] == "raised"
    assert macro["priority_delta"] > 0
    assert tennis["movement"] == "lowered"
    assert tennis["priority_delta"] < 0
    assert esports["movement"] == "raised"
    assert "positive_clv_cohort" in esports["reasons"]
    assert evidence["sweep_decision"] == "sweep_candidate_validated_shadow_only"
    assert "tight_spread_maximum=0.02" in payload["notes"][0]
    assert "minimum_book_imbalance=0.4" in payload["notes"][0]
    evidence_json = json.dumps(evidence).lower()
    assert "gate" not in evidence_json
    assert "threshold" not in evidence_json


def test_research_focus_optional_evidence_inputs_empty_without_artifacts(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "signal_cohort_pnl.json",
        {
            "cohorts": [
                {
                    "signal_cohort": "macro_economy",
                    "total_pnl_usdc": 1.0,
                    "roi": 0.01,
                    "promotion_ready_score": 5,
                    "promotion_ready_checks": 6,
                }
            ]
        },
    )

    payload = build_research_focus(cfg)

    assert payload["collection_queries"][0] == "economy"
    assert "base_priority_score" not in payload["watchlist"][0]
    assert "research_evidence_priority_delta" not in payload["watchlist"][0]
    assert payload["notes"] == []
    assert payload["evidence_inputs"]["priority_adjustments"] == []
    assert payload["evidence_inputs"]["collection_queries_added"] == []
    assert payload["evidence_inputs"]["edge_attribution_status"] is None
    assert payload["evidence_inputs"]["closing_line_value_status"] is None
    assert payload["evidence_inputs"]["sweep_decision"] == ""


def test_research_focus_routes_quote_audit_blockers_to_collection_only(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "baseline_quote_audit_by_cohort": [
                {
                    "signal_cohort": "exploratory_crypto_updown_live_model|crypto_eth_updown_daily|outcome=up",
                    "family": "crypto_eth_updown_daily",
                    "round_trips": 3,
                    "quote_consistent_round_trips": 1,
                    "quote_conflict_round_trips": 0,
                    "quote_unverified_round_trips": 2,
                    "quote_other_blocked_round_trips": 0,
                    "entry_snapshot_missing_round_trips": 2,
                    "exit_snapshot_missing_round_trips": 0,
                    "raw_pnl_usdc": 12.5,
                    "audited_pnl_usdc": 0.5,
                    "excluded_from_audit_pnl_usdc": 12.0,
                    "top_blocker_status": "unverified",
                    "top_blocker_count": 2,
                    "recommended_action": "collect independent bid/ask snapshots through the paper exit horizon",
                },
                {
                    "signal_cohort": "tennis_tennis_winner",
                    "family": "tennis_tennis_winner",
                    "round_trips": 1,
                    "quote_consistent_round_trips": 0,
                    "quote_conflict_round_trips": 1,
                    "quote_unverified_round_trips": 0,
                    "quote_other_blocked_round_trips": 0,
                    "raw_pnl_usdc": -1.0,
                    "audited_pnl_usdc": 0.0,
                    "excluded_from_audit_pnl_usdc": -1.0,
                    "top_blocker_status": "quote_conflict",
                    "top_blocker_count": 1,
                },
            ]
        },
    )

    payload = build_research_focus(cfg)

    quote_focus = payload["quote_audit_focus"]
    assert quote_focus["status"] == "quote_audit_blockers_present"
    assert quote_focus["decision_use"] == "collection_only_not_trade_authorisation"
    assert quote_focus["collection_queries"][:2] == ["eth updown", "tennis"]
    assert quote_focus["blocked_cohorts"][0]["cohort"] == "exploratory_crypto_updown_live_model|crypto_eth_updown_daily|outcome=up"
    assert quote_focus["blocked_cohorts"][0]["decision_use"] == "quote_audit_repair_collection_only_not_trade_authorisation"
    assert quote_focus["blocked_cohorts"][0]["entry_snapshot_missing_round_trips"] == 2
    assert quote_focus["blocked_cohorts"][0]["exit_snapshot_missing_round_trips"] == 0
    assert payload["raw_collection_queries"][:2] == ["eth updown", "tennis"]
    assert "eth updown" in payload["collection_queries"]
    assert "tennis" in payload["collection_queries"]
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False
    assert "proof repair only" in payload["notes"][0]


def test_research_focus_routes_proof_snapshot_gaps_to_collection_only(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "baseline_quote_audit_by_cohort": [
                {
                    "signal_cohort": "tennis_tennis_winner",
                    "family": "tennis_tennis_winner",
                    "round_trips": 2,
                    "quote_consistent_round_trips": 2,
                    "quote_conflict_round_trips": 0,
                    "quote_unverified_round_trips": 0,
                    "quote_other_blocked_round_trips": 0,
                    "proof_entry_snapshot_missing_round_trips": 1,
                    "proof_exit_snapshot_missing_round_trips": 1,
                    "raw_pnl_usdc": 3.25,
                    "audited_pnl_usdc": 3.25,
                    "excluded_from_audit_pnl_usdc": 0.0,
                    "top_blocker_status": "proof_snapshot_gap",
                    "top_blocker_count": 2,
                }
            ]
        },
    )

    payload = build_research_focus(cfg)

    quote_focus = payload["quote_audit_focus"]
    assert quote_focus["status"] == "quote_audit_blockers_present"
    assert quote_focus["collection_queries"] == ["tennis"]
    blocked = quote_focus["blocked_cohorts"][0]
    assert blocked["cohort"] == "tennis_tennis_winner"
    assert blocked["quote_conflict_round_trips"] == 0
    assert blocked["quote_unverified_round_trips"] == 0
    assert blocked["entry_snapshot_missing_round_trips"] == 1
    assert blocked["exit_snapshot_missing_round_trips"] == 1
    assert blocked["proof_snapshot_missing_round_trips"] == 2
    assert "profit-target proof" in blocked["recommended_action"]
    assert blocked["decision_use"] == "quote_audit_repair_collection_only_not_trade_authorisation"
    assert payload["raw_collection_queries"][0] == "tennis"
    assert "tennis" in payload["collection_queries"]
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False


def test_research_focus_prioritises_quote_repair_before_updown_guard(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "collection_queries": ["xrp updown", "ethereum"],
            "learning_state": "suppress_negative_price_action_and_broaden",
        },
    )
    write_json(
        cfg.output_root / "polymarket_price_action" / "paper_broker_round_trip_summary.json",
        {
            "baseline_quote_audit_by_cohort": [
                {
                    "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
                    "family": "crypto_btc_updown_daily",
                    "round_trips": 4,
                    "quote_consistent_round_trips": 2,
                    "quote_conflict_round_trips": 2,
                    "quote_unverified_round_trips": 0,
                    "quote_other_blocked_round_trips": 0,
                    "proof_entry_snapshot_missing_round_trips": 2,
                    "proof_exit_snapshot_missing_round_trips": 0,
                    "raw_pnl_usdc": 5.0,
                    "audited_pnl_usdc": 0.2,
                    "excluded_from_audit_pnl_usdc": 4.8,
                    "top_blocker_status": "quote_conflict",
                    "top_blocker_count": 2,
                }
            ]
        },
    )

    payload = build_research_focus(cfg)

    assert payload["quote_audit_priority_queries"] == ["btc updown"]
    assert payload["raw_collection_queries"][:3] == ["btc updown", "xrp updown", "ethereum"]
    assert payload["collection_queries"][0] == "btc updown"
    assert "xrp updown" not in payload["collection_queries"]
    assert payload["collection_query_guard"]["rejected_queries"][0] == {
        "query": "xrp updown",
        "family": "crypto_updown",
        "reason": "max_updown_queries",
    }
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False
