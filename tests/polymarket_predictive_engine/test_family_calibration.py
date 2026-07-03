from __future__ import annotations

from pathlib import Path

from polymarket_predictive_engine.cli import COMMANDS
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.family_calibration import (
    CLASS_INSUFFICIENT,
    CLASS_MARKET_BEATS,
    CLASS_MODEL_BEATS,
    build_family_calibration_scorecard,
)
from polymarket_predictive_engine.utils import csv_columns, read_csv_rows, write_csv


def _cfg(tmp_path: Path, *, minimum_rows: int = 4) -> EngineConfig:
    return EngineConfig(
        raw={
            "paths": {"output_root": str(tmp_path / "outputs")},
            "calibration": {"buckets": 5},
            "family_calibration": {
                "minimum_rows": minimum_rows,
                "bucket_count": 5,
                "bootstrap_iterations": 100,
                "bootstrap_seed": 7,
            },
        },
        path=tmp_path / "cfg.yaml",
    )


def _row_pair(
    *,
    market_id: str,
    token_id: str,
    target: int,
    model_probability: float,
    market_probability: float,
    question: str,
    category: str = "sports",
) -> tuple[dict[str, object], dict[str, object]]:
    prediction_timestamp = f"2026-07-03T00:{int(token_id[-2:]):02d}:00Z"
    prediction = {
        "market_id": market_id,
        "token_id": token_id,
        "prediction_timestamp": prediction_timestamp,
        "category": category,
        "market_slug": market_id,
        "question": question,
        "model_probability": model_probability,
        "market_midpoint": market_probability,
        "best_bid": max(0.01, market_probability - 0.01),
        "best_ask": min(0.99, market_probability + 0.01),
        "liquidity": 1000,
    }
    label = {
        "market_id": market_id,
        "token_id": token_id,
        "prediction_timestamp": prediction_timestamp,
        "horizon": "all_valid",
        "target": target,
        "resolution_quality": "clean_settlement",
    }
    return prediction, label


def _write_fixture_rows(cfg: EngineConfig) -> None:
    predictions: list[dict[str, object]] = []
    labels: list[dict[str, object]] = []

    specs: list[tuple[str, str, int, float, float, str, str, int]] = [
        (
            "macro-good",
            "macro_rates",
            6,
            0.90,
            0.55,
            "Will the Fed cut interest rates after the July 2026 meeting?",
            "fed",
            1,
        ),
        (
            "tennis-bad",
            "tennis_tennis_winner",
            6,
            0.10,
            0.55,
            "Will the tennis player be the winner of Wimbledon?",
            "sports",
            1,
        ),
        (
            "ai-thin",
            "ai_model_leader",
            2,
            0.90,
            0.55,
            "Will OpenAI release the best AI model by December?",
            "technology",
            1,
        ),
    ]
    token_counter = 0
    for prefix, _expected_family, count, win_model, win_market, question, category, first_target in specs:
        for i in range(count):
            target = first_target if i % 2 == 0 else 1 - first_target
            model_probability = win_model if target == 1 else 1 - win_model
            market_probability = win_market if target == 1 else 1 - win_market
            token_counter += 1
            prediction, label = _row_pair(
                market_id=f"{prefix}-{i}",
                token_id=f"t{token_counter:02d}",
                target=target,
                model_probability=model_probability,
                market_probability=market_probability,
                question=question,
                category=category,
            )
            predictions.append(prediction)
            labels.append(label)

    train_root = cfg.output_root / "polymarket_training"
    pred_root = cfg.output_root / "polymarket_predictions"
    write_csv(pred_root / "predictions.csv", predictions)
    write_csv(train_root / "labels.csv", labels)


def test_family_calibration_classifies_family_skill_and_thin_samples(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, minimum_rows=4)
    _write_fixture_rows(cfg)

    payload = build_family_calibration_scorecard(cfg)
    by_family = {row["family"]: row for row in payload["families"]}

    assert payload["status"] == "ok"
    assert payload["clean_settled_joined_rows"] == 14
    assert by_family["macro_rates"]["evidence_class"] == CLASS_MODEL_BEATS
    assert by_family["macro_rates"]["brier_gain_ci_low"] > 0
    assert by_family["tennis_tennis_winner"]["evidence_class"] == CLASS_MARKET_BEATS
    assert by_family["tennis_tennis_winner"]["brier_gain_ci_high"] < 0
    assert by_family["ai_model_leader"]["evidence_class"] == CLASS_INSUFFICIENT
    assert by_family["ai_model_leader"]["sample_status"] == "below_minimum"
    assert payload["paper_trading_invoked"] is False
    assert payload["live_trading_invoked"] is False

    csv_path = cfg.governance_root / "family_calibration_scorecard.csv"
    assert csv_path.exists()
    assert "family" in csv_columns(csv_path)
    assert "brier_gain_ci_low" in csv_columns(csv_path)
    assert len(read_csv_rows(csv_path)) == 3


def test_family_calibration_fails_closed_with_no_clean_rows(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, minimum_rows=4)

    payload = build_family_calibration_scorecard(cfg)

    assert payload["status"] == "no_clean_settled_rows"
    assert payload["families"] == []
    assert payload["evidence_counts"] == {}
    assert read_csv_rows(cfg.governance_root / "family_calibration_scorecard.csv") == []


def test_family_calibration_cli_is_registered() -> None:
    assert "family-calibration" in COMMANDS
