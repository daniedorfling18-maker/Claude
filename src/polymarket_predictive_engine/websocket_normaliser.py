"""Normalise raw Polymarket CLOB market-channel websocket messages into a
feature-only training table plus a data-quality report.

Input is the raw capture written by ``websocket_collector.collect_websocket``:
``outputs/polymarket_websocket/websocket_messages.json`` is a list of
``{"collected_at_utc": ..., "message": "<raw json string>"}`` entries, where the
message is exactly what the socket delivered (a single event object, a JSON array
of events, or a control frame such as ``PONG``).

For each parsed event this module derives point-in-time order-book microstructure
features (best bid/ask, midpoint, spread, top-of-book sizes, depth within 1%/5% of
the touch, and top-of-book imbalance) and incremental ``price_change`` fields.

CRITICAL: the feature table is feature-only. It must never contain a market's
final outcome, resolution, winner, settlement, payout or any other label-derived
column - that would leak the prediction target into the training inputs. The
schema is asserted against ``FORBIDDEN_FEATURE_FIELDS`` before anything is written.

Three artefacts are produced:
  - outputs/polymarket_training/websocket_market_features.csv
  - outputs/polymarket_model_governance/websocket_feature_quality_report.csv
  - outputs/polymarket_model_governance/websocket_feature_summary.json
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json

# The feature columns required by the training schema. Order is the CSV order.
FEATURE_FIELDS = [
    "collected_at_utc",
    "source_timestamp",
    "market",
    "asset_id",
    "market_slug",
    "question",
    "category",
    "selection",
    "close_time",
    "event_type",
    "best_bid",
    "best_ask",
    "midpoint",
    "spread",
    "last_trade_price",
    "top_bid_size",
    "top_ask_size",
    "bid_depth_1pct",
    "ask_depth_1pct",
    "bid_depth_5pct",
    "ask_depth_5pct",
    "book_imbalance",
    "price_change_side",
    "price_change_price",
    "price_change_size",
]

QUALITY_FIELDS = [
    "collected_at_utc",
    "source_timestamp",
    "market",
    "asset_id",
    "event_type",
    "severity",
    "issue_type",
    "message",
]

# Any feature column or row key containing one of these substrings would leak the
# resolution/label of the market into the feature table and is forbidden.
FORBIDDEN_FEATURE_FIELDS = {
    "target",
    "label",
    "winner",
    "winning",
    "winning_outcome",
    "winning_token_id",
    "resolved",
    "resolution",
    "settled",
    "settlement",
    "payout",
    "final_result",
    "outcome",
}

# Depth bands, expressed as a fraction of the best price on each side.
DEPTH_BANDS = {"1pct": 0.01, "5pct": 0.05}
_ROUND = 6


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, _ROUND)


def _str_or_blank(value: Any) -> str:
    return "" if value is None else str(value)


def _levels(raw_levels: Any) -> list[tuple[float, float]]:
    """Parse a list of order-book levels into (price, size) pairs, dropping any
    entry whose price or size is not a finite number."""
    levels: list[tuple[float, float]] = []
    if not isinstance(raw_levels, (list, tuple)):
        return levels
    for level in raw_levels:
        if isinstance(level, dict):
            price = safe_float(level.get("price"))
            size = safe_float(level.get("size"))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            price = safe_float(level[0])
            size = safe_float(level[1])
        else:
            price = size = None
        if price is None or size is None:
            continue
        levels.append((price, size))
    return levels


def _depth(levels: list[tuple[float, float]], reference: float | None, pct: float, side: str) -> float | None:
    """Cumulative resting size within ``pct`` of the ``reference`` price on one side."""
    if reference is None:
        return None
    total = 0.0
    if side == "bid":
        floor = reference * (1.0 - pct)
        for price, size in levels:
            if price >= floor:
                total += size
    else:
        ceiling = reference * (1.0 + pct)
        for price, size in levels:
            if price <= ceiling:
                total += size
    return _round(total)


def _top_size(levels: list[tuple[float, float]], best_price: float | None) -> float | None:
    if best_price is None:
        return None
    return _round(sum(size for price, size in levels if price == best_price))


def _imbalance(top_bid_size: float | None, top_ask_size: float | None) -> float | None:
    if top_bid_size is None or top_ask_size is None:
        return None
    denom = top_bid_size + top_ask_size
    if denom <= 0:
        return None
    return _round((top_bid_size - top_ask_size) / denom)


def _blank_feature_row(collected_at_utc: str, event: dict[str, Any], event_type: str) -> dict[str, Any]:
    row = {field: None for field in FEATURE_FIELDS}
    row["collected_at_utc"] = collected_at_utc
    row["source_timestamp"] = _str_or_blank(event.get("timestamp"))
    row["market"] = _str_or_blank(event.get("market") or event.get("market_id"))
    row["asset_id"] = _str_or_blank(event.get("asset_id") or event.get("token_id"))
    row["event_type"] = event_type
    return row


def _quality_row(collected_at_utc: str, event: Any, event_type: str, severity: str, issue_type: str, message: str) -> dict[str, Any]:
    event = event if isinstance(event, dict) else {}
    return {
        "collected_at_utc": collected_at_utc,
        "source_timestamp": _str_or_blank(event.get("timestamp")),
        "market": _str_or_blank(event.get("market") or event.get("market_id")),
        "asset_id": _str_or_blank(event.get("asset_id") or event.get("token_id")),
        "event_type": event_type,
        "severity": severity,
        "issue_type": issue_type,
        "message": message,
    }


def _metadata_paths(cfg: EngineConfig) -> list[Path]:
    return [
        cfg.governance_root / "websocket_liquidity_targets.csv",
        cfg.output_root / "polymarket_liquidity_discovery" / "liquidity_watchlist.csv",
        cfg.output_root / "polymarket" / "market_snapshot.csv",
        cfg.output_root / "polymarket_fast_updown" / "fast_updown_market_snapshot.csv",
    ]


def _metadata_index(cfg: EngineConfig) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for path in _metadata_paths(cfg):
        for row in read_csv_rows(path):
            meta = {
                "market_slug": _str_or_blank(row.get("market_slug") or row.get("slug")),
                "question": _str_or_blank(row.get("question") or row.get("title") or row.get("market_question")),
                "category": _str_or_blank(row.get("family") or row.get("category")),
                "selection": _str_or_blank(row.get("outcome") or row.get("selection") or row.get("runner")),
                "close_time": _str_or_blank(row.get("close_time") or row.get("end_time") or row.get("end_date")),
                "market": _str_or_blank(row.get("market") or row.get("market_id") or row.get("condition_id")),
            }
            keys = [
                row.get("token_id"),
                row.get("asset_id"),
                row.get("outcome_token_id"),
                row.get("market"),
                row.get("market_id"),
                row.get("condition_id"),
                row.get("market_slug"),
                row.get("slug"),
            ]
            for key in keys:
                key_text = _str_or_blank(key).strip()
                if key_text and key_text not in index:
                    index[key_text] = meta
    return index


def _metadata_for_row(index: dict[str, dict[str, str]], row: dict[str, Any]) -> dict[str, str]:
    for key in (row.get("asset_id"), row.get("market"), row.get("market_slug")):
        key_text = _str_or_blank(key).strip()
        if key_text and key_text in index:
            return index[key_text]
    return {}


def _apply_metadata(cfg: EngineConfig, rows: list[dict[str, Any]]) -> tuple[int, int]:
    index = _metadata_index(cfg)
    enriched = 0
    for row in rows:
        meta = _metadata_for_row(index, row)
        if not meta:
            continue
        changed = False
        for field in ("market_slug", "question", "category", "selection", "close_time"):
            value = meta.get(field, "")
            current = _str_or_blank(row.get(field)).strip()
            if value and (not current or current.lower() == "unknown"):
                row[field] = value
                changed = True
        if meta.get("market") and not _str_or_blank(row.get("market")).strip():
            row["market"] = meta["market"]
            changed = True
        if changed:
            enriched += 1
    return enriched, len(index)


def _normalise_book(collected_at_utc: str, event: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bid_levels = _levels(event.get("bids") or event.get("buys"))
    ask_levels = _levels(event.get("asks") or event.get("sells"))
    quality: list[dict[str, Any]] = []

    if not bid_levels and not ask_levels:
        quality.append(_quality_row(collected_at_utc, event, "book", "medium", "empty_order_book", "book event carried no parseable bids or asks"))
        return [], quality

    best_bid = max(price for price, _ in bid_levels) if bid_levels else None
    best_ask = min(price for price, _ in ask_levels) if ask_levels else None

    if best_bid is None or best_ask is None:
        missing = "ask" if best_bid is not None else "bid"
        quality.append(_quality_row(collected_at_utc, event, "book", "medium", "missing_order_book_side", f"book event missing the {missing} side; midpoint/spread left null"))

    midpoint = _round((best_bid + best_ask) / 2.0) if (best_bid is not None and best_ask is not None) else None
    spread = _round(best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None
    top_bid_size = _top_size(bid_levels, best_bid)
    top_ask_size = _top_size(ask_levels, best_ask)

    row = _blank_feature_row(collected_at_utc, event, "book")
    row["best_bid"] = best_bid
    row["best_ask"] = best_ask
    row["midpoint"] = midpoint
    row["spread"] = spread
    row["top_bid_size"] = top_bid_size
    row["top_ask_size"] = top_ask_size
    for band, pct in DEPTH_BANDS.items():
        row[f"bid_depth_{band}"] = _depth(bid_levels, best_bid, pct, "bid")
        row[f"ask_depth_{band}"] = _depth(ask_levels, best_ask, pct, "ask")
    row["book_imbalance"] = _imbalance(top_bid_size, top_ask_size)
    return [row], quality


def _normalise_price_change(collected_at_utc: str, event: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    changes = event.get("price_changes")
    if not isinstance(changes, list):
        changes = event.get("changes")
    if not isinstance(changes, list):
        changes = [event]

    rows: list[dict[str, Any]] = []
    for change in changes:
        change = change if isinstance(change, dict) else {}
        best_bid = safe_float(change.get("best_bid"))
        if best_bid is None:
            best_bid = safe_float(event.get("best_bid"))
        best_ask = safe_float(change.get("best_ask"))
        if best_ask is None:
            best_ask = safe_float(event.get("best_ask"))
        midpoint = _round((best_bid + best_ask) / 2.0) if (best_bid is not None and best_ask is not None) else None
        spread = _round(best_ask - best_bid) if (best_bid is not None and best_ask is not None) else None

        row = _blank_feature_row(collected_at_utc, event, "price_change")
        # Polymarket's current market-channel price_change messages often keep
        # token-level identifiers inside each price_changes[] element, not on
        # the parent event. Preserve those asset ids so downstream feature rows
        # can later join to labels by token_id.
        row["market"] = _str_or_blank(change.get("market") or event.get("market") or event.get("market_id"))
        row["asset_id"] = _str_or_blank(change.get("asset_id") or change.get("token_id") or event.get("asset_id") or event.get("token_id"))
        row["best_bid"] = best_bid
        row["best_ask"] = best_ask
        row["midpoint"] = midpoint
        row["spread"] = spread
        row["price_change_side"] = change.get("side")
        row["price_change_price"] = safe_float(change.get("price"))
        row["price_change_size"] = safe_float(change.get("size"))
        rows.append(row)
    return rows, []


def _normalise_last_trade(collected_at_utc: str, event: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    price = safe_float(event.get("price"))
    if price is None:
        return [], [_quality_row(collected_at_utc, event, "last_trade_price", "medium", "missing_last_trade_price", "last_trade_price event carried no parseable price")]
    row = _blank_feature_row(collected_at_utc, event, "last_trade_price")
    row["last_trade_price"] = price
    return [row], []


def _normalise_event(collected_at_utc: str, event: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(event, dict):
        return [], [_quality_row(collected_at_utc, {}, "", "high", "non_object_event", f"event was not a JSON object: {type(event).__name__}")]
    event_type = str(event.get("event_type") or "").strip()
    if event_type == "book":
        return _normalise_book(collected_at_utc, event)
    if event_type == "price_change":
        return _normalise_price_change(collected_at_utc, event)
    if event_type == "last_trade_price":
        return _normalise_last_trade(collected_at_utc, event)
    return [], [_quality_row(collected_at_utc, event, event_type or "unknown", "informational", "non_feature_event", f"event type {event_type or 'unknown'!r} carries no order-book features")]


def normalize_websocket_message(raw_message: Any, collected_at_utc: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalise one captured websocket message into (feature_rows, quality_rows).

    Never raises: malformed payloads and control frames are routed to the quality
    report instead of crashing the run.
    """
    if isinstance(raw_message, str):
        stripped = raw_message.strip()
        if stripped == "" or stripped.upper() in {"PING", "PONG"}:
            return [], [_quality_row(collected_at_utc, {}, "", "informational", "control_frame", f"non-data control frame: {stripped!r}")]
        try:
            payload = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return [], [_quality_row(collected_at_utc, {}, "", "high", "malformed_json", "message could not be parsed as JSON")]
    else:
        payload = raw_message

    events = payload if isinstance(payload, list) else [payload]
    features: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    for event in events:
        rows, issues = _normalise_event(collected_at_utc, event)
        features.extend(rows)
        quality.extend(issues)
    return features, quality


