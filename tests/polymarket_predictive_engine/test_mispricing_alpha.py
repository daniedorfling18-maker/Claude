from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

import polymarket_predictive_engine.mispricing_alpha as mispricing_alpha_module
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.mispricing_alpha import (
    apply_mispricing_alpha,
    train_mispricing_alpha_model,
)
from polymarket_predictive_engine.strategy import _same_category_label_counts, generate_signals
from polymarket_predictive_engine.utils import read_csv_rows, read_json, write_csv, write_json


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


def _book(price: str = "0.50") -> dict[str, str]:
    return {
        "best_ask": price,
        "top_ask_size": "1000",
        "ask_depth_1pct": "1000",
        "ask_depth_5pct": "1000",
        "websocket_quote_age_seconds": "30",
    }


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


def test_alpha_edge_and_strategy_priority_charge_taker_fee_once(tmp_path):
    cfg = _config(tmp_path)
    _write_training_set(cfg)
    train_mispricing_alpha_model(cfg)
    cfg.raw["costs"]["slippage"] = 0.0
    cfg.raw["mispricing_alpha"]["require_same_category_labels_for_trading"] = False
    cfg.raw["cohort_promotion"]["require_positive_forward_pnl"] = False
    prediction_path = cfg.output_root / "polymarket_predictions" / "predictions.csv"

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "fee-market",
                "market_slug": "tennis-fee-market",
                "question": "Will Player One win?",
                "token_id": "fee-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.5",
                "raw_probability": "0.5",
                "calibrated_probability": "0.5",
                "model_probability": "0.5",
                "executable_price": "0.5",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "1",
                **_book("0.50"),
            }
        ],
        output_path=str(prediction_path),
    )

    assert scored[0]["taker_fee_rate"] == 0.05
    assert scored[0]["taker_fee_per_share"] == pytest.approx(0.0125)
    assert float(scored[0]["edge_lower_bound_before_taker_fee"]) - float(scored[0]["edge_lower_bound"]) == pytest.approx(
        0.0125
    )
    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )
    assert rejected == []
    assert len(approved) == 1
    assert float(approved[0]["edge"]) == pytest.approx(float(scored[0]["edge_lower_bound"]))
    assert float(approved[0]["taker_fee_per_share"]) == pytest.approx(0.0125)


def test_alpha_explicit_fee_free_market_does_not_invent_taker_cost(tmp_path):
    cfg = _config(tmp_path)
    _write_training_set(cfg)
    train_mispricing_alpha_model(cfg)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "fee-free-market",
                "token_id": "fee-free-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "crypto",
                "fees_enabled": False,
                "market_midpoint": "0.5",
                "raw_probability": "0.5",
                "calibrated_probability": "0.5",
                "model_probability": "0.5",
                "executable_price": "0.5",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "1",
            }
        ],
    )

    assert scored[0]["taker_fee_rate"] == 0
    assert scored[0]["taker_fee_source"] == "explicit_fees_disabled"
    assert scored[0]["edge_lower_bound"] == pytest.approx(scored[0]["edge_lower_bound_before_taker_fee"])


def test_alpha_scoring_penalises_shallow_execution_depth(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"]["model_residual_shrinkage"] = 1.0
    cfg.raw["mispricing_alpha"]["max_model_residual_adjustment"] = 0.2
    cfg.raw["mispricing_alpha"]["execution_cost_probe_stake_usdc"] = 10.0

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "shallow-depth-market",
                "market_slug": "shallow-depth-market",
                "token_id": "shallow-depth-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.50",
                "raw_probability": "0.70",
                "calibrated_probability": "0.70",
                "model_probability": "0.70",
                "executable_price": "0.50",
                "best_ask": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "top_ask_size": "1",
                "ask_depth_1pct": "2",
                "ask_depth_5pct": "3",
                "time_to_close_hours": "24",
                "confidence": "1",
            }
        ],
    )

    assert float(scored[0]["execution_cost_penalty"]) > 0
    assert scored[0]["execution_cost_status"] == "insufficient_depth"
    assert float(scored[0]["edge_lower_bound"]) < float(scored[0]["alpha_raw_edge"])


def test_alpha_scoring_enriches_stale_predictions_with_latest_websocket_depth(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"]["model_residual_shrinkage"] = 1.0
    cfg.raw["mispricing_alpha"]["max_model_residual_adjustment"] = 0.2
    cfg.raw["mispricing_alpha"]["execution_cost_probe_stake_usdc"] = 10.0
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "market_id": "depth-market",
                "market_slug": "depth-market",
                "asset_id": "depth-token",
                "token_id": "depth-token",
                "collected_at_utc": "2026-02-01T00:00:30Z",
                "best_bid": "0.47",
                "best_ask": "0.49",
                "midpoint": "0.48",
                "spread": "0.02",
                "liquidity": "2500",
                "top_bid_size": "80",
                "top_ask_size": "40",
                "bid_depth_1pct": "120",
                "ask_depth_1pct": "80",
                "bid_depth_5pct": "200",
                "ask_depth_5pct": "160",
                "book_imbalance": "0.62",
            }
        ],
    )

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "depth-market",
                "market_slug": "depth-market",
                "token_id": "depth-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.50",
                "raw_probability": "0.70",
                "calibrated_probability": "0.70",
                "model_probability": "0.70",
                "executable_price": "0.50",
                "time_to_close_hours": "24",
                "confidence": "1",
            }
        ],
    )

    assert scored[0]["websocket_quote_enrichment_status"] == "enriched"
    assert float(scored[0]["executable_price"]) == 0.49
    assert float(scored[0]["market_midpoint"]) == 0.48
    assert scored[0]["execution_cost_status"] == "top_of_book_fill"
    assert float(scored[0]["execution_depth_stake_cap_usdc"]) > 10.0


