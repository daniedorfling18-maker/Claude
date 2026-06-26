from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .utils import parse_timestamp, safe_float

JsonFetcher = Callable[[str, float], Any]


ASSET_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}

_SNAPSHOT_CACHE: dict[tuple[str, int, int, str, int], dict[str, Any]] = {}


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = sum(values) / len(values)
    return math.sqrt(max(0.0, sum((value - avg) ** 2 for value in values) / (len(values) - 1)))


def _fetch_json(url: str, timeout_seconds: float) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "superbru-polymarket-crypto-updown-model/0.1",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - configured public API
        return json.loads(response.read().decode("utf-8"))


def crypto_updown_asset(row: dict[str, Any]) -> str:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("market_slug", "question", "event_id", "category")
    ).lower()
    padded = f" {text.replace('-', ' ')} "
    if "bitcoin" in padded or " btc " in padded:
        return "btc"
    if "ethereum" in padded or " eth " in padded:
        return "eth"
    if "solana" in padded or " sol " in padded:
        return "sol"
    if "xrp" in padded or "ripple" in padded:
        return "xrp"
    return ""


def is_crypto_updown_daily(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(row.get(key) or "")
        for key in ("market_slug", "question", "event_id", "category")
    ).lower()
    if not crypto_updown_asset(row):
        return False
    if not ("up or down" in text or "up-or-down" in text or "updown" in text):
        return False
    if re.search(r"\b(5|15)\s*(m|min|minute)s?\b", text) or re.search(r"updown[-_\s]*(5|15)m", text):
        return False
    return bool(
        "daily" in text
        or re.search(
            r"\bon[-_\s]+(january|february|march|april|may|june|july|august|september|october|november|december)[-_\s]+\d{1,2}",
            text,
        )
        or re.search(
            r"\bon\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}",
            text,
        )
    )


def _first_float_from_kline(payload: Any, index: int) -> float | None:
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    if not isinstance(first, list) or len(first) <= index:
        return None
    return safe_float(first[index])


def _volatility_per_sqrt_second(payload: Any) -> tuple[float, int]:
    closes = [
        safe_float(row[4])
        for row in payload
        if isinstance(row, list) and len(row) > 4 and safe_float(row[4]) is not None
    ] if isinstance(payload, list) else []
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i] is not None and closes[i - 1] is not None and closes[i] > 0 and closes[i - 1] > 0
    ]
    return (_stddev(returns) / math.sqrt(60.0) if returns else 0.0, len(returns))


