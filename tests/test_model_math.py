from __future__ import annotations

import numpy as np

from superbru_score_engine.ingest import MarketOdds, MatchOdds, OutcomeOdds
from superbru_score_engine.model.devig import (
    FairOutcomeMarket,
    FairTotalMarket,
    devig_implied_probabilities,
    extract_fair_totals,
    extract_fair_totals_all,
    primary_total_market,
)
from superbru_score_engine.model.dixon_coles import apply_dixon_coles
from superbru_score_engine.model.odds_to_scoreline import scoreline_distribution_diagnostics
from superbru_score_engine.model.poisson import (
    independent_poisson_matrix,
    matrix_over_probability,
    outcome_probabilities,
    solve_lambdas,
)


def _totals_match() -> MatchOdds:
    """A match quoting three over/under lines (1.5 / 2.5 / 3.5), 2.5 best-booked."""
    return MatchOdds(
        match_id="m-totals",
        commence_time="2026-06-16T18:00:00Z",
        home_team="Spain",
        away_team="Croatia",
        markets={
            "totals": (
                MarketOdds(
                    key="totals",
                    bookmaker="book-a",
                    outcomes=(
                        OutcomeOdds("Over", 1.40, point=1.5),
                        OutcomeOdds("Under", 2.95, point=1.5),
                        OutcomeOdds("Over", 1.95, point=2.5),
                        OutcomeOdds("Under", 1.90, point=2.5),
                        OutcomeOdds("Over", 3.10, point=3.5),
                        OutcomeOdds("Under", 1.38, point=3.5),
                    ),
                ),
                MarketOdds(
                    key="totals",
                    bookmaker="book-b",
                    outcomes=(
                        OutcomeOdds("Over", 1.97, point=2.5),
                        OutcomeOdds("Under", 1.88, point=2.5),
                    ),
                ),
            )
        },
    )


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


def test_extract_fair_totals_all_returns_every_line() -> None:
    markets = extract_fair_totals_all(_totals_match())
    lines = [market.line for market in markets]
    assert lines == [1.5, 2.5, 3.5]  # sorted ascending, one per distinct point
    for market in markets:
        assert np.isclose(market.over + market.under, 1.0)
        assert 0.0 < market.over < 1.0
    # 2.5 is quoted by two books, so it is both the best-booked and the primary line.
    by_line = {market.line: market for market in markets}
    assert by_line[2.5].count == 2
    primary = primary_total_market(markets)
    assert primary is not None and primary.line == 2.5
    assert extract_fair_totals(_totals_match()).line == 2.5


def test_single_line_via_fair_totals_matches_legacy_fair_total() -> None:
    # Budget normalisation means one line through `fair_totals` must reproduce the
    # original single-line fit exactly, so wiring multi-line in changes nothing
    # for matches that quote only one line.
    fair_1x2 = FairOutcomeMarket(home=0.50, draw=0.27, away=0.23)
    line = FairTotalMarket(line=2.5, over=0.52, under=0.48, count=3)
    legacy = solve_lambdas(fair_1x2=fair_1x2, fair_total=line, solver_grid_goals=12, dixon_coles_rho=-0.08)
    multi = solve_lambdas(
        fair_1x2=fair_1x2, fair_total=None, fair_totals=[line], solver_grid_goals=12, dixon_coles_rho=-0.08
    )
    assert abs(legacy[0] - multi[0]) < 1e-6
    assert abs(legacy[1] - multi[1]) < 1e-6
    assert multi[2]["solver_total_lines_used"] == 1


def test_solve_lambdas_fits_multiple_total_lines() -> None:
    # Generate internally consistent targets from known lambdas, then check the solver
    # recovers the over-probability at every line simultaneously.
    rho = -0.08
    target = apply_dixon_coles(independent_poisson_matrix(1.7, 1.0, 14), 1.7, 1.0, rho)
    outcomes = outcome_probabilities(target)
    fair_1x2 = FairOutcomeMarket(home=float(outcomes[0]), draw=float(outcomes[1]), away=float(outcomes[2]))
    lines = []
    for line in (1.5, 2.5, 3.5):
        over = float(matrix_over_probability(target, line))
        lines.append(FairTotalMarket(line=line, over=over, under=1.0 - over, count=5))

    lambda_home, lambda_away, diagnostics = solve_lambdas(
        fair_1x2=fair_1x2,
        fair_total=None,
        fair_totals=lines,
        solver_grid_goals=14,
        dixon_coles_rho=rho,
    )

    assert diagnostics["solver_total_lines_used"] == 3
    assert diagnostics["solver_total_lines"] == "1.5,2.5,3.5"
    fitted = apply_dixon_coles(independent_poisson_matrix(lambda_home, lambda_away, 14), lambda_home, lambda_away, rho)
    for line in (1.5, 2.5, 3.5):
        assert abs(matrix_over_probability(fitted, line) - matrix_over_probability(target, line)) < 0.01
    assert diagnostics["solver_total_over_rmse"] < 0.01
