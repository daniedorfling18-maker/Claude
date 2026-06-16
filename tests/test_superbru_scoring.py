from __future__ import annotations

from superbru_score_engine.decision.superbru import closeness_index, score_actual_prediction


def test_exact_score_gets_three_points() -> None:
    assert score_actual_prediction(2, 1, 2, 1, 1.5) == 3.0


def test_close_score_gets_one_point_five() -> None:
    assert score_actual_prediction(2, 1, 1, 0, 1.5) == 1.5


def test_right_outcome_only_gets_one_point() -> None:
    assert score_actual_prediction(3, 0, 1, 0, 1.5) == 1.0


def test_wrong_outcome_gets_zero() -> None:
    assert score_actual_prediction(2, 1, 1, 1, 1.5) == 0.0


def test_closeness_index_matches_distance_examples() -> None:
    assert closeness_index(2, 1, 1, 0) <= 1.5
    assert closeness_index(2, 0, 1, 0) <= 1.5
    assert closeness_index(2, 1, 4, 2) > 1.5
