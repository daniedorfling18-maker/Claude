import csv

import pytest

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.models.calibration_v2 import train_calibration_model
from polymarket_predictive_engine.paper_edge_simulator import simulate_paper_edge
from polymarket_predictive_engine.paper_session import run_paper_session
from polymarket_predictive_engine.readiness import paper_trade_readiness


def _cfg(tmp_path):
    return EngineConfig(
        raw={
            "paths": {"data_root": str(tmp_path), "output_root": str(tmp_path / "outputs")},
            "trading": {"mode": "paper"},
            "risk": {"minimum_edge": 0.03, "bankroll": 1000, "minimum_confidence": 0.0,
                     "maximum_spread": 1.0, "minimum_liquidity": 0, "kelly_cap": 0.01},
            "calibration": {"buckets": 5},
            "calibration_v2": {"minimum_training_rows": 20},
            "governance_thresholds": {"min_training_rows": 20, "min_paper_labels": 10},
            "paper_edge_simulator": {"oos_test_fraction": 0.3, "minimum_edge": 0.03},
        },
        path=tmp_path / "cfg.yaml",
    )


def _write(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def _dataset(tmp_path, n_markets=60):
    """Uninformative market (midpoint 0.5) so the calibrator finds no real edge."""
    train = tmp_path / "outputs" / "polymarket_training"
    feats, labels, res = [], [], []
    for mi in range(n_markets):
        target = mi % 2
        res.append({"resolution_quality": "clean_settlement", "token_id": f"t{mi}", "target": target,
                    "condition_id": f"m{mi}", "market_slug": f"m{mi}"})
        for s in range(2):
            ts = f"2026-01-{1 + mi // 30:02d}T{mi % 24:02d}:{s * 30:02d}:00Z"
            feats.append({"market_id": f"m{mi}", "token_id": f"t{mi}", "prediction_timestamp": ts,
                          "midpoint": 0.5, "implied_probability": 0.5, "executable_buy_price": 0.5})
            labels.append({"market_id": f"m{mi}", "token_id": f"t{mi}", "prediction_timestamp": ts,
                           "target": target, "horizon": "all_valid"})
    _write(train / "features_v2.csv", feats, ["market_id", "token_id", "prediction_timestamp", "midpoint", "implied_probability", "executable_buy_price"])
    _write(train / "labels.csv", labels, ["market_id", "token_id", "prediction_timestamp", "target", "horizon"])
    _write(train / "market_resolutions.csv", res, ["resolution_quality", "token_id", "target", "condition_id", "market_slug"])


def test_simulate_paper_edge_is_out_of_sample(tmp_path):
    cfg = _cfg(tmp_path)
    _dataset(tmp_path)
    train_calibration_model(cfg)
    summary = simulate_paper_edge(cfg)
    assert summary["status"] == "paper_only"
    assert summary["evaluation"] == "out_of_sample_by_market"
    assert summary["in_sample"] is False
    assert summary["train_markets"] + summary["test_markets"] == 60
    assert summary["test_markets"] > 0 and summary["test_rows"] > 0
    assert [c["edge_threshold"] for c in summary["paper_pnl_by_edge_threshold"]] == [0.0, 0.01, 0.02, 0.03, 0.05]


def test_simulate_paper_edge_refuses_without_model(tmp_path):
    cfg = _cfg(tmp_path)
    _dataset(tmp_path)
    with pytest.raises(RuntimeError):
        simulate_paper_edge(cfg)


def test_paper_readiness_blocks_without_model(tmp_path):
    cfg = _cfg(tmp_path)
    _dataset(tmp_path)
    gate = paper_trade_readiness(cfg)
    assert gate["approved_for_paper_trading"] is False
    assert any("calibration model" in r for r in gate["blockers"])


def test_paper_readiness_approves_with_model_and_labels(tmp_path):
    cfg = _cfg(tmp_path)
    _dataset(tmp_path)
    train_calibration_model(cfg)
    gate = paper_trade_readiness(cfg)
    assert gate["approved_for_paper_trading"] is True
    assert gate["resolved_labels"] == 60


def test_paper_readiness_blocks_on_kill_switch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _dataset(tmp_path)
    train_calibration_model(cfg)
    monkeypatch.setenv("POLYMARKET_KILL_SWITCH", "1")
    gate = paper_trade_readiness(cfg)
    assert gate["approved_for_paper_trading"] is False
    assert any("kill switch" in r for r in gate["blockers"])


def test_run_paper_session_runs_and_reports_oos(tmp_path):
    cfg = _cfg(tmp_path)
    _dataset(tmp_path)
    report = run_paper_session(cfg)
    assert report["status"] == "ran"
    assert report["live_trading"] is False
    assert report["headline"]["evaluation"] == "out_of_sample_by_market"
    assert "expected_profitable_oos" in report["headline"]


def test_run_paper_session_blocks_in_live_mode(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.raw["trading"]["mode"] = "live"
    _dataset(tmp_path)
    report = run_paper_session(cfg)
    assert report["status"] == "blocked"
    assert report["live_trading"] is False
