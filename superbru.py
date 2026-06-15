from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from superbru_score_engine.config import SuperbruConfig
from superbru_score_engine.model import DistributionResult


Outcome = str


@dataclass(frozen=True)
class CandidateEvaluation:
    home_goals: int
    away_goals: int
    expected_points: float
    adjusted_expected_points: float
    p_exact: float
    p_close: float
    p_close_non_exact: float
    p_outcome: float
    p_outcome_only: float
    outcome: Outcome

    @property
    def scoreline(self) -> str:
        return f"{self.home_goals}-{self.away_goals}"


@dataclass(frozen=True)
class Prediction:
    match_id: str
    home_team: str
    away_team: str
    commence_time: str
    recommended: CandidateEvaluation
    top_candidates: tuple[CandidateEvaluation, ...]
    diagnostics: dict


class SuperbruDecisionEngine:
    def __init__(self, config: SuperbruConfig, candidate_grid_goals: int) -> None:
        self.config = config
        self.candidate_grid_goals = candidate_grid_goals

    def predict(self, distribution: DistributionResult) -> Prediction:
        evaluations = [
            score_prediction(
                matrix=distribution.matrix,
                pred_home=home_goals,
                pred_away=away_goals,
                ci_cutoff=self.config.ci_cutoff,
                contrarian=self.config.contrarian,
                contrarian_weight=self.config.contrarian_weight,
            )
            for home_goals in range(self.candidate_grid_goals + 1)
            for away_goals in range(self.candidate_grid_goals + 1)
        ]
        max_ev = max(candidate.adjusted_expected_points for candidate in evaluations)
        contenders = [
            candidate
            for candidate in evaluations
            if max_ev - candidate.adjusted_expected_points <= self.config.tie_epsilon
        ]
        contenders.sort(key=lambda candidate: (candidate.p_close, candidate.p_exact, -candidate.home_goals - candidate.away_goals), reverse=True)
        recommended = contenders[0]
        top = tuple(
            sorted(evaluations, key=lambda candidate: (candidate.adjusted_expected_points, candidate.p_close), reverse=True)[:3]
        )
        return Prediction(
            match_id=distribution.match.match_id,
            home_team=distribution.match.home_team,
            away_team=distribution.match.away_team,
            commence_time=distribution.match.commence_time,
            recommended=recommended,
            top_candidates=top,
            diagnostics=distribution.diagnostics,
        )


def score_prediction(
    matrix: np.ndarray,
    pred_home: int,
    pred_away: int,
    ci_cutoff: float,
    contrarian: bool = False,
    contrarian_weight: float = 0.0,
) -> CandidateEvaluation:
    pred_outcome = outcome(pred_home, pred_away)
    p_exact = float(matrix[pred_home, pred_away]) if pred_home < matrix.shape[0] and pred_away < matrix.shape[1] else 0.0
    p_close = 0.0
    p_outcome = 0.0

    for actual_home in range(matrix.shape[0]):
        for actual_away in range(matrix.shape[1]):
            prob = float(matrix[actual_home, actual_away])
            if outcome(actual_home, actual_away) != pred_outcome:
                continue
            p_outcome += prob
            if closeness_index(pred_home, pred_away, actual_home, actual_away) <= ci_cutoff:
                p_close += prob

    p_close_non_exact = max(0.0, p_close - p_exact)
    p_outcome_only = max(0.0, p_outcome - p_close)
    expected_points = 3.0 * p_exact + 1.5 * p_close_non_exact + 1.0 * p_outcome_only
    adjusted = expected_points
    if contrarian and contrarian_weight > 0:
        adjusted = expected_points - contrarian_weight * p_exact

    return CandidateEvaluation(
        home_goals=pred_home,
        away_goals=pred_away,
        expected_points=float(expected_points),
        adjusted_expected_points=float(adjusted),
        p_exact=p_exact,
        p_close=float(p_close),
        p_close_non_exact=float(p_close_non_exact),
        p_outcome=float(p_outcome),
        p_outcome_only=float(p_outcome_only),
        outcome=pred_outcome,
    )


def outcome(home_goals: int, away_goals: int) -> Outcome:
    if home_goals > away_goals:
        return "home"
    if home_goals < away_goals:
        return "away"
    return "draw"


def closeness_index(pred_home: int, pred_away: int, actual_home: int, actual_away: int) -> float:
    pred_goal_diff = pred_home - pred_away
    actual_goal_diff = actual_home - actual_away
    pred_total = pred_home + pred_away
    actual_total = actual_home + actual_away
    return abs(pred_goal_diff - actual_goal_diff) + abs(pred_total - actual_total) / 2.0


def score_actual_prediction(pred_home: int, pred_away: int, actual_home: int, actual_away: int, ci_cutoff: float) -> float:
    if pred_home == actual_home and pred_away == actual_away:
        return 3.0
    if outcome(pred_home, pred_away) != outcome(actual_home, actual_away):
        return 0.0
    if closeness_index(pred_home, pred_away, actual_home, actual_away) <= ci_cutoff:
        return 1.5
    return 1.0

