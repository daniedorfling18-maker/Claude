from __future__ import annotations

import numpy as np

from superbru_score_engine.model.devig import devig_implied_probabilities
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.odds_to_scoreline import scoreline_distribution_diagnostics
from superbru_score_engine.model.poisson import independent_poisson_matrix, outcome_probabilities


def test_independent_poisson_matrix_normalises() -> None:
    matrix = independent_poisson_matrix(1.4, 1.1, 8)
    assert matrix.shape == (9, 9)
    assert np.isclose(matrix.sum(), 1.0)
    assert np.isclose(outcome_probabilities(matrix).sum(), 1.0)


def test_dixon_coles_keeps_matrix_normalised_and_changes_low_scores() -> None:
    matrix = independent_poisson_matrix(1.2, 1.0, 8)
    adjusted = apply_dixon_coles(matrix, 1.2, 1.0, -0.08)
    assert np.isclose(adjusted.sum(), 1.0)
    assert not np.isclose(adjusted[0, 0], matrix[0, 0])
    assert not np.isclose(adjusted[1, 1], matrix[1, 1])


def test_devig_methods_return_valid_probabilities() -> None:
    implied = np.array([0.55, 0.30, 0.25])
    for method in ("multiplicative", "additive", "power"):
        fair = devig_implied_probabilities(implied, method)
        assert np.isclose(fair.sum(), 1.0)
        assert np.all(fair > 0)


def test_scoreline_distribution_diagnostics_are_present() -> None:
    matrix = independent_poisson_matrix(1.5, 0.9, 8)
    diagnostics = scoreline_distribution_diagnostics(matrix, candidate_grid_goals=6)
    assert diagnostics["modal_scoreline"]
    assert diagnostics["total_goals_mean"] > 0
    assert diagnostics["probability_mass_inside_candidate_grid"] <= 1.0
    assert diagnostics["probability_mass_outside_candidate_grid"] >= 0.0
    assert "p_score_0_0" in diagnostics
