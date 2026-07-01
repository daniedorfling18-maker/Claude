from __future__ import annotations

import numpy as np
from pytest import approx

from quant_lab.foundations import (
    autocorrelation,
    black_scholes_price,
    geometric_brownian_motion,
    monte_carlo_european_option,
    random_walk,
    sample_bernoulli,
    sample_normal,
    sample_poisson,
)


def test_normal_and_discrete_sampling_moments_are_reasonable():
    normal = sample_normal(50_000, mean=2.0, std=3.0, seed=7)
    bernoulli = sample_bernoulli(50_000, p=0.3, seed=7)
    poisson = sample_poisson(50_000, lam=4.0, seed=7)

    assert normal.mean() == approx(2.0, abs=0.05)
    assert normal.std(ddof=1) == approx(3.0, abs=0.05)
    assert bernoulli.mean() == approx(0.3, abs=0.01)
    assert poisson.mean() == approx(4.0, abs=0.05)


def test_random_walk_and_gbm_shapes_and_positive_prices():
    walk = random_walk(steps=100, start=10.0, drift=0.1, volatility=0.2, seed=11)
    paths = geometric_brownian_motion(s0=100, mu=0.05, sigma=0.2, years=1, steps=252, paths=20, seed=11)

    assert walk.shape == (100,)
    assert paths.shape == (20, 253)
    assert np.all(paths > 0)
    assert np.all(paths[:, 0] == 100)


def test_monte_carlo_option_price_converges_toward_black_scholes():
    mc = monte_carlo_european_option(s0=100, strike=100, rate=0.03, sigma=0.2, years=1, paths=80_000, seed=13)
    bs = black_scholes_price(s0=100, strike=100, rate=0.03, sigma=0.2, years=1)

    assert mc.price == approx(bs, abs=3 * mc.standard_error + 0.15)
    assert mc.standard_error > 0


def test_autocorrelation_detects_persistence():
    series = np.arange(100, dtype=float)
    assert autocorrelation(series, lag=1) > 0.95
