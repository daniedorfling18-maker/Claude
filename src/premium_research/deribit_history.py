"""Deribit public history access (WO-166).

Two endpoints, both keyless and read-only:

* ``public/get_funding_rate_history`` — hourly rows with ``interest_8h``,
  ``interest_1h`` and ``index_price``; the server returns at most 744 rows
  (31 days) per call, so the span is walked in 744-hour windows and the
  pages are de-duplicated on ``timestamp``.
* ``public/get_volatility_index_data`` — DVOL candles. The server returns the
  newest 1,000 candles of the requested window and, when truncated, a
  ``continuation`` timestamp to use as the next ``end_timestamp``; the walk
  continues until it returns null, and the boundary candle that appears in
  two pages is de-duplicated.

Fail-closed: a JSON-RPC error, an empty page inside the requested span, a
duplicated timestamp with a different value, or a non-finite field raises
:class:`DeribitError`. Network access is only through the injected ``fetch``
callable so tests never touch it.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pandas as pd

from .binance_vision import HTTP_RETRIES, HTTP_TIMEOUT_SECONDS, _session

BASE_URL = "https://www.deribit.com/api/v2"
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
PAGE_HOURS = 744
PAGE_MS = PAGE_HOURS * HOUR_MS

FUNDING_FIELDS = ("timestamp", "index_price", "interest_8h", "interest_1h")
DVOL_FIELDS = ("timestamp", "open", "high", "low", "close")

FetchJsonFn = Callable[[str, Mapping[str, Any]], dict[str, Any]]


class DeribitError(RuntimeError):
    """Raised on any Deribit defect. The fail-safe direction is always 'no data'."""


def fetch_json(url: str, params: Mapping[str, Any], *, timeout: float = HTTP_TIMEOUT_SECONDS, retries: int = HTTP_RETRIES) -> dict[str, Any]:
    """GET a JSON-RPC endpoint and return the decoded envelope. Raises on error envelopes."""
    last_error: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            response = _session().get(url, params=dict(params), timeout=timeout)
        except Exception as exc:  # pragma: no cover - network path (requests.RequestException and socket errors)
            last_error = exc
            continue
        if response.status_code != 200:
            last_error = DeribitError(f"HTTP {response.status_code} for {url}")
            continue
        try:
            envelope = response.json()
        except json.JSONDecodeError as exc:
            raise DeribitError(f"non-JSON body from {url}") from exc
        if "error" in envelope:
            raise DeribitError(f"JSON-RPC error from {url}: {envelope['error']}")
        return envelope
    raise DeribitError(f"fetch failed after {retries} attempts for {url}: {last_error}")


def _finite(value: Any, *, label: str) -> float:
    if value is None or value == "":
        raise DeribitError(f"empty {label}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DeribitError(f"non-numeric {label}: {value!r}") from exc
    if not math.isfinite(number):
        raise DeribitError(f"non-finite {label}: {value!r}")
    return number


def parse_funding_rows(rows: list[Mapping[str, Any]]) -> pd.DataFrame:
    """Validate and frame one page of funding rows."""
    if not rows:
        raise DeribitError("funding page is empty")
    records: list[dict[str, float | int]] = []
    for row in rows:
        try:
            ts = int(row["timestamp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise DeribitError(f"funding row without integer timestamp: {row!r}") from exc
        records.append(
            {
                "timestamp": ts,
                "index_price": _finite(row.get("index_price"), label="index_price"),
                "interest_8h": _finite(row.get("interest_8h"), label="interest_8h"),
                "interest_1h": _finite(row.get("interest_1h"), label="interest_1h"),
            }
        )
    return pd.DataFrame.from_records(records, columns=list(FUNDING_FIELDS)).sort_values("timestamp").reset_index(drop=True)


def dedup_concat(pages: list[pd.DataFrame]) -> pd.DataFrame:
    """Union pages on ``timestamp``; a timestamp seen twice with different values raises."""
    if not pages:
        raise DeribitError("no pages to concatenate")
    frame = pd.concat(pages, ignore_index=True)
    grouped = frame.groupby("timestamp", sort=True)
    conflicts = grouped.nunique(dropna=False).drop(columns=[], errors="ignore")
    bad = conflicts[(conflicts > 1).any(axis=1)]
    if not bad.empty:
        raise DeribitError(f"conflicting values for timestamps {bad.index.tolist()[:3]}")
    return frame.drop_duplicates(subset="timestamp", keep="first").sort_values("timestamp").reset_index(drop=True)


def funding_windows(start_ms: int, end_ms: int) -> Iterator[tuple[int, int]]:
    """Yield ``[start, end]`` windows of at most 744 hours covering the span."""
    if end_ms <= start_ms:
        raise DeribitError(f"reversed or empty span {start_ms}..{end_ms}")
    cursor = start_ms
    while cursor < end_ms:
        upper = min(cursor + PAGE_MS, end_ms)
        yield cursor, upper
        cursor = upper


def fetch_funding_history(instrument: str, start_ms: int, end_ms: int, *, fetch: FetchJsonFn | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Walk the span in 744-hour windows; returns the de-duplicated frame and the URLs called."""
    fetch_fn = fetch or (lambda url, params: fetch_json(url, params))
    url = f"{BASE_URL}/public/get_funding_rate_history"
    pages: list[pd.DataFrame] = []
    calls: list[str] = []
    for lower, upper in funding_windows(start_ms, end_ms):
        params = {"instrument_name": instrument, "start_timestamp": lower, "end_timestamp": upper}
        calls.append(f"{url}?instrument_name={instrument}&start_timestamp={lower}&end_timestamp={upper}")
        envelope = fetch_fn(url, params)
        rows = envelope.get("result")
        if not isinstance(rows, list) or not rows:
            raise DeribitError(f"empty funding page inside the requested span: {instrument} {lower}..{upper}")
        pages.append(parse_funding_rows(rows))
    frame = dedup_concat(pages)
    return frame, calls


