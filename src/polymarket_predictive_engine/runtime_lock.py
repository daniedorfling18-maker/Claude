from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .config import EngineConfig
from .utils import ensure_dir, now_utc, parse_timestamp, read_json


_PROCESS_STARTED_AT_UTC = now_utc()
# A NAMESPACE-INDEPENDENT owner token (Codex P1 wave-40). `pid` alone does not
# identify a process across containers: the VPS Compose stack runs the live loop
# and the scheduler in SEPARATE PID namespaces while sharing ./outputs, so two
# live processes routinely hold the same namespace-local number. With only that
# number plus a second-resolution start stamp,
# `_same_pid_lock_predates_current_process` could read a FRESH holder in another
# container as this process' own dead orphan and unlink its lock -- admitting
# both shadow-ledger writers, which is the failure the lock exists to prevent.
#
# uuid4 is unique without coordination and needs no host facts (no boot id, no
# cgroup path, nothing to read or parse), so it cannot collide across namespaces
# and cannot fail on a platform that does not expose those.
_PROCESS_OWNER_TOKEN = uuid.uuid4().hex


class RuntimeLockOwnershipLost(RuntimeError):
    """Raised when a holder discovers the lock it is publishing under is not its own.

    WO-143b.1 (Codex P1 wave-42). Age-based stale reclaim can steal a lock from
    a holder that is in fact alive; no filesystem primitive prevents that, so
    the holder detects it instead and stops before writing. Deliberately a
    distinct type so callers can tell "I lost the lock" from an I/O failure.
    """


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


