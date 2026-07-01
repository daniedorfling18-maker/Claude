from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


OHLCV_COLUMNS = ["open", "high", "low", "close", "adj_close", "volume"]


@dataclass(frozen=True)
class DataQualityReport:
    rows: int
    start: str
    end: str
    missing_values: int
    duplicate_timestamps: int
    non_positive_close: int
    stale_close_rows: int


def normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize common OHLCV vendor columns into a clean lowercase schema."""
    if frame.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    renamed = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "AdjClose": "adj_close",
        "Volume": "volume",
    }
    out = frame.rename(columns={**renamed, **{k.lower(): k.lower() for k in frame.columns}}).copy()
    if "adj_close" not in out.columns and "close" in out.columns:
        out["adj_close"] = out["close"]
    for column in OHLCV_COLUMNS:
        if column not in out.columns:
            out[column] = 0.0
    out = out[OHLCV_COLUMNS]
    out.index = pd.to_datetime(out.index)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.apply(pd.to_numeric, errors="coerce")


def download_ohlcv_yfinance(
    tickers: str | Iterable[str],
    *,
    start: str | None = None,
    end: str | None = None,
    auto_adjust: bool = False,
) -> dict[str, pd.DataFrame]:
    """Download daily OHLCV data using the free yfinance package when installed.

    This function intentionally has no tests that hit the network.  It is a
    reproducible interface for notebooks and local experiments, while unit tests
    use deterministic frames.
    """
    try:
        import yfinance as yf
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Install yfinance to download free Yahoo Finance data") from exc
    ticker_list = [tickers] if isinstance(tickers, str) else list(tickers)
    result: dict[str, pd.DataFrame] = {}
    for ticker in ticker_list:
        raw = yf.download(ticker, start=start, end=end, auto_adjust=auto_adjust, progress=False)
        result[ticker] = normalize_ohlcv(raw)
    return result


def returns_from_prices(prices: pd.Series, *, log_returns: bool = False) -> pd.Series:
    """Compute simple or log returns from a price series."""
    prices = pd.to_numeric(prices, errors="coerce").dropna()
    if log_returns:
        import numpy as np

        return pd.Series(np.log(prices / prices.shift(1)), index=prices.index).dropna()
    return prices.pct_change().dropna()


def data_quality_report(frame: pd.DataFrame) -> DataQualityReport:
    """Produce a compact quality report for a normalized OHLCV frame."""
    if frame.empty:
        return DataQualityReport(0, "", "", 0, 0, 0, 0)
    close = pd.to_numeric(frame["close"], errors="coerce")
    return DataQualityReport(
        rows=int(len(frame)),
        start=str(frame.index.min()),
        end=str(frame.index.max()),
        missing_values=int(frame.isna().sum().sum()),
        duplicate_timestamps=int(frame.index.duplicated().sum()),
        non_positive_close=int((close <= 0).sum()),
        stale_close_rows=int((close.diff().abs().fillna(0) == 0).sum()),
    )
