from __future__ import annotations

from dataclasses import dataclass
from math import erf, exp, log, sqrt
from typing import Literal

import numpy as np

OptionType = Literal["call", "put"]


def rng(seed: int | None = None) -> np.random.Generator:
    """Return a NumPy random generator so simulations are reproducible."""
    return np.random.default_rng(seed)


def sample_normal(n: int, mean: float = 0.0, std: float = 1.0, seed: int | None = None) -> np.ndarray:
    """Sample from a normal distribution N(mean, std^2)."""
    return rng(seed).normal(loc=mean, scale=std, size=n)


def sample_lognormal(n: int, mean: float = 0.0, sigma: float = 1.0, seed: int | None = None) -> np.ndarray:
    """Sample from a lognormal distribution where log(X) is normal."""
    return rng(seed).lognormal(mean=mean, sigma=sigma, size=n)


def sample_student_t(n: int, df: float, seed: int | None = None) -> np.ndarray:
    """Sample from Student's t distribution with fat tails controlled by df."""
    return rng(seed).standard_t(df=df, size=n)


def sample_bernoulli(n: int, p: float, seed: int | None = None) -> np.ndarray:
    """Sample Bernoulli outcomes as 0/1 integers."""
    if not 0 <= p <= 1:
        raise ValueError("p must be in [0, 1]")
    return rng(seed).binomial(n=1, p=p, size=n)


def sample_poisson(n: int, lam: float, seed: int | None = None) -> np.ndarray:
    """Sample Poisson event counts with arrival rate lambda."""
    if lam < 0:
        raise ValueError("lam must be non-negative")
    return rng(seed).poisson(lam=lam, size=n)


def random_walk(
    steps: int,
    *,
    start: float = 0.0,
    drift: float = 0.0,
    volatility: float = 1.0,
    seed: int | None = None,
) -> np.ndarray:
    """Simulate an additive random walk: X_t = X_{t-1} + drift + volatility * eps_t."""
    shocks = rng(seed).normal(loc=drift, scale=volatility, size=steps)
    return start + np.cumsum(shocks)


def geometric_brownian_motion(
    *,
    s0: float,
    mu: float,
    sigma: float,
    years: float,
    steps: int,
    paths: int,
    seed: int | None = None,
) -> np.ndarray:
    """Simulate GBM paths under dS/S = mu dt + sigma dW.

    Returns an array with shape ``(paths, steps + 1)`` including the initial price.
    """
    if s0 <= 0 or years <= 0 or steps <= 0 or paths <= 0:
        raise ValueError("s0, years, steps, and paths must be positive")
    dt = years / steps
    shocks = rng(seed).normal(size=(paths, steps))
    increments = (mu - 0.5 * sigma**2) * dt + sigma * sqrt(dt) * shocks
    log_paths = np.cumsum(increments, axis=1)
    prices = s0 * np.exp(log_paths)
    return np.concatenate([np.full((paths, 1), s0), prices], axis=1)


def option_payoff(spot: np.ndarray, strike: float, option_type: OptionType = "call") -> np.ndarray:
    """Vectorized European call/put payoff at expiry."""
    if option_type == "call":
        return np.maximum(spot - strike, 0.0)
    if option_type == "put":
        return np.maximum(strike - spot, 0.0)
    raise ValueError("option_type must be 'call' or 'put'")


@dataclass(frozen=True)
class MonteCarloOptionResult:
    price: float
    standard_error: float
    paths: int
    option_type: OptionType


def monte_carlo_european_option(
    *,
    s0: float,
    strike: float,
    rate: float,
    sigma: float,
    years: float,
    paths: int = 50_000,
    option_type: OptionType = "call",
    seed: int | None = None,
) -> MonteCarloOptionResult:
    """Price a European option with risk-neutral GBM Monte Carlo."""
    terminal = geometric_brownian_motion(
        s0=s0,
        mu=rate,
        sigma=sigma,
        years=years,
        steps=1,
        paths=paths,
        seed=seed,
    )[:, -1]
    discounted = exp(-rate * years) * option_payoff(terminal, strike, option_type)
    return MonteCarloOptionResult(
        price=float(np.mean(discounted)),
        standard_error=float(np.std(discounted, ddof=1) / sqrt(paths)),
        paths=paths,
        option_type=option_type,
    )


def normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def black_scholes_price(
    *,
    s0: float,
    strike: float,
    rate: float,
    sigma: float,
    years: float,
    option_type: OptionType = "call",
) -> float:
    """Closed-form Black-Scholes price for a non-dividend European option."""
    if s0 <= 0 or strike <= 0 or sigma <= 0 or years <= 0:
        raise ValueError("s0, strike, sigma, and years must be positive")
    d1 = (log(s0 / strike) + (rate + 0.5 * sigma**2) * years) / (sigma * sqrt(years))
    d2 = d1 - sigma * sqrt(years)
    if option_type == "call":
        return s0 * normal_cdf(d1) - strike * exp(-rate * years) * normal_cdf(d2)
    if option_type == "put":
        return strike * exp(-rate * years) * normal_cdf(-d2) - s0 * normal_cdf(-d1)
    raise ValueError("option_type must be 'call' or 'put'")


def autocorrelation(series: np.ndarray, lag: int = 1) -> float:
    """Compute sample autocorrelation at a positive lag."""
    values = np.asarray(series, dtype=float)
    if lag <= 0 or lag >= len(values):
        raise ValueError("lag must be between 1 and len(series)-1")
    x = values[:-lag] - values[:-lag].mean()
    y = values[lag:] - values[lag:].mean()
    denom = np.sqrt(np.sum(x**2) * np.sum(y**2))
    return float(np.sum(x * y) / denom) if denom > 0 else 0.0


def adf_stationarity_test(series: np.ndarray) -> dict[str, float | str]:
    """Run an Augmented Dickey-Fuller test using statsmodels when available."""
    try:
        from statsmodels.tsa.stattools import adfuller
    except Exception as exc:  # pragma: no cover - depends on optional local package
        return {"status": "statsmodels_unavailable", "error": f"{type(exc).__name__}: {exc}"}
    statistic, pvalue, usedlag, nobs, critical_values, _ = adfuller(np.asarray(series, dtype=float))
    return {
        "status": "ok",
        "adf_statistic": float(statistic),
        "pvalue": float(pvalue),
        "used_lag": float(usedlag),
        "nobs": float(nobs),
        "critical_1pct": float(critical_values["1%"]),
        "critical_5pct": float(critical_values["5%"]),
        "critical_10pct": float(critical_values["10%"]),
    }


def arima_forecast(series: np.ndarray, *, order: tuple[int, int, int] = (1, 0, 0), steps: int = 1) -> np.ndarray:
    """Fit a small ARIMA model using statsmodels and return point forecasts."""
    try:
        from statsmodels.tsa.arima.model import ARIMA
    except Exception as exc:  # pragma: no cover - depends on optional local package
        raise RuntimeError("statsmodels is required for arima_forecast") from exc
    model = ARIMA(np.asarray(series, dtype=float), order=order)
    fitted = model.fit()
    return np.asarray(fitted.forecast(steps=steps), dtype=float)
