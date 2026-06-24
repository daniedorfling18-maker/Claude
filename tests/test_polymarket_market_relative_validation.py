from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.market_relative_validation import (
    assert_no_leakage_columns,
    join_clean_settled_predictions,
    simulate_roi,
    validate_market_relative_model,
    walk_forward_market_splits,
)
from polymarket_predictive_engine.readiness import paper_live_promotion_gate
from polymarket_predictive_engine.utils import write_csv, write_json


def _cfg(tmp_path: Path, *, min_rows: int = 3, min_markets: int = 3, min_labels: int = 1) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"data_root": str(tmp_path), "output_root": str(tmp_path / "outputs")},
            "categories": ["all"],
            "schema": {
                "timestamp_fields": ["prediction_timestamp", "snapshot_timestamp"],
                "market_id_fields": ["market_id", "condition_id", "market_slug"],
                "token_id_fields": ["token_id", "asset_id"],
            },
            "freshness_thresholds": {"collector_stale_minutes": 60},
            "calibration": {"buckets": 10},
            "costs": {"transaction_cost": 0.0, "slippage": 0.01},
            "risk": {
                "bankroll": 1000,
                "minimum_edge": 0.03,
                "maximum_single_market_exposure": 0.02,
                "liquidity_cap_fraction": 0.05,
            },
            "paper_trading": {"starting_cash": 1000},
            "live_trading": {"approval_file": str(tmp_path / "approval.yaml")},
            "market_relative_validation": {
                "min_rows": min_rows,
                "min_markets": min_markets,
                "min_train_markets": 1,
                "walk_forward_splits": 3,
                "bootstrap_iterations": 200,
                "min_brier_gain_ci_low": 0.0,
            },
            "governance_thresholds": {
                "min_validation_rows": min_rows,
                "min_validation_markets": min_markets,
                "min_resolved_labels": min_labels,
                "target_resolved_labels": 999,
            },
        },
        path=tmp_path / "config.yaml",
    )


def _write_prediction_label_set(cfg: EngineConfig, rows: list[tuple[str, float, float, int]]) -> None:
    predictions = []
    labels = []
    resolutions = []
    for i, (market_id, model_p, market_p, target) in enumerate(rows):
        token_id = f"token-{i}"
        ts = f"2026-01-{i + 1:02d}T00:00:00Z"
        predictions.append(
            {
                "market_id": market_id,
                "token_id": token_id,
                "prediction_timestamp": ts,
                "calibrated_probability": model_p,
                "market_midpoint": market_p,
                "executable_price": market_p + 0.01,
                "spread": 0.02,
                "liquidity": 100,
            }
        )
        label = {
            "market_id": market_id,
            "token_id": token_id,
            "prediction_timestamp": ts,
            "target": target,
            "horizon": "all_valid",
            "resolution_quality": "clean_settlement",
        }
        labels.append(label)
        resolutions.append({**label, "market_slug": market_id})
    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", predictions)
    write_csv(cfg.output_root / "polymarket_training" / "labels.csv", labels)
    write_csv(cfg.output_root / "polymarket_training" / "historical_resolutions.csv", resolutions)


def test_market_baseline_is_separate_from_model_probability() -> None:
    joined, rejected = join_clean_settled_predictions(
        [
            {
                "market_id": "m1",
                "token_id": "t1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                "calibrated_probability": "0.70",
                "market_midpoint": "0.55",
            }
        ],
        [
            {
                "market_id": "m1",
                "token_id": "t1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                "target": "1",
                "horizon": "all_valid",
                "resolution_quality": "clean_settlement",
            }
        ],
    )
    assert not rejected
    assert joined[0]["model_probability"] == 0.70
    assert joined[0]["market_probability"] == 0.55
    assert joined[0]["model_probability_source"] != joined[0]["market_probability_source"]


def test_leakage_columns_are_rejected() -> None:
    with pytest.raises(ValueError):
        assert_no_leakage_columns(["momentum", "winner", "price"])
    with pytest.raises(ValueError):
        assert_no_leakage_columns(["feature_a", "resolution_status"])


def test_walk_forward_train_test_markets_do_not_overlap() -> None:
    rows = [
        {"validation_unit_id": f"m{i}", "prediction_timestamp": f"2026-01-{i + 1:02d}T00:00:00Z"}
        for i in range(8)
    ]
    splits = walk_forward_market_splits(rows, min_train_markets=2, n_splits=3)
    assert splits
    for split in splits:
        assert set(split["train_units"]).isdisjoint(set(split["test_units"]))


def test_insufficient_sample_size_refuses_approval(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, min_rows=10, min_markets=2)
    _write_prediction_label_set(
        cfg,
        [("m1", 0.80, 0.60, 1), ("m2", 0.20, 0.40, 0), ("m3", 0.75, 0.55, 1)],
    )
    summary = validate_market_relative_model(cfg)
    assert summary["approved_for_paper_trading"] is False
    assert any("insufficient validation rows" in reason for reason in summary["blockers"])


def test_model_that_merely_copies_midpoint_is_not_approved(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, min_rows=3, min_markets=3)
    _write_prediction_label_set(
        cfg,
        [("m1", 0.60, 0.60, 1), ("m2", 0.40, 0.40, 0), ("m3", 0.65, 0.65, 1), ("m4", 0.35, 0.35, 0)],
    )
    summary = validate_market_relative_model(cfg)
    assert summary["approved_for_paper_trading"] is False
    assert summary["model_copies_market_midpoint"] is True
    assert any("copy" in reason or "market midpoint" in reason for reason in summary["blockers"])


def test_readiness_gate_blocks_without_statistically_credible_market_skill(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, min_rows=3, min_markets=3, min_labels=1)
    write_csv(
        cfg.output_root / "polymarket_training" / "historical_resolutions.csv",
        [
            {"market_id": "m1", "token_id": "t1", "target": "1", "resolution_quality": "clean_settlement"},
            {"market_id": "m2", "token_id": "t2", "target": "0", "resolution_quality": "clean_settlement"},
        ],
    )
    write_json(
        cfg.governance_root / "market_relative_validation_summary.json",
        {
            "status": "blocked",
            "approved_for_paper_trading": False,
            "beats_market_oos": False,
            "statistically_credible_market_relative_skill": False,
            "model_copies_market_midpoint": False,
            "oos": {"sample_size": 50, "markets": 10, "brier_improvement_vs_market": -0.001},
        },
    )
    gate = paper_live_promotion_gate(cfg)
    assert gate["approved_for_paper_trading"] is False
    assert any("market midpoint" in reason for reason in gate["paper_blockers"])


def test_roi_simulation_uses_executable_price_slippage_liquidity_and_exposure_caps() -> None:
    summary, trades = simulate_roi(
        [
            {
                "market_id": "m1",
                "token_id": "t1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                "model_probability": 0.80,
                "market_probability": 0.50,
                "yes_executable_price": 0.52,
                "no_executable_price": 0.50,
                "spread": 0.02,
                "liquidity": 100,
                "target": 1,
            }
        ],
        bankroll=1000,
        minimum_edge=0.03,
        slippage=0.01,
        transaction_cost=0.0,
        liquidity_cap_fraction=0.05,
        maximum_single_market_exposure=0.02,
    )
    assert summary["n_trades"] == 1
    assert trades[0]["net_executable_price"] == pytest.approx(0.53)
    assert trades[0]["simulated_spend"] <= 5.0
    assert summary["roi"] is not None
