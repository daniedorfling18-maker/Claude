from __future__ import annotations

import sqlite3
from pathlib import Path

LEGACY_TABLES = [
    "markets",
    "outcomes",
    "raw_snapshots",
    "features",
    "labels",
    "external_signals",
    "predictions",
    "trade_signals",
    "rejected_signals",
    "paper_orders",
    "live_orders",
    "fills",
    "positions",
    "portfolio_snapshots",
    "model_runs",
    "data_quality_issues",
    "risk_events",
    "backtest_trades",
]


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('paper','live')),
        status TEXT NOT NULL,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        limit_price REAL NOT NULL CHECK (limit_price > 0 AND limit_price < 1),
        stake_usdc REAL NOT NULL CHECK (stake_usdc >= 0),
        quantity REAL NOT NULL CHECK (quantity >= 0),
        strategy_name TEXT NOT NULL DEFAULT '',
        model_version TEXT NOT NULL DEFAULT '',
        prediction_id TEXT NOT NULL DEFAULT '',
        risk_decision_json TEXT NOT NULL DEFAULT '{}',
        source_signal_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        fill_price REAL NOT NULL CHECK (fill_price > 0 AND fill_price < 1),
        quantity REAL NOT NULL CHECK (quantity >= 0),
        gross_notional_usdc REAL NOT NULL CHECK (gross_notional_usdc >= 0),
        fee_usdc REAL NOT NULL DEFAULT 0 CHECK (fee_usdc >= 0),
        slippage_usdc REAL NOT NULL DEFAULT 0 CHECK (slippage_usdc >= 0),
        FOREIGN KEY(order_id) REFERENCES orders(order_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        position_id TEXT PRIMARY KEY,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity REAL NOT NULL DEFAULT 0,
        average_entry_price REAL NOT NULL DEFAULT 0,
        cost_basis_usdc REAL NOT NULL DEFAULT 0,
        realised_pnl_usdc REAL NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        UNIQUE(market_id, token_id, side)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cash_ledger (
        cash_entry_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        entry_type TEXT NOT NULL,
        amount_usdc REAL NOT NULL,
        cash_balance_after_usdc REAL NOT NULL,
        order_id TEXT NOT NULL DEFAULT '',
        fill_id TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        cash_usdc REAL NOT NULL,
        open_order_count INTEGER NOT NULL,
        position_count INTEGER NOT NULL,
        total_exposure_usdc REAL NOT NULL,
        realised_pnl_usdc REAL NOT NULL,
        unrealised_pnl_usdc REAL NOT NULL,
        daily_loss_usdc REAL NOT NULL,
        drawdown REAL NOT NULL,
        risk_usage_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(created_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_events (
        risk_event_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        message TEXT NOT NULL,
        context_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_predictions (
        prediction_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        prediction_timestamp TEXT NOT NULL,
        model_probability REAL NOT NULL CHECK (model_probability >= 0 AND model_probability <= 1),
        market_probability REAL NOT NULL CHECK (market_probability >= 0 AND market_probability <= 1),
        executable_price REAL NOT NULL CHECK (executable_price > 0 AND executable_price < 1),
        edge REAL NOT NULL,
        model_version TEXT NOT NULL DEFAULT '',
        feature_set_version TEXT NOT NULL DEFAULT '',
        validation_status TEXT NOT NULL DEFAULT '',
        raw_payload_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        collected_at TEXT NOT NULL,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        best_bid REAL,
        best_ask REAL,
        midpoint REAL,
        spread REAL,
        liquidity REAL NOT NULL DEFAULT 0,
        raw_payload_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        migration_id TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
    )
    """,
]


INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_orders_market_token ON orders(market_id, token_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)",
    "CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_positions_market_token ON positions(market_id, token_id)",
    "CREATE INDEX IF NOT EXISTS idx_cash_ledger_created ON cash_ledger(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_predictions_market_token_time ON model_predictions(market_id, token_id, prediction_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_token_time ON market_snapshots(market_id, token_id, collected_at)",
]


def init_db(path: str | Path) -> None:
    """Initialise an audit-grade typed ledger schema.

    The older payload_json-only tables are left in place for backward compatibility,
    but all paper and live execution should use the typed tables above.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        for table in LEGACY_TABLES:
            cur.execute(
                f"CREATE TABLE IF NOT EXISTS legacy_{table} ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), "
                "payload_json TEXT NOT NULL DEFAULT '{}'"
                ")"
            )
        for statement in SCHEMA_STATEMENTS:
            cur.execute(statement)
        for statement in INDEX_STATEMENTS:
            cur.execute(statement)
        cur.execute(
            "INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES (?)",
            ("typed_paper_ledger_v1",),
        )
        con.commit()
    finally:
        con.close()


def connect_db(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(Path(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con
