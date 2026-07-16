"""WO-99 append-only, point-in-time market-resolution observations.

The legacy resolution CSVs are current-run snapshots and may be replaced by a
later harvest.  This module preserves every distinct observed resolution state
in one versioned append-only ledger.  It is label provenance only: no feature,
signal, gate, broker, or order path reads settlement fields from this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .config import EngineConfig
from .runtime_lock import runtime_lock
from .utils import append_csv_rows, now_utc, parse_timestamp, read_csv_rows


WORK_ORDER = "WO-99"
RESOLUTION_CORPUS_RELATIVE_PATH = Path("polymarket_training") / "resolution_corpus_v1.csv"
RESOLUTION_CORPUS_LOCK = "wo99_resolution_corpus"

RESOLUTION_CORPUS_FIELDS = [
    "resolution_observation_id",
    "resolution_observed_at_utc",
    "producer",
    "market_slug",
    "gamma_market_id",
    "condition_id",
    "question_id",
    "question",
    "category",
    "closed",
    "active",
    "archived",
    "end_time",
    "close_time",
    "resolution_time",
    "resolution_source",
    "resolved_by",
    "neg_risk",
    "accepting_orders",
    "order_min_size",
    "order_price_min_tick_size",
    "winning_outcome",
    "winning_token_id",
    "resolution_quality",
    "resolution_quality_reason",
    "outcome_index",
    "outcome",
    "token_id",
    "outcome_price",
    "target",
    "paper_trading_invoked",
    "live_trading_invoked",
]

_IDENTITY_FIELDS = [
    "market_slug",
    "gamma_market_id",
    "condition_id",
    "question_id",
    "closed",
    "close_time",
    "resolution_time",
    "winning_outcome",
    "winning_token_id",
    "resolution_quality",
    "outcome_index",
    "outcome",
    "token_id",
    "outcome_price",
    "target",
]


def canonical_utc(value: Any | None = None) -> str:
    """Normalize one run clock; malformed explicit clocks fail loudly."""

    raw = now_utc() if value is None else value
    if isinstance(raw, datetime):
        parsed = raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    else:
        parsed = parse_timestamp(raw)
    if parsed is None:
        raise ValueError(f"invalid UTC observation clock: {raw!r}")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalise_optional_time(value: Any) -> str:
    if value in (None, ""):
        return ""
    parsed = parse_timestamp(value)
    if parsed is None:
        return ""
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serial(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return bool(value)
    return value


def _observation_id(row: Mapping[str, Any]) -> str:
    payload = {field: _serial(row.get(field, "")) for field in _IDENTITY_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def prepare_resolution_observations(
    rows: Iterable[Mapping[str, Any]],
    *,
    producer: str,
    observed_at_utc: Any | None = None,
) -> list[dict[str, Any]]:
    """Return schema-fixed observations using one injected collection clock."""

    observed = canonical_utc(observed_at_utc)
    prepared: list[dict[str, Any]] = []
    for raw in rows:
        row = {field: _serial(raw.get(field, "")) for field in RESOLUTION_CORPUS_FIELDS}
        row["producer"] = str(producer or "unknown")
        row["resolution_observed_at_utc"] = observed
        row["end_time"] = _normalise_optional_time(raw.get("end_time"))
        row["close_time"] = _normalise_optional_time(raw.get("close_time"))
        row["resolution_time"] = _normalise_optional_time(raw.get("resolution_time"))
        row["paper_trading_invoked"] = False
        row["live_trading_invoked"] = False
        row["resolution_observation_id"] = _observation_id(row)
        prepared.append(row)
    return prepared


def append_resolution_observations(
    cfg: EngineConfig,
    rows: Iterable[Mapping[str, Any]],
    *,
    producer: str,
    observed_at_utc: Any | None = None,
) -> dict[str, Any]:
    """Append unseen states under the shared producer lock.

    A repeated state has the same content-derived observation id even if a
    later producer sees it again.  A changed target/quality/timestamp is a new
    immutable observation; the downstream assembler rejects conflicting clean
    targets rather than rewriting history.
    """

    prepared = prepare_resolution_observations(
        rows,
        producer=producer,
        observed_at_utc=observed_at_utc,
    )
    path = cfg.output_root / RESOLUTION_CORPUS_RELATIVE_PATH
    with runtime_lock(cfg, RESOLUTION_CORPUS_LOCK, stale_after_seconds=300) as lock:
        if not lock.acquired:
            return {
                "status": "skipped_lock_held",
                "rows_seen": len(prepared),
                "rows_appended": 0,
                "output_file": str(path),
                "runtime_lock": lock.as_dict(),
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            }
        existing_ids = {
            str(row.get("resolution_observation_id") or "")
            for row in read_csv_rows(path)
            if str(row.get("resolution_observation_id") or "")
        }
        new_rows: list[dict[str, Any]] = []
        batch_ids: set[str] = set()
        for row in prepared:
            observation_id = str(row["resolution_observation_id"])
            if observation_id in existing_ids or observation_id in batch_ids:
                continue
            batch_ids.add(observation_id)
            new_rows.append(row)
        append_csv_rows(path, new_rows, fieldnames=RESOLUTION_CORPUS_FIELDS)
    return {
        "status": "ok",
        "rows_seen": len(prepared),
        "rows_appended": len(new_rows),
        "duplicate_states": len(prepared) - len(new_rows),
        "output_file": str(path),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
