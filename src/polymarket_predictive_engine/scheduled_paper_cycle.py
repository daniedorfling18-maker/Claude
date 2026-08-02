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
import math
import os
import resource
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig
from .paper_cycle import run_paper_cycle
from .refresh_governance import LOCK_CONTENTION_EXIT_CODE
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float

# Bounded lock-wait budget: retry a `prediction_cycle`-lock-contended cycle
# on this cadence instead of failing on the first contended attempt (the
# live bridge's cycle is a normal, brief holder of the same lock). No second
# lock is introduced here and `prediction_cycle_lock_stale_seconds` is never
# reduced -- `run_paper_cycle` owns the one lock, this loop only re-attempts.
LOCK_WAIT_MAX_SECONDS = 300.0
LOCK_WAIT_SLEEP_SECONDS = 10.0

# WO-143.7(b), registered 2026-08-01: the maximum age of the freshest
# `websocket_market_features.csv` observation that still counts as a CURRENT
# scoring input. Basis: 6x the 300s `websocket_max_gap_seconds` reporting
# target, and well inside the job's 4h default cadence. Configurable
# tighten-only (a config value may only SHRINK it) via
# `scheduled_paper_cycle.max_websocket_observation_age_seconds`.
#
# Deliberately a NEW registered setting rather than a reuse of either
# near-miss candidate: `operating_state_slos.websocket_max_gap_seconds` lives
# in `REGISTERED_SLO_TARGETS`, annotated "never read by a gate, broker,
# sizing rule, or order path" (operating_state.py:44-52), so wiring it in
# here would make a reporting-only block gate-bearing; and
# `mispricing_alpha.max_websocket_quote_enrichment_age_seconds` bounds
# per-row quote/prediction PAIRING (mispricing_alpha.py:363), not feed
# freshness, and is unset in config.
MAX_WEBSOCKET_OBSERVATION_AGE_SECONDS = 1800.0

_RECEIPT_RELATIVE = Path("polymarket_model_governance") / "scheduled_paper_cycle.json"
_LIVE_SUMMARY_RELATIVE = Path("polymarket_model_governance") / "mispricing_alpha_live_summary.json"
_WEBSOCKET_FEATURES_RELATIVE = Path("polymarket_training") / "websocket_market_features.csv"
_OBSERVATION_TIMESTAMP_COLUMNS = (
    "collected_at_utc",
    "snapshot_timestamp",
    "timestamp",
    "collected_at",
)


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


