from __future__ import annotations

from typing import Any

from ..config import EngineConfig, load_config
from ..readiness import paper_live_promotion_gate
from ..utils import write_csv, write_json


def paper_trade(cfg: EngineConfig) -> dict[str, Any]:
    gate = paper_live_promotion_gate(cfg)
    out = cfg.output_root / "polymarket_portfolio"
    write_csv(out / "paper_orders.csv", [])
    write_csv(out / "paper_fills.csv", [])
    write_csv(out / "positions.csv", [])
    summary = {
        "mode": "paper",
        "status": "blocked_by_readiness_gate",
        "orders": 0,
        "fills": 0,
        "cash": float(cfg.raw.get("paper_trading", {}).get("starting_cash", cfg.raw.get("risk", {}).get("bankroll", 1000))),
        "approved_for_paper_trading": bool(gate.get("approved_for_paper_trading")),
        "blockers": gate.get("paper_blockers", []),
    }
    write_json(out / "paper_trading_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return paper_trade(load_config(config_path))
