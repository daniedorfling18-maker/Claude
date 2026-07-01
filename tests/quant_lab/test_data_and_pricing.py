from __future__ import annotations

import pandas as pd
from pytest import approx

from quant_lab.data import data_quality_report, normalize_ohlcv, returns_from_prices
from quant_lab.pricing import black_scholes_greeks, black_scholes_price, crr_binomial_price


def test_ohlcv_normalisation_and_quality_report():
    raw = pd.DataFrame(
        {
            "Open": [10, 11, 12],
            "High": [11, 12, 13],
            "Low": [9, 10, 11],
            "Close": [10.5, 11.5, 12.5],
            "Volume": [100, 120, 130],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-02", "2026-01-03"]),
    )

    frame = normalize_ohlcv(raw)
    report = data_quality_report(frame)

    assert list(frame.columns) == ["open", "high", "low", "close", "adj_close", "volume"]
    assert len(frame) == 2
    assert frame.index.is_monotonic_increasing
    assert report.rows == 2
    assert report.non_positive_close == 0


def test_returns_from_prices_supports_simple_and_log_returns():
    prices = pd.Series([100.0, 105.0, 110.25])

    simple = returns_from_prices(prices)
    log_ret = returns_from_prices(prices, log_returns=True)

    assert simple.tolist() == approx([0.05, 0.05])
    assert len(log_ret) == 2
    assert log_ret.iloc[0] > 0


def test_black_scholes_greeks_and_binomial_price_are_consistent():
    call = black_scholes_price(spot=100, strike=100, rate=0.05, volatility=0.2, years=1, option_type="call")
    put = black_scholes_price(spot=100, strike=100, rate=0.05, volatility=0.2, years=1, option_type="put")
    greeks = black_scholes_greeks(spot=100, strike=100, rate=0.05, volatility=0.2, years=1, option_type="call")
    tree = crr_binomial_price(spot=100, strike=100, rate=0.05, volatility=0.2, years=1, steps=200, option_type="call")

    assert call == approx(10.4506, abs=0.02)
    assert call - put == approx(100 - 100 * 2.718281828459045 ** -0.05, abs=0.02)
    assert greeks.delta == approx(0.6368, abs=0.01)
    assert tree == approx(call, abs=0.05)
