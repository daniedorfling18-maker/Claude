"""WO-142: wire the volatility columns into prediction rows so the declared
volatility penalty actually applies.

Registered scope (see docs/POLYMARKET_CODEX_WORK_ORDERS.md, "## WO-142"):
142.1 copies rolling_volatility_1h/6h/24h from feature rows into prediction
rows verbatim (models/calibrated.py::predict_from_features). 142.2 stamps
provenance (volatility_source) on the existing 6h-then-24h read in
mispricing_alpha.py::_microstructure_penalty without changing the penalty
arithmetic. 142.3 adds three summary counters. Tests here use
pytest.approx(expected, abs=1e-12) for every numeric assertion, distinct
market_id/token_id per fixture row so the cross-market penalty is exactly
0.0, and a test config that zeroes every microstructure/cross-market penalty
weight except volatility_penalty_weight (0.15), per the WO's registered test
conventions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import polymarket_predictive_engine.mispricing_alpha as mispricing_alpha_module
from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.features_v2 import build_features_v2
from polymarket_predictive_engine.mispricing_alpha import apply_mispricing_alpha
from polymarket_predictive_engine.models.calibrated import predict_from_features
from polymarket_predictive_engine.utils import write_csv


def _config(tmp_path: Path, *, volatility_penalty_weight: float = 0.15):
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
    # Registered test convention: zero every OTHER microstructure/cross-market
    # penalty weight so volatility_penalty is the only nonzero contributor.
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
            "execution_cost_penalty_weight": 0.0,
            "uncertainty_penalty_weight": 0.0,
            "market_overround_penalty_weight": 0.0,
            "model_overround_penalty_weight": 0.0,
            "volatility_penalty_weight": volatility_penalty_weight,
        }
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _penalty_only_settings(volatility_penalty_weight: float = 0.15) -> dict:
    return {
        "spread_penalty_weight": 0.0,
        "low_liquidity_penalty": 0.0,
        "missing_liquidity_penalty": 0.0,
        "depth_imbalance_penalty_weight": 0.0,
        "execution_cost_penalty_weight": 0.0,
        "execution_cost_probe_stake_usdc": 1.0,
        "volatility_penalty_weight": volatility_penalty_weight,
    }


# --- (1) copy-through -------------------------------------------------------


def test_prediction_dict_carries_rolling_volatility_columns_verbatim():
    """142.1: MUST fail against unfixed source (KeyError -- the keys do not
    exist in predict_from_features's output today).
    """
    features = [
        {
            "market_id": "vol-copy-market",
            "token_id": "vol-copy-token",
            "prediction_timestamp": "2026-02-01T00:00:00Z",
            "midpoint": "0.50",
            "executable_buy_price": "0.50",
            "category": "synthetic",
            "rolling_volatility_1h": "0.04",
            "rolling_volatility_6h": "0.09",
            "rolling_volatility_24h": "",
        }
    ]
    predictions = predict_from_features(features)
    assert len(predictions) == 1
    prediction = predictions[0]
    # Verbatim: no safe_float, no rounding, no default beyond "".
    assert prediction["rolling_volatility_1h"] == "0.04"
    assert prediction["rolling_volatility_6h"] == "0.09"
    assert prediction["rolling_volatility_24h"] == ""


# --- (2)/(3)/(4) unit coverage of the provenance-stamping read -------------


def test_microstructure_penalty_unit_6h_branch():
    settings = _penalty_only_settings()
    penalty, parts = mispricing_alpha_module._microstructure_penalty(
        {"rolling_volatility_6h": "0.04"}, settings
    )
    assert penalty == pytest.approx(0.006, abs=1e-12)
    assert parts["volatility_penalty"] == pytest.approx(0.006, abs=1e-12)
    assert parts["volatility_source"] == "rolling_volatility_6h"


def test_microstructure_penalty_unit_24h_fallback():
    settings = _penalty_only_settings()
    penalty, parts = mispricing_alpha_module._microstructure_penalty(
        {"rolling_volatility_6h": "", "rolling_volatility_24h": "0.10"}, settings
    )
    assert penalty == pytest.approx(0.015, abs=1e-12)
    assert parts["volatility_penalty"] == pytest.approx(0.015, abs=1e-12)
    assert parts["volatility_source"] == "rolling_volatility_24h"


def test_microstructure_penalty_blank_absent_malformed_and_negative_volatility():
    settings = _penalty_only_settings()

    for row in (
        {},
        {"rolling_volatility_6h": "", "rolling_volatility_24h": ""},
        {"rolling_volatility_6h": "n/a", "rolling_volatility_24h": "-"},
    ):
        penalty, parts = mispricing_alpha_module._microstructure_penalty(row, settings)
        assert penalty == pytest.approx(0.0, abs=1e-12)
        assert parts["volatility_penalty"] == pytest.approx(0.0, abs=1e-12)
        assert parts["volatility_source"] == "missing"

    # A negative but parseable 6h value clamps the *penalty* to 0.0 via the
    # existing max(0.0, ...) guard, but the provenance stamp still records
    # what was read -- the stamp is about which field fed the read, not
    # about whether that read ended up contributing a positive penalty.
    negative_penalty, negative_parts = mispricing_alpha_module._microstructure_penalty(
        {"rolling_volatility_6h": "-0.05"}, settings
    )
    assert negative_penalty == pytest.approx(0.0, abs=1e-12)
    assert negative_parts["volatility_penalty"] == pytest.approx(0.0, abs=1e-12)
    assert negative_parts["volatility_source"] == "rolling_volatility_6h"


# --- (5) headline: apply_mispricing_alpha end-to-end on plain rows ---------


def test_headline_edge_lower_bound_shrinks_exactly_by_volatility_penalty(tmp_path):
    cfg = _config(tmp_path)
    blank_row = {
        "market_id": "vol-headline-blank-market",
        "token_id": "vol-headline-blank-token",
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
    }
    present_row = {**blank_row, "market_id": "vol-headline-present-market", "token_id": "vol-headline-present-token", "rolling_volatility_6h": "0.04"}

    scored = apply_mispricing_alpha(cfg, [blank_row, present_row])
    scored_blank = next(row for row in scored if row["market_id"] == "vol-headline-blank-market")
    scored_present = next(row for row in scored if row["market_id"] == "vol-headline-present-market")

    assert scored_blank["volatility_penalty"] == pytest.approx(0.0, abs=1e-12)
    assert scored_present["volatility_penalty"] == pytest.approx(0.006, abs=1e-12)
    difference = scored_blank["edge_lower_bound"] - scored_present["edge_lower_bound"]
    assert difference == pytest.approx(0.006, abs=1e-12)
    assert scored_blank["edge_lower_bound"] > scored_present["edge_lower_bound"]


# --- (6) end-to-end from a real websocket_market_features.csv fixture -----


def test_end_to_end_rolling_volatility_from_websocket_features_v2(tmp_path):
    """142.1 end-to-end: MUST fail against unfixed source (volatility_penalty
    stays 0.0 -- rolling_volatility_6h never reaches _microstructure_penalty
    without the copy-through).
    """
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "market_id": "vol-e2e-market",
                "asset_id": "vol-e2e-token",
                "token_id": "vol-e2e-token",
                "collected_at_utc": "2026-02-01T01:00:00Z",  # T-5h
                "midpoint": "0.50",
            },
            {
                "market_id": "vol-e2e-market",
                "asset_id": "vol-e2e-token",
                "token_id": "vol-e2e-token",
                "collected_at_utc": "2026-02-01T04:00:00Z",  # T-2h
                "midpoint": "0.54",
            },
            {
                "market_id": "vol-e2e-market",
                "asset_id": "vol-e2e-token",
                "token_id": "vol-e2e-token",
                "collected_at_utc": "2026-02-01T06:00:00Z",  # T
                "midpoint": "0.52",
            },
        ],
    )

    features = build_features_v2(cfg, source="websocket")
    latest = features[-1]
    assert latest["prediction_timestamp"] == "2026-02-01T06:00:00Z"
    # Hand-computed sample std (ddof=1) of [0.50, 0.54, 0.52]: mean 0.52,
    # sum-sq-dev 0.0008, /(3-1) = 0.0004, sqrt = 0.02.
    assert float(latest["rolling_volatility_6h"]) == pytest.approx(0.02, abs=1e-12)

    predictions = predict_from_features(features)
    assert len(predictions) == 1  # latest_feature_rows collapses to one row.

    scored = apply_mispricing_alpha(cfg, predictions)
    assert scored[0]["volatility_source"] == "rolling_volatility_6h"
    assert scored[0]["volatility_penalty"] == pytest.approx(0.003, abs=1e-12)


# --- (7) single-snapshot asset: the honest blank case (input to WO-142b) --


def test_single_snapshot_asset_yields_blank_volatility_and_no_exception(tmp_path):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "market_id": "vol-single-snapshot-market",
                "asset_id": "vol-single-snapshot-token",
                "token_id": "vol-single-snapshot-token",
                "collected_at_utc": "2026-02-01T06:00:00Z",
                "midpoint": "0.50",
            }
        ],
    )

    features = build_features_v2(cfg, source="websocket")
    row = features[-1]
    assert row["rolling_volatility_1h"] == ""
    assert row["rolling_volatility_6h"] == ""
    assert row["rolling_volatility_24h"] == ""

    predictions = predict_from_features(features)
    assert predictions[0]["rolling_volatility_6h"] == ""
    assert predictions[0]["rolling_volatility_24h"] == ""

    scored = apply_mispricing_alpha(cfg, predictions)
    assert scored[0]["volatility_penalty"] == pytest.approx(0.0, abs=1e-12)
    assert scored[0]["volatility_source"] == "missing"
