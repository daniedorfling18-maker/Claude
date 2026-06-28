from __future__ import annotations

import asyncio
import importlib
import json
import time
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, read_json, safe_float, write_csv, write_json

_TARGET_DECISIONS = {"collect_settlement_evidence", "candidate_for_focus"}
_DEFAULT_TARGET_FAMILIES = {
    "sports_other",
    "crypto_btc_special",
    "crypto_btc_updown_15m",
    "crypto_eth_updown_15m",
    "crypto_updown_event",
}
_EXCLUDED_FAMILIES = {
    "unknown",
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
}


async def _collect_messages(
    url: str,
    seconds: int,
    subscription_message: dict[str, Any] | None,
    *,
    connect_timeout_seconds: float = 8.0,
) -> list[dict[str, Any]]:
    websockets = importlib.import_module("websockets")
    rows: list[dict[str, Any]] = []
    deadline = time.time() + max(0, seconds)
    async with websockets.connect(
        url,
        open_timeout=max(1.0, connect_timeout_seconds),
        close_timeout=1.0,
        ping_interval=20,
        ping_timeout=5,
    ) as ws:
        if subscription_message:
            await ws.send(json.dumps(subscription_message))
        while time.time() < deadline:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=max(0.1, min(1.0, deadline - time.time())))
            except asyncio.TimeoutError:
                continue
            rows.append({"collected_at_utc": now_utc(), "message": message})
    return rows


def _existing_messages(path) -> list[dict[str, Any]]:
    existing = read_json(path, default=[])
    if not isinstance(existing, list):
        return []
    return [row for row in existing if isinstance(row, dict)]


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _target_families(cfg: EngineConfig, settings: dict[str, Any]) -> set[str]:
    configured = settings.get("liquidity_target_families") or []
    if configured:
        return {str(family).strip() for family in configured if str(family).strip()}
    rows = read_csv_rows(cfg.governance_root / "family_viability_leaderboard.csv")
    families = {
        str(row.get("family") or "").strip()
        for row in rows
        if str(row.get("decision") or "").strip() in _TARGET_DECISIONS
    }
    families = {family for family in families if family and family not in _EXCLUDED_FAMILIES}
    return families or set(_DEFAULT_TARGET_FAMILIES)


def _liquidity_target_rows(cfg: EngineConfig, settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not _boolish(settings.get("use_liquidity_targets", True)):
        return []
    watchlist_path = cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv"
    rows = read_csv_rows(watchlist_path)
    families = _target_families(cfg, settings)
    max_assets = int(settings.get("max_liquidity_target_assets", settings.get("max_assets", 24)) or 24)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        family = str(row.get("family") or row.get("category") or "").strip()
        token_id = str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()
        if not token_id or token_id in seen:
            continue
        if family not in families or family in _EXCLUDED_FAMILIES:
            continue
        if not _boolish(row.get("tradable_liquidity_candidate")):
            continue
        seen.add(token_id)
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            safe_float(row.get("liquidity")) or 0.0,
            -(safe_float(row.get("spread")) or 999.0),
        ),
        reverse=True,
    )
    return candidates[:max_assets]


def _asset_ids_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        token_id = str(row.get("token_id") or row.get("asset_id") or row.get("outcome_token_id") or "").strip()
        if token_id and token_id not in seen:
            seen.add(token_id)
            ids.append(token_id)
    return ids


def _family_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        family = str(row.get("family") or row.get("category") or "unknown")
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


def collect_websocket(cfg: EngineConfig, websocket_seconds: int = 60) -> dict[str, Any]:
    settings = cfg.raw.get("websocket_market_data", {})
    out_root = cfg.output_root / "polymarket_websocket"
    out_root.mkdir(parents=True, exist_ok=True)
    try:
        importlib.import_module("websockets")
    except Exception:
        summary = {"status": "skipped", "reason": "websockets dependency is not installed", "messages": 0}
        write_json(out_root / "websocket_summary.json", summary)
        return summary

    target_rows = _liquidity_target_rows(cfg, settings)
    dynamic_ids = _asset_ids_from_rows(target_rows)
    target_source = "liquidity_watchlist" if dynamic_ids else "configured_market_ids"
    if target_rows:
        write_csv(cfg.governance_root / "websocket_liquidity_targets.csv", target_rows)

    market_ids = dynamic_ids or list(settings.get("market_ids", []) or [])
    if not market_ids:
        summary = {
            "status": "skipped",
            "reason": "no websocket market_ids configured and no liquidity targets available",
            "messages": 0,
            "target_source": target_source,
            "target_assets": 0,
        }
        write_json(out_root / "websocket_summary.json", summary)
        return summary
    url = str(settings.get("url", "wss://ws-subscriptions-clob.polymarket.com/ws/market"))
    subscription = {"markets": market_ids, "type": "market"} if dynamic_ids else settings.get("subscription_message") or {"markets": market_ids, "type": "market"}
    output_file = out_root / "websocket_messages.json"
    existing_rows = _existing_messages(output_file)
    connect_timeout = float(settings.get("connect_timeout_seconds", 8.0))
    try:
        new_rows = asyncio.run(
            _collect_messages(
                url,
                websocket_seconds,
                subscription,
                connect_timeout_seconds=connect_timeout,
            )
        )
    except Exception as exc:  # noqa: BLE001 - collection should fail closed on socket stalls
        summary = {
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
            "messages": len(existing_rows),
            "new_messages": 0,
            "existing_messages": len(existing_rows),
            "seconds": websocket_seconds,
            "output_file": str(output_file),
            "append_mode": True,
            "target_source": target_source,
            "target_assets": len(market_ids),
            "target_family_counts": _family_counts(target_rows),
        }
        write_json(out_root / "websocket_summary.json", summary)
        return summary
    combined_rows = existing_rows + new_rows
    max_messages = int(settings.get("max_messages", 0) or 0)
    dropped_messages = 0
    if max_messages > 0 and len(combined_rows) > max_messages:
        dropped_messages = len(combined_rows) - max_messages
        combined_rows = combined_rows[-max_messages:]
    write_json(output_file, combined_rows)
    summary = {
        "status": "collected",
        "messages": len(combined_rows),
        "new_messages": len(new_rows),
        "existing_messages": len(existing_rows),
        "dropped_messages": dropped_messages,
        "max_messages": max_messages,
        "total_messages": len(combined_rows),
        "seconds": websocket_seconds,
        "output_file": str(output_file),
        "append_mode": True,
        "target_source": target_source,
        "target_assets": len(market_ids),
        "target_family_counts": _family_counts(target_rows),
        "target_file": str(cfg.governance_root / "websocket_liquidity_targets.csv") if target_rows else "",
    }
    write_json(out_root / "websocket_summary.json", summary)
    return summary


def main(config_path: str, websocket_seconds: int = 60) -> dict[str, Any]:
    return collect_websocket(load_config(config_path), websocket_seconds=websocket_seconds)
