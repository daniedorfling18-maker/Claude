from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from superbru_score_engine.config import PublicPickConfig, SensitivityConfig, SuperbruConfig
from superbru_score_engine.decision.public_pick import estimate_public_pick_shares
from superbru_score_engine.decision.sensitivity import build_sensitivity_scenarios, summarise_sensitivity
from superbru_score_engine.model import DistributionResult


Outcome = str

STRATEGY_MODES = ("raw_ev", "conservative", "exact_chase", "contrarian", "risk_adjusted", "private_chase")


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
    risk_adjusted_pick: CandidateEvaluation
    private_chase_pick: CandidateEvaluation
    strategy_mode: str
    top_candidates: tuple[CandidateEvaluation, ...]
    diagnostics: dict


class SuperbruDecisionEngine:
    def __init__(
        self,
        config: SuperbruConfig,
        candidate_grid_goals: int,
        public_pick_config: PublicPickConfig | None = None,
        sensitivity_config: SensitivityConfig | None = None,
    ) -> None:
        self.config = config
        self.candidate_grid_goals = candidate_grid_goals
        self.public_pick_config = public_pick_config or PublicPickConfig()
        self.sensitivity_config = sensitivity_config or SensitivityConfig()

    def predict(self, distribution: DistributionResult) -> Prediction:
        matrix = distribution.matrix
        selections = self._select_for_matrix(matrix, self.config, self.public_pick_config, distribution.match.home_team, distribution.match.away_team)
        evaluations = selections["evaluations"]
        recommended = selections["recommended"]
        raw_ev_pick = selections["raw_ev"]
        modal_score_pick = selections["modal_score"]
        conservative_pick = selections["conservative"]
        exact_chase_pick = selections["exact_chase"]
        contrarian_pick = selections["contrarian"]
        risk_adjusted_pick = selections["risk_adjusted"]
        private_chase_pick = selections["private_chase"]
        mode = selections["mode"]

        top = tuple(sorted(evaluations, key=lambda c: (c.expected_points, c.p_close), reverse=True)[:3])
        diagnostics = dict(distribution.diagnostics)
        diagnostics.update(decision_diagnostics(matrix, evaluations, recommended, self.config.ci_cutoff))
        diagnostics.update(
            {
                "strategy_mode": mode,
                "raw_ev_scoreline": raw_ev_pick.scoreline,
                "modal_score_scoreline": modal_score_pick.scoreline,
                "conservative_scoreline": conservative_pick.scoreline,
                "exact_chase_scoreline": exact_chase_pick.scoreline,
                "contrarian_scoreline": contrarian_pick.scoreline,
                "risk_adjusted_scoreline": risk_adjusted_pick.scoreline,
                "private_chase_scoreline": private_chase_pick.scoreline,
                "private_chase_expected_points": float(private_chase_pick.expected_points),
                "private_chase_ev_loss": float(max(0.0, raw_ev_pick.expected_points - private_chase_pick.expected_points)),
                "private_chase_p_exact": float(private_chase_pick.p_exact),
                "private_chase_public_pick_share": float(private_chase_pick.public_pick_share),
                "private_chase_max_ev_loss": float(self.config.private_chase_max_ev_loss),
                "public_pick_model": "synthetic" if self.public_pick_config.enabled else "disabled",
                "recommended_public_pick_share": float(recommended.public_pick_share),
                "recommended_ev_vs_field": float(recommended.ev_vs_field),
                "recommended_risk_adjusted_score": float(recommended.risk_adjusted_score),
            }
        )
        diagnostics.update(self._sensitivity_for_distribution(distribution, recommended.scoreline))
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
            risk_adjusted_pick=risk_adjusted_pick,
            private_chase_pick=private_chase_pick,
            strategy_mode=mode,
            top_candidates=top,
            diagnostics=diagnostics,
        )

    def _select_for_matrix(
        self,
        matrix: np.ndarray,
        superbru: SuperbruConfig,
        public_pick_config: PublicPickConfig,
        home_team: str,
        away_team: str,
    ) -> dict[str, object]:
        evaluations = _evaluate_candidates(matrix, superbru, self.candidate_grid_goals)
        return _select_picks(
            matrix=matrix,
            evaluations=evaluations,
            superbru=superbru,
            public_pick_config=public_pick_config,
            home_team=home_team,
            away_team=away_team,
        )

    def _sensitivity_for_distribution(self, distribution: DistributionResult, base_scoreline: str) -> dict[str, object]:
        if not self.sensitivity_config.enabled:
            return {"sensitivity_enabled": False}
        lambda_home = getattr(distribution, "lambda_home", None)
        lambda_away = getattr(distribution, "lambda_away", None)
        if lambda_home is None or lambda_away is None:
            return {
                "sensitivity_enabled": False,
                "sensitivity_skip_reason": "lambda_home/lambda_away unavailable on distribution",
            }
        rho = float(distribution.diagnostics.get("dixon_coles_rho", distribution.diagnostics.get("rho", 0.0)) or 0.0)
        scenarios = build_sensitivity_scenarios(
            base_matrix=distribution.matrix,
            lambda_home=float(lambda_home),
            lambda_away=float(lambda_away),
            rho=rho,
            superbru=self.config,
            public_pick=self.public_pick_config,
            sensitivity=self.sensitivity_config,
        )

        def pick_fn(matrix: np.ndarray, superbru: SuperbruConfig, public_pick: PublicPickConfig) -> str:
            selected = self._select_for_matrix(matrix, superbru, public_pick, distribution.match.home_team, distribution.match.away_team)
            return selected["recommended"].scoreline

        summary = summarise_sensitivity(
            base_scoreline=base_scoreline,
            scenarios=scenarios,
            pick_fn=pick_fn,
            warning_threshold=self.sensitivity_config.stability_warning_threshold,
        )
        summary["sensitivity_enabled"] = True
        return summary


