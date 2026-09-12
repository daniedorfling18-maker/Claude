from __future__ import annotations

import json
import math

import numpy as np

from premium_research.bootstrap import block_length_for, cluster_bootstrap_mean, lower_bound, stationary_block_bootstrap_mean


def _ar1(rng: np.random.Generator, n: int, phi: float, mu: float, sigma: float = 1.0) -> np.ndarray:
    values = np.empty(n)
    values[0] = mu + rng.normal(0.0, sigma / math.sqrt(1.0 - phi * phi))
    for t in range(1, n):
        values[t] = mu + phi * (values[t - 1] - mu) + rng.normal(0.0, sigma)
    return values


def _coverage(method, phi: float, *, trials: int = 200, n: int = 300, draws: int = 2_000, mu: float = 0.5) -> float:
    rng = np.random.default_rng(1)
    hits = 0
    for trial in range(trials):
        sample = _ar1(rng, n, phi, mu)
        lo, hi = method(sample, n_draws=draws, seed=trial)["intervals"]["0.90"]
        hits += int(lo <= mu <= hi)
    return hits / trials


def test_bootstrap_is_deterministic_for_a_seed() -> None:
    values = np.random.default_rng(7).normal(size=350)
    first = json.dumps(cluster_bootstrap_mean(values), sort_keys=True)
    second = json.dumps(cluster_bootstrap_mean(values), sort_keys=True)
    assert first == second
    assert first != json.dumps(cluster_bootstrap_mean(values, seed=1), sort_keys=True)
    block_first = json.dumps(stationary_block_bootstrap_mean(values), sort_keys=True)
    block_second = json.dumps(stationary_block_bootstrap_mean(values), sort_keys=True)
    assert block_first == block_second
    assert block_first != first


def test_block_length_rule_is_the_cube_root_with_a_floor_of_two() -> None:
    assert block_length_for(300) == 7  # 300 ** (1/3) = 6.69
    assert block_length_for(347) == 7  # Lane A weeks
    assert block_length_for(66) == 4  # Lane B windows
    assert block_length_for(1) == 2 and block_length_for(0) == 2


def test_stationary_block_bootstrap_covers_a_known_mean_under_autocorrelation() -> None:
    # AR(1) phi = 0.3, 300 weeks (block length 7 by the rule), 200 trials, 2,000 draws, seed 1: measured 0.865 on 2026-09-12.
    coverage = _coverage(lambda s, **kw: stationary_block_bootstrap_mean(s, expected_block=block_length_for(300), **kw), 0.3)
    assert coverage >= 0.85, coverage


def test_cluster_bootstrap_covers_a_known_mean_when_units_are_independent() -> None:
    # iid weeks: measured 0.885 on 2026-09-12.
    coverage = _coverage(cluster_bootstrap_mean, 0.0)
    assert coverage >= 0.85, coverage


def test_cluster_bootstrap_undercovers_under_autocorrelation_which_is_why_the_gate_takes_the_minimum() -> None:
    # Same AR(1) phi = 0.3 data: the week-cluster interval is too narrow (measured 0.795),
    # the stationary block interval less so (0.865 at block length 7). A too-narrow interval pushes
    # the lower bound UP, the favourable direction, so the gate reads the minimum of the two.
    cluster = _coverage(cluster_bootstrap_mean, 0.3)
    block = _coverage(lambda s, **kw: stationary_block_bootstrap_mean(s, expected_block=block_length_for(300), **kw), 0.3)
    assert cluster < 0.85
    assert block - cluster >= 0.03


def test_non_finite_input_yields_non_finite_intervals_and_lower_bound() -> None:
    result = cluster_bootstrap_mean([float("nan")] * 40)
    assert result["finite"] is False
    assert math.isnan(lower_bound(result, 0.95))
    assert math.isnan(lower_bound(result, 0.90))
    single = cluster_bootstrap_mean([0.1])
    assert single["finite"] is False and math.isnan(lower_bound(single, 0.95))
    block = stationary_block_bootstrap_mean([0.1, float("inf"), 0.2])
    assert block["finite"] is False


def test_intervals_bracket_the_point_and_widen_with_level() -> None:
    values = np.random.default_rng(3).normal(loc=0.2, size=200)
    result = cluster_bootstrap_mean(values)
    lo90, hi90 = result["intervals"]["0.90"]
    lo95, hi95 = result["intervals"]["0.95"]
    assert lo95 <= lo90 <= result["point"] <= hi90 <= hi95
    assert lower_bound(result, 0.95) == lo95
