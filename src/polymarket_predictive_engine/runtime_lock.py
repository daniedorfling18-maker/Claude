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
    """Age of the lock for stale-reclaim purposes.

    WO-143b.1 F1: measured from the LATER of ``acquired_at_utc`` and
    ``heartbeat_at_utc``. ``acquired_at_utc`` is never re-stamped -- it is read
    by other payload consumers and re-stamping would make the field's name
    false -- so a live, progressing holder keeps the lock fresh through the
    separate heartbeat field instead. A payload with no heartbeat behaves
    exactly as before.
    """
    acquired_at = parse_timestamp(payload.get("acquired_at_utc"))
    now = parse_timestamp(now_utc())
    if acquired_at is None or now is None:
        return None
    latest = acquired_at
    heartbeat_at = parse_timestamp(payload.get("heartbeat_at_utc"))
    # A FUTURE heartbeat is malformed evidence, not fresh evidence (Codex P1
    # wave-36). After a wall-clock correction or sidecar corruption, a
    # parseable future `heartbeat_at_utc` was selected here and its negative age
    # clamped to zero by the max() below -- and `_valid_lock_payload` performs
    # no heartbeat sanity check, so even a DEAD owner stayed non-stale until
    # that date arrived and every shadow update was skipped until then. A
    # permanent wedge from one bad field.
    #
    # Ignoring it degrades to the ordinary stale timeout on `acquired_at_utc`,
    # which is exactly the pre-heartbeat behaviour: a heartbeat may only ever
    # make a lock look FRESHER than its acquisition, never fresher than now.
    if heartbeat_at is not None and heartbeat_at > now:
        heartbeat_at = None
    if heartbeat_at is not None and heartbeat_at > latest:
        latest = heartbeat_at
    return max(0.0, (now - latest).total_seconds())


