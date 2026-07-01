from __future__ import annotations

import importlib.util
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.utils import write_json


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "run_polymarket_liquidity_discovery",
    ROOT / "scripts" / "run_polymarket_liquidity_discovery.py",
)
assert SPEC and SPEC.loader
discovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discovery)


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


def test_liquidity_discovery_expands_updown_feedback_queries_without_5m_aliases(tmp_path):
    cfg = _cfg(tmp_path)
    write_json(
        cfg.governance_root / "price_action_feedback.json",
        {
            "status": "ok",
            "collection_queries": ["btc updown", "solana updown"],
        },
    )

    adaptive_queries = discovery._adaptive_collection_queries(cfg)
    settings = {
        "queries": ["world cup"],
        "broad_discovery_enabled": False,
        "crypto_updown_date_search": {"enabled": True, "days_ahead": 1},
        "query_aliases": {
            "btc updown": ["bitcoin up or down", "btc updown 5m"],
            "solana updown": ["solana up or down", "solana updown 5m"],
        },
    }

    event_queries = discovery._event_queries(settings, adaptive_queries=adaptive_queries)
    public_queries = discovery._public_search_queries(
        {"**": "unused", **settings, "public_search_queries": []},
        adaptive_queries=adaptive_queries,
    )

    for queries in (event_queries, public_queries):
        assert "btc updown" in queries
        assert "bitcoin up or down" in queries
        assert "bitcoin updown" in queries
        assert "solana updown" in queries
        assert "solana up or down" in queries
        assert all("5m" not in query for query in queries)
    assert not any(query.startswith("bitcoin up or down July") for query in event_queries)
    assert not any(query.startswith("solana up or down July") for query in event_queries)
    assert any(query.startswith("bitcoin up or down July") for query in public_queries)
    assert any(query.startswith("solana up or down July") for query in public_queries)
    assert len(event_queries) <= 28
    assert len(public_queries) <= 48
