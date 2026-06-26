from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .storage import connect_db
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json


def _snapshot_id(key: str) -> str:
    return "snapshot_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def ingest_scanner_snapshot(
    cfg: EngineConfig,
    snapshot_path: str | Path | None = None,
) -> dict[str, Any]:
    """Append the scanner's latest CSV into the canonical point-in-time raw stream."""
    source = Path(snapshot_path) if snapshot_path else cfg.output_root / "polymarket" / "market_snapshot.csv"
    rows = read_csv_rows(source)
    raw_path = cfg.data_root / "outputs" / "polymarket_fixed" / "worldcup" / "ml" / "raw_market_snapshots.csv"
    existing = read_csv_rows(raw_path)
    existing_keys = {
        (row.get("snapshot_timestamp", ""), row.get("market_id", ""), row.get("token_id", ""))
        for row in existing
    }
    appended: list[dict[str, Any]] = []
    db_error = ""
    for row in rows:
        timestamp = row.get("timestamp") or now_utc()
        market_id = row.get("condition_id") or row.get("market_slug") or ""
        token_id = row.get("token_id", "")
        if not market_id or not token_id:
            continue
        key = (timestamp, market_id, token_id)
        if key in existing_keys:
            continue
        bid = safe_float(row.get("best_bid"))
        ask = safe_float(row.get("best_ask"))
        midpoint = (bid + ask) / 2 if bid is not None and ask is not None else safe_float(row.get("gamma_price"))
        spread = ask - bid if bid is not None and ask is not None else safe_float(row.get("spread"))
        bid_size = safe_float(row.get("bid_size")) or 0.0
        ask_size = safe_float(row.get("ask_size")) or 0.0
        normalized = {
            "snapshot_timestamp": timestamp,
            "market_id": market_id,
            "market_slug": row.get("market_slug", ""),
            "event_id": row.get("event_slug", ""),
            "token_id": token_id,
            "question": row.get("question", ""),
            "category": "worldcup",
            "outcome": row.get("outcome", ""),
            "best_bid": "" if bid is None else bid,
            "best_ask": "" if ask is None else ask,
            "midpoint": "" if midpoint is None else midpoint,
            "spread": "" if spread is None else spread,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "liquidity": bid_size + ask_size,
            "close_time": row.get("close_time", ""),
            "source": "polymarket_mispricing_bot",
            "_raw_row_json": json.dumps(row, sort_keys=True),
        }
        appended.append(normalized)
        existing_keys.add(key)

    db_status = "skipped_no_new_rows"
    if appended:
        retry_attempts = int((cfg.raw.get("storage", {}) or {}).get("sqlite_retry_attempts", 3))
        for attempt in range(max(1, retry_attempts)):
            con = connect_db(cfg.database_path)
            try:
                with con:
                    for normalized in appended:
                        key = (
                            str(normalized.get("snapshot_timestamp") or ""),
                            str(normalized.get("market_id") or ""),
                            str(normalized.get("token_id") or ""),
                        )
                        idempotency_key = "|".join(key)
                        bid = safe_float(normalized.get("best_bid"))
                        ask = safe_float(normalized.get("best_ask"))
                        midpoint = safe_float(normalized.get("midpoint"))
                        spread = safe_float(normalized.get("spread"))
                        liquidity = safe_float(normalized.get("liquidity")) or 0.0
                        con.execute(
                            """
                            INSERT OR IGNORE INTO market_snapshots(
                                snapshot_id, idempotency_key, collected_at, market_id, token_id,
                                best_bid, best_ask, midpoint, spread, liquidity, raw_payload_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                _snapshot_id(idempotency_key),
                                idempotency_key,
                                key[0],
                                key[1],
                                key[2],
                                bid,
                                ask,
                                midpoint,
                                spread,
                                liquidity,
                                str(normalized.get("_raw_row_json") or "{}"),
                            ),
                        )
                db_status = "inserted"
                db_error = ""
                break
            except sqlite3.OperationalError as exc:
                db_status = "db_error"
                db_error = f"{type(exc).__name__}: {exc}"
                if attempt + 1 >= max(1, retry_attempts):
                    break
                time.sleep(min(10.0, 1.5 * (attempt + 1)))
            finally:
                con.close()

    if db_status != "db_error":
        for normalized in appended:
            normalized.pop("_raw_row_json", None)
            existing.append(normalized)
        write_csv(raw_path, existing)
    else:
        for normalized in appended:
            normalized.pop("_raw_row_json", None)
        appended = []

    summary = {
        "status": "ingested" if db_status != "db_error" else "db_error",
        "db_status": db_status,
        "db_error": db_error,
        "source_file": str(source),
        "raw_output_file": str(raw_path),
        "source_rows": len(rows),
        "rows_appended": len(appended),
        "total_raw_rows": len(existing),
        "database_path": str(cfg.database_path),
        "generated_at_utc": now_utc(),
    }
    write_json(cfg.governance_root / "scanner_snapshot_ingest_summary.json", summary)
    return summary