def _finite(value: Any) -> float | None:
    """`safe_float` that also rejects non-finite (NaN/inf) values.

    WO-143.7(b): `safe_float("nan")` returns NaN (`utils.py:373-379`), and a
    NaN on either side of a `<=` threshold comparison makes it False. An
    unguarded age comparison would therefore read a CORRUPT timestamp as
    fresh -- the exact fail-open hole item (b) exists to close -- so
    non-finite is treated as missing, never as fresh.
    """

    parsed = safe_float(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _websocket_observation_max_age_seconds(cfg: EngineConfig) -> float:
    """The registered ceiling, tighten-only: config may only SHRINK it.

    Mirrors the `_mb_tighter_max` precedent (`maker_carry_study.py:1864`):
    a negative or non-finite override falls back to the registered default
    rather than silently widening the window.
    """

    settings = cfg.raw.get("scheduled_paper_cycle", {}) or {}
    value = _finite(settings.get("max_websocket_observation_age_seconds"))
    if value is None or value < 0:
        return MAX_WEBSOCKET_OBSERVATION_AGE_SECONDS
    return min(MAX_WEBSOCKET_OBSERVATION_AGE_SECONDS, value)


def _websocket_observation_age_seconds(cfg: EngineConfig) -> float | None:
    """Age in seconds of the freshest `websocket_market_features.csv` row.

    Returns `None` -- meaning UNMEASURABLE, which the caller treats as
    blocked, never as fresh -- for every bad-input class WO-143.7(b)
    enumerates: an absent file, an empty file, a malformed file (whose
    garbage-keyed `csv.DictReader` rows carry none of the timestamp
    columns), rows whose timestamps are unparseable, and rows whose
    timestamps are non-finite.
    """

    rows = read_csv_rows(cfg.output_root / _WEBSOCKET_FEATURES_RELATIVE)
    if not rows:
        return None
    freshest: datetime | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = next(
            (row.get(column) for column in _OBSERVATION_TIMESTAMP_COLUMNS if row.get(column)),
            None,
        )
        if raw is None:
            continue
        # If the cell parses as a number at all, it must be finite before it
        # can be treated as a timestamp: safe_float("nan") returns NaN, and a
        # NaN silently passes the `<=` ceiling comparison in the caller.
        numeric = safe_float(raw)
        if numeric is not None and not math.isfinite(numeric):
            continue
        candidate = parse_timestamp(raw)
        if candidate is None:
            continue
        stamp = candidate.timestamp()
        if not math.isfinite(stamp):
            continue
        if freshest is None or candidate > freshest:
            freshest = candidate
    if freshest is None:
        return None
    age = (datetime.now(timezone.utc) - freshest).total_seconds()
    if not math.isfinite(age):
        return None
    return max(0.0, age)


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
        lock_wait_seconds = 0.0
        report: dict[str, Any] = {}
        while True:
            # WO-143.7(e): bracket each attempt individually and add its span
            # to `lock_wait_seconds` ONLY on the branches below where the
            # attempt was turned away by a held lock. A first-attempt
            # acquisition breaks out before any accumulation, so it reports
            # ~0 instead of the whole feature/model/scoring runtime (which
            # previously made this field a duplicate of `duration_seconds`).
            attempt_started_monotonic = time.monotonic()
            lock_attempts += 1
            report = run_paper_cycle(cfg, source=source, scope="scoring_only")
            if report.get("status") != "skipped_existing_prediction_cycle":
                break
            if time.monotonic() >= deadline_monotonic:
                lock_wait_seconds += max(0.0, time.monotonic() - attempt_started_monotonic)
                break
            time.sleep(LOCK_WAIT_SLEEP_SECONDS)
            lock_wait_seconds += max(0.0, time.monotonic() - attempt_started_monotonic)

        # WO-143.7(b): the old `"predictions" in report` was a KEY-PRESENCE
        # test, which passes even when the value is 0 -- what a missing,
        # empty, or malformed `websocket_market_features.csv` produces, since
        # `read_csv_rows` returns `[]`/garbage rows rather than raising.
        # Require a POSITIVE count instead.
        prediction_count = report.get("predictions")
        has_predictions = (
            isinstance(prediction_count, int)
            and not isinstance(prediction_count, bool)
            and prediction_count > 0
        )
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
            # WO-143.7(b): also validate the websocket observation age against
            # its registered ceiling BEFORE classifying completion. A present,
            # parseable file whose freshest row has aged out is not current
            # evidence even though it "parses and scores" (produces predictions
            # and refreshes the overlay). `_websocket_observation_age_seconds`
            # returns None for every bad-input class -- absent, empty,
            # malformed, unparseable, non-finite -- and None is treated as
            # blocked, never fresh (fail-closed, WO-121/129).
            #
            # Scoped to `source == "websocket"`, the deployed job's only
            # source and the only source for which this file is an input at
            # all; a `raw_snapshot`-sourced cycle never reads it, and the
            # registered §143.2 test set exercises exactly that path.
            websocket_observation_fresh = True
            if source == "websocket":
                websocket_age = _websocket_observation_age_seconds(cfg)
                websocket_observation_fresh = (
                    websocket_age is not None
                    and websocket_age <= _websocket_observation_max_age_seconds(cfg)
                )
            cycle_completed = has_predictions and websocket_observation_fresh
            if has_predictions and websocket_observation_fresh and cycle_status == "ran" and overlay_refreshed:
                status = "ran"
                exit_code = 0
            elif has_predictions and websocket_observation_fresh and cycle_status == "blocked" and overlay_refreshed:
                status = "blocked_readiness"
                exit_code = 0
            elif has_predictions and websocket_observation_fresh and not overlay_refreshed:
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