def _evaluate_candidates(matrix: np.ndarray, superbru: SuperbruConfig, candidate_grid_goals: int) -> list[CandidateEvaluation]:
    return [
        score_prediction(matrix, h, a, superbru.ci_cutoff, superbru.contrarian, superbru.contrarian_weight)
        for h in range(candidate_grid_goals + 1)
        for a in range(candidate_grid_goals + 1)
    ]


def _select_picks(
    *,
    matrix: np.ndarray,
    evaluations: list[CandidateEvaluation],
    superbru: SuperbruConfig,
    public_pick_config: PublicPickConfig,
    home_team: str,
    away_team: str,
) -> dict[str, object]:
    favourite = _favourite_outcome(matrix)
    evaluations = _fill_strategic_fields(
        evaluations,
        favourite_outcome=favourite,
        home_team=home_team,
        away_team=away_team,
        public_pick_config=public_pick_config,
        superbru=superbru,
    )

    tie = superbru.tie_epsilon
    w = superbru.exact_chase_weight
    raw_ev_pick = _top_by(evaluations, lambda c: c.adjusted_expected_points, tie)
    modal_score_pick = max(evaluations, key=lambda c: (c.p_exact, -(c.home_goals + c.away_goals)))
    conservative_pick = max(evaluations, key=lambda c: (c.p_outcome, c.expected_points, c.p_close))
    exact_chase_pick = _top_by(evaluations, lambda c: c.expected_points + (w - 1.0) * 3.0 * c.p_exact, tie)
    risk_adjusted_pick = _top_by(evaluations, lambda c: c.risk_adjusted_score, tie)
    contrarian_pick = _contrarian_pick(evaluations, raw_ev_pick)
    private_chase_pick = _private_chase_pick(evaluations, raw_ev_pick, superbru)

    picks = {
        "raw_ev": raw_ev_pick,
        "conservative": conservative_pick,
        "exact_chase": exact_chase_pick,
        "contrarian": contrarian_pick,
        "risk_adjusted": risk_adjusted_pick,
        "private_chase": private_chase_pick,
    }
    mode = superbru.strategy_mode if superbru.strategy_mode in picks else "raw_ev"
    return {
        "evaluations": evaluations,
        "recommended": picks[mode],
        "raw_ev": raw_ev_pick,
        "modal_score": modal_score_pick,
        "conservative": conservative_pick,
        "exact_chase": exact_chase_pick,
        "contrarian": contrarian_pick,
        "risk_adjusted": risk_adjusted_pick,
        "private_chase": private_chase_pick,
        "mode": mode,
    }


