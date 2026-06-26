from __future__ import annotations

import asyncio
import importlib
import json
import time
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, read_json, write_json


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

    market_ids = list(settings.get("market_ids", []) or [])
    if not market_ids:
        summary = {"status": "skipped", "reason": "no websocket market_ids configured", "messages": 0}
        write_json(out_root / "websocket_summary.json", summary)
        return summary

    url = str(settings.get("url", "wss://ws-subscriptions-clob.polymarket.com/ws/market"))
    subscription = settings.get("subscription_message") or {"markets": market_ids, "type": "market"}
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
    except Exception as exc:  # noqa: BLE001 - live loop should fail closed on socket stalls
        summary = {
            "status": "error",
            "reason": f"{type(exc).__name__}: {exc}",
            "messages": len(existing_rows),
            "new_messages": 0,
            "existing_messages": len(existing_rows),
            "seconds": websocket_seconds,
            "output_file": str(output_file),
            "append_mode": True,
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
    }
    write_json(out_root / "websocket_summary.json", summary)
    return summary


def main(config_path: str, websocket_seconds: int = 60) -> dict[str, Any]:
    return collect_websocket(load_config(config_path), websocket_seconds=websocket_seconds)
