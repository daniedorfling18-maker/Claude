from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from superbru_score_engine.config import PublicPickConfig, SuperbruConfig
from superbru_score_engine.decision.public_pick import estimate_public_pick_shares
from superbru_score_engine.model import DistributionResult


Outcome = str

STRATEGY_MODES = ("raw_ev", "conservative", "exact_chase", "contrarian", "risk_adjusted")


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
    # risk diagnostics (per-candidate, context-free)
    p_zero_points: float = 0.0
    variance_points: float = 0.0
    # strategic fields (contextual: filled once the full candidate set is known)
    public_pick_share: float = 0.0  # SYNTHETIC estimate, never real pool data
    ev_vs_field: float = 0.0
    risk_adjusted_score: float = 0.0

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
    raw_ev_pick: CandidateEvaluation
    modal_score_pick: CandidateEvaluation
    conservative_pick: CandidateEvaluation
    exact_chase_pick: CandidateEvaluation
    contrarian_pick: CandidateEvaluation
    strategy_mode: str
    top_candidates: tuple[CandidateEvaluation, ...]
    diagnostics: dict


class SuperbruDecisionEngine:
    def __init__(
        self,
        config: SuperbruConfig,
        candidate_grid_goals: int,
        public_pick_config: PublicPickConfig | None = None,
    ) -> None:
        self.config = config
        self.candidate_grid_goals = candidate_grid_goals
        self.public_pick_config = public_pick_config or PublicPickConfig()

    def predict(self, distribution: DistributionResult) -> Prediction:
        matrix = distribution.matrix
        ci = self.config.ci_cutoff
        evaluations = [
            score_prediction(matrix, h, a, ci, self.config.contrarian, self.config.contrarian_weight)
            for h in range(self.candidate_grid_goals + 1)
            for a in range(self.candidate_grid_goals + 1)
        ]

        # Synthetic public-pick shares -> field EV -> per-candidate risk-adjusted score.
        favourite = _favourite_outcome(matrix)
        evaluations = _fill_strategic_fields(
            evaluations,
            favourite_outcome=favourite,
            home_team=distribution.match.home_team,
            away_team=distribution.match.away_team,
            public_pick_config=self.public_pick_config,
            superbru=self.config,
        )

        # Pick types. With score=adjusted EV, _top_by reproduces the prior default
        # recommendation exactly, so exact_chase (weight 1) and risk_adjusted (zero
        # weights) collapse to raw_ev by construction.
        tie = self.config.tie_epsilon
        w = self.config.exact_chase_weight
        raw_ev_pick = _top_by(evaluations, lambda c: c.adjusted_expected_points, tie)
        modal_score_pick = max(evaluations, key=lambda c: (c.p_exact, -(c.home_goals + c.away_goals)))
        conservative_pick = max(evaluations, key=lambda c: (c.p_outcome, c.expected_points, c.p_close))
        exact_chase_pick = _top_by(evaluations, lambda c: c.expected_points + (w - 1.0) * 3.0 * c.p_exact, tie)
        risk_adjusted_pick = _top_by(evaluations, lambda c: c.risk_adjusted_score, tie)
        contrarian_pick = _contrarian_pick(evaluations, raw_ev_pick)

        picks = {
            "raw_ev": raw_ev_pick,
            "conservative": conservative_pick,
            "exact_chase": exact_chase_pick,
            "contrarian": contrarian_pick,
            "risk_adjusted": risk_adjusted_pick,
        }
        mode = self.config.strategy_mode if self.config.strategy_mode in picks else "raw_ev"
        recommended = picks[mode]

        top = tuple(sorted(evaluations, key=lambda c: (c.expected_points, c.p_close), reverse=True)[:3])
        diagnostics = dict(distribution.diagnostics)
        diagnostics.update(decision_diagnostics(matrix, evaluations, recommended, ci))
        diagnostics.update(
            {
                "strategy_mode": mode,
                "raw_ev_scoreline": raw_ev_pick.scoreline,
                "modal_score_scoreline": modal_score_pick.scoreline,
                "conservative_scoreline": conservative_pick.scoreline,
                "exact_chase_scoreline": exact_chase_pick.scoreline,
                "contrarian_scoreline": contrarian_pick.scoreline,
                "public_pick_model": "synthetic" if self.public_pick_config.enabled else "disabled",
                "recommended_public_pick_share": float(recommended.public_pick_share),
                "recommended_ev_vs_field": float(recommended.ev_vs_field),
                "recommended_risk_adjusted_score": float(recommended.risk_adjusted_score),
            }
        )
        return Prediction(
            match_id=distribution.match.match_id,
            home_team=distribution.match.home_team,
            away_team=distribution.match.away_team,
            commence_time=distribution.match.commence_time,
            recommended=recommended,
            raw_ev_pick=raw_ev_pick,
            modal_score_pick=modal_score_pick,
            conservative_pick=conservative_pick,
            exact_chase_pick=exact_chase_pick,
            contrarian_pick=contrarian_pick,
            strategy_mode=mode,
            top_candidates=top,
            diagnostics=diagnostics,
        )


