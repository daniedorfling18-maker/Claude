from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from superbru_score_engine.config import PublicPickConfig, SuperbruConfig
from superbru_score_engine.decision.superbru import SuperbruDecisionEngine


@dataclass
class _Match:
    match_id: str
    home_team: str
    away_team: str
    commence_time: str = ""


@dataclass
class _Distribution:
    matrix: np.ndarray
    match: _Match
    diagnostics: dict
    lambda_home: float = 0.0
    lambda_away: float = 0.0


def _distribution(match_id: str, home: str, away: str, visible_grid_percent: list[list[float]]) -> _Distribution:
    """Build a matrix from the visible 0-3 x 0-3 Oddspedia exact-score cells.

    Rows are home goals 0..3 and columns are away goals 0..3. The screenshots
    also show aggregate ">3" buckets, but those are not exact scorelines, so this
    golden test intentionally verifies the engine's EV decision on the visible
    exact-score grid only.
    """
    matrix = np.asarray(visible_grid_percent, dtype=float)
    matrix = matrix / matrix.sum()
    return _Distribution(
        matrix=matrix,
        match=_Match(match_id=match_id, home_team=home, away_team=away),
        diagnostics={"source": "manual_oddspedia_screenshot_grid"},
    )


@pytest.mark.parametrize(
    (
        "match_id",
        "home",
        "away",
        "visible_grid_percent",
        "expected_modal",
        "expected_ev",
    ),
    [
        (
            "gha-pan",
            "Ghana",
            "Panama",
            [
                [7.6, 11.1, 8.0, 3.9],
                [8.5, 12.4, 9.0, 4.4],
                [4.8, 7.0, 5.1, 2.5],
                [1.8, 2.6, 1.9, 0.9],
            ],
            "1-1",
            "1-2",
        ),
        (
            "eng-cro",
            "England",
            "Croatia",
            [
                [4.6, 6.2, 4.2, 1.9],
                [7.9, 10.7, 7.2, 3.2],
                [6.9, 9.3, 6.2, 2.8],
                [4.0, 5.4, 3.6, 1.6],
            ],
            "1-1",
            "2-1",
        ),
        (
            "uzb-col",
            "Uzbekistan",
            "Colombia",
            [
                [10.4, 15.6, 11.7, 5.8],
                [7.7, 11.6, 8.7, 4.4],
                [3.1, 4.6, 3.5, 1.8],
                [0.8, 1.3, 1.0, 0.5],
            ],
            "0-1",
            "0-1",
        ),
        (
            "por-cod",
            "Portugal",
            "DR Congo",
            [
                [7.1, 6.0, 2.5, 0.7],
                [12.7, 10.7, 4.5, 1.3],
                [11.4, 9.7, 4.1, 1.1],
                [6.9, 5.8, 2.5, 0.7],
            ],
            "1-0",
            "2-0",
        ),
        (
            "aut-jor",
            "Austria",
            "Jordan",
            [
                [5.7, 5.1, 2.4, 0.8],
                [11.1, 9.9, 4.7, 1.6],
                [10.9, 9.6, 4.5, 1.5],
                [7.1, 6.2, 2.9, 1.0],
            ],
            "1-0",
            "2-0",
        ),
    ],
)
def test_manual_oddspedia_score_grids_select_superbru_ev_not_just_modal(
    match_id: str,
    home: str,
    away: str,
    visible_grid_percent: list[list[float]],
    expected_modal: str,
    expected_ev: str,
) -> None:
    distribution = _distribution(match_id, home, away, visible_grid_percent)
    engine = SuperbruDecisionEngine(SuperbruConfig(tie_epsilon=0.005), candidate_grid_goals=3, public_pick_config=PublicPickConfig())

    prediction = engine.predict(distribution)

    assert prediction.modal_score_pick.scoreline == expected_modal
    assert prediction.raw_ev_pick.scoreline == expected_ev
    assert prediction.recommended.scoreline == expected_ev
    assert prediction.raw_ev_pick.expected_points >= prediction.modal_score_pick.expected_points