def test_alpha_scoring_refuses_stale_websocket_quote_enrichment(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"]["model_residual_shrinkage"] = 1.0
    cfg.raw["mispricing_alpha"]["max_websocket_quote_enrichment_age_seconds"] = 60
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "asset_id": "stale-depth-token",
                "token_id": "stale-depth-token",
                "collected_at_utc": "2026-02-01T00:00:00Z",
                "best_bid": "0.20",
                "best_ask": "0.21",
                "midpoint": "0.205",
                "top_ask_size": "100",
                "ask_depth_1pct": "100",
                "ask_depth_5pct": "100",
            }
        ],
    )

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "stale-depth-market",
                "market_slug": "stale-depth-market",
                "token_id": "stale-depth-token",
                "prediction_timestamp": "2026-02-01T00:10:00Z",
                "category": "synthetic",
                "market_midpoint": "0.50",
                "raw_probability": "0.70",
                "calibrated_probability": "0.70",
                "model_probability": "0.70",
                "executable_price": "0.50",
                "time_to_close_hours": "24",
                "confidence": "1",
            }
        ],
    )

    assert scored[0]["websocket_quote_enrichment_status"] == "stale_quote_outside_age_window"
    assert scored[0]["executable_price"] == "0.50"
    assert scored[0]["market_midpoint"] == "0.50"
    assert scored[0]["execution_cost_status"] == "missing_depth"


def test_near_miss_learning_lane_records_liquid_uncertain_edge(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"]["model_residual_shrinkage"] = 1.0
    cfg.raw["mispricing_alpha"]["max_model_residual_adjustment"] = 0.1
    cfg.raw["mispricing_alpha"]["uncertainty_penalty_weight"] = 0.05

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "near-miss-market",
                "market_slug": "near-miss-market",
                "token_id": "near-miss-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "crypto",
                "market_midpoint": "0.50",
                "raw_probability": "0.58",
                "calibrated_probability": "0.58",
                "model_probability": "0.58",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "0",
                **_book("0.50"),
            }
        ],
    )

    assert scored[0]["alpha_trade_candidate"] is False
    assert scored[0]["near_miss_learning_candidate"] is True
    assert scored[0]["near_miss_learning_reason"] == "near_miss_eligible"
    assert 0 < float(scored[0]["edge_lower_bound"]) < 0.03
    saved = read_csv_rows(cfg.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv")
    assert saved[0]["market_slug"] == "near-miss-market"


def test_fast_crypto_updown_must_have_scored_live_model_before_trading(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "model_residual_shrinkage": 1.0,
            "max_model_residual_adjustment": 0.4,
            "market_overround_penalty_weight": 0.0,
        }
    )
    cfg.raw["crypto_updown_live_model"]["require_scored_model_for_trading"] = True

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "btc-fast-future",
                "market_slug": "btc-updown-5m-4102444800",
                "token_id": "btc-up-token",
                "question": "Bitcoin Up or Down - future window",
                "outcome": "Up",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "crypto",
                "market_midpoint": "0.50",
                "raw_probability": "0.80",
                "calibrated_probability": "0.80",
                "model_probability": "0.80",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "0.2",
                "confidence": "1",
            }
        ],
    )

    assert float(scored[0]["alpha_raw_edge"]) > 0.03
    assert scored[0]["crypto_model_status"] == "before_model_window"
    assert scored[0]["crypto_model_gate_pass"] is False
    assert "crypto_updown_model_not_scored_for_trading" in scored[0]["validation_layer_reason"]
    assert scored[0]["validation_layer_pass"] is False
    assert scored[0]["alpha_trade_candidate"] is False


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
                **_book("0.40"),
            }
        ],
    )

    assert float(scored[0]["fundamental_probability"]) == 0.70
    assert float(scored[0]["fundamental_adjustment"]) == 0.04
    assert float(scored[0]["alpha_probability"]) == 0.44
    assert float(scored[0]["edge_lower_bound_before_taker_fee"]) > 0.03
    assert float(scored[0]["edge_lower_bound"]) == pytest.approx(0.028)
    assert scored[0]["alpha_trade_candidate"] is False


def test_sharp_anchor_non_worldcup_market_can_feed_shadow_evidence(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "fundamental_residual_shrinkage": 0.35,
            "max_fundamental_adjustment": 0.12,
            "market_overround_penalty_weight": 0.0,
        }
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "sharp_fundamental_probabilities.csv",
        [{"token_id": "sports-token", "probability": "0.58"}],
    )

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "sports-match-market",
                "market_slug": "will-boston-beat-new-york-on-july-3",
                "question": "Will Boston beat New York on July 3?",
                "category": "sports",
                "outcome": "Yes",
                "token_id": "sports-token",
                "prediction_timestamp": "2026-07-03T11:00:00Z",
                "market_midpoint": "0.40",
                "calibrated_probability": "0.40",
                "executable_price": "0.40",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "12",
                "confidence": "1",
                **_book("0.40"),
            }
        ],
    )

    assert scored[0]["worldcup_winner_validation_market"] is False
    assert "sharp_fundamental_probabilities.csv" in scored[0]["fundamental_source"].replace("\\", "/")
    assert scored[0]["non_worldcup_shadow_fundamental_source_allowed"] is True
    assert float(scored[0]["fundamental_edge_after_haircut"]) > 0.008
    assert scored[0]["shadow_trade_candidate"] is True
    assert scored[0]["shadow_candidate_reason"] == "shadow_eligible"


def test_static_non_worldcup_fundamental_probability_cannot_feed_shadow_evidence(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "fundamental_residual_shrinkage": 0.35,
            "max_fundamental_adjustment": 0.12,
            "market_overround_penalty_weight": 0.0,
        }
    )
    write_csv(
        tmp_path / "inputs" / "polymarket" / "model_probabilities.csv",
        [{"token_id": "static-model-token", "probability": "0.70"}],
    )

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "static-sports-market",
                "market_slug": "will-los-angeles-beat-miami-on-july-3",
                "question": "Will Los Angeles beat Miami on July 3?",
                "category": "sports",
                "outcome": "Yes",
                "token_id": "static-model-token",
                "prediction_timestamp": "2026-07-03T11:00:00Z",
                "market_midpoint": "0.40",
                "calibrated_probability": "0.40",
                "executable_price": "0.40",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "12",
                "confidence": "1",
                **_book("0.40"),
            }
        ],
    )

    assert scored[0]["worldcup_winner_validation_market"] is False
    assert "model_probabilities.csv" in scored[0]["fundamental_source"].replace("\\", "/")
    assert scored[0]["non_worldcup_shadow_fundamental_source_allowed"] is False
    assert scored[0]["shadow_trade_candidate"] is False
    assert "non_worldcup_fundamental_source_not_allowed_for_shadow" in scored[0]["shadow_candidate_reason"]


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