def _same_owner_lock_predates_current_process(payload: dict[str, Any]) -> bool:
    """Detect a persisted lock left behind by an EARLIER RUN OF THIS PROCESS.

    A container restart can reuse the same small PID, so a lock on a bind-mounted
    volume can carry this process' own number while its writer is long gone;
    without this check the next run waits out the whole stale timeout for
    nothing.

    Matched on `owner_token`, NOT on pid (Codex P1 wave-40). A pid identifies a
    process only within its namespace, and the VPS Compose stack runs the live
    loop and the scheduler in SEPARATE namespaces sharing ./outputs -- so two
    LIVE processes routinely carry the same number, and pid equality plus an
    older acquisition time was treated as proof of a dead orphan. That let this
    check unlink a fresh holder in another container and admit both
    shadow-ledger writers: the exact concurrency failure the lock exists to
    prevent, sitting inside the mechanism meant to prevent it.

    A payload with no `owner_token` predates this field and can no longer be
    matched. It falls through to the ordinary stale timeout, which is the
    conservative direction: a slower reclaim, never a wrongful one.
    """
    existing_token = str(payload.get("owner_token") or "")
    if not existing_token or existing_token != _PROCESS_OWNER_TOKEN:
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
        self._ownership_lost = False
        self._ownership_checks_failed = 0
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

    @property
    def ownership_lost(self) -> bool:
        return self._ownership_lost

    def _still_owns_lock(self) -> bool | None:
        """Whether the lock file still names US. ``None`` when unreadable.

        THE ACQUISITION WINDOW IN STALE RECLAIM CANNOT BE CLOSED FROM HERE, SO
        THE CONSEQUENCE IS CLOSED INSTEAD (Codex P1 wave-42). Reclaim renames
        the stale lock aside, verifies the captured payload, and restores it
        when a live holder heartbeated in between -- but between the rename and
        the restore the lock path does not exist, and a third caller can
        acquire in that gap. The restore then loses, and the original holder,
        whose heartbeat succeeded, still believes it holds the lock.

        No filesystem CAS closes that gap: ``os.replace`` rebinds a name
        atomically but unconditionally, so there is no "replace only if it still
        names the payload I read". This is not specific to rename-to-claim --
        ANY age-based steal from a holder that turns out to be alive admits two
        writers, which is why ``stale_after_seconds=0.0`` is the registered
        setting on the locks that cannot tolerate it at all.

        What IS decidable is whether WE still hold the lock, at the moment it
        matters: immediately before publishing. A holder that has lost the lock
        must not write the ledgers and must not keep stamping heartbeats over
        the new holder's payload -- the second of which is its own defect, since
        it would overwrite the owner token the new holder's release validates
        and strand the lock.

        Fail-safe direction, deliberately split:
          - ABSENT, or present with a DIFFERENT ``owner_token`` -> we do not
            hold it. A foreign token is proof outright. Absence is not proof
            that someone else took it, but it IS proof we do not hold it at
            this instant, which is the question being asked; this process never
            unlinks its own lock before release.
          - unreadable or malformed -> ambiguous, and counted rather than
            treated as loss, so a transient read error cannot abort a pass.

        ``read_json`` returns its default for a MISSING file and for an
        unreadable one alike, so the existence check is made separately -- the
        two answers here are different and collapsing them would put the
        ambiguous case on the fail-closed side.

        REGISTERED FALSE POSITIVE, accepted deliberately: reclaim's
        rename-to-claim removes the lock path for the length of its verify, so a
        probe landing inside that window sees absence and latches loss even when
        the payload is about to be restored. That costs an aborted pass, which
        the next pass redoes; the alternative reading -- assume we still hold it
        -- costs two concurrent ledger writers, which is the failure the lock
        exists to prevent. The flag is latched rather than re-tested for the
        same reason: during that window a third caller may have acquired, and
        nothing observable afterwards distinguishes that from a clean restore.
        """
        path = self._lock.path
        try:
            present = path.exists()
        except OSError:
            self._ownership_checks_failed += 1
            return None
        if not present:
            self._ownership_lost = True
            return False
        raw = read_json(path, default=None)
        if not isinstance(raw, dict):
            self._ownership_checks_failed += 1
            return None
        token = str(raw.get("fencing_token") or "")
        if not token:
            # A payload with no fencing token predates this field or was written
            # by something else. Ambiguous, not proof.
            self._ownership_checks_failed += 1
            return None
        if token != str(self._lock.payload.get("fencing_token") or ""):
            self._ownership_lost = True
            return False
        return True

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
        if self._still_owns_lock() is False:
            # Republishing here would overwrite the NEW holder's payload with
            # ours -- including its owner token, which its own
            # ``release_runtime_lock`` validates before unlinking. It would then
            # refuse to release and strand the lock until the stale window
            # elapsed again. Losing the lock is bad; stranding the lane on the
            # way out is worse.
            return
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
        if self._still_owns_lock() is False:
            # FAIL CLOSED AT THE ONE POINT WHERE IT MATTERS. The publishes
            # inside this section are the writes the lock exists to serialise,
            # so a holder that has provably lost the lock stops here rather
            # than racing the new one. Ambiguity (an unreadable payload) does
            # not stop the pass -- it is counted in the diagnostics instead.
            raise RuntimeLockOwnershipLost(
                f"runtime lock {self._lock.name!r} at {self._lock.path} is no longer held by this "
                "process; refusing to publish the artifacts it serialises. A stale reclaim took the "
                "lock while this holder was judged aged-out."
            )
        # A CAP-EXEMPT BEAT ON THE WAY IN (Codex P1 wave-42). Past
        # `cap_seconds` the beat immediately BEFORE this section is refused --
        # `_beating_allowed` only makes its carve-out once the section is
        # already open -- so the section could be entered with a heartbeat
        # already close to the stale threshold, and a contender could reclaim
        # while an `os.replace` was running. The exit-only overrun flag records
        # that afterwards; it does not prevent it. Refreshing here is the same
        # carve-out the cap already grants INSIDE the section, applied at the
        # boundary so the whole bounded section starts from a fresh stamp.
        #
        # `_write_beat` directly, not `maybe_beat`: the progress gate would
        # refuse this beat whenever the caller's last step advanced no counter,
        # which is exactly the case here. It re-checks ownership itself, so a
        # lost lock still cannot be stamped over.
        self._write_beat()
        entered = time.monotonic()
        self._critical_section_entered_monotonic = entered
        try:
            yield self
        finally:
            # MEASURED ON EXIT, not only by a beat landing while open (Codex P2
            # wave-40). `_beating_allowed` sets the overrun flag, so the flag
            # could only ever be raised if a heartbeat happened to be attempted
            # DURING the section -- and once the beats were correctly moved
            # outside it (wave-38, to keep the section replace-only), nothing
            # inside could raise it at all. A blocking os.replace would clear
            # the start time here and the summary would omit
            # shadow_ledger_critical_section_overran entirely: the bound that
            # makes "replace-only" auditable, silently unenforceable.
            #
            # Measuring here needs no beat and cannot be moved out of reach by
            # rearranging the callers.
            if (time.monotonic() - entered) > self._critical_section_max_seconds:
                self._critical_section_overran = True
            self._critical_section_entered_monotonic = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "heartbeat_beats": self._beats,
            "heartbeat_progress": self._progress,
            "heartbeat_write_failures": self._write_failures,
            "heartbeat_cap_stopped": self._cap_stopped,
            "heartbeat_critical_section_overran": self._critical_section_overran,
            "heartbeat_ownership_lost": self._ownership_lost,
            "heartbeat_ownership_checks_inconclusive": self._ownership_checks_failed,
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
        # PROCESS identity: shared by every acquisition this process makes, and
        # used only by `_same_owner_lock_predates_current_process`, whose
        # question is "did MY process write this before it started".
        "owner_token": _PROCESS_OWNER_TOKEN,
        # ACQUISITION identity: unique to this one acquire (Codex P1 wave-42).
        # The process token cannot distinguish a stalled holder from the thread
        # in the SAME process that reclaimed the lock out from under it, so both
        # passed the ownership check, both could publish, and the resumed holder
        # could even unlink the replacement lock on its way out. Ownership
        # validation and release both key on this instead.
        "fencing_token": uuid.uuid4().hex,
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
        elif _same_owner_lock_predates_current_process(existing_payload):
            stale_reason = "same_owner_lock_predates_current_process"
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
    # Validated on the OWNER TOKEN too (Codex P1 wave-40): a pid check alone
    # would let this process delete a lock another container's identically
    # numbered process had since taken.
    current = read_json(lock.path, default={}) or {}
    if isinstance(current, dict):
        # THE ACQUISITION token, not the process one (Codex P1 wave-42): two
        # acquisitions in the same process share `owner_token`, so a stalled
        # holder whose lock was reclaimed by a sibling thread would pass that
        # check and delete the sibling's live lock.
        current_fencing = current.get("fencing_token")
        if current_fencing not in {None, lock.payload.get("fencing_token")}:
            return
        current_token = current.get("owner_token")
        if current_token not in {None, lock.payload.get("owner_token")}:
            return
        if current.get("pid") not in {None, lock.payload.get("pid")}:
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
