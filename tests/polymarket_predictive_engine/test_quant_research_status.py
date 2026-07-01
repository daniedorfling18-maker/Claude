from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.quant_research_status import build_quant_research_status
from polymarket_predictive_engine.utils import read_json, write_json


def test_quant_research_status_reports_course_modules_and_bot_gates(tmp_path: Path):
    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    write_json(
        cfg.output_root / "polymarket_price_action" / "price_action_model_summary.json",
        {
            "status": "trained",
            "decision": "collect_more_bid_ask_price_action_model_evidence",
            "promotion_ready": False,
            "training_events": 120,
            "validation_rows": 40,
            "validation_selected": {"selected_roi": 0.02},
            "validation_selected_risk": {"max_drawdown": -0.04},
            "quant_promotion_decision": {"approved": False, "reason": "forward evidence still required"},
            "validation_blockers": ["selected validation P&L is not positive"],
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "learning_state": "collect_more_price_action_evidence",
            "next_action": "Collect BTC/SOL repricing examples",
            "collection_queries": ["btc updown", "solana updown"],
        },
    )

    payload = build_quant_research_status(cfg)
    written = read_json(cfg.governance_root / "quant_research_status.json")

    assert payload["implementation_complete"] is True
    assert payload["implemented_modules"] == payload["total_modules"] == 8
    assert payload["price_action_model"]["promotion_ready"] is False
    assert "btc updown" in payload["price_action_feedback"]["collection_queries"]
    assert written["trading_posture"] == "shadow_research_only_until_positive_forward_bid_ask_evidence"