def test_alpha_uses_crypto_updown_contract_model_overlay(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "use_fundamental_probabilities": False,
            "market_overround_penalty_weight": 0.0,
        }
    )
    cfg.raw["crypto_updown_live_model"].update(
        {
            "enabled": True,
            "probability_blend_weight": 0.5,
            "max_probability_adjustment": 0.3,
        }
    )

    def fake_crypto_model(row, settings):
        assert settings["enabled"] is True
        assert row["market_slug"] == "bitcoin-up-or-down-on-june-26-2026"
        return {
            "crypto_model_status": "scored",
            "crypto_model_probability": 0.80,
            "crypto_model_p_up": 0.80,
            "crypto_model_edge_after_cost": 0.39,
        }

    monkeypatch.setattr(mispricing_alpha_module, "score_crypto_updown_prediction", fake_crypto_model)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "btc-daily",
                "market_slug": "bitcoin-up-or-down-on-june-26-2026",
                "question": "Bitcoin Up or Down on June 26?",
                "outcome": "Up",
                "token_id": "btc-up-token",
                "prediction_timestamp": "2026-06-26T09:00:00Z",
                "close_time": "2026-06-26T16:00:00Z",
                "category": "crypto",
                "market_midpoint": "0.40",
                "calibrated_probability": "0.40",
                "executable_price": "0.40",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "7",
                "confidence": "1",
                **_book("0.40"),
            }
        ],
    )

    assert scored[0]["crypto_model_status"] == "scored"
    assert float(scored[0]["crypto_model_adjustment"]) == 0.2
    assert round(float(scored[0]["alpha_probability"]), 6) == 0.6
    assert float(scored[0]["edge_lower_bound"]) > 0.18
    assert scored[0]["alpha_trade_candidate"] is True
    assert scored[0]["shadow_trade_candidate"] is True
    assert scored[0]["shadow_candidate_reason"] == "shadow_eligible"


def test_alpha_uses_fast_crypto_updown_contract_model_overlay(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "use_fundamental_probabilities": False,
            "market_overround_penalty_weight": 0.0,
        }
    )
    cfg.raw["crypto_updown_live_model"].update(
        {
            "enabled": True,
            "probability_blend_weight": 0.5,
            "max_probability_adjustment": 0.3,
        }
    )
    calls = 0

    def fake_crypto_model(row, settings):
        nonlocal calls
        calls += 1
        assert settings["enabled"] is True
        assert row["market_slug"] == "btc-updown-5m-1782468000"
        return {
            "crypto_model_status": "scored",
            "crypto_model_contract_kind": "fast",
            "crypto_model_interval": "5m",
            "crypto_model_probability": 0.70,
            "crypto_model_p_up": 0.70,
            "crypto_model_edge_after_cost": 0.24,
        }

    monkeypatch.setattr(mispricing_alpha_module, "score_crypto_updown_prediction", fake_crypto_model)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "btc-5m",
                "market_slug": "btc-updown-5m-1782468000",
                "question": "Bitcoin UpDown 5M",
                "outcome": "Up",
                "token_id": "btc-5m-up-token",
                "prediction_timestamp": "2026-06-26T09:00:00Z",
                "category": "crypto",
                "market_midpoint": "0.45",
                "calibrated_probability": "0.45",
                "executable_price": "0.45",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "0.05",
                "confidence": "1",
            }
        ],
    )

    assert calls == 1
    assert scored[0]["crypto_model_status"] == "scored"
    assert scored[0]["crypto_model_contract_kind"] == "fast"
    assert scored[0]["crypto_model_interval"] == "5m"
    assert round(float(scored[0]["crypto_model_adjustment"]), 6) == 0.125
    assert round(float(scored[0]["alpha_probability"]), 6) == 0.575


def test_crypto_shadow_evidence_uses_crypto_edge_not_generic_alpha_lower_bound(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "use_fundamental_probabilities": False,
            "uncertainty_penalty_weight": 0.2,
            "market_overround_penalty_weight": 0.0,
        }
    )
    cfg.raw["crypto_updown_live_model"].update(
        {
            "enabled": True,
            "probability_blend_weight": 0.5,
            "minimum_shadow_edge_after_cost": 0.015,
        }
    )

    def fake_crypto_model(row, settings):
        return {
            "crypto_model_status": "scored",
            "crypto_model_probability": 0.60,
            "crypto_model_p_up": 0.60,
            "crypto_model_edge_after_cost": 0.04,
        }

    monkeypatch.setattr(mispricing_alpha_module, "score_crypto_updown_prediction", fake_crypto_model)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "btc-daily",
                "market_slug": "bitcoin-up-or-down-on-june-26-2026",
                "question": "Bitcoin Up or Down on June 26?",
                "outcome": "Up",
                "token_id": "btc-up-token",
                "prediction_timestamp": "2026-06-26T09:00:00Z",
                "close_time": "2026-06-26T16:00:00Z",
                "category": "crypto",
                "market_midpoint": "0.55",
                "calibrated_probability": "0.55",
                "executable_price": "0.55",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "7",
                "confidence": "0",
            }
        ],
    )

    assert scored[0]["crypto_model_status"] == "scored"
    assert float(scored[0]["edge_lower_bound"]) < 0
    assert scored[0]["alpha_trade_candidate"] is False
    assert scored[0]["shadow_trade_candidate"] is True
    assert scored[0]["shadow_candidate_reason"] == "shadow_eligible"


def test_fast_crypto_shadow_uses_fast_learning_liquidity_threshold(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "use_fundamental_probabilities": False,
            "uncertainty_penalty_weight": 0.2,
            "market_overround_penalty_weight": 0.0,
        }
    )
    cfg.raw["shadow_cohort_validation"].update(
        {
            "minimum_liquidity": 100,
            "fast_minimum_liquidity": 5,
            "maximum_spread": 0.02,
            "fast_maximum_spread": 0.06,
        }
    )
    cfg.raw["crypto_updown_live_model"].update(
        {
            "enabled": True,
            "probability_blend_weight": 0.5,
            "minimum_shadow_edge_after_cost": 0.015,
        }
    )

    def fake_crypto_model(row, settings):
        return {
            "crypto_model_status": "scored",
            "crypto_model_contract_kind": "fast",
            "crypto_model_interval": "5m",
            "crypto_model_probability": 0.60,
            "crypto_model_p_up": 0.60,
            "crypto_model_edge_after_cost": 0.04,
        }

    monkeypatch.setattr(mispricing_alpha_module, "score_crypto_updown_prediction", fake_crypto_model)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "xrp-fast",
                "market_slug": "xrp-updown-5m-1782489000",
                "question": "XRP Up or Down - 5m",
                "outcome": "Down",
                "token_id": "xrp-down-token",
                "prediction_timestamp": "2026-06-26T09:00:00Z",
                "close_time": "2026-06-26T09:05:00Z",
                "category": "crypto",
                "market_midpoint": "0.495",
                "calibrated_probability": "0.55",
                "executable_price": "0.495",
                "spread": "0.01",
                "liquidity": "5",
                "time_to_close_hours": "0.05",
                "confidence": "0",
            }
        ],
    )

    assert scored[0]["crypto_model_contract_kind"] == "fast"
    assert scored[0]["shadow_trade_candidate"] is True
    assert scored[0]["shadow_candidate_reason"] == "shadow_eligible"

