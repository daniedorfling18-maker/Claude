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


def test_rejected_join_reasons_are_published_not_just_counted(tmp_path: Path) -> None:
    """A 100% rejection rate must be diagnosable from the artifact alone.

    Measured 2026-08-15 on origin/vps-telemetry: the scorecard reported
    ``rejected_join_rows: 16910`` with ``clean_settled_joined_rows: 0`` and no
    reason breakdown, so there was no way to tell "predictions and labels cover
    disjoint markets" apart from "the model probability IS the market midpoint"
    or an exact-timestamp key that never matches. The join already computes a
    reason per rejection; this pins that it reaches the artifact.
    """
    cfg = _cfg(tmp_path, minimum_rows=4)
    prediction, label = _row_pair(
        market_id="market-1",
        token_id="token-01",
        target=1,
        model_probability=0.7,
        market_probability=0.5,
        question="Some question",
    )
    # A label whose market the prediction never covers: the disjoint-population
    # case, which is exactly what the corpus sampling defect produced.
    label["market_id"] = "market-unrelated"
    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [prediction], list(prediction))
    write_csv(cfg.output_root / "polymarket_training" / "labels.csv", [label], list(label))

    payload = build_family_calibration_scorecard(cfg)

    assert payload["status"] == "no_clean_settled_rows"
    assert payload["clean_settled_joined_rows"] == 0
    assert payload["rejected_join_rows"] == 1
    # The reason histogram is the diagnostic that was missing.
    assert payload["rejected_join_reasons"] == {"no clean settled label": 1}
    assert payload["rejected_join_examples"][0]["market_id"] == "market-1"
    assert payload["rejected_join_examples"][0]["reason"] == "no clean settled label"


def test_rejected_join_reasons_distinguish_the_same_source_defect(tmp_path: Path) -> None:
    """The other candidate cause, and it is a different fix entirely.

    If the model probability and the market midpoint resolve to the same source
    column, every row is rejected for a reason that has nothing to do with the
    corpus - so the two must not be conflated in the artifact.
    """
    cfg = _cfg(tmp_path, minimum_rows=4)
    prediction, label = _row_pair(
        market_id="market-1",
        token_id="token-01",
        target=1,
        model_probability=0.7,
        market_probability=0.5,
        question="Some question",
    )
    # Strip every distinct model field so model and market resolve identically.
    prediction.pop("model_probability", None)
    prediction["market_midpoint"] = 0.5
    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [prediction], list(prediction))
    write_csv(cfg.output_root / "polymarket_training" / "labels.csv", [label], list(label))

    payload = build_family_calibration_scorecard(cfg)

    assert payload["clean_settled_joined_rows"] == 0
    reasons = payload["rejected_join_reasons"]
    assert reasons, "a rejection reason must always be published"
    assert "no clean settled label" not in reasons
