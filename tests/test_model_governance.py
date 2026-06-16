from __future__ import annotations

import numpy as np

from superbru_score_engine.config import ModelConfig, RatingsConfig
from superbru_score_engine.ingest import MatchOdds, MarketOdds, OutcomeOdds
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.devig import FairOutcomeMarket
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.poisson import (
    independent_poisson_matrix,
    matrix_over_probability,
    outcome_probabilities,
    solve_lambdas,
)
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


def test_build_distribution_fits_multiple_total_lines_end_to_end() -> None:
    # Price a fair market (no vig) from a known distribution so the solver can recover
    # it, quoting three over/under lines plus the matching 1X2.
    rho = -0.08
    target = apply_dixon_coles(independent_poisson_matrix(1.6, 0.9, 14), 1.6, 0.9, rho)
    home, draw, away = (float(value) for value in outcome_probabilities(target))
    totals_outcomes = []
    for line in (1.5, 2.5, 3.5):
        over = float(matrix_over_probability(target, line))
        totals_outcomes.append(OutcomeOdds("Over", 1.0 / over, point=line))
        totals_outcomes.append(OutcomeOdds("Under", 1.0 / (1.0 - over), point=line))

    match = MatchOdds(
        match_id="m-multiline",
        commence_time="2026-06-16T18:00:00Z",
        home_team="Argentina",
        away_team="Nigeria",
        markets={
            "h2h": (
                MarketOdds(
                    key="h2h",
                    bookmaker="unit-test",
                    outcomes=(
                        OutcomeOdds("Argentina", 1.0 / home),
                        OutcomeOdds("Draw", 1.0 / draw),
                        OutcomeOdds("Nigeria", 1.0 / away),
                    ),
                ),
            ),
            "totals": (
                MarketOdds(key="totals", bookmaker="unit-test", outcomes=tuple(totals_outcomes)),
            ),
        },
    )

    model_config = ModelConfig(odds_weight=1.0, ratings_weight=0.0, dixon_coles_rho=rho)
    ratings = RatingsStore(config=RatingsConfig(use_as_fallback_only=True))
    distribution = OddsToScorelineModel(model_config, ratings).build_distribution(match)

    assert distribution.diagnostics["fair_total_lines_count"] == 3
    assert distribution.diagnostics["fair_total_lines"] == "1.5,2.5,3.5"
    assert distribution.diagnostics["solver_total_lines_used"] == 3
    assert len(distribution.fair_totals) == 3
    assert distribution.fair_total is not None and distribution.fair_total.line == 2.5
    # All three lines are matched simultaneously to within a tight tolerance.
    assert distribution.diagnostics["model_over_rmse_across_lines"] < 0.01
