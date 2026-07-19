"""WO-101 historical executable bid/ask corpus.

Polymarket's public ``prices-history`` response contains only ``{t, p}`` and
cannot reconstruct an executable spread.  The public market websocket does
publish order books and best-bid/ask changes, and the live normaliser already
archives those rows.  This collector turns that producer-owned history into a
versioned append-only quote ledger without inventing either side from a
midpoint.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import (
    append_csv_rows,
    normalize_external_timestamp,
    read_json,
    safe_float,
    write_json,
)
from .websocket_normaliser import WEBSOCKET_FEATURES_RELATIVE_PATH


WORK_ORDER = "WO-101"
QUOTE_LEDGER_RELATIVE_PATH = Path("polymarket_training") / "historical_bid_ask_v1.csv"
QUOTE_STATE_RELATIVE_PATH = Path("polymarket_model_governance") / "historical_bid_ask_ingest_state.json"
QUOTE_SUMMARY_RELATIVE_PATH = Path("polymarket_model_governance") / "historical_bid_ask_summary.json"
QUOTE_LOCK = "wo101_historical_bid_ask"

QUOTE_FIELDS = [
    "quote_observation_id",
    "observed_at_utc",
    "source_timestamp_utc",
    "market_id",
    "token_id",
    "category",
    "event_type",
    "best_bid",
    "best_ask",
    "midpoint",
    "spread",
    "top_bid_size",
    "top_ask_size",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "bid_depth_5pct",
    "ask_depth_5pct",
    "book_imbalance",
    "fees_enabled",
    "fee_schedule_rate",
    "fee_schedule_exponent",
    "fee_schedule_taker_only",
    "source_corpus",
    "paper_trading_invoked",
    "live_trading_invoked",
]


def _utc_from_external(value: Any) -> datetime | None:
    seconds = normalize_external_timestamp(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_clock(value: datetime | str | None) -> datetime:
    parsed = _utc_from_external(value) if value is not None else datetime.now(timezone.utc)
    if parsed is None:
        raise ValueError(f"invalid historical bid/ask run clock: {value!r}")
    return parsed


def iter_csv_rows(path: Path) -> Iterator[dict[str, str]]:
    """Stream plain or gzip CSV without materialising the archive."""

    if not path.exists() or path.stat().st_size == 0:
        return
    if path.suffix == ".gz":
        context = gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    else:
        context = path.open("rt", encoding="utf-8-sig", errors="replace", newline="")
    with context as handle:
        for row in csv.DictReader(handle):
            yield {str(key): "" if value is None else str(value) for key, value in row.items()}


def _identity(row: Mapping[str, Any]) -> str:
    payload = {
        key: row.get(key, "")
        for key in (
            "observed_at_utc",
            "source_timestamp_utc",
            "market_id",
            "token_id",
            "event_type",
            "best_bid",
            "best_ask",
            "top_bid_size",
            "top_ask_size",
        )
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def quote_observation_from_feature(
    raw: Mapping[str, Any],
    *,
    source_corpus: str,
    as_of: datetime,
) -> tuple[dict[str, Any] | None, str]:
    """Normalize one producer row; missing or one-sided books stay excluded."""

    observed = _utc_from_external(
        raw.get("collected_at_utc")
        or raw.get("prediction_timestamp")
        or raw.get("snapshot_timestamp")
    )
    if observed is None:
        return None, "missing_observation_time"
    if observed > as_of:
        return None, "future_observation"
    source_time = _utc_from_external(raw.get("source_timestamp"))
    market_id = str(raw.get("market") or raw.get("market_id") or raw.get("condition_id") or "").strip()
    token_id = str(raw.get("asset_id") or raw.get("token_id") or "").strip()
    if not market_id or not token_id:
        return None, "missing_market_or_token"
    bid = safe_float(raw.get("best_bid"))
    ask = safe_float(raw.get("best_ask"))
    if bid is None or ask is None:
        return None, "midpoint_or_one_sided_only"
    if not 0.0 < bid <= ask < 1.0:
        return None, "invalid_or_crossed_book"

    midpoint = (bid + ask) / 2.0
    row: dict[str, Any] = {
        "observed_at_utc": _iso(observed),
        "source_timestamp_utc": _iso(source_time) if source_time is not None else "",
        "market_id": market_id,
        "token_id": token_id,
        "category": str(raw.get("category") or "unknown"),
        "event_type": str(raw.get("event_type") or "book"),
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": midpoint,
        "spread": ask - bid,
        "top_bid_size": raw.get("top_bid_size") or raw.get("bid_size") or "",
        "top_ask_size": raw.get("top_ask_size") or raw.get("ask_size") or "",
        "bid_depth_1pct": raw.get("bid_depth_1pct") or "",
        "ask_depth_1pct": raw.get("ask_depth_1pct") or "",
        "bid_depth_5pct": raw.get("bid_depth_5pct") or "",
        "ask_depth_5pct": raw.get("ask_depth_5pct") or "",
        "book_imbalance": raw.get("book_imbalance") or "",
        "fees_enabled": raw.get("fees_enabled") or "",
        "fee_schedule_rate": raw.get("fee_schedule_rate") or raw.get("taker_fee_rate") or "",
        "fee_schedule_exponent": raw.get("fee_schedule_exponent") or raw.get("taker_fee_exponent") or "",
        "fee_schedule_taker_only": raw.get("fee_schedule_taker_only") or raw.get("taker_fee_taker_only") or "",
        "source_corpus": source_corpus,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    row["quote_observation_id"] = _identity(row)
    return row, "ok"


def _source_paths(cfg: EngineConfig) -> list[Path]:
    paths = [cfg.output_root / WEBSOCKET_FEATURES_RELATIVE_PATH]
    archive = cfg.output_root / "polymarket_training_archive"
    if archive.exists():
        paths.extend(sorted(archive.glob("features_*.csv.gz")))
        paths.extend(sorted(archive.glob("daily_training_archive_*.csv.gz")))
    return [path for path in paths if path.exists() and path.is_file()]


def _source_key(cfg: EngineConfig, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.output_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _fingerprint(path: Path) -> dict[str, int]:
    stat = path.stat()
    return {"size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _chunks(rows: Iterable[dict[str, Any]], size: int = 5000) -> Iterator[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def collect_historical_bid_ask(
    cfg: EngineConfig,
    *,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Ingest changed producer files with disk-backed exact deduplication."""

    current = _run_clock(as_of)
    generated_at = _iso(current)
    ledger_path = cfg.output_root / QUOTE_LEDGER_RELATIVE_PATH
    state_path = cfg.output_root / QUOTE_STATE_RELATIVE_PATH
    summary_path = cfg.output_root / QUOTE_SUMMARY_RELATIVE_PATH
    sources = _source_paths(cfg)
    prior = read_json(state_path, default={})
    prior_sources = prior.get("sources", {}) if isinstance(prior, dict) and isinstance(prior.get("sources"), dict) else {}
    fingerprints = {_source_key(cfg, path): _fingerprint(path) for path in sources}
    committed_fingerprints = {
        key: prior_sources[key]
        for key in fingerprints
        if key in prior_sources
    }
    changed = [
        path
        for path in sources
        if prior_sources.get(_source_key(cfg, path)) != fingerprints[_source_key(cfg, path)]
    ]

    base = {
        "work_order": WORK_ORDER,
        "generated_at_utc": generated_at,
        "producer_contract": "websocket_normaliser exact two-sided book rows and immutable training archives",
        "source_files_available": len(sources),
        "source_files_changed": len(changed),
        "output_file": str(ledger_path),
        "state_file": str(state_path),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    with runtime_lock(cfg, QUOTE_LOCK, stale_after_seconds=1800) as lock:
        if not lock.acquired:
            payload = {
                **base,
                "status": "skipped_lock_held",
                "rows_seen": 0,
                "rows_appended": 0,
                "runtime_lock": lock.as_dict(),
            }
            write_json(summary_path, payload)
            return payload
        if not changed:
            payload = {
                **base,
                "status": "ok",
                "rows_seen": 0,
                "rows_appended": 0,
                "exclusions": {},
                "note": "no producer file changed since the last successful ingest",
            }
            write_json(summary_path, payload)
            return payload

        scratch = ledger_path.with_name(f".wo101_quote_dedup.{os.getpid()}.sqlite")
        scratch.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(scratch)
        rows_seen = 0
        exclusions: dict[str, int] = {}
        appended = 0
        deferred_sources: set[str] = set()
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            connection.execute("PRAGMA temp_store=FILE")
            connection.execute("CREATE TABLE seen (observation_id TEXT PRIMARY KEY) WITHOUT ROWID")
            connection.execute(
                "CREATE TABLE pending (observation_id TEXT PRIMARY KEY, observed_at TEXT NOT NULL, payload TEXT NOT NULL) WITHOUT ROWID"
            )
            for existing in iter_csv_rows(ledger_path):
                observation_id = str(existing.get("quote_observation_id") or "")
                if observation_id:
                    connection.execute("INSERT OR IGNORE INTO seen VALUES (?)", (observation_id,))
            connection.commit()

            for source in changed:
                source_name = _source_key(cfg, source)
                source_has_future_observation = False
                for raw in iter_csv_rows(source):
                    rows_seen += 1
                    row, reason = quote_observation_from_feature(
                        raw,
                        source_corpus=source_name,
                        as_of=current,
                    )
                    if row is None:
                        exclusions[reason] = exclusions.get(reason, 0) + 1
                        if reason == "future_observation":
                            source_has_future_observation = True
                        continue
                    connection.execute(
                        "INSERT OR IGNORE INTO pending VALUES (?, ?, ?)",
                        (
                            row["quote_observation_id"],
                            row["observed_at_utc"],
                            json.dumps(row, sort_keys=True, separators=(",", ":")),
                        ),
                    )
                    if rows_seen % 10_000 == 0:
                        connection.commit()
                if source_has_future_observation:
                    # Do not acknowledge this exact file version yet.  The
                    # unchanged source must be revisited when the run clock
                    # advances far enough for those rows to become observable.
                    deferred_sources.add(source_name)
                else:
                    committed_fingerprints[source_name] = fingerprints[source_name]
            connection.execute(
                "DELETE FROM pending WHERE observation_id IN (SELECT observation_id FROM seen)"
            )
            connection.commit()

            def pending_rows() -> Iterator[dict[str, Any]]:
                cursor = connection.execute(
                    "SELECT payload FROM pending ORDER BY observed_at, observation_id"
                )
                for (payload,) in cursor:
                    decoded = json.loads(payload)
                    if isinstance(decoded, dict):
                        yield decoded

            for batch in _chunks(pending_rows()):
                append_csv_rows(ledger_path, batch, fieldnames=QUOTE_FIELDS)
                appended += len(batch)
            write_json(
                state_path,
                {
                    "work_order": WORK_ORDER,
                    "generated_at_utc": generated_at,
                    "sources": committed_fingerprints,
                    "deferred_future_observation_sources": sorted(deferred_sources),
                    "ledger_path": str(ledger_path),
                    "paper_trading_invoked": False,
                    "live_trading_invoked": False,
                },
            )
        finally:
            connection.close()
            try:
                scratch.unlink()
            except FileNotFoundError:
                pass

    payload = {
        **base,
        "status": "ok",
        "rows_seen": rows_seen,
        "rows_appended": appended,
        "duplicate_rows": max(0, rows_seen - sum(exclusions.values()) - appended),
        "exclusions": dict(sorted(exclusions.items())),
        "source_files_deferred_for_future_observations": len(deferred_sources),
        "deferred_future_observation_sources": sorted(deferred_sources),
        "midpoint_only_rows_accepted": 0,
    }
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return collect_historical_bid_ask(load_config(config_path))
