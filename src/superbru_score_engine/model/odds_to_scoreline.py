from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from superbru_score_engine.config import ModelConfig
from superbru_score_engine.ingest import MatchOdds

from .devig import FairOutcomeMarket, FairTotalMarket, extract_correct_score_matrix, extract_fair_1x2, extract_fair_totals
from .dixon_coles import apply_dixon_coles, geometric_blend
from .poisson import independent_poisson_matrix, matrix_over_probability, outcome_probabilities, solve_lambdas
from .ratings import RatingsStore


@dataclass(frozen=True)
class DistributionResult:
    match: MatchOdds
    matrix: np.ndarray
    lambda_home: float
    lambda_away: float
    fair_1x2: FairOutcomeMarket | None
    fair_total: FairTotalMarket | None
    diagnostics: dict[str, object]


class OddsToScorelineModel:
    def __init__(self, config: ModelConfig, ratings: RatingsStore | None = None) -> None:
        self.config = config
        self.ratings = ratings or RatingsStore()

    def build_distribution(self, match: MatchOdds) -> DistributionResult:
        fair_1x2 = extract_fair_1x2(match, self.config.devig_method)
        fair_total = extract_fair_totals(match, self.config.devig_method)
        apply_manual_home_advantage = fair_1x2 is None
        prior_home, prior_away = self.ratings.prior_lambdas(
            match.home_team,
            match.away_team,
            neutral=match.neutral,
            venue_country=match.venue_country,
            host_teams=self.config.host_teams,
            home_advantage_goals=self.config.home_advantage_goals,
            apply_home_advantage=apply_manual_home_advantage,
        )
        ratings_diagnostics = self.ratings.diagnostics()

        diagnostics: dict[str, object] = {
            "ratings_lambda_home": prior_home,
            "ratings_lambda_away": prior_away,
            "distribution_source": "odds",
            "devig_method": self.config.devig_method,
            "calibration_profile": self.config.calibration_profile,
            "dixon_coles_rho": self.config.dixon_coles_rho,
            "manual_home_advantage_applied": apply_manual_home_advantage,
            "ratings_source": ratings_diagnostics.get("source"),
            "ratings_source_url": ratings_diagnostics.get("source_url"),
            "ratings_cutoff_date": ratings_diagnostics.get("cutoff_date"),
            "ratings_update_method": ratings_diagnostics.get("update_method"),
            "ratings_updated_at": ratings_diagnostics.get("updated_at"),
            "ratings_number_of_applied_results": ratings_diagnostics.get("number_of_applied_results"),
            "ratings_number_of_teams": ratings_diagnostics.get("number_of_teams"),
            "ratings_use_as_fallback_only": self.ratings.config.use_as_fallback_only,
        }

        if fair_1x2:
            lambda_home, lambda_away, solver_diagnostics = solve_lambdas(
                fair_1x2=fair_1x2,
                fair_total=fair_total,
                solver_grid_goals=self.config.solver_grid_goals,
                dixon_coles_rho=self.config.dixon_coles_rho,
            )
            diagnostics.update(solver_diagnostics)
            odds_weight = min(1.0, max(0.0, self.config.odds_weight))
            ratings_weight = min(1.0, max(0.0, self.config.ratings_weight))
            diagnostics["odds_weight_configured"] = self.config.odds_weight
            diagnostics["ratings_weight_configured"] = self.config.ratings_weight

            if self.ratings.config.use_as_fallback_only and ratings_weight > 0:
                diagnostics["ratings_blend_skipped_reason"] = "ratings.use_as_fallback_only is true for market-backed match"
                ratings_weight = 0.0
                odds_weight = 1.0

            total_weight = odds_weight + ratings_weight
            if total_weight > 0:
                odds_weight /= total_weight
                ratings_weight /= total_weight
                diagnostics["odds_weight_effective"] = odds_weight
                diagnostics["ratings_weight_effective"] = ratings_weight
                lambda_home = odds_weight * lambda_home + ratings_weight * prior_home
                lambda_away = odds_weight * lambda_away + ratings_weight * prior_away
        else:
            lambda_home, lambda_away = prior_home, prior_away
            diagnostics["distribution_source"] = "ratings_prior"
            diagnostics["ratings_only_warning"] = "No usable 1X2 market; ratings-only score distribution is lower confidence."
            diagnostics["odds_weight_configured"] = self.config.odds_weight
            diagnostics["ratings_weight_configured"] = self.config.ratings_weight
            diagnostics["odds_weight_effective"] = 0.0
            diagnostics["ratings_weight_effective"] = 1.0

        matrix = independent_poisson_matrix(lambda_home, lambda_away, self.config.model_grid_goals)
        matrix = apply_dixon_coles(matrix, lambda_home, lambda_away, self.config.dixon_coles_rho)

        correct_score_matrix = extract_correct_score_matrix(match, self.config.model_grid_goals, self.config.devig_method)
        if correct_score_matrix is not None and self.config.correct_score_blend_weight > 0:
            matrix = geometric_blend(matrix, correct_score_matrix, self.config.correct_score_blend_weight)
            diagnostics["correct_score_blended"] = True
        else:
            diagnostics["correct_score_blended"] = False

        model_outcomes = outcome_probabilities(matrix)
        diagnostics.update(
            {
                "lambda_home": float(lambda_home),
                "lambda_away": float(lambda_away),
                "model_home_win": float(model_outcomes[0]),
                "model_draw": float(model_outcomes[1]),
                "model_away_win": float(model_outcomes[2]),
            }
        )
        diagnostics.update(scoreline_distribution_diagnostics(matrix, self.config.candidate_grid_goals))
        if fair_1x2:
            diagnostics.update(
                {
                    "fair_home_win": float(fair_1x2.home),
                    "fair_draw": float(fair_1x2.draw),
                    "fair_away_win": float(fair_1x2.away),
                }
            )
        if fair_total:
            diagnostics.update(
                {
                    "fair_total_line": float(fair_total.line),
                    "fair_over": float(fair_total.over),
                    "model_over": float(matrix_over_probability(matrix, fair_total.line)),
                }
            )

        return DistributionResult(
            match=match,
            matrix=matrix,
            lambda_home=float(lambda_home),
            lambda_away=float(lambda_away),
            fair_1x2=fair_1x2,
            fair_total=fair_total,
            diagnostics=diagnostics,
        )


