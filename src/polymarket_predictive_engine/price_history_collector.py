from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"
SNAPSHOT_FIELDS = ["market_id", "market_slug", "condition_id", "gamma_market_id", "token_id", "outcome", "category", "timestamp", "midpoint", "price", "close_time", "resolution_time", "source"]


def _clean_resolution_rows(cfg: EngineConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in ("historical_resolutions.csv", "market_resolutions.csv"):
        path = cfg.output_root / "polymarket_training" / name
        rows.extend([r for r in read_csv_rows(path) if r.get("resolution_quality") == "clean_settlement" and r.get("token_id")])
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id") or "", row.get("token_id", ""))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def normalize_price_history_payload(payload: Any) -> list[dict[str, Any]]:
    points = payload.get("history") if isinstance(payload, dict) else payload
    if not isinstance(points, list):
        points = []
    out: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        ts_raw = point.get("t") or point.get("timestamp") or point.get("time")
        price = safe_float(point.get("p") or point.get("price") or point.get("midpoint") or point.get("close"))
        ts = parse_timestamp(ts_raw)
        if ts is None:
            try:
                raw = float(ts_raw)
                if raw > 10_000_000_000:
                    raw = raw / 1000.0
                ts = datetime.fromtimestamp(raw, timezone.utc)
            except Exception:
                ts = None
        if ts is not None and price is not None:
            out.append({"timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "price": price})
    return sorted(out, key=lambda r: r["timestamp"])


def _fetch_history(base_url: str, token_id: str, interval: str, fidelity: int, timeout: int) -> Any:
    response = requests.get(f"{base_url.rstrip('/')}/prices-history", params={"market": token_id, "interval": interval, "fidelity": fidelity}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def collect_price_history(cfg: EngineConfig, historical_limit: int | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    settings = cfg.raw.get("historical_price_history", {})
    base_url = str(settings.get("clob_base_url", DEFAULT_CLOB_BASE_URL))
    interval = str(settings.get("interval", "max"))
    fidelity = int(settings.get("fidelity_seconds", 300))
    max_tokens = int(historical_limit or settings.get("max_tokens", 500))
    timeout = int(settings.get("request_timeout_seconds", 30))
    pause = float(settings.get("request_pause_seconds", 0.1))
    clean_rows = _clean_resolution_rows(cfg)[:max_tokens]
    snapshots: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors = 0
    for resolution in clean_rows:
        token_id = resolution.get("token_id", "")
        try:
            points = normalize_price_history_payload(_fetch_history(base_url, token_id, interval, fidelity, timeout))
            for point in points:
                row = {
                    "market_id": resolution.get("condition_id") or resolution.get("market_slug") or resolution.get("gamma_market_id") or "",
                    "market_slug": resolution.get("market_slug", ""),
                    "condition_id": resolution.get("condition_id", ""),
                    "gamma_market_id": resolution.get("gamma_market_id", ""),
                    "token_id": token_id,
                    "outcome": resolution.get("outcome", ""),
                    "category": resolution.get("category", ""),
                    "timestamp": point["timestamp"],
                    "midpoint": point["price"],
                    "price": point["price"],
                    "close_time": resolution.get("close_time", ""),
                    "resolution_time": resolution.get("resolution_time", ""),
                    "source": "polymarket_price_history",
                }
                snapshots.append({field: row.get(field, "") for field in SNAPSHOT_FIELDS})
            quality.append({"token_id": token_id, "market_slug": resolution.get("market_slug", ""), "status": "ok" if points else "empty_history", "history_points": len(points)})
        except Exception as exc:
            errors += 1
            quality.append({"token_id": token_id, "market_slug": resolution.get("market_slug", ""), "status": "fetch_error", "error": str(exc)})
        if pause:
            time.sleep(pause)
    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    write_csv(out_root / "historical_price_snapshots.csv", snapshots, fieldnames=SNAPSHOT_FIELDS)
    write_csv(gov_root / "historical_price_history_quality.csv", quality)
    summary = {"requested_tokens": len(clean_rows), "snapshot_rows": len(snapshots), "quality_rows": len(quality), "error_count": errors, "collected_at_utc": now_utc(), "output_file": str(out_root / "historical_price_snapshots.csv"), "quality_file": str(gov_root / "historical_price_history_quality.csv")}
    write_json(gov_root / "historical_price_history_summary.json", summary)
    return snapshots, quality, summary


def main(config_path: str, historical_limit: int | None = None) -> dict[str, Any]:
    _, _, summary = collect_price_history(load_config(config_path), historical_limit=historical_limit)
    return summary
