"""Binance Vision archive access (WO-166).

The archive at ``https://data.binance.vision`` is a static bucket of monthly
zip files, each with a ``.CHECKSUM`` sidecar holding the sha256 of the zip.
Everything here fails closed: a missing month, a checksum mismatch, a grid
point off by more than the tolerance, a duplicated or missing grid point, a
non-finite value, or a funding interval other than 8 hours raises
:class:`VisionError`. Nothing is forward-filled, interpolated, or skipped.

Only :func:`fetch_bytes` touches the network; every caller takes an injected
``fetch`` callable so tests never do.
"""

from __future__ import annotations

import hashlib
import io
import math
import zipfile
from collections.abc import Callable, Iterable
from datetime import datetime, timezone

import pandas as pd

BASE_URL = "https://data.binance.vision/data"
FUNDING_INTERVAL_HOURS = 8
FUNDING_INTERVAL_MS = FUNDING_INTERVAL_HOURS * 3_600_000
HOUR_MS = 3_600_000
GRID_TOLERANCE_MS = 1_000
MAX_CONSECUTIVE_MISSING_HOURS = 24

FUNDING_COLUMNS = ("calc_time", "funding_interval_hours", "last_funding_rate")
OUTPUT_FUNDING_COLUMNS = ("calc_time", "calc_time_raw", "funding_interval_hours", "last_funding_rate")
KLINE_COLUMNS = ("open_time", "open", "high", "low", "close")

FetchFn = Callable[[str], bytes]


class VisionError(RuntimeError):
    """Raised on any archive defect. The fail-safe direction is always 'no data'."""


# --------------------------------------------------------------------------- URLs


def month_range(start: str, end: str) -> list[str]:
    """Inclusive list of ``YYYY-MM`` strings from ``start`` to ``end``."""
    try:
        sy, sm = (int(part) for part in start.split("-"))
        ey, em = (int(part) for part in end.split("-"))
    except ValueError as exc:  # pragma: no cover - defensive
        raise VisionError(f"month must be YYYY-MM, got {start!r} / {end!r}") from exc
    if (sy, sm) > (ey, em):
        raise VisionError(f"month range is reversed: {start} > {end}")
    months: list[str] = []
    year, month = sy, sm
    while (year, month) <= (ey, em):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


def funding_zip_url(symbol: str, month: str) -> str:
    return f"{BASE_URL}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"


def klines_zip_url(market: str, symbol: str, interval: str, month: str) -> str:
    if market == "spot":
        return f"{BASE_URL}/spot/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    if market == "um":
        return f"{BASE_URL}/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"
    raise VisionError(f"market must be 'spot' or 'um', got {market!r}")


def checksum_url(zip_url: str) -> str:
    return zip_url + ".CHECKSUM"


# ------------------------------------------------------------------------ network


def fetch_bytes(url: str, *, timeout: float = 60.0, retries: int = 3) -> bytes:
    """GET ``url`` and return the body. Any status other than 200 raises.

    ``timeout`` is a per-socket-operation timeout (connect, then each read), not a
    deadline; the caller owns the wall-clock budget.
    """
    import requests  # local import: keeps the pure modules import-light

    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            response = requests.get(url, timeout=timeout)
        except requests.RequestException as exc:  # pragma: no cover - network path
            last_error = exc
            continue
        if response.status_code == 200:
            return response.content
        if response.status_code == 404:
            raise VisionError(f"archive object missing (HTTP 404): {url}")
        last_error = VisionError(f"HTTP {response.status_code} for {url}")
    raise VisionError(f"fetch failed after {retries} attempts for {url}: {last_error}")


def parse_checksum_sidecar(text: str, expected_name: str | None = None) -> str:
    """Parse ``<sha256 hex>  <filename>`` and return the hex digest."""
    parts = text.strip().split()
    if len(parts) < 1 or len(parts[0]) != 64:
        raise VisionError(f"unparseable .CHECKSUM sidecar: {text[:80]!r}")
    digest = parts[0].lower()
    try:
        int(digest, 16)
    except ValueError as exc:
        raise VisionError(f"non-hex .CHECKSUM digest: {digest!r}") from exc
    if expected_name is not None and len(parts) >= 2 and parts[1] != expected_name:
        raise VisionError(f".CHECKSUM names {parts[1]!r}, expected {expected_name!r}")
    return digest


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected_hex: str, *, label: str = "") -> str:
    actual = sha256_hex(data)
    if actual != expected_hex.lower():
        raise VisionError(f"sha256 mismatch for {label or 'object'}: expected {expected_hex}, got {actual}")
    return actual


