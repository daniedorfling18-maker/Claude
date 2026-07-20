from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_predictive_engine import runtime_lock
from polymarket_predictive_engine.config import EngineConfig


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "config.yaml")


def _write_lock(cfg: EngineConfig, *, acquired_at_utc: str) -> Path:
    path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": "prediction_cycle",
                "pid": os.getpid(),
                "acquired_at_utc": acquired_at_utc,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_runtime_lock_blocks_second_acquire_in_same_process(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)

    first = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)
    assert first.acquired is True

    second = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)
    assert second.acquired is False
    assert second.stale_lock_replaced is False

    runtime_lock.release_runtime_lock(first)
    third = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)
    assert third.acquired is True
    runtime_lock.release_runtime_lock(third)


def _iso_minutes_ago(minutes: float) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_runtime_lock_replaces_same_pid_lock_from_prior_process_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # S1 (ENGINEERING_STANDARDS): fixtures are clock-relative. The original
    # hardcoded 2026-07-03 stamps became a time bomb — once wall-clock age
    # crossed the 999999s staleness window (2026-07-14), the age branch won
    # and the same-pid branch under test was never reached.
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(runtime_lock, "_PROCESS_STARTED_AT_UTC", _iso_minutes_ago(10))
    _write_lock(cfg, acquired_at_utc=_iso_minutes_ago(30))

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)

    assert lock.acquired is True
    assert lock.stale_lock_replaced is True
    assert lock.stale_lock_reason == "same_pid_lock_predates_current_process"
    runtime_lock.release_runtime_lock(lock)


def test_runtime_lock_keeps_same_process_lock_after_process_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(runtime_lock, "_PROCESS_STARTED_AT_UTC", _iso_minutes_ago(30))
    _write_lock(cfg, acquired_at_utc=_iso_minutes_ago(10))

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)

    assert lock.acquired is False
    assert lock.stale_lock_replaced is False
