import importlib.util
import json
from pathlib import Path

from polymarket_predictive_engine.config import EngineConfig


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run_polymarket_live_paper_loop.py"


def _load_loop_module():
    spec = importlib.util.spec_from_file_location("run_polymarket_live_paper_loop", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_adaptive_scan_priority_prefers_positive_near_promoted_cohorts(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_historical_rule|crypto_updown_5m|outcome=down",
                        "promotion_ready_score": 6,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": -100,
                        "roi": -0.25,
                        "monthly_run_rate_usdc": -5000,
                    },
                    {
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_daily|outcome=up",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 4.5,
                        "roi": 0.15,
                        "monthly_run_rate_usdc": 900,
                        "probationary": True,
                    },
                    {
                        "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_event|outcome=down",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 21,
                        "roi": 1.05,
                        "monthly_run_rate_usdc": 4000,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "rotate",
                "max_queries_per_cycle": 1,
                "prioritize_near_promoted": True,
                "adaptive_priority_require_positive_evidence": True,
                "queries": ["world cup", "bitcoin", "ethereum", "xrp"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["bitcoin"]
    assert plan["adaptive_priority"]["priority_queries"] == ["bitcoin", "xrp"]
    assert "ethereum" not in plan["adaptive_priority"]["priority_queries"]
    assert plan["ordered_queries"][:2] == ["bitcoin", "xrp"]
    assert plan["adaptive_priority"]["top_cohorts"][0]["probationary"] is True


def test_query_attempts_include_family_alias_fallbacks(tmp_path):
    loop = _load_loop_module()
    cfg = EngineConfig(
        raw={
            "paper_market_scan": {
                "query_aliases": {
                    "xrp": ["ripple", "crypto"],
                    "bitcoin": ["btc", "crypto"],
                }
            }
        },
        path=tmp_path / "cfg.yaml",
    )

    assert loop._query_attempts(cfg, "xrp") == ["xrp", "ripple", "crypto"]
    assert loop._query_attempts(cfg, "bitcoin") == ["bitcoin", "btc", "crypto"]
    assert loop._query_attempts(cfg, "world cup") == ["world cup"]


def test_worldcup_cohort_prioritises_exact_winner_queries():
    loop = _load_loop_module()

    keys = loop._cohort_to_query_keys("worldcup_2026_winner_fundamental")

    assert keys[:2] == ["fifaworldcup", "worldcupwinner"]
    assert "worldcup" in keys


def test_static_scan_queries_can_target_worldcup_winner_before_generic_worldcup(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "prioritize_near_promoted": False,
                "broad_repricing_reserve_enabled": False,
                "queries": ["fifa world cup", "world cup winner", "world cup", "bitcoin"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["fifa world cup", "world cup winner", "world cup"]
    assert plan["configured_queries"][:3] == ["fifa world cup", "world cup winner", "world cup"]


def test_batch_mode_scans_top_evidence_families_together(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_daily|outcome=up",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 4.5,
                        "roi": 0.15,
                        "monthly_run_rate_usdc": 900,
                        "probationary": True,
                    },
                    {
                        "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_event|outcome=down",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 21,
                        "roi": 1.05,
                        "monthly_run_rate_usdc": 4000,
                    },
                    {
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_sol_updown_event|outcome=up",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 10,
                        "roi": 1.0,
                        "monthly_run_rate_usdc": 1600,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "queries": ["world cup", "bitcoin", "ethereum", "solana", "xrp", "tennis"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["bitcoin", "xrp", "solana"]
    assert plan["scan_sequence"] == 1
    assert plan["adaptive_priority"]["priority_queries"][:3] == ["bitcoin", "xrp", "solana"]


def test_batch_mode_targets_updown_queries_for_positive_updown_cohorts(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_daily|outcome=up",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 4.5,
                        "roi": 0.15,
                        "monthly_run_rate_usdc": 900,
                        "probationary": True,
                    },
                    {
                        "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_event|outcome=down",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 21,
                        "roi": 1.05,
                        "monthly_run_rate_usdc": 4000,
                    },
                    {
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_sol_updown_event|outcome=up",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 10,
                        "roi": 1.0,
                        "monthly_run_rate_usdc": 1600,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "queries": [
                    "world cup",
                    "btc updown",
                    "xrp updown",
                    "solana updown",
                    "bitcoin",
                    "solana",
                    "xrp",
                ],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["btc updown", "xrp updown", "solana updown"]
    assert plan["adaptive_priority"]["priority_queries"][:3] == ["btc updown", "xrp updown", "solana updown"]


def test_blank_environment_overrides_do_not_disable_live_batch_mode(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.setenv("POLYMARKET_SCAN_QUERY_MODE", "")
    monkeypatch.setenv("POLYMARKET_MAX_SCAN_QUERIES", "")
    monkeypatch.setenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", "")

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 2,
                        "roi": 0.08,
                        "monthly_run_rate_usdc": 120,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 2,
                "prioritize_near_promoted": True,
                "queries": ["world cup", "bitcoin", "xrp"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert plan["mode"] == "batch"
    assert plan["max_queries_per_cycle"] == 2
    assert plan["adaptive_priority"]["enabled"] is True
    assert selected[0] == "bitcoin"
    assert len(selected) == 2


def test_research_focus_feedback_queries_priority_and_suppression(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "research_focus.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "summary": "Prioritise positive price-action tennis cohort.",
                "collection_queries": ["tennis"],
                "suppressed_queries": ["bitcoin"],
                "price_action_feedback": {
                    "learning_state": "collect_more_positive_price_action_evidence",
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "prioritize_near_promoted": True,
                "queries": ["bitcoin", "tennis", "world cup"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["tennis", "world cup", "bitcoin"]
    assert plan["adaptive_priority"]["research_focus_queries"] == ["tennis"]
    assert plan["adaptive_priority"]["suppressed_queries"] == ["bitcoin"]


def test_validation_gap_research_queries_are_injected_and_prioritised(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "research_focus.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "summary": "Collect validation positives in fed/ethereum/esports.",
                "collection_queries": ["fed", "ethereum", "esports"],
                "price_action_model": {
                    "validation_gap_needs_collection": True,
                    "validation_gap_queries": ["fed", "ethereum", "esports"],
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "prioritize_near_promoted": True,
                "queries": ["world cup", "ethereum", "bitcoin"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["fed", "ethereum", "esports"]
    assert plan["configured_queries"] == ["world cup", "ethereum", "bitcoin"]
    assert plan["injected_research_focus_queries"] == ["fed", "esports"]
    assert plan["adaptive_priority"]["research_focus_queries"] == ["fed", "ethereum", "esports"]
    assert plan["adaptive_priority"]["research_focus_validation_gap_needs_collection"] is True
    assert plan["adaptive_priority"]["top_cohorts"][0]["validation_gap_needs_collection"] is True


def test_quarantined_5m_crypto_cohorts_do_not_drive_scan_priority(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
                        "promotion_ready_score": 6,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 25,
                        "roi": 0.8,
                        "monthly_run_rate_usdc": 4000,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 1,
                "prioritize_near_promoted": True,
                "queries": ["world cup", "btc updown", "bitcoin"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert selected == ["world cup"]
    assert plan["adaptive_priority"]["priority_queries"] == []


def test_feedback_broaden_state_reserves_exploration_slot_in_batch_scan(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 4.5,
                        "roi": 0.15,
                        "monthly_run_rate_usdc": 900,
                    },
                    {
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_sol_updown_event|outcome=up",
                        "promotion_ready_score": 4,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 10,
                        "roi": 0.5,
                        "monthly_run_rate_usdc": 1600,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (governance / "price_action_feedback.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "learning_state": "suppress_negative_price_action_and_broaden",
                "collection_queries": ["world cup", "tennis", "bitcoin", "ethereum"],
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "prioritize_near_promoted": True,
                "feedback_broaden_reserve_slots": 1,
                "queries": ["btc updown", "solana updown", "bitcoin", "world cup", "tennis", "ethereum"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert set(selected[:2]) == {"btc updown", "solana updown"}
    assert selected[2] == "world cup"
    assert plan["adaptive_priority"]["feedback_learning_state"] == "suppress_negative_price_action_and_broaden"
    assert plan["adaptive_priority"]["feedback_broaden_queries"][:2] == ["world cup", "tennis"]


def test_broad_repricing_reserve_keeps_cross_event_scan_slot(tmp_path, monkeypatch):
    loop = _load_loop_module()
    monkeypatch.delenv("POLYMARKET_SCAN_QUERY_MODE", raising=False)
    monkeypatch.delenv("POLYMARKET_MAX_SCAN_QUERIES", raising=False)
    monkeypatch.delenv("POLYMARKET_ADAPTIVE_SCAN_PRIORITY", raising=False)

    governance = tmp_path / "outputs" / "polymarket_model_governance"
    governance.mkdir(parents=True)
    (governance / "signal_cohort_pnl.json").write_text(
        json.dumps(
            {
                "cohorts": [
                    {
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_btc_updown_daily|outcome=down",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 5,
                        "roi": 0.20,
                        "monthly_run_rate_usdc": 250,
                    },
                    {
                        "signal_cohort": "exploratory_crypto_updown_live_model|crypto_sol_updown_event|outcome=up",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 4,
                        "roi": 0.18,
                        "monthly_run_rate_usdc": 200,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    cfg = EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "paper_market_scan": {
                "mode": "batch",
                "max_queries_per_cycle": 3,
                "prioritize_near_promoted": True,
                "broad_repricing_reserve_enabled": True,
                "broad_repricing_reserve_slots": 1,
                "broad_repricing_queries": ["fed", "tennis"],
                "queries": ["btc updown", "solana updown", "bitcoin"],
            },
        },
        path=tmp_path / "cfg.yaml",
    )

    selected, plan = loop._select_scan_queries(cfg, "world cup", scan_sequence=1)

    assert set(selected[:2]) == {"btc updown", "solana updown"}
    assert selected[2] == "fed"
    assert plan["injected_broad_repricing_queries"] == ["fed", "tennis"]
    assert plan["broad_repricing_reserved_queries"] == ["fed"]
    assert plan["broad_repricing_reserve_enabled"] is True
