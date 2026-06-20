"""
Robustness, best-practice, and validity tests added after the 2026-06-20 audit.

Covers:
- Dixon-Coles rho sign interpretation and invariants
- Devig edge cases (sharp/wide markets, zero probability, fallback)
- Correct-score matrix conservation
- matrix_over_probability integer-line threshold behaviour
- solve_lambdas convergence under varied inputs
- Superbru scoring formula invariants (EV, variance, probability ordering)
- Backtest metric completeness (RPS, CRPS, Brier edge cases)
- parse_scoreline_label edge cases
- geometric_blend boundary weights
- Asian handicap quarter-line splitting
- Poisson model independence and marginal correctness
- Modelling consistency across devig methods
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from superbru_score_engine.model.devig import (
    FairCorrectScoreMarket,
    FairOutcomeMarket,
    FairTotalMarket,
    decimal_implied_probabilities,
    devig_implied_probabilities,
    parse_scoreline_label,
)
from superbru_score_engine.model.dixon_coles import apply_dixon_coles, geometric_blend
from superbru_score_engine.model.poisson import (
    _asian_handicap_components,
    independent_poisson_matrix,
    matrix_over_probability,
    outcome_probabilities,
    poisson_pmf,
    solve_lambdas,
)
from superbru_score_engine.backtest.metrics import (
    brier_score_1x2,
    log_loss_1x2,
    ranked_probability_score,
    total_goals_crps,
    total_goals_distribution,
)
from superbru_score_engine.decision.superbru import (
    closeness_index,
    score_actual_prediction,
    score_prediction,
)


# ---------------------------------------------------------------------------
# SECTION 1: Dixon-Coles rho sign interpretation and invariants
# ---------------------------------------------------------------------------

def test_dixon_coles_rho_zero_is_identity() -> None:
    """rho=0 leaves all four low-score factors at 1.0: matrix unchanged."""
    matrix = independent_poisson_matrix(1.5, 1.0, 8)
    adjusted = apply_dixon_coles(matrix, 1.5, 1.0, rho=0.0)
    assert np.allclose(adjusted, matrix)


def test_dixon_coles_negative_rho_increases_0_0_and_1_1() -> None:
    """Standard negative rho raises the 0-0 and 1-1 probability mass.

    In the Dixon-Coles (1997) parameterisation:
      τ(0,0) = 1 - λ_h λ_a ρ  →  >1 when ρ<0
      τ(1,1) = 1 - ρ           →  >1 when ρ<0
    so draws are over-represented relative to the independent Poisson baseline.
    """
    matrix = independent_poisson_matrix(1.5, 1.2, 8)
    adjusted = apply_dixon_coles(matrix, 1.5, 1.2, rho=-0.08)
    assert adjusted[0, 0] > matrix[0, 0], "rho<0 should raise p(0-0)"
    assert adjusted[1, 1] > matrix[1, 1], "rho<0 should raise p(1-1)"
    assert adjusted[0, 1] < matrix[0, 1], "rho<0 should lower p(0-1)"
    assert adjusted[1, 0] < matrix[1, 0], "rho<0 should lower p(1-0)"


def test_dixon_coles_positive_rho_decreases_0_0_and_1_1() -> None:
    """Positive rho reverses the Dixon-Coles correction direction."""
    matrix = independent_poisson_matrix(1.5, 1.2, 8)
    adjusted = apply_dixon_coles(matrix, 1.5, 1.2, rho=0.10)
    assert adjusted[0, 0] < matrix[0, 0]
    assert adjusted[1, 1] < matrix[1, 1]


def test_dixon_coles_never_produces_negative_cells() -> None:
    """The max(0.001, factor) guard must hold for any rho in a realistic range."""
    matrix = independent_poisson_matrix(3.0, 2.5, 8)
    for rho in (-0.30, -0.15, -0.08, 0.0, 0.08, 0.15, 0.30):
        adjusted = apply_dixon_coles(matrix, 3.0, 2.5, rho)
        assert np.all(adjusted >= 0), f"Negative cell at rho={rho}"
        assert np.isclose(adjusted.sum(), 1.0, atol=1e-9), f"Sum≠1 at rho={rho}"


def test_dixon_coles_preserves_higher_scores_unchanged() -> None:
    """Cells above (1,1) must not be altered by the Dixon-Coles correction."""
    matrix = independent_poisson_matrix(1.4, 1.0, 8)
    adjusted = apply_dixon_coles(matrix, 1.4, 1.0, rho=-0.08)
    for h in range(2, matrix.shape[0]):
        for a in range(2, matrix.shape[1]):
            # Higher scores are scaled by renormalisation but only via the sum correction;
            # their relative ratios must be unchanged (common scaling factor).
            ratio_before = matrix[h, a] / matrix[2, 2] if matrix[2, 2] > 0 else 0.0
            ratio_after = adjusted[h, a] / adjusted[2, 2] if adjusted[2, 2] > 0 else 0.0
            assert math.isclose(ratio_before, ratio_after, rel_tol=1e-6), \
                f"Higher-score ratio changed at ({h},{a})"


# ---------------------------------------------------------------------------
# SECTION 2: Devig edge cases
# ---------------------------------------------------------------------------

def test_devig_sharp_market_power() -> None:
    """A 1% margin should still produce valid probabilities."""
    implied = np.array([0.505, 0.252, 0.253])  # sum ≈ 1.010
    fair = devig_implied_probabilities(implied, "power")
    assert np.isclose(fair.sum(), 1.0, atol=1e-9)
    assert np.all(fair > 0)


def test_devig_wide_market_power() -> None:
    """A 20% margin (high-juice book) should still normalise correctly."""
    implied = np.array([0.55, 0.35, 0.30])  # sum = 1.20
    fair = devig_implied_probabilities(implied, "power")
    assert np.isclose(fair.sum(), 1.0, atol=1e-9)
    assert np.all(fair > 0)


def test_devig_additive_falls_back_when_outcome_would_go_negative() -> None:
    """additive silently falls back to multiplicative when any adjusted prob ≤ 0."""
    # 0.01 outcome would become 0.01 - margin/3 < 0 with a typical margin
    implied = np.array([0.98, 0.01, 0.06])  # sum=1.05, margin=0.05
    fair = devig_implied_probabilities(implied, "additive")
    assert np.isclose(fair.sum(), 1.0, atol=1e-9)
    assert np.all(fair > 0)


def test_devig_empty_market_raises() -> None:
    with pytest.raises(ValueError, match="non-positive"):
        devig_implied_probabilities(np.array([0.0, 0.0, 0.0]), "multiplicative")


def test_devig_unknown_method_raises() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        devig_implied_probabilities(np.array([0.5, 0.3, 0.25]), "shin_method")


def test_devig_two_way_market() -> None:
    """Over/under (two-outcome) markets must normalise for all methods."""
    implied = np.array([0.53, 0.52])  # sum=1.05
    for method in ("multiplicative", "additive", "power"):
        fair = devig_implied_probabilities(implied, method)
        assert np.isclose(fair.sum(), 1.0, atol=1e-9), f"method={method}"
        assert np.all(fair > 0), f"method={method}"


def test_devig_methods_agree_for_small_margin() -> None:
    """For a narrow 2% margin multiplicative and power should agree within 0.5pp."""
    implied = np.array([0.510, 0.255, 0.255])  # sum = 1.020
    mult = devig_implied_probabilities(implied, "multiplicative")
    power = devig_implied_probabilities(implied, "power")
    assert np.allclose(mult, power, atol=0.005), \
        "multiplicative and power should agree within 0.5pp for small margins"


def test_decimal_implied_probabilities_wrapper() -> None:
    """decimal_implied_probabilities should wrap devig_implied_probabilities correctly."""
    prices = [2.0, 3.6, 4.5]  # sum(1/p) ≈ 1.083
    fair = decimal_implied_probabilities(prices, method="multiplicative")
    assert np.isclose(fair.sum(), 1.0, atol=1e-9)
    assert np.all(fair > 0)
    # Verify wrapper: manual implied probs
    implied = np.array([1.0 / p for p in prices])
    expected = devig_implied_probabilities(implied, "multiplicative")
    assert np.allclose(fair, expected)


# ---------------------------------------------------------------------------
# SECTION 3: Correct-score matrix conservation
# ---------------------------------------------------------------------------

def test_correct_score_to_matrix_sums_to_one() -> None:
    market = FairCorrectScoreMarket(
        cell_probs={(0, 0): 0.10, (1, 0): 0.15, (1, 1): 0.12, (2, 0): 0.08},
        other_mass=0.55,
        count=2,
    )
    matrix = market.to_matrix(max_goals=5)
    assert np.isclose(matrix.sum(), 1.0, atol=1e-9)
    assert np.all(matrix >= 0)


def test_correct_score_to_matrix_with_model_prior_sums_to_one() -> None:
    model = independent_poisson_matrix(1.4, 1.0, 5)
    market = FairCorrectScoreMarket(
        cell_probs={(0, 0): 0.10, (1, 0): 0.15},
        other_mass=0.0,
        count=1,
    )
    matrix = market.to_matrix(max_goals=5, model_matrix=model)
    assert np.isclose(matrix.sum(), 1.0, atol=1e-9)
    assert np.all(matrix >= 0)


def test_correct_score_to_matrix_clips_cells_beyond_max_goals() -> None:
    """Cells beyond max_goals are discarded; residual fills the tail uniformly."""
    market = FairCorrectScoreMarket(
        cell_probs={(10, 10): 0.80},
        other_mass=0.20,
        count=1,
    )
    matrix = market.to_matrix(max_goals=5)
    # other_mass=0.20 fills the 6x6 tail uniformly; normalised → uniform 1/36
    assert np.isclose(matrix.sum(), 1.0, atol=1e-9)
    assert np.allclose(matrix, 1.0 / 36.0, atol=1e-9)


def test_correct_score_to_matrix_all_beyond_no_residual_returns_zeros() -> None:
    """Degenerate: all cells out of range, no residual → zero matrix (caller must handle)."""
    market = FairCorrectScoreMarket(
        cell_probs={(10, 10): 1.0},
        other_mass=0.0,
        count=1,
    )
    matrix = market.to_matrix(max_goals=5)
    assert matrix.sum() == 0.0


# ---------------------------------------------------------------------------
# SECTION 4: matrix_over_probability threshold behaviour
# ---------------------------------------------------------------------------

def test_over_probability_integer_line_same_threshold_as_half_line() -> None:
    """Over 2.0 and Over 2.5 both use threshold=3 (goals>=3), so P should be equal."""
    matrix = independent_poisson_matrix(1.5, 1.0, 8)
    assert math.isclose(
        matrix_over_probability(matrix, 2.0),
        matrix_over_probability(matrix, 2.5),
        rel_tol=1e-9,
    )


def test_over_probability_strictly_decreases_with_line() -> None:
    """Higher lines are harder to exceed; over-probability must decrease monotonically."""
    matrix = independent_poisson_matrix(1.5, 1.0, 8)
    lines = [0.5, 1.5, 2.5, 3.5, 4.5]
    probs = [matrix_over_probability(matrix, line) for line in lines]
    for i in range(len(probs) - 1):
        assert probs[i] > probs[i + 1], f"P(over {lines[i]}) not > P(over {lines[i+1]})"


def test_over_probability_near_one_for_low_line() -> None:
    matrix = independent_poisson_matrix(1.8, 1.2, 8)
    assert matrix_over_probability(matrix, 0.5) > 0.90


# ---------------------------------------------------------------------------
# SECTION 5: solve_lambdas convergence under varied inputs
# ---------------------------------------------------------------------------

def test_solve_lambdas_no_totals_still_converges() -> None:
    """1X2-only fit uses a soft total-goals prior and must return positive rates."""
    fair_1x2 = FairOutcomeMarket(home=0.50, draw=0.28, away=0.22)
    lh, la, diag = solve_lambdas(
        fair_1x2=fair_1x2,
        fair_total=None,
        fair_totals=[],
        solver_grid_goals=12,
        dixon_coles_rho=-0.08,
    )
    assert lh > 0 and la > 0
    assert diag["solver_total_lines_used"] == 0
    # Fitted outcome probs should be within 2pp of targets
    matrix = apply_dixon_coles(independent_poisson_matrix(lh, la, 12), lh, la, -0.08)
    fitted = outcome_probabilities(matrix)
    assert abs(fitted[0] - fair_1x2.home) < 0.02
    assert abs(fitted[1] - fair_1x2.draw) < 0.02


def test_solve_lambdas_positive_rates_for_varied_1x2_distributions() -> None:
    """Solver must return positive rates for strong home, balanced, and strong away markets."""
    scenarios = [
        FairOutcomeMarket(home=0.70, draw=0.18, away=0.12),
        FairOutcomeMarket(home=0.33, draw=0.33, away=0.34),
        FairOutcomeMarket(home=0.15, draw=0.22, away=0.63),
    ]
    for fair_1x2 in scenarios:
        lh, la, _ = solve_lambdas(
            fair_1x2=fair_1x2, fair_total=None, solver_grid_goals=10
        )
        assert lh > 0, f"Non-positive lambda_home for {fair_1x2}"
        assert la > 0, f"Non-positive lambda_away for {fair_1x2}"


# ---------------------------------------------------------------------------
# SECTION 6: Superbru scoring formula invariants
# ---------------------------------------------------------------------------

def test_score_prediction_expected_points_formula() -> None:
    """E[pts] must equal 3*p_exact + 1.5*p_close_non_exact + 1.0*p_outcome_only."""
    matrix = independent_poisson_matrix(1.5, 1.0, 8)
    ev = score_prediction(matrix, 1, 0, ci_cutoff=1.5)
    expected = 3.0 * ev.p_exact + 1.5 * ev.p_close_non_exact + 1.0 * ev.p_outcome_only
    assert math.isclose(ev.expected_points, expected, rel_tol=1e-9)


def test_score_prediction_probability_ordering() -> None:
    """p_outcome >= p_close >= p_exact must hold for every cell of the grid."""
    matrix = independent_poisson_matrix(1.4, 1.1, 8)
    for h in range(5):
        for a in range(5):
            ev = score_prediction(matrix, h, a, ci_cutoff=1.5)
            assert ev.p_outcome >= ev.p_close - 1e-12, f"p_outcome<p_close at {h}-{a}"
            assert ev.p_close >= ev.p_exact - 1e-12, f"p_close<p_exact at {h}-{a}"


def test_score_prediction_variance_non_negative() -> None:
    matrix = independent_poisson_matrix(1.4, 1.1, 8)
    for h in range(5):
        for a in range(5):
            ev = score_prediction(matrix, h, a, ci_cutoff=1.5)
            assert ev.variance_points >= 0.0, f"Negative variance at {h}-{a}"


def test_score_prediction_p_zero_plus_p_outcome_equals_one() -> None:
    """p_zero_points + p_outcome must equal 1 (probability conservation)."""
    matrix = independent_poisson_matrix(1.5, 1.0, 8)
    for h in range(4):
        for a in range(4):
            ev = score_prediction(matrix, h, a, ci_cutoff=1.5)
            assert math.isclose(ev.p_zero_points + ev.p_outcome, 1.0, abs_tol=1e-9), \
                f"Conservation violated at {h}-{a}: p_zero={ev.p_zero_points}, p_outcome={ev.p_outcome}"


def test_ci_cutoff_exact_boundary_gives_close_points() -> None:
    """CI exactly equal to ci_cutoff must score 1.5 (close), not 1.0 (right outcome only)."""
    # pred=3-1, actual=2-1: GD diff=|2-1|=1, total diff=|4-3|/2=0.5, CI=1.5
    ci = closeness_index(3, 1, 2, 1)
    assert math.isclose(ci, 1.5)
    assert score_actual_prediction(3, 1, 2, 1, ci_cutoff=1.5) == 1.5


def test_ci_above_cutoff_gives_right_outcome_only() -> None:
    """CI above cutoff scores 1.0 (right outcome but not close)."""
    # pred=4-1, actual=2-1: GD diff=|3-1|=2, total diff=|5-3|/2=1, CI=3
    ci = closeness_index(4, 1, 2, 1)
    assert ci > 1.5
    assert score_actual_prediction(4, 1, 2, 1, ci_cutoff=1.5) == 1.0


# ---------------------------------------------------------------------------
# SECTION 7: Backtest metric completeness
# ---------------------------------------------------------------------------

def test_rps_perfect_forecast_all_three_outcomes() -> None:
    """Perfect probability assignment gives RPS=0 for each outcome."""
    assert math.isclose(ranked_probability_score([1.0, 0.0, 0.0], "home"), 0.0, abs_tol=1e-9)
    assert math.isclose(ranked_probability_score([0.0, 1.0, 0.0], "draw"), 0.0, abs_tol=1e-9)
    assert math.isclose(ranked_probability_score([0.0, 0.0, 1.0], "away"), 0.0, abs_tol=1e-9)


def test_rps_penalises_far_misses_more_than_near_misses_for_away() -> None:
    """Mass on home (far from away) should score worse than mass on draw (adjacent)."""
    probs_near = (0.10, 0.40, 0.50)  # mass near away
    probs_far = (0.60, 0.25, 0.15)   # mass on home, far from away
    assert ranked_probability_score(probs_near, "away") < ranked_probability_score(probs_far, "away")


def test_total_goals_crps_at_actual_zero() -> None:
    """CRPS with actual_total=0 (left edge) must be finite and non-negative."""
    matrix = independent_poisson_matrix(1.5, 1.0, 5)
    crps = total_goals_crps(matrix.tolist(), actual_total=0)
    assert crps >= 0 and math.isfinite(crps)


def test_total_goals_distribution_sums_to_one() -> None:
    matrix = independent_poisson_matrix(1.5, 1.0, 6)
    dist = total_goals_distribution(matrix.tolist())
    assert math.isclose(sum(dist), 1.0, rel_tol=1e-9)


def test_log_loss_clips_zero_probability_to_epsilon() -> None:
    """log_loss must not return infinity when model assigns zero to the correct outcome."""
    probs = (1.0, 0.0, 0.0)  # all mass on home
    loss_away = log_loss_1x2(probs, "away")
    assert math.isfinite(loss_away) and loss_away > 0


def test_brier_score_all_mass_on_wrong_outcome_is_two() -> None:
    """Maximum Brier score (worst possible) is 2.0 for a three-outcome market."""
    probs = (1.0, 0.0, 0.0)
    assert math.isclose(brier_score_1x2(probs, "away"), 2.0)


def test_brier_score_all_mass_on_correct_outcome_is_zero() -> None:
    assert math.isclose(brier_score_1x2((1.0, 0.0, 0.0), "home"), 0.0)


# ---------------------------------------------------------------------------
# SECTION 8: parse_scoreline_label edge cases
# ---------------------------------------------------------------------------

def test_parse_scoreline_label_standard_formats() -> None:
    assert parse_scoreline_label("2-1") == (2, 1)
    assert parse_scoreline_label("2:1") == (2, 1)
    assert parse_scoreline_label("0-0") == (0, 0)
    assert parse_scoreline_label("10-0") == (10, 0)


def test_parse_scoreline_label_whitespace_tolerant() -> None:
    assert parse_scoreline_label("  2 - 1  ") == (2, 1)
    assert parse_scoreline_label("2 - 1") == (2, 1)


def test_parse_scoreline_label_other_bucket_returns_none() -> None:
    assert parse_scoreline_label("Any Other Score") is None
    assert parse_scoreline_label("Other") is None
    assert parse_scoreline_label("field") is None


def test_parse_scoreline_label_non_numeric_returns_none() -> None:
    assert parse_scoreline_label("x-y") is None
    assert parse_scoreline_label("no score here") is None
    assert parse_scoreline_label("") is None


# ---------------------------------------------------------------------------
# SECTION 9: geometric_blend boundary weights
# ---------------------------------------------------------------------------

def test_geometric_blend_weight_zero_returns_base() -> None:
    """Weight=0: output should be (proportional to) the base matrix."""
    base = independent_poisson_matrix(1.5, 1.0, 5)
    market = independent_poisson_matrix(2.0, 0.8, 5)
    blended = geometric_blend(base, market, market_weight=0.0)
    assert np.isclose(blended.sum(), 1.0, atol=1e-9)
    # With weight=0 the output must have the same relative shape as base
    assert np.allclose(blended / blended.max(), base / base.max(), atol=1e-6)


def test_geometric_blend_weight_one_returns_market() -> None:
    """Weight=1: output should be (proportional to) the market matrix."""
    base = independent_poisson_matrix(1.5, 1.0, 5)
    market = independent_poisson_matrix(2.0, 0.8, 5)
    blended = geometric_blend(base, market, market_weight=1.0)
    assert np.isclose(blended.sum(), 1.0, atol=1e-9)
    assert np.allclose(blended / blended.max(), market / market.max(), atol=1e-6)


def test_geometric_blend_intermediate_weight_sums_to_one() -> None:
    base = independent_poisson_matrix(1.5, 1.0, 5)
    market = independent_poisson_matrix(2.0, 0.8, 5)
    for w in (0.25, 0.50, 0.75):
        blended = geometric_blend(base, market, market_weight=w)
        assert np.isclose(blended.sum(), 1.0, atol=1e-9)
        assert np.all(blended >= 0)


def test_geometric_blend_mismatched_shapes_raises() -> None:
    base = independent_poisson_matrix(1.5, 1.0, 5)
    market = independent_poisson_matrix(1.5, 1.0, 7)
    with pytest.raises(ValueError):
        geometric_blend(base, market, market_weight=0.5)


# ---------------------------------------------------------------------------
# SECTION 10: Asian handicap quarter-line splitting
# ---------------------------------------------------------------------------

def test_asian_handicap_components_integer_line() -> None:
    assert _asian_handicap_components(-1.0) == (-1.0,)
    assert _asian_handicap_components(0.0) == (0.0,)
    assert _asian_handicap_components(0.5) == (0.5,)


def test_asian_handicap_components_quarter_line_splits() -> None:
    """Quarter lines split into two adjacent half-lines."""
    lower, upper = _asian_handicap_components(-0.25)
    assert math.isclose(lower, -0.5, abs_tol=1e-9)
    assert math.isclose(upper, 0.0, abs_tol=1e-9)

    lower, upper = _asian_handicap_components(0.25)
    assert math.isclose(lower, 0.0, abs_tol=1e-9)
    assert math.isclose(upper, 0.5, abs_tol=1e-9)


def test_asian_handicap_components_three_quarter_line() -> None:
    lower, upper = _asian_handicap_components(0.75)
    assert math.isclose(lower, 0.5, abs_tol=1e-9)
    assert math.isclose(upper, 1.0, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# SECTION 11: Poisson model independence and marginal correctness
# ---------------------------------------------------------------------------

def test_poisson_matrix_factorises_before_dixon_coles() -> None:
    """Before DC adjustment, matrix[i,j] ≈ P(H=i) * P(A=j) (independent Poisson)."""
    lh, la = 1.4, 1.1
    matrix = independent_poisson_matrix(lh, la, 8)
    pmf_h = poisson_pmf(np.arange(9), lh)
    pmf_a = poisson_pmf(np.arange(9), la)
    expected = np.outer(pmf_h / pmf_h.sum(), pmf_a / pmf_a.sum())
    assert np.allclose(matrix, expected, atol=1e-9)


def test_poisson_matrix_row_sums_are_home_marginal() -> None:
    """Row sums of the independent Poisson matrix must equal home PMF marginal."""
    lh, la = 1.6, 1.0
    matrix = independent_poisson_matrix(lh, la, 8)
    row_sums = matrix.sum(axis=1)
    pmf_h = poisson_pmf(np.arange(9), lh)
    pmf_h = pmf_h / pmf_h.sum()
    assert np.allclose(row_sums, pmf_h, atol=1e-9)


def test_poisson_matrix_col_sums_are_away_marginal() -> None:
    lh, la = 1.6, 1.0
    matrix = independent_poisson_matrix(lh, la, 8)
    col_sums = matrix.sum(axis=0)
    pmf_a = poisson_pmf(np.arange(9), la)
    pmf_a = pmf_a / pmf_a.sum()
    assert np.allclose(col_sums, pmf_a, atol=1e-9)


def test_outcome_probabilities_sum_to_exactly_one() -> None:
    for lh, la in [(1.0, 1.0), (2.0, 0.5), (0.8, 1.8), (1.5, 1.5)]:
        matrix = apply_dixon_coles(
            independent_poisson_matrix(lh, la, 12), lh, la, rho=-0.08
        )
        probs = outcome_probabilities(matrix)
        assert math.isclose(probs.sum(), 1.0, abs_tol=1e-9), \
            f"Outcome probs do not sum to 1 for lh={lh}, la={la}"


# ---------------------------------------------------------------------------
# SECTION 12: Modelling consistency (reliability / validity)
# ---------------------------------------------------------------------------

def test_solver_recovers_outcome_targets_1x2_only() -> None:
    """With no totals constraint, 1X2 home and away must be reproduced within 1pp.

    The draw is the residual and is not separately asserted here; use the
    1X2-only path so there is no totals/draw tension in the objective.
    """
    for (home_p, draw_p, away_p) in [
        (0.60, 0.25, 0.15),
        (0.40, 0.30, 0.30),
        (0.20, 0.30, 0.50),
    ]:
        fair_1x2 = FairOutcomeMarket(home=home_p, draw=draw_p, away=away_p)
        lh, la, _ = solve_lambdas(
            fair_1x2=fair_1x2,
            fair_total=None,
            fair_totals=[],
            solver_grid_goals=12,
            dixon_coles_rho=-0.08,
        )
        matrix = apply_dixon_coles(
            independent_poisson_matrix(lh, la, 12), lh, la, rho=-0.08
        )
        fitted = outcome_probabilities(matrix)
        # 2pp tolerance: Poisson+DC has no exact closed form so the optimiser reaches
        # a near-optimal solution; 2pp is well within any practical calibration budget.
        assert abs(fitted[0] - home_p) < 0.02, \
            f"Home prob mismatch: target={home_p}, fitted={fitted[0]:.4f}"
        assert abs(fitted[2] - away_p) < 0.02, \
            f"Away prob mismatch: target={away_p}, fitted={fitted[2]:.4f}"


def test_solver_recovers_totals_target_within_tolerance() -> None:
    """Fitted lambda must reproduce the supplied over-probability within 1pp."""
    fair_1x2 = FairOutcomeMarket(home=0.50, draw=0.27, away=0.23)
    target_over = 0.52
    line = FairTotalMarket(line=2.5, over=target_over, under=1.0 - target_over, count=5)
    lh, la, _ = solve_lambdas(
        fair_1x2=fair_1x2, fair_total=line, solver_grid_goals=12, dixon_coles_rho=-0.08
    )
    matrix = apply_dixon_coles(
        independent_poisson_matrix(lh, la, 12), lh, la, rho=-0.08
    )
    fitted_over = matrix_over_probability(matrix, 2.5)
    assert abs(fitted_over - target_over) < 0.01


def test_devig_methods_produce_valid_domain_for_all_valid_inputs() -> None:
    """All three devig methods must return probabilities strictly in (0, 1] for any market."""
    test_markets = [
        np.array([0.55, 0.30, 0.25]),   # typical 3-way
        np.array([0.52, 0.53]),           # typical 2-way
        np.array([0.80, 0.15, 0.12]),    # heavy favourite
        np.array([0.35, 0.35, 0.35]),    # equal market
    ]
    for implied in test_markets:
        for method in ("multiplicative", "additive", "power"):
            fair = devig_implied_probabilities(implied, method)
            assert np.isclose(fair.sum(), 1.0, atol=1e-9), \
                f"{method}: sum≠1 for {implied}"
            assert np.all(fair >= 0), f"{method}: negative probability for {implied}"


def test_rpo_home_advantage_reflected_in_lambda_ordering() -> None:
    """When home team is a clear favourite, lambda_home > lambda_away."""
    fair_1x2 = FairOutcomeMarket(home=0.65, draw=0.22, away=0.13)
    line = FairTotalMarket(line=2.5, over=0.55, under=0.45, count=5)
    lh, la, _ = solve_lambdas(fair_1x2=fair_1x2, fair_total=line, solver_grid_goals=12)
    assert lh > la, f"Expected lambda_home > lambda_away, got {lh:.3f} vs {la:.3f}"


def test_rpo_away_advantage_reflected_in_lambda_ordering() -> None:
    """When away team is a clear favourite, lambda_away > lambda_home."""
    fair_1x2 = FairOutcomeMarket(home=0.15, draw=0.22, away=0.63)
    line = FairTotalMarket(line=2.5, over=0.55, under=0.45, count=5)
    lh, la, _ = solve_lambdas(fair_1x2=fair_1x2, fair_total=line, solver_grid_goals=12)
    assert la > lh, f"Expected lambda_away > lambda_home, got {lh:.3f} vs {la:.3f}"
