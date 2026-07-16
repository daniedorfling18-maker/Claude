"""Collect public Polymarket trade prints (time & sales) for tracked markets.

Quotes say what the market offered; prints say what it DID. Signed trade flow
(price, size, aggressor side) is the strongest microstructure feature family
and the basis for empirical fill/slippage modelling (verdict Gate B), yet
until 2026-07-09 nothing in the system captured executed trades at all.

Source: the public data-API ``/trades`` endpoint (no API key, no odds
credits). Markets to poll are taken from the live websocket feature table -
the same markets the collector is already researching - so this adds no new
market-selection logic. Prints are appended deduplicated to a CSV ledger and
roll into the compressed training archive above a row cap.

Collection only: no labels, no gates, no trading behaviour of any kind.
"""
from __future__ import annotations

from pathlib import Path
import time
from typing import Any

import requests

from .config import EngineConfig, load_config
from .runtime_lock import runtime_lock
from .utils import (
    normalize_external_timestamp,
    now_utc,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
    write_text_atomic,
)

DEFAULT_BASE_URL = "https://data-api.polymarket.com"

PRINT_FIELDS = [
    "trade_id",
    "market",
    "asset_id",
    "wallet",
    "side",
    "price",
    "size",
    "timestamp",
    "collected_at_utc",
    "title",
    "market_slug",
    "event_slug",
    "outcome",
    "outcome_index",
]

PRINT_METADATA_FIELDS = (
    "wallet",
    "title",
    "market_slug",
    "event_slug",
    "outcome",
    "outcome_index",
)
PRINT_IMMUTABLE_FIELDS = ("market", "asset_id", "side", "price", "size", "timestamp")
BACKFILL_COMPLETION_FILENAME = "backfill_completed_markets_wallet_v2.txt"

