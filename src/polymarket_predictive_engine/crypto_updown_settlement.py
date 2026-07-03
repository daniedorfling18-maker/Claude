from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .utils import parse_timestamp, safe_float

_ASSET_KEYS = {
    "bitcoin": "btc",
    "btc": "btc",
    "ethereum": "eth",
    "eth": "eth",
    "solana": "sol",
    "sol": "sol",
    "xrp": "xrp",
    "ripple": "xrp",
}
_BINANCE_SYMBOLS = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}
_COINBASE_PRODUCTS = {
    "btc": "BTC-USD",
    "eth": "ETH-USD",
    "sol": "SOL-USD",
    "xrp": "XRP-USD",
}
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_FAST_UPDOWN_RE = re.compile(r"\b(?P<asset>btc|eth|sol|xrp)-updown-(?P<interval>5m|15m)-(?P<start>\d{9,11})\b")
_NAMED_UPDOWN_RE = re.compile(
    r"\b(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|xrp|ripple)"
    r"[-_\s]+up[-_\s]+or[-_\s]+down[-_\s]+"
    r"(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)"
    r"[-_\s]+(?P<day>\d{1,2})[-_\s]+(?P<year>\d{4})"
    r"(?:[-_\s]+(?P<hour>\d{1,2})(?:(?::|-)?(?P<minute>\d{2}))?(?P<ampm>am|pm)[-_\s]*(?P<tz>et|est|edt))?",
    re.IGNORECASE,
)


def _row_text(value: Any) -> str:
    if isinstance(value, dict):
        return " ".join(
            str(value.get(key) or "")
            for key in ("market_slug", "slug", "market_id", "question", "event_id", "category")
        ).strip().lower()
    return str(value or "").strip().lower()


def _asset_key(raw: str) -> str:
    return _ASSET_KEYS.get(str(raw or "").strip().lower(), "")


def crypto_updown_slug_window(value: Any) -> tuple[str, int, datetime, datetime] | None:
    """Return ``(asset_key, interval_minutes, start_utc, close_utc)`` for crypto Up/Down slugs."""
    text = _row_text(value).replace("_", "-")
    fast = _FAST_UPDOWN_RE.search(text)
    if fast:
        interval_minutes = 15 if fast.group("interval") == "15m" else 5
        try:
            start_utc = datetime.fromtimestamp(int(fast.group("start")), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            return None
        return fast.group("asset"), interval_minutes, start_utc, start_utc + timedelta(minutes=interval_minutes)

    named = _NAMED_UPDOWN_RE.search(text)
    if not named:
        return None
    asset_key = _asset_key(named.group("asset"))
    month = _MONTHS.get(str(named.group("month") or "").lower())
    if not asset_key or month is None:
        return None
    try:
        day = int(named.group("day"))
        year = int(named.group("year"))
    except (TypeError, ValueError):
        return None
    tz = ZoneInfo("America/New_York")
    if named.group("hour"):
        try:
            hour = int(named.group("hour"))
            minute = int(named.group("minute") or 0)
        except (TypeError, ValueError):
            return None
        ampm = str(named.group("ampm") or "").lower()
        if hour < 1 or hour > 12 or minute < 0 or minute > 59:
            return None
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        close_et = datetime(year, month, day, hour, minute, tzinfo=tz)
        start_et = close_et - timedelta(hours=1)
        interval_minutes = 60
    else:
        start_et = datetime(year, month, day, 0, 0, tzinfo=tz)
        close_et = start_et + timedelta(days=1)
        interval_minutes = 24 * 60
    return asset_key, interval_minutes, start_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc)


def crypto_updown_slug_close_time(value: Any) -> str:
    window = crypto_updown_slug_window(value)
    if window is None:
        return ""
    return window[3].isoformat().replace("+00:00", "Z")


def _binance_interval(interval_minutes: int) -> str:
    if interval_minutes <= 5:
        return "5m"
    if interval_minutes <= 15:
        return "15m"
    if interval_minutes <= 60:
        return "1h"
    return "1d"


def _coinbase_granularity(interval_minutes: int) -> int:
    if interval_minutes <= 5:
        return 300
    if interval_minutes <= 15:
        return 900
    if interval_minutes <= 60:
        return 3600
    return 86400


