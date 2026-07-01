from __future__ import annotations

import numpy as np
import pandas as pd


def simple_returns(prices: pd.Series) -> pd.Series:
    return pd.to_numeric(prices, errors="coerce").pct_change()


def log_returns(prices: pd.Series) -> pd.Series:
    clean = pd.to_numeric(prices, errors="coerce")
    return np.log(clean / clean.shift(1))


def rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    mean = values.rolling(window).mean()
    std = values.rolling(window).std(ddof=0).replace(0, np.nan)
    return (values - mean) / std


def rolling_volatility(returns: pd.Series, window: int, *, periods_per_year: int = 252) -> pd.Series:
    return pd.to_numeric(returns, errors="coerce").rolling(window).std(ddof=0) * np.sqrt(periods_per_year)


def rsi(prices: pd.Series, window: int = 14) -> pd.Series:
    delta = pd.to_numeric(prices, errors="coerce").diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)
    avg_gain = gains.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = losses.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def macd(prices: pd.Series, *, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    clean = pd.to_numeric(prices, errors="coerce")
    fast_ema = clean.ewm(span=fast, adjust=False).mean()
    slow_ema = clean.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": macd_line, "macd_signal": signal_line, "macd_hist": macd_line - signal_line}, index=prices.index)


def bollinger_bands(prices: pd.Series, *, window: int = 20, width: float = 2.0) -> pd.DataFrame:
    clean = pd.to_numeric(prices, errors="coerce")
    mid = clean.rolling(window).mean()
    std = clean.rolling(window).std(ddof=0)
    return pd.DataFrame(
        {
            "bb_mid": mid,
            "bb_upper": mid + width * std,
            "bb_lower": mid - width * std,
            "bb_percent": (clean - (mid - width * std)) / (2 * width * std.replace(0, np.nan)),
        },
        index=prices.index,
    )


def technical_feature_frame(prices: pd.Series, *, prefix: str = "") -> pd.DataFrame:
    """Build a compact, point-in-time technical feature frame.

    The returned features are contemporaneous transformations. A trading model
    should shift labels forward and should shift positions by at least one bar
    during backtests.
    """
    clean = pd.to_numeric(prices, errors="coerce")
    ret = simple_returns(clean)
    macd_frame = macd(clean)
    bands = bollinger_bands(clean)
    frame = pd.DataFrame(
        {
            "close": clean,
            "return_1": ret,
            "return_5": clean.pct_change(5),
            "return_20": clean.pct_change(20),
            "volatility_20": rolling_volatility(ret, 20),
            "ma_10": clean.rolling(10).mean(),
            "ma_30": clean.rolling(30).mean(),
            "ma_ratio_10_30": clean.rolling(10).mean() / clean.rolling(30).mean() - 1.0,
            "rsi_14": rsi(clean, 14),
            "zscore_20": rolling_zscore(clean, 20),
        },
        index=clean.index,
    )
    frame = pd.concat([frame, macd_frame, bands], axis=1)
    if prefix:
        frame = frame.rename(columns={column: f"{prefix}{column}" for column in frame.columns})
    return frame
