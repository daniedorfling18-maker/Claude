from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .storage import connect_db, init_db
from .utils import now_utc, write_csv, write_json


def _starting_cash(cfg: EngineConfig) -> float:
    return float(cfg.raw.get("paper_trading", {}).get("starting_cash", cfg.raw.get("risk", {}).get("bankroll", 1000)))


def _latest_cash(con: sqlite3.Connection, starting_cash: float) -> float:
    row = con.execute(
        "SELECT cash_balance_after_usdc FROM cash_ledger ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row else float(starting_cash)


def portfolio_snapshot(cfg: EngineConfig) -> dict[str, Any]:
    """Read portfolio state from the typed SQLite ledger, not CSV placeholders."""
    init_db(cfg.database_path)
    con = connect_db(cfg.database_path)
    try:
        cash = _latest_cash(con, _starting_cash(cfg))
        open_orders = con.execute(
            "SELECT COUNT(*) FROM orders WHERE lower(status) IN ('created','submitted','open','partially_filled','pending')"
        ).fetchone()[0]
        positions = con.execute("SELECT * FROM positions").fetchall()
        exposure = sum(float(p["quantity"] or 0.0) * float(p["average_entry_price"] or 0.0) for p in positions)
        realised = sum(float(p["realised_pnl_usdc"] or 0.0) for p in positions)
        timestamp = now_utc()
        snap = {
            "timestamp": timestamp,
            "cash": round(cash, 6),
            "open_orders": int(open_orders),
            "position_count": len(positions),
            "total_exposure": round(exposure, 6),
            "realized_pnl": round(realised, 6),
            "unrealized_pnl": 0.0,
            "drawdown": 0.0,
            "daily_loss": 0.0,
            "database_path": str(cfg.database_path),
        }
        con.execute(
            """
            INSERT OR REPLACE INTO portfolio_snapshots(
                snapshot_id, created_at, cash_usdc, open_order_count, position_count,
                total_exposure_usdc, realised_pnl_usdc, unrealised_pnl_usdc,
                daily_loss_usdc, drawdown, risk_usage_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"portfolio:{timestamp}",
                timestamp,
                snap["cash"],
                snap["open_orders"],
                snap["position_count"],
                snap["total_exposure"],
                snap["realized_pnl"],
                snap["unrealized_pnl"],
                snap["daily_loss"],
                snap["drawdown"],
                json.dumps({"source": "sqlite_ledger"}, sort_keys=True),
            ),
        )
        con.commit()
    finally:
        con.close()
    out = cfg.output_root / "polymarket_portfolio"
    write_csv(out / "portfolio_snapshot.csv", [snap])
    write_json(out / "risk_state.json", {"snapshot": snap, "risk_limit_usage": {"single_market": 0, "category": 0, "daily_loss": 0}})
    return snap


def reconciliation_report(cfg: EngineConfig) -> dict[str, Any]:
    """Reconcile cash and positions from the typed SQLite ledger."""
    init_db(cfg.database_path)
    starting_cash = _starting_cash(cfg)
    con = connect_db(cfg.database_path)
    try:
        deposits = payouts = stakes = fees = other = 0.0
        for row in con.execute("SELECT entry_type, amount_usdc FROM cash_ledger"):
            entry_type = str(row["entry_type"] or "").lower()
            amount = float(row["amount_usdc"] or 0.0)
            if entry_type == "deposit":
                deposits += amount
            elif entry_type in {"payout", "settlement"}:
                payouts += amount
            elif entry_type in {"stake", "order_stake", "buy"}:
                stakes += abs(amount)
            elif entry_type in {"fee", "trading_fee"}:
                fees += abs(amount)
            elif entry_type not in {"starting_cash", "opening_balance"}:
                other += amount
        expected_cash = starting_cash + deposits + payouts - stakes - fees + other
        ledger_cash = _latest_cash(con, starting_cash)
        fill_rows = con.execute("SELECT market_id, token_id, side, SUM(quantity) AS q FROM fills GROUP BY market_id, token_id, side").fetchall()
        fill_qty = {(r["market_id"], r["token_id"], r["side"]): float(r["q"] or 0.0) for r in fill_rows}
        breaks = []
        for pos in con.execute("SELECT market_id, token_id, side, quantity FROM positions"):
            key = (pos["market_id"], pos["token_id"], pos["side"])
            diff = float(pos["quantity"] or 0.0) - fill_qty.get(key, 0.0)
            if abs(diff) > 1e-6:
                breaks.append({"market_id": key[0], "token_id": key[1], "side": key[2], "difference": diff})
    finally:
        con.close()
    report = {
        "generated_at_utc": now_utc(),
        "database_path": str(Path(cfg.database_path)),
        "starting_cash": starting_cash,
        "deposits": round(deposits, 6),
        "stakes": round(stakes, 6),
        "payouts": round(payouts, 6),
        "fees": round(fees, 6),
        "expected_ending_cash": round(expected_cash, 6),
        "ledger_ending_cash": round(ledger_cash, 6),
        "cash_difference": round(ledger_cash - expected_cash, 9),
        "cash_reconciles": abs(ledger_cash - expected_cash) <= 1e-6,
        "position_breaks": breaks,
        "positions_tie_to_fills": not breaks,
        "balanced": abs(ledger_cash - expected_cash) <= 1e-6 and not breaks,
    }
    write_json(cfg.output_root / "polymarket_portfolio" / "reconciliation_report.json", report)
    return report


def main(config_path: str):
    return portfolio_snapshot(load_config(config_path))