OI_FIELDS = [
    "market",
    "open_interest",
    "timestamp",
    "collected_at_utc",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("trade_prints", {}) if isinstance(cfg.raw.get("trade_prints"), dict) else {}
    merged = {
        "enabled": True,
        "base_url": DEFAULT_BASE_URL,
        "max_markets": 60,
        "limit_per_market": 500,
        "request_timeout_seconds": 20,
        "max_ledger_rows": 200000,
        "open_interest_enabled": True,
        # WO-54: one-shot deep backfill for maker-study markets. Collection
        # only; the ledger is training substrate, never a trading signal.
        "backfill_max_prints_per_market": 5000,
        "backfill_limit_per_page": 500,
        "backfill_request_pause_seconds": 0.1,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _tracked_markets(cfg: EngineConfig, max_markets: int) -> list[str]:
    """Markets currently in the websocket feature table, most recent first."""
    features_path = cfg.output_root / "polymarket_websocket" / "websocket_features.csv"
    markets: dict[str, None] = {}
    for row in reversed(read_csv_rows(features_path)):
        market = str(row.get("market") or "").strip()
        if market and market not in markets:
            markets[market] = None
        if len(markets) >= max_markets:
            break
    return list(markets)


def _timestamp_text(value: Any) -> str:
    stamp = normalize_external_timestamp(value)
    if stamp is None:
        return ""
    return str(int(stamp)) if stamp.is_integer() else str(stamp)


def _print_row(trade: dict[str, Any], market: str) -> dict[str, Any] | None:
    trade_id = str(
        trade.get("id")
        or trade.get("transactionHash")
        or trade.get("transaction_hash")
        or ""
    ).strip()
    price = safe_float(trade.get("price"))
    size = safe_float(trade.get("size"))
    if not trade_id or price is None or size is None:
        return None
    return {
        "trade_id": trade_id,
        "market": str(trade.get("market") or trade.get("conditionId") or market),
        "asset_id": str(trade.get("asset") or trade.get("asset_id") or trade.get("outcomeIndex") or ""),
        "wallet": str(
            trade.get("proxyWallet")
            or trade.get("proxy_wallet")
            or trade.get("wallet")
            or trade.get("address")
            or ""
        ).strip().lower(),
        "side": str(trade.get("side") or "").upper(),
        "price": price,
        "size": size,
        "timestamp": _timestamp_text(trade.get("timestamp") or trade.get("matchTime")),
        "collected_at_utc": now_utc(),
        "title": str(trade.get("title") or trade.get("question") or "").strip(),
        "market_slug": str(trade.get("slug") or trade.get("marketSlug") or trade.get("market_slug") or "").strip(),
        "event_slug": str(trade.get("eventSlug") or trade.get("event_slug") or "").strip(),
        "outcome": str(trade.get("outcome") or "").strip(),
        "outcome_index": str(
            trade.get("outcomeIndex") if trade.get("outcomeIndex") is not None else trade.get("outcome_index") or ""
        ).strip(),
    }


def _immutable_value_equal(field: str, left: Any, right: Any) -> bool:
    if left in (None, "") or right in (None, ""):
        return left in (None, "") and right in (None, "")
    if field in {"price", "size"}:
        left_number = safe_float(left)
        right_number = safe_float(right)
        return left_number is not None and right_number is not None and abs(left_number - right_number) <= 1e-12
    if field == "side":
        return str(left).strip().upper() == str(right).strip().upper()
    return str(left).strip() == str(right).strip()


def _merge_print_rows(
    existing: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int, int]:
    """Append unseen IDs and fill blank metadata without revising trade facts."""

    combined = [dict(row) for row in existing]
    index = {str(row.get("trade_id") or ""): row for row in combined if str(row.get("trade_id") or "")}
    new_prints = 0
    enriched_existing = 0
    immutable_conflicts = 0
    for candidate in incoming:
        trade_id = str(candidate.get("trade_id") or "").strip()
        if not trade_id:
            continue
        current = index.get(trade_id)
        if current is None:
            current = dict(candidate)
            combined.append(current)
            index[trade_id] = current
            new_prints += 1
            continue
        if any(
            not _immutable_value_equal(field, current.get(field), candidate.get(field))
            for field in PRINT_IMMUTABLE_FIELDS
        ):
            immutable_conflicts += 1
            continue
        changed = False
        for field in PRINT_METADATA_FIELDS:
            if current.get(field) in (None, "") and candidate.get(field) not in (None, ""):
                current[field] = candidate[field]
                changed = True
        if changed:
            enriched_existing += 1
    return combined, new_prints, enriched_existing, immutable_conflicts


def _commit_print_rows(
    cfg: EngineConfig,
    *,
    ledger_path: Path,
    incoming: list[dict[str, Any]],
    max_rows: int,
) -> tuple[list[dict[str, Any]], int, int, int, bool]:
    """Serialize only the short read/merge/replace section across collectors."""

    with runtime_lock(cfg, "trade_print_ledger_commit", stale_after_seconds=300) as lock:
        if not lock.acquired:
            return read_csv_rows(ledger_path), 0, 0, 0, True
        existing = read_csv_rows(ledger_path)
        combined, new_prints, enriched_existing, immutable_conflicts = _merge_print_rows(existing, incoming)
        if max_rows > 0 and len(combined) > max_rows:
            # Oldest rows roll into the same compressed training archive used
            # by the websocket feature substrate, then leave the live table.
            overflow = combined[:-max_rows]
            combined = combined[-max_rows:]
            from .websocket_normaliser import _archive_expiring_features

            _archive_expiring_features(
                cfg,
                {"training_archive_enabled": True, "training_archive_max_mb": 1024},
                [{"collected_at_utc": row.get("collected_at_utc"), **row} for row in overflow],
            )
        write_csv(ledger_path, combined, fieldnames=PRINT_FIELDS)
    return combined, new_prints, enriched_existing, immutable_conflicts, False


def _payload_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "openInterest", "open_interest", "oi", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
    return [payload]


def _trade_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("trades", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _open_interest_row(item: dict[str, Any], market: str, collected_at: str) -> dict[str, Any] | None:
    value = None
    for key in ("open_interest", "openInterest", "oi", "value", "amount", "total"):
        value = safe_float(item.get(key))
        if value is not None:
            break
    if value is None:
        return None
    return {
        "market": str(item.get("market") or item.get("conditionId") or market),
        "open_interest": value,
        "timestamp": _timestamp_text(item.get("timestamp") or item.get("updatedAt") or item.get("date")),
        "collected_at_utc": collected_at,
    }


def _collect_open_interest(
    *,
    base_url: str,
    timeout: float,
    markets: list[str],
    ledger_path: Path,
    max_rows: int,
) -> tuple[int, int, list[str]]:
    existing = read_csv_rows(ledger_path)
    new_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    collected_at = now_utc()

    for market in markets:
        try:
            response = requests.get(
                f"{base_url}/oi",
                params={"market": market},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            errors.append(f"{market}: {type(exc).__name__}: {exc}")
            continue
        row_added = False
        for item in _payload_items(payload):
            if not isinstance(item, dict):
                continue
            row = _open_interest_row(item, market, collected_at)
            if row is None:
                continue
            new_rows.append(row)
            row_added = True
        if not row_added:
            errors.append(f"{market}: no_open_interest_value")

    combined = [*existing, *new_rows]
    if max_rows > 0 and len(combined) > max_rows:
        combined = combined[-max_rows:]
    write_csv(ledger_path, combined, fieldnames=OI_FIELDS)
    captured_markets = len({str(row.get("market") or "") for row in new_rows if row.get("market")})
    return captured_markets, len(combined), errors


def _maker_portfolio_markets(cfg: EngineConfig, max_markets: int) -> list[str]:
    """Condition IDs on the current maker quote sheet, in sheet order."""

    study = read_json(cfg.output_root / "maker_carry" / "maker_carry_study.json", default={}) or {}
    portfolio = study.get("portfolio") if isinstance(study, dict) and isinstance(study.get("portfolio"), list) else []
    markets: dict[str, None] = {}
    for row in portfolio:
        if not isinstance(row, dict):
            continue
        market = str(row.get("condition_id") or row.get("market") or "").strip()
        if market:
            markets[market] = None
        if len(markets) >= max_markets:
            break
    return list(markets)


def collect_trade_prints(
    cfg: EngineConfig,
    *,
    markets: list[str] | None = None,
    market_scope: str = "websocket",
    summary_filename: str = "trade_prints_summary.json",
    collect_open_interest: bool = True,
) -> dict[str, Any]:
    settings = _settings(cfg)
    ledger_path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    oi_path = cfg.output_root / "polymarket_trade_prints" / "open_interest_history.csv"
    summary_path = cfg.output_root / "polymarket_trade_prints" / summary_filename
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "markets_polled": 0,
        "new_prints": 0,
        "enriched_existing_prints": 0,
        "immutable_conflicts": 0,
        "wallet_rows_observed": 0,
        "wallet_rows_persisted": 0,
        "ledger_rows": 0,
        "oi_markets_captured": 0,
        "oi_ledger_rows": 0,
        "oi_errors": [],
        "errors": [],
        "market_scope": market_scope,
        "market_polls": [],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    if markets is None:
        markets = _tracked_markets(cfg, int(settings["max_markets"]))
    else:
        markets = list(dict.fromkeys(str(market).strip() for market in markets if str(market).strip()))[
            : int(settings["max_markets"])
        ]
    base_url = str(settings["base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    max_rows = int(safe_float(settings.get("max_ledger_rows")) or 0)
    observed_rows: list[dict[str, Any]] = []
    observed_index: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    market_polls: list[dict[str, Any]] = []

    for market in markets:
        market_new_before = len(observed_index)
        try:
            response = requests.get(
                f"{base_url}/trades",
                params={"market": market, "limit": int(settings["limit_per_market"])},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            errors.append(f"{market}: {type(exc).__name__}: {exc}")
            market_polls.append(
                {
                    "condition_id": market,
                    "status": "failed",
                    "prints_returned": 0,
                    "new_prints": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        trades = _trade_items(payload)
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            row = _print_row(trade, market)
            if row is None:
                continue
            trade_id = str(row["trade_id"])
            existing_observed = observed_index.get(trade_id)
            if existing_observed is None:
                observed_index[trade_id] = row
                observed_rows.append(row)
            else:
                _merge_print_rows([existing_observed], [row])
        market_polls.append(
            {
                "condition_id": market,
                "status": "ok",
                "prints_returned": len(trades),
                "new_prints": len(observed_index) - market_new_before,
                "error": "",
            }
        )

    oi_markets_captured = 0
    oi_ledger_rows = 0
    oi_errors: list[str] = []
    if collect_open_interest and str(settings.get("open_interest_enabled", True)).strip().lower() not in {"0", "false", "no"}:
        oi_markets_captured, oi_ledger_rows, oi_errors = _collect_open_interest(
            base_url=base_url,
            timeout=timeout,
            markets=markets,
            ledger_path=oi_path,
            max_rows=max_rows,
        )

    combined, new_prints, enriched_existing, immutable_conflicts, lock_contended = _commit_print_rows(
        cfg,
        ledger_path=ledger_path,
        incoming=observed_rows,
        max_rows=max_rows,
    )
    if lock_contended:
        errors.append("trade_print_ledger_commit: lock_contended")
    summary.update(
        {
            "status": "ok" if not errors else ("partial" if combined else "failed"),
            "markets_polled": len(markets),
            "new_prints": new_prints,
            "enriched_existing_prints": enriched_existing,
            "immutable_conflicts": immutable_conflicts,
            "wallet_rows_observed": sum(1 for row in observed_rows if row.get("wallet")),
            "wallet_rows_persisted": sum(1 for row in combined if row.get("wallet")),
            "ledger_rows": len(combined),
            "ledger_path": str(ledger_path),
            "oi_markets_captured": oi_markets_captured,
            "oi_ledger_rows": oi_ledger_rows,
            "oi_path": str(oi_path),
            "oi_errors": oi_errors[:10],
            "errors": errors[:10],
            "market_polls": market_polls,
        }
    )
    write_json(summary_path, summary)
    return summary


def collect_maker_portfolio_trade_prints(cfg: EngineConfig) -> dict[str, Any]:
    """Poll prints for exactly the current maker quote-sheet markets.

    This dedicated scope makes a successful zero-print response distinguishable
    from a collection gap. It appends to the canonical print ledger and never
    labels, scores, gates, or trades.
    """

    settings = _settings(cfg)
    markets = _maker_portfolio_markets(cfg, int(settings["max_markets"]))
    return collect_trade_prints(
        cfg,
        markets=markets,
        market_scope="maker_quote_sheet",
        summary_filename="maker_portfolio_trade_prints_summary.json",
        collect_open_interest=False,
    )


def _maker_backfill_markets(cfg: EngineConfig, max_markets: int) -> list[str]:
    markets: dict[str, None] = {}
    for row in read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv"):
        market = str(row.get("condition_id") or row.get("market") or "").strip()
        if market:
            markets[market] = None
    study = read_json(cfg.output_root / "maker_carry" / "maker_carry_study.json", default={}) or {}
    if isinstance(study, dict):
        portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
        for row in portfolio:
            if not isinstance(row, dict):
                continue
            market = str(row.get("condition_id") or row.get("market") or "").strip()
            if market:
                markets[market] = None
    # WO-96 must be able to enrich the already collected tape, not only the
    # current maker sheet. Existing market IDs are collection scope, never a
    # signal or priority change.
    for row in reversed(read_csv_rows(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv")):
        market = str(row.get("market") or "").strip()
        if market:
            markets[market] = None
        if len(markets) >= max_markets:
            break
    return list(markets)[:max_markets]


def _read_completed_backfills(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def _write_completed_backfills(path: Path, markets: set[str]) -> None:
    write_text_atomic(path, "\n".join(sorted(markets)) + ("\n" if markets else ""))


def backfill_trade_prints(cfg: EngineConfig) -> dict[str, Any]:
    """Deep one-shot trade-print backfill for maker-study markets.

    This extends the same public trade-print ledger used by the rolling
    collector. It does not label, score, gate, paper trade, or live trade.
    """

    settings = _settings(cfg)
    ledger_path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    stamp_path = cfg.output_root / "polymarket_trade_prints" / BACKFILL_COMPLETION_FILENAME
    summary_path = cfg.output_root / "polymarket_trade_prints" / "trade_print_backfill_summary.json"
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "candidate_markets": 0,
        "markets_attempted": 0,
        "markets_skipped_completed": 0,
        "markets_completed": 0,
        "new_prints": 0,
        "enriched_existing_prints": 0,
        "immutable_conflicts": 0,
        "wallet_rows_observed": 0,
        "wallet_rows_persisted": 0,
        "ledger_rows": 0,
        "completed_stamp": str(stamp_path),
        "errors": [],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    candidate_markets = _maker_backfill_markets(cfg, int(settings["max_markets"]))
    completed = _read_completed_backfills(stamp_path)
    todo = [market for market in candidate_markets if market not in completed]
    base_url = str(settings["base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    cap = max(1, int(safe_float(settings.get("backfill_max_prints_per_market")) or 5000))
    page_limit = max(1, int(safe_float(settings.get("backfill_limit_per_page")) or 500))
    pause = max(0.1, float(safe_float(settings.get("backfill_request_pause_seconds")) or 0.1))
    max_rows = int(safe_float(settings.get("max_ledger_rows")) or 0)
    observed_rows: list[dict[str, Any]] = []
    observed_index: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    completed_this_run: set[str] = set()
    cap_hit_markets: list[str] = []

    for market in todo:
        fetched = 0
        offset = 0
        market_failed = False
        while fetched < cap:
            limit = min(page_limit, cap - fetched)
            try:
                response = requests.get(
                    f"{base_url}/trades",
                    params={"market": market, "limit": limit, "offset": offset},
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                errors.append(f"{market}: {type(exc).__name__}: {exc}")
                market_failed = True
                break
            trades = _trade_items(payload)
            if not trades:
                break
            fetched += len(trades)
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                row = _print_row(trade, market)
                if row is None:
                    continue
                trade_id = str(row["trade_id"])
                existing_observed = observed_index.get(trade_id)
                if existing_observed is None:
                    observed_index[trade_id] = row
                    observed_rows.append(row)
                else:
                    _merge_print_rows([existing_observed], [row])
            time.sleep(pause)
            if len(trades) < limit:
                break
            offset += limit
        if not market_failed:
            completed_this_run.add(market)
            if fetched >= cap:
                cap_hit_markets.append(market)

    combined, new_prints, enriched_existing, immutable_conflicts, lock_contended = _commit_print_rows(
        cfg,
        ledger_path=ledger_path,
        incoming=observed_rows,
        max_rows=max_rows,
    )
    if lock_contended:
        errors.append("trade_print_ledger_commit: lock_contended")
    completed.update(completed_this_run)
    if not lock_contended:
        _write_completed_backfills(stamp_path, completed)
    status = "ok"
    if errors:
        status = "partial" if completed_this_run or combined else "failed"
    elif not candidate_markets:
        status = "no_candidate_markets"
    elif not todo:
        status = "skipped_all_completed"
    summary.update(
        {
            "status": status,
            "candidate_markets": len(candidate_markets),
            "markets_attempted": len(todo),
            "markets_skipped_completed": len(candidate_markets) - len(todo),
            "markets_completed": len(completed_this_run),
            "cap_hit_markets": cap_hit_markets,
            "new_prints": new_prints,
            "enriched_existing_prints": enriched_existing,
            "immutable_conflicts": immutable_conflicts,
            "wallet_rows_observed": sum(1 for row in observed_rows if row.get("wallet")),
            "wallet_rows_persisted": sum(1 for row in combined if row.get("wallet")),
            "ledger_rows": len(combined),
            "ledger_path": str(ledger_path),
            "errors": errors[:10],
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return collect_trade_prints(load_config(config_path))
