from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from quant_lab.risk import conditional_var, historical_var

from .config import EngineConfig, load_config
from .paper_broker import export_ledger, portfolio_state, write_portfolio_snapshot
from .storage import connect_db, init_db
from .utils import now_utc, safe_float, write_json
from .worldcup_validation import normalised_correlation_key


def _starting_cash(cfg: EngineConfig) -> float:
    return float(cfg.raw.get("paper_trading", {}).get("starting_cash", cfg.raw.get("risk", {}).get("bankroll", 1000)))


def _latest_cash(con: sqlite3.Connection, starting_cash: float) -> float:
    row = con.execute(
        "SELECT cash_balance_after_usdc FROM cash_ledger ORDER BY created_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return float(row[0]) if row else float(starting_cash)


def _latest_position_mark(con: sqlite3.Connection, position: sqlite3.Row) -> float | None:
    quote = con.execute(
        """
        SELECT best_bid, best_ask, midpoint
        FROM market_snapshots
        WHERE market_id = ? AND token_id = ?
        ORDER BY collected_at DESC, rowid DESC
        LIMIT 1
        """,
        (position["market_id"], position["token_id"]),
    ).fetchone()
    if quote is None:
        return None
    for key in ("midpoint", "best_bid", "best_ask"):
        value = safe_float(quote[key])
        if value is not None and 0 <= value <= 1:
            return float(value)
    return None


def _exposure_rows(exposures: dict[str, float], *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = [
        {"key": key or "unknown", "cost_basis_usdc": round(value, 6)}
        for key, value in sorted(exposures.items(), key=lambda item: item[1], reverse=True)
        if value > 0
    ]
    return rows[:limit] if limit is not None else rows


def _portfolio_risk_snapshot(con: sqlite3.Connection, cfg: EngineConfig) -> dict[str, Any]:
    """Report-only portfolio VaR and correlated exposure over open paper positions."""
    positions = con.execute(
        "SELECT * FROM positions WHERE status = 'open' AND quantity > 0 ORDER BY position_id"
    ).fetchall()
    exposure_by_correlation: dict[str, float] = {}
    exposure_by_category: dict[str, float] = {}
    returns: list[float] = []
    marked_positions = 0
    skipped_unmarked = 0
    total_cost = 0.0
    worst_return: float | None = None

    for position in positions:
        row = dict(position)
        cost = safe_float(row.get("cost_basis_usdc")) or 0.0
        quantity = safe_float(row.get("quantity")) or 0.0
        total_cost += cost
        correlation_key = normalised_correlation_key(row) or "unknown"
        category = str(row.get("category") or "unknown").strip() or "unknown"
        exposure_by_correlation[correlation_key] = exposure_by_correlation.get(correlation_key, 0.0) + cost
        exposure_by_category[category] = exposure_by_category.get(category, 0.0) + cost

        entry = safe_float(row.get("average_entry_price"))
        mark = _latest_position_mark(con, position)
        if entry is None or entry <= 0 or mark is None or quantity <= 0:
            skipped_unmarked += 1
            continue
        position_return = (float(mark) - float(entry)) / float(entry)
        returns.append(position_return)
        marked_positions += 1
        worst_return = position_return if worst_return is None else min(worst_return, position_return)

    var_return = historical_var(returns, confidence=0.95)
    cvar_return = conditional_var(returns, confidence=0.95)
    return {
        "generated_at_utc": now_utc(),
        "decision_use": "reporting_only_not_trade_authorisation",
        "open_positions": len(positions),
        "marked_positions": marked_positions,
        "unmarked_positions": skipped_unmarked,
        "total_cost_usdc": round(total_cost, 6),
        "exposure_by_correlation_key": _exposure_rows(exposure_by_correlation, limit=10),
        "exposure_by_category": _exposure_rows(exposure_by_category),
        "var_95_usdc": round(float(var_return) * total_cost, 6),
        "cvar_95_usdc": round(float(cvar_return) * total_cost, 6),
        "var_95_return": round(float(var_return), 6),
        "cvar_95_return": round(float(cvar_return), 6),
        "worst_position_return_pct": None if worst_return is None else round(float(worst_return) * 100.0, 6),
        "mark_source": "latest market_snapshots midpoint, then bid, then ask",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }


def portfolio_snapshot(cfg: EngineConfig) -> dict[str, float | str]:
    con = connect_db(cfg.database_path)
    try:
        with con:
            snapshot = write_portfolio_snapshot(con, cfg)
            export_ledger(con, cfg)
            portfolio = portfolio_state(con, cfg)
            portfolio_risk = _portfolio_risk_snapshot(con, cfg)
        write_json(
            cfg.output_root / "polymarket_portfolio" / "risk_state.json",
            {"snapshot": snapshot, "portfolio": portfolio, "portfolio_risk": portfolio_risk},
        )
        return snapshot
    finally:
        con.close()


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
