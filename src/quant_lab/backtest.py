from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .risk import PerformanceReport, performance_report


@dataclass(frozen=True)
class BacktestResult:
    equity_curve: pd.Series
    strategy_returns: pd.Series
    positions: pd.Series
    turnover: pd.Series
    trades: int
    total_cost: float
    metrics: PerformanceReport

    def summary(self) -> dict[str, float | int]:
        return {"trades": self.trades, "total_cost": self.total_cost, **asdict(self.metrics)}


def signals_to_positions(signals: pd.Series, *, max_position: float = 1.0) -> pd.Series:
    """Map arbitrary signed signals into clipped target positions."""
    return pd.to_numeric(signals, errors="coerce").fillna(0.0).clip(-max_position, max_position)


def vectorized_backtest(
    prices: pd.Series,
    signals: pd.Series,
    *,
    initial_equity: float = 1_000.0,
    cost_bps: float = 0.0,
    slippage_bps: float = 0.0,
    max_position: float = 1.0,
    periods_per_year: int = 252,
    execution_lag: int = 1,
) -> BacktestResult:
    """Backtest target-position signals with costs and a default one-bar execution lag.

    The execution lag is deliberate: a signal created at bar t earns returns
    from t+1 onward, preventing accidental lookahead.
    """
    clean_prices = pd.to_numeric(prices, errors="coerce").dropna()
    aligned_signals = pd.to_numeric(signals, errors="coerce").reindex(clean_prices.index).fillna(0.0)
    positions = signals_to_positions(aligned_signals, max_position=max_position).shift(execution_lag).fillna(0.0)
    asset_returns = clean_prices.pct_change().fillna(0.0)
    turnover = positions.diff().abs().fillna(positions.abs())
    total_cost_rate = (cost_bps + slippage_bps) / 10_000.0
    strategy_returns = positions * asset_returns - turnover * total_cost_rate
    equity = initial_equity * (1.0 + strategy_returns).cumprod()
    metrics = performance_report(strategy_returns, periods_per_year=periods_per_year)
    return BacktestResult(
        equity_curve=equity,
        strategy_returns=strategy_returns,
        positions=positions,
        turnover=turnover,
        trades=int((turnover > 0).sum()),
        total_cost=float((turnover * total_cost_rate * initial_equity).sum()),
        metrics=metrics,
    )
