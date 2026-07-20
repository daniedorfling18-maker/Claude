"""WO-40 maker fill realism replay.

The maker-carry study charges adverse selection from bar moves and trade-print
markouts. This replay asks a narrower execution question: given recorded full
book levels or explicitly quote-aligned depth, would a last-in-queue maker
quote actually have filled when public prints crossed the quote level?

Measurement only. The replay reports a realism ratio next to the study charge
but never modifies the study, gates, quote sheet, or any order path.
"""
from __future__ import annotations

import ast
import csv
import gzip
import json
import time
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any, Iterable

import requests

from .config import EngineConfig, load_config
from .trade_print_collector import collect_maker_portfolio_trade_prints
from .utils import (
    normalize_external_timestamp,
    now_utc,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)

DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"

HORIZONS_MINUTES = (5, 15, 60)
OFFICIAL_BOOK_FIELDS = [
    "condition_id",
    "asset_id",
    "source_timestamp",
    "observation_timestamp",
    "hash",
    "best_bid",
    "best_ask",
    "midpoint",
    "top_bid_size",
    "top_ask_size",
    "bids_json",
    "asks_json",
    "collected_at_utc",
]

COLLECTION_WINDOW_FIELDS = [
    "window_id",
    "condition_id",
    "asset_id",
    "portfolio_generated_at_utc",
    "collected_at_utc",
    "quote_bid_price",
    "quote_ask_price",
    "quote_size_shares",
    "book_poll_status",
    "book_snapshot_rows",
    "trade_poll_status",
    "trade_prints_returned",
    "covered",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("maker_fill_replay", {}) if isinstance(cfg.raw.get("maker_fill_replay"), dict) else {}
    merged = {
        "enabled": True,
        "max_markets": 10,
        # WO-104 fix: reserved budget for recently-active (persistent) markets
        # so a churned-out recurring market keeps maturing Tier-0 coverage even
        # when the current portfolio already fills max_markets.
        "max_persistent_markets": 10,
        "replay_days": 7,
        "book_source": "both",
        "clob_base_url": DEFAULT_CLOB_BASE_URL,
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.1,
        "max_official_book_rows": 200000,
        "max_collection_window_rows": 200000,
        # Twice the 15-minute collection cadence: observations outside this
        # envelope are missing coverage, not a licence to reuse a stale book.
        "max_book_state_lag_seconds": 1800,
        "regime_days": 7,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _stamp(value: Any) -> float | None:
    return normalize_external_timestamp(value)


def _minute(stamp: float) -> float:
    return float(int(stamp // 60) * 60)


def _iter_csv_any(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    if path.suffix == ".gz":
        handle_context = gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    else:
        handle_context = path.open("rt", encoding="utf-8-sig", errors="replace", newline="")
    with handle_context as handle:
        for row in csv.DictReader(handle):
            yield {str(key): "" if value is None else str(value) for key, value in row.items()}


def _read_csv_any(path: Path) -> list[dict[str, str]]:
    """Materialize one bounded file for append/rewrite call sites only."""

    return list(_iter_csv_any(path))


def _write_gzip_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _feature_files(cfg: EngineConfig) -> list[Path]:
    archive_root = cfg.output_root / "polymarket_training_archive"
    files = (
        sorted(
            path
            for path in archive_root.glob("*.csv.gz")
            if not path.name.startswith("daily_official_books_")
        )
        if archive_root.exists()
        else []
    )
    live = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if live.exists():
        files.append(live)
    return files


def _official_book_files(cfg: EngineConfig) -> list[Path]:
    root = cfg.output_root / "maker_carry" / "official_books"
    return sorted(root.glob("*.csv.gz")) if root.exists() else []


def _archived_book_levels(value: Any) -> tuple[list[tuple[float, float]], bool]:
    """Return exact archived levels and whether a level payload was observed."""

    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return [], False
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            try:
                payload = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return [], False
    if not isinstance(payload, list):
        return [], False
    levels: list[tuple[float, float]] = []
    for level in payload:
        if not isinstance(level, dict):
            continue
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append((price, size))
    return levels, bool(levels)


def _book_states_from_rows(
    rows: Iterable[dict[str, Any]],
    token_ids: set[str],
    replay_days: float,
) -> dict[str, list[dict[str, Any]]]:
    max_stamp = 0.0
    latest_by_minute: dict[tuple[str, float], dict[str, Any]] = {}
    for row in rows:
        token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if token_id not in token_ids:
            continue
        # Official point snapshots retain the venue's last-change timestamp,
        # but replay coverage is bounded by when this system observed the book.
        stamp = _stamp(
            row.get("observation_timestamp") or row.get("source_timestamp") or row.get("collected_at_utc")
        )
        bid = safe_float(row.get("best_bid"))
        ask = safe_float(row.get("best_ask"))
        midpoint = safe_float(row.get("midpoint"))
        if stamp is None or bid is None or ask is None or ask <= bid:
            continue
        midpoint = midpoint if midpoint is not None else (bid + ask) / 2.0
        bid_levels, bid_levels_observed = _archived_book_levels(row.get("bids_json"))
        ask_levels, ask_levels_observed = _archived_book_levels(row.get("asks_json"))
        resting_bid_depth = safe_float(row.get("resting_bid_depth_at_quote"))
        resting_ask_depth = safe_float(row.get("resting_ask_depth_at_quote"))
        max_stamp = max(max_stamp, stamp)
        parsed = {
            "stamp": stamp,
            "minute": _minute(stamp),
            "token_id": token_id,
            "best_bid": bid,
            "best_ask": ask,
            "midpoint": midpoint,
            "bid_depth": resting_bid_depth
            if resting_bid_depth is not None
            else safe_float(row.get("top_bid_size"))
            or safe_float(row.get("bid_depth_1pct"))
            or 0.0,
            "ask_depth": resting_ask_depth
            if resting_ask_depth is not None
            else safe_float(row.get("top_ask_size"))
            or safe_float(row.get("ask_depth_1pct"))
            or 0.0,
            "resting_bid_depth_at_quote": resting_bid_depth,
            "resting_ask_depth_at_quote": resting_ask_depth,
            "bid_levels": bid_levels,
            "ask_levels": ask_levels,
            "bid_levels_observed": bid_levels_observed,
            "ask_levels_observed": ask_levels_observed,
        }
        key = (token_id, parsed["minute"])
        previous = latest_by_minute.get(key)
        if previous is None or parsed["stamp"] >= previous["stamp"]:
            latest_by_minute[key] = parsed
    cutoff = max_stamp - replay_days * 86400.0 if max_stamp and replay_days > 0 else float("-inf")
    by_token: dict[str, list[dict[str, Any]]] = {}
    for row in latest_by_minute.values():
        if row["stamp"] >= cutoff:
            by_token.setdefault(str(row["token_id"]), []).append(row)
    for token_rows in by_token.values():
        token_rows.sort(key=lambda item: item["stamp"])
    return by_token


def _book_states(cfg: EngineConfig, token_ids: set[str], replay_days: float) -> dict[str, list[dict[str, Any]]]:
    rows = (row for path in _feature_files(cfg) for row in _iter_csv_any(path))
    return _book_states_from_rows(rows, token_ids, replay_days)


def _official_book_states(cfg: EngineConfig, token_ids: set[str], replay_days: float) -> dict[str, list[dict[str, Any]]]:
    rows = (row for path in _official_book_files(cfg) for row in _iter_csv_any(path))
    return _book_states_from_rows(rows, token_ids, replay_days)


def _trades(cfg: EngineConfig, markets: set[str], token_ids: set[str]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for row in _iter_csv_any(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"):
        market = str(row.get("market") or "").strip()
        token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if market not in markets and token_id not in token_ids:
            continue
        stamp = _stamp(row.get("timestamp") or row.get("collected_at_utc"))
        price = safe_float(row.get("price"))
        size = safe_float(row.get("size"))
        side = str(row.get("side") or "").upper()
        if stamp is None or price is None or size is None or side not in {"BUY", "SELL"}:
            continue
        trades.append({"stamp": stamp, "market": market, "token_id": token_id, "side": side, "price": price, "size": size})
    trades.sort(key=lambda item: item["stamp"])
    return trades


def _state_at_or_before(states: list[dict[str, Any]], stamp: float) -> dict[str, Any] | None:
    index = bisect_right(states, stamp, key=lambda row: row["stamp"]) - 1
    return states[index] if index >= 0 else None


def _state_at_or_after(states: list[dict[str, Any]], stamp: float) -> dict[str, Any] | None:
    index = bisect_left(states, stamp, key=lambda row: row["stamp"])
    return states[index] if index < len(states) else None


def _queue_depth_at_quote(
    state: dict[str, Any],
    *,
    direction: str,
    quote: float,
) -> tuple[float | None, str]:
    """Measure queue ahead only from quote-aligned or full-level evidence."""

    if direction == "bid_fill":
        aligned = safe_float(state.get("resting_bid_depth_at_quote"))
        levels = state.get("bid_levels")
        observed = state.get("bid_levels_observed") is True
    else:
        aligned = safe_float(state.get("resting_ask_depth_at_quote"))
        levels = state.get("ask_levels")
        observed = state.get("ask_levels_observed") is True
    if observed and isinstance(levels, list):
        if direction == "bid_fill":
            depth = sum(size for price, size in levels if float(price) >= quote - 1e-12)
        else:
            depth = sum(size for price, size in levels if float(price) <= quote + 1e-12)
        return depth, "full_book_levels"
    if aligned is not None:
        return aligned, "quote_aligned_depth"
    return None, "unavailable"


def _candidate_map(cfg: EngineConfig) -> dict[str, dict[str, str]]:
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")
    return {str(row.get("condition_id") or ""): row for row in rows if row.get("condition_id")}


def _portfolio(summary: dict[str, Any], candidates: dict[str, dict[str, str]], max_markets: int) -> list[dict[str, Any]]:
    portfolio: list[dict[str, Any]] = []
    for entry in (summary.get("portfolio") or [])[:max_markets]:
        if not isinstance(entry, dict):
            continue
        condition_id = str(entry.get("condition_id") or "").strip()
        candidate = candidates.get(condition_id, {})
        token_id = str(entry.get("token_id") or candidate.get("token_id") or "").strip()
        quote_size = safe_float(entry.get("quote_size_shares"))
        quote_distance = safe_float(entry.get("quote_distance"))
        if condition_id and token_id and quote_size is not None and quote_distance is not None:
            portfolio.append({**entry, "condition_id": condition_id, "token_id": token_id, "quote_size_shares": quote_size, "quote_distance": quote_distance})
    return portfolio


def _recent_book_markets(
    cfg: EngineConfig,
    settings: dict[str, Any],
    *,
    exclude: set[str],
) -> list[dict[str, Any]]:
    """Markets whose official-book file was appended within the regime window.

    WO-104: Tier-0 markouts need CONTINUOUS book history around trades, but the
    study portfolio churns daily, so a market that leaves the top portfolio
    used to stop being snapshotted and its coverage never matured. Keeping a
    recently-active market on the snapshot watchlist for the regime window lets
    a persistent/recurring market accumulate the coverage the evaluator needs.
    Recency uses file mtime (cheap); the token id comes from the candidate map,
    falling back to the file's last recorded asset id. Read-only collection;
    no gate, sizing, or order path reads it.
    """
    books_dir = cfg.output_root / "maker_carry" / "official_books"
    if not books_dir.exists():
        return []
    horizon = float(settings["regime_days"]) * 86400.0
    now = time.time()
    candidates = _candidate_map(cfg)
    watchlist: list[dict[str, Any]] = []
    for path in sorted(books_dir.glob("*.csv.gz")):
        condition_id = path.name[: -len(".csv.gz")]  # .stem strips only ".gz"
        if condition_id in exclude:
            continue
        try:
            if now - path.stat().st_mtime > horizon:
                continue
        except OSError:
            continue
        token_id = str(candidates.get(condition_id, {}).get("token_id") or "").strip()
        if not token_id:
            rows = _read_csv_any(path)
            token_id = str(rows[-1].get("asset_id") or "").strip() if rows else ""
        if token_id:
            watchlist.append({"condition_id": condition_id, "token_id": token_id})
    return watchlist


def _payload_snapshots(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("history", "data", "results", "orderbooks", "books"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return [payload] if ("bids" in payload or "asks" in payload) else []


def _levels(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _best_level(levels: list[dict[str, Any]], *, side: str) -> tuple[float | None, float]:
    parsed: list[tuple[float, float]] = []
    for level in levels:
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is not None and size is not None and size >= 0:
            parsed.append((price, size))
    if not parsed:
        return None, 0.0
    price, size = (max(parsed, key=lambda item: item[0]) if side == "bid" else min(parsed, key=lambda item: item[0]))
    return price, size


def _official_row(snapshot: dict[str, Any], *, condition_id: str, token_id: str, collected_at: str) -> dict[str, Any] | None:
    stamp = _stamp(snapshot.get("timestamp") or snapshot.get("t") or snapshot.get("createdAt") or snapshot.get("created_at"))
    observation_stamp = _stamp(collected_at)
    bids = _levels(snapshot.get("bids"))
    asks = _levels(snapshot.get("asks"))
    bid, bid_size = _best_level(bids, side="bid")
    ask, ask_size = _best_level(asks, side="ask")
    if stamp is None or bid is None or ask is None or ask <= bid:
        return None
    return {
        "condition_id": condition_id,
        "asset_id": str(snapshot.get("asset_id") or snapshot.get("token_id") or snapshot.get("market") or token_id),
        "source_timestamp": stamp,
        "observation_timestamp": observation_stamp if observation_stamp is not None else stamp,
        "hash": str(snapshot.get("hash") or snapshot.get("book_hash") or f"{int(stamp)}:{bid}:{ask}"),
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": (bid + ask) / 2.0,
        "top_bid_size": bid_size,
        "top_ask_size": ask_size,
        "bids_json": json.dumps(bids, separators=(",", ":")),
        "asks_json": json.dumps(asks, separators=(",", ":")),
        "collected_at_utc": collected_at,
    }


def _books_by_token(payload: Any, token_ids: list[str]) -> dict[str, dict[str, Any]]:
    snapshots = _payload_snapshots(payload)
    books: dict[str, dict[str, Any]] = {}
    for index, snapshot in enumerate(snapshots):
        token_id = str(snapshot.get("asset_id") or snapshot.get("token_id") or "").strip()
        if not token_id and index < len(token_ids):
            token_id = token_ids[index]
        if token_id:
            books[token_id] = snapshot
    return books


def snapshot_official_books(cfg: EngineConfig) -> dict[str, Any]:
    """Append one current official CLOB book for every quote-sheet market.

    WO-83 deliberately uses the documented current ``/book``/``/books`` API.
    Repeated cadence snapshots create the history; an undocumented historical
    endpoint must not be treated as coverage.
    """

    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "official_book_snapshot.json"
    generated_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-44/WO-83",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary
    maker_summary = read_json(out_root / "maker_carry_study.json", default={}) or {}
    if not isinstance(maker_summary, dict):
        maker_summary = {}
    portfolio = _portfolio(maker_summary, _candidate_map(cfg), int(settings["max_markets"]))
    # WO-104: keep recently-active markets on the watchlist so a persistent or
    # recurring market accumulates continuous Tier-0 book coverage across
    # portfolio churn, bounded by max_markets.
    persistent = _recent_book_markets(
        cfg, settings, exclude={str(entry["condition_id"]) for entry in portfolio}
    )
    # Always cover the FULL current portfolio (already capped at max_markets),
    # then reserve a separate budget for persistent markets so a full portfolio
    # can no longer crowd recently-active recurring markets off the watchlist.
    persistent_cap = max(0, int(settings.get("max_persistent_markets", settings["max_markets"])))
    watchlist = portfolio + persistent[:persistent_cap]
    if not watchlist:
        summary.update(
            {
                "status": "no_portfolio",
                "markets_polled": 0,
                "rows_added": 0,
                "market_polls": [],
                "errors": [],
            }
        )
        write_json(summary_path, summary)
        return summary

    base = str(settings["clob_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    pause = max(float(settings["request_pause_seconds"]), 0.0)
    token_ids = [str(entry["token_id"]) for entry in watchlist]
    books: dict[str, dict[str, Any]] = {}
    batch_error = ""
    if len(token_ids) > 1:
        try:
            response = requests.post(
                f"{base}/books",
                json=[{"token_id": token_id} for token_id in token_ids],
                timeout=timeout,
            )
            response.raise_for_status()
            books.update(_books_by_token(response.json(), token_ids))
        except Exception as exc:
            batch_error = f"{type(exc).__name__}: {exc}"

    missing = [token_id for token_id in token_ids if token_id not in books]
    fetch_errors: dict[str, str] = {}
    for index, token_id in enumerate(missing):
        try:
            response = requests.get(f"{base}/book", params={"token_id": token_id}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                books[token_id] = payload
            else:
                fetch_errors[token_id] = "invalid_book_payload"
        except Exception as exc:
            fetch_errors[token_id] = f"{type(exc).__name__}: {exc}"
        if pause and index < len(missing) - 1:
            time.sleep(max(pause, 0.1))

    rows_added = 0
    files_written: list[str] = []
    market_polls: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in watchlist:
        condition_id = str(entry["condition_id"])
        token_id = str(entry["token_id"])
        snapshot = books.get(token_id)
        error = fetch_errors.get(token_id, "")
        row = (
            _official_row(snapshot, condition_id=condition_id, token_id=token_id, collected_at=generated_at)
            if isinstance(snapshot, dict)
            else None
        )
        if row is None:
            error = error or "invalid_or_empty_book"
            errors.append(f"{condition_id}: {error}")
            market_polls.append(
                {
                    "condition_id": condition_id,
                    "asset_id": token_id,
                    "status": "failed",
                    "rows_added": 0,
                    "error": error,
                }
            )
            continue

        path = out_root / "official_books" / f"{condition_id}.csv.gz"
        existing = _read_csv_any(path)
        existing_keys = {
            (
                str(item.get("observation_timestamp") or item.get("source_timestamp") or ""),
                str(item.get("hash") or ""),
            )
            for item in existing
        }
        dedup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*existing, row]:
            key = (
                str(item.get("observation_timestamp") or item.get("source_timestamp") or ""),
                str(item.get("hash") or ""),
            )
            if all(key):
                dedup[key] = item
        combined = sorted(
            dedup.values(),
            key=lambda item: _stamp(item.get("observation_timestamp"))
            or _stamp(item.get("source_timestamp"))
            or 0.0,
        )
        max_rows = int(settings["max_official_book_rows"])
        if max_rows > 0 and len(combined) > max_rows:
            combined = combined[-max_rows:]
        _write_gzip_csv(path, combined, OFFICIAL_BOOK_FIELDS)
        added = int((str(row["observation_timestamp"]), str(row["hash"])) not in existing_keys)
        rows_added += added
        files_written.append(str(path))
        market_polls.append(
            {
                "condition_id": condition_id,
                "asset_id": token_id,
                "status": "ok",
                "rows_added": added,
                "error": "",
            }
        )

    successful = sum(row["status"] == "ok" for row in market_polls)
    status = "ok" if successful == len(watchlist) else ("partial" if successful else "failed")
    summary.update(
        {
            "status": status,
            "markets_polled": len(watchlist),
            "markets_succeeded": successful,
            "rows_added": rows_added,
            "files_written": files_written,
            "market_polls": market_polls,
            "batch_fallback_reason": batch_error,
            "errors": errors[:10],
            "note": (
                "Current official CLOB books for exactly the maker quote-sheet portfolio. "
                "Repeated point-in-time snapshots form the replay archive; no orders or gates are touched."
            ),
        }
    )
    write_json(summary_path, summary)
    return summary


def collect_maker_replay_data(cfg: EngineConfig) -> dict[str, Any]:
    """Collect matched book/print observations for the current quote sheet."""

    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_replay_collection.json"
    ledger_path = out_root / "maker_replay_collection_windows.csv"
    generated_at = now_utc()
    maker_summary = read_json(out_root / "maker_carry_study.json", default={}) or {}
    if not isinstance(maker_summary, dict):
        maker_summary = {}
    portfolio = _portfolio(maker_summary, _candidate_map(cfg), int(settings["max_markets"]))
    base: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-83",
        "portfolio_generated_at_utc": str(maker_summary.get("generated_at_utc") or ""),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, base)
        return base
    if not portfolio:
        # Churn gap: the current quote sheet is empty, but snapshot_official_books
        # still refreshes recently-active (persistent) markets via its watchlist,
        # so Tier-0 coverage keeps maturing across the gap. Previously this
        # early return skipped the snapshot entirely on the scheduled path.
        book_summary = snapshot_official_books(cfg)
        payload = {
            **base,
            "status": "no_portfolio",
            "markets_polled": int(book_summary.get("markets_polled") or 0),
            "windows_covered": 0,
            "persistent_snapshot_status": book_summary.get("status"),
        }
        write_json(summary_path, payload)
        return payload

    book_summary = snapshot_official_books(cfg)
    trade_summary = collect_maker_portfolio_trade_prints(cfg)
    books = {
        str(row.get("condition_id") or ""): row
        for row in (book_summary.get("market_polls") or [])
        if isinstance(row, dict)
    }
    prints = {
        str(row.get("condition_id") or ""): row
        for row in (trade_summary.get("market_polls") or [])
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for entry in portfolio:
        condition_id = str(entry["condition_id"])
        book = books.get(condition_id, {})
        trade = prints.get(condition_id, {})
        covered = book.get("status") == "ok" and trade.get("status") == "ok"
        rows.append(
            {
                "window_id": generated_at,
                "condition_id": condition_id,
                "asset_id": entry["token_id"],
                "portfolio_generated_at_utc": str(maker_summary.get("generated_at_utc") or ""),
                "collected_at_utc": generated_at,
                "quote_bid_price": entry.get("quote_bid_price", ""),
                "quote_ask_price": entry.get("quote_ask_price", ""),
                "quote_size_shares": entry.get("quote_size_shares", ""),
                "book_poll_status": str(book.get("status") or "not_observed"),
                "book_snapshot_rows": int(safe_float(book.get("rows_added")) or 0),
                "trade_poll_status": str(trade.get("status") or "not_observed"),
                "trade_prints_returned": int(safe_float(trade.get("prints_returned")) or 0),
                "covered": covered,
            }
        )

    existing = read_csv_rows(ledger_path)
    dedup = {
        (str(row.get("window_id") or ""), str(row.get("condition_id") or "")): row
        for row in [*existing, *rows]
        if row.get("window_id") and row.get("condition_id")
    }
    combined = sorted(dedup.values(), key=lambda row: str(row.get("collected_at_utc") or ""))
    max_rows = int(settings["max_collection_window_rows"])
    if max_rows > 0 and len(combined) > max_rows:
        combined = combined[-max_rows:]
    write_csv(ledger_path, combined, fieldnames=COLLECTION_WINDOW_FIELDS)
    covered_count = sum(bool(row["covered"]) for row in rows)
    payload = {
        **base,
        "status": "ok" if covered_count == len(rows) else ("partial" if covered_count else "failed"),
        "markets_polled": len(rows),
        "windows_simulated": len(rows),
        "windows_covered": covered_count,
        "coverage_ratio": round(covered_count / len(rows), 6) if rows else None,
        "market_windows": rows,
        "book_snapshot": book_summary,
        "trade_print_collection": trade_summary,
        "collection_ledger": str(ledger_path),
        "note": "Matched official-book and public-print polls only; a successful zero-print response is covered.",
    }
    write_json(summary_path, payload)
    return payload


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return round(sum(vals) / len(vals), 6) if vals else None


def _percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    observed = [float(value) for value in values]
    return {
        "count": len(observed),
        "mean": _mean(observed),
        "min": round(min(observed), 6) if observed else None,
        "p25": _percentile(observed, 0.25),
        "median": _percentile(observed, 0.5),
        "p75": _percentile(observed, 0.75),
        "p95": _percentile(observed, 0.95),
        "max": round(max(observed), 6) if observed else None,
    }


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _coverage_slice(rows: list[dict[str, Any]], portfolio: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, dict[str, Any]] = {
        str(entry["condition_id"]): {
            "condition_id": str(entry["condition_id"]),
            "asset_id": str(entry["token_id"]),
            "question": str(entry.get("question") or ""),
            "windows_simulated": 0,
            "windows_covered": 0,
        }
        for entry in portfolio
    }
    for row in rows:
        condition_id = str(row.get("condition_id") or "")
        target = by_condition.get(condition_id)
        if target is None:
            continue
        target["windows_simulated"] += 1
        target["windows_covered"] += int(_truthy(row.get("covered")))
    per_market = []
    for target in by_condition.values():
        simulated = int(target["windows_simulated"])
        covered = int(target["windows_covered"])
        target["coverage_ratio"] = round(covered / simulated, 6) if simulated else None
        per_market.append(target)
    simulated_total = sum(int(row["windows_simulated"]) for row in per_market)
    covered_total = sum(int(row["windows_covered"]) for row in per_market)
    if simulated_total == 0:
        status = "not_observed"
    elif covered_total == simulated_total:
        status = "covered"
    elif covered_total:
        status = "partial"
    else:
        status = "insufficient_coverage"
    return {
        "status": status,
        "windows_simulated": simulated_total,
        "windows_covered": covered_total,
        "coverage_ratio": round(covered_total / simulated_total, 6) if simulated_total else None,
        "per_market": per_market,
    }


def _collection_coverage(cfg: EngineConfig, portfolio: list[dict[str, Any]], regime_days: float) -> dict[str, Any]:
    current = {str(entry["condition_id"]) for entry in portfolio}
    rows = [
        row
        for row in _iter_csv_any(
            cfg.output_root / "maker_carry" / "maker_replay_collection_windows.csv"
        )
        if str(row.get("condition_id") or "") in current
    ]
    stamps = [stamp for row in rows if (stamp := _stamp(row.get("collected_at_utc"))) is not None]
    cutoff = max(stamps) - regime_days * 86400.0 if stamps else None
    recent = [row for row in rows if cutoff is not None and (_stamp(row.get("collected_at_utc")) or 0.0) >= cutoff]
    prior = [row for row in rows if cutoff is not None and (_stamp(row.get("collected_at_utc")) or 0.0) < cutoff]
    return {
        **_coverage_slice(rows, portfolio),
        "regime_cut": {
            "cutoff_utc": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff)) if cutoff is not None else None
            ),
            "last_7_days": _coverage_slice(recent, portfolio),
            "prior_to_last_7_days": _coverage_slice(prior, portfolio),
        },
    }


def _study_charge_by_condition(
    cfg: EngineConfig, portfolio: list[dict[str, Any]]
) -> dict[str, float]:
    candidates = _candidate_map(cfg)
    charges: dict[str, float] = {}
    for entry in portfolio:
        condition_id = str(entry["condition_id"])
        candidate = candidates.get(condition_id, {})
        charge = safe_float(candidate.get("adverse_selection_usd_per_day")) or 0.0
        size_multiple = safe_float(entry.get("size_multiple")) or 1.0
        charges[condition_id] = charges.get(condition_id, 0.0) + charge * size_multiple
    return charges


def _replay_against_states(
    *,
    source: str,
    states_by_token: dict[str, list[dict[str, Any]]],
    trades: list[dict[str, Any]],
    portfolio: list[dict[str, Any]],
    study_charge: float,
    study_charge_by_condition: dict[str, float],
    max_state_lag_seconds: float,
    start_stamp: float | None = None,
    end_stamp: float | None = None,
) -> dict[str, Any]:
    portfolio_by_token = {row["token_id"]: row for row in portfolio}
    coverage_by_token: dict[str, dict[str, Any]] = {
        str(row["token_id"]): {
            "condition_id": str(row["condition_id"]),
            "asset_id": str(row["token_id"]),
            "question": str(row.get("question") or ""),
            "simulated_fill_opportunities": 0,
            "last_in_queue_evaluable_opportunities": 0,
            "queue_depth_unavailable_opportunities": 0,
            "confirmed_fills": 0,
            "windows_simulated": 0,
            "windows_covered": 0,
            "book_history_span_days": 0.0,
            "by_horizon": {
                f"{horizon}m": {"windows_simulated": 0, "windows_covered": 0}
                for horizon in HORIZONS_MINUTES
            },
        }
        for row in portfolio
    }
    # WO-113: record the observed official-book span per market so audits can see
    # how deep the archive is next to the (window-aligned) coverage ratio.
    for _token, _cov in coverage_by_token.items():
        _states = states_by_token.get(_token) or []
        if len(_states) >= 2:
            _cov["book_history_span_days"] = round(
                (_states[-1]["stamp"] - _states[0]["stamp"]) / 86400.0, 4
            )
    fills: list[dict[str, Any]] = []
    simulated_fill_opportunities = 0
    last_in_queue_evaluable_opportunities = 0
    relevant_trades: list[dict[str, Any]] = []
    for trade in trades:
        if start_stamp is not None and trade["stamp"] < start_stamp:
            continue
        if end_stamp is not None and trade["stamp"] >= end_stamp:
            continue
        entry = portfolio_by_token.get(trade["token_id"])
        if entry is None:
            continue
        relevant_trades.append(trade)
        states = states_by_token.get(trade["token_id"]) or []
        state = _state_at_or_before(states, trade["stamp"])
        if state is not None and trade["stamp"] - state["stamp"] > max_state_lag_seconds:
            state = None
        bid_quote = safe_float(entry.get("quote_bid_price"))
        ask_quote = safe_float(entry.get("quote_ask_price"))
        if (bid_quote is None or ask_quote is None) and state is not None:
            bid_quote = state["midpoint"] - float(entry["quote_distance"])
            ask_quote = state["midpoint"] + float(entry["quote_distance"])
        if bid_quote is None or ask_quote is None or ask_quote <= bid_quote:
            continue
        if trade["side"] == "SELL" and trade["price"] <= bid_quote:
            fill_price = bid_quote
            direction = "bid_fill"
        elif trade["side"] == "BUY" and trade["price"] >= ask_quote:
            fill_price = ask_quote
            direction = "ask_fill"
        else:
            continue
        simulated_fill_opportunities += 1
        market_coverage = coverage_by_token[str(entry["token_id"])]
        market_coverage["simulated_fill_opportunities"] += 1
        later_by_horizon: dict[int, dict[str, float]] = {}
        observed_last = states[-1]["stamp"] if states else None
        for horizon in HORIZONS_MINUTES:
            key = f"{horizon}m"
            target_stamp = trade["stamp"] + horizon * 60.0
            # WO-113 coverage-window alignment: only windows the OBSERVED official-
            # book history can bracket enter the denominator. A window needs a
            # book state at/before the fill (queue depth, `state`) AND observed
            # history reaching the markout horizon (`observed_last >= target`).
            # Fills outside that span are physically unmeasurable, so counting
            # them as "simulated-but-uncovered" made the 80% coverage minimum
            # unreachable for weeks after a market entered the portfolio.
            if state is None or observed_last is None or observed_last < target_stamp:
                continue
            market_coverage["windows_simulated"] += 1
            market_coverage["by_horizon"][key]["windows_simulated"] += 1
            later = _state_at_or_after(states, target_stamp)
            if later is None or later["stamp"] - target_stamp > max_state_lag_seconds:
                continue
            later_by_horizon[horizon] = later
            market_coverage["windows_covered"] += 1
            market_coverage["by_horizon"][key]["windows_covered"] += 1

        if state is None:
            market_coverage["queue_depth_unavailable_opportunities"] += 1
            continue
        depth_ahead, queue_depth_source = _queue_depth_at_quote(
            state,
            direction=direction,
            quote=fill_price,
        )
        if depth_ahead is None:
            market_coverage["queue_depth_unavailable_opportunities"] += 1
            continue
        last_in_queue_evaluable_opportunities += 1
        market_coverage["last_in_queue_evaluable_opportunities"] += 1
        fillable = trade["size"] - depth_ahead
        if fillable <= 0:
            continue
        fill_size = min(float(entry["quote_size_shares"]), fillable)
        if fill_size <= 0:
            continue
        market_coverage["confirmed_fills"] += 1
        markouts: dict[str, float] = {}
        adverse_usd: dict[str, float] = {}
        for horizon in HORIZONS_MINUTES:
            later = later_by_horizon.get(horizon)
            if later is None:
                continue
            if direction == "bid_fill":
                per_share = fill_price - later["midpoint"]
            else:
                per_share = later["midpoint"] - fill_price
            markouts[f"{horizon}m"] = round(per_share, 6)
            adverse_usd[f"{horizon}m"] = round(per_share * fill_size, 6)
        fills.append(
            {
                "source": source,
                "condition_id": entry["condition_id"],
                "token_id": entry["token_id"],
                "direction": direction,
                "stamp": trade["stamp"],
                "fill_price": round(fill_price, 6),
                "fill_size": round(fill_size, 6),
                "depth_ahead": round(depth_ahead, 6),
                "queue_depth_source": queue_depth_source,
                "trade_size": trade["size"],
                "markout_per_share": markouts,
                "adverse_usd": adverse_usd,
            }
        )

    all_stamps = [
        row["stamp"]
        for rows in states_by_token.values()
        for row in rows
        if (start_stamp is None or row["stamp"] >= start_stamp - max_state_lag_seconds)
        and (end_stamp is None or row["stamp"] < end_stamp + 60 * max(HORIZONS_MINUTES) + max_state_lag_seconds)
    ]
    if not all_stamps:
        all_stamps = [trade["stamp"] for trade in relevant_trades]
    span_days = max((max(all_stamps) - min(all_stamps)) / 86400.0, 1.0 / 1440.0) if all_stamps else 1.0
    adverse_5m = sum((fill.get("adverse_usd") or {}).get("5m", 0.0) for fill in fills)
    realized_adverse = round(max(0.0, adverse_5m / span_days), 6)

    per_market_coverage: list[dict[str, Any]] = []
    for row in coverage_by_token.values():
        opportunities = int(row["simulated_fill_opportunities"])
        evaluable = int(row["last_in_queue_evaluable_opportunities"])
        confirmed = int(row["confirmed_fills"])
        simulated_windows = int(row["windows_simulated"])
        covered_windows = int(row["windows_covered"])
        for horizon in HORIZONS_MINUTES:
            horizon_row = row["by_horizon"][f"{horizon}m"]
            denominator = int(horizon_row["windows_simulated"])
            horizon_row["coverage_ratio"] = (
                round(int(horizon_row["windows_covered"]) / denominator, 6) if denominator else None
            )
        haircut_windows = int(row["by_horizon"]["5m"]["windows_covered"])
        row["confirmed_fill_ratio"] = round(confirmed / evaluable, 6) if evaluable else None
        row["confirmed_fill_ratio_status"] = "observed" if evaluable else (
            "insufficient_coverage" if opportunities else "no_simulated_fill_opportunities"
        )
        row["coverage_ratio"] = round(covered_windows / simulated_windows, 6) if simulated_windows else None
        if opportunities and haircut_windows == 0:
            row["coverage_status"] = "insufficient_coverage"
        elif opportunities and covered_windows < simulated_windows:
            row["coverage_status"] = "partial"
        elif opportunities:
            row["coverage_status"] = "covered"
        else:
            row["coverage_status"] = "no_simulated_fill_opportunities"
        token_fills = [
            fill for fill in fills if str(fill.get("token_id") or "") == str(row["asset_id"])
        ]
        row["realized_markout_distribution"] = {
            f"{horizon}m": _distribution(
                (fill.get("markout_per_share") or {}).get(f"{horizon}m")
                for fill in token_fills
                if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
            )
            for horizon in HORIZONS_MINUTES
        }
        market_adverse_5m = sum(
            (fill.get("adverse_usd") or {}).get("5m", 0.0) for fill in token_fills
        )
        market_stamps = [
            state["stamp"] for state in states_by_token.get(str(row["asset_id"]), [])
        ]
        market_stamps.extend(
            trade["stamp"]
            for trade in relevant_trades
            if str(trade.get("token_id") or "") == str(row["asset_id"])
        )
        market_span_days = (
            max((max(market_stamps) - min(market_stamps)) / 86400.0, 1.0 / 1440.0)
            if market_stamps
            else 1.0
        )
        market_realized_adverse = round(
            max(0.0, market_adverse_5m / market_span_days), 6
        )
        market_study_charge = round(
            float(study_charge_by_condition.get(str(row["condition_id"]), 0.0)), 6
        )
        row["realized_adverse_usd_per_day"] = market_realized_adverse
        row["simulated_adverse_charge_usd_per_day"] = market_study_charge
        row["replay_span_days"] = round(market_span_days, 6)
        row["simulation_to_reality_haircut"] = (
            round(market_realized_adverse / market_study_charge, 6)
            if market_study_charge > 0 and haircut_windows > 0
            else None
        )
        per_market_coverage.append(row)

    windows_simulated = sum(int(row["windows_simulated"]) for row in per_market_coverage)
    windows_covered = sum(int(row["windows_covered"]) for row in per_market_coverage)
    by_horizon = {
        f"{horizon}m": {
            "windows_simulated": sum(
                int(row["by_horizon"][f"{horizon}m"]["windows_simulated"])
                for row in per_market_coverage
            ),
            "windows_covered": sum(
                int(row["by_horizon"][f"{horizon}m"]["windows_covered"])
                for row in per_market_coverage
            ),
        }
        for horizon in HORIZONS_MINUTES
    }
    for row in by_horizon.values():
        row["coverage_ratio"] = (
            round(row["windows_covered"] / row["windows_simulated"], 6)
            if row["windows_simulated"]
            else None
        )
    haircut_coverage = int(by_horizon["5m"]["windows_covered"])
    insufficient = simulated_fill_opportunities > 0 and haircut_coverage == 0
    if insufficient:
        coverage_status = "insufficient_coverage"
        haircut: float | str | None = "insufficient_coverage"
    else:
        coverage_status = (
            "covered"
            if windows_simulated and windows_covered == windows_simulated
            else ("partial" if windows_covered else "no_simulated_fill_opportunities")
        )
        haircut = round(realized_adverse / study_charge, 6) if study_charge > 0 else None

    markout_distribution = {
        f"{horizon}m": _distribution(
            (fill.get("markout_per_share") or {}).get(f"{horizon}m")
            for fill in fills
            if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
        )
        for horizon in HORIZONS_MINUTES
    }
    return {
        "status": "insufficient_coverage" if insufficient else "ok",
        "source": source,
        "book_states": sum(len(rows) for rows in states_by_token.values()),
        "simulated_fill_opportunities": simulated_fill_opportunities,
        "last_in_queue_evaluable_opportunities": last_in_queue_evaluable_opportunities,
        "queue_depth_unavailable_opportunities": (
            simulated_fill_opportunities - last_in_queue_evaluable_opportunities
        ),
        "queue_depth_standard": "full_book_levels_or_quote_aligned_depth_only",
        "confirmed_fills": len(fills),
        "confirmed_fill_ratio": (
            round(len(fills) / last_in_queue_evaluable_opportunities, 6)
            if last_in_queue_evaluable_opportunities
            else None
        ),
        "confirmed_fill_ratio_status": (
            "observed"
            if last_in_queue_evaluable_opportunities
            else ("insufficient_coverage" if simulated_fill_opportunities else "no_simulated_fill_opportunities")
        ),
        # Backward-compatible names: these have always represented fills that
        # survive the last-in-queue depth test, not all crossing opportunities.
        "simulated_fills": len(fills),
        "simulated_fills_per_day": round(len(fills) / span_days, 6),
        "markout_per_fill": {
            f"{horizon}m": _mean(
                (fill.get("markout_per_share") or {}).get(f"{horizon}m")
                for fill in fills
                if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
            )
            for horizon in HORIZONS_MINUTES
        },
        "realized_markout_distribution": markout_distribution,
        "realized_adverse_usd_per_day": realized_adverse,
        "simulated_adverse_charge_usd_per_day": round(study_charge, 6),
        "simulation_to_reality_haircut": haircut,
        "coverage_status": coverage_status,
        "coverage": {
            "windows_simulated": windows_simulated,
            "windows_covered": windows_covered,
            "coverage_ratio": round(windows_covered / windows_simulated, 6) if windows_simulated else None,
            "by_horizon": by_horizon,
        },
        "per_market_coverage": per_market_coverage,
        "implied_adverse_usd_per_day": realized_adverse,
        "study_adverse_usd_per_day": round(study_charge, 6),
        "realism_ratio": haircut,
        "fills_preview": fills[:20],
    }


def run_maker_fill_replay(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_fill_replay.json"
    generated_at = now_utc()
    payload: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-40/WO-44/WO-83",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, payload)
        return payload

    maker_summary = read_json(out_root / "maker_carry_study.json", default={}) or {}
    if not isinstance(maker_summary, dict):
        maker_summary = {}
    payload["portfolio_generated_at_utc"] = str(maker_summary.get("generated_at_utc") or "")
    portfolio = _portfolio(maker_summary, _candidate_map(cfg), int(settings["max_markets"]))
    if not portfolio:
        payload.update(
            {
                "status": "no_portfolio",
                "portfolio_markets": 0,
                "simulated_fills": 0,
                "simulated_fills_per_day": 0.0,
                "implied_adverse_usd_per_day": 0.0,
                "realism_ratio": None,
                "simulation_to_reality_haircut": None,
                "coverage_status": "no_portfolio",
                "note": "No current maker-carry quote-sheet portfolio with token IDs was available to replay.",
            }
        )
        write_json(summary_path, payload)
        return payload

    token_ids = {row["token_id"] for row in portfolio}
    markets = {row["condition_id"] for row in portfolio}
    trades = _trades(cfg, markets, token_ids)
    collection_coverage = _collection_coverage(cfg, portfolio, float(settings["regime_days"]))
    requested_source = str(settings.get("book_source") or "both").strip().lower()
    if requested_source not in {"archive", "official", "both"}:
        requested_source = "both"
    states_by_source: dict[str, dict[str, list[dict[str, float]]]] = {}
    if requested_source in {"archive", "both"}:
        states_by_source["archive"] = _book_states(cfg, token_ids, 0.0)
    if requested_source in {"official", "both"}:
        states_by_source["official"] = _official_book_states(cfg, token_ids, 0.0)
    if not trades:
        payload.update(
            {
                "status": "no_replay_data",
                "portfolio_markets": len(portfolio),
                "simulated_fills": 0,
                "simulated_fills_per_day": 0.0,
                "implied_adverse_usd_per_day": 0.0,
                "realism_ratio": None,
                "simulation_to_reality_haircut": None,
                "coverage_status": "no_trade_prints",
                "collection_coverage": collection_coverage,
                "note": "No public trade prints were recorded for the current quote-sheet portfolio.",
            }
        )
        write_json(summary_path, payload)
        return payload

    study_charge_by_condition = _study_charge_by_condition(cfg, portfolio)
    study_charge = sum(study_charge_by_condition.values())
    max_state_lag = float(settings["max_book_state_lag_seconds"])
    source_results = {
        source: _replay_against_states(
            source=source,
            states_by_token=states,
            trades=trades,
            portfolio=portfolio,
            study_charge=study_charge,
            study_charge_by_condition=study_charge_by_condition,
            max_state_lag_seconds=max_state_lag,
        )
        for source, states in states_by_source.items()
    }
    if requested_source == "archive":
        primary_source = "archive"
    else:
        # WO-83: archive evidence remains diagnostic, but it may not mask a
        # missing official-book validation window.
        primary_source = "official"
    primary = source_results[primary_source]
    source_agreement = None
    if "archive" in source_results and "official" in source_results:
        archive_fills = float(source_results["archive"]["simulated_fills_per_day"])
        official_fills = float(source_results["official"]["simulated_fills_per_day"])
        source_agreement = {
            "archive_fills_per_day": archive_fills,
            "official_fills_per_day": official_fills,
            "fills_per_day_divergence": round(abs(archive_fills - official_fills), 6),
        }

    observed_stamps = [trade["stamp"] for trade in trades]
    observed_stamps.extend(
        row["stamp"] for states in states_by_source.values() for rows in states.values() for row in rows
    )
    cutoff = max(observed_stamps) - float(settings["regime_days"]) * 86400.0
    regime_cut_by_source = {
        source: {
            "last_7_days": _replay_against_states(
                source=source,
                states_by_token=states,
                trades=trades,
                portfolio=portfolio,
                study_charge=study_charge,
                study_charge_by_condition=study_charge_by_condition,
                max_state_lag_seconds=max_state_lag,
                start_stamp=cutoff,
            ),
            "prior_to_last_7_days": _replay_against_states(
                source=source,
                states_by_token=states,
                trades=trades,
                portfolio=portfolio,
                study_charge=study_charge,
                study_charge_by_condition=study_charge_by_condition,
                max_state_lag_seconds=max_state_lag,
                end_stamp=cutoff,
            ),
        }
        for source, states in states_by_source.items()
    }
    cutoff_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
    payload.update(
        {
            "status": primary["status"],
            "portfolio_markets": len(portfolio),
            "replay_days": float(settings["regime_days"]),
            "book_source_requested": requested_source,
            "primary_book_source": primary_source,
            "available_book_sources": sorted(
                source for source, states in states_by_source.items() if any(states.values())
            ),
            "evaluated_book_sources": sorted(source_results),
            "official_snapshot": read_json(out_root / "official_book_snapshot.json", default={}) or {},
            "book_states": primary["book_states"],
            "trade_prints_seen": len(trades),
            "simulated_fill_opportunities": primary["simulated_fill_opportunities"],
            "last_in_queue_evaluable_opportunities": primary[
                "last_in_queue_evaluable_opportunities"
            ],
            "confirmed_fills": primary["confirmed_fills"],
            "confirmed_fill_ratio": primary["confirmed_fill_ratio"],
            "confirmed_fill_ratio_status": primary["confirmed_fill_ratio_status"],
            "simulated_fills": primary["simulated_fills"],
            "simulated_fills_per_day": primary["simulated_fills_per_day"],
            "markout_per_fill": primary["markout_per_fill"],
            "realized_markout_distribution": primary["realized_markout_distribution"],
            "realized_adverse_usd_per_day": primary["realized_adverse_usd_per_day"],
            "simulated_adverse_charge_usd_per_day": primary["simulated_adverse_charge_usd_per_day"],
            "simulation_to_reality_haircut": primary["simulation_to_reality_haircut"],
            "coverage_status": primary["coverage_status"],
            "coverage": primary["coverage"],
            "per_market_coverage": primary["per_market_coverage"],
            "collection_coverage": collection_coverage,
            "implied_adverse_usd_per_day": primary["implied_adverse_usd_per_day"],
            "study_adverse_usd_per_day": primary["study_adverse_usd_per_day"],
            "realism_ratio": primary["realism_ratio"],
            "realism_ratio_by_source": {source: result["realism_ratio"] for source, result in source_results.items()},
            "source_results": source_results,
            "source_agreement": source_agreement,
            "regime_cut": {
                "cutoff_utc": cutoff_utc,
                "last_7_days": regime_cut_by_source[primary_source]["last_7_days"],
                "prior_to_last_7_days": regime_cut_by_source[primary_source]["prior_to_last_7_days"],
                "by_source": regime_cut_by_source,
            },
            "haircut_policy": {
                "reported_only": True,
                "automatically_applied_to_gate": False,
                "tightening_requires": "dated M-B governance amendment",
                "permitted_direction": "tighten_only",
                "gate_changed_by_this_report": False,
            },
            "fills_preview": primary["fills_preview"],
            "note": (
                "Tier-0 last-in-queue replay. A numeric haircut above 1 means the maker-carry study may "
                "undercharge adverse selection. The haircut is reporting-only and can only support a future "
                "dated tighten-only amendment; it never changes M-B automatically."
            ),
        }
    )
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_maker_fill_replay(load_config(config_path))