def _assert_no_leakage(rows: list[dict[str, Any]]) -> None:
    bad_columns = [field for field in FEATURE_FIELDS if any(token in field.lower() for token in FORBIDDEN_FEATURE_FIELDS)]
    if bad_columns:
        raise ValueError("websocket feature schema contains forbidden label columns: " + ", ".join(sorted(bad_columns)))
    for row in rows:
        for key in row.keys():
            if any(token in str(key).lower() for token in FORBIDDEN_FEATURE_FIELDS):
                raise ValueError(f"websocket feature row contains forbidden label column: {key}")


def _normaliser_settings(cfg: EngineConfig) -> dict[str, Any]:
    value = cfg.raw.get("websocket_market_data", {})
    return value if isinstance(value, dict) else {}


def _feature_row_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(key) or "")
        for key in (
            "collected_at_utc",
            "source_timestamp",
            "market",
            "asset_id",
            "event_type",
            "best_bid",
            "best_ask",
            "last_trade_price",
            "price_change_side",
            "price_change_price",
            "price_change_size",
        )
    )


def _feature_time(row: dict[str, Any]):
    return parse_timestamp(row.get("collected_at_utc") or row.get("source_timestamp"))


def _fmt_utc(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else ""


def _run_as_of(value: datetime | str | None = None) -> datetime:
    parsed = parse_timestamp(value) if value is not None else parse_timestamp(now_utc())
    if parsed is None:
        raise ValueError("as_of must be a valid UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _retention_write_due(
    *,
    settings: dict[str, Any],
    previous_summary: dict[str, Any],
    features_path: Path,
    return_latest_features_only: bool,
    as_of: datetime | str | None = None,
) -> tuple[bool, dict[str, Any]]:
    interval = safe_float(settings.get("feature_retention_write_interval_seconds"))
    if interval is None or interval <= 0:
        return True, {"feature_retention_write_interval_seconds": interval or 0}
    if not return_latest_features_only:
        return True, {"feature_retention_write_interval_seconds": interval}
    if not features_path.exists():
        return True, {"feature_retention_write_interval_seconds": interval}
    last_write = parse_timestamp(previous_summary.get("feature_retention_last_write_utc") or previous_summary.get("collected_at_utc"))
    if last_write is None:
        return True, {"feature_retention_write_interval_seconds": interval}
    age = max(0.0, (_run_as_of(as_of) - last_write.astimezone(timezone.utc)).total_seconds())
    return age >= float(interval), {
        "feature_retention_write_interval_seconds": interval,
        "feature_retention_last_write_utc": _fmt_utc(last_write),
        "feature_retention_last_write_age_seconds": age,
    }


def _feature_only(row: dict[str, Any]) -> dict[str, Any]:
    """Drop legacy/accidental columns before retention.

    Older local feature files may contain convenience metadata such as
    ``outcome``.  Those columns are useful for display, but they are forbidden
    in the model feature table.  Retention must preserve quote history while
    stripping anything outside the audited feature schema.
    """
    return {field: row.get(field, "") for field in FEATURE_FIELDS}


def _archive_expiring_features(
    cfg: EngineConfig,
    settings: dict[str, Any],
    expired_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist rows leaving the live retention window as compressed CSV.

    The live feature table must stay small for the loop, but expired rows are
    still training substrate. One gzip file per flush, oldest files deleted
    once the archive exceeds ``training_archive_max_mb`` (default 1024)."""
    import csv as _csv
    import gzip as _gzip

    enabled = str(settings.get("training_archive_enabled", True)).strip().lower() not in {"0", "false", "no"}
    if not enabled or not expired_rows:
        return {"enabled": enabled, "archived_rows": 0}

    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in expired_rows:
        key = _feature_row_key(row)
        if any(key):
            deduped[key] = row
    rows = list(deduped.values())
    if not rows:
        return {"enabled": True, "archived_rows": 0}

    archive_dir = cfg.output_root / "polymarket_training_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({field for row in rows for field in row})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive_path = archive_dir / f"features_{stamp}.csv.gz"
    with _gzip.open(archive_path, "wt", encoding="utf-8", newline="") as handle:
        writer = _csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    max_mb = safe_float(settings.get("training_archive_max_mb"))
    max_bytes = int((max_mb if max_mb and max_mb > 0 else 1024) * 1024 * 1024)
    archives = sorted(archive_dir.glob("features_*.csv.gz"))
    total = sum(path.stat().st_size for path in archives)
    deleted = 0
    for path in archives:
        if total <= max_bytes:
            break
        total -= path.stat().st_size
        path.unlink()
        deleted += 1
    return {
        "enabled": True,
        "archived_rows": len(rows),
        "archive_file": str(archive_path),
        "archive_total_bytes": total,
        "oldest_archives_deleted_for_cap": deleted,
    }


def _retained_feature_rows(
    cfg: EngineConfig,
    *,
    features_path: Path,
    new_features: list[dict[str, Any]],
    as_of: datetime | str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge current websocket features with prior feature rows.

    The raw websocket message file is intentionally capped so the live process
    cannot grow forever. The model, however, needs several days of bid/ask
    variation. This retention layer keeps the normalised feature history as the
    reusable training substrate, without adding settlement labels or changing
    trade gates. Retention is anchored to the run clock, never to the newest
    observed row; ``as_of`` exists only for deterministic clock-advance tests.
    """
    settings = _normaliser_settings(cfg)
    enabled = str(settings.get("retain_existing_features", True)).strip().lower() not in {"0", "false", "no"}
    existing = [_feature_only(row) for row in read_csv_rows(features_path)] if enabled else []
    current_features = [_feature_only(row) for row in new_features]
    combined: list[dict[str, Any]] = [*existing, *current_features]
    max_rows = int(safe_float(settings.get("max_feature_rows")) or 0)
    retention_hours = safe_float(settings.get("feature_retention_hours"))
    cutoff = None
    expired_rows: list[dict[str, Any]] = []
    if retention_hours is not None and retention_hours > 0 and combined:
        cutoff = _run_as_of(as_of) - timedelta(hours=float(retention_hours))
        kept: list[dict[str, Any]] = []
        for row in combined:
            row_time = _feature_time(row)
            if row_time is None or row_time >= cutoff:
                kept.append(row)
            else:
                expired_rows.append(row)
        combined = kept

    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in combined:
        key = _feature_row_key(row)
        if not any(key):
            continue
        deduped[key] = row
    retained = sorted(
        deduped.values(),
        key=lambda row: (
            _feature_time(row) or parse_timestamp("1970-01-01T00:00:00Z"),
            str(row.get("source_timestamp") or ""),
            str(row.get("asset_id") or ""),
        ),
    )
    dropped_for_max_rows = 0
    if max_rows > 0 and len(retained) > max_rows:
        dropped_for_max_rows = len(retained) - max_rows
        expired_rows.extend(retained[:-max_rows])
        retained = retained[-max_rows:]

    # 2026-07-09: rows leaving the live retention window used to be silently
    # DELETED - a day of order-book training substrate destroyed every day.
    # Archive them (deduplicated, compressed, disk-capped) before dropping.
    archive_diag = _archive_expiring_features(cfg, settings, expired_rows)

    diagnostics = {
        "training_archive": archive_diag,
        "feature_retention_enabled": enabled,
        "new_feature_rows": len(current_features),
        "existing_feature_rows": len(existing),
        "retained_feature_rows": len(retained),
        "deduped_feature_rows": len(combined) - len(deduped),
        "feature_retention_hours": retention_hours,
        "feature_retention_cutoff_utc": _fmt_utc(cutoff),
        "max_feature_rows": max_rows,
        "dropped_for_max_feature_rows": dropped_for_max_rows,
    }
    return retained, diagnostics


def normalize_websocket_file(
    cfg: EngineConfig,
    input_path: str | Path | None = None,
    *,
    as_of: datetime | str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Read captured websocket messages and write the feature table, quality report
    and summary. Returns (features, quality, summary)."""
    settings = _normaliser_settings(cfg)
    run_at = _run_as_of(as_of)
    generated_at = _fmt_utc(run_at)
    input_scope = "explicit"
    if input_path is None:
        latest_path = cfg.output_root / "polymarket_websocket" / "websocket_messages_latest.json"
        latest_only = str(settings.get("normalise_latest_messages_only", False)).strip().lower() in {"1", "true", "yes", "y", "on"}
        if latest_only and latest_path.exists():
            input_path = latest_path
            input_scope = "latest_messages"
        else:
            input_path = cfg.output_root / "polymarket_websocket" / "websocket_messages.json"
            input_scope = "all_retained_messages"
    input_path = Path(input_path)
    messages = read_json(input_path, default=[])
    if not isinstance(messages, list):
        messages = []

    features: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    for entry in messages:
        if isinstance(entry, dict):
            collected_at = _str_or_blank(entry.get("collected_at_utc"))
            raw_message = entry.get("message")
        else:
            collected_at = ""
            raw_message = entry
        rows, issues = normalize_websocket_message(raw_message, collected_at_utc=collected_at)
        features.extend(rows)
        quality.extend(issues)

    metadata_enriched_rows, metadata_index_keys = _apply_metadata(cfg, features)
    _assert_no_leakage(features)

    train_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    features_path = train_root / "websocket_market_features.csv"
    quality_path = gov_root / "websocket_feature_quality_report.csv"
    summary_path = gov_root / "websocket_feature_summary.json"
    previous_summary = read_json(summary_path, default={}) or {}
    if not isinstance(previous_summary, dict):
        previous_summary = {}
    current_features = [_feature_only(row) for row in features]
    return_latest_features_only = str(settings.get("return_latest_features_only", False)).strip().lower() in {"1", "true", "yes", "y", "on"}
    retention_due, retention_schedule = _retention_write_due(
        settings=settings,
        previous_summary=previous_summary,
        features_path=features_path,
        return_latest_features_only=return_latest_features_only,
        as_of=run_at,
    )
    if retention_due:
        retained_features, retention = _retained_feature_rows(
            cfg,
            features_path=features_path,
            new_features=features,
            as_of=run_at,
        )
        _assert_no_leakage(retained_features)
        write_csv(features_path, retained_features, fieldnames=FEATURE_FIELDS)
        feature_retention_last_write_utc = generated_at
        event_type_counts = Counter(row["event_type"] for row in retained_features)
        category_counts = Counter(str(row.get("category") or "unknown") for row in retained_features)
        retained_feature_count = len(retained_features)
    else:
        retained_features = []
        retention = {
            "feature_retention_enabled": True,
            "new_feature_rows": len(current_features),
            "existing_feature_rows": previous_summary.get("retained_feature_rows") or previous_summary.get("feature_rows") or "",
            "retained_feature_rows": previous_summary.get("retained_feature_rows") or previous_summary.get("feature_rows") or "",
            "deduped_feature_rows": "",
            "feature_retention_hours": settings.get("feature_retention_hours"),
            "feature_retention_cutoff_utc": previous_summary.get("feature_retention_cutoff_utc", ""),
            "max_feature_rows": settings.get("max_feature_rows"),
            "dropped_for_max_feature_rows": 0,
        }
        feature_retention_last_write_utc = str(
            previous_summary.get("feature_retention_last_write_utc")
            or previous_summary.get("collected_at_utc")
            or ""
        )
        event_type_counts = Counter(previous_summary.get("event_type_counts", {}) if isinstance(previous_summary.get("event_type_counts"), dict) else {})
        category_counts = Counter(previous_summary.get("category_counts", {}) if isinstance(previous_summary.get("category_counts"), dict) else {})
        retained_feature_count = int(safe_float(previous_summary.get("feature_rows") or previous_summary.get("retained_feature_rows")) or 0)
    write_csv(quality_path, quality, fieldnames=QUALITY_FIELDS)

    issue_type_counts = Counter(row["issue_type"] for row in quality)
    summary = {
        "status": "ok",
        "collected_at_utc": generated_at,
        "input_file": str(input_path),
        "input_scope": input_scope,
        "input_exists": input_path.exists(),
        "messages_processed": len(messages),
        "feature_rows": retained_feature_count,
        "returned_feature_rows": len(current_features)
        if return_latest_features_only
        else retained_feature_count,
        "return_latest_features_only": return_latest_features_only,
        **retention_schedule,
        **retention,
        "feature_retention_write_due": retention_due,
        "feature_retention_write_skipped": not retention_due,
        "feature_retention_last_write_utc": feature_retention_last_write_utc,
        "metadata_index_keys": metadata_index_keys,
        "metadata_enriched_rows": metadata_enriched_rows,
        "category_counts": dict(sorted(category_counts.items())),
        "quality_rows": len(quality),
        "event_type_counts": dict(sorted(event_type_counts.items())),
        "issue_type_counts": dict(sorted(issue_type_counts.items())),
        "malformed_message_count": issue_type_counts.get("malformed_json", 0),
        "feature_columns": FEATURE_FIELDS,
        "label_columns_present": [],
        "features_file": str(features_path),
        "quality_file": str(quality_path),
    }
    write_json(summary_path, summary)
    if return_latest_features_only:
        return current_features, quality, summary
    return retained_features, quality, summary


def main(config_path: str, input_path: str | None = None) -> dict[str, Any]:
    _, _, summary = normalize_websocket_file(load_config(config_path), input_path=input_path)
    return summary
