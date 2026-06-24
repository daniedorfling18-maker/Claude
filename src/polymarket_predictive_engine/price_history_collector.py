from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"
SNAPSHOT_FIELDS = [
    "market_id",
    "market_slug",
    "condition_id",
    "gamma_market_id",
    "token_id",
    "outcome",
    "category",
    "timestamp",
    "midpoint",
    "price",
    "close_time",
    "source",
]
FORBIDDEN_PRICE_HISTORY_FIELDS = {
    "target",
    "winner",
    "winning",
    "winning_outcome",
    "winning_token_id",
    "resolved",
    "settled",
    "settlement",
    "payout",
    "final_result",
}


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    try:
        text = str(value).replace("Z", "+00:00")
        if text.endswith("+00"):
            text = text + ":00"
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except Exception:
        return None


def _clean_resolution_rows(cfg: EngineConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in ("historical_resolutions.csv", "market_resolutions.csv"):
        path = cfg.output_root / "polymarket_training" / name
        for row in read_csv_rows(path):
            if row.get("resolution_quality") == "clean_settlement" and row.get("token_id"):
                rows.append(row)

    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        market_key = row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id") or ""
        key = (market_key, row.get("token_id", ""))
        if key not in seen:
            seen.add(key)
            unique.append(row)

    settings = cfg.raw.get("historical_price_history", {})
    min_close = _dt(settings.get("min_close_time", "2024-01-01T00:00:00Z"))

    def close_key(row: dict[str, str]) -> datetime:
        return _dt(row.get("close_time") or row.get("resolution_time")) or datetime(1970, 1, 1, tzinfo=timezone.utc)

    sorted_rows = sorted(unique, key=close_key, reverse=True)
    recent = [row for row in sorted_rows if min_close and close_key(row) >= min_close]
    return recent or sorted_rows


def _epoch_to_timestamp(value: Any) -> str:
    raw = float(value)
    if raw > 10_000_000_000:
        raw = raw / 1000.0
    return datetime.fromtimestamp(raw, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_price_history_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        points = payload.get("history") or payload.get("prices") or payload.get("data") or []
    elif isinstance(payload, list):
        points = payload
    else:
        points = []

    normalised: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        ts_raw = point.get("t") or point.get("timestamp") or point.get("time") or point.get("created_at")
        price = safe_float(point.get("p") or point.get("price") or point.get("midpoint") or point.get("close"))
        ts = parse_timestamp(ts_raw)
        if ts is None and ts_raw not in (None, ""):
            try:
                ts_text = _epoch_to_timestamp(ts_raw)
            except Exception:
                ts_text = ""
        elif ts is not None:
            ts_text = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            ts_text = ""

        if not ts_text or price is None:
            continue
        normalised.append({"timestamp": ts_text, "price": price})

    return sorted(normalised, key=lambda r: r["timestamp"])


def _request_history(base_url: str, params: dict[str, Any], timeout: int) -> Any:
    response = requests.get(f"{base_url.rstrip('/')}/prices-history", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _window_params(
    token_id: str,
    close_time: datetime | None,
    fidelity: int,
    lookback_days: int,
    lookahead_days: int,
) -> dict[str, Any] | None:
    if close_time is None:
        return None
    start = close_time - timedelta(days=max(1, lookback_days))
    end = close_time + timedelta(days=max(1, lookahead_days))
    return {
        "market": token_id,
        "startTs": int(start.timestamp()),
        "endTs": int(end.timestamp()),
        "fidelity": fidelity,
    }


def _fetch_history(
    base_url: str,
    token_id: str,
    close_time: datetime | None,
    interval: str,
    fidelity: int,
    timeout: int,
    lookback_days: int,
    lookahead_days: int,
) -> tuple[Any, str]:
    attempts: list[tuple[str, dict[str, Any]]] = []
    bounded = _window_params(token_id, close_time, fidelity, lookback_days, lookahead_days)
    if bounded:
        attempts.append(("bounded_close_window", bounded))

        short = _window_params(token_id, close_time, fidelity, min(7, lookback_days), min(2, lookahead_days))
        if short and short != bounded:
            attempts.append(("short_close_window", short))

    attempts.extend(
        [
            ("interval_1m", {"market": token_id, "interval": "1m", "fidelity": fidelity}),
            ("interval_1w", {"market": token_id, "interval": "1w", "fidelity": fidelity}),
            ("configured_interval", {"market": token_id, "interval": interval, "fidelity": fidelity}),
        ]
    )

    last_error: Exception | None = None
    for source, params in attempts:
        try:
            payload = _request_history(base_url, params, timeout)
            points = normalize_price_history_payload(payload)
            if points:
                return payload, source
            last_error = None
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    return {"history": []}, "all_attempts_empty"


def _assert_snapshot_schema(rows: list[dict[str, Any]]) -> None:
    field_lowers = {field.lower() for field in SNAPSHOT_FIELDS}
    bad = [field for field in field_lowers if any(hint in field for hint in FORBIDDEN_PRICE_HISTORY_FIELDS)]
    if bad:
        raise ValueError("historical price snapshots contain forbidden fields: " + ", ".join(sorted(bad)))
    for row in rows:
        for key in list(row.keys()):
            lower = key.lower()
            if any(hint in lower for hint in FORBIDDEN_PRICE_HISTORY_FIELDS):
                raise ValueError(f"historical price snapshot row contains forbidden field: {key}")


def collect_price_history(
    cfg: EngineConfig,
    historical_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    settings = cfg.raw.get("historical_price_history", {})
    base_url = str(settings.get("clob_base_url", DEFAULT_CLOB_BASE_URL))
    interval = str(settings.get("interval", "max"))
    fidelity = int(settings.get("fidelity_seconds", 300))
    max_tokens = int(historical_limit or settings.get("max_tokens", 500))
    timeout = int(settings.get("request_timeout_seconds", 20))
    pause = float(settings.get("request_pause_seconds", 0.05))
    lookback_days = int(settings.get("lookback_days_before_close", 30))
    lookahead_days = int(settings.get("lookahead_days_after_close", 3))
    progress_every = int(settings.get("progress_every_tokens", 10))

    clean_rows = _clean_resolution_rows(cfg)[:max_tokens]
    snapshots: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors = 0
    empty = 0

    for idx, resolution in enumerate(clean_rows, start=1):
        token_id = resolution.get("token_id", "")
        close_dt = _dt(resolution.get("close_time") or resolution.get("resolution_time"))
        try:
            payload, source = _fetch_history(base_url, token_id, close_dt, interval, fidelity, timeout, lookback_days, lookahead_days)
            points = normalize_price_history_payload(payload)
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
                    "source": "polymarket_clob_prices_history:" + source,
                }
                snapshots.append({field: row.get(field, "") for field in SNAPSHOT_FIELDS})

            status = "ok" if points else "empty_history"
            if not points:
                empty += 1
            quality.append(
                {
                    "token_id": token_id,
                    "market_slug": resolution.get("market_slug", ""),
                    "close_time": resolution.get("close_time", ""),
                    "status": status,
                    "history_points": len(points),
                    "fetch_source": source,
                }
            )
        except Exception as exc:
            errors += 1
            quality.append(
                {
                    "token_id": token_id,
                    "market_slug": resolution.get("market_slug", ""),
                    "close_time": resolution.get("close_time", ""),
                    "status": "fetch_error",
                    "error": str(exc),
                }
            )

        if progress_every and (idx % progress_every == 0 or idx == len(clean_rows)):
            print(f"collect-price-history progress: {idx}/{len(clean_rows)} tokens; snapshots={len(snapshots)}; empty={empty}; errors={errors}", flush=True)

        if pause:
            time.sleep(pause)

    _assert_snapshot_schema(snapshots)
    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    write_csv(out_root / "historical_price_snapshots.csv", snapshots, fieldnames=SNAPSHOT_FIELDS)
    write_csv(gov_root / "historical_price_history_quality.csv", quality)
    summary = {
        "requested_tokens": len(clean_rows),
        "snapshot_rows": len(snapshots),
        "quality_rows": len(quality),
        "empty_history_count": empty,
        "error_count": errors,
        "collected_at_utc": now_utc(),
        "output_file": str(out_root / "historical_price_snapshots.csv"),
        "quality_file": str(gov_root / "historical_price_history_quality.csv"),
    }
    write_json(gov_root / "historical_price_history_summary.json", summary)
    return snapshots, quality, summary


def main(config_path: str, historical_limit: int | None = None) -> dict[str, Any]:
    _, _, summary = collect_price_history(load_config(config_path), historical_limit=historical_limit)
    return summary