def test_quarantined_crypto_live_model_cohort_blocks_alpha_and_shadow_candidate(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "use_fundamental_probabilities": False,
            "uncertainty_penalty_weight": 0.0,
            "market_overround_penalty_weight": 0.0,
        }
    )
    cfg.raw["crypto_updown_live_model"].update(
        {
            "enabled": True,
            "probability_blend_weight": 1.0,
            "minimum_shadow_edge_after_cost": 0.015,
        }
    )
    quarantined_cohort = "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=up"
    write_json(
        cfg.governance_root / "shadow_signal_cohort_pnl.json",
        {
            "quarantined_cohorts": [
                {
                    "signal_cohort": quarantined_cohort,
                    "closed_positions": 3,
                    "closed_realised_pnl_usdc": -7.0,
                    "closed_roi": -0.23,
                    "quarantine_reason": "closed evidence below threshold",
                }
            ]
        },
    )

    def fake_crypto_model(row, settings):
        return {
            "crypto_model_status": "scored",
            "crypto_model_contract_kind": "fast",
            "crypto_model_interval": "5m",
            "crypto_model_probability": 0.70,
            "crypto_model_p_up": 0.70,
            "crypto_model_edge_after_cost": 0.18,
        }

    monkeypatch.setattr(mispricing_alpha_module, "score_crypto_updown_prediction", fake_crypto_model)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "btc-fast",
                "market_slug": "btc-updown-5m-1782489000",
                "question": "BTC Up or Down - 5m",
                "outcome": "Up",
                "token_id": "btc-up-token",
                "prediction_timestamp": "2026-06-26T09:00:00Z",
                "close_time": "2026-06-26T09:05:00Z",
                "category": "crypto",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.50",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "0.05",
                "confidence": "1",
            }
        ],
    )

    assert scored[0]["signal_cohort"] == quarantined_cohort
    assert scored[0]["cohort_evidence_filter_pass"] is False
    assert scored[0]["cohort_evidence_status"] == "cohort_quarantined"
    assert scored[0]["validation_layer_pass"] is False
    assert scored[0]["alpha_trade_candidate"] is False
    assert scored[0]["shadow_trade_candidate"] is False
    assert "cohort_quarantined" in scored[0]["shadow_candidate_reason"]

def test_alpha_crypto_overlay_has_fail_fast_row_budget(tmp_path, monkeypatch):
    cfg = _config(tmp_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "bias_alpha_shrinkage": 0.0,
            "model_residual_shrinkage": 0.0,
            "use_fundamental_probabilities": False,
        }
    )
    cfg.raw["crypto_updown_live_model"].update(
        {
            "enabled": True,
            "max_scored_rows_per_cycle": 1,
        }
    )
    calls = 0

    def fake_crypto_model(row, settings):
        nonlocal calls
        calls += 1
        return {
            "crypto_model_status": "scored",
            "crypto_model_probability": 0.55,
        }

    monkeypatch.setattr(mispricing_alpha_module, "score_crypto_updown_prediction", fake_crypto_model)
    base = {
        "market_id": "btc-daily",
        "market_slug": "bitcoin-up-or-down-on-june-26-2026",
        "question": "Bitcoin Up or Down on June 26?",
        "token_id": "btc-token",
        "prediction_timestamp": "2026-06-26T09:00:00Z",
        "close_time": "2026-06-26T16:00:00Z",
        "category": "crypto",
        "market_midpoint": "0.50",
        "calibrated_probability": "0.50",
        "executable_price": "0.50",
        "time_to_close_hours": "7",
        "confidence": "1",
    }

    scored = apply_mispricing_alpha(
        cfg,
        [{**base, "outcome": "Up", "token_id": "up"}, {**base, "outcome": "Down", "token_id": "down"}],
    )

    assert calls == 1
    assert scored[0]["crypto_model_status"] == "scored"
    assert scored[1]["crypto_model_status"] == "row_budget_exhausted"


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


def test_strategy_same_category_gate_reclassifies_legacy_unknown_crypto_labels(tmp_path):
    cfg = _config(tmp_path)
    train_root = cfg.output_root / "polymarket_training"
    features = []
    labels = []
    for idx in range(25):
        ts = f"2026-06-26T10:{idx:02d}:00Z"
        features.append(
            {
                "market_id": f"m-{idx}",
                "market_slug": f"btc-updown-5m-1782487{idx:03d}",
                "question": "Bitcoin Up or Down - June 26, 5 minute window",
                "token_id": f"t-{idx}",
                "prediction_timestamp": ts,
                "category": "unknown",
                "implied_probability": "0.51",
            }
        )
        labels.append(
            {
                "market_id": f"m-{idx}",
                "token_id": f"t-{idx}",
                "prediction_timestamp": ts,
                "target": "1",
                "horizon": "all_valid",
            }
        )
    write_csv(train_root / "features_v2.csv", features)
    write_csv(train_root / "labels.csv", labels)

    counts = _same_category_label_counts(cfg)

    assert counts["crypto_btc_updown_5m"] == 25
    assert counts["crypto"] == 25


