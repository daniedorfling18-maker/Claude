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
        category TEXT NOT NULL DEFAULT '',
        event_id TEXT NOT NULL DEFAULT '',
        correlation_key TEXT NOT NULL DEFAULT '',
        strategy_name TEXT NOT NULL DEFAULT '',
        model_version TEXT NOT NULL DEFAULT '',
        prediction_id TEXT NOT NULL DEFAULT '',
        prediction_timestamp TEXT NOT NULL DEFAULT '',
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
        fill_model TEXT NOT NULL DEFAULT '',
        FOREIGN KEY(order_id) REFERENCES orders(order_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS positions (
        position_id TEXT PRIMARY KEY,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        side TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',
        correlation_key TEXT NOT NULL DEFAULT '',
        quantity REAL NOT NULL DEFAULT 0,
        average_entry_price REAL NOT NULL DEFAULT 0,
        cost_basis_usdc REAL NOT NULL DEFAULT 0,
        realised_pnl_usdc REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'open',
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
    CREATE TABLE IF NOT EXISTS settlements (
        settlement_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        market_id TEXT NOT NULL,
        token_id TEXT NOT NULL,
        target INTEGER NOT NULL CHECK (target IN (0,1)),
        quantity REAL NOT NULL,
        cost_basis_usdc REAL NOT NULL,
        payout_usdc REAL NOT NULL,
        realised_pnl_usdc REAL NOT NULL,
        resolution_source TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        cash_usdc REAL NOT NULL,
        equity_usdc REAL NOT NULL,
        open_order_count INTEGER NOT NULL,
        position_count INTEGER NOT NULL,
        total_exposure_usdc REAL NOT NULL,
        realised_pnl_usdc REAL NOT NULL,
        unrealised_pnl_usdc REAL NOT NULL,
        daily_loss_usdc REAL NOT NULL,
        drawdown REAL NOT NULL,
        risk_usage_json TEXT NOT NULL DEFAULT '{}'
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
    "CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_positions_market_token ON positions(market_id, token_id)",
    "CREATE INDEX IF NOT EXISTS idx_cash_ledger_created ON cash_ledger(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_predictions_market_token_time ON model_predictions(market_id, token_id, prediction_timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_market_snapshots_market_token_time ON market_snapshots(market_id, token_id, collected_at)",
]

TABLES = [f"legacy_{table}" for table in LEGACY_TABLES] + [
    "orders",
    "fills",
    "positions",
    "cash_ledger",
    "settlements",
    "portfolio_snapshots",
    "risk_events",
    "model_predictions",
    "market_snapshots",
    "schema_migrations",
]

EXPECTED_COLUMNS = {
    "fills": {"fill_id", "order_id", "fill_price", "quantity"},
    "positions": {"position_id", "market_id", "token_id", "quantity"},
    "portfolio_snapshots": {"snapshot_id", "cash_usdc", "equity_usdc"},
    "risk_events": {"risk_event_id", "event_type", "severity"},
}

MIGRATION_COLUMNS = {
    "orders": [
        ("category", "TEXT NOT NULL DEFAULT ''"),
        ("event_id", "TEXT NOT NULL DEFAULT ''"),
        ("correlation_key", "TEXT NOT NULL DEFAULT ''"),
        ("strategy_name", "TEXT NOT NULL DEFAULT ''"),
        ("model_version", "TEXT NOT NULL DEFAULT ''"),
        ("prediction_id", "TEXT NOT NULL DEFAULT ''"),
        ("prediction_timestamp", "TEXT NOT NULL DEFAULT ''"),
        ("risk_decision_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("source_signal_json", "TEXT NOT NULL DEFAULT '{}'"),
    ],
    "fills": [
        ("fee_usdc", "REAL NOT NULL DEFAULT 0"),
        ("slippage_usdc", "REAL NOT NULL DEFAULT 0"),
        ("fill_model", "TEXT NOT NULL DEFAULT ''"),
    ],
    "positions": [
        ("category", "TEXT NOT NULL DEFAULT ''"),
        ("correlation_key", "TEXT NOT NULL DEFAULT ''"),
        ("realised_pnl_usdc", "REAL NOT NULL DEFAULT 0"),
        ("status", "TEXT NOT NULL DEFAULT 'open'"),
    ],
    "portfolio_snapshots": [
        ("equity_usdc", "REAL NOT NULL DEFAULT 0"),
        ("risk_usage_json", "TEXT NOT NULL DEFAULT '{}'"),
    ],
}


def _configure_connection(con: sqlite3.Connection) -> None:
    """Apply conservative SQLite settings for Docker/Windows bind mounts."""
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA temp_store = MEMORY")
    try:
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = NORMAL")
    except sqlite3.OperationalError:
        # Some filesystems/container mounts reject WAL changes transiently. The
        # busy timeout still makes the connection safer than the SQLite default.
        pass


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _table_columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _legacy_table_name(con: sqlite3.Connection, table: str) -> str:
    version = 0
    while _table_exists(con, f"{table}_legacy_v{version}"):
        version += 1
    return f"{table}_legacy_v{version}"


def _preserve_incompatible_legacy_tables(con: sqlite3.Connection) -> None:
    """Rename original payload-only placeholder tables before typed creation."""
    for table, expected in EXPECTED_COLUMNS.items():
        if not _table_exists(con, table):
            continue
        if expected.issubset(_table_columns(con, table)):
            continue
        legacy_name = _legacy_table_name(con, table)
        con.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy_name}"')


def _create_legacy_placeholder_tables(con: sqlite3.Connection) -> None:
    for table in LEGACY_TABLES:
        con.execute(
            f"CREATE TABLE IF NOT EXISTS legacy_{table} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), "
            "payload_json TEXT NOT NULL DEFAULT '{}'"
            ")"
        )


def _migrate_missing_columns(con: sqlite3.Connection) -> None:
    for table, columns in MIGRATION_COLUMNS.items():
        if not _table_exists(con, table):
            continue
        existing = _table_columns(con, table)
        for column, definition in columns:
            if column not in existing:
                con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}')


def init_db(path: str | Path) -> None:
    """Initialise the typed, audit-grade paper execution ledger."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    try:
        _configure_connection(con)
        _preserve_incompatible_legacy_tables(con)
        _create_legacy_placeholder_tables(con)
        for statement in SCHEMA_STATEMENTS:
            con.execute(statement)
        _migrate_missing_columns(con)
        for statement in INDEX_STATEMENTS:
            con.execute(statement)
        con.execute(
            "INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES (?)",
            ("typed_paper_ledger_v1",),
        )
        con.execute(
            "INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES (?)",
            ("typed_paper_ledger_v2_paper_broker",),
        )
        con.commit()
    finally:
        con.close()


def connect_db(path: str | Path) -> sqlite3.Connection:
    init_db(path)
    con = sqlite3.connect(Path(path), timeout=30)
    con.row_factory = sqlite3.Row
    _configure_connection(con)
    return con