def _top_by(evaluations, score, tie_epsilon: float) -> CandidateEvaluation:
    """Maximise ``score``; ties within ``tie_epsilon`` broken by close prob, then
    exact prob, then preferring the lower-total scoreline. With
    score=adjusted_expected_points this is the prior default recommendation, so
    exact_chase (weight 1) and risk_adjusted (zero weights) reduce to it exactly."""
    scored = [(c, score(c)) for c in evaluations]
    best = max(value for _, value in scored)
    contenders = [c for c, value in scored if best - value <= tie_epsilon]
    contenders.sort(key=lambda c: (c.p_close, c.p_exact, -(c.home_goals + c.away_goals)), reverse=True)
    return contenders[0]


def _contrarian_pick(evaluations: list[CandidateEvaluation], raw_ev_pick: CandidateEvaluation) -> CandidateEvaluation:
    floor = 0.5 * raw_ev_pick.expected_points
    eligible = [c for c in evaluations if c.expected_points >= floor] or list(evaluations)
    return max(eligible, key=lambda c: (c.ev_vs_field + 0.75 * (1.0 - c.public_pick_share), c.expected_points))


def _private_chase_pick(evaluations: list[CandidateEvaluation], raw_ev_pick: CandidateEvaluation, superbru: SuperbruConfig) -> CandidateEvaluation:
    max_loss = max(0.0, float(superbru.private_chase_max_ev_loss))
    eligible = [c for c in evaluations if raw_ev_pick.expected_points - c.expected_points <= max_loss] or [raw_ev_pick]

    def score(candidate: CandidateEvaluation) -> float:
        ev_loss = raw_ev_pick.expected_points - candidate.expected_points
        exact_gain = candidate.p_exact - raw_ev_pick.p_exact
        public_gain = raw_ev_pick.public_pick_share - candidate.public_pick_share
        return (
            -ev_loss
            + superbru.private_chase_exact_weight * exact_gain
            + superbru.private_chase_differentiation_weight * public_gain
        )

    return max(
        eligible,
        key=lambda c: (
            score(c),
            c.p_exact,
            -c.public_pick_share,
            c.p_close,
            c.expected_points,
            -(c.home_goals + c.away_goals),
        ),
    )


def _favourite_outcome(matrix: np.ndarray) -> dict[str, float]:
    return {
        "home": float(np.tril(matrix, -1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, 1).sum()),
    }


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
        estimates = estimate_public_pick_shares(
            [(c.home_goals, c.away_goals) for c in evaluations],
            favourite_outcome=fav_label,
            home_team=home_team,
            away_team=away_team,
            config=public_pick_config,
        )
        shares = {key: estimate.public_pick_share for key, estimate in estimates.items()}
    else:
        equal = 1.0 / max(len(evaluations), 1)
        shares = {(c.home_goals, c.away_goals): equal for c in evaluations}
    field_ev = sum(shares[(c.home_goals, c.away_goals)] * c.expected_points for c in evaluations)
    return tuple(
        replace(
            c,
            public_pick_share=float(shares[(c.home_goals, c.away_goals)]),
            ev_vs_field=float(c.expected_points - field_ev),
            risk_adjusted_score=float(
                c.adjusted_expected_points
                + superbru.public_pick_weight * (c.expected_points - field_ev)
                + superbru.differentiation_weight * (1.0 - shares[(c.home_goals, c.away_goals)])
                - superbru.risk_aversion * c.p_zero_points
                - superbru.variance_penalty * c.variance_points
            ),
        )
        for c in evaluations
    )