def parse_dvol_rows(data: list[list[Any]]) -> pd.DataFrame:
    if not data:
        raise DeribitError("DVOL page is empty")
    records: list[dict[str, float | int]] = []
    for row in data:
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            raise DeribitError(f"DVOL row does not have 5 fields: {row!r}")
        records.append(
            {
                "timestamp": int(row[0]),
                "open": _finite(row[1], label="open"),
                "high": _finite(row[2], label="high"),
                "low": _finite(row[3], label="low"),
                "close": _finite(row[4], label="close"),
            }
        )
    return pd.DataFrame.from_records(records, columns=list(DVOL_FIELDS)).sort_values("timestamp").reset_index(drop=True)


MAX_DVOL_PAGES = 1_000


def fetch_dvol(currency: str, start_ms: int, end_ms: int, *, resolution: int = 86_400, fetch: FetchJsonFn | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Walk DVOL candles newest-first: each truncated page returns the ``end_timestamp`` for the next call.

    A continuation that does not move the window earlier, or more than
    ``MAX_DVOL_PAGES`` pages, aborts; an empty page inside the span aborts.
    """
    fetch_fn = fetch or (lambda url, params: fetch_json(url, params))
    url = f"{BASE_URL}/public/get_volatility_index_data"
    pages: list[pd.DataFrame] = []
    calls: list[str] = []
    window_end = int(end_ms)
    for _ in range(MAX_DVOL_PAGES):
        params: dict[str, Any] = {"currency": currency, "resolution": resolution, "start_timestamp": int(start_ms), "end_timestamp": window_end}
        calls.append(f"{url}?currency={currency}&resolution={resolution}&start_timestamp={int(start_ms)}&end_timestamp={window_end}")
        envelope = fetch_fn(url, params)
        result = envelope.get("result") or {}
        data = result.get("data")
        if not isinstance(data, list) or not data:
            raise DeribitError(f"empty DVOL page for {currency} {int(start_ms)}..{window_end}")
        pages.append(parse_dvol_rows(data))
        continuation = result.get("continuation")
        if continuation is None:
            frame = dedup_concat(pages)
            return frame, calls
        try:
            next_end = int(continuation)
        except (TypeError, ValueError) as exc:
            raise DeribitError(f"non-integer DVOL continuation: {continuation!r}") from exc
        if next_end >= window_end or next_end < int(start_ms):
            raise DeribitError(f"DVOL continuation {next_end} does not move the window earlier from {window_end}")
        window_end = next_end
    raise DeribitError(f"DVOL paging exceeded {MAX_DVOL_PAGES} pages")