def _fetch_binance_window_prices(
    asset_key: str,
    *,
    start_utc: datetime,
    interval_minutes: int,
    timeout_seconds: int,
) -> tuple[float, float, str] | None:
    symbol = _BINANCE_SYMBOLS.get(asset_key)
    if not symbol:
        return None
    interval = _binance_interval(interval_minutes)
    start_ms = int(start_utc.timestamp() * 1000)
    params = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": interval,
            "startTime": str(start_ms),
            "limit": "1",
        }
    )
    request = urllib.request.Request(
        f"https://api.binance.com/api/v3/klines?{params}",
        headers={
            "User-Agent": "superbru-polymarket-mispricing-bot/0.1",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - public market data API
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        return None
    candle = payload[0]
    if not isinstance(candle, list) or len(candle) < 5:
        return None
    open_price = safe_float(candle[1])
    close_price = safe_float(candle[4])
    if open_price is None or close_price is None or open_price <= 0 or close_price <= 0:
        return None
    return open_price, close_price, f"binance_{interval}_klines"


def _fetch_coinbase_window_prices(
    asset_key: str,
    *,
    start_utc: datetime,
    interval_minutes: int,
    timeout_seconds: int,
) -> tuple[float, float, str] | None:
    product = _COINBASE_PRODUCTS.get(asset_key)
    if not product:
        return None
    granularity = _coinbase_granularity(interval_minutes)
    end_utc = start_utc + timedelta(minutes=interval_minutes)
    params = urllib.parse.urlencode(
        {
            "start": start_utc.isoformat().replace("+00:00", "Z"),
            "end": end_utc.isoformat().replace("+00:00", "Z"),
            "granularity": str(granularity),
        }
    )
    request = urllib.request.Request(
        f"https://api.exchange.coinbase.com/products/{product}/candles?{params}",
        headers={
            "User-Agent": "superbru-polymarket-mispricing-bot/0.1",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - public market data API
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        return None
    start_second = int(start_utc.timestamp())
    candle = next(
        (row for row in payload if isinstance(row, list) and row and int(safe_float(row[0]) or -1) == start_second),
        payload[0],
    )
    if not isinstance(candle, list) or len(candle) < 5:
        return None
    open_price = safe_float(candle[3])
    close_price = safe_float(candle[4])
    if open_price is None or close_price is None or open_price <= 0 or close_price <= 0:
        return None
    return open_price, close_price, f"coinbase_{granularity}s_candles"


def crypto_updown_proxy_settlement_price(
    position: dict[str, Any],
    *,
    timeout_seconds: int,
) -> tuple[float | None, str]:
    window = crypto_updown_slug_window(position)
    if window is None:
        return None, "not_crypto_updown_slug"
    asset_key, interval_minutes, start_utc, close_utc = window
    if datetime.now(timezone.utc) < close_utc + timedelta(seconds=30):
        return None, "crypto_updown_not_past_close"
    outcome = str(position.get("outcome") or "").strip().lower()
    if outcome not in {"up", "down"}:
        return None, "crypto_updown_missing_outcome"

    errors: list[str] = []
    for provider in (_fetch_binance_window_prices, _fetch_coinbase_window_prices):
        try:
            prices = provider(
                asset_key,
                start_utc=start_utc,
                interval_minutes=interval_minutes,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - try the next public price source
            errors.append(f"{provider.__name__}:{type(exc).__name__}")
            continue
        if prices is None:
            errors.append(f"{provider.__name__}:empty")
            continue
        start_price, end_price, source = prices
        if end_price == start_price:
            return None, f"crypto_updown_tie:{source}"
        winning_outcome = "up" if end_price > start_price else "down"
        return (1.0 if outcome == winning_outcome else 0.0), (
            f"crypto_updown_proxy_settlement:{source}:{winning_outcome}:"
            f"{start_price:.8g}->{end_price:.8g}"
        )
    return None, "crypto_updown_oracle_unavailable:" + ",".join(errors[:3])


def crypto_updown_close_time_from_row(row: dict[str, Any]) -> datetime | None:
    for key in ("close_time", "end_time", "endDate", "endDateIso", "market_close_time"):
        parsed = parse_timestamp(row.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    close_text = crypto_updown_slug_close_time(row)
    return parse_timestamp(close_text)
