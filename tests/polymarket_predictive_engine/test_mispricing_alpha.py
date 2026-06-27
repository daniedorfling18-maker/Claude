from __future__ import annotations

from pathlib import Path

import yaml

import polymarket_predictive_engine.mispricing_alpha as mispricing_alpha_module
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.mispricing_alpha import (
    apply_mispricing_alpha,
    train_mispricing_alpha_model,
)
from polymarket_predictive_engine.strategy import generate_signals
from polymarket_predictive_engine.utils import read_csv_rows, write_csv, write_json


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
                "raw_probability": "0.56",
                "calibrated_probability": "0.56",
                "model_probability": "0.56",
                "executable_price": "0.50",
                "spread": "0.01",
                "liquidity": "1000",
                "time_to_close_hours": "24",
                "confidence": "0",
            }
        ],
    )

    assert scored[0]["alpha_trade_candidate"] is False
    assert scored[0]["near_miss_learning_candidate"] is True
    assert scored[0]["near_miss_learning_reason"] == "near_miss_eligible"
    assert 0 < float(scored[0]["edge_lower_bound"]) < 0.03
    saved = read_csv_rows(cfg.output_root / "polymarket_predictions" / "near_miss_learning_candidates.csv")
    assert saved[0]["market_slug"] == "near-miss-market"


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
            }
        ],
    )

    assert scored[0]["crypto_model_status"] == "scored"
    assert float(scored[0]["crypto_model_adjustment"]) == 0.2
    assert round(float(scored[0]["alpha_probability"]), 6) == 0.6
    assert float(scored[0]["edge_lower_bound"]) > 0.19
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
