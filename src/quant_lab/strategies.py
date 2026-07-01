from __future__ import annotations

import numpy as np
import pandas as pd

from .features import rolling_zscore


def moving_average_crossover(prices: pd.Series, *, fast: int = 10, slow: int = 30) -> pd.Series:
    clean = pd.to_numeric(prices, errors="coerce")
    fast_ma = clean.rolling(fast).mean()
    slow_ma = clean.rolling(slow).mean()
    return pd.Series(np.where(fast_ma > slow_ma, 1.0, -1.0), index=clean.index).where(slow_ma.notna(), 0.0)


def mean_reversion_zscore(prices: pd.Series, *, window: int = 20, entry_z: float = 1.0, exit_z: float = 0.25) -> pd.Series:
    z = rolling_zscore(prices, window)
    position = 0.0
    out: list[float] = []
    for value in z:
        if np.isnan(value):
            position = 0.0
        elif value <= -entry_z:
            position = 1.0
        elif value >= entry_z:
            position = -1.0
        elif abs(value) <= exit_z:
            position = 0.0
        out.append(position)
    return pd.Series(out, index=prices.index)


def time_series_momentum(prices: pd.Series, *, lookback: int = 20) -> pd.Series:
    clean = pd.to_numeric(prices, errors="coerce")
    momentum = clean.pct_change(lookback)
    return pd.Series(np.where(momentum > 0, 1.0, -1.0), index=clean.index).where(momentum.notna(), 0.0)


def cross_sectional_rank_signal(frame: pd.DataFrame, *, lookback: int = 20, long_quantile: float = 0.8, short_quantile: float = 0.2) -> pd.DataFrame:
    """Long strongest columns and short weakest columns by lookback return."""
    returns = frame.pct_change(lookback)
    ranks = returns.rank(axis=1, pct=True)
    signals = pd.DataFrame(0.0, index=frame.index, columns=frame.columns)
    signals = signals.mask(ranks >= long_quantile, 1.0)
    signals = signals.mask(ranks <= short_quantile, -1.0)
    return signals.fillna(0.0)
