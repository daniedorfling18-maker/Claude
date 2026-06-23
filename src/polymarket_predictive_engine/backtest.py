from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .utils import read_csv_rows, safe_float, write_csv, write_json


def backtest(cfg: EngineConfig) -> dict[str, Any]:
    labels = read_csv_rows(cfg.output_root / "polymarket_training" / "labels.csv")
    signals = read_csv_rows(cfg.output_root / "polymarket_predictions" / "trade_signals.csv")
    label_key = {(r.get("market_id"), r.get("token_id")): int(float(r.get("target", 0))) for r in labels if r.get("horizon") in {"", "all_valid"}}
    trades: list[dict[str, Any]] = []
    bankroll = float(cfg.raw.get("risk", {}).get("bankroll", 1000))
    equity = bankroll
    curve = []
    for s in signals:
        key = (s.get("market_id"), s.get("token_id"))
        if key not in label_key:
            continue
        size = safe_float(s.get("sizing_decision")) or 0.0
        entry = safe_float(s.get("executable_price")) or 0.0
        slippage = float(cfg.raw.get("costs", {}).get("slippage", 0.01))
        cost = min(1.0, entry + slippage)
        exit_price = 1.0 if label_key[key] == 1 else 0.0
        pnl = size * (exit_price - cost)
        equity += pnl
        trade = {"market_id": key[0], "token_id": key[1], "entry_timestamp": s.get("data_snapshot_timestamp", ""), "exit_timestamp": "resolution", "side": s.get("side", "BUY_YES"), "entry_price": cost, "exit_price": exit_price, "size": size, "pnl": pnl, "category": s.get("category", "")}
        trades.append(trade)
        curve.append({"timestamp": trade["entry_timestamp"], "equity": equity, "pnl": pnl})
    total_pnl = sum(safe_float(t.get("pnl")) or 0 for t in trades)
    summary = {"trade_count": len(trades), "total_pnl": total_pnl, "roi": total_pnl / bankroll if bankroll else 0, "approved": bool(trades), "note": "execution-aware backtest uses best ask plus configured slippage and settlement value"}
    out = cfg.output_root / "polymarket_backtests"
    write_csv(out / "backtest_trades.csv", trades)
    write_json(out / "backtest_summary.json", summary)
    write_csv(out / "backtest_equity_curve.csv", curve)
    write_csv(out / "backtest_by_category.csv", [])
    write_csv(out / "backtest_by_edge_bucket.csv", [])
    write_csv(out / "backtest_by_liquidity_bucket.csv", [])
    return summary


def main(config_path: str) -> dict[str, Any]:
    return backtest(load_config(config_path))
