from __future__ import annotations

import hashlib
from typing import Any

from ..config import EngineConfig, load_config
from ..utils import now_utc, read_csv_rows, safe_float, write_csv, write_json


def paper_trade(cfg: EngineConfig) -> dict[str, Any]:
    signals = read_csv_rows(cfg.output_root / "polymarket_predictions" / "trade_signals.csv")
    cash = float(cfg.raw.get("paper_trading", {}).get("starting_cash", cfg.raw.get("risk", {}).get("bankroll", 1000)))
    orders = []
    fills = []
    positions: dict[str, dict[str, Any]] = {}
    for s in signals:
        size = safe_float(s.get("sizing_decision")) or 0.0
        price = safe_float(s.get("executable_price")) or 0.0
        if size <= 0 or price <= 0 or cash < size * price:
            continue
        order_id = hashlib.sha256((s.get("market_id", "") + s.get("token_id", "") + now_utc()).encode()).hexdigest()[:16]
        cash -= size * price
        orders.append({"order_id": order_id, "market_id": s.get("market_id", ""), "token_id": s.get("token_id", ""), "side": s.get("side", "BUY_YES"), "size": size, "price": price, "status": "filled_simulated", "timestamp": now_utc()})
        fills.append({"order_id": order_id, "fill_timestamp": now_utc(), "token_id": s.get("token_id", ""), "size": size, "price": price})
        pos = positions.setdefault(s.get("token_id", ""), {"token_id": s.get("token_id", ""), "market_id": s.get("market_id", ""), "category": s.get("category", ""), "quantity": 0.0, "average_entry_price": price, "realized_pnl": 0.0, "unrealized_pnl": 0.0})
        old_qty = pos["quantity"]
        pos["quantity"] += size
        pos["average_entry_price"] = ((pos["average_entry_price"] * old_qty) + size * price) / pos["quantity"]
    out = cfg.output_root / "polymarket_portfolio"
    write_csv(out / "paper_orders.csv", orders)
    write_csv(out / "paper_fills.csv", fills)
    write_csv(out / "positions.csv", positions.values())
    summary = {"mode": "paper", "orders": len(orders), "fills": len(fills), "cash": cash, "live_trading": False}
    write_json(out / "paper_trading_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return paper_trade(load_config(config_path))
