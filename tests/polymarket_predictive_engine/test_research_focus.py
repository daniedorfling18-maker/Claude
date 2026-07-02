from __future__ import annotations

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

    assert payload["collection_queries"][:2] == ["btc updown", "solana updown"]
    assert payload["price_action_model"]["model_needs_repricing_data"] is True
    assert "Strict price-action model needs profitable ask-to-future-bid" in payload["summary"]
    assert saved["collection_queries"][:2] == ["btc updown", "solana updown"]


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
    assert payload["collection_queries"][3:5] == ["btc updown", "solana updown"]
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

    assert payload["collection_queries"][:3] == ["xrp updown", "btc updown", "ethereum"]
    assert payload["price_action_model"]["historical_breadth_queries"] == ["xrp updown"]
    assert "near-positive historical buckets" in payload["summary"]


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
    assert payload["collection_queries"][4:6] == ["eth updown", "xrp updown"]
    assert payload["price_action_model"]["analogue_scan_needs_breadth"] is True
    assert payload["price_action_model"]["near_miss_candidate_queries"][:4] == ["fed", "world cup", "esports", "bitcoin"]
    assert "broaden evidence collection into near-miss markets" in payload["summary"]


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

    assert payload["collection_queries"][:3] == ["fed", "ethereum", "btc updown"]
    assert payload["price_action_model"]["current_positive_analogue_queries"] == ["fed"]
    assert payload["price_action_current_positive_analogues"]["state"] == "learning_targets_available"
    target = payload["price_action_current_positive_analogues"]["targets"][0]
    assert target["recommended_collection_query"] == "fed"
    assert target["decision_use"] == "forward_shadow_learning_target_not_trade_authorisation"
    assert target["robust_validation_roi_gap"] > 0
    assert "not a trade approval" in payload["summary"]