def _valid_lock_payload(payload: dict[str, Any]) -> bool:
    """Only payloads carrying every ownership field may block indefinitely."""
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return False
    return bool(
        payload.get("name")
        and pid > 0
        and parse_timestamp(payload.get("process_started_at_utc")) is not None
        and parse_timestamp(payload.get("acquired_at_utc")) is not None
    )


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
    # Publish the lock fully populated in a single atomic step: write the payload to a
    # sibling temp file, then hard-link it into place. os.link raises FileExistsError if
    # the lock already exists (preserving exclusive-create semantics), and once the lock
    # path exists it ALWAYS already contains its payload -- there is no observable
    # empty-lock window a crash or pause between create and write could leave behind for
    # another process to misjudge as dead. (The prior O_CREAT|O_EXCL-then-write left
    # exactly that window.)
    body = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    # The single outer try guarantees the temp file is removed on EVERY exit path --
    # a short/failed write, a link conflict, or success -- so a repeated storage
    # failure cannot accumulate orphan temp files. os.write may return a short count,
    # so loop until the whole payload is on disk before linking; a truncated body must
    # never become the published lock.
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        try:
            offset = 0
            while offset < len(body):
                offset += os.write(fd, body[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(str(tmp), str(path))  # raises FileExistsError if the lock already exists
    finally:
        try:
            os.unlink(str(tmp))
        except FileNotFoundError:
            pass
    return True


def _rewrite_lock_payload(path: Path, payload: dict[str, Any]) -> None:
    """Replace a HELD lock's payload in place, atomically.

    WO-143b.1 F1 mechanical constraint: ``_try_acquire`` publishes with
    ``os.link``, which cannot overwrite an existing path, so there is no API
    for updating a held lock. Unlink-then-recreate is explicitly FORBIDDEN --
    it opens a window in which another process can legitimately acquire the
    lock. Write a sibling temp file and ``os.replace`` it over the lock, which
    is atomic and never leaves the path absent.
    """
    body = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.beat")
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        try:
            offset = 0
            while offset < len(body):
                offset += os.write(fd, body[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(path))  # atomic; the path is never absent
    except BaseException:
        try:
            os.unlink(str(tmp))
        except FileNotFoundError:
            pass
        raise


class RuntimeLockHeartbeat:
    """Progress-derived heartbeat for a held runtime lock (WO-143b.1 F1).

    Opt-in per lock: a lock that does not request one is byte-identical to
    today. The heartbeat exists so a live holder is never judged stale, while a
    HUNG holder still is -- the difference between a bounded pause and a
    permanent wedge.

    It beats only when a monotonically increasing progress counter advanced
    since the previous beat, so a worker blocked inside a single unbounded
    read stops being beaten and becomes reclaimable on schedule. It is
    deliberately NOT timer-driven: a timer would beat a hung holder forever.

    Fail-safe: any heartbeat write failure is swallowed and recorded. The lock
    then simply ages as it does today (plain stale-timeout), never becoming
    unreclaimable.
    """

    def __init__(
        self,
        lock: RuntimeLockResult,
        *,
        cap_seconds: float,
        critical_section_max_seconds: float,
    ) -> None:
        self._lock = lock
        self._cap_seconds = float(cap_seconds)
        self._critical_section_max_seconds = float(critical_section_max_seconds)
        self._started_monotonic = time.monotonic()
        self._critical_section_entered_monotonic: float | None = None
        self._progress = 0
        self._progress_at_last_beat = -1
        self._beats = 0
        self._write_failures = 0
        self._cap_stopped = False
        self._critical_section_overran = False
        self._last_progress_label = ""

    # -- state ---------------------------------------------------------
    @property
    def beats(self) -> int:
        return self._beats

    @property
    def progress(self) -> int:
        return self._progress

    @property
    def critical_section_overran(self) -> bool:
        return self._critical_section_overran

    def _in_critical_section(self) -> bool:
        return self._critical_section_entered_monotonic is not None

    def _beating_allowed(self) -> bool:
        """Whether a beat may be written right now.

        Past ``cap_seconds`` the heartbeat normally stops and ordinary stale
        reclaim resumes. The cap is CONDITIONAL: it does not fire while inside
        the ledger-write critical section, because cutting the beat off
        mid-rewrite would invite exactly the reclaim-under-a-writer corruption
        this item exists to prevent. That carve-out is itself bounded by
        ``critical_section_max_seconds`` -- a bounded wedge beats an unbounded
        one.
        """
        elapsed = time.monotonic() - self._started_monotonic
        if elapsed <= self._cap_seconds:
            return True
        if not self._in_critical_section():
            self._cap_stopped = True
            return False
        entered = self._critical_section_entered_monotonic or time.monotonic()
        if (time.monotonic() - entered) <= self._critical_section_max_seconds:
            return True
        if not self._critical_section_overran:
            self._critical_section_overran = True
        return False

    def record_progress(self, label: str = "") -> None:
        """Advance the progress counter. Cheap: no I/O.

        Called at every phase boundary and every ledger-write step, so the
        counter covers the whole protected region -- not only settlement. The
        settlement position counter alone would stop advancing once
        ``_settle_due_positions`` returns, silencing the heartbeat for exactly
        the remainder phase that performs the ledger writes.
        """
        self._progress += 1
        if label:
            self._last_progress_label = label

    def maybe_beat(self) -> bool:
        """Write a heartbeat ONLY if the progress counter advanced since the last one.

        This gate is what separates a live holder from a hung one. A worker
        blocked inside a single unbounded read advances no counter, so however
        often this is sampled it writes nothing, the lock ages normally and
        reclaim works on schedule. A timer-driven beat would instead stamp
        forever and wedge the lane -- the failure this design exists to avoid.
        """
        if self._progress == self._progress_at_last_beat:
            return False
        if not self._beating_allowed():
            return False
        self._write_beat()
        return True

    def note_progress(self, label: str = "") -> bool:
        """Record progress and beat for it -- the common inline case."""
        self.record_progress(label)
        return self.maybe_beat()

    def _write_beat(self) -> None:
        payload = dict(self._lock.payload)
        stamp = now_utc()
        # Never re-stamp acquired_at_utc: other readers rely on it meaning
        # "when this lock was taken".
        payload["heartbeat_at_utc"] = stamp
        payload["heartbeat_count"] = self._beats + 1
        payload["last_progress_at_utc"] = stamp
        payload["progress_counter"] = self._progress
        if self._last_progress_label:
            payload["last_progress_step"] = self._last_progress_label
        try:
            _rewrite_lock_payload(self._lock.path, payload)
        except (OSError, ValueError):
            # Fail-safe: degrade to today's plain stale-timeout behaviour.
            self._write_failures += 1
            return
        self._beats += 1
        self._progress_at_last_beat = self._progress

    @contextmanager
    def critical_section(self) -> Iterator["RuntimeLockHeartbeat"]:
        """Mark the bounded ledger-write critical section.

        The section must contain only the atomic ``os.replace`` publishes --
        content is built in temp files OUTSIDE it -- so it is
        near-instantaneous by construction.
        """
        self._critical_section_entered_monotonic = time.monotonic()
        try:
            yield self
        finally:
            self._critical_section_entered_monotonic = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "heartbeat_beats": self._beats,
            "heartbeat_progress": self._progress,
            "heartbeat_write_failures": self._write_failures,
            "heartbeat_cap_stopped": self._cap_stopped,
            "heartbeat_critical_section_overran": self._critical_section_overran,
        }


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
        elif not _valid_lock_payload(existing_payload) and stale_after_seconds > 0:
            # Atomic publishing means this process never creates a malformed
            # lock.  Treat an externally corrupt lock as ambiguous until its
            # filesystem age crosses the same stale ceiling, then reclaim it.
            try:
                mtime_age = max(0.0, time.time() - path.stat().st_mtime)
            except (FileNotFoundError, OSError):
                mtime_age = 0.0
            if mtime_age > stale_after_seconds:
                stale_reason = f"corrupt_payload_mtime_age_seconds>{stale_after_seconds:g}"
        if stale_reason:
            # RENAME-TO-CLAIM, then VERIFY (Codex P1 wave-35 on #416). A plain
            # unlink here is a time-of-check/time-of-use hole: the decision above
            # was made from a payload read at :339, and a live holder can
            # heartbeat between that read and this line. The unlink then deletes
            # a FRESH heartbeat, the holder's os.replace reports success, the
            # contender acquires, and both publish the shadow ledgers
            # concurrently -- the precise failure this lock exists to prevent.
            #
            # os.rename is atomic, so exactly one of the two operations wins.
            # Whichever it is, the file we end up holding tells us: if it still
            # carries the payload we judged stale, our reclaim is sound; if it
            # carries anything else, the holder beat us and proved itself alive.
            claim = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.reclaim")
            claimed = True
            try:
                os.rename(str(path), str(claim))
            except (FileNotFoundError, OSError):
                claimed = False
            if claimed:
                captured = read_json(claim, default={}) or {}
                captured_payload = captured if isinstance(captured, dict) else {}
                if captured_payload != existing_payload:
                    # The holder heartbeated between the read and the rename.
                    # Put its evidence back and do NOT reclaim. os.link rather
                    # than os.rename, because the holder may already have
                    # republished at the original path and a rename would
                    # silently overwrite that newer file.
                    try:
                        os.link(str(claim), str(path))
                    except (FileExistsError, OSError):
                        pass
                    try:
                        os.unlink(str(claim))
                    except FileNotFoundError:
                        pass
                    return RuntimeLockResult(
                        name=name,
                        path=path,
                        acquired=False,
                        payload=payload,
                        existing_payload=captured_payload,
                    )
                try:
                    os.unlink(str(claim))
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


@contextmanager
def runtime_lock_with_heartbeat(
    cfg: EngineConfig,
    name: str,
    *,
    stale_after_seconds: float,
    heartbeat_cap_seconds: float,
    critical_section_max_seconds: float,
) -> Iterator[tuple[RuntimeLockResult, RuntimeLockHeartbeat | None]]:
    """``runtime_lock`` plus an opt-in progress-derived heartbeat (WO-143b.1 F1).

    **Opt-in per lock.** ``runtime_lock`` backs ``prediction_cycle`` and every
    other lane, so a heartbeat defect would wedge all of them. Only callers
    that explicitly use THIS entry point get heartbeat behaviour; every
    existing caller of ``runtime_lock`` above is untouched and byte-identical.

    Yields ``(lock, heartbeat)``; ``heartbeat`` is ``None`` when the lock was
    not acquired, so callers cannot accidentally beat a lock they do not hold.
    """
    lock = acquire_runtime_lock(cfg, name, stale_after_seconds=stale_after_seconds)
    heartbeat = (
        RuntimeLockHeartbeat(
            lock,
            cap_seconds=heartbeat_cap_seconds,
            critical_section_max_seconds=critical_section_max_seconds,
        )
        if lock.acquired
        else None
    )
    try:
        yield lock, heartbeat
    finally:
        release_runtime_lock(lock)
