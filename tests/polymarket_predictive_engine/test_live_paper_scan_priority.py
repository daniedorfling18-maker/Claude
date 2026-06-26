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
                        "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
                        "promotion_ready_score": 5,
                        "promotion_ready_checks": 6,
                        "total_pnl_usdc": 4.5,
                        "roi": 0.15,
                        "monthly_run_rate_usdc": 900,
                        "probationary": True,
                    },
                    {
                        "signal_cohort": "exploratory_historical_rule|crypto_xrp_updown_5m|outcome=down",
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

    assert selected == ["xrp"]
    assert plan["adaptive_priority"]["priority_queries"] == ["xrp", "bitcoin"]
    assert "ethereum" not in plan["adaptive_priority"]["priority_queries"]
    assert plan["ordered_queries"][:2] == ["xrp", "bitcoin"]