def _top_by(evaluations, score, tie_epsilon: float) -> CandidateEvaluation:
    """Maximise ``score``; ties within ``tie_epsilon`` broken by close prob, then
    exact prob, then preferring the lower-total scoreline. With
    score=adjusted_expected_points this is the prior default recommendation, so
    exact_chase (weight 1) and risk_adjusted (zero weights) reduce to it exactly."""
    best = max(score(c) for c in evaluations)
    contenders = [c for c in evaluations if best - score(c) <= tie_epsilon]
    contenders.sort(key=lambda c: (c.p_close, c.p_exact, -(c.home_goals + c.away_goals)), reverse=True)
    return contenders[0]


def _contrarian_pick(evaluations: list[CandidateEvaluation], raw_ev_pick: CandidateEvaluation) -> CandidateEvaluation:
    """Most field-beating + differentiated candidate, with an EV floor so it is never a joke pick."""
    floor = 0.5 * raw_ev_pick.expected_points
    eligible = [c for c in evaluations if c.expected_points >= floor] or list(evaluations)
    return max(eligible, key=lambda c: (c.ev_vs_field + 0.75 * (1.0 - c.public_pick_share), c.expected_points))


def _favourite_outcome(matrix: np.ndarray) -> Outcome:
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    return {"home": home, "draw": draw, "away": away}


def _fill_strategic_fields(
    evaluations: list[CandidateEvaluation],
    *,
    favourite_outcome: dict,
    home_team: str,
    away_team: str,
    public_pick_config: PublicPickConfig,
    superbru: SuperbruConfig,
) -> list[CandidateEvaluation]:
    fav_label = max(favourite_outcome, key=favourite_outcome.get)
    if public_pick_config.enabled:
        shares = estimate_public_pick_shares(
            [(c.home_goals, c.away_goals) for c in evaluations],
            favourite_outcome=fav_label,
            home_team=home_team,
            away_team=away_team,
            config=public_pick_config,
        )
        share_of = {k: v.public_pick_share for k, v in shares.items()}
        field_ev = sum(share_of[(c.home_goals, c.away_goals)] * c.expected_points for c in evaluations)
    else:
        share_of = {(c.home_goals, c.away_goals): 0.0 for c in evaluations}
        field_ev = 0.0

    alpha = superbru.public_pick_weight
    delta = superbru.differentiation_weight
    beta = superbru.risk_aversion
    gamma = superbru.variance_penalty

    filled: list[CandidateEvaluation] = []
    for c in evaluations:
        share = share_of[(c.home_goals, c.away_goals)]
        ev_vs_field = c.expected_points - field_ev if public_pick_config.enabled else 0.0
        risk_adjusted = (
            c.expected_points
            + alpha * ev_vs_field
            + delta * (1.0 - share)
            - beta * c.p_zero_points
            - gamma * c.variance_points
        )
        filled.append(
            replace(c, public_pick_share=share, ev_vs_field=ev_vs_field, risk_adjusted_score=risk_adjusted)
        )
    return filled


def decision_diagnostics(
    matrix: np.ndarray,
    evaluations: list[CandidateEvaluation],
    recommended: CandidateEvaluation,
    ci_cutoff: float,
) -> dict[str, float | str]:
    modal_home, modal_away = (int(value) for value in np.unravel_index(np.argmax(matrix), matrix.shape))
    modal_candidate = score_prediction(matrix, modal_home, modal_away, ci_cutoff)
    top_raw_ev = max(evaluations, key=lambda candidate: candidate.expected_points)
    second_ev = sorted((candidate.expected_points for candidate in evaluations), reverse=True)[1] if len(evaluations) > 1 else 0.0
    return {
        "recommended_scoreline": recommended.scoreline,
        "recommended_expected_points": float(recommended.expected_points),
        "recommended_adjusted_expected_points": float(recommended.adjusted_expected_points),
        "raw_ev_scoreline": top_raw_ev.scoreline,
        "raw_ev_expected_points": float(top_raw_ev.expected_points),
        "modal_scoreline_ev": modal_candidate.scoreline,
        "modal_scoreline_expected_points": float(modal_candidate.expected_points),
        "ev_gap_recommended_to_modal": float(recommended.expected_points - modal_candidate.expected_points),
        "ev_gap_to_second": float(max(0.0, recommended.expected_points - second_ev)),
    }


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
    # risk diagnostics
    p_zero_points = max(0.0, 1.0 - p_outcome)
    e_points_sq = 9.0 * p_exact + 2.25 * p_close_non_exact + 1.0 * p_outcome_only
    variance_points = max(0.0, e_points_sq - expected_points ** 2)
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
        p_zero_points=float(p_zero_points),
        variance_points=float(variance_points),
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
