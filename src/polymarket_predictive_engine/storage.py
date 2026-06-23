from __future__ import annotations

import sqlite3
from pathlib import Path

TABLES = [
    "markets", "outcomes", "raw_snapshots", "features", "labels", "external_signals", "predictions", "trade_signals", "rejected_signals", "paper_orders", "live_orders", "fills", "positions", "portfolio_snapshots", "model_runs", "data_quality_issues", "risk_events", "backtest_trades"
]


def init_db(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        for table in TABLES:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), payload_json TEXT NOT NULL DEFAULT '{{}}')")
        con.commit()
    finally:
        con.close()
