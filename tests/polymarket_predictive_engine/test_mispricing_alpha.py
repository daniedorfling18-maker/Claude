from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.mispricing_alpha import (
    apply_mispricing_alpha,
    train_mispricing_alpha_model,
)
from polymarket_predictive_engine.strategy import generate_signals
from polymarket_predictive_engine.utils import read_csv_rows, write_csv


def _config(tmp_path: Path):
    raw = yaml.safe_load(
        Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    )
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["risk"].update(
        {
            "minimum_edge": 0.03,
            "minimum_confidence": 0.0,
            "minimum_liquidity": 0,
            "maximum_spread": 1.0,
            "minimum_time_to_close_minutes": 0,
        }
    )
    raw["governance_thresholds"]["min_paper_labels"] = 0
    raw["mispricing_alpha"].update(
        {
            "minimum_rows": 10,
            "minimum_cell_rows": 1,
            "minimum_live_cell_rows": 1,
            "bias_prior_strength": 1,
            "bias_alpha_shrinkage": 1.0,
            "model_residual_shrinkage": 0.0,
            "max_alpha_probability_deviation_from_market": 0.5,
            "spread_penalty_weight": 0.0,
            "low_liquidity_penalty": 0.0,
            "missing_liquidity_penalty": 0.0,
            "depth_imbalance_penalty_weight": 0.0,
            "volatility_penalty_weight": 0.0,
            "uncertainty_penalty_weight": 0.0,
            "market_overround_penalty_weight": 0.5,
            "model_overround_penalty_weight": 0.0,
        }
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_training_set(cfg) -> None:
    features = []
    labels = []
    for i in range(20):
        market_id = f"bias-market-{i}"
        token_id = f"bias-token-{i}"
        timestamp = f"2026-01-{(i % 9) + 1:02d}T00:00:00Z"
        target = 1 if i < 16 else 0
        features.append(
            {
                "market_id": market_id,
                "market_slug": market_id,
                "token_id": token_id,
                "prediction_timestamp": timestamp,
                "category": "synthetic",
                "midpoint": "0.4",
                "implied_probability": "0.4",
                "executable_buy_price": "0.4",
                "best_ask": "0.4",
                "spread": "0.01",
                "liquidity": "1000",
                "hours_to_close": "24",
            }
        )
        labels.append(
            {
                "market_id": market_id,
                "token_id": token_id,
                "prediction_timestamp": timestamp,
                "horizon": "all_valid",
                "target": str(target),
            }
        )
    train_root = cfg.output_root / "polymarket_training"
    write_csv(train_root / "features_v2.csv", features)
    write_csv(train_root / "labels.csv", labels)


def test_alpha_bias_model_creates_conservative_edge_lower_bound(tmp_path):
    cfg = _config(tmp_path)
    _write_training_set(cfg)

    model = train_mispricing_alpha_model(cfg)
    assert model["status"] == "trained"

    predictions = [
        {
            "market_id": "live-market-1",
            "market_slug": "live-market-1",
            "token_id": "live-token-1",
            "prediction_timestamp": "2026-02-01T00:00:00Z",
            "category": "synthetic",
            "market_midpoint": "0.4",
            "raw_probability": "0.4",
            "calibrated_probability": "0.4",
            "model_probability": "0.4",
            "executable_price": "0.4",
            "spread": "0.01",
            "liquidity": "1000",
            "time_to_close_hours": "24",
            "confidence": "1",
        }
    ]
    scored = apply_mispricing_alpha(cfg, predictions)

    assert scored[0]["alpha_status"] == "scored"
    assert float(scored[0]["alpha_probability"]) > 0.7
    assert float(scored[0]["edge_lower_bound"]) > 0.3
    assert scored[0]["alpha_trade_candidate"] is True


def test_alpha_cross_market_overround_penalises_complete_set(tmp_path):
    cfg = _config(tmp_path)
    _write_training_set(cfg)
    train_mispricing_alpha_model(cfg)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "same-market",
                "token_id": "yes-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.7",
                "calibrated_probability": "0.7",
                "executable_price": "0.69",
                "time_to_close_hours": "24",
                "confidence": "1",
            },
            {
                "market_id": "same-market",
                "token_id": "no-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.6",
                "calibrated_probability": "0.6",
                "executable_price": "0.59",
                "time_to_close_hours": "24",
                "confidence": "1",
            },
        ],
    )

    assert float(scored[0]["cross_market_overround"]) > 0.29
    assert float(scored[0]["alpha_cross_market_penalty"]) > 0.14
    assert float(scored[0]["edge_lower_bound"]) < float(scored[0]["alpha_raw_edge"])


def test_alpha_uses_capped_fundamental_probability_overlay(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "fundamental_residual_shrinkage": 0.5,
            "max_fundamental_adjustment": 0.04,
            "max_alpha_probability_deviation_from_market": 0.25,
            "market_overround_penalty_weight": 0.0,
        }
    )
    write_csv(
        tmp_path / "inputs" / "polymarket" / "model_probabilities.csv",
        [{"token_id": "fundamental-token", "probability": "0.70"}],
    )

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "fundamental-market",
                "token_id": "fundamental-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.40",
                "calibrated_probability": "0.40",
                "executable_price": "0.40",
                "time_to_close_hours": "24",
                "confidence": "1",
            }
        ],
    )

    assert float(scored[0]["fundamental_probability"]) == 0.70
    assert float(scored[0]["fundamental_adjustment"]) == 0.04
    assert float(scored[0]["alpha_probability"]) == 0.44
    assert float(scored[0]["edge_lower_bound"]) > 0.03
    assert scored[0]["alpha_trade_candidate"] is True


def test_alpha_caps_legacy_model_residual_on_tiny_longshots(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.25,
            "max_model_residual_adjustment": 0.02,
            "use_fundamental_probabilities": False,
        }
    )

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "longshot-market",
                "token_id": "longshot-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.001",
                "calibrated_probability": "0.81",
                "executable_price": "0.001",
                "time_to_close_hours": "24",
                "confidence": "1",
            }
        ],
    )

    assert float(scored[0]["alpha_model_residual_raw_adjustment"]) > 0.20
    assert float(scored[0]["alpha_model_residual_adjustment"]) == 0.02
    assert round(float(scored[0]["alpha_probability"]), 6) == 0.021


def test_strategy_uses_alpha_lower_bound_gate(tmp_path):
    cfg = _config(tmp_path)
    predictions_path = cfg.output_root / "polymarket_predictions" / "predictions.csv"
    write_csv(
        predictions_path,
        [
            {
                "market_id": "market-low-alpha",
                "token_id": "token-low-alpha",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.4",
                "calibrated_probability": "0.9",
                "model_probability": "0.9",
                "executable_price": "0.4",
                "edge": "0.5",
                "edge_lower_bound": "0.01",
                "alpha_trade_candidate": "false",
                "confidence": "1",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
            }
        ],
    )

    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )

    assert approved == []
    assert len(rejected) == 1
    assert "alpha lower-bound edge" in rejected[0]["rejection_reason"]
    saved = read_csv_rows(cfg.output_root / "polymarket_predictions" / "rejected_signals.csv")
    assert saved
