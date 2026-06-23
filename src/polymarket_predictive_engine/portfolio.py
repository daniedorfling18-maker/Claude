from __future__ import annotations

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json


def portfolio_snapshot(cfg: EngineConfig) -> dict[str, float | str]:
    positions = read_csv_rows(cfg.output_root / "polymarket_portfolio" / "positions.csv")
    cash = float(cfg.raw.get("paper_trading", {}).get("starting_cash", cfg.raw.get("risk", {}).get("bankroll", 1000)))
    exposure = sum((safe_float(p.get("quantity")) or 0) * (safe_float(p.get("average_entry_price")) or 0) for p in positions)
    snap = {"timestamp": now_utc(), "cash": cash, "open_orders": 0, "position_count": len(positions), "total_exposure": exposure, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "drawdown": 0.0, "daily_loss": 0.0}
    out = cfg.output_root / "polymarket_portfolio"
    write_csv(out / "portfolio_snapshot.csv", [snap])
    write_json(out / "risk_state.json", {"snapshot": snap, "risk_limit_usage": {"single_market": 0, "category": 0, "daily_loss": 0}})
    return snap


def main(config_path: str):
    return portfolio_snapshot(load_config(config_path))