def score_crypto_updown_prediction(
    row: dict[str, Any],
    settings: dict[str, Any] | None = None,
    *,
    fetch_json: JsonFetcher | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    if not settings.get("enabled", True):
        return {"crypto_model_status": "disabled"}
    if not is_crypto_updown_daily(row):
        return {"crypto_model_status": "not_crypto_updown_daily"}

    asset = crypto_updown_asset(row)
    symbol = ASSET_SYMBOLS.get(asset, "")
    close_time = parse_timestamp(
        row.get("close_time")
        or row.get("end_time")
        or row.get("endDate")
        or row.get("market_close_time")
    )
    if not symbol:
        return {"crypto_model_status": "unsupported_asset"}
    if close_time is None:
        return {"crypto_model_status": "missing_close_time"}
    close_time = close_time.astimezone(timezone.utc)
    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window_hours = float(settings.get("daily_window_hours", 24.0))
    start_time = close_time - timedelta(hours=window_hours)
    elapsed_seconds = (now_dt - start_time).total_seconds()
    remaining_seconds = (close_time - now_dt).total_seconds()
    if elapsed_seconds < float(settings.get("minimum_elapsed_seconds", 300.0)):
        return {
            "crypto_model_status": "before_model_window",
            "crypto_model_elapsed_seconds": elapsed_seconds,
            "crypto_model_remaining_seconds": remaining_seconds,
        }
    if remaining_seconds < float(settings.get("minimum_remaining_seconds", 60.0)):
        return {
            "crypto_model_status": "too_close_to_resolution",
            "crypto_model_elapsed_seconds": elapsed_seconds,
            "crypto_model_remaining_seconds": remaining_seconds,
        }

    api_base = str(settings.get("binance_base_url", "https://api.binance.com/api/v3")).rstrip("/")
    timeout_seconds = float(settings.get("request_timeout_seconds", 10.0))
    lookback = int(settings.get("volatility_lookback_minutes", 240))
    cache_seconds = max(1, int(settings.get("cache_seconds", 30)))
    cache_key = (
        symbol,
        int(start_time.timestamp()),
        int(close_time.timestamp()),
        api_base,
        int(now_dt.timestamp() // cache_seconds),
    )
    snapshot = None if fetch_json is not None else _SNAPSHOT_CACHE.get(cache_key)
    if snapshot is None:
        fetcher = fetch_json or _fetch_json
        start_ms = int(start_time.timestamp() * 1000)
        start_url = (
            f"{api_base}/klines?"
            + urllib.parse.urlencode({"symbol": symbol, "interval": "1m", "startTime": str(start_ms), "limit": "1"})
        )
        ticker_url = f"{api_base}/ticker/price?" + urllib.parse.urlencode({"symbol": symbol})
        vol_url = (
            f"{api_base}/klines?"
            + urllib.parse.urlencode({"symbol": symbol, "interval": "1m", "limit": str(max(20, min(1000, lookback)))})
        )
        try:
            start_payload = fetcher(start_url, timeout_seconds)
            ticker_payload = fetcher(ticker_url, timeout_seconds)
            vol_payload = fetcher(vol_url, timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - live model must fail closed
            return {"crypto_model_status": f"price_fetch_error:{type(exc).__name__}"}

        # Polymarket resolves these daily contracts against the Binance 1m candle "Close" price.
        start_price = _first_float_from_kline(start_payload, 4)
        current_price = safe_float(ticker_payload.get("price") if isinstance(ticker_payload, dict) else None)
        sigma, volatility_rows = _volatility_per_sqrt_second(vol_payload)
        sigma = max(float(settings.get("sigma_floor_per_sqrt_second", 0.00002)), sigma)
        snapshot = {
            "start_price": start_price,
            "current_price": current_price,
            "sigma": sigma,
            "volatility_rows": volatility_rows,
            "cache_hit": False,
        }
        if fetch_json is None:
            _SNAPSHOT_CACHE[cache_key] = snapshot
    else:
        snapshot = {**snapshot, "cache_hit": True}
    start_price = safe_float(snapshot.get("start_price"))
    current_price = safe_float(snapshot.get("current_price"))
    sigma = safe_float(snapshot.get("sigma")) or float(settings.get("sigma_floor_per_sqrt_second", 0.00002))
    volatility_rows = int(safe_float(snapshot.get("volatility_rows")) or 0)
    if start_price is None or current_price is None or start_price <= 0 or current_price <= 0:
        return {"crypto_model_status": "invalid_price_snapshot"}

    distance = math.log(current_price / start_price)
    z_score = distance / max(1e-12, sigma * math.sqrt(max(1.0, remaining_seconds)))
    p_up = min(0.999, max(0.001, _normal_cdf(z_score)))
    outcome = str(row.get("outcome") or "").strip().lower()
    probability = p_up if outcome == "up" else (1.0 - p_up if outcome == "down" else 0.5)
    price = safe_float(row.get("executable_price") or row.get("best_ask") or row.get("market_midpoint"))
    spread = safe_float(row.get("spread")) or 0.0
    edge = "" if price is None else probability - price
    edge_after_cost = "" if price is None else probability - price - spread / 2.0
    return {
        "crypto_model_status": "scored",
        "crypto_model_asset": asset,
        "crypto_model_symbol": symbol,
        "crypto_model_probability": probability,
        "crypto_model_p_up": p_up,
        "crypto_model_edge": edge,
        "crypto_model_edge_after_cost": edge_after_cost,
        "crypto_model_z_score": z_score,
        "crypto_model_start_time": start_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crypto_model_close_time": close_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crypto_model_start_price": start_price,
        "crypto_model_current_price": current_price,
        "crypto_model_distance_log": distance,
        "crypto_model_sigma_per_sqrt_second": sigma,
        "crypto_model_elapsed_seconds": elapsed_seconds,
        "crypto_model_remaining_seconds": remaining_seconds,
        "crypto_model_volatility_return_rows": volatility_rows,
        "crypto_model_price_source": "binance_1m_close",
        "crypto_model_cache_hit": bool(snapshot.get("cache_hit")),
    }
