from __future__ import annotations

from dataclasses import asdict, dataclass
from math import sqrt
from statistics import NormalDist
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PerformanceReport:
    observations: int
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    hit_rate: float
    profit_factor: float
    average_return: float

    def to_dict(self) -> dict[str, float | int]:
        return asdict(self)


def to_returns(values: Iterable[float] | pd.Series) -> pd.Series:
    series = pd.Series(values, dtype=float).dropna()
    return series if series.empty else series.astype(float)


def equity_curve(returns: Iterable[float] | pd.Series, *, initial_equity: float = 1.0) -> pd.Series:
    returns_series = to_returns(returns)
    return initial_equity * (1.0 + returns_series).cumprod()


def drawdown_series(equity: Iterable[float] | pd.Series) -> pd.Series:
    curve = pd.Series(equity, dtype=float).dropna()
    if curve.empty:
        return pd.Series(dtype=float)
    peak = curve.cummax()
    return curve / peak - 1.0


def max_drawdown_from_returns(returns: Iterable[float] | pd.Series) -> float:
    curve = equity_curve(returns)
    drawdowns = drawdown_series(curve)
    return float(drawdowns.min()) if not drawdowns.empty else 0.0


def historical_var(returns: Iterable[float] | pd.Series, *, confidence: float = 0.95) -> float:
    """Historical one-period VaR as a positive loss number."""
    values = np.asarray(to_returns(returns), dtype=float)
    if values.size == 0:
        return 0.0
    tail = np.quantile(values, 1.0 - confidence)
    return float(max(0.0, -tail))


def conditional_var(returns: Iterable[float] | pd.Series, *, confidence: float = 0.95) -> float:
    """Historical CVaR/expected shortfall as a positive loss number."""
    values = np.asarray(to_returns(returns), dtype=float)
    if values.size == 0:
        return 0.0
    cutoff = np.quantile(values, 1.0 - confidence)
    tail = values[values <= cutoff]
    return float(max(0.0, -tail.mean())) if tail.size else 0.0


def parametric_var(returns: Iterable[float] | pd.Series, *, confidence: float = 0.95) -> float:
    values = np.asarray(to_returns(returns), dtype=float)
    if values.size == 0:
        return 0.0
    mean = float(values.mean())
    std = float(values.std(ddof=1)) if values.size > 1 else 0.0
    quantile = NormalDist(mu=mean, sigma=max(std, 1e-12)).inv_cdf(1.0 - confidence)
    return float(max(0.0, -quantile))


def profit_factor(returns: Iterable[float] | pd.Series) -> float:
    values = np.asarray(to_returns(returns), dtype=float)
    gains = values[values > 0].sum()
    losses = -values[values < 0].sum()
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def performance_report(
    returns: Iterable[float] | pd.Series,
    *,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> PerformanceReport:
    values = np.asarray(to_returns(returns), dtype=float)
    n = int(values.size)
    if n == 0:
        return PerformanceReport(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    total_return = float(np.prod(1.0 + values) - 1.0)
    annualized_return = float((1.0 + total_return) ** (periods_per_year / n) - 1.0) if total_return > -1.0 else -1.0
    excess = values - risk_free_rate / periods_per_year
    vol = float(values.std(ddof=1) * sqrt(periods_per_year)) if n > 1 else 0.0
    excess_std = float(excess.std(ddof=1)) if n > 1 else 0.0
    downside = values[values < 0]
    downside_std = float(downside.std(ddof=1)) if downside.size > 1 else 0.0
    sharpe = float(excess.mean() / excess_std * sqrt(periods_per_year)) if excess_std > 0 else 0.0
    sortino = float(excess.mean() / downside_std * sqrt(periods_per_year)) if downside_std > 0 else 0.0
    hit_rate = float((values > 0).mean())
    return PerformanceReport(
        observations=n,
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_drawdown_from_returns(values),
        hit_rate=hit_rate,
        profit_factor=profit_factor(values),
        average_return=float(values.mean()),
    )
