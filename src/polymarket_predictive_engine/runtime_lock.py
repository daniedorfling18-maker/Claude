from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import EngineConfig
from .utils import ensure_dir, now_utc, parse_timestamp, read_json


@dataclass(frozen=True)
class RuntimeLockResult:
    name: str
    path: Path
    acquired: bool
    payload: dict[str, Any]
    existing_payload: dict[str, Any] | None = None
    stale_lock_replaced: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "acquired": self.acquired,
            "payload": self.payload,
            "existing_payload": self.existing_payload or {},
            "stale_lock_replaced": self.stale_lock_replaced,
        }


def runtime_lock_path(cfg: EngineConfig, name: str) -> Path:
    return cfg.output_root / "polymarket_runtime" / f"{name}.lock"


def _lock_age_seconds(payload: dict[str, Any]) -> float | None:
    acquired_at = parse_timestamp(payload.get("acquired_at_utc"))
    now = parse_timestamp(now_utc())
    if acquired_at is None or now is None:
        return None
    return max(0.0, (now - acquired_at).total_seconds())


def _try_acquire(path: Path, payload: dict[str, Any]) -> bool:
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"))
        os.write(fd, b"\n")
    finally:
        os.close(fd)
    return True


def acquire_runtime_lock(
    cfg: EngineConfig,
    name: str,
    *,
    stale_after_seconds: float = 1800.0,
) -> RuntimeLockResult:
    path = runtime_lock_path(cfg, name)
    ensure_dir(path.parent)
    payload = {
        "name": name,
        "pid": os.getpid(),
        "acquired_at_utc": now_utc(),
    }
    try:
        _try_acquire(path, payload)
        return RuntimeLockResult(name=name, path=path, acquired=True, payload=payload)
    except FileExistsError:
        existing = read_json(path, default={}) or {}
        existing_payload = existing if isinstance(existing, dict) else {}
        age = _lock_age_seconds(existing_payload)
        if age is not None and stale_after_seconds > 0 and age > stale_after_seconds:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            try:
                _try_acquire(path, payload)
                return RuntimeLockResult(
                    name=name,
                    path=path,
                    acquired=True,
                    payload=payload,
                    existing_payload=existing_payload,
                    stale_lock_replaced=True,
                )
            except FileExistsError:
                existing = read_json(path, default={}) or existing_payload
                existing_payload = existing if isinstance(existing, dict) else {}
        return RuntimeLockResult(
            name=name,
            path=path,
            acquired=False,
            payload=payload,
            existing_payload=existing_payload,
        )


def release_runtime_lock(lock: RuntimeLockResult) -> None:
    if not lock.acquired:
        return
    current = read_json(lock.path, default={}) or {}
    if isinstance(current, dict) and current.get("pid") not in {None, lock.payload.get("pid")}:
        return
    try:
        lock.path.unlink()
    except FileNotFoundError:
        pass


@contextmanager
def runtime_lock(
    cfg: EngineConfig,
    name: str,
    *,
    stale_after_seconds: float = 1800.0,
) -> Iterator[RuntimeLockResult]:
    lock = acquire_runtime_lock(cfg, name, stale_after_seconds=stale_after_seconds)
    try:
        yield lock
    finally:
        release_runtime_lock(lock)
