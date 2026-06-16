from __future__ import annotations

import math

from superbru_score_engine.backtest.metrics import (
    brier_score_1x2,
    log_loss_1x2,
    low_score_calibration,
    outcome_label,
    result_vector,
    right_result_hit,
    scoreline_hit,
    summarise_validation,
)


def test_result_helpers() -> None:
    assert outcome_label(2, 1) == "home"
    assert outcome_label(1, 1) == "draw"
    assert outcome_label(0, 1) == "away"
    assert result_vector(2, 1) == (1.0, 0.0, 0.0)
    assert scoreline_hit(2, 1, 2, 1)
    assert not scoreline_hit(2, 1, 1, 0)
    assert right_result_hit(2, 1, 1, 0)
    assert not right_result_hit(2, 1, 1, 1)


def test_brier_and_log_loss_1x2() -> None:
    probs = (0.60, 0.25, 0.15)
    assert math.isclose(brier_score_1x2(probs, "home"), (0.60 - 1) ** 2 + 0.25**2 + 0.15**2)
    assert math.isclose(log_loss_1x2(probs, "home"), -math.log(0.60))


def test_low_score_calibration_counts_observed_scores() -> None:
    rows = low_score_calibration(
        [
            {"actual_scoreline": "1-0", "p_score_1_0": 0.20, "p_score_0_0": 0.10},
            {"actual_scoreline": "0-0", "p_score_1_0": 0.15, "p_score_0_0": 0.30},
        ],
        scorelines=("1-0", "0-0"),
    )
    by_score = {row["scoreline"]: row for row in rows}
    assert by_score["1-0"]["count"] == 2
    assert by_score["1-0"]["observed_frequency"] == 0.5
    assert by_score["0-0"]["observed_frequency"] == 0.5


def test_summarise_validation_reports_core_metrics() -> None:
    records = [
        {
            "actual_scoreline": "1-0",
            "actual_result": "home",
            "model_points": 3.0,
            "model_expected_points": 1.2,
            "model_exact_hit": 1.0,
            "model_right_result_hit": 1.0,
            "model_close_hit": 0.0,
            "naive_points": 1.5,
            "naive_exact_hit": 0.0,
            "naive_right_result_hit": 1.0,
            "naive_close_hit": 1.0,
            "model_home_win": 0.60,
            "model_draw": 0.25,
            "model_away_win": 0.15,
            "brier_1x2": brier_score_1x2((0.60, 0.25, 0.15), "home"),
            "log_loss_1x2": log_loss_1x2((0.60, 0.25, 0.15), "home"),
            "p_score_1_0": 0.20,
        },
        {
            "actual_scoreline": "1-1",
            "actual_result": "draw",
            "model_points": 0.0,
            "model_expected_points": 1.1,
            "model_exact_hit": 0.0,
            "model_right_result_hit": 0.0,
            "model_close_hit": 0.0,
            "naive_points": 1.5,
            "naive_exact_hit": 0.0,
            "naive_right_result_hit": 1.0,
            "naive_close_hit": 1.0,
            "model_home_win": 0.50,
            "model_draw": 0.30,
            "model_away_win": 0.20,
            "brier_1x2": brier_score_1x2((0.50, 0.30, 0.20), "draw"),
            "log_loss_1x2": log_loss_1x2((0.50, 0.30, 0.20), "draw"),
            "p_score_1_0": 0.15,
        },
    ]
    summary = summarise_validation(records)
    assert summary["matches_scored"] == 2
    assert summary["average_model_points"] == 1.5
    assert summary["average_naive_points"] == 1.5
    assert summary["exact_score_hit_rate"] == 0.5
    assert summary["right_result_accuracy"] == 0.5
    assert summary["draw_calibration"]["observed_draw_frequency"] == 0.5
    assert "naive" in summary["baseline_metrics"]
