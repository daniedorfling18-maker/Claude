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
from polymarket_predictive_engine.market_relative_validation import join_clean_settled_predictions
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
    # The reason histogram is the diagnostic that was missing, and it must name
    # the genuinely-absent-market case specifically - not a blanket string that
    # a timestamp mismatch or a dirty label would also produce.
    assert payload["rejected_join_reasons"] == {"no label for this market and token": 1}
    assert payload["rejected_join_examples"][0]["market_id"] == "market-1"
    assert payload["rejected_join_examples"][0]["reason"] == "no label for this market and token"


def test_rejected_join_reasons_separate_timestamp_and_label_quality_misses(tmp_path: Path) -> None:
    """The conflation that made the diagnostic useless in its first form.

    A label present for the same market and token but at a different
    prediction_timestamp, and a label dropped for not being a clean binary
    settlement, both used to surface as the same string as a market that was
    never labelled at all. Those need three different fixes, so they must be
    three different reasons.
    """
    cfg = _cfg(tmp_path, minimum_rows=4)

    matched_pred, timestamp_label = _row_pair(
        market_id="market-ts", token_id="token-01", target=1,
        model_probability=0.7, market_probability=0.5, question="timestamp case",
    )
    timestamp_label["prediction_timestamp"] = "2026-07-03T23:59:00Z"  # same market/token, different instant

    dirty_pred, dirty_label = _row_pair(
        market_id="market-dirty", token_id="token-02", target=1,
        model_probability=0.7, market_probability=0.5, question="dirty label case",
    )
    dirty_label["target"] = ""  # not a clean binary settlement

    write_csv(
        cfg.output_root / "polymarket_predictions" / "predictions.csv",
        [matched_pred, dirty_pred],
        list(matched_pred),
    )
    write_csv(
        cfg.output_root / "polymarket_training" / "labels.csv",
        [timestamp_label, dirty_label],
        list(timestamp_label),
    )

    payload = build_family_calibration_scorecard(cfg)

    assert payload["clean_settled_joined_rows"] == 0
    assert payload["rejected_join_reasons"] == {
        "label exists for market and token but not at this prediction_timestamp": 1,
        "label is not a clean binary settlement": 1,
    }


def test_missing_model_probability_is_named_as_such(tmp_path: Path) -> None:
    """A prediction with no model column is a wiring fault, not a corpus fault."""
    cfg = _cfg(tmp_path, minimum_rows=4)
    prediction, label = _row_pair(
        market_id="market-1", token_id="token-01", target=1,
        model_probability=0.7, market_probability=0.5, question="Some question",
    )
    prediction.pop("model_probability", None)
    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [prediction], list(prediction))
    write_csv(cfg.output_root / "polymarket_training" / "labels.csv", [label], list(label))

    payload = build_family_calibration_scorecard(cfg)

    assert payload["clean_settled_joined_rows"] == 0
    assert payload["rejected_join_reasons"] == {"missing model or market probability": 1}


