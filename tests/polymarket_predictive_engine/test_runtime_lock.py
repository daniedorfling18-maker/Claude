from __future__ import annotations

import json
import os
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


def test_runtime_lock_replaces_same_pid_lock_from_prior_process_start(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(runtime_lock, "now_utc", lambda: "2026-07-03T05:22:00Z")
    monkeypatch.setattr(runtime_lock, "_PROCESS_STARTED_AT_UTC", "2026-07-03T05:20:00Z")
    _write_lock(cfg, acquired_at_utc="2026-07-03T05:00:00Z")

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
    monkeypatch.setattr(runtime_lock, "now_utc", lambda: "2026-07-03T05:22:00Z")
    monkeypatch.setattr(runtime_lock, "_PROCESS_STARTED_AT_UTC", "2026-07-03T05:20:00Z")
    _write_lock(cfg, acquired_at_utc="2026-07-03T05:21:00Z")

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)

    assert lock.acquired is False
    assert lock.stale_lock_replaced is False
