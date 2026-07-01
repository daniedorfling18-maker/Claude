from __future__ import annotations

import numpy as np
import pandas as pd

from quant_lab.backtest import vectorized_backtest
from quant_lab.features import technical_feature_frame
from quant_lab.risk import conditional_var, historical_var, max_drawdown_from_returns, performance_report
from quant_lab.strategies import mean_reversion_zscore, moving_average_crossover, time_series_momentum


def test_technical_features_have_expected_columns_without_leakage_shift():
    prices = pd.Series(np.linspace(100, 130, 80), index=pd.date_range("2026-01-01", periods=80))

    features = technical_feature_frame(prices)

    assert {"return_1", "return_20", "volatility_20", "rsi_14", "macd", "bb_percent"}.issubset(features.columns)
    assert features["return_1"].iloc[0] != features["return_1"].iloc[0]
    assert features["return_20"].dropna().iloc[-1] > 0


def test_classical_strategies_create_signed_signals():
    prices = pd.Series([100, 99, 98, 99, 100, 102, 104, 106, 108, 110] * 8, dtype=float)

    ma = moving_average_crossover(prices, fast=3, slow=5)
    mr = mean_reversion_zscore(prices, window=5, entry_z=0.5, exit_z=0.1)
    mom = time_series_momentum(prices, lookback=3)

    assert set(ma.dropna().unique()).issubset({-1.0, 0.0, 1.0})
    assert mr.abs().max() <= 1.0
    assert mom.iloc[-1] in {-1.0, 0.0, 1.0}


def test_vectorized_backtest_charges_costs_and_uses_lagged_positions():
    prices = pd.Series(np.linspace(100, 120, 60), index=pd.date_range("2026-01-01", periods=60))
    signal = pd.Series(1.0, index=prices.index)

    result = vectorized_backtest(prices, signal, cost_bps=10, slippage_bps=5)

    assert result.positions.iloc[0] == 0.0
    assert result.positions.iloc[1] == 1.0
    assert result.equity_curve.iloc[-1] > result.equity_curve.iloc[0]
    assert result.trades == 1
    assert result.total_cost > 0
    assert result.metrics.total_return > 0


def test_risk_report_var_cvar_and_drawdown():
    returns = pd.Series([0.02, -0.01, 0.015, -0.08, 0.01, -0.02, 0.03])

    report = performance_report(returns)

    assert max_drawdown_from_returns(returns) < 0
    assert historical_var(returns, confidence=0.8) > 0
    assert conditional_var(returns, confidence=0.8) >= historical_var(returns, confidence=0.8)
    assert report.observations == 7
    assert report.profit_factor > 0