def extract_single_csv(zip_bytes: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise VisionError("archive object is not a zip file") from exc
    names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
    if len(names) != 1:
        raise VisionError(f"expected exactly one CSV member, found {names}")
    return archive.read(names[0]).decode("utf-8")


# ------------------------------------------------------------------------ parsing


def snap_to_grid(ts_ms: int, *, interval_ms: int = FUNDING_INTERVAL_MS, tolerance_ms: int = GRID_TOLERANCE_MS) -> int:
    """Snap ``ts_ms`` to the nearest multiple of ``interval_ms`` if within tolerance, else raise."""
    nearest = int(round(ts_ms / interval_ms)) * interval_ms
    if abs(ts_ms - nearest) > tolerance_ms:
        raise VisionError(f"timestamp {ts_ms} is {abs(ts_ms - nearest)} ms off the {interval_ms} ms grid (tolerance {tolerance_ms} ms)")
    return nearest


def timestamp_to_ms(raw: str, *, label: str) -> int:
    """Accept a 13-digit millisecond or a 16-digit microsecond timestamp; anything else aborts.

    The spot klines archive switched ``open_time`` to microseconds from 2025-01 while the
    USDT-M futures archive stayed in milliseconds; a 16-digit value is divided by 1,000 and
    must divide exactly.
    """
    text = raw.strip()
    if not text.isdigit():
        raise VisionError(f"non-integer {label}: {raw!r}")
    if len(text) == 13:
        return int(text)
    if len(text) == 16:
        value = int(text)
        if value % 1000:
            raise VisionError(f"microsecond {label} {text} is not a whole millisecond")
        return value // 1000
    raise VisionError(f"{label} {text!r} has {len(text)} digits; 13 (ms) or 16 (us) expected")


def _has_header(first_line: str) -> bool:
    stripped = first_line.strip()
    return bool(stripped) and not stripped[0].isdigit()


def _finite_float(raw: str, *, label: str) -> float:
    text = raw.strip()
    if not text:
        raise VisionError(f"empty {label}")
    try:
        value = float(text)
    except ValueError as exc:
        raise VisionError(f"non-numeric {label}: {text!r}") from exc
    if not math.isfinite(value):
        raise VisionError(f"non-finite {label}: {text!r}")
    return value


def parse_funding_csv(text: str) -> pd.DataFrame:
    """Parse one monthly funding CSV into ``calc_time`` (snapped to the 8h grid), ``calc_time_raw`` (as received), ``funding_interval_hours``, ``last_funding_rate``.

    A header row is optional: the archive ships funding files with one and
    klines files with or without one depending on the month, so the first
    line is treated as a header only when it does not start with a digit.
    """
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise VisionError("funding CSV is empty")
    if _has_header(lines[0]):
        header = tuple(part.strip() for part in lines[0].split(","))
        if header != FUNDING_COLUMNS:
            raise VisionError(f"unexpected funding header {header}")
        lines = lines[1:]
    if not lines:
        raise VisionError("funding CSV has a header but no rows")
    calc_times: list[int] = []
    raw_times: list[int] = []
    intervals: list[int] = []
    rates: list[float] = []
    for line in lines:
        parts = line.split(",")
        if len(parts) != 3:
            raise VisionError(f"funding row does not have 3 fields: {line!r}")
        raw_ts = timestamp_to_ms(parts[0], label="calc_time")
        interval_text = parts[1].strip()
        if not interval_text.isdigit():
            raise VisionError(f"funding_interval_hours is not an integer: {interval_text!r}")
        interval = int(interval_text)
        if interval != FUNDING_INTERVAL_HOURS:
            raise VisionError(f"funding interval {interval}h at {raw_ts} is not {FUNDING_INTERVAL_HOURS}h")
        calc_times.append(snap_to_grid(raw_ts))
        raw_times.append(raw_ts)
        intervals.append(interval)
        rates.append(_finite_float(parts[2], label="last_funding_rate"))
    frame = pd.DataFrame({"calc_time": calc_times, "calc_time_raw": raw_times, "funding_interval_hours": intervals, "last_funding_rate": rates})
    return frame.sort_values("calc_time").reset_index(drop=True)


def parse_klines_csv(text: str) -> pd.DataFrame:
    """Parse one monthly klines CSV and keep only ``open_time, open, high, low, close``."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise VisionError("klines CSV is empty")
    if _has_header(lines[0]):
        lines = lines[1:]
    if not lines:
        raise VisionError("klines CSV has a header but no rows")
    open_times: list[int] = []
    cols: dict[str, list[float]] = {"open": [], "high": [], "low": [], "close": []}
    for line in lines:
        parts = line.split(",")
        if len(parts) < 5:
            raise VisionError(f"klines row has fewer than 5 fields: {line!r}")
        raw_ts = timestamp_to_ms(parts[0], label="open_time")
        if raw_ts % HOUR_MS:
            raise VisionError(f"open_time {raw_ts} is not hour-aligned")
        open_times.append(raw_ts)  # stored as received; the archive's klines carry no jitter
        for index, name in enumerate(("open", "high", "low", "close"), start=1):
            cols[name].append(_finite_float(parts[index], label=name))
    frame = pd.DataFrame({"open_time": open_times, **cols})
    return frame.sort_values("open_time").reset_index(drop=True)


# ---------------------------------------------------------------------- validation


def validate_funding_grid(frame: pd.DataFrame) -> None:
    """Sorted, unique, and gap-free on the 8h grid between first and last; otherwise raise."""
    if frame.empty:
        raise VisionError("funding frame is empty")
    times = frame["calc_time"].to_numpy()
    if len(set(times.tolist())) != len(times):
        dupes = frame[frame["calc_time"].duplicated()]["calc_time"].tolist()[:5]
        raise VisionError(f"duplicated funding grid points: {dupes}")
    expected = (int(times[-1]) - int(times[0])) // FUNDING_INTERVAL_MS + 1
    if expected != len(times):
        present = set(times.tolist())
        missing = [t for t in range(int(times[0]), int(times[-1]) + 1, FUNDING_INTERVAL_MS) if t not in present]
        raise VisionError(f"funding grid has {len(missing)} missing points, first {missing[:3]}")


def hourly_gap_report(frame: pd.DataFrame, *, max_consecutive_missing: int = MAX_CONSECUTIVE_MISSING_HOURS) -> dict[str, int]:
    """Count missing hours; raise when any run of missing hours exceeds the limit."""
    if frame.empty:
        raise VisionError("hourly frame is empty")
    times = frame["open_time"].to_numpy()
    if len(set(times.tolist())) != len(times):
        raise VisionError("duplicated hourly open_time values")
    missing_total = 0
    longest_run = 0
    previous = int(times[0])
    for current in times[1:]:
        gap_hours = (int(current) - previous) // HOUR_MS - 1
        if gap_hours > 0:
            missing_total += gap_hours
            longest_run = max(longest_run, gap_hours)
        previous = int(current)
    if longest_run > max_consecutive_missing:
        raise VisionError(f"hourly series has a run of {longest_run} missing hours (limit {max_consecutive_missing})")
    return {"missing_hours": missing_total, "longest_missing_run": longest_run, "rows": int(len(times))}


# ------------------------------------------------------------------------ download


def _download_verified_csv(zip_url: str, fetch: FetchFn) -> tuple[str, str]:
    """Return ``(csv_text, sha256_hex)`` after verifying the archive's own sidecar."""
    name = zip_url.rsplit("/", 1)[-1]
    sidecar = fetch(checksum_url(zip_url))
    expected = parse_checksum_sidecar(sidecar.decode("utf-8", errors="replace"), expected_name=name)
    payload = fetch(zip_url)
    digest = verify_sha256(payload, expected, label=name)
    return extract_single_csv(payload), digest


def download_funding(symbol: str, months: Iterable[str], *, fetch: FetchFn = fetch_bytes) -> tuple[pd.DataFrame, dict[str, str]]:
    """Concatenate verified monthly funding files; returns the frame and ``{url: sha256}``."""
    frames: list[pd.DataFrame] = []
    digests: dict[str, str] = {}
    for month in months:
        url = funding_zip_url(symbol, month)
        text, digest = _download_verified_csv(url, fetch)
        digests[url] = digest
        frames.append(parse_funding_csv(text))
    if not frames:
        raise VisionError("no months requested")
    frame = pd.concat(frames, ignore_index=True).sort_values("calc_time").reset_index(drop=True)
    validate_funding_grid(frame)
    return frame, digests


def download_klines(market: str, symbol: str, interval: str, months: Iterable[str], *, fetch: FetchFn = fetch_bytes) -> tuple[pd.DataFrame, dict[str, str], dict[str, int]]:
    """Concatenate verified monthly klines; returns the frame, ``{url: sha256}`` and the gap report."""
    frames: list[pd.DataFrame] = []
    digests: dict[str, str] = {}
    for month in months:
        url = klines_zip_url(market, symbol, interval, month)
        text, digest = _download_verified_csv(url, fetch)
        digests[url] = digest
        frames.append(parse_klines_csv(text))
    if not frames:
        raise VisionError("no months requested")
    frame = pd.concat(frames, ignore_index=True).sort_values("open_time").reset_index(drop=True)
    gaps = hourly_gap_report(frame)
    return frame, digests, gaps


def iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
