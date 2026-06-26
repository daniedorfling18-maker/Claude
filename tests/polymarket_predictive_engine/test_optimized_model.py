from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.models.calibrated import load_prediction_models, predict_from_features
from polymarket_predictive_engine.models.optimized import (
    predict_optimized_probability,
    train_optimized_model,
)
from polymarket_predictive_engine.utils import write_csv, write_json


def _config(tmp_path: Path):
    raw = yaml.safe_load(
        Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8")
    )
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["governance_thresholds"]["min_paper_labels"] = 0
    raw["ml_optimization"].update(
        {
            "minimum_rows": 16,
            "minimum_markets": 8,
            "minimum_train_markets": 3,
            "final_holdout_fraction": 0.25,
            "minimum_holdout_rows": 4,
            "minimum_holdout_markets": 2,
            "walk_forward_splits": 2,
            "max_rows_per_token": 0,
            "candidate_l2": [0.1, 1.0, 10.0],
            "bootstrap_iterations": 50,
        }
    )
    path = tmp_path / "config.yaml"
    write_json(tmp_path / "raw_config.json", raw)
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def _write_training_rows(cfg, *, useful_signal: bool) -> list[dict[str, str]]:
    train_root = cfg.output_root / "polymarket_training"
    features: list[dict[str, str]] = []
    labels: list[dict[str, str]] = []
    for market_idx in range(8):
        target = 1 if market_idx % 2 else 0
        for snap_idx in range(3):
            timestamp = f"2026-01-{market_idx + 1:02d}T0{snap_idx}:00:00Z"
            signal = (1 if target else -1) if useful_signal else 0
            feature = {
                "market_id": f"market-{market_idx}",
                "market_slug": f"market-{market_idx}",
                "token_id": f"token-{market_idx}",
                "prediction_timestamp": timestamp,
                "category": "synthetic",
                "question": "Synthetic market",
                "outcome": "YES",
                "implied_probability": "0.5",
                "midpoint": "0.5",
                "best_ask": "0.45",
                "executable_buy_price": "0.45",
                "spread": "0.02",
                "liquidity": "100",
                "hours_to_close": "24",
                "signal_strength": str(signal),
            }
            features.append(feature)
            labels.append(
                {
                    "market_id": feature["market_id"],
                    "token_id": feature["token_id"],
                    "prediction_timestamp": timestamp,
                    "horizon": "all_valid",
                    "target": str(target),
                }
            )
    write_csv(train_root / "features_v2.csv", features)
    write_csv(train_root / "labels.csv", labels)
    return features


def test_optimized_model_promotes_clear_signal_to_champion(tmp_path):
    cfg = _config(tmp_path)
    features = _write_training_rows(cfg, useful_signal=True)

    artifact = train_optimized_model(cfg)

    assert artifact["status"] == "trained"
    assert artifact["deployment_mode"] == "champion"
    assert "signal_strength" in artifact["feature_columns"]
    assert "best_ask" not in artifact["feature_columns"]
    yes_probability = predict_optimized_probability(
        {**features[-1], "signal_strength": "1"}, artifact
    )
    no_probability = predict_optimized_probability(
        {**features[-1], "signal_strength": "-1"}, artifact
    )
    assert yes_probability > no_probability
    assert yes_probability > 0.55
    assert no_probability < 0.45

    model, category_models = load_prediction_models(cfg)
    predictions = predict_from_features(features, model=model, category_models=category_models)
    assert predictions
    assert predictions[0]["model_source"] == "optimized_market_anchored_logistic"


def test_optimized_model_without_incremental_signal_stays_shadow(tmp_path):
    cfg = _config(tmp_path)
    _write_training_rows(cfg, useful_signal=False)

    artifact = train_optimized_model(cfg)

    assert artifact["status"] == "trained"
    assert artifact["deployment_mode"] == "shadow"
    assert artifact["champion_blockers"]
