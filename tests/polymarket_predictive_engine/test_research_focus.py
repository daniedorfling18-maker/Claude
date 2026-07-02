from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.research_focus import build_research_focus
from polymarket_predictive_engine.utils import read_json, write_json


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
