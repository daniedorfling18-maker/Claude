from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from superbru_score_engine.config import ModelConfig
from superbru_score_engine.ingest import MatchOdds

from .devig import FairOutcomeMarket, FairTotalMarket, extract_correct_score_matrix, extract_fair_1x2, extract_fair_totals
from .dixon_coles import apply_dixon_coles, geometric_blend
from .poisson import independent_poisson_matrix, outcome_probabilities, over_probability, solve_lambdas
from .ratings import RatingsStore


@dataclass(frozen=True)
class DistributionResult:
    match: MatchOdds
    matrix: np.ndarray
    lambda_home: float
    lambda_away: float
    fair_1x2: FairOutcomeMarket | None
    fair_total: FairTotalMarket | None
    diagnostics: dict[str, float | str | bool]


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

        diagnostics: dict[str, float | str | bool] = {
            "ratings_lambda_home": prior_home,
            "ratings_lambda_away": prior_away,
            "distribution_source": "odds",
            "devig_method": self.config.devig_method,
            "manual_home_advantage_applied": apply_manual_home_advantage,
        }

        if fair_1x2:
            lambda_home, lambda_away, solver_diagnostics = solve_lambdas(
                fair_1x2=fair_1x2,
                fair_total=fair_total,
                solver_grid_goals=self.config.solver_grid_goals,
            )
            diagnostics.update(solver_diagnostics)
            odds_weight = min(1.0, max(0.0, self.config.odds_weight))
            ratings_weight = min(1.0, max(0.0, self.config.ratings_weight))
            total_weight = odds_weight + ratings_weight
            if total_weight > 0:
                odds_weight /= total_weight
                ratings_weight /= total_weight
                lambda_home = odds_weight * lambda_home + ratings_weight * prior_home
                lambda_away = odds_weight * lambda_away + ratings_weight * prior_away
        else:
            lambda_home, lambda_away = prior_home, prior_away
            diagnostics["distribution_source"] = "ratings_prior"

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
                    "model_over": float(over_probability(lambda_home + lambda_away, fair_total.line)),
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
