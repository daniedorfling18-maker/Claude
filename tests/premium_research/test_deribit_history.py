from __future__ import annotations

import json
from pathlib import Path

import pytest

from premium_research import deribit_history as dh

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "recorded"
FUNDING_PAGE = FIXTURES / "deribit_funding_history_BTC_2026-09-12.json"
DVOL_PAGE = FIXTURES / "deribit_dvol_BTC_2026-09-12.json"
HOUR_MS = 3_600_000


def _rows() -> list[dict]:
    return json.loads(FUNDING_PAGE.read_text(encoding="utf-8"))["result"]


def test_recorded_funding_page_has_744_hourly_rows() -> None:
    frame = dh.parse_funding_rows(_rows())
    assert len(frame) == 744
    assert int(frame["timestamp"].iloc[0]) == 1704070800000  # 2024-01-01T01:00Z: rows sit at hour ends
    assert int(frame["timestamp"].iloc[-1]) == 1706745600000  # the window's end_timestamp, inclusive
    assert (frame["timestamp"].diff().dropna() == HOUR_MS).all()


def test_deribit_pages_dedup_on_overlap() -> None:
    rows = _rows()
    page_a = dh.parse_funding_rows(rows)
    overlap = rows[720:744]  # identical last 24 rows of page A
    shifted = [{**row, "timestamp": int(row["timestamp"]) + 744 * HOUR_MS} for row in rows[:720]]
    page_b = dh.parse_funding_rows(overlap + shifted)
    assert len(page_b) == 744
    union = dh.dedup_concat([page_a, page_b])
    assert len(union) == 744 + 744 - 24
    assert union["timestamp"].is_monotonic_increasing
    assert not union["timestamp"].duplicated().any()


def test_conflicting_duplicate_timestamps_abort() -> None:
    rows = _rows()
    page_a = dh.parse_funding_rows(rows[:10])
    altered = [dict(rows[9], interest_8h=float(rows[9]["interest_8h"]) + 1e-6)]
    page_b = dh.parse_funding_rows(altered)
    with pytest.raises(dh.DeribitError, match="conflicting"):
        dh.dedup_concat([page_a, page_b])


def test_deribit_empty_page_inside_span_aborts() -> None:
    rows = _rows()
    calls: list[dict] = []

    def fetch(url: str, params: dict) -> dict:
        calls.append(dict(params))
        if len(calls) == 1:
            return {"result": rows}
        return {"result": []}

    start = 1704067200000
    with pytest.raises(dh.DeribitError, match="empty funding page"):
        dh.fetch_funding_history("BTC-PERPETUAL", start, start + 2 * dh.PAGE_MS, fetch=fetch)
    assert len(calls) == 2
    assert calls[0]["end_timestamp"] - calls[0]["start_timestamp"] == dh.PAGE_MS


def test_funding_windows_cover_the_span_without_gaps_or_overlap() -> None:
    start = 1704067200000
    windows = list(dh.funding_windows(start, start + 2 * dh.PAGE_MS + HOUR_MS))
    assert windows[0] == (start, start + dh.PAGE_MS)
    assert windows[1] == (start + dh.PAGE_MS, start + 2 * dh.PAGE_MS)
    assert windows[2] == (start + 2 * dh.PAGE_MS, start + 2 * dh.PAGE_MS + HOUR_MS)
    with pytest.raises(dh.DeribitError):
        list(dh.funding_windows(start, start))


def test_non_finite_funding_field_aborts() -> None:
    rows = _rows()[:3]
    rows[1] = dict(rows[1], interest_8h="nan")
    with pytest.raises(dh.DeribitError, match="non-finite"):
        dh.parse_funding_rows(rows)
    rows[1] = dict(rows[1], interest_8h=None)
    with pytest.raises(dh.DeribitError, match="empty"):
        dh.parse_funding_rows(rows)


def test_dvol_page_parses_and_continuation_is_followed() -> None:
    envelope = json.loads(DVOL_PAGE.read_text(encoding="utf-8"))
    data = envelope["result"]["data"]
    assert len(data) == 92
    calls: list[dict] = []

    def fetch(url: str, params: dict) -> dict:
        calls.append(dict(params))
        if "continuation" not in params:
            return {"result": {"data": data[:50], "continuation": "abc"}}
        return {"result": {"data": data[50:], "continuation": None}}

    frame, urls = dh.fetch_dvol("BTC", 1704067200000, 1711929600000, fetch=fetch)
    assert len(frame) == 92
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close"]
    assert float(frame["close"].iloc[0]) == 66.81
    assert calls[1]["continuation"] == "abc"
    assert len(urls) == 2


def test_dvol_empty_page_aborts() -> None:
    with pytest.raises(dh.DeribitError, match="empty DVOL page"):
        dh.fetch_dvol("BTC", 1704067200000, 1711929600000, fetch=lambda url, params: {"result": {"data": [], "continuation": None}})
