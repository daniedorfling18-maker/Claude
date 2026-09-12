from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from premium_research import vrp

HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
START = 1_616_544_000_000  # 2021-03-24T00:00Z


def _spot(start_ms: int, hours: int, sigma_annual: float) -> pd.DataFrame:
    """Hourly closes whose log returns alternate +s/-s with s = sigma_annual / sqrt(8760)."""
    s = sigma_annual / math.sqrt(8_760)
    signs = np.array([1.0 if i % 2 == 0 else -1.0 for i in range(hours - 1)])
    log_closes = np.concatenate([[math.log(100.0)], math.log(100.0) + np.cumsum(signs * s)])
    return pd.DataFrame({"open_time": [start_ms + i * HOUR_MS for i in range(hours)], "close": np.exp(log_closes)})


def _dvol(days: list[int], level: float) -> pd.DataFrame:
    return pd.DataFrame({"timestamp": days, "open": [level] * len(days), "close": [level + 5.0] * len(days)})


def test_vrp_constant_vol_and_dvol_offset() -> None:
    spot = _spot(START, 720, 0.60)
    frame = vrp.vrp_series(_dvol([START], 70.0), spot, start_ms=START, end_ms=START + 720 * HOUR_MS)
    assert len(frame) == 1
    row = frame.iloc[0]
    assert not row["rejected"]
    assert row["valid_hours"] == 719
    assert round(float(row["realised_variance"]), 4) == 0.3600
    assert round(float(row["implied_variance"]), 4) == 0.4900
    assert round(float(row["vrp"]), 4) == 0.1300
    assert round(float(row["vrp_points"]), 1) == 1300.0


def test_window_with_699_valid_hours_is_rejected_and_700_is_not() -> None:
    end = START + 720 * HOUR_MS
    short = _spot(START, 700, 0.60)  # 700 closes -> 699 returns
    rejected = vrp.vrp_series(_dvol([START], 70.0), short, start_ms=START, end_ms=end)
    assert bool(rejected.iloc[0]["rejected"]) is True
    assert rejected.iloc[0]["rejected_reason"] == "insufficient_valid_hours:699"
    assert math.isnan(float(rejected.iloc[0]["vrp"]))
    enough = _spot(START, 701, 0.60)  # 701 closes -> 700 returns
    accepted = vrp.vrp_series(_dvol([START], 70.0), enough, start_ms=START, end_ms=end)
    assert bool(accepted.iloc[0]["rejected"]) is False
    assert accepted.iloc[0]["valid_hours"] == 700


def test_gap_hours_are_not_counted_as_valid_returns() -> None:
    spot = _spot(START, 720, 0.60)
    spot = spot.drop(index=range(300, 330))  # 30 missing hours -> 689 valid returns
    frame = vrp.vrp_series(_dvol([START], 70.0), spot, start_ms=START, end_ms=START + 720 * HOUR_MS)
    assert frame.iloc[0]["rejected_reason"] == "insufficient_valid_hours:688"


def test_missing_dvol_candle_on_window_start_is_rejected_not_filled() -> None:
    spot = _spot(START, 720 + 30 * 24, 0.60)
    end = START + (720 + 30 * 24) * HOUR_MS
    frame = vrp.vrp_series(_dvol([START + 30 * DAY_MS], 70.0), spot, start_ms=START, end_ms=end)
    assert len(frame) == 2
    assert frame.iloc[0]["rejected_reason"] == "no_dvol_candle_on_window_start"
    assert bool(frame.iloc[1]["rejected"]) is False


def test_window_starts_step_thirty_days_and_never_run_past_the_end() -> None:
    starts = vrp.window_starts(START, START + 100 * DAY_MS)
    assert starts == [START, START + 30 * DAY_MS, START + 60 * DAY_MS]  # 90 + 30 = 120 > 100
    with pytest.raises(ValueError):
        vrp.window_starts(START, START)


def test_yearly_means_use_the_window_start_year_and_skip_rejected() -> None:
    frame = pd.DataFrame(
        {
            "window_start_ms": [1_640_995_200_000, 1_672_531_200_000, 1_672_531_200_000 + 30 * DAY_MS],  # 2022-01-01, 2023-01-01, +30d
            "vrp": [0.10, 0.20, float("nan")],
            "rejected": [False, False, True],
        }
    )
    assert vrp.yearly_means(frame) == {"2022": 0.10, "2023": 0.20}


def test_pooled_windows_use_one_cluster_per_window_and_reject_when_either_currency_does() -> None:
    btc = pd.DataFrame({"window_start_ms": [1, 2, 3], "vrp": [0.10, 0.20, float("nan")], "rejected": [False, False, True]})
    eth = pd.DataFrame({"window_start_ms": [1, 2, 3], "vrp": [0.30, float("nan"), 0.40], "rejected": [False, True, False]})
    pooled = vrp.pooled_windows({"BTC": btc, "ETH": eth})
    assert pooled["rejected"].tolist() == [False, True, True]
    assert pooled["vrp"].tolist()[0] == pytest.approx(0.20)
    assert all(math.isnan(v) for v in pooled["vrp"].tolist()[1:])
