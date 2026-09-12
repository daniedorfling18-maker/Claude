"""Lane B — variance risk premium existence (WO-166).

``VRP_t = (DVOL_open_t / 100)^2 - RV^2_t`` in annualised decimal variance,
where ``DVOL_open_t`` is the DVOL index at 00:00 UTC of day ``t`` (the daily
candle's ``open``) and ``RV^2_t`` is the annualised realised variance of
Binance spot 1h log returns over the 720 hours starting at that same instant,
so the implied observation precedes every hour it is compared against.
Windows are non-overlapping 30-day steps. A window with fewer than 700 valid
hourly returns, or without a DVOL candle on its start day, is rejected and
counted; nothing is interpolated. The cluster unit for the pooled test is one
window: the window's VRP is the mean of the BTC and ETH values, and a window
is accepted only when both are.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
WINDOW_HOURS = 720
MIN_VALID_HOURS = 700
HOURS_PER_YEAR = 8_760
STEP_DAYS = 30
VARIANCE_POINTS = 10_000.0


def window_starts(start_ms: int, end_ms: int, *, step_days: int = STEP_DAYS, window_hours: int = WINDOW_HOURS) -> list[int]:
    """Window start timestamps from ``start_ms`` stepping ``step_days`` while the window ends on or before ``end_ms``."""
    if end_ms <= start_ms:
        raise ValueError(f"reversed or empty span {start_ms}..{end_ms}")
    starts: list[int] = []
    cursor = int(start_ms)
    step = step_days * DAY_MS
    window = window_hours * HOUR_MS
    while cursor + window <= end_ms:
        starts.append(cursor)
        cursor += step
    return starts


def realised_variance(spot_1h: pd.DataFrame, start_ms: int, *, window_hours: int = WINDOW_HOURS, min_valid_hours: int = MIN_VALID_HOURS) -> tuple[float, int]:
    """Annualised realised variance over ``[start, start + window)`` from hourly closes; returns ``(rv, n_valid)``.

    A return spanning more than one hour (a gap) is not a valid hourly return
    and is dropped; the count of valid returns drives both the annualisation
    and the rejection rule. ``rv`` is NaN when ``n_valid < min_valid_hours``.
    """
    end_ms = int(start_ms) + window_hours * HOUR_MS
    frame = spot_1h[(spot_1h["open_time"] >= int(start_ms)) & (spot_1h["open_time"] < end_ms)]
    if len(frame) < 2:
        return float("nan"), 0
    times = frame["open_time"].to_numpy(dtype=np.int64)
    closes = frame["close"].to_numpy(dtype=float)
    if not np.isfinite(closes).all() or (closes <= 0).any():
        raise ValueError("non-finite or non-positive spot close inside a realised-variance window")
    spacing = np.diff(times)
    log_returns = np.diff(np.log(closes))
    valid = spacing == HOUR_MS
    n_valid = int(valid.sum())
    if n_valid < min_valid_hours:
        return float("nan"), n_valid
    rv = float(np.sum(log_returns[valid] ** 2) * (HOURS_PER_YEAR / n_valid))
    return rv, n_valid


def vrp_series(dvol_daily: pd.DataFrame, spot_1h: pd.DataFrame, *, start_ms: int, end_ms: int, step_days: int = STEP_DAYS) -> pd.DataFrame:
    """One row per window: DVOL open, implied variance, realised variance, VRP, and a rejection flag with reason."""
    dvol_open = {int(ts): float(value) for ts, value in zip(dvol_daily["timestamp"], dvol_daily["open"])}
    records: list[dict[str, object]] = []
    for start in window_starts(start_ms, end_ms, step_days=step_days):
        level = dvol_open.get(start)
        rv, n_valid = realised_variance(spot_1h, start)
        rejected_reason = ""
        if level is None or not math.isfinite(level):
            rejected_reason = "no_dvol_candle_on_window_start"
        elif not math.isfinite(rv):
            rejected_reason = f"insufficient_valid_hours:{n_valid}"
        implied = (level / 100.0) ** 2 if level is not None and math.isfinite(level) else float("nan")
        vrp = implied - rv if not rejected_reason else float("nan")
        records.append(
            {
                "window_start_ms": int(start),
                "dvol_open": float(level) if level is not None else float("nan"),
                "implied_variance": implied,
                "realised_variance": rv,
                "valid_hours": int(n_valid),
                "vrp": vrp,
                "vrp_points": vrp * VARIANCE_POINTS if math.isfinite(vrp) else float("nan"),
                "rejected": bool(rejected_reason),
                "rejected_reason": rejected_reason,
            }
        )
    return pd.DataFrame.from_records(records)


def pooled_windows(per_currency: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One cluster per window start: the mean VRP across currencies, accepted only when every currency accepted it."""
    frames = list(per_currency.values())
    if not frames:
        raise ValueError("no currencies to pool")
    merged = None
    for name, frame in per_currency.items():
        part = frame[["window_start_ms", "vrp", "rejected"]].rename(columns={"vrp": f"vrp_{name}", "rejected": f"rejected_{name}"})
        merged = part if merged is None else merged.merge(part, on="window_start_ms", how="outer")
    assert merged is not None
    vrp_cols = [f"vrp_{name}" for name in per_currency]
    rej_cols = [f"rejected_{name}" for name in per_currency]
    merged[rej_cols] = merged[rej_cols].fillna(True).astype(bool)
    merged["rejected"] = merged[rej_cols].any(axis=1)
    merged["vrp"] = np.where(merged["rejected"], float("nan"), merged[vrp_cols].mean(axis=1))
    return merged.sort_values("window_start_ms").reset_index(drop=True)


def calendar_year(ts_ms: int) -> int:
    return int(pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").year)


def yearly_means(frame: pd.DataFrame, *, value_column: str = "vrp") -> dict[str, float]:
    """Mean per calendar year of the window start, over accepted windows only."""
    accepted = frame[~frame["rejected"]]
    if accepted.empty:
        return {}
    years = accepted["window_start_ms"].map(calendar_year)
    grouped = accepted.groupby(years)[value_column].mean()
    return {str(year): float(value) for year, value in grouped.items()}


def yearly_counts(frame: pd.DataFrame) -> dict[str, int]:
    accepted = frame[~frame["rejected"]]
    if accepted.empty:
        return {}
    years = accepted["window_start_ms"].map(calendar_year)
    return {str(year): int(count) for year, count in years.value_counts().sort_index().items()}
