from __future__ import annotations

from pathlib import Path

import yaml

from polymarket_predictive_engine.config import load_config
from polymarket_predictive_engine.features_v2 import build_features_v2
from polymarket_predictive_engine.mispricing_alpha import apply_mispricing_alpha
from polymarket_predictive_engine.utils import write_csv


def _config(tmp_path: Path):
    raw = yaml.safe_load(Path("polymarket_predictive_config.example.yaml").read_text(encoding="utf-8"))
    raw["paths"]["data_root"] = str(tmp_path)
    raw["paths"]["output_root"] = str(tmp_path / "outputs")
    raw["paths"]["database_path"] = str(tmp_path / "work" / "paper.sqlite")
    raw["mispricing_alpha"]["model_residual_shrinkage"] = 1.0
    raw["mispricing_alpha"]["max_model_residual_adjustment"] = 0.1
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_config(path)


def test_websocket_feature_build_classifies_blank_fed_family(tmp_path):
    cfg = _config(tmp_path)
    write_csv(
        cfg.output_root / "polymarket_training" / "websocket_market_features.csv",
        [
            {
                "collected_at_utc": "2026-07-02T12:00:00Z",
                "market_id": "fed-sequence",
                "asset_id": "fed-token",
                "market_slug": "will-the-fed-cutpausecut-in-the-next-three-decisions-julsepoct",
                "question": "Will the Fed Cut-Pause-Cut in the next three decisions?",
                "category": "",
                "best_bid": "0.40",
                "best_ask": "0.42",
                "midpoint": "0.41",
                "top_bid_size": "100",
                "top_ask_size": "100",
            }
        ],
    )

    features = build_features_v2(cfg, source="websocket")

    assert len(features) == 1
    assert features[0]["category"] == "macro_rates"


def test_alpha_scoring_rewrites_unknown_category_to_classified_research_family(tmp_path):
    cfg = _config(tmp_path)

    scored = apply_mispricing_alpha(
        cfg,
        [
            {
                "market_id": "fed-sequence",
                "market_slug": "will-the-fed-cutpausecut-in-the-next-three-decisions-julsepoct",
                "question": "Will the Fed Cut-Pause-Cut in the next three decisions?",
                "category": "unknown",
                "token_id": "fed-token",
                "outcome": "Yes",
                "prediction_timestamp": "2026-07-02T12:00:00Z",
                "market_midpoint": "0.40",
                "raw_probability": "0.45",
                "calibrated_probability": "0.45",
                "model_probability": "0.45",
                "executable_price": "0.41",
                "spread": "0.02",
                "liquidity": "1000",
                "time_to_close_hours": "72",
                "confidence": "0.8",
            }
        ],
    )

    assert scored[0]["category"] == "macro_rates"
    assert scored[0]["signal_cohort"] == "macro_rates"
