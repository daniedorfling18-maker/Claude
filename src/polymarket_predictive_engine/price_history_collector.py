from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

from .config import EngineConfig, load_config
from .crypto_updown_model import is_crypto_updown_contract
from .utils import (
    csv_columns,
    normalize_external_timestamp,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    safe_float,
    write_csv,
    write_json,
)

DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"
SNAPSHOT_FIELDS = [
    "market_id",
    "market_slug",
    "condition_id",
    "gamma_market_id",
    "token_id",
    "outcome",
    "category",
    "timestamp",
    "midpoint",
    "price",
    "close_time",
    "source",
]
FORBIDDEN_PRICE_HISTORY_FIELDS = {
    "target",
    "winner",
    "winning",
    "winning_outcome",
    "winning_token_id",
    "resolved",
    "settled",
    "settlement",
    "payout",
    "final_result",
}
COLLECTION_PRIORITY_FOCUS_FINAL = "focus_final"
COLLECTION_PRIORITY_GENERAL = "general"
QUALITY_FIELDS = [
    "token_id",
    "market_slug",
    "close_time",
    "collection_priority",
    "status",
    "history_points",
    "fetch_source",
    "attempt_errors",
    "error",
]
FROZEN_UPDOWN_MARKERS = ("updown", "up_down", "up-down", "up or down")


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    try:
        text = str(value).replace("Z", "+00:00")
        if text.endswith("+00"):
            text = text + ":00"
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except Exception:
        return None


