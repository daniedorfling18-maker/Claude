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
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_BASE_URL = "https://data-api.polymarket.com"

PRINT_FIELDS = [
    "trade_id",
    "market",
    "asset_id",
    "side",
    "price",
    "size",
    "timestamp",
    "collected_at_utc",
]

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
        "side": str(trade.get("side") or "").upper(),
        "price": price,
        "size": size,
        "timestamp": str(trade.get("timestamp") or trade.get("matchTime") or ""),
        "collected_at_utc": now_utc(),
    }


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
        "timestamp": str(item.get("timestamp") or item.get("updatedAt") or item.get("date") or ""),
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


def collect_trade_prints(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    ledger_path = cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"
    oi_path = cfg.output_root / "polymarket_trade_prints" / "open_interest_history.csv"
    summary_path = cfg.output_root / "polymarket_trade_prints" / "trade_prints_summary.json"
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "markets_polled": 0,
        "new_prints": 0,
        "ledger_rows": 0,
        "oi_markets_captured": 0,
        "oi_ledger_rows": 0,
        "oi_errors": [],
        "errors": [],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    markets = _tracked_markets(cfg, int(settings["max_markets"]))
    existing = read_csv_rows(ledger_path)
    seen = {str(row.get("trade_id") or "") for row in existing}
    base_url = str(settings["base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    max_rows = int(safe_float(settings.get("max_ledger_rows")) or 0)
    new_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for market in markets:
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
            continue
        trades = payload if isinstance(payload, list) else payload.get("trades") or payload.get("data") or []
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            row = _print_row(trade, market)
            if row is None or row["trade_id"] in seen:
                continue
            seen.add(row["trade_id"])
            new_rows.append(row)

    oi_markets_captured = 0
    oi_ledger_rows = 0
    oi_errors: list[str] = []
    if str(settings.get("open_interest_enabled", True)).strip().lower() not in {"0", "false", "no"}:
        oi_markets_captured, oi_ledger_rows, oi_errors = _collect_open_interest(
            base_url=base_url,
            timeout=timeout,
            markets=markets,
            ledger_path=oi_path,
            max_rows=max_rows,
        )

    combined = [*existing, *new_rows]
    if max_rows > 0 and len(combined) > max_rows:
        # Oldest rows roll into the same compressed training archive used by
        # the websocket feature substrate, then leave the live ledger.
        overflow = combined[:-max_rows]
        combined = combined[-max_rows:]
        from .websocket_normaliser import _archive_expiring_features

        _archive_expiring_features(
            cfg,
            {"training_archive_enabled": True, "training_archive_max_mb": 1024},
            [{"collected_at_utc": row.get("collected_at_utc"), **row} for row in overflow],
        )

    write_csv(ledger_path, combined, fieldnames=PRINT_FIELDS)
    summary.update(
        {
            "status": "ok" if not errors else ("partial" if new_rows or combined else "failed"),
            "markets_polled": len(markets),
            "new_prints": len(new_rows),
            "ledger_rows": len(combined),
            "ledger_path": str(ledger_path),
            "oi_markets_captured": oi_markets_captured,
            "oi_ledger_rows": oi_ledger_rows,
            "oi_path": str(oi_path),
            "oi_errors": oi_errors[:10],
            "errors": errors[:10],
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return collect_trade_prints(load_config(config_path))