def test_strategy_same_category_gate_uses_reclassified_broad_category_before_cohort_gate(tmp_path):
    cfg = _config(tmp_path)
    train_root = cfg.output_root / "polymarket_training"
    features = []
    labels = []
    for idx in range(25):
        ts = f"2026-06-26T11:{idx:02d}:00Z"
        features.append(
            {
                "market_id": f"label-m-{idx}",
                "market_slug": f"btc-updown-5m-1782488{idx:03d}",
                "question": "Bitcoin Up or Down - June 26, 5 minute window",
                "token_id": f"label-t-{idx}",
                "prediction_timestamp": ts,
                "category": "unknown",
                "implied_probability": "0.51",
            }
        )
        labels.append(
            {
                "market_id": f"label-m-{idx}",
                "token_id": f"label-t-{idx}",
                "prediction_timestamp": ts,
                "target": "1",
                "horizon": "all_valid",
            }
        )
    write_csv(train_root / "features_v2.csv", features)
    write_csv(train_root / "labels.csv", labels)
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "crypto-alpha",
                "market_slug": "will-bitcoin-reach-80000-by-december-31-2026",
                "question": "Will Bitcoin reach $80,000 by December 31, 2026?",
                "token_id": "crypto-alpha-token",
                "prediction_timestamp": "2026-06-26T15:00:00Z",
                "category": "crypto",
                "signal_cohort": "crypto",
                "outcome": "Yes",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.65",
                "model_probability": "0.65",
                "alpha_probability": "0.65",
                "executable_price": "0.50",
                "edge": "0.08",
                "edge_lower_bound": "0.08",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "microstructure_filter_pass": "true",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "1",
                "confidence": "1",
                **_book("0.50"),
            }
        ],
    )

    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["same_category_label_rows"] == 25
    assert "cohort promotion gate failed" in rejected[0]["rejection_reason"]
    assert "same-category validation gate failed" not in rejected[0]["rejection_reason"]


def test_strategy_allows_probationary_cohort_to_satisfy_same_category_label_gate(tmp_path):
    cfg = _config(tmp_path)
    cohort = "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up"
    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    (cfg.governance_root / "signal_cohort_pnl.json").write_text(
        """
        {
          "status": "computed",
          "cohorts": [
            {
              "signal_cohort": "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up",
              "promoted": false,
              "probationary": true,
              "probationary_max_stake_usdc": 2.0,
              "buy_fills": 3,
              "settled_fills": 3,
              "total_pnl_usdc": 4.5,
              "roi": 0.15
            }
          ]
        }
        """,
        encoding="utf-8",
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "btc-probationary",
                "market_slug": "btc-updown-5m-1782487800",
                "question": "Bitcoin UpDown 5M",
                "token_id": "btc-up-token",
                "prediction_timestamp": "2026-06-26T15:00:00Z",
                "category": "crypto",
                "signal_cohort": cohort,
                "outcome": "Up",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.65",
                "model_probability": "0.65",
                "alpha_probability": "0.65",
                "executable_price": "0.50",
                "edge": "0.08",
                "edge_lower_bound": "0.08",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "microstructure_filter_pass": "true",
                    "spread": "0.01",
                    "liquidity": "1000",
                    "time_to_close_hours": "1",
                    "confidence": "1",
                    **_book("0.50"),
                },
                {
                "market_id": "btc-not-promoted",
                "market_slug": "btc-updown-5m-1782487800",
                "question": "Bitcoin UpDown 5M",
                "token_id": "btc-down-token",
                "prediction_timestamp": "2026-06-26T15:00:00Z",
                "category": "crypto",
                "signal_cohort": "exploratory_historical_rule|crypto_btc_updown_5m|outcome=down",
                "outcome": "Down",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.65",
                "model_probability": "0.65",
                "alpha_probability": "0.65",
                "executable_price": "0.50",
                "edge": "0.08",
                "edge_lower_bound": "0.08",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "microstructure_filter_pass": "true",
                    "spread": "0.01",
                    "liquidity": "1000",
                    "time_to_close_hours": "1",
                    "confidence": "1",
                    **_book("0.50"),
                },
        ],
    )

    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )

    assert len(approved) == 1
    assert approved[0]["signal_cohort"] == cohort
    assert approved[0]["cohort_promotion_status"] == "probationary"
    assert approved[0]["cohort_evidence_label_gate_override"] is True
    assert float(approved[0]["sizing_decision"]) <= 2.0
    assert len(rejected) == 1
    assert "same-category validation gate failed" in rejected[0]["rejection_reason"]


def test_strategy_proxies_crypto_live_model_to_same_updown_probationary_evidence(tmp_path):
    cfg = _config(tmp_path)
    source_cohort = "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up"
    live_cohort = "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=up"
    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    (cfg.governance_root / "signal_cohort_pnl.json").write_text(
        f"""
        {{
          "status": "computed",
          "cohorts": [
            {{
              "signal_cohort": "{source_cohort}",
              "promoted": false,
              "probationary": true,
              "probationary_max_stake_usdc": 2.0,
              "promotion_ready_score": 5,
              "buy_fills": 3,
              "settled_fills": 3,
              "total_pnl_usdc": 4.5,
              "roi": 0.15
            }}
          ]
        }}
        """,
        encoding="utf-8",
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "btc-live-model",
                "market_slug": "btc-updown-5m-1782487800",
                "question": "Bitcoin UpDown 5M",
                "token_id": "btc-up-token",
                "prediction_timestamp": "2026-06-26T15:00:00Z",
                "category": "crypto",
                "signal_cohort": live_cohort,
                "outcome": "Up",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.65",
                "model_probability": "0.65",
                "alpha_probability": "0.65",
                "executable_price": "0.50",
                "edge": "0.08",
                "edge_lower_bound": "0.08",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "microstructure_filter_pass": "true",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "1",
                "confidence": "1",
                **_book("0.50"),
            }
        ],
    )

    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )

    assert rejected == []
    assert len(approved) == 1
    assert approved[0]["signal_cohort"] == live_cohort
    assert approved[0]["cohort_evidence_source_cohort"] == source_cohort
    assert approved[0]["cohort_promotion_status"] == "probationary"
    assert approved[0]["cohort_evidence_label_gate_override"] is True