def _clean_resolution_rows(cfg: EngineConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for name in ("historical_resolutions.csv", "market_resolutions.csv"):
        path = cfg.output_root / "polymarket_training" / name
        for row in read_csv_rows(path):
            if row.get("resolution_quality") == "clean_settlement" and row.get("token_id"):
                rows.append(row)

    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        market_key = row.get("market_slug") or row.get("condition_id") or row.get("gamma_market_id") or ""
        key = (market_key, row.get("token_id", ""))
        if key not in seen:
            seen.add(key)
            unique.append(row)

    settings = cfg.raw.get("historical_price_history", {})
    min_close = _dt(settings.get("min_close_time", "2024-01-01T00:00:00Z"))

    def close_key(row: dict[str, str]) -> datetime:
        return _dt(row.get("close_time") or row.get("resolution_time")) or datetime(1970, 1, 1, tzinfo=timezone.utc)

    sorted_rows = sorted(unique, key=close_key, reverse=True)
    recent = [row for row in sorted_rows if min_close and close_key(row) >= min_close]
    return recent or sorted_rows


def _is_frozen_updown_row(row: dict[str, Any]) -> bool:
    if is_crypto_updown_contract(dict(row)):
        return True
    family_text = " ".join(
        str(row.get(key) or "")
        for key in ("signal_cohort", "cohort", "family", "market_slug", "question")
    ).lower()
    return any(marker in family_text for marker in FROZEN_UPDOWN_MARKERS)


def _close_key(row: dict[str, Any]) -> datetime:
    return _dt(row.get("close_time") or row.get("resolution_time")) or datetime(1970, 1, 1, tzinfo=timezone.utc)


def _priority_final_rows(
    cfg: EngineConfig,
    resolution_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    resolution_by_token: dict[str, dict[str, str]] = {}
    for row in resolution_rows:
        token_id = str(row.get("token_id") or "").strip()
        if token_id and token_id not in resolution_by_token:
            resolution_by_token[token_id] = row

    final_rows = sorted(
        read_csv_rows(cfg.governance_root / "closing_line_final_history.csv"),
        key=_close_key,
        reverse=True,
    )
    priority_rows: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    for final in final_rows:
        token_id = str(final.get("token_id") or "").strip()
        if not token_id or token_id in seen_tokens:
            continue
        seen_tokens.add(token_id)
        row: dict[str, Any] = dict(resolution_by_token.get(token_id, {}))
        row["token_id"] = token_id
        for target, source in (
            ("market_slug", "market_slug"),
            ("condition_id", "market_id"),
            ("category", "category"),
            ("close_time", "close_time"),
            ("question", "question"),
            ("signal_cohort", "signal_cohort"),
        ):
            value = str(final.get(source) or "").strip()
            if value:
                row[target] = value
        if not row.get("resolution_time") and row.get("close_time"):
            row["resolution_time"] = row["close_time"]
        row["collection_priority"] = COLLECTION_PRIORITY_FOCUS_FINAL
        priority_rows.append(row)
    return priority_rows


def _collection_rows(
    cfg: EngineConfig,
    max_tokens: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    resolution_rows = _clean_resolution_rows(cfg)
    priority_rows = _priority_final_rows(cfg, resolution_rows)
    priority_ids = {str(row.get("token_id") or "") for row in priority_rows}

    general_rows: list[dict[str, Any]] = []
    seen_tokens = set(priority_ids)
    excluded_updown_tokens: set[str] = set()
    for source_row in resolution_rows:
        token_id = str(source_row.get("token_id") or "").strip()
        if not token_id or token_id in seen_tokens:
            continue
        seen_tokens.add(token_id)
        if _is_frozen_updown_row(source_row):
            excluded_updown_tokens.add(token_id)
            continue
        row: dict[str, Any] = dict(source_row)
        row["collection_priority"] = COLLECTION_PRIORITY_GENERAL
        general_rows.append(row)

    general_slots = max(0, max_tokens - len(priority_rows))
    requested_general = general_rows[:general_slots]
    selected = [*priority_rows, *requested_general]
    selected_priority_ids = {
        str(row.get("token_id") or "")
        for row in selected
        if row.get("collection_priority") == COLLECTION_PRIORITY_FOCUS_FINAL
    }
    return selected, {
        "priority_tokens_available": len(priority_ids),
        "priority_tokens_requested": len(selected_priority_ids),
        "priority_tokens_missing": len(priority_ids - selected_priority_ids),
        "priority_updown_tokens_requested": sum(1 for row in priority_rows if _is_frozen_updown_row(row)),
        "general_tokens_available": len(general_rows),
        "general_tokens_requested": len(requested_general),
        "general_tokens_truncated": max(0, len(general_rows) - len(requested_general)),
        "general_updown_tokens_excluded": len(excluded_updown_tokens),
        "general_updown_tokens_requested": sum(1 for row in requested_general if _is_frozen_updown_row(row)),
    }


def _epoch_to_timestamp(value: Any) -> str:
    seconds = normalize_external_timestamp(value)
    if seconds is None:
        return ""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_is_recognized(payload: Any) -> bool:
    """Return whether an HTTP-success body carries a recognizable history schema.

    A recognized answer is a bare list, or a dict carrying one of the
    ``history``/``prices``/``data`` keys (regardless of whether that key's
    value is itself empty). Anything else -- e.g. an error-shaped body like
    ``{"error": "not found"}`` -- is a payload the venue did not actually
    answer with, even though the HTTP call itself did not raise. WO-141 keys
    the merge/preservation decision on this distinction, not just on raised
    exceptions, so a chain of "200 OK but nothing recognizable" responses is
    not mistaken for a clean, venue-authoritative empty answer.
    """

    if isinstance(payload, list):
        return True
    if isinstance(payload, dict):
        return any(key in payload for key in ("history", "prices", "data"))
    return False


def normalize_price_history_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        points = payload.get("history") or payload.get("prices") or payload.get("data") or []
    elif isinstance(payload, list):
        points = payload
    else:
        points = []

    normalised: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            continue
        ts_raw = point.get("t") or point.get("timestamp") or point.get("time") or point.get("created_at")
        price = safe_float(point.get("p") or point.get("price") or point.get("midpoint") or point.get("close"))
        ts_text = _epoch_to_timestamp(ts_raw)

        if not ts_text or price is None:
            continue
        normalised.append({"timestamp": ts_text, "price": price})

    return sorted(normalised, key=lambda r: r["timestamp"])


def _request_history(base_url: str, params: dict[str, Any], timeout: int) -> Any:
    response = requests.get(f"{base_url.rstrip('/')}/prices-history", params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _window_params(
    token_id: str,
    close_time: datetime | None,
    fidelity: int,
    lookback_days: int,
    lookahead_days: int,
) -> dict[str, Any] | None:
    if close_time is None:
        return None
    start = close_time - timedelta(days=max(1, lookback_days))
    end = close_time + timedelta(days=max(1, lookahead_days))
    return {
        "market": token_id,
        "startTs": int(start.timestamp()),
        "endTs": int(end.timestamp()),
        "fidelity": fidelity,
    }


def _fetch_history(
    base_url: str,
    token_id: str,
    close_time: datetime | None,
    interval: str,
    fidelity: int,
    timeout: int,
    lookback_days: int,
    lookahead_days: int,
) -> tuple[Any, str, int, str]:
    """Try each fetch shape in turn until a recognized, non-empty answer.

    Returns ``(payload, source, attempt_errors, error_message)``.

    ``attempt_errors`` (WO-141) counts every attempt in the chain -- not just
    the last one -- that either raised or returned an HTTP-success payload
    with no recognizable history schema (see ``_payload_is_recognized``); a
    recognized-but-empty answer is not an attempt error, it is the venue
    legitimately reporting no data for that window/shape.

    ``error_message`` mirrors today's raise-on-total-failure behaviour: it is
    the final attempt's exception message when the chain never produced any
    response at all, and "" otherwise -- including when earlier attempts in
    the same chain raised or were unrecognized. Callers use ``error_message``
    only to reproduce the existing ``fetch_error`` status classification;
    the WO-141 merge decision is keyed on ``attempt_errors`` and the returned
    point count instead, so it also catches chains that end without raising
    (mixed error/empty chains, or an all-unrecognized chain).
    """

    attempts: list[tuple[str, dict[str, Any]]] = []
    bounded = _window_params(token_id, close_time, fidelity, lookback_days, lookahead_days)
    if bounded:
        attempts.append(("bounded_close_window", bounded))

        short = _window_params(token_id, close_time, fidelity, min(7, lookback_days), min(2, lookahead_days))
        if short and short != bounded:
            attempts.append(("short_close_window", short))

    attempts.extend(
        [
            ("interval_1m", {"market": token_id, "interval": "1m", "fidelity": fidelity}),
            ("interval_1w", {"market": token_id, "interval": "1w", "fidelity": fidelity}),
            ("configured_interval", {"market": token_id, "interval": interval, "fidelity": fidelity}),
        ]
    )

    attempt_errors = 0
    error_message = ""
    for source, params in attempts:
        try:
            payload = _request_history(base_url, params, timeout)
        except Exception as exc:
            attempt_errors += 1
            error_message = str(exc)
            continue

        error_message = ""
        if not _payload_is_recognized(payload):
            attempt_errors += 1
            continue

        points = normalize_price_history_payload(payload)
        if points:
            return payload, source, attempt_errors, ""

    return {"history": []}, "all_attempts_empty", attempt_errors, error_message


def _is_valid_preserved_row(row: dict[str, Any]) -> bool:
    """Per-row validity predicate for WO-141 corpus preservation.

    Valid only with a non-empty ``token_id``, a parseable ``timestamp``, and
    finite numeric ``price`` AND ``midpoint`` that are equal -- the writer
    always emits them identical, and consumers read ``midpoint`` first, so a
    row validated on ``price`` alone could still feed a corrupt or missing
    ``midpoint`` into training. ``read_csv_rows`` tolerates arbitrary
    content, so this predicate is the whole defence against a corrupted
    prior file poisoning preserved rows.
    """

    token_id = str(row.get("token_id") or "").strip()
    if not token_id:
        return False
    if parse_timestamp(row.get("timestamp")) is None:
        return False
    price = safe_float(row.get("price"))
    midpoint = safe_float(row.get("midpoint"))
    if price is None or midpoint is None:
        return False
    if not (math.isfinite(price) and math.isfinite(midpoint)):
        return False
    return price == midpoint


def _load_prior_corpus(path: Path) -> tuple[dict[str, list[dict[str, Any]]], bool, int]:
    """Load yesterday's snapshot corpus for WO-141 preservation.

    Returns ``(rows_by_token, untrusted, dropped_invalid_count)``.

    The corpus is trusted for preservation only when its header is exactly
    ``SNAPSHOT_FIELDS``; any other header (missing/empty, reordered-with-
    missing, or foreign columns) renders the whole prior file untrusted --
    ``rows_by_token`` comes back empty and ``untrusted`` is True, so the run
    falls back to today's write-what-was-fetched behaviour. A path that has
    never been written (no prior run yet) is not "untrusted" -- there is
    simply no prior corpus to trust or distrust.

    Within a trusted corpus, each row is checked with
    ``_is_valid_preserved_row``; invalid rows are dropped alone (counted in
    ``dropped_invalid_count``) while the token's remaining valid rows survive.
    """

    if not path.exists():
        return {}, False, 0
    if path.stat().st_size == 0 or csv_columns(path) != SNAPSHOT_FIELDS:
        return {}, True, 0

    rows_by_token: dict[str, list[dict[str, Any]]] = {}
    dropped_invalid = 0
    for row in read_csv_rows(path):
        if _is_valid_preserved_row(row):
            rows_by_token.setdefault(str(row.get("token_id") or ""), []).append(row)
        else:
            dropped_invalid += 1
    return rows_by_token, False, dropped_invalid


def _union_preserve_and_fresh_rows(
    prior_rows: list[dict[str, Any]],
    fresh_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """WO-141 fallback-success merge: union prior rows with this run's fetch.

    Deduplicated on (token_id, timestamp), with the freshly fetched row
    winning ties -- a degraded upstream may answer a shorter window than the
    errored primary attempt, so replacement would shrink coverage; the next
    clean success replaces wholesale and re-trims to the venue's full answer.
    """

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in prior_rows:
        key = (str(row.get("token_id") or ""), str(row.get("timestamp") or ""))
        merged[key] = row
    for row in fresh_rows:
        key = (str(row.get("token_id") or ""), str(row.get("timestamp") or ""))
        merged[key] = row
    return sorted(merged.values(), key=lambda r: str(r.get("timestamp") or ""))


def _assert_snapshot_schema(rows: list[dict[str, Any]]) -> None:
    field_lowers = {field.lower() for field in SNAPSHOT_FIELDS}
    bad = [field for field in field_lowers if any(hint in field for hint in FORBIDDEN_PRICE_HISTORY_FIELDS)]
    if bad:
        raise ValueError("historical price snapshots contain forbidden fields: " + ", ".join(sorted(bad)))
    for row in rows:
        for key in list(row.keys()):
            lower = key.lower()
            if any(hint in lower for hint in FORBIDDEN_PRICE_HISTORY_FIELDS):
                raise ValueError(f"historical price snapshot row contains forbidden field: {key}")


def collect_price_history(
    cfg: EngineConfig,
    historical_limit: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    settings = cfg.raw.get("historical_price_history", {})
    base_url = str(settings.get("clob_base_url", DEFAULT_CLOB_BASE_URL))
    interval = str(settings.get("interval", "max"))
    fidelity = int(settings.get("fidelity_seconds", 300))
    max_tokens = int(historical_limit or settings.get("max_tokens", 500))
    timeout = int(settings.get("request_timeout_seconds", 20))
    pause = float(settings.get("request_pause_seconds", 0.05))
    lookback_days = int(settings.get("lookback_days_before_close", 30))
    lookahead_days = int(settings.get("lookahead_days_after_close", 3))
    progress_every = int(settings.get("progress_every_tokens", 10))

    clean_rows, selection = _collection_rows(cfg, max_tokens)
    snapshots: list[dict[str, Any]] = []
    quality: list[dict[str, Any]] = []
    errors = 0
    empty = 0
    preserved_token_count = 0
    preserved_row_count = 0

    out_root = cfg.output_root / "polymarket_training"
    gov_root = cfg.governance_root
    snapshot_path = out_root / "historical_price_snapshots.csv"
    # WO-141: read yesterday's corpus before any write this run so a failed or
    # unrecognized fetch can fall back to the token's prior rows instead of
    # erasing them.
    prior_by_token, prior_corpus_untrusted, preserved_rows_dropped_invalid = _load_prior_corpus(snapshot_path)

    for idx, resolution in enumerate(clean_rows, start=1):
        token_id = resolution.get("token_id", "")
        close_dt = _dt(resolution.get("close_time") or resolution.get("resolution_time"))
        prior_rows = prior_by_token.get(token_id, [])
        try:
            payload, source, attempt_errors, error_message = _fetch_history(
                base_url, token_id, close_dt, interval, fidelity, timeout, lookback_days, lookahead_days
            )
            points = normalize_price_history_payload(payload)
            fresh_rows = []
            for point in points:
                row = {
                    "market_id": resolution.get("condition_id") or resolution.get("market_slug") or resolution.get("gamma_market_id") or "",
                    "market_slug": resolution.get("market_slug", ""),
                    "condition_id": resolution.get("condition_id", ""),
                    "gamma_market_id": resolution.get("gamma_market_id", ""),
                    "token_id": token_id,
                    "outcome": resolution.get("outcome", ""),
                    "category": resolution.get("category", ""),
                    "timestamp": point["timestamp"],
                    "midpoint": point["price"],
                    "price": point["price"],
                    "close_time": resolution.get("close_time", ""),
                    "source": "polymarket_clob_prices_history:" + source,
                }
                fresh_rows.append({field: row.get(field, "") for field in SNAPSHOT_FIELDS})

            # WO-141 per-token merge rule, exhaustive over (attempt_errors, points):
            if attempt_errors == 0 and points:
                # Clean success: replace exactly, byte-identical to pre-WO-141 behaviour.
                final_rows = fresh_rows
            elif attempt_errors > 0 and points:
                # Fallback success: a shorter-window answer must not shrink coverage.
                final_rows = _union_preserve_and_fresh_rows(prior_rows, fresh_rows)
            elif attempt_errors == 0 and not points:
                # Clean empty: venue-authoritative, prior rows drop as today.
                final_rows = []
            else:
                # Failed or unrecognized with no points: preserve prior rows unchanged.
                final_rows = list(prior_rows)
                if prior_rows:
                    preserved_token_count += 1
                    preserved_row_count += len(prior_rows)
            snapshots.extend(final_rows)

            status = "fetch_error" if error_message else ("ok" if points else "empty_history")
            if status == "fetch_error":
                errors += 1
            elif status == "empty_history":
                empty += 1
            quality.append(
                {
                    "token_id": token_id,
                    "market_slug": resolution.get("market_slug", ""),
                    "close_time": resolution.get("close_time", ""),
                    "collection_priority": resolution.get("collection_priority", COLLECTION_PRIORITY_GENERAL),
                    "status": status,
                    "history_points": len(points),
                    "fetch_source": source,
                    "attempt_errors": attempt_errors,
                    "error": error_message,
                }
            )
        except Exception as exc:
            # Fail-safe: an unexpected bug must never crash the run or silently
            # fabricate a clean outcome -- treat it like any other failed fetch
            # and preserve whatever prior rows the token has.
            errors += 1
            final_rows = list(prior_rows)
            if prior_rows:
                preserved_token_count += 1
                preserved_row_count += len(prior_rows)
            snapshots.extend(final_rows)
            quality.append(
                {
                    "token_id": token_id,
                    "market_slug": resolution.get("market_slug", ""),
                    "close_time": resolution.get("close_time", ""),
                    "collection_priority": resolution.get("collection_priority", COLLECTION_PRIORITY_GENERAL),
                    "status": "fetch_error",
                    "history_points": 0,
                    "fetch_source": "",
                    "attempt_errors": 1,
                    "error": str(exc),
                }
            )

        if progress_every and (idx % progress_every == 0 or idx == len(clean_rows)):
            print(f"collect-price-history progress: {idx}/{len(clean_rows)} tokens; snapshots={len(snapshots)}; empty={empty}; errors={errors}", flush=True)

        if pause:
            time.sleep(pause)

    _assert_snapshot_schema(snapshots)
    write_csv(snapshot_path, snapshots, fieldnames=SNAPSHOT_FIELDS)
    write_csv(gov_root / "historical_price_history_quality.csv", quality, fieldnames=QUALITY_FIELDS)
    priority_with_history = sum(
        1
        for row in quality
        if row.get("collection_priority") == COLLECTION_PRIORITY_FOCUS_FINAL and row.get("status") == "ok"
    )
    summary = {
        "work_order": "WO-91",
        "requested_tokens": len(clean_rows),
        "snapshot_rows": len(snapshots),
        "quality_rows": len(quality),
        "empty_history_count": empty,
        "error_count": errors,
        **selection,
        "priority_tokens_with_history": priority_with_history,
        "priority_tokens_empty_or_error": selection["priority_tokens_requested"] - priority_with_history,
        "collection_priority_counts": {
            COLLECTION_PRIORITY_FOCUS_FINAL: selection["priority_tokens_requested"],
            COLLECTION_PRIORITY_GENERAL: selection["general_tokens_requested"],
        },
        # WO-141: distinguish "collected today" from "carried forward" so a
        # reader of the summary can see corpus health survive a degraded run.
        "preserved_token_count": preserved_token_count,
        "preserved_row_count": preserved_row_count,
        "preserved_rows_dropped_invalid": preserved_rows_dropped_invalid,
        "prior_corpus_untrusted": prior_corpus_untrusted,
        "collected_at_utc": now_utc(),
        "output_file": str(out_root / "historical_price_snapshots.csv"),
        "quality_file": str(gov_root / "historical_price_history_quality.csv"),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    write_json(gov_root / "historical_price_history_summary.json", summary)
    return snapshots, quality, summary


def main(config_path: str, historical_limit: int | None = None) -> dict[str, Any]:
    _, _, summary = collect_price_history(load_config(config_path), historical_limit=historical_limit)
    return summary
