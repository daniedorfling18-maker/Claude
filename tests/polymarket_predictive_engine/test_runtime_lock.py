from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polymarket_predictive_engine import runtime_lock
from polymarket_predictive_engine.config import EngineConfig


def _cfg(tmp_path: Path) -> EngineConfig:
    return EngineConfig(raw={"paths": {"output_root": str(tmp_path / "outputs")}}, path=tmp_path / "config.yaml")


def _write_lock(
    cfg: EngineConfig, *, acquired_at_utc: str, owner_token: str | None = None
) -> Path:
    """A persisted lock. `owner_token` defaults to THIS process' token.

    The token, not the pid, is what identifies an owner (wave-40): a pid is
    namespace-local, and the VPS stack shares ./outputs across namespaces.
    """
    payload = {
        "name": "prediction_cycle",
        "pid": os.getpid(),
        "acquired_at_utc": acquired_at_utc,
        "owner_token": runtime_lock._PROCESS_OWNER_TOKEN if owner_token is None else owner_token,
    }
    path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
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


def test_runtime_lock_replaces_same_owner_lock_from_prior_process_start(
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
    assert lock.stale_lock_reason == "same_owner_lock_predates_current_process"
    runtime_lock.release_runtime_lock(lock)


def test_runtime_lock_does_not_reclaim_a_foreign_owner_sharing_our_pid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A pid match is not an ownership match (Codex P1 wave-40).

    The VPS Compose stack runs the live loop and the scheduler in SEPARATE PID
    namespaces while sharing ./outputs, so two LIVE processes routinely carry
    the same namespace-local number. Matching on pid alone read the other
    container's fresh holder as this process' own dead orphan and unlinked it,
    admitting both shadow-ledger writers.
    """
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(runtime_lock, "_PROCESS_STARTED_AT_UTC", _iso_minutes_ago(10))
    # Same pid, DIFFERENT owner: another container's live process.
    _write_lock(cfg, acquired_at_utc=_iso_minutes_ago(30), owner_token="another-container")

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)

    assert lock.acquired is False, (
        "a pid shared across namespaces is not evidence that the holder is dead"
    )
    assert lock.stale_lock_replaced is False


def test_runtime_lock_does_not_reclaim_a_payload_without_an_owner_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A pre-token payload falls through to the ordinary stale timeout."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(runtime_lock, "_PROCESS_STARTED_AT_UTC", _iso_minutes_ago(10))
    _write_lock(cfg, acquired_at_utc=_iso_minutes_ago(30), owner_token="")

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)

    assert lock.acquired is False, (
        "an unmatched payload must wait out the stale timeout: slower reclaim, "
        "never a wrongful one"
    )


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


def test_acquired_lock_is_published_fully_populated(tmp_path: Path) -> None:
    # #347 Codex P2: the lock is published atomically (temp write + os.link), so the lock
    # file — once it exists — ALWAYS already contains its payload. There is no observable
    # empty-lock window that a crash or a paused creator could leave behind (the window the
    # old O_CREAT|O_EXCL-then-write path had, and that a mtime heuristic could misjudge).
    cfg = _cfg(tmp_path)

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)

    assert lock.acquired is True
    payload = json.loads(
        runtime_lock.runtime_lock_path(cfg, "prediction_cycle").read_text(encoding="utf-8")
    )
    assert payload["pid"] == os.getpid()
    assert payload["name"] == "prediction_cycle"
    assert payload["acquired_at_utc"]
    runtime_lock.release_runtime_lock(lock)


def test_preexisting_empty_lock_is_held_fail_closed_not_reclaimed(tmp_path: Path) -> None:
    # An empty/malformed lock is ambiguous — a dead creator vs. one merely paused mid-write —
    # so it must NOT be reclaimed by an unsound age/mtime heuristic (that could start a second
    # holder on a live ledger lock). Fail closed. Atomic publishing means our own code never
    # creates an empty lock; a stray one simply blocks until an operator clears it.
    cfg = _cfg(tmp_path)
    path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=1)

    assert lock.acquired is False
    assert lock.stale_lock_replaced is False


def test_corrupt_lock_is_reclaimed_after_mtime_stale_ceiling(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json", encoding="utf-8")
    old = datetime.now(timezone.utc).timestamp() - 120
    os.utime(path, (old, old))

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=60)

    assert lock.acquired is True
    assert lock.stale_lock_reason == "corrupt_payload_mtime_age_seconds>60"
    runtime_lock.release_runtime_lock(lock)


def test_nonempty_malformed_lock_is_reclaimed_after_mtime_stale_ceiling(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"pid": "not-a-pid"}', encoding="utf-8")
    old = datetime.now(timezone.utc).timestamp() - 120
    os.utime(path, (old, old))

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=60)

    assert lock.acquired is True
    assert lock.stale_lock_reason == "corrupt_payload_mtime_age_seconds>60"
    runtime_lock.release_runtime_lock(lock)


def test_short_writes_still_publish_the_complete_payload(tmp_path: Path, monkeypatch) -> None:
    # #347 Codex P2: os.write may return a short count; the loop must persist the whole
    # payload before linking, or a truncated (malformed) lock could be published.
    cfg = _cfg(tmp_path)
    real_write = os.write
    monkeypatch.setattr(os, "write", lambda fd, data: real_write(fd, data[:1]))  # 1 byte/call

    lock = runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)
    monkeypatch.undo()

    assert lock.acquired is True
    payload = json.loads(
        runtime_lock.runtime_lock_path(cfg, "prediction_cycle").read_text(encoding="utf-8")
    )
    assert payload["name"] == "prediction_cycle"
    assert payload["pid"] == os.getpid()
    runtime_lock.release_runtime_lock(lock)


def test_write_failure_leaves_no_lock_and_no_orphan_temp(tmp_path: Path, monkeypatch) -> None:
    # #347 Codex P2: a write failure after the temp file is created must not leave an
    # orphan temp (which repeated attempts would accumulate) nor a partial lock.
    cfg = _cfg(tmp_path)

    def _boom(fd, data):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(os, "write", _boom)
    with pytest.raises(OSError):
        runtime_lock.acquire_runtime_lock(cfg, "prediction_cycle", stale_after_seconds=999999)
    monkeypatch.undo()

    lock_path = runtime_lock.runtime_lock_path(cfg, "prediction_cycle")
    assert not lock_path.exists()
    leftovers = list(lock_path.parent.glob(".*.tmp")) if lock_path.parent.exists() else []
    assert leftovers == []


def _held_heartbeat(cfg: EngineConfig):
    lock = runtime_lock.acquire_runtime_lock(cfg, "shadow_cohort", stale_after_seconds=999999)
    assert lock.acquired is True
    return lock, runtime_lock.RuntimeLockHeartbeat(
        lock, cap_seconds=999999.0, critical_section_max_seconds=999999.0
    )


def test_a_holder_that_lost_the_lock_refuses_to_publish(tmp_path: Path) -> None:
    """Fail closed at the one point where it is decidable (Codex P1 wave-42).

    Stale reclaim renames the lock aside, verifies, and restores it -- and in
    the gap a third caller can acquire. No filesystem CAS closes that window:
    `os.replace` rebinds a name atomically but unconditionally. What IS
    decidable is whether WE still hold the lock at the moment it matters, so
    the critical section refuses to run when a foreign owner token is sitting
    at the path.
    """
    cfg = _cfg(tmp_path)
    lock, heartbeat = _held_heartbeat(cfg)

    # Someone else reclaimed and now holds it.
    stolen = dict(lock.payload)
    stolen["owner_token"] = "a-different-process-entirely"
    runtime_lock._rewrite_lock_payload(lock.path, stolen)

    with pytest.raises(runtime_lock.RuntimeLockOwnershipLost):
        with heartbeat.critical_section():
            raise AssertionError("the ledger publishes must not run under a lock we lost")
    assert heartbeat.ownership_lost is True
    assert heartbeat.as_dict()["heartbeat_ownership_lost"] is True


def test_a_holder_that_lost_the_lock_does_not_stamp_over_the_new_owner(tmp_path: Path) -> None:
    """Republishing would strand the lane, not merely be wrong (Codex P1 wave-42).

    The beat rewrites the whole payload, OWNER TOKEN INCLUDED. Stamped over a
    new holder, its `release_runtime_lock` -- which validates the token before
    unlinking -- would refuse to release, leaving the lock held until the stale
    window elapsed all over again.
    """
    cfg = _cfg(tmp_path)
    lock, heartbeat = _held_heartbeat(cfg)

    stolen = dict(lock.payload)
    stolen["owner_token"] = "a-different-process-entirely"
    runtime_lock._rewrite_lock_payload(lock.path, stolen)
    before = json.loads(lock.path.read_text(encoding="utf-8"))

    heartbeat.note_progress("settlement_position")

    after = json.loads(lock.path.read_text(encoding="utf-8"))
    assert after == before, "the losing holder overwrote the new owner's payload"
    assert after["owner_token"] == "a-different-process-entirely"
    assert heartbeat.beats == 0


def test_an_absent_lock_file_is_treated_as_lost_not_as_still_held(tmp_path: Path) -> None:
    """This process never unlinks its own lock before release, so absence answers the question."""
    cfg = _cfg(tmp_path)
    lock, heartbeat = _held_heartbeat(cfg)
    lock.path.unlink()

    with pytest.raises(runtime_lock.RuntimeLockOwnershipLost):
        with heartbeat.critical_section():
            raise AssertionError("unreachable")
    assert heartbeat.ownership_lost is True


def test_an_unreadable_lock_payload_is_ambiguous_and_does_not_abort_the_pass(tmp_path: Path) -> None:
    """The fail-safe split: proof of loss stops the pass, ambiguity is counted.

    `read_json` returns its default for a MISSING file and for an unreadable
    one alike. Collapsing the two would put a transient read error on the
    fail-closed side and abort passes for no reason.
    """
    cfg = _cfg(tmp_path)
    lock, heartbeat = _held_heartbeat(cfg)
    lock.path.write_text("{not json at all", encoding="utf-8")

    entered = False
    with heartbeat.critical_section():
        entered = True
    assert entered is True
    assert heartbeat.ownership_lost is False
    assert heartbeat.as_dict()["heartbeat_ownership_checks_inconclusive"] >= 1


def test_the_ordinary_held_case_still_beats_and_publishes(tmp_path: Path) -> None:
    """The over-correction guard: an intact, still-ours lock is untouched by any of this."""
    cfg = _cfg(tmp_path)
    lock, heartbeat = _held_heartbeat(cfg)

    assert heartbeat.note_progress("settlement_position") is True
    assert heartbeat.beats == 1
    with heartbeat.critical_section():
        pass
    assert heartbeat.ownership_lost is False
    assert heartbeat.as_dict()["heartbeat_ownership_checks_inconclusive"] == 0
    runtime_lock.release_runtime_lock(lock)