def scoreline_distribution_diagnostics(matrix: np.ndarray, candidate_grid_goals: int) -> dict[str, object]:
    max_home, max_away = matrix.shape[0] - 1, matrix.shape[1] - 1
    home_goals = np.arange(matrix.shape[0], dtype=float)
    away_goals = np.arange(matrix.shape[1], dtype=float)
    home_grid = home_goals[:, None]
    away_grid = away_goals[None, :]

    modal_idx = tuple(int(value) for value in np.unravel_index(np.argmax(matrix), matrix.shape))
    total_goals_mean = float(((home_grid + away_grid) * matrix).sum())
    expected_goal_difference = float(((home_grid - away_grid) * matrix).sum())

    candidate_limit = min(candidate_grid_goals, max_home, max_away)
    candidate_mass = float(matrix[: candidate_limit + 1, : candidate_limit + 1].sum())

    diagnostics: dict[str, object] = {
        "total_goals_mean": total_goals_mean,
        "expected_goal_difference": expected_goal_difference,
        "modal_scoreline": f"{modal_idx[0]}-{modal_idx[1]}",
        "modal_scoreline_probability": float(matrix[modal_idx]),
        "top_exact_scoreline": f"{modal_idx[0]}-{modal_idx[1]}",
        "top_exact_scoreline_probability": float(matrix[modal_idx]),
        "score_matrix_max_goals": int(max_home),
        "score_matrix_truncated_and_renormalised": True,
        "probability_mass_inside_model_grid": float(matrix.sum()),
        "probability_mass_outside_model_grid_estimated": None,
        "candidate_grid_goals": int(candidate_grid_goals),
        "probability_mass_inside_candidate_grid": candidate_mass,
        "probability_mass_outside_candidate_grid": max(0.0, 1.0 - candidate_mass),
    }
    for home, away in ((0, 0), (1, 0), (0, 1), (1, 1), (2, 1), (1, 2)):
        diagnostics[f"p_score_{home}_{away}"] = float(matrix[home, away]) if home <= max_home and away <= max_away else 0.0
    return diagnostics
