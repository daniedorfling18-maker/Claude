"""WO-143: a scheduled, scoring-only owner for the full paper cycle.

Nothing here marks a market measured, changes any M-A/M-B/M-C or maker
threshold, opens any order path, or changes what the live loop does per
cycle; the scheduled job is additive evidence accrual, paper-only, and a
failed or skipped scheduled cycle degrades only the taker alpha lane's
evidence freshness -- loudly: a missing, stale, or malformed input leaves
the cycle report without a ``predictions`` key and exits nonzero, a held
``prediction_cycle`` lock exits 75 after a bounded 300-second wait, a
disabled alpha overlay exits 1 rather than reporting success, and in every
failure case the scheduler's ``last_success_utc`` is not refreshed so
``scheduler_completion_freshness`` trips at its registered ceiling.
"""

from __future__ import annotations

import json
import os
import resource
import signal
import time
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .paper_cycle import run_paper_cycle
from .refresh_governance import LOCK_CONTENTION_EXIT_CODE
from .utils import now_utc, read_json

# Bounded lock-wait budget: retry a `prediction_cycle`-lock-contended cycle
# on this cadence instead of failing on the first contended attempt (the
# live bridge's cycle is a normal, brief holder of the same lock). No second
# lock is introduced here and `prediction_cycle_lock_stale_seconds` is never
# reduced -- `run_paper_cycle` owns the one lock, this loop only re-attempts.
LOCK_WAIT_MAX_SECONDS = 300.0
LOCK_WAIT_SLEEP_SECONDS = 10.0

_RECEIPT_RELATIVE = Path("polymarket_model_governance") / "scheduled_paper_cycle.json"
_LIVE_SUMMARY_RELATIVE = Path("polymarket_model_governance") / "mispricing_alpha_live_summary.json"


def _install_sigterm_handler() -> Any:
    """Install a SIGTERM handler that raises SystemExit, returning the prior one.

    The scheduler's `timeout` command (never `timeout -k`) sends SIGTERM on
    overrun. Python's default SIGTERM disposition terminates the process
    immediately, which would leak the `prediction_cycle` runtime lock for up
    to its stale window. Raising SystemExit instead lets the exception
    unwind normally through `run_paper_cycle`'s `with runtime_lock(...)`
    context manager, which releases the lock in its `finally` clause before
    the process actually exits.
    """

    previous = signal.getsignal(signal.SIGTERM)

    def _handler(signum: int, frame: Any) -> None:
        raise SystemExit(f"scheduled_paper_cycle: terminated by signal {signum}")

    signal.signal(signal.SIGTERM, _handler)
    return previous


def _restore_sigterm_handler(previous: Any) -> None:
    signal.signal(signal.SIGTERM, previous)


def _live_summary_generated_at(cfg: EngineConfig) -> str:
    payload = read_json(cfg.output_root / _LIVE_SUMMARY_RELATIVE, default={}) or {}
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("generated_at_utc") or "")


def _write_ordered_json_atomic(path: Path, payload: dict[str, Any]) -> Path:
    """Write JSON preserving `payload`'s key order (unlike the shared
    `write_json` helper, which always sorts keys) so the registered 22-key
    receipt order is what lands on disk, not merely in the returned dict.
    Atomic: sibling temp file, fsync, then `os.replace`.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return path


def run_scheduled_paper_cycle(cfg: EngineConfig, *, source: str = "websocket") -> dict[str, Any]:
    """Run the scoring-only paper cycle under a bounded lock-wait, classify
    the result into exactly one of five registered statuses, and write the
    22-key receipt atomically. Returns the receipt dict (also the atomically
    written JSON payload, in the same key order).
    """

    started_at_utc = now_utc()
    started_monotonic = time.monotonic()
    previous_sigterm_handler = _install_sigterm_handler()
    try:
        before = _live_summary_generated_at(cfg)
        deadline_monotonic = started_monotonic + LOCK_WAIT_MAX_SECONDS
        lock_attempts = 0
        report: dict[str, Any] = {}
        while True:
            lock_attempts += 1
            report = run_paper_cycle(cfg, source=source, scope="scoring_only")
            if report.get("status") != "skipped_existing_prediction_cycle":
                break
            if time.monotonic() >= deadline_monotonic:
                break
            time.sleep(LOCK_WAIT_SLEEP_SECONDS)
        lock_wait_seconds = max(0.0, time.monotonic() - started_monotonic)

        has_predictions = "predictions" in report
        cycle_status = report.get("status")
        if report.get("status") == "skipped_existing_prediction_cycle":
            status = "skipped_prediction_cycle_lock"
            exit_code = LOCK_CONTENTION_EXIT_CODE
            cycle_completed = False
            after = before
            overlay_refreshed = False
        else:
            after = _live_summary_generated_at(cfg)
            overlay_refreshed = bool(after and after != before)
            cycle_completed = has_predictions
            if has_predictions and cycle_status == "ran" and overlay_refreshed:
                status = "ran"
                exit_code = 0
            elif has_predictions and cycle_status == "blocked" and overlay_refreshed:
                status = "blocked_readiness"
                exit_code = 0
            elif has_predictions and not overlay_refreshed:
                status = "blocked_overlay_disabled"
                exit_code = 1
            else:
                status = "blocked_inputs"
                exit_code = 1

        longshot = report.get("longshot_bias") if isinstance(report.get("longshot_bias"), dict) else {}
        signals_approved = report.get("signals_approved")
        duration_seconds = round(max(0.0, time.monotonic() - started_monotonic), 3)
        peak_rss_bytes = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024

        receipt: dict[str, Any] = {
            "status": status,
            "generated_at_utc": now_utc(),
            "started_at_utc": started_at_utc,
            "duration_seconds": duration_seconds,
            "source": source,
            "scope": "scoring_only",
            "lock_attempts": lock_attempts,
            "lock_wait_seconds": round(lock_wait_seconds, 3),
            "cycle_completed": cycle_completed,
            "features": report.get("features"),
            "predictions": report.get("predictions"),
            "signals_approved": signals_approved,
            "signals_rejected": report.get("signals_rejected"),
            "paper_signals_published": signals_approved or 0,
            "mispricing_alpha_live_summary_generated_at_utc": after,
            "mispricing_alpha_overlay_refreshed": overlay_refreshed,
            "longshot_status": longshot.get("status"),
            "longshot_candidates": longshot.get("candidates"),
            "peak_rss_bytes": peak_rss_bytes,
            "exit_code": exit_code,
            "paper_trading_invoked": False,
            "live_trading_invoked": False,
        }
        _write_ordered_json_atomic(cfg.output_root / _RECEIPT_RELATIVE, receipt)
        return receipt
    finally:
        _restore_sigterm_handler(previous_sigterm_handler)