def score_prediction(
    matrix: np.ndarray,
    pred_home: int,
    pred_away: int,
    ci_cutoff: float,
    contrarian: bool = False,
    contrarian_weight: float = 0.0,
) -> CandidateEvaluation:
    p_exact = float(matrix[pred_home, pred_away]) if pred_home < matrix.shape[0] and pred_away < matrix.shape[1] else 0.0
    p_close = 0.0
    p_outcome = 0.0
    p_close_non_exact = 0.0
    p_outcome_only = 0.0
    for home_goals in range(matrix.shape[0]):
        for away_goals in range(matrix.shape[1]):
            prob = float(matrix[home_goals, away_goals])
            if prob <= 0:
                continue
            points = score_actual_prediction(pred_home, pred_away, home_goals, away_goals, ci_cutoff)
            # points == 3.0 is the exact cell, already counted directly in p_exact above.
            if points == 1.5:
                p_close += prob
                p_close_non_exact += prob
            elif points == 1.0:
                p_outcome += prob
                p_outcome_only += prob
    p_close += p_exact
    p_outcome += p_close
    expected = 3.0 * p_exact + 1.5 * p_close_non_exact + 1.0 * p_outcome_only
    p_zero = max(0.0, 1.0 - p_outcome)
    second_moment = 9.0 * p_exact + 2.25 * p_close_non_exact + 1.0 * p_outcome_only
    variance = max(0.0, second_moment - expected**2)
    adjusted = expected
    if contrarian:
        adjusted += contrarian_weight * (p_exact - float(matrix.max()))
    return CandidateEvaluation(
        home_goals=pred_home,
        away_goals=pred_away,
        expected_points=expected,
        adjusted_expected_points=adjusted,
        p_exact=p_exact,
        p_close=p_close,
        p_close_non_exact=p_close_non_exact,
        p_outcome=p_outcome,
        p_outcome_only=p_outcome_only,
        p_zero_points=p_zero,
        variance_points=variance,
        outcome=_outcome(pred_home, pred_away),
    )


def decision_diagnostics(matrix: np.ndarray, evaluations: list[CandidateEvaluation], recommended: CandidateEvaluation, ci_cutoff: float) -> dict[str, float | str]:
    modal_idx = np.unravel_index(np.argmax(matrix), matrix.shape)
    modal_eval = next((c for c in evaluations if (c.home_goals, c.away_goals) == modal_idx), None)
    raw = max(evaluations, key=lambda c: (c.adjusted_expected_points, c.p_close))
    modal_expected = modal_eval.expected_points if modal_eval else None
    return {
        "recommended_scoreline": recommended.scoreline,
        "recommended_expected_points": recommended.expected_points,
        "recommended_adjusted_expected_points": recommended.adjusted_expected_points,
        "recommended_p_exact": recommended.p_exact,
        "recommended_p_close": recommended.p_close,
        "recommended_p_outcome": recommended.p_outcome,
        "recommended_p_zero_points": recommended.p_zero_points,
        "recommended_points_variance": recommended.variance_points,
        "raw_ev_scoreline": raw.scoreline,
        "raw_ev_expected_points": raw.expected_points,
        "modal_scoreline_ev": modal_expected,
        "modal_scoreline_expected_points": modal_expected,
        "modal_scoreline_probability": float(matrix[modal_idx]),
        "ev_gap_recommended_to_modal": (recommended.expected_points - modal_expected) if modal_expected is not None else None,
        "ci_cutoff": ci_cutoff,
    }


def closeness_index(pred_home: int, pred_away: int, actual_home: int, actual_away: int) -> float:
    predicted_goal_difference = pred_home - pred_away
    actual_goal_difference = actual_home - actual_away
    predicted_total_goals = pred_home + pred_away
    actual_total_goals = actual_home + actual_away
    return float(abs(predicted_goal_difference - actual_goal_difference) + abs(predicted_total_goals - actual_total_goals) / 2.0)


def score_actual_prediction(pred_home: int, pred_away: int, actual_home: int, actual_away: int, ci_cutoff: float) -> float:
    if pred_home == actual_home and pred_away == actual_away:
        return 3.0
    if _outcome(pred_home, pred_away) == _outcome(actual_home, actual_away):
        if closeness_index(pred_home, pred_away, actual_home, actual_away) <= ci_cutoff:
            return 1.5
        return 1.0
    return 0.0


def _outcome(home: int, away: int) -> Outcome:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"