def test_strategy_proxies_promoted_near_miss_learning_to_base_cohort(tmp_path):
    cfg = _config(tmp_path)
    cfg.raw["risk"]["fast_market_overrides"] = {
        "enabled": True,
        "minimum_edge": 0.005,
        "minimum_confidence": 0.05,
        "maximum_spread": 0.06,
        "minimum_liquidity": 5,
        "minimum_time_to_close_minutes": 0.25,
        "maximum_stake_usdc": 2,
    }
    base_cohort = "crypto"
    near_miss_cohort = "near_miss_learning|crypto"
    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    (cfg.governance_root / "signal_cohort_pnl.json").write_text(
        f"""
        {{
          "status": "computed",
          "cohorts": [
            {{
              "signal_cohort": "{near_miss_cohort}",
              "promoted": false,
              "probationary": true,
              "probationary_max_stake_usdc": 2.0,
              "promotion_ready_score": 5,
              "buy_fills": 3,
              "settled_fills": 3,
              "total_pnl_usdc": 4.5,
              "roi": 0.15
            }}
          ]
        }}
        """,
        encoding="utf-8",
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "near-miss-proxy",
                "market_slug": "btc-updown-5m-1782487800",
                "question": "Bitcoin UpDown 5M",
                "token_id": "near-miss-token",
                "prediction_timestamp": "2026-06-26T15:00:00Z",
                "category": "crypto",
                "signal_cohort": base_cohort,
                "outcome": "Up",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.56",
                "model_probability": "0.56",
                "alpha_probability": "0.56",
                "executable_price": "0.50",
                "edge": "0.009",
                "edge_lower_bound": "0.009",
                "taker_fee_in_edge": "true",
                "taker_fee_rate": "0.07",
                "taker_fee_category": "crypto",
                "taker_fee_source": "documented_category_fallback",
                "alpha_trade_candidate": "false",
                "near_miss_learning_candidate": "true",
                "validation_layer_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "microstructure_filter_pass": "true",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "0.05",
                "time_to_close_minutes": "3",
                "confidence": "0.12",
                **_book("0.50"),
            }
        ],
    )

    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )

    assert rejected == []
    assert len(approved) == 1
    assert approved[0]["signal_cohort"] == base_cohort
    assert approved[0]["cohort_evidence_source_cohort"] == near_miss_cohort
    assert approved[0]["cohort_promotion_status"] == "probationary"
    assert approved[0]["near_miss_evidence_proxy"] is True
    assert approved[0]["promotion_override"] == "probationary_positive_edge_probe"
    assert float(approved[0]["sizing_decision"]) <= 2.0


def test_strategy_does_not_proxy_over_direct_live_model_evidence(tmp_path):
    cfg = _config(tmp_path)
    source_cohort = "exploratory_inverse_historical_rule|crypto_btc_updown_5m|outcome=up"
    live_cohort = "exploratory_crypto_updown_live_model|crypto_btc_updown_5m|outcome=up"
    cfg.governance_root.mkdir(parents=True, exist_ok=True)
    (cfg.governance_root / "signal_cohort_pnl.json").write_text(
        f"""
        {{
          "status": "computed",
          "cohorts": [
            {{
              "signal_cohort": "{source_cohort}",
              "promoted": false,
              "probationary": true,
              "probationary_max_stake_usdc": 2.0,
              "promotion_ready_score": 5,
              "buy_fills": 3,
              "settled_fills": 3,
              "total_pnl_usdc": 4.5,
              "roi": 0.15
            }},
            {{
              "signal_cohort": "{live_cohort}",
              "promoted": false,
              "probationary": false,
              "promotion_ready_score": 1,
              "buy_fills": 3,
              "settled_fills": 3,
              "total_pnl_usdc": -7.0,
              "roi": -0.23
            }}
          ]
        }}
        """,
        encoding="utf-8",
    )
    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [
            {
                "market_id": "btc-live-model-negative-evidence",
                "market_slug": "btc-updown-5m-1782487800",
                "question": "Bitcoin UpDown 5M",
                "token_id": "btc-up-token",
                "prediction_timestamp": "2026-06-26T15:00:00Z",
                "category": "crypto",
                "signal_cohort": live_cohort,
                "outcome": "Up",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.65",
                "model_probability": "0.65",
                "alpha_probability": "0.65",
                "executable_price": "0.50",
                "edge": "0.08",
                "edge_lower_bound": "0.08",
                "alpha_trade_candidate": "true",
                "validation_layer_pass": "true",
                "bookmaker_cross_check_pass": "true",
                "microstructure_filter_pass": "true",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "1",
                "confidence": "1",
            }
        ],
    )

    approved, rejected = generate_signals(
        cfg,
        readiness={"approved_for_paper_trading": True, "blockers": []},
    )

    assert approved == []
    assert len(rejected) == 1
    assert rejected[0]["cohort_promotion_status"] == "not_promoted"
    assert rejected[0]["cohort_evidence_source_cohort"] == live_cohort
    assert "same-category validation gate failed" in rejected[0]["rejection_reason"]


def test_training_path_never_reaches_quote_enrichment(tmp_path, monkeypatch):
    """Leakage invariant (WO-9): quote enrichment is scoring-time only.

    _enrich_with_latest_websocket_quotes merges the LATEST quotes into rows;
    reaching it from training would be lookahead. This test makes the training
    path explode if anyone ever wires enrichment into it.
    """
    import polymarket_predictive_engine.mispricing_alpha as alpha_module
    from polymarket_predictive_engine.config import EngineConfig
    from polymarket_predictive_engine.utils import write_csv

    cfg = EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "cfg.yaml")
    train_root = cfg.output_root / "polymarket_training"
    # A websocket features file is present, so enrichment WOULD have data to
    # merge if the training path ever called it.
    write_csv(
        train_root / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-07-02T10:00:00Z",
                "source_timestamp": "2026-07-02T10:00:00Z",
                "asset_id": "t1",
                "best_bid": 0.5,
                "best_ask": 0.52,
                "midpoint": 0.51,
                "spread": 0.02,
            }
        ],
    )
    write_csv(
        train_root / "features_v2.csv",
        [{"market_id": "m1", "token_id": "t1", "prediction_timestamp": "2026-07-01T10:00:00Z", "market_midpoint": 0.4}],
    )
    write_csv(
        train_root / "labels.csv",
        [{"market_id": "m1", "token_id": "t1", "target": 1}],
    )

    def _explode(*args, **kwargs):
        raise AssertionError("quote enrichment must never be reached from the training path")

    monkeypatch.setattr(alpha_module, "_enrich_with_latest_websocket_quotes", _explode)
    result = alpha_module.train_mispricing_alpha_model(cfg)
    assert result.get("status") in {"insufficient_data", "trained"}

    # Scoring, by contrast, does route through enrichment.
    with pytest.raises(AssertionError, match="never be reached"):
        alpha_module.apply_mispricing_alpha(cfg, predictions=[{"token_id": "t1", "market_midpoint": 0.4}])


# --- WO-142: volatility copy-through wiring (mispricing_alpha.py extension) ---
#
# Shared test-config convention for this WO (registered): zero every
# microstructure penalty weight EXCEPT volatility, and set
# volatility_penalty_weight explicitly per call. `_config` above already
# zeroes spread/liquidity/depth/uncertainty/model-overround weights; this
# helper additionally zeroes execution_cost_penalty_weight (default 1.0) and
# market_overround_penalty_weight (the base helper leaves it at 0.5) so the
# only nonzero microstructure/cross-market term is the volatility penalty
# under test. Fixtures below use distinct market_id/token_id per row so the
# cross-market group size is always 1 and cross_penalty is exactly 0.0.


