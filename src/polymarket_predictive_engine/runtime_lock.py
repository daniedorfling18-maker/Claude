from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import EngineConfig
from .utils import ensure_dir, now_utc, parse_timestamp, read_json


_PROCESS_STARTED_AT_UTC = now_utc()


@dataclass(frozen=True)
class RuntimeLockResult:
    name: str
    path: Path
    acquired: bool
    payload: dict[str, Any]
    existing_payload: dict[str, Any] | None = None
    stale_lock_replaced: bool = False
    stale_lock_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "acquired": self.acquired,
            "payload": self.payload,
            "existing_payload": self.existing_payload or {},
            "stale_lock_replaced": self.stale_lock_replaced,
            "stale_lock_reason": self.stale_lock_reason,
        }


def runtime_lock_path(cfg: EngineConfig, name: str) -> Path:
    return cfg.output_root / "polymarket_runtime" / f"{name}.lock"


def _lock_age_seconds(payload: dict[str, Any]) -> float | None:
    acquired_at = parse_timestamp(payload.get("acquired_at_utc"))
    now = parse_timestamp(now_utc())
    if acquired_at is None or now is None:
        return None
    return max(0.0, (now - acquired_at).total_seconds())


def _lock_file_mtime_age_seconds(path: Path) -> float | None:
    """Wall-clock age of the lock file itself, independent of its payload.

    Used as a fallback when the payload carries no parseable acquisition time so a
    malformed lock (e.g. one whose JSON body was never written) can still be aged out.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, time.time() - mtime)


def _same_pid_lock_predates_current_process(payload: dict[str, Any]) -> bool:
    """Detect a persisted lock from a prior process that reused our PID.

    Container restarts can reuse the same small PID. If a lock file lives on a
    bind-mounted volume, a new process may see an old lock with its own PID and
    wait until the wall-clock stale timeout even though the owner process is
    gone. Comparing the lock's acquisition time to this process' start time
    lets the next process fail open for stale locks while preserving normal
    same-process mutual exclusion.
    """
    try:
        existing_pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return False
    if existing_pid != os.getpid():
        return False
    acquired_at = parse_timestamp(payload.get("acquired_at_utc"))
    process_started_at = parse_timestamp(_PROCESS_STARTED_AT_UTC)
    if acquired_at is None or process_started_at is None:
        return False
    return acquired_at < process_started_at


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
        "process_started_at_utc": _PROCESS_STARTED_AT_UTC,
        "acquired_at_utc": now_utc(),
    }
    try:
        _try_acquire(path, payload)
        return RuntimeLockResult(name=name, path=path, acquired=True, payload=payload)
    except FileExistsError:
        existing = read_json(path, default={}) or {}
        existing_payload = existing if isinstance(existing, dict) else {}
        age = _lock_age_seconds(existing_payload)
        stale_reason = ""
        if age is not None and stale_after_seconds > 0 and age > stale_after_seconds:
            stale_reason = f"age_seconds>{stale_after_seconds:g}"
        elif _same_pid_lock_predates_current_process(existing_payload):
            stale_reason = "same_pid_lock_predates_current_process"
        elif age is None and stale_after_seconds > 0:
            # A lock whose payload carries no parseable acquisition time (e.g. a
            # process killed after O_CREAT|O_EXCL but before its JSON body was
            # written) satisfies neither branch above and would otherwise deadlock
            # every future caller until an operator removes it. Fall back to the
            # lock file's own mtime so a genuinely abandoned malformed lock is still
            # reclaimed once it is older than the stale window (a lock mid-write has
            # a fresh mtime and is left alone).
            mtime_age = _lock_file_mtime_age_seconds(path)
            if mtime_age is not None and mtime_age > stale_after_seconds:
                stale_reason = f"malformed_lock_mtime_age_seconds>{stale_after_seconds:g}"
        if stale_reason:
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
                    stale_lock_reason=stale_reason,
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