def test_a_model_probability_copied_from_the_midpoint_is_not_currently_diagnosed() -> None:
    """Records a REAL fail-open, so it is disclosed rather than assumed handled.

    join_clean_settled_predictions has a guard that rejects a row when
    ``model_source == market_source``. MODEL_PROBABILITY_FIELDS and
    MARKET_MIDPOINT_FIELDS are disjoint, so that comparison can never be true
    for an ordinary row and the guard is unreachable. A prediction whose model
    probability is simply the midpoint copied under a model field name is
    therefore ACCEPTED and scored as though it carried independent information.

    This test pins the current behaviour rather than asserting the guard works.
    Closing it means detecting equality of VALUES, which changes what the join
    accepts and belongs in its own change, not in a diagnostic one.
    """
    joined, rejected = join_clean_settled_predictions(
        [
            {
                "market_id": "m1",
                "token_id": "t1",
                "prediction_timestamp": "2026-01-01T00:00:00Z",
                # identical value, different field names: a copied midpoint
                "model_probability": "0.55",
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

    assert rejected == [], "the source-equality guard cannot fire on disjoint field lists"
    assert len(joined) == 1
    assert joined[0]["model_probability"] == joined[0]["market_probability"] == 0.55
    assert joined[0]["model_probability_source"] != joined[0]["market_probability_source"]


def test_incomplete_label_key_is_not_reported_as_a_timestamp_mismatch(tmp_path: Path) -> None:
    """A malformed label producer must not look like a bad join key.

    A label missing only its prediction_timestamp still registers its
    (market_id, token_id) pair, so the mismatch branch would claim the label
    exists at a different instant - sending the operator to change the join key
    when the actual fault is upstream of it.
    """
    cfg = _cfg(tmp_path, minimum_rows=4)
    prediction, label = _row_pair(
        market_id="market-1", token_id="token-01", target=1,
        model_probability=0.7, market_probability=0.5, question="Some question",
    )
    label["prediction_timestamp"] = ""  # malformed producer, pair still present

    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [prediction], list(prediction))
    write_csv(cfg.output_root / "polymarket_training" / "labels.csv", [label], list(label))

    payload = build_family_calibration_scorecard(cfg)

    assert payload["clean_settled_joined_rows"] == 0
    assert payload["rejected_join_reasons"] == {"label key is incomplete": 1}


def test_incomplete_prediction_key_blames_the_prediction_not_the_labels(tmp_path: Path) -> None:
    """A malformed prediction producer must not read as a label-corpus fault."""
    cfg = _cfg(tmp_path, minimum_rows=4)
    prediction, label = _row_pair(
        market_id="market-1", token_id="token-01", target=1,
        model_probability=0.7, market_probability=0.5, question="Some question",
    )
    prediction["prediction_timestamp"] = ""  # malformed prediction, label is fine

    write_csv(cfg.output_root / "polymarket_predictions" / "predictions.csv", [prediction], list(prediction))
    write_csv(cfg.output_root / "polymarket_training" / "labels.csv", [label], list(label))

    payload = build_family_calibration_scorecard(cfg)
    assert payload["rejected_join_reasons"] == {"prediction key is incomplete": 1}


def test_duplicate_label_drop_reasons_are_order_independent() -> None:
    """The label producer emits several horizon rows per key, so a first-wins
    setdefault would let a reordering of labels.csv swap which defect the
    histogram reports and hide the other."""
    prediction = {
        "market_id": "m1", "token_id": "t1", "prediction_timestamp": "2026-01-01T00:00:00Z",
        "model_probability": "0.7", "market_midpoint": "0.5",
    }
    dirty = {
        "market_id": "m1", "token_id": "t1", "prediction_timestamp": "2026-01-01T00:00:00Z",
        "target": "", "horizon": "all_valid", "resolution_quality": "clean_settlement",
    }
    wrong_horizon = {
        "market_id": "m1", "token_id": "t1", "prediction_timestamp": "2026-01-01T00:00:00Z",
        "target": "1", "horizon": "h24", "resolution_quality": "clean_settlement",
    }

    forward = join_clean_settled_predictions([prediction], [dirty, wrong_horizon])[1]
    reverse = join_clean_settled_predictions([prediction], [wrong_horizon, dirty])[1]

    assert forward[0]["reason"] == reverse[0]["reason"]
    assert forward[0]["reason"] == "label is not a clean binary settlement"


def test_incomplete_label_key_wins_even_when_the_label_is_also_dirty() -> None:
    """The declared precedence must be the executed precedence.

    A label that is BOTH missing its timestamp AND carries a bad horizon used to
    exit at the horizon branch, so incomplete_pairs never populated and a
    complete prediction for the same market/token was reported as a timestamp
    mismatch - pointing at the join key rather than the malformed producer.
    """
    prediction = {
        "market_id": "m1", "token_id": "t1", "prediction_timestamp": "2026-01-01T00:00:00Z",
        "model_probability": "0.7", "market_midpoint": "0.5",
    }
    both_wrong = {
        "market_id": "m1", "token_id": "t1", "prediction_timestamp": "",
        "target": "", "horizon": "h24", "resolution_quality": "clean_settlement",
    }

    rejected = join_clean_settled_predictions([prediction], [both_wrong])[1]

    assert rejected[0]["reason"] == "label key is incomplete"