def _volatility_test_config(base_path: Path, *, volatility_penalty_weight: float):
    base_path.mkdir(parents=True, exist_ok=True)
    cfg = _config(base_path)
    cfg.raw["mispricing_alpha"].update(
        {
            "execution_cost_penalty_weight": 0.0,
            "market_overround_penalty_weight": 0.0,
            "volatility_penalty_weight": volatility_penalty_weight,
        }
    )
    return cfg


def _volatility_monotone_fixture() -> list[dict]:
    return [
        {
            # Control row: comfortably above the trade-edge threshold before
            # AND after the volatility penalty applies (raw edge 0.20, a
            # penalty of 0.15 * 0.02 = 0.003 never closes that gap).
            "market_id": "vol-control-market",
            "token_id": "vol-control-token",
            "prediction_timestamp": "2026-02-01T00:00:00Z",
            "category": "synthetic",
            "market_midpoint": "0.70",
            "calibrated_probability": "0.70",
            "executable_price": "0.50",
            "spread": "0.01",
            "liquidity": "1000",
            "time_to_close_hours": "24",
            "confidence": "1",
            "fees_enabled": False,
            "rolling_volatility_6h": "0.02",
        },
        {
            # Widening row: raw edge 0.06 clears the 0.03 trade threshold at
            # weight 0.0 (edge_lower_bound == 0.06), but a volatility penalty
            # of 0.15 * 0.30 == 0.045 at weight 0.15 drops edge_lower_bound to
            # 0.015 -- inside the near-miss band [0.0, 0.03), not a trade
            # candidate anymore. This is the WO-142 disclosed widening.
            "market_id": "vol-widening-market",
            "token_id": "vol-widening-token",
            "prediction_timestamp": "2026-02-01T00:00:00Z",
            "category": "synthetic",
            "market_midpoint": "0.56",
            "calibrated_probability": "0.56",
            "executable_price": "0.50",
            "spread": "0.01",
            "liquidity": "1000",
            "time_to_close_hours": "24",
            "confidence": "1",
            "fees_enabled": False,
            "rolling_volatility_6h": "0.30",
        },
        {
            # Shadow row: mirrors test_sharp_anchor_non_worldcup_market_can_
            # feed_shadow_evidence's fixture shape. raw edge 0.021 stays
            # below both the trade (0.03) and near-miss-raw-edge (0.05)
            # thresholds at every weight, so this row exercises ONLY the
            # shadow-candidate boundary: edge_lower_bound == 0.021 at weight
            # 0.0 (>= shadow minimum -0.01, a shadow candidate) falls to
            # 0.021 - 0.15 * 0.30 == -0.024 at weight 0.15 (< -0.01, not a
            # shadow candidate).
            "market_id": "vol-shadow-market",
            "market_slug": "will-shadow-team-win",
            "question": "Will Shadow Team win?",
            "category": "sports",
            "outcome": "Yes",
            "token_id": "vol-shadow-token",
            "prediction_timestamp": "2026-02-01T00:00:00Z",
            "market_midpoint": "0.40",
            "calibrated_probability": "0.40",
            "executable_price": "0.40",
            "spread": "0.01",
            "liquidity": "1000",
            "time_to_close_hours": "12",
            "confidence": "1",
            "fees_enabled": False,
            "rolling_volatility_6h": "0.30",
        },
    ]


def _write_shadow_fundamental_probability(cfg) -> None:
    write_csv(
        cfg.output_root / "polymarket_training" / "sharp_fundamental_probabilities.csv",
        [{"token_id": "vol-shadow-token", "probability": "0.46"}],
    )


def test_volatility_summary_counters_third_state_invariant(tmp_path):
    """142.3: rows_with_rolling_volatility / rows_missing_rolling_volatility.

    Three rows: one scored with a volatility source, one scored but blank
    (stamped "missing"), one that exits early before scoring. The early-exit
    row carries no volatility_source stamp at all, so it lands in NEITHER
    counter -- the two counters must sum to <= predictions, not ==.
    """
    cfg = _volatility_test_config(tmp_path, volatility_penalty_weight=0.15)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "vol-tele-a",
                "token_id": "vol-tele-a-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.50",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "1",
                "fees_enabled": False,
                "rolling_volatility_6h": "0.05",
            },
            {
                "market_id": "vol-tele-b",
                "token_id": "vol-tele-b-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.50",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "1",
                "fees_enabled": False,
                "rolling_volatility_6h": "",
                "rolling_volatility_24h": "",
            },
            {
                # No market/model probability field at all -> skipped early,
                # never reaches _microstructure_penalty.
                "market_id": "vol-tele-c",
                "token_id": "vol-tele-c-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
            },
        ],
    )

    assert len(scored) == 3
    row_a = next(row for row in scored if row["market_id"] == "vol-tele-a")
    row_b = next(row for row in scored if row["market_id"] == "vol-tele-b")
    row_c = next(row for row in scored if row["market_id"] == "vol-tele-c")

    assert row_a["volatility_source"] == "rolling_volatility_6h"
    assert row_a["volatility_penalty"] == pytest.approx(0.0075, abs=1e-12)
    assert row_b["volatility_source"] == "missing"
    assert row_b["volatility_penalty"] == pytest.approx(0.0, abs=1e-12)
    assert row_c["alpha_status"] == "skipped_missing_probability_or_price"
    assert "volatility_source" not in row_c

    summary = read_json(cfg.governance_root / "mispricing_alpha_live_summary.json")
    assert summary["predictions"] == 3
    assert summary["rows_with_rolling_volatility"] == 1
    assert summary["rows_missing_rolling_volatility"] == 1
    assert (
        summary["rows_with_rolling_volatility"] + summary["rows_missing_rolling_volatility"]
        <= summary["predictions"]
    )
    assert summary["volatility_penalty_sum"] == pytest.approx(0.0075, abs=1e-12)


