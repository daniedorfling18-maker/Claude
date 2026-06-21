from __future__ import annotations

import numpy as np

from superbru_score_engine.backtest.inference import bootstrap_mean_ci, paired_bootstrap_delta
from superbru_score_engine.backtest.metrics import (
    exact_score_log_loss,
    ranked_probability_score,
    total_goals_crps,
    total_goals_distribution,
)


def test_rps_is_zero_for_a_perfect_forecast() -> None:
    assert ranked_probability_score([0.0, 1.0, 0.0], "draw") == 0.0
    assert ranked_probability_score([1.0, 0.0, 0.0], (2, 0)) == 0.0


def test_rps_known_value_and_orders_near_misses_below_far_misses() -> None:
    # probs=[0.5,0.3,0.2], actual home: (0.5-1)^2 + (0.8-1)^2, /2 = (0.25+0.04)/2.
    assert abs(ranked_probability_score([0.5, 0.3, 0.2], "home") - 0.145) < 1e-9
    # Actual is a draw: mass split to the extremes is penalised more than mass on the draw.
    near = ranked_probability_score([0.15, 0.70, 0.15], "draw")
    far = ranked_probability_score([0.45, 0.10, 0.45], "draw")
    assert near < far


def test_exact_score_log_loss_uses_the_actual_cell_and_penalises_off_grid() -> None:
    matrix = [[0.4, 0.1], [0.2, 0.3]]
    assert abs(exact_score_log_loss(matrix, 1, 1) - (-np.log(0.3))) < 1e-9
    # A scoreline outside the grid collapses to the epsilon floor (large but finite).
    assert exact_score_log_loss(matrix, 5, 0) > 20.0


def test_total_goals_distribution_and_crps() -> None:
    matrix = np.outer([0.5, 0.3, 0.2], [0.5, 0.3, 0.2])  # independent, normalised
    dist = total_goals_distribution(matrix)
    assert abs(sum(dist) - 1.0) < 1e-9
    assert len(dist) == 5  # totals 0..4

    # Degenerate: all mass on total = 2 (cell 1-1).
    point = [[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 0.0]]
    assert total_goals_crps(point, 2) == 0.0
    assert total_goals_crps(point, 3) == 1.0  # CDF jumps to 1 at k=2, indicator at k>=3


def test_bootstrap_mean_ci_brackets_the_mean_and_is_tight_for_constants() -> None:
    constant = bootstrap_mean_ci([2.0, 2.0, 2.0, 2.0], n_boot=500)
    assert constant["mean"] == 2.0 and constant["ci_low"] == 2.0 and constant["ci_high"] == 2.0

    spread = bootstrap_mean_ci([0.0, 1.0, 2.0, 3.0, 4.0], n_boot=2000, seed=1)
    assert spread["ci_low"] <= spread["mean"] <= spread["ci_high"]
    assert spread["n"] == 5


def test_paired_bootstrap_delta_flags_signal_and_noise() -> None:
    rng = np.random.default_rng(0)
    base = rng.normal(0.9, 0.8, size=400)

    # No real difference -> CI straddles zero, not significant.
    noise = paired_bootstrap_delta(base, base.copy(), n_boot=2000)
    assert noise["mean_delta"] == 0.0
    assert noise["significant"] is False

    # A constant +0.5 uplift on every match -> significant, CI strictly positive.
    signal = paired_bootstrap_delta(base + 0.5, base, n_boot=2000)
    assert abs(signal["mean_delta"] - 0.5) < 1e-9
    assert signal["ci_low"] > 0.0
    assert signal["significant"] is True


def test_paired_bootstrap_requires_aligned_series() -> None:
    try:
        paired_bootstrap_delta([1.0, 2.0], [1.0])
    except ValueError:
        return
    raise AssertionError("expected ValueError on misaligned series")
