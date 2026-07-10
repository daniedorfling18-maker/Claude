"""WO-37 wallet-intelligence collection lane.

This module harvests the two public Polymarket data-API streams that carry
wallet-level alpha content but are not yet model inputs:

* leaderboard snapshots (realised PnL / volume ranks);
* holder snapshots for markets the websocket collector is already tracking.

Collection only: the artifacts are reporting substrate for later leakage-
reviewed WO-35/WO-33 feature work. This module does not create labels, change
gates, approve paper/live trades, or place orders.
"""
from __future__ import annotations

from typing import Any

import requests

from .config import EngineConfig, load_config
from .trade_print_collector import _tracked_markets
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_BASE_URL = "https://data-api.polymarket.com"

LEADERBOARD_FIELDS = [
    "snapshot_date",
    "snapshot_at_utc",
    "wallet",
    "rank",
    "pnl",
    "volume",
    "raw_window",
    "raw_rank_type",
]

HOLDER_FIELDS = [
    "snapshot_date",
    "snapshot_at_utc",
    "market",
    "wallet",
    "outcome",
    "size",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("wallet_intelligence", {}) if isinstance(cfg.raw.get("wallet_intelligence"), dict) else {}
    merged = {
        "enabled": True,
        "base_url": DEFAULT_BASE_URL,
        "max_markets": 40,
        "top_holders_per_market": 20,
        "leaderboard_limit": 100,
        "max_ledger_rows": 200000,
        "request_timeout_seconds": 20,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _payload_rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys:
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    for value in payload.values():
        if isinstance(value, list) and all(isinstance(row, dict) for row in value):
            return list(value)
    return []


def _wallet(row: dict[str, Any]) -> str:
    for key in ("wallet", "address", "proxyWallet", "proxy_wallet", "user", "trader"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _leaderboard_row(row: dict[str, Any], *, snapshot_at: str, params: dict[str, Any]) -> dict[str, Any] | None:
    wallet = _wallet(row)
    if not wallet:
        return None
    rank = row.get("rank") or row.get("position") or row.get("place") or ""
    pnl = safe_float(row.get("pnl") or row.get("profit") or row.get("realizedPnl") or row.get("realized_pnl"))
    volume = safe_float(row.get("volume") or row.get("vol") or row.get("tradeVolume") or row.get("trade_volume"))
    return {
        "snapshot_date": snapshot_at[:10],
        "snapshot_at_utc": snapshot_at,
        "wallet": wallet,
        "rank": rank,
        "pnl": pnl,
        "volume": volume,
        "raw_window": str(params.get("window") or ""),
        "raw_rank_type": str(params.get("rankType") or params.get("rank_type") or ""),
    }


def _holder_row(row: dict[str, Any], *, snapshot_at: str, market: str) -> dict[str, Any] | None:
    wallet = _wallet(row)
    size = safe_float(row.get("size") or row.get("shares") or row.get("balance") or row.get("position"))
    if not wallet or size is None:
        return None
    return {
        "snapshot_date": snapshot_at[:10],
        "snapshot_at_utc": snapshot_at,
        "market": market,
        "wallet": wallet,
        "outcome": str(row.get("outcome") or row.get("name") or row.get("asset") or row.get("asset_id") or ""),
        "size": size,
    }


def _dedupe_latest(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    ordered: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(field) or "") for field in key_fields)
        if all(key):
            if key in ordered:
                # Move same-day refreshes to the latest append position so
                # max_ledger_rows keeps the newest snapshot order, not the
                # position from the first time that key appeared.
                ordered.pop(key)
            ordered[key] = row
    return list(ordered.values())


def _cap(rows: list[dict[str, Any]], max_rows: int) -> list[dict[str, Any]]:
    return rows[-max_rows:] if max_rows > 0 and len(rows) > max_rows else rows


def _fetch_leaderboard(settings: dict[str, Any], snapshot_at: str) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    base_url = str(settings["base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    limit = int(settings["leaderboard_limit"])
    probes = [
        {"limit": limit, "window": "all", "rankType": "pnl"},
        {"limit": limit, "window": "all", "rank_type": "pnl"},
        {"limit": limit},
    ]
    last_error = ""
    for params in probes:
        try:
            response = requests.get(f"{base_url}/leaderboard", params=params, timeout=timeout)
            response.raise_for_status()
            rows = _payload_rows(response.json(), "leaderboard", "data", "results")
        except Exception as exc:  # noqa: BLE001 - endpoint probing must be fail-soft
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        parsed = [
            parsed_row
            for row in rows[:limit]
            if (parsed_row := _leaderboard_row(row, snapshot_at=snapshot_at, params=params)) is not None
        ]
        if parsed:
            return parsed, params, ""
    return [], {}, last_error or "empty leaderboard response"


def _fetch_holders(settings: dict[str, Any], market: str, snapshot_at: str) -> tuple[list[dict[str, Any]], str]:
    base_url = str(settings["base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    limit = int(settings["top_holders_per_market"])
    try:
        response = requests.get(f"{base_url}/holders", params={"market": market, "limit": limit}, timeout=timeout)
        response.raise_for_status()
        rows = _payload_rows(response.json(), "holders", "data", "results")
    except Exception as exc:  # noqa: BLE001 - one missing holders endpoint must not fail the daily job
        return [], f"{market}: {type(exc).__name__}: {exc}"
    parsed = [
        parsed_row
        for row in rows[:limit]
        if (parsed_row := _holder_row(row, snapshot_at=snapshot_at, market=market)) is not None
    ]
    return parsed, ""


def collect_wallet_intelligence(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "wallet_intelligence"
    leaderboard_path = out_root / "leaderboard_history.csv"
    holders_path = out_root / "holders_history.csv"
    summary_path = out_root / "wallet_intelligence_summary.json"
    snapshot_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": snapshot_at,
        "work_order": "WO-37",
        "markets_polled": 0,
        "leaderboard_rows_added": 0,
        "holder_rows_added": 0,
        "wallets_seen": 0,
        "holder_leaderboard_overlap_count": 0,
        "errors": [],
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    max_rows = int(settings["max_ledger_rows"])
    leaderboard_rows, leaderboard_params, leaderboard_error = _fetch_leaderboard(settings, snapshot_at)
    existing_leaderboard = read_csv_rows(leaderboard_path)
    combined_leaderboard = _cap(
        _dedupe_latest([*existing_leaderboard, *leaderboard_rows], ("snapshot_date", "wallet")),
        max_rows,
    )
    write_csv(leaderboard_path, combined_leaderboard, fieldnames=LEADERBOARD_FIELDS)

    markets = _tracked_markets(cfg, int(settings["max_markets"]))
    holder_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if leaderboard_error:
        errors.append(f"leaderboard: {leaderboard_error}")
    for market in markets:
        rows, error = _fetch_holders(settings, market, snapshot_at)
        holder_rows.extend(rows)
        if error:
            errors.append(error)
    existing_holders = read_csv_rows(holders_path)
    combined_holders = _cap(
        _dedupe_latest([*existing_holders, *holder_rows], ("snapshot_date", "market", "wallet")),
        max_rows,
    )
    write_csv(holders_path, combined_holders, fieldnames=HOLDER_FIELDS)

    latest_leaderboard = {row["wallet"] for row in leaderboard_rows[: int(settings["leaderboard_limit"])] if row.get("wallet")}
    current_holders = {row["wallet"] for row in holder_rows if row.get("wallet")}
    overlap = sorted(latest_leaderboard & current_holders)
    summary.update(
        {
            "status": "ok" if (leaderboard_rows or holder_rows or not errors) else "failed",
            "markets_polled": len(markets),
            "leaderboard_rows_added": len(leaderboard_rows),
            "holder_rows_added": len(holder_rows),
            "leaderboard_rows": len(combined_leaderboard),
            "holder_rows": len(combined_holders),
            "wallets_seen": len({*latest_leaderboard, *current_holders}),
            "holder_leaderboard_overlap_count": len(overlap),
            "holder_leaderboard_overlap_wallets": overlap[:20],
            "leaderboard_probe_params": leaderboard_params,
            "leaderboard_history_path": str(leaderboard_path),
            "holders_history_path": str(holders_path),
            "errors": errors[:20],
            "note": (
                "Collection-only wallet intelligence. Overlap between current holders and the latest "
                "leaderboard is a reporting scalar only; it does not feed gates or trading decisions."
            ),
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return collect_wallet_intelligence(load_config(config_path))