def test_volatility_penalty_moves_no_gate_monotone_tightening(tmp_path):
    """142.2/142.3 direction disclosure: no gate, threshold, or config moves.

    Same three-row fixture scored at volatility_penalty_weight 0.0 and 0.15.
    The trade-candidate set and the shadow-candidate set at 0.15 must each be
    a SUBSET of their weight-0.0 counterpart (tighten-only), trade_candidates
    count must be <=, and the untouched config literals must read exactly as
    registered.
    """
    fixture = _volatility_monotone_fixture()

    cfg_zero = _volatility_test_config(tmp_path / "w0", volatility_penalty_weight=0.0)
    _write_shadow_fundamental_probability(cfg_zero)
    scored_zero = apply_mispricing_alpha(cfg_zero, [dict(row) for row in fixture])

    cfg_full = _volatility_test_config(tmp_path / "w15", volatility_penalty_weight=0.15)
    _write_shadow_fundamental_probability(cfg_full)
    scored_full = apply_mispricing_alpha(cfg_full, [dict(row) for row in fixture])

    trade_zero = {row["market_id"] for row in scored_zero if row["alpha_trade_candidate"] is True}
    trade_full = {row["market_id"] for row in scored_full if row["alpha_trade_candidate"] is True}
    shadow_zero = {row["market_id"] for row in scored_zero if row["shadow_trade_candidate"] is True}
    shadow_full = {row["market_id"] for row in scored_full if row["shadow_trade_candidate"] is True}

    assert trade_zero == {"vol-control-market", "vol-widening-market"}
    assert trade_full == {"vol-control-market"}
    assert trade_full <= trade_zero

    assert shadow_zero == {"vol-shadow-market"}
    assert shadow_full == set()
    assert shadow_full <= shadow_zero

    assert len(scored_full) == len(scored_zero) == 3
    assert sum(1 for row in scored_full if row["alpha_trade_candidate"] is True) <= sum(
        1 for row in scored_zero if row["alpha_trade_candidate"] is True
    )

    for cfg in (cfg_zero, cfg_full):
        assert cfg.raw["risk"]["minimum_edge"] == 0.03
        assert cfg.raw["mispricing_alpha"]["edge_field_for_trading"] == "edge_lower_bound"
        assert cfg.raw["mispricing_alpha"]["require_alpha_trade_candidate"] is True


def test_volatility_penalty_near_miss_widening_disclosed(tmp_path):
    """142.3 direction disclosure, single disclosed exception: a row that is
    a trade candidate at weight 0.0 can fall into the near-miss learning band
    at weight 0.15 (never suppressed -- this test must stay red if the band
    is ever suppressed to hide the widening).
    """
    fixture = _volatility_monotone_fixture()

    cfg_zero = _volatility_test_config(tmp_path / "w0", volatility_penalty_weight=0.0)
    scored_zero = apply_mispricing_alpha(cfg_zero, [dict(row) for row in fixture])
    widening_zero = next(row for row in scored_zero if row["market_id"] == "vol-widening-market")
    assert widening_zero["alpha_trade_candidate"] is True
    assert widening_zero["near_miss_learning_candidate"] is False
    assert "already_trade_candidate" in widening_zero["near_miss_learning_reason"]

    cfg_full = _volatility_test_config(tmp_path / "w15", volatility_penalty_weight=0.15)
    scored_full = apply_mispricing_alpha(cfg_full, [dict(row) for row in fixture])
    widening_full = next(row for row in scored_full if row["market_id"] == "vol-widening-market")
    assert widening_full["alpha_trade_candidate"] is False
    assert widening_full["near_miss_learning_candidate"] is True
    assert widening_full["near_miss_learning_reason"] == "near_miss_eligible"
    assert widening_full["edge_lower_bound"] == pytest.approx(0.015, abs=1e-12)

    near_miss_rows = read_csv_rows(
        cfg_full.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv"
    )
    assert any(row.get("market_id") == "vol-widening-market" for row in near_miss_rows)


def test_volatility_columns_are_contiguous_in_header_order(tmp_path):
    """142.1/142.2 position invariant: utils.write_csv derives fieldnames
    from first-seen key insertion order, so the three rolling_volatility_*
    copy-through keys must sit contiguously right after book_imbalance (and
    before time_to_close_hours), and volatility_source must sit immediately
    after volatility_penalty -- in both the in-memory dict and the CSV header
    actually written to disk.
    """
    from polymarket_predictive_engine.models.calibrated import predict_from_features

    features = [
        {
            "market_id": "vol-header-market",
            "token_id": "vol-header-token",
            "prediction_timestamp": "2026-02-01T00:00:00Z",
            "midpoint": "0.50",
            "executable_buy_price": "0.50",
            "category": "synthetic",
            "book_imbalance": "0.10",
            "rolling_volatility_1h": "0.01",
            "rolling_volatility_6h": "0.02",
            "rolling_volatility_24h": "0.03",
        }
    ]
    predictions = predict_from_features(features)
    keys = list(predictions[0].keys())
    book_imbalance_idx = keys.index("book_imbalance")
    assert keys[book_imbalance_idx + 1 : book_imbalance_idx + 4] == [
        "rolling_volatility_1h",
        "rolling_volatility_6h",
        "rolling_volatility_24h",
    ]
    assert keys[book_imbalance_idx + 4] == "time_to_close_hours"

    predictions_csv = tmp_path / "predictions_header.csv"
    write_csv(predictions_csv, predictions)
    with predictions_csv.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    book_imbalance_col = header.index("book_imbalance")
    assert header[book_imbalance_col + 1 : book_imbalance_col + 4] == [
        "rolling_volatility_1h",
        "rolling_volatility_6h",
        "rolling_volatility_24h",
    ]

    cfg = _volatility_test_config(tmp_path / "header-scoring", volatility_penalty_weight=0.15)
    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "vol-header-score-market",
                "token_id": "vol-header-score-token",
                "prediction_timestamp": "2026-02-01T00:00:00Z",
                "category": "synthetic",
                "market_midpoint": "0.50",
                "calibrated_probability": "0.50",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "1",
                "fees_enabled": False,
                "rolling_volatility_6h": "0.04",
            }
        ],
    )
    scored_keys = list(scored[0].keys())
    penalty_idx = scored_keys.index("volatility_penalty")
    assert scored_keys[penalty_idx + 1] == "volatility_source"

    scores_csv = cfg.output_root / "polymarket_predictions" / "mispricing_alpha_scores.csv"
    with scores_csv.open(newline="", encoding="utf-8") as handle:
        scores_header = next(csv.reader(handle))
    penalty_col = scores_header.index("volatility_penalty")
    assert scores_header[penalty_col + 1] == "volatility_source"
