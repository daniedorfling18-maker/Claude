from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from premium_research import binance_vision as bv
from premium_research import cli

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "recorded"
FUNDING_FIXTURE = FIXTURES / "binance_vision_fundingRate_BTCUSDT_2024-01.csv"
KLINES_FIXTURE = FIXTURES / "binance_vision_klines_BTCUSDT_1h_2024-01_head.csv"


def _zip_bytes(csv_text: str, member: str = "x.csv") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, csv_text)
    return buffer.getvalue()


def _fake_fetch_for(objects: dict[str, bytes]):
    """Serve zip bytes and correct .CHECKSUM sidecars for the given URLs; 404 everything else."""

    def fetch(url: str) -> bytes:
        if url.endswith(".CHECKSUM"):
            base = url[: -len(".CHECKSUM")]
            if base not in objects:
                raise bv.VisionError(f"archive object missing (HTTP 404): {url}")
            name = base.rsplit("/", 1)[-1]
            return f"{hashlib.sha256(objects[base]).hexdigest()}  {name}\n".encode()
        if url not in objects:
            raise bv.VisionError(f"archive object missing (HTTP 404): {url}")
        return objects[url]

    return fetch


def test_binance_funding_fixture_month_loads_exactly() -> None:
    frame = bv.parse_funding_csv(FUNDING_FIXTURE.read_text(encoding="utf-8"))
    assert len(frame) == 93  # 31 days x 3 settlements
    assert int(frame["calc_time"].iloc[0]) == 1704067200000
    assert int(frame["calc_time"].iloc[-1]) == 1706716800000  # 2024-01-31T16:00Z = first + 92 x 8h
    assert (frame["funding_interval_hours"] == 8).all()
    bv.validate_funding_grid(frame)  # gap-free after snapping


def test_one_millisecond_jitter_snaps_to_grid() -> None:
    assert bv.snap_to_grid(1704412800001) == 1704412800000
    assert bv.snap_to_grid(1704412799000) == 1704412800000
    with pytest.raises(bv.VisionError):
        bv.snap_to_grid(1704412801001)  # 1,001 ms off the grid


def test_missing_month_aborts_without_partial_file(tmp_path: Path) -> None:
    good = _zip_bytes(FUNDING_FIXTURE.read_text(encoding="utf-8"))
    objects = {bv.funding_zip_url("BTCUSDT", "2024-01"): good}  # 2024-02 deliberately absent
    root = tmp_path / "premium_poc"
    with pytest.raises(bv.VisionError, match="404"):
        cli.run_fetch(
            root,
            fetch_bytes=_fake_fetch_for(objects),
            fetch_json=lambda url, params: {"result": []},
            now_iso=lambda: "2026-09-12T00:00:00Z",
            code_revision="test",
            start_month="2024-01",
            end_month="2024-02",
            symbols=("BTCUSDT",),
        )
    assert not (root / "data").exists()
    assert not (root / "manifest.json").exists()
    assert not list(tmp_path.glob(".premium_poc.fetch-tmp-*"))


def test_checksum_mismatch_aborts() -> None:
    url = bv.funding_zip_url("BTCUSDT", "2024-01")
    payload = _zip_bytes(FUNDING_FIXTURE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(payload).hexdigest()
    flipped = ("0" if digest[0] != "0" else "1") + digest[1:]

    def fetch(requested: str) -> bytes:
        if requested.endswith(".CHECKSUM"):
            return f"{flipped}  BTCUSDT-fundingRate-2024-01.zip\n".encode()
        return payload

    with pytest.raises(bv.VisionError, match="sha256 mismatch"):
        bv.download_funding("BTCUSDT", ["2024-01"], fetch=fetch)
    assert url.endswith("BTCUSDT-fundingRate-2024-01.zip")


def test_non_finite_funding_aborts() -> None:
    header = "calc_time,funding_interval_hours,last_funding_rate\n"
    with pytest.raises(bv.VisionError, match="non-finite"):
        bv.parse_funding_csv(header + "1704067200000,8,nan\n")
    with pytest.raises(bv.VisionError, match="empty"):
        bv.parse_funding_csv(header + "1704067200000,8,\n")
    with pytest.raises(bv.VisionError, match="not 8h"):
        bv.parse_funding_csv(header + "1704067200000,4,0.0001\n")


def test_klines_projection_keeps_only_ohlc() -> None:
    frame = bv.parse_klines_csv(KLINES_FIXTURE.read_text(encoding="utf-8"))
    assert list(frame.columns) == ["open_time", "open", "high", "low", "close"]
    assert len(frame) == 48
    assert int(frame["open_time"].iloc[0]) == 1704067200000
    assert int(frame["open_time"].iloc[-1]) == 1704067200000 + 47 * 3_600_000
    # a header-less file (the archive's older months) parses identically
    body = "\n".join(KLINES_FIXTURE.read_text(encoding="utf-8").splitlines()[1:]) + "\n"
    assert bv.parse_klines_csv(body).equals(frame)


def test_hourly_gap_longer_than_24_hours_aborts() -> None:
    frame = bv.parse_klines_csv(KLINES_FIXTURE.read_text(encoding="utf-8"))
    report = bv.hourly_gap_report(frame)
    assert report == {"missing_hours": 0, "longest_missing_run": 0, "rows": 48}
    shifted = frame.copy()
    shifted.loc[shifted.index[-1], "open_time"] = int(shifted["open_time"].iloc[-2]) + 26 * 3_600_000
    with pytest.raises(bv.VisionError, match="run of 25 missing hours"):
        bv.hourly_gap_report(shifted)


def test_funding_grid_gap_aborts() -> None:
    frame = bv.parse_funding_csv(FUNDING_FIXTURE.read_text(encoding="utf-8"))
    with pytest.raises(bv.VisionError, match="missing points"):
        bv.validate_funding_grid(frame.drop(index=10))
    with pytest.raises(bv.VisionError, match="duplicated"):
        bv.validate_funding_grid(pd.concat([frame, frame.iloc[[3]]], ignore_index=True))


def test_fetch_wall_clock_deadline_aborts_without_partial_file(tmp_path: Path) -> None:
    good = _zip_bytes(FUNDING_FIXTURE.read_text(encoding="utf-8"))
    objects = {bv.funding_zip_url("BTCUSDT", "2024-01"): good}
    ticks = iter([0.0, 10.0, 4000.0, 4000.0, 4000.0])  # the third request sees the deadline expired
    root = tmp_path / "premium_poc"
    with pytest.raises(RuntimeError, match="wall-clock deadline"):
        cli.run_fetch(
            root,
            fetch_bytes=_fake_fetch_for(objects),
            fetch_json=lambda url, params: {"result": []},
            now_iso=lambda: "2026-09-12T00:00:00Z",
            code_revision="test",
            start_month="2024-01",
            end_month="2024-01",
            symbols=("BTCUSDT",),
            deadline_seconds=3600.0,
            clock=lambda: next(ticks),
        )
    assert not (root / "data").exists()
    assert not list(tmp_path.glob(".premium_poc.fetch-tmp-*"))
