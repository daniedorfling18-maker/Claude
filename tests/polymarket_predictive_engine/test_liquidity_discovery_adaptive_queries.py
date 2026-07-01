from __future__ import annotations

import importlib
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import write_json


discovery = importlib.import_module("scripts.run_polymarket_liquidity_discovery")


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")


def test_liquidity_discovery_prepends_model_validation_gap_queries(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "research_focus.json",
        {
            "status": "ok",
            "collection_queries": ["fed", "ethereum", "esports"],
            "price_action_model": {
                "validation_gap_needs_collection": True,
                "validation_gap_queries": ["fed", "ethereum", "esports"],
            },
        },
    )
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "learning_state": "collect_model_validation_gap_price_action_evidence",
            "collection_queries": ["economy", "fed"],
            "model_validation_gap_queries": ["economy", "fed"],
        },
    )

    adaptive_queries = discovery._adaptive_collection_queries(cfg)
    event_queries = discovery._event_queries(
        {"queries": ["world cup"], "broad_discovery_enabled": False},
        adaptive_queries=adaptive_queries,
    )
    public_queries = discovery._public_search_queries(
        {"public_search_queries": ["bitcoin"], "broad_discovery_enabled": False, "crypto_updown_date_search": {"enabled": False}},
        adaptive_queries=adaptive_queries,
    )

    assert adaptive_queries == ["fed", "ethereum", "esports", "economy"]
    assert event_queries[:4] == ["fed", "ethereum", "esports", "economy"]
    assert event_queries[-1] == "world cup"
    assert public_queries[:4] == ["fed", "ethereum", "esports", "economy"]
    assert public_queries[-1] == "bitcoin"
