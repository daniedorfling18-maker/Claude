from __future__ import annotations

import numpy as np

from superbru_score_engine.config import ModelConfig, RatingsConfig
from superbru_score_engine.ingest import MatchOdds, MarketOdds, OutcomeOdds
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.devig import FairOutcomeMarket
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import independent_poisson_matrix, outcome_probabilities, solve_lambdas
from superbru_score_engine.model.ratings import RatingsStore


def _market_match() -> MatchOdds:
    return MatchOdds(
        match_id="m1",
        commence_time="2026-06-16T18:00:00Z",
        home_team="Brazil",
        away_team="Japan",
        markets={
            "h2h": (
                MarketOdds(
                    key="h2h",
                    bookmaker="unit-test",
                    outcomes=(
                        OutcomeOdds("Brazil", 1.85),
                        OutcomeOdds("Draw", 3.40),
                        OutcomeOdds("Japan", 4.60),
                    ),
                ),
            )
        },
    )


def test_market_backed_match_ignores_ratings_when_ratings_are_fallback_only() -> None:
    model_config = ModelConfig(odds_weight=0.5, ratings_weight=0.5, dixon_coles_rho=0.0)
    ratings = RatingsStore(config=RatingsConfig(use_as_fallback_only=True))

    distribution = OddsToScorelineModel(model_config, ratings).build_distribution(_market_match())

    assert distribution.diagnostics["distribution_source"] == "odds"
    assert distribution.diagnostics["ratings_weight_configured"] == 0.5
    assert distribution.diagnostics["ratings_weight_effective"] == 0.0
    assert distribution.diagnostics["odds_weight_effective"] == 1.0
    assert "ratings.use_as_fallback_only" in distribution.diagnostics["ratings_blend_skipped_reason"]


def test_lambda_solver_fits_market_after_dixon_coles_correction() -> None:
    target_matrix = apply_dixon_coles(independent_poisson_matrix(1.55, 0.95, 14), 1.55, 0.95, -0.08)
    target = outcome_probabilities(target_matrix)
    fair = FairOutcomeMarket(home=float(target[0]), draw=float(target[1]), away=float(target[2]))

    lambda_home, lambda_away, diagnostics = solve_lambdas(
        fair_1x2=fair,
        fair_total=None,
        solver_grid_goals=14,
        initial_total_goals=2.50,
        dixon_coles_rho=-0.08,
    )

    fitted_matrix = apply_dixon_coles(independent_poisson_matrix(lambda_home, lambda_away, 14), lambda_home, lambda_away, -0.08)
    fitted = outcome_probabilities(fitted_matrix)

    assert diagnostics["solver_fitted_post_dixon_coles"] is True
    assert np.max(np.abs(fitted - target)) < 0.005
