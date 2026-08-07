"""WO-40 maker fill realism replay.

The maker-carry study charges adverse selection from bar moves and trade-print
markouts. This replay asks a narrower execution question: given recorded full
book levels or explicitly quote-aligned depth, would a last-in-queue maker
quote actually have filled when public prints crossed the quote level?

Measurement only. The replay reports a realism ratio next to the study charge
but never modifies the study, gates, quote sheet, or any order path.
"""
from __future__ import annotations

import ast
import csv
import gzip
import json
import time
from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from math import ceil, isfinite
from pathlib import Path
from typing import Any, Iterable

import requests

from .config import EngineConfig, load_config
from .trade_print_collector import collect_maker_portfolio_trade_prints
from .utils import (
    append_csv_rows,
    normalize_external_timestamp,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)

DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"

HORIZONS_MINUTES = (5, 15, 60)
OFFICIAL_BOOK_FIELDS = [
    "condition_id",
    "asset_id",
    "source_timestamp",
    "observation_timestamp",
    "hash",
    "best_bid",
    "best_ask",
    "midpoint",
    "top_bid_size",
    "top_ask_size",
    "bids_json",
    "asks_json",
    "collected_at_utc",
]

COLLECTION_WINDOW_FIELDS = [
    "window_id",
    "condition_id",
    "asset_id",
    "portfolio_generated_at_utc",
    "collected_at_utc",
    "quote_bid_price",
    "quote_ask_price",
    "quote_size_shares",
    "book_poll_status",
    "book_snapshot_rows",
    "trade_poll_status",
    "trade_prints_returned",
    "covered",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("maker_fill_replay", {}) if isinstance(cfg.raw.get("maker_fill_replay"), dict) else {}
    merged = {
        "enabled": True,
        "max_markets": 10,
        # WO-104 fix: reserved budget for recently-active (persistent) markets
        # so a churned-out recurring market keeps maturing Tier-0 coverage even
        # when the current portfolio already fills max_markets.
        "max_persistent_markets": 10,
        # WO-116: reserved budget for top-ranked CANDIDATE markets that are not
        # yet in the portfolio, so fresh rewarded markets accumulate the book
        # history the WO-113 measurement-eligibility gate requires BEFORE they
        # are selected. Without this, a fast-churning rewarded universe leaves
        # the portfolio oscillating at 0-1 eligible markets. Collection-only:
        # no gate, threshold, or eligibility rule reads this setting.
        "max_candidate_markets": 20,
        # WO-131: delisted-token hygiene. A candidate whose token 404s is a
        # corpse: on 2026-07-27, 7 of 50 polled markets returned HTTP 404, so
        # ~14% of the seeding budget bought markets that no longer exist while
        # M-A had 23 days left. The skip is a COOLDOWN, never a blacklist - a
        # skipped token has no book file, so it never enters the mtime tranche
        # either, and a permanent skip could never be undone by any later
        # success. Config may make the system poll MORE (lower threshold is
        # rejected, shorter cooldown is honoured), never blind it.
        "delisted_skip_threshold": 3,
        "delisted_cooldown_hours": 24.0,
        "replay_days": 7,
        "book_source": "both",
        "clob_base_url": DEFAULT_CLOB_BASE_URL,
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.1,
        "max_official_book_rows": 200000,
        "max_collection_window_rows": 200000,
        # WO-149: a fixed 30-minute join tolerance. This was registered as
        # "twice the 15-minute collection cadence", but the portfolio's
        # measured archive-derived cadence has drifted to ~37.15 min/snapshot
        # (~1.7x the nominal 15-minute figure this comment used to cite) -
        # WO-149 raises portfolio-only observation frequency and instruments
        # the actual join lag so any future change to this value has a
        # measured basis; it does not change the value itself.
        "max_book_state_lag_seconds": 1800,
        "regime_days": 7,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    # Tighten-only: configuration may only make collection MORE willing to poll.
    threshold = safe_float(merged.get("delisted_skip_threshold"))
    merged["delisted_skip_threshold"] = max(3, int(threshold)) if threshold is not None else 3
    cooldown = safe_float(merged.get("delisted_cooldown_hours"))
    merged["delisted_cooldown_hours"] = (
        min(24.0, cooldown) if cooldown is not None and cooldown > 0 else 24.0
    )
    return merged


DELISTED_MARKER_FILE = "delisted_token_markers.json"


def _read_delisted_markers(out_root: Path) -> dict[str, dict[str, Any]]:
    """Per-token 404 history. Fail-safe: unreadable means NOTHING is skipped.

    WO-131. For a collector the conservative direction is to collect, so a
    missing or malformed marker file must never suppress a poll.
    """

    payload = read_json(out_root / DELISTED_MARKER_FILE, default={}) or {}
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    if not isinstance(tokens, dict):
        return {}
    return {str(key): value for key, value in tokens.items() if isinstance(value, dict)}


def _delisted_skip_tokens(
    markers: dict[str, dict[str, Any]], *, threshold: int, cooldown_hours: float, now: datetime
) -> set[str]:
    """Tokens inside their cooldown. Past the TTL a token is re-probed once."""

    skip: set[str] = set()
    for token_id, row in markers.items():
        count = safe_float(row.get("consecutive_404s"))
        if count is None or int(count) < threshold:
            continue
        last = parse_timestamp(row.get("last_404_utc"))
        if last is None:
            # An unparseable stamp cannot establish that the cooldown is still
            # running, so the token is re-probed rather than silently dropped.
            continue
        if (now - last).total_seconds() < cooldown_hours * 3600.0:
            skip.add(token_id)
    return skip


def _update_delisted_markers(
    markers: dict[str, dict[str, Any]],
    *,
    polls: list[dict[str, Any]],
    generated_at: str,
    cooldown_hours: float,
) -> dict[str, dict[str, Any]]:
    """Fold this cycle's outcomes in: a 404 extends the cooldown, a book clears it."""

    updated = {str(key): dict(value) for key, value in markers.items()}
    stamp = parse_timestamp(generated_at)
    for poll in polls:
        token_id = str(poll.get("asset_id") or "").strip()
        if not token_id:
            continue
        error = str(poll.get("error") or "")
        if str(poll.get("status") or "") == "ok":
            # Any valid book clears the marker outright - a re-listing or a
            # transient outage recovers with no operator action.
            updated.pop(token_id, None)
            continue
        if "404" not in error:
            # Only a 404 is evidence the token is gone. A timeout or a 5xx is an
            # outage on our side of the wire and must not accrue toward a skip.
            continue
        row = updated.setdefault(token_id, {})
        row["condition_id"] = str(poll.get("condition_id") or row.get("condition_id") or "")
        row["first_404_utc"] = row.get("first_404_utc") or generated_at
        row["last_404_utc"] = generated_at
        previous = safe_float(row.get("consecutive_404s")) or 0.0
        row["consecutive_404s"] = int(previous) + 1
        row["next_probe_due_utc"] = (
            (stamp + timedelta(hours=cooldown_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
            if stamp is not None
            else ""
        )
    return updated


def _stamp(value: Any) -> float | None:
    return normalize_external_timestamp(value)


def _minute(stamp: float) -> float:
    return float(int(stamp // 60) * 60)


def _iter_csv_any(path: Path) -> Iterable[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    if path.suffix == ".gz":
        handle_context = gzip.open(path, "rt", encoding="utf-8-sig", errors="replace", newline="")
    else:
        handle_context = path.open("rt", encoding="utf-8-sig", errors="replace", newline="")
    with handle_context as handle:
        for row in csv.DictReader(handle):
            yield {str(key): "" if value is None else str(value) for key, value in row.items()}


def _read_csv_any(path: Path) -> list[dict[str, str]]:
    """Materialize one bounded file for append/rewrite call sites only."""

    return list(_iter_csv_any(path))


def _write_gzip_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def _feature_files(cfg: EngineConfig) -> list[Path]:
    archive_root = cfg.output_root / "polymarket_training_archive"
    files = (
        sorted(
            path
            for path in archive_root.glob("*.csv.gz")
            if not path.name.startswith("daily_official_books_")
        )
        if archive_root.exists()
        else []
    )
    live = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if live.exists():
        files.append(live)
    return files


def _official_book_files(cfg: EngineConfig) -> list[Path]:
    root = cfg.output_root / "maker_carry" / "official_books"
    return sorted(root.glob("*.csv.gz")) if root.exists() else []


def _archived_book_levels(value: Any) -> tuple[list[tuple[float, float]], bool]:
    """Return exact archived levels and whether a level payload was observed."""

    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return [], False
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            try:
                payload = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                return [], False
    if not isinstance(payload, list):
        return [], False
    levels: list[tuple[float, float]] = []
    for level in payload:
        if not isinstance(level, dict):
            continue
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append((price, size))
    return levels, bool(levels)


def _book_states_from_rows(
    rows: Iterable[dict[str, Any]],
    token_ids: set[str],
    replay_days: float,
) -> dict[str, list[dict[str, Any]]]:
    max_stamp = 0.0
    latest_by_minute: dict[tuple[str, float], dict[str, Any]] = {}
    for row in rows:
        token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if token_id not in token_ids:
            continue
        # Official point snapshots retain the venue's last-change timestamp,
        # but replay coverage is bounded by when this system observed the book.
        stamp = _stamp(
            row.get("observation_timestamp") or row.get("source_timestamp") or row.get("collected_at_utc")
        )
        bid = safe_float(row.get("best_bid"))
        ask = safe_float(row.get("best_ask"))
        midpoint = safe_float(row.get("midpoint"))
        if stamp is None or bid is None or ask is None or ask <= bid:
            continue
        midpoint = midpoint if midpoint is not None else (bid + ask) / 2.0
        bid_levels, bid_levels_observed = _archived_book_levels(row.get("bids_json"))
        ask_levels, ask_levels_observed = _archived_book_levels(row.get("asks_json"))
        resting_bid_depth = safe_float(row.get("resting_bid_depth_at_quote"))
        resting_ask_depth = safe_float(row.get("resting_ask_depth_at_quote"))
        max_stamp = max(max_stamp, stamp)
        parsed = {
            "stamp": stamp,
            "minute": _minute(stamp),
            "token_id": token_id,
            "best_bid": bid,
            "best_ask": ask,
            "midpoint": midpoint,
            "bid_depth": resting_bid_depth
            if resting_bid_depth is not None
            else safe_float(row.get("top_bid_size"))
            or safe_float(row.get("bid_depth_1pct"))
            or 0.0,
            "ask_depth": resting_ask_depth
            if resting_ask_depth is not None
            else safe_float(row.get("top_ask_size"))
            or safe_float(row.get("ask_depth_1pct"))
            or 0.0,
            "resting_bid_depth_at_quote": resting_bid_depth,
            "resting_ask_depth_at_quote": resting_ask_depth,
            "bid_levels": bid_levels,
            "ask_levels": ask_levels,
            "bid_levels_observed": bid_levels_observed,
            "ask_levels_observed": ask_levels_observed,
        }
        key = (token_id, parsed["minute"])
        previous = latest_by_minute.get(key)
        if previous is None or parsed["stamp"] >= previous["stamp"]:
            latest_by_minute[key] = parsed
    cutoff = max_stamp - replay_days * 86400.0 if max_stamp and replay_days > 0 else float("-inf")
    by_token: dict[str, list[dict[str, Any]]] = {}
    for row in latest_by_minute.values():
        if row["stamp"] >= cutoff:
            by_token.setdefault(str(row["token_id"]), []).append(row)
    for token_rows in by_token.values():
        token_rows.sort(key=lambda item: item["stamp"])
    return by_token


def _book_states(cfg: EngineConfig, token_ids: set[str], replay_days: float) -> dict[str, list[dict[str, Any]]]:
    rows = (row for path in _feature_files(cfg) for row in _iter_csv_any(path))
    return _book_states_from_rows(rows, token_ids, replay_days)


def _official_book_states(cfg: EngineConfig, token_ids: set[str], replay_days: float) -> dict[str, list[dict[str, Any]]]:
    rows = (row for path in _official_book_files(cfg) for row in _iter_csv_any(path))
    return _book_states_from_rows(rows, token_ids, replay_days)


def _trades(cfg: EngineConfig, markets: set[str], token_ids: set[str]) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for row in _iter_csv_any(cfg.output_root / "polymarket_trade_prints" / "trade_prints.csv"):
        market = str(row.get("market") or "").strip()
        token_id = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if market not in markets and token_id not in token_ids:
            continue
        stamp = _stamp(row.get("timestamp") or row.get("collected_at_utc"))
        price = safe_float(row.get("price"))
        size = safe_float(row.get("size"))
        side = str(row.get("side") or "").upper()
        if stamp is None or price is None or size is None or side not in {"BUY", "SELL"}:
            continue
        trades.append({"stamp": stamp, "market": market, "token_id": token_id, "side": side, "price": price, "size": size})
    trades.sort(key=lambda item: item["stamp"])
    return trades


def _state_at_or_before(states: list[dict[str, Any]], stamp: float) -> dict[str, Any] | None:
    index = bisect_right(states, stamp, key=lambda row: row["stamp"]) - 1
    return states[index] if index >= 0 else None


def _state_at_or_after(states: list[dict[str, Any]], stamp: float) -> dict[str, Any] | None:
    index = bisect_left(states, stamp, key=lambda row: row["stamp"])
    return states[index] if index < len(states) else None


def _queue_depth_at_quote(
    state: dict[str, Any],
    *,
    direction: str,
    quote: float,
) -> tuple[float | None, str]:
    """Measure queue ahead only from quote-aligned or full-level evidence."""

    if direction == "bid_fill":
        aligned = safe_float(state.get("resting_bid_depth_at_quote"))
        levels = state.get("bid_levels")
        observed = state.get("bid_levels_observed") is True
    else:
        aligned = safe_float(state.get("resting_ask_depth_at_quote"))
        levels = state.get("ask_levels")
        observed = state.get("ask_levels_observed") is True
    if observed and isinstance(levels, list):
        if direction == "bid_fill":
            depth = sum(size for price, size in levels if float(price) >= quote - 1e-12)
        else:
            depth = sum(size for price, size in levels if float(price) <= quote + 1e-12)
        return depth, "full_book_levels"
    if aligned is not None:
        return aligned, "quote_aligned_depth"
    return None, "unavailable"


def _candidate_map(cfg: EngineConfig) -> dict[str, dict[str, str]]:
    rows = read_csv_rows(cfg.output_root / "maker_carry" / "maker_carry_candidates.csv")
    return {str(row.get("condition_id") or ""): row for row in rows if row.get("condition_id")}


def _valid_stale_reasons(value: Any) -> list[str]:
    """WO-147 fix round (F8): a stale-map entry is trustworthy only as a
    list/tuple of `str` reasons.

    Anything else - a bare string (which would otherwise explode into
    per-character "reasons" if handed straight to `list()`), a dict, a
    number, or a list/tuple containing a non-str element - is malformed and
    is treated as carrying NO reasons: not iterated, and handled by the
    exact same path as an absent or genuinely empty entry ("no reasons for
    this id"), never silently vanished into a false "kept, nothing to see
    here".
    """

    if isinstance(value, (list, tuple)) and all(isinstance(reason, str) for reason in value):
        return list(value)
    return []


def _watchlist_expired_reasons(
    row: dict[str, Any], stale_map: dict[str, list[str]], *, as_of: datetime
) -> list[str]:
    """WO-147 (147.2): union of stale-map and current-row expiry evidence.

    Calls `maker_carry_study._candidate_staleness_reasons` verbatim - the
    precedent registered at :585-590 above ("a second implementation would
    drift from the rule it is meant to describe") - rather than
    reimplementing date parsing. `row` is a `maker_carry_candidates.csv`
    row (or an empty/partial stand-in with `condition_id` always populated
    by the caller, even when the row is otherwise absent). Either reason
    source is independently sufficient and neither is conditioned on the
    other; the stale map's positive evidence therefore excludes a market
    even when the current row's own fields are themselves missing (WO-147
    test 10 - the 61-market persistent case). A malformed stale-map entry
    (F8) is validated via `_valid_stale_reasons` rather than iterated
    directly.
    """

    from .maker_carry_study import _candidate_staleness_reasons

    condition_id = str(row.get("condition_id") or "").strip()
    reasons = _valid_stale_reasons(stale_map.get(condition_id))
    market = {
        "endDateIso": row.get("end_date_utc"),
        "question": row.get("question"),
        "umaResolutionStatus": row.get("uma_resolution_status"),
    }
    for reason in _candidate_staleness_reasons(market, as_of=as_of):
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def _watchlist_kept_counters(row: dict[str, Any]) -> tuple[bool, bool]:
    """WO-147 (147.3): which 'kept' diagnostics apply to a non-excluded row.

    Meaningful only once `_watchlist_expired_reasons` returned no reasons for
    this row - by construction that also means the stale map carries no
    VALID, non-empty entry for it (per `_valid_stale_reasons`, F8 fix round:
    an absent id, a genuinely empty `[]`, and a malformed non-list/tuple-of-
    str entry are all "no reasons for this id" and route through this same
    kept-counting path rather than any of them silently vanishing), which is
    the fail-safe precedence rule's own guarantee. A missing/empty/
    unparseable/non-finite `end_date_utc` cannot itself be read as fresh,
    and a missing `question` or `uma_resolution_status` cannot itself be
    read as clean, so each is counted rather than silently assumed live.
    Reuses `normalize_external_timestamp`; never a second date parser.
    """

    close_time_unparseable = normalize_external_timestamp(row.get("end_date_utc")) is None
    missing_fields = not str(row.get("question") or "").strip() or not str(
        row.get("uma_resolution_status") or ""
    ).strip()
    return close_time_unparseable, missing_fields


def _watchlist_stale_map(raw_summary: Any, *, as_of: datetime) -> tuple[dict[str, list[str]], str]:
    """WO-147 (147.2/147.3): the persisted stale map, gated by freshness.

    Fails open toward collecting on every branch - the map returned is
    EMPTY, never partially trusted, whenever the status is anything but
    "ok": `raw_summary` missing/unreadable/not-a-dict => "unavailable"; a
    present dict with no `excluded_stale_condition_ids` key at all, or that
    key present but `None` (F2 fix round - a valid dict study file that
    simply never reached the point of writing the key is unmeasured, not a
    genuinely empty scan, and must not be indistinguishable from "ok") =>
    "unavailable"; a present-but-non-dict `excluded_stale_condition_ids`, or
    a present dict any of whose entries is not a list/tuple of `str` (F8 fix
    round - routed through this SAME "malformed" classification rather than
    a new status value) => "malformed"; an absent, unparseable, future, or
    more-than-48h-old `generated_at_utc` => "stale_ignored". A present,
    genuinely empty `excluded_stale_condition_ids: {}` (a clean scan that
    found nothing stale) stays "ok". Basis for 48.0h: two times the
    registered study interval `OPS_MAKER_STUDY_INTRADAY_INTERVAL_SECONDS`
    default 86400s - one missed study run tolerated, two is not evidence.
    """

    if not isinstance(raw_summary, dict):
        return {}, "unavailable"
    if "excluded_stale_condition_ids" not in raw_summary:
        return {}, "unavailable"
    condition_ids = raw_summary.get("excluded_stale_condition_ids")
    if condition_ids is None:
        return {}, "unavailable"
    if not isinstance(condition_ids, dict):
        return {}, "malformed"
    validated_condition_ids: dict[str, list[str]] = {}
    for condition_id, entry in condition_ids.items():
        if not (isinstance(entry, (list, tuple)) and all(isinstance(reason, str) for reason in entry)):
            return {}, "malformed"
        validated_condition_ids[condition_id] = list(entry)
    study_as_of = parse_timestamp(raw_summary.get("generated_at_utc"))
    if study_as_of is None:
        return {}, "stale_ignored"
    age_hours = (as_of - study_as_of).total_seconds() / 3600.0
    if age_hours < 0 or age_hours > 48.0:
        return {}, "stale_ignored"
    return validated_condition_ids, "ok"


def _portfolio(summary: dict[str, Any], candidates: dict[str, dict[str, str]], max_markets: int) -> list[dict[str, Any]]:
    portfolio: list[dict[str, Any]] = []
    for entry in (summary.get("portfolio") or [])[:max_markets]:
        if not isinstance(entry, dict):
            continue
        condition_id = str(entry.get("condition_id") or "").strip()
        candidate = candidates.get(condition_id, {})
        token_id = str(entry.get("token_id") or candidate.get("token_id") or "").strip()
        quote_size = safe_float(entry.get("quote_size_shares"))
        quote_distance = safe_float(entry.get("quote_distance"))
        if condition_id and token_id and quote_size is not None and quote_distance is not None:
            portfolio.append({**entry, "condition_id": condition_id, "token_id": token_id, "quote_size_shares": quote_size, "quote_distance": quote_distance})
    return portfolio


def _recent_book_markets(
    cfg: EngineConfig,
    settings: dict[str, Any],
    *,
    exclude: set[str],
    stale_map: dict[str, list[str]] | None = None,
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Markets whose official-book file was appended within the regime window.

    WO-104: Tier-0 markouts need CONTINUOUS book history around trades, but the
    study portfolio churns daily, so a market that leaves the top portfolio
    used to stop being snapshotted and its coverage never matured. Keeping a
    recently-active market on the snapshot watchlist for the regime window lets
    a persistent/recurring market accumulate the coverage the evaluator needs.
    Recency uses file mtime (cheap); the token id comes from the candidate map,
    falling back to the file's last recorded asset id. Read-only collection;
    no gate, sizing, or order path reads it.

    WO-147 (147.2): each candidate is checked against
    `_watchlist_expired_reasons` before being added to the watchlist -
    positive evidence of pastness (from `stale_map` or the candidate's own
    parsed fields) excludes it and is counted under the returned
    diagnostics' `"expired"` key; every other outcome keeps it, per this
    collector's registered fail-open direction (:134-138 above).
    """
    exclusions: dict[str, Any] = {
        "expired": 0,
        "close_time_unparseable": 0,
        "kept_missing_fields": 0,
        "examples": [],
    }
    books_dir = cfg.output_root / "maker_carry" / "official_books"
    if not books_dir.exists():
        return [], exclusions
    horizon = float(settings["regime_days"]) * 86400.0
    now = time.time()
    candidates = _candidate_map(cfg)
    stale_map = stale_map or {}
    check_as_of = as_of or datetime.now(timezone.utc)
    watchlist: list[dict[str, Any]] = []

    def _mtime_desc(path: Path) -> float:
        try:
            return -path.stat().st_mtime
        except OSError:
            # An unreadable entry must not prevent the remaining archives from
            # being considered. It sorts behind every known modification time.
            return float("inf")

    for path in sorted(books_dir.glob("*.csv.gz"), key=lambda p: (_mtime_desc(p), p.name)):
        condition_id = path.name[: -len(".csv.gz")]  # .stem strips only ".gz"
        if condition_id in exclude:
            continue
        try:
            if now - path.stat().st_mtime > horizon:
                continue
        except OSError:
            continue
        row = {**candidates.get(condition_id, {}), "condition_id": condition_id}
        reasons = _watchlist_expired_reasons(row, stale_map, as_of=check_as_of)
        if reasons:
            exclusions["expired"] += 1
            if len(exclusions["examples"]) < 10:
                exclusions["examples"].append(
                    {"condition_id": condition_id, "tranche": "persistent", "reasons": reasons}
                )
            continue
        if not _valid_stale_reasons(stale_map.get(condition_id)):
            close_time_unparseable, missing_fields = _watchlist_kept_counters(row)
            if close_time_unparseable:
                exclusions["close_time_unparseable"] += 1
            if missing_fields:
                exclusions["kept_missing_fields"] += 1
        token_id = str(row.get("token_id") or "").strip()
        if not token_id:
            rows = _read_csv_any(path)
            token_id = str(rows[-1].get("asset_id") or "").strip() if rows else ""
        if token_id:
            watchlist.append({"condition_id": condition_id, "token_id": token_id})
    return watchlist, exclusions


def _candidate_seed_markets(
    candidates: dict[str, dict[str, str]],
    *,
    exclude: set[str],
    cap: int,
    skip_tokens: set[str] | None = None,
    stale_map: dict[str, list[str]] | None = None,
    as_of: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """Top-ranked study candidates not already on the watchlist (WO-116).

    Fresh rewarded markets carry no local book history, so the WO-113
    measurement-eligibility gate (rightly) keeps them out of the portfolio -
    but portfolio-only collection then never lets them season, and a
    fast-churning rewarded universe starves the portfolio to 0-1 eligible
    markets. Seeding collection for the best-ranked candidates lets them
    accumulate the required history while still ineligible; the existing
    mtime-based persistent tranche keeps them warm afterwards. Read-only
    collection breadth: no gate, sizing, eligibility, or order path reads it.

    WO-147 (147.2): an expired candidate (per `_watchlist_expired_reasons`)
    is skipped before ranking, exactly like the other skip reasons below,
    and counted under `excluded["expired"]` so it surfaces through
    `candidate_seed_exclusions`. The third return value carries the
    close_time_unparseable/kept_missing_fields/examples diagnostics (147.3)
    separately, so `candidate_seed_exclusions`'s existing three-key schema
    stays exactly what WO-116 registered, plus this WO's "expired".
    """
    excluded = {"delisted_cooldown": 0, "non_finite_rank": 0, "missing_token": 0, "expired": 0}
    expiry_diagnostics: dict[str, Any] = {
        "close_time_unparseable": 0,
        "kept_missing_fields": 0,
        "examples": [],
    }
    if cap <= 0:
        return [], excluded, expiry_diagnostics
    skip = skip_tokens or set()
    stale_map = stale_map or {}
    check_as_of = as_of or datetime.now(timezone.utc)
    ranked: list[tuple[float, float, str, str]] = []
    for condition_id, row in candidates.items():
        if condition_id in exclude:
            continue
        reasons = _watchlist_expired_reasons(row, stale_map, as_of=check_as_of)
        if reasons:
            excluded["expired"] += 1
            if len(expiry_diagnostics["examples"]) < 10:
                expiry_diagnostics["examples"].append(
                    {"condition_id": condition_id, "tranche": "seed", "reasons": reasons}
                )
            continue
        if not _valid_stale_reasons(stale_map.get(condition_id)):
            close_time_unparseable, missing_fields = _watchlist_kept_counters(row)
            if close_time_unparseable:
                expiry_diagnostics["close_time_unparseable"] += 1
            if missing_fields:
                expiry_diagnostics["kept_missing_fields"] += 1
        token_id = str(row.get("token_id") or "").strip()
        if not token_id:
            excluded["missing_token"] += 1
            continue
        if token_id in skip:
            excluded["delisted_cooldown"] += 1
            continue
        carry = safe_float(row.get("net_carry_usd_per_day"))
        yield_rank = safe_float(row.get("yield_rank"))
        # WO-131: a NaN carry participates in the sort with UNDEFINED ordering
        # and can take a seeding slot ahead of a real candidate. Today such rows
        # sort last only by accident of -(-inf); exclude them explicitly and
        # count them, so an upstream producer emitting NaN is visible.
        if (carry is not None and not isfinite(carry)) or (
            yield_rank is not None and not isfinite(yield_rank)
        ):
            excluded["non_finite_rank"] += 1
            continue
        ranked.append(
            (
                -(carry if carry is not None else float("-inf")),
                yield_rank if yield_rank is not None else float("inf"),
                condition_id,
                token_id,
            )
        )
    ranked.sort()
    # WO-139: spend the fixed collection budget on rows that already clear the
    # sizer's non-depth predicates, without reimplementing or changing the
    # sizer. Missing/malformed fields simply keep a row in the legacy ranking;
    # they never remove it or shrink the tranche.
    tier1: list[tuple[float, float, str, str]] = []
    tier2: list[tuple[float, float, str, str]] = []
    remainder: list[tuple[float, float, str, str]] = []
    for entry in ranked:
        carry = -entry[0]
        row = candidates[entry[2]]
        band_eligible = str(row.get("band_eligible") or "").strip().lower()
        clears_common_predicates = (
            isfinite(carry)
            and carry > 0.0
            and band_eligible == "true"
            and str(row.get("resolution_risk") or "").strip().lower() != "high"
        )
        estimate_quality = str(row.get("estimate_quality") or "").strip()
        if clears_common_predicates and estimate_quality == "book_and_history":
            tier1.append(entry)
        elif clears_common_predicates and estimate_quality == "single_window_history":
            tier2.append(entry)
        else:
            remainder.append(entry)
    ranked = tier1 + tier2 + remainder
    return (
        [
            {"condition_id": condition_id, "token_id": token_id}
            for _, _, condition_id, token_id in ranked[:cap]
        ],
        excluded,
        expiry_diagnostics,
    )


def _seasoning_runway(out_root: Path, watchlist: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-market book depth against the registered eligibility floors.

    WO-131. The 48h / 100-snapshot runway was previously only inferrable, so
    nobody could say which seeded market was closest to becoming measurable.
    Depth is read from ``maker_carry_study._book_history_depth`` - the exact
    helper ``_measurement_eligible`` consumes - because a second implementation
    would drift from the rule it is meant to describe. Reporting only: no gate,
    threshold, or eligibility path reads any of this.
    """

    from .maker_carry_study import MAKER_POLICY_DEFAULTS, _book_history_depth

    min_hours = float(MAKER_POLICY_DEFAULTS.get("maker_min_book_history_hours", 48.0))
    min_snaps = int(MAKER_POLICY_DEFAULTS.get("maker_min_book_snapshots", 100))
    rows: list[dict[str, Any]] = []
    for entry in watchlist:
        condition_id = str(entry.get("condition_id") or "")
        try:
            hours, snapshots = _book_history_depth(out_root, condition_id)
        except (OSError, ValueError):
            # Unmeasurable depth reports null and is never ranked as closer to
            # eligibility than a measured market.
            rows.append(
                {
                    "condition_id": condition_id,
                    "book_history_hours": None,
                    "book_snapshot_count": None,
                    "hours_remaining": None,
                    "snapshots_remaining": None,
                }
            )
            continue
        rows.append(
            {
                "condition_id": condition_id,
                "book_history_hours": round(float(hours), 4),
                "book_snapshot_count": int(snapshots),
                "hours_remaining": round(max(0.0, min_hours - float(hours)), 4),
                "snapshots_remaining": max(0, min_snaps - int(snapshots)),
            }
        )
    measured = [row for row in rows if row["hours_remaining"] is not None]
    closest = sorted(
        measured, key=lambda row: (row["hours_remaining"], row["snapshots_remaining"])
    )[:3]
    return {
        "markets": rows,
        "closest": closest,
        "min_book_history_hours": min_hours,
        "min_book_snapshots": min_snaps,
    }


def _payload_snapshots(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("history", "data", "results", "orderbooks", "books"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return [payload] if ("bids" in payload or "asks" in payload) else []


def _levels(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _best_level(levels: list[dict[str, Any]], *, side: str) -> tuple[float | None, float]:
    parsed: list[tuple[float, float]] = []
    for level in levels:
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is not None and size is not None and size >= 0:
            parsed.append((price, size))
    if not parsed:
        return None, 0.0
    price, size = (max(parsed, key=lambda item: item[0]) if side == "bid" else min(parsed, key=lambda item: item[0]))
    return price, size


def _official_row(snapshot: dict[str, Any], *, condition_id: str, token_id: str, collected_at: str) -> dict[str, Any] | None:
    stamp = _stamp(snapshot.get("timestamp") or snapshot.get("t") or snapshot.get("createdAt") or snapshot.get("created_at"))
    observation_stamp = _stamp(collected_at)
    bids = _levels(snapshot.get("bids"))
    asks = _levels(snapshot.get("asks"))
    bid, bid_size = _best_level(bids, side="bid")
    ask, ask_size = _best_level(asks, side="ask")
    if stamp is None or bid is None or ask is None or ask <= bid:
        return None
    return {
        "condition_id": condition_id,
        "asset_id": str(snapshot.get("asset_id") or snapshot.get("token_id") or snapshot.get("market") or token_id),
        "source_timestamp": stamp,
        "observation_timestamp": observation_stamp if observation_stamp is not None else stamp,
        "hash": str(snapshot.get("hash") or snapshot.get("book_hash") or f"{int(stamp)}:{bid}:{ask}"),
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": (bid + ask) / 2.0,
        "top_bid_size": bid_size,
        "top_ask_size": ask_size,
        "bids_json": json.dumps(bids, separators=(",", ":")),
        "asks_json": json.dumps(asks, separators=(",", ":")),
        "collected_at_utc": collected_at,
    }


def _books_by_token(payload: Any, token_ids: list[str]) -> dict[str, dict[str, Any]]:
    snapshots = _payload_snapshots(payload)
    books: dict[str, dict[str, Any]] = {}
    for index, snapshot in enumerate(snapshots):
        token_id = str(snapshot.get("asset_id") or snapshot.get("token_id") or "").strip()
        if not token_id and index < len(token_ids):
            token_id = token_ids[index]
        if token_id:
            books[token_id] = snapshot
    return books


_SNAPSHOT_SCOPES = frozenset({"watchlist", "portfolio"})

# WO-148: the maker-watchlist tier-assignment event ledger. Append-only per
# 148.1 - never write_csv, never truncated, row-capped, sorted, or rewritten.
# The WO-115 incident (a full-rewrite writer on an append-only-shaped ledger
# re-serialised historical rows and blocked every anchor run for ten days) is
# the reason. Deliberately NOT enrolled in ledger_anchor.DEFAULT_LEDGER_REGISTRY
# nor the example config's `ledger_globs` - see 148.4. Referenced by name in
# exactly this one place so the WO-148 test (16) static scan for the literal
# path string has exactly one call site to find.
TIER_EVENTS_CSV_NAME = "maker_watchlist_tier_events.csv"
TIER_STATE_JSON_NAME = "maker_watchlist_tier_state.json"
TIER_EVENT_FIELDS = ["event_utc", "condition_id", "token_id", "previous_tier", "tier"]
_TIER_DOMAIN = frozenset({"portfolio", "persistent", "seed", "absent"})
# 148.3: 6.0h == 24 consecutive missed 900s `run_trade_prints` cycles; measured
# against the deployed collection ledger's observed 637-minute (10.62h) worst
# inter-poll gap, not chosen for roundness.
_TIER_STATE_MAX_AGE_SECONDS = 21600.0
# 148.3: deployed caps (max_markets/max_persistent_markets/max_candidate_markets
# all 25) bound a total-churn cycle at 75 departures + 75 arrivals == 150 rows;
# 200 is 150 + 33% headroom, so exceeding it is structurally impossible and
# signals a defect - rows are still written, never dropped.
_TIER_EVENT_BURST_THRESHOLD = 200
# The four fields `_portfolio` (:426-438) reads from a portfolio entry.
_PORTFOLIO_CONTRACT_FIELDS = ("condition_id", "token_id", "quote_size_shares", "quote_distance")


def _portfolio_field_contract_valid(value: Any) -> bool:
    """§148.6 correction 2: is a `maker_carry_study.json` ``portfolio`` field
    shaped like a real (possibly genuinely empty) portfolio, rather than a
    read failure or malformed producer output masquerading as one?

    A list is contract-valid if it is empty - a well-formed producer can
    legitimately rank zero candidates - or if AT LEAST ONE entry is a dict
    carrying at least one of the four fields ``_portfolio`` reads. Only a
    NON-EMPTY list every entry of which fails that per-entry shape test is
    contract-invalid; a mixed list (some entries pass, some fail) is not,
    because ``_portfolio``'s own per-entry filtering already tolerates that.
    """

    if not isinstance(value, list):
        return False
    if not value:
        return True
    return any(
        isinstance(entry, dict) and any(field in entry for field in _PORTFOLIO_CONTRACT_FIELDS)
        for entry in value
    )


def _tier_summary_contract_valid(maker_summary: Any) -> bool:
    """§148.6 correction 2, condition (1): the summary must be a mapping
    carrying a contract-valid ``portfolio`` field - not merely "a dict",
    because ``read_json``'s own failure default is also ``{}``, identical by
    construction to a syntactically valid but semantically empty summary.
    """

    if not isinstance(maker_summary, dict):
        return False
    if "portfolio" not in maker_summary:
        return False
    return _portfolio_field_contract_valid(maker_summary.get("portfolio"))


def _write_tier_events(
    out_root: Path,
    generated_at: str,
    portfolio: list[dict[str, Any]],
    persistent: list[dict[str, Any]],
    seeds: list[dict[str, Any]],
) -> dict[str, Any]:
    """148.1/148.3: diff this cycle's watchlist against the ledger's own last
    row per condition id and append only the transitions, then (on success
    only) snapshot the 148.2 liveness state file.

    Callers must have already confirmed ``scope="watchlist"`` and the §148.6
    three-input contract (summary contract-valid, candidates CSV present,
    ``official_books`` directory present) - this function performs no scope
    or input-existence check of its own; it only ever runs the diff.
    """

    events_path = out_root / TIER_EVENTS_CSV_NAME
    state_path = out_root / TIER_STATE_JSON_NAME

    # 148.3(2): current_tier from this cycle's three lists; multi-membership
    # is impossible by construction (`exclude` at :791, :810-812) but if it
    # occurs precedence is portfolio > persistent > seed - iterate in that
    # order and count every later duplicate as a conflict without moving it.
    current_tier: dict[str, str] = {}
    current_token: dict[str, str] = {}
    precedence_conflicts = 0
    for tier_name, entries in (("portfolio", portfolio), ("persistent", persistent), ("seed", seeds)):
        for entry in entries:
            condition_id = str(entry.get("condition_id") or "").strip()
            if not condition_id:
                continue
            if condition_id in current_tier:
                precedence_conflicts += 1
                continue
            current_tier[condition_id] = tier_name
            current_token[condition_id] = str(entry.get("token_id") or "").strip()

    # A2/148.3(1): unreadable ledger (OSError/csv.Error) -> no rows appended,
    # "read_failed", state not written - never diffed from an unknown baseline.
    try:
        existing_rows = read_csv_rows(events_path)
    except (OSError, csv.Error):
        return {
            "tier_events_status": "read_failed",
            "tier_events_written": 0,
            "tier_events_resync": False,
            "tier_events_malformed_rows": 0,
            "tier_event_burst": False,
            "tier_precedence_conflicts": precedence_conflicts,
        }

    # 148.3(1): last_tier = the tier of the LAST row in file order per
    # condition id; absent -> "absent" (handled by the .get default below).
    # A2: a row with empty condition_id or a tier outside the closed domain
    # is ignored for last_tier and counted, never repaired or rewritten.
    last_tier: dict[str, str] = {}
    last_token: dict[str, str] = {}
    malformed_rows = 0
    for row in existing_rows:
        condition_id = str(row.get("condition_id") or "").strip()
        tier = str(row.get("tier") or "").strip()
        if not condition_id or tier not in _TIER_DOMAIN:
            malformed_rows += 1
            continue
        last_tier[condition_id] = tier
        last_token[condition_id] = str(row.get("token_id") or "").strip()

    # 148.3(3)-(4): resync=True when the state file is missing, unreadable,
    # not a dict, `generated_at_utc` absent/unparseable, age negative, or
    # age > 21600.0 (6.0h), anchored to the run clock (S1).
    state = read_json(state_path, default=None)
    resync = True
    if isinstance(state, dict):
        state_dt = parse_timestamp(state.get("generated_at_utc"))
        run_dt = parse_timestamp(generated_at)
        if state_dt is not None and run_dt is not None:
            age = (run_dt - state_dt).total_seconds()
            if isfinite(age) and 0.0 <= age <= _TIER_STATE_MAX_AGE_SECONDS:
                resync = False

    # 148.3(5)-(6): emit one row per condition id whose CURRENT tier differs
    # from its REAL last tier (the idempotency dedup key), with the RECORDED
    # previous_tier replaced by "unknown" only when resync fires - resync
    # changes what is written, never whether a transition is considered to
    # have happened.
    event_rows: list[dict[str, Any]] = []
    for condition_id in sorted(set(last_tier) | set(current_tier)):
        last = last_tier.get(condition_id, "absent")
        current = current_tier.get(condition_id, "absent")
        if current == last:
            continue
        token_id = current_token.get(condition_id) or last_token.get(condition_id, "")
        event_rows.append(
            {
                "event_utc": generated_at,
                "condition_id": condition_id,
                "token_id": token_id,
                "previous_tier": "unknown" if resync else last,
                "tier": current,
            }
        )

    # A2: `append_csv_rows` raising is caught - "write_failed", state not
    # written, and the caller (snapshot_official_books) continues normally -
    # a measurement sidecar must never stop the collector.
    try:
        append_csv_rows(events_path, event_rows, fieldnames=TIER_EVENT_FIELDS)
    except Exception:
        return {
            "tier_events_status": "write_failed",
            "tier_events_written": 0,
            "tier_events_resync": resync,
            "tier_events_malformed_rows": malformed_rows,
            "tier_event_burst": False,
            "tier_precedence_conflicts": precedence_conflicts,
        }

    # 148.2: liveness state file, written AFTER the events append, atomic
    # full-rewrite, only on a successful append. B1 fix round: this write is
    # brought under the SAME failure containment as the append above - on a
    # zero-transition cycle (the steady state; Day-after check (2) requires
    # it) `append_csv_rows` short-circuits before touching disk when `rows`
    # is empty (utils.py:191-192), making this write_json call the cycle's
    # first disk write. A raise here (state path exists as a directory,
    # ENOSPC/EROFS/permission) must not escape - "write_failed", same as an
    # append failure, and the caller (snapshot_official_books) continues
    # normally - a measurement sidecar must never stop the collector.
    try:
        write_json(
            state_path,
            {
                "generated_at_utc": generated_at,
                "work_order": "WO-148",
                "watchlist_size": len(portfolio) + len(persistent) + len(seeds),
                "tier_counts": {
                    "portfolio": len(portfolio),
                    "persistent": len(persistent),
                    "seed": len(seeds),
                },
                "reporting_only": True,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
        )
    except Exception:
        return {
            "tier_events_status": "write_failed",
            "tier_events_written": len(event_rows),
            "tier_events_resync": resync,
            "tier_events_malformed_rows": malformed_rows,
            "tier_event_burst": len(event_rows) > _TIER_EVENT_BURST_THRESHOLD,
            "tier_precedence_conflicts": precedence_conflicts,
        }

    return {
        "tier_events_status": "ok",
        "tier_events_written": len(event_rows),
        "tier_events_resync": resync,
        "tier_events_malformed_rows": malformed_rows,
        "tier_event_burst": len(event_rows) > _TIER_EVENT_BURST_THRESHOLD,
        "tier_precedence_conflicts": precedence_conflicts,
    }


def snapshot_official_books(cfg: EngineConfig, *, scope: str = "watchlist") -> dict[str, Any]:
    """Append one current official CLOB book for tracked markets.

    WO-83 deliberately uses the documented current ``/book``/``/books`` API.
    Repeated cadence snapshots create the history; an undocumented historical
    endpoint must not be treated as coverage.

    WO-149 ``scope``. ``scope="watchlist"`` (the default) is byte-identical to
    the original full-watchlist collector on every path: portfolio +
    persistent (WO-104) + candidate-seed (WO-116) tranches, the seasoning
    runway report, and the WO-131 delisted-marker read/write. It writes
    ``official_book_snapshot.json``.

    ``scope="portfolio"`` polls ONLY the current maker-carry portfolio - the
    persistent and candidate-seed tranches are neither computed nor polled -
    and writes ``official_book_pulse.json``, **never**
    ``official_book_snapshot.json``: ``collect_maker_replay_data`` reads the
    latter's ``market_polls`` to build ``maker_replay_collection_windows.csv``,
    and a pulse overwriting it would corrupt the collection-window ledger and
    thence ``coverage_ratio`` (WO-116's registration forbids this). The
    seasoning-runway report is skipped (it is a full-watchlist report and
    would mislead over a portfolio-only slice). The WO-131 delisted-marker
    file is READ, so a token already cooling down is still skipped, but never
    WRITTEN from this scope - the pulse polls up to 3x as often and letting it
    drive the 404 cooldown would silently change what
    ``delisted_skip_threshold`` counts.

    Any other ``scope`` raises ``ValueError`` before any network call or file
    write.
    """

    if scope not in _SNAPSHOT_SCOPES:
        raise ValueError(
            f"snapshot_official_books: scope must be one of {sorted(_SNAPSHOT_SCOPES)}, got {scope!r}"
        )
    portfolio_scope = scope == "portfolio"

    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / ("official_book_pulse.json" if portfolio_scope else "official_book_snapshot.json")
    generated_at = now_utc()
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-44/WO-83",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    # WO-148 §148.6: every return path carries these six keys - initialized
    # here at construction so a path that never reaches the write-site anchor
    # below (the disabled early return, or the no_portfolio early return
    # under either scope) cannot silently drop them and break the
    # scope-invariant key set (test 22).
    summary["tier_events_status"] = "ok"
    summary["tier_events_written"] = 0
    summary["tier_events_resync"] = False
    summary["tier_events_malformed_rows"] = 0
    summary["tier_event_burst"] = False
    summary["tier_precedence_conflicts"] = 0
    if portfolio_scope:
        summary["scope"] = "portfolio"
        summary["work_order"] = "WO-149"
        # §148.6 binding correction: the tier-event ledger is written ONLY
        # when this function runs under scope="watchlist". Under
        # scope="portfolio" no event is appended and no state file is
        # written, regardless of the resulting watchlist's size - `persistent`
        # and `seeds` are forced to `[]` by construction under this scope
        # (the pulse does not compute those two tranches at all), so a writer
        # with no information about them cannot contribute a transition.
        # Guarded by the SAME `portfolio_scope` boolean the tranche
        # computation branches on below (`:764`) - never re-derived here.
        summary["tier_events_status"] = "skipped_portfolio_scope"
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        # §148.6 third binding correction: this exit precedes BOTH
        # scope-based checks and needs its own label, assigned before either
        # `write_json` call so it takes priority regardless of scope -
        # neither "ok" (a disabled collector did not run at all) nor
        # `skipped_portfolio_scope` (which implies the pulse ran and was
        # correctly routed away from the watchlist write) is accurate here.
        summary["tier_events_status"] = "skipped_disabled"
        write_json(summary_path, summary)
        return summary
    # WO-147 (147.2): read once, keeping the raw parse result alongside the
    # dict `_portfolio` below has always consumed, so `_watchlist_stale_map`
    # can distinguish missing/unreadable/not-a-dict ("unavailable") from a
    # present dict whose `excluded_stale_condition_ids` is itself malformed,
    # without a second read of the same file.
    maker_summary_raw = read_json(out_root / "maker_carry_study.json", default=None)
    maker_summary = maker_summary_raw if isinstance(maker_summary_raw, dict) else {}
    candidate_rows = _candidate_map(cfg)
    # §148.6 correction 2: `_portfolio` (:426-438) consumes `summary["portfolio"]`
    # via a raw slice, `(summary.get("portfolio") or [])[:max_markets]` - a
    # truthy non-list scalar (an int or a mapping) RAISES there instead of
    # degrading to a controlled skip. §148.6's second binding correction, and
    # the substitution below that implements it, applies only under
    # `scope="watchlist"` - "as throughout this section, neither applies
    # under scope=\"portfolio\", where the portfolio-scope skip fires first."
    # Gated on the SAME `portfolio_scope` boolean already computed above
    # (`:967`), never re-derived here: under `scope="portfolio"` a malformed
    # summary is passed through UNSUBSTITUTED, preserving the pre-existing
    # loud `TypeError` `_portfolio` raises on it, because that scope's own
    # skip (`skipped_portfolio_scope`) already makes this contract check
    # unnecessary there. Under `scope="watchlist"` an invalid summary is
    # substituted with `{}`, which `_portfolio` already handles exactly like
    # every OTHER bad-but-non-crashing shape it survives (a falsy scalar, an
    # empty string) - contributing zero portfolio entries, never raising.
    summary_contract_ok = _tier_summary_contract_valid(maker_summary)
    portfolio = _portfolio(
        maker_summary if (portfolio_scope or summary_contract_ok) else {},
        candidate_rows,
        int(settings["max_markets"]),
    )
    tier_inputs_ok = False
    if portfolio_scope:
        # WO-149.1(1): scope="portfolio" polls ONLY the current portfolio; the
        # persistent (WO-104) and candidate-seed (WO-116) tranches are
        # full-watchlist collection breadth and are neither computed nor
        # polled from this scope.
        persistent: list[dict[str, Any]] = []
        seeds: list[dict[str, Any]] = []
        seed_exclusions = {"delisted_cooldown": 0, "non_finite_rank": 0, "missing_token": 0, "expired": 0}
        # WO-149.1(4): the WO-131 cooldown marker is READ (so a cooling-down
        # token is still skipped here) but never WRITTEN from this scope
        # (see below) - the pulse polls up to 3x as often, and letting it
        # drive the 404 cooldown would change delisted_skip_threshold from
        # "three collector cycles" to "three cycles of a different job".
        markers = _read_delisted_markers(out_root)
        now_dt = parse_timestamp(generated_at) or datetime.now(timezone.utc)
        skip_tokens = _delisted_skip_tokens(
            markers,
            threshold=int(settings["delisted_skip_threshold"]),
            cooldown_hours=float(settings["delisted_cooldown_hours"]),
            now=now_dt,
        )
        portfolio = [entry for entry in portfolio if str(entry["token_id"]) not in skip_tokens]
    else:
        # WO-104: keep recently-active markets on the watchlist so a persistent or
        # recurring market accumulates continuous Tier-0 book coverage across
        # portfolio churn, bounded by max_markets.
        markers = _read_delisted_markers(out_root)
        now_dt = parse_timestamp(generated_at) or datetime.now(timezone.utc)
        # WO-147 (147.2/147.3): the persisted stale map and its freshness gate,
        # read against this run's own clock (`now_dt`) - never the max of
        # observed data timestamps.
        stale_map, stale_map_status = _watchlist_stale_map(maker_summary_raw, as_of=now_dt)
        persistent, persistent_exclusions = _recent_book_markets(
            cfg,
            settings,
            exclude={str(entry["condition_id"]) for entry in portfolio},
            stale_map=stale_map,
            as_of=now_dt,
        )
        # Always cover the FULL current portfolio (already capped at max_markets),
        # then reserve a separate budget for persistent markets so a full portfolio
        # can no longer crowd recently-active recurring markets off the watchlist.
        persistent_cap = max(0, int(settings.get("max_persistent_markets", settings["max_markets"])))
        persistent = persistent[:persistent_cap]
        # WO-116: third tranche - seed collection for the best-ranked candidates so
        # they season toward the WO-113 book-history requirement before selection.
        # Runs even when the portfolio is empty (exactly the starved state it fixes).
        # WO-131: tokens inside their delisted cooldown are not seeded this cycle.
        skip_tokens = _delisted_skip_tokens(
            markers,
            threshold=int(settings["delisted_skip_threshold"]),
            cooldown_hours=float(settings["delisted_cooldown_hours"]),
            now=now_dt,
        )
        seeds, seed_exclusions, seed_expiry_diagnostics = _candidate_seed_markets(
            candidate_rows,
            exclude={str(entry["condition_id"]) for entry in portfolio}
            | {str(entry["condition_id"]) for entry in persistent},
            cap=max(0, int(settings.get("max_candidate_markets", 0))),
            skip_tokens=skip_tokens,
            stale_map=stale_map,
            as_of=now_dt,
        )
        # WO-147 (147.2): the portfolio tranche is explicitly NEVER excluded
        # (WO-116's registration binds here - dropping a portfolio market on
        # the strength of a possibly-stale study file would blank a
        # measurement denominator) but positive expiry evidence against it is
        # still counted for observability.
        portfolio_observed_not_excluded = 0
        for entry in portfolio:
            condition_id = str(entry["condition_id"])
            observed_row = {**candidate_rows.get(condition_id, {}), "condition_id": condition_id}
            if _watchlist_expired_reasons(observed_row, stale_map, as_of=now_dt):
                portfolio_observed_not_excluded += 1
        watchlist_excluded_expired_examples_raw = (
            persistent_exclusions["examples"] + seed_expiry_diagnostics["examples"]
        )
        # §148.6 second binding correction, checked first and independently
        # of the resulting watchlist's size: ALL THREE tranche inputs must be
        # confirmed present (and, for the summary, contract-valid) before an
        # empty watchlist can be trusted as a genuine mass departure rather
        # than an artifact of a glitched read on any ONE of them - a failure
        # on only the portfolio's summary read, or only the candidates CSV,
        # or only the official_books directory, does not necessarily empty
        # the other two tranches, so gating on the summary alone (or on
        # emptiness alone) would miss it.
        candidates_csv_path = out_root / "maker_carry_candidates.csv"
        books_dir = out_root / "official_books"
        tier_inputs_ok = (
            summary_contract_ok
            and candidates_csv_path.exists()
            and books_dir.exists()
        )
        if not tier_inputs_ok:
            summary["tier_events_status"] = "skipped_unreadable_inputs"
    # WO-147 (147.3): built once, right after both tranches are known, so it
    # is recorded on EVERY path this scope takes below - including the
    # no-watchlist early return just below, where "everything got excluded"
    # is exactly the observability case this WO exists to surface, not a
    # reason to drop the diagnostic.
    watchlist_excluded_expired = None
    watchlist_excluded_expired_examples = None
    if not portfolio_scope:
        watchlist_excluded_expired = {
            "persistent": persistent_exclusions["expired"],
            "seed": seed_exclusions["expired"],
            "portfolio_observed_not_excluded": portfolio_observed_not_excluded,
            "close_time_unparseable": (
                persistent_exclusions["close_time_unparseable"]
                + seed_expiry_diagnostics["close_time_unparseable"]
            ),
            "kept_missing_fields": (
                persistent_exclusions["kept_missing_fields"]
                + seed_expiry_diagnostics["kept_missing_fields"]
            ),
            "stale_map_status": stale_map_status,
        }
        watchlist_excluded_expired_examples = sorted(
            watchlist_excluded_expired_examples_raw, key=lambda example: example["condition_id"]
        )[:10]
    watchlist = portfolio + persistent + seeds
    if not watchlist:
        if tier_inputs_ok:
            # §148.6 second binding correction: a genuinely empty watchlist
            # backed by fully-validated tranche inputs is truth, not an
            # artifact of a read failure - run the diff so a real mass
            # departure is recorded (up to the entire prior watchlist)
            # instead of silently lost via this shortcut, which is the exact
            # confusion 148.1 exists to end.
            summary.update(_write_tier_events(out_root, generated_at, portfolio, persistent, seeds))
        summary.update(
            {
                "status": "no_portfolio",
                "markets_polled": 0,
                "rows_added": 0,
                "market_polls": [],
                "errors": [],
                "portfolio_markets": 0,
                "persistent_markets": 0,
                "candidate_seed_markets": 0,
            }
        )
        if watchlist_excluded_expired is not None:
            summary["watchlist_excluded_expired"] = watchlist_excluded_expired
            summary["watchlist_excluded_expired_examples"] = watchlist_excluded_expired_examples
        write_json(summary_path, summary)
        return summary
    summary["portfolio_markets"] = len(portfolio)
    summary["persistent_markets"] = len(persistent)
    summary["candidate_seed_markets"] = len(seeds)
    if tier_inputs_ok:
        # WO-148 write site: the tier assignment is a fact regardless of
        # whether the HTTP polls below succeed, so it is recorded here,
        # before the batch POST - this keeps a heartbeat when polling fails.
        summary.update(_write_tier_events(out_root, generated_at, portfolio, persistent, seeds))

    base = str(settings["clob_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    pause = max(float(settings["request_pause_seconds"]), 0.0)
    token_ids = [str(entry["token_id"]) for entry in watchlist]
    books: dict[str, dict[str, Any]] = {}
    batch_error = ""
    if len(token_ids) > 1:
        try:
            response = requests.post(
                f"{base}/books",
                json=[{"token_id": token_id} for token_id in token_ids],
                timeout=timeout,
            )
            response.raise_for_status()
            books.update(_books_by_token(response.json(), token_ids))
        except Exception as exc:
            batch_error = f"{type(exc).__name__}: {exc}"

    missing = [token_id for token_id in token_ids if token_id not in books]
    fetch_errors: dict[str, str] = {}
    for index, token_id in enumerate(missing):
        try:
            response = requests.get(f"{base}/book", params={"token_id": token_id}, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                books[token_id] = payload
            else:
                fetch_errors[token_id] = "invalid_book_payload"
        except Exception as exc:
            fetch_errors[token_id] = f"{type(exc).__name__}: {exc}"
        if pause and index < len(missing) - 1:
            time.sleep(max(pause, 0.1))

    rows_added = 0
    files_written: list[str] = []
    market_polls: list[dict[str, Any]] = []
    errors: list[str] = []
    for entry in watchlist:
        condition_id = str(entry["condition_id"])
        token_id = str(entry["token_id"])
        snapshot = books.get(token_id)
        error = fetch_errors.get(token_id, "")
        row = (
            _official_row(snapshot, condition_id=condition_id, token_id=token_id, collected_at=generated_at)
            if isinstance(snapshot, dict)
            else None
        )
        if row is None:
            error = error or "invalid_or_empty_book"
            errors.append(f"{condition_id}: {error}")
            market_polls.append(
                {
                    "condition_id": condition_id,
                    "asset_id": token_id,
                    "status": "failed",
                    "rows_added": 0,
                    "error": error,
                }
            )
            continue

        path = out_root / "official_books" / f"{condition_id}.csv.gz"
        existing = _read_csv_any(path)
        existing_keys = {
            (
                str(item.get("observation_timestamp") or item.get("source_timestamp") or ""),
                str(item.get("hash") or ""),
            )
            for item in existing
        }
        dedup: dict[tuple[str, str], dict[str, Any]] = {}
        for item in [*existing, row]:
            key = (
                str(item.get("observation_timestamp") or item.get("source_timestamp") or ""),
                str(item.get("hash") or ""),
            )
            if all(key):
                dedup[key] = item
        combined = sorted(
            dedup.values(),
            key=lambda item: _stamp(item.get("observation_timestamp"))
            or _stamp(item.get("source_timestamp"))
            or 0.0,
        )
        max_rows = int(settings["max_official_book_rows"])
        if max_rows > 0 and len(combined) > max_rows:
            combined = combined[-max_rows:]
        _write_gzip_csv(path, combined, OFFICIAL_BOOK_FIELDS)
        added = int((str(row["observation_timestamp"]), str(row["hash"])) not in existing_keys)
        rows_added += added
        files_written.append(str(path))
        market_polls.append(
            {
                "condition_id": condition_id,
                "asset_id": token_id,
                "status": "ok",
                "rows_added": added,
                "error": "",
            }
        )

    successful = sum(row["status"] == "ok" for row in market_polls)
    status = "ok" if successful == len(watchlist) else ("partial" if successful else "failed")

    if portfolio_scope:
        # WO-149.1(4): this scope never writes the WO-131 cooldown ledger -
        # the marker file above was read-only. `delisted_token_count` still
        # reports the as-read state so the field stays meaningful.
        updated_markers = markers
    else:
        # WO-131: fold this cycle's 404s into the cooldown ledger, and clear any
        # token that returned a book. Written atomically by this lane only.
        updated_markers = _update_delisted_markers(
            markers,
            polls=market_polls,
            generated_at=generated_at,
            cooldown_hours=float(settings["delisted_cooldown_hours"]),
        )
        write_json(
            out_root / DELISTED_MARKER_FILE,
            {
                "work_order": "WO-131",
                "generated_at_utc": generated_at,
                "reporting_only": True,
                "skip_threshold": int(settings["delisted_skip_threshold"]),
                "cooldown_hours": float(settings["delisted_cooldown_hours"]),
                "tokens": updated_markers,
                "paper_trading_invoked": False,
                "live_trading_invoked": False,
            },
        )

    if portfolio_scope:
        # WO-149.1(3): a full-watchlist-only report; it would mislead over a
        # portfolio-only slice, so it is skipped entirely for this scope.
        runway = {"markets": [], "closest": []}
    else:
        # WO-131: the 48h/100-snapshot runway, measured by the SAME helper the
        # eligibility rule uses, so the report and the rule cannot drift.
        runway = _seasoning_runway(out_root, watchlist)
    summary.update(
        {
            "status": status,
            "markets_polled": len(watchlist),
            "markets_succeeded": successful,
            "rows_added": rows_added,
            "files_written": files_written,
            "market_polls": market_polls,
            "batch_fallback_reason": batch_error,
            "errors": errors[:10],
            "candidate_seed_exclusions": seed_exclusions,
            "delisted_tokens_skipped": sorted(skip_tokens),
            "delisted_token_count": len(updated_markers),
            "seasoning_runway": runway["markets"],
            "closest_to_eligibility": runway["closest"],
            "note": (
                "Current official CLOB books for exactly the maker quote-sheet portfolio. "
                "Repeated point-in-time snapshots form the replay archive; no orders or gates are touched."
            ),
        }
    )
    if watchlist_excluded_expired is not None:
        # WO-147 (147.3): diagnostics only - no gate, sizing, eligibility, or
        # order surface reads this artifact. `official_book_pulse.json`
        # (scope="portfolio") never carries these keys; WO-147's logic is
        # structurally unreachable there (persistent/seed are forced empty
        # above and this file is not `official_book_snapshot.json`).
        summary["watchlist_excluded_expired"] = watchlist_excluded_expired
        summary["watchlist_excluded_expired_examples"] = watchlist_excluded_expired_examples
    if portfolio_scope:
        summary["delisted_marker_write"] = "skipped_portfolio_scope"
    write_json(summary_path, summary)
    return summary


def collect_maker_replay_data(cfg: EngineConfig) -> dict[str, Any]:
    """Collect matched book/print observations for the current quote sheet."""

    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_replay_collection.json"
    ledger_path = out_root / "maker_replay_collection_windows.csv"
    generated_at = now_utc()
    maker_summary = read_json(out_root / "maker_carry_study.json", default={}) or {}
    if not isinstance(maker_summary, dict):
        maker_summary = {}
    portfolio = _portfolio(maker_summary, _candidate_map(cfg), int(settings["max_markets"]))
    base: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-83",
        "portfolio_generated_at_utc": str(maker_summary.get("generated_at_utc") or ""),
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, base)
        return base
    if not portfolio:
        # Churn gap: the current quote sheet is empty, but snapshot_official_books
        # still refreshes recently-active (persistent) markets via its watchlist,
        # so Tier-0 coverage keeps maturing across the gap. Previously this
        # early return skipped the snapshot entirely on the scheduled path.
        book_summary = snapshot_official_books(cfg)
        payload = {
            **base,
            "status": "no_portfolio",
            "markets_polled": int(book_summary.get("markets_polled") or 0),
            "windows_covered": 0,
            "persistent_snapshot_status": book_summary.get("status"),
        }
        write_json(summary_path, payload)
        return payload

    book_summary = snapshot_official_books(cfg)
    trade_summary = collect_maker_portfolio_trade_prints(cfg)
    books = {
        str(row.get("condition_id") or ""): row
        for row in (book_summary.get("market_polls") or [])
        if isinstance(row, dict)
    }
    prints = {
        str(row.get("condition_id") or ""): row
        for row in (trade_summary.get("market_polls") or [])
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for entry in portfolio:
        condition_id = str(entry["condition_id"])
        book = books.get(condition_id, {})
        trade = prints.get(condition_id, {})
        covered = book.get("status") == "ok" and trade.get("status") == "ok"
        rows.append(
            {
                "window_id": generated_at,
                "condition_id": condition_id,
                "asset_id": entry["token_id"],
                "portfolio_generated_at_utc": str(maker_summary.get("generated_at_utc") or ""),
                "collected_at_utc": generated_at,
                "quote_bid_price": entry.get("quote_bid_price", ""),
                "quote_ask_price": entry.get("quote_ask_price", ""),
                "quote_size_shares": entry.get("quote_size_shares", ""),
                "book_poll_status": str(book.get("status") or "not_observed"),
                "book_snapshot_rows": int(safe_float(book.get("rows_added")) or 0),
                "trade_poll_status": str(trade.get("status") or "not_observed"),
                "trade_prints_returned": int(safe_float(trade.get("prints_returned")) or 0),
                "covered": covered,
            }
        )

    existing = read_csv_rows(ledger_path)
    dedup = {
        (str(row.get("window_id") or ""), str(row.get("condition_id") or "")): row
        for row in [*existing, *rows]
        if row.get("window_id") and row.get("condition_id")
    }
    combined = sorted(dedup.values(), key=lambda row: str(row.get("collected_at_utc") or ""))
    max_rows = int(settings["max_collection_window_rows"])
    if max_rows > 0 and len(combined) > max_rows:
        combined = combined[-max_rows:]
    write_csv(ledger_path, combined, fieldnames=COLLECTION_WINDOW_FIELDS)
    covered_count = sum(bool(row["covered"]) for row in rows)
    payload = {
        **base,
        "status": "ok" if covered_count == len(rows) else ("partial" if covered_count else "failed"),
        "markets_polled": len(rows),
        "windows_simulated": len(rows),
        "windows_covered": covered_count,
        "coverage_ratio": round(covered_count / len(rows), 6) if rows else None,
        "market_windows": rows,
        "book_snapshot": book_summary,
        "trade_print_collection": trade_summary,
        "collection_ledger": str(ledger_path),
        "note": "Matched official-book and public-print polls only; a successful zero-print response is covered.",
    }
    write_json(summary_path, payload)
    return payload


def _mean(values: Iterable[float]) -> float | None:
    vals = list(values)
    return round(sum(vals) / len(vals), 6) if vals else None


def _percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def _nearest_rank_percentile(values: list[float], quantile: float) -> float | None:
    """Nearest-rank percentile - no interpolation, so tests can hand-compute it.

    WO-149.2: ``ordinal rank = ceil(quantile * n)`` (1-indexed) over the
    ascending-sorted population; the element at that rank is returned as-is.
    An empty population returns ``None`` (never ``0.0`` - a ``0.0`` p90 would
    read as perfect freshness).
    """

    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    rank = max(1, min(n, ceil(quantile * n)))
    return round(ordered[rank - 1], 6)


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    observed = [float(value) for value in values]
    return {
        "count": len(observed),
        "mean": _mean(observed),
        "min": round(min(observed), 6) if observed else None,
        "p25": _percentile(observed, 0.25),
        "median": _percentile(observed, 0.5),
        "p75": _percentile(observed, 0.75),
        "p95": _percentile(observed, 0.95),
        "max": round(max(observed), 6) if observed else None,
    }


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _coverage_slice(rows: list[dict[str, Any]], portfolio: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, dict[str, Any]] = {
        str(entry["condition_id"]): {
            "condition_id": str(entry["condition_id"]),
            "asset_id": str(entry["token_id"]),
            "question": str(entry.get("question") or ""),
            "windows_simulated": 0,
            "windows_covered": 0,
        }
        for entry in portfolio
    }
    for row in rows:
        condition_id = str(row.get("condition_id") or "")
        target = by_condition.get(condition_id)
        if target is None:
            continue
        target["windows_simulated"] += 1
        target["windows_covered"] += int(_truthy(row.get("covered")))
    per_market = []
    for target in by_condition.values():
        simulated = int(target["windows_simulated"])
        covered = int(target["windows_covered"])
        target["coverage_ratio"] = round(covered / simulated, 6) if simulated else None
        per_market.append(target)
    simulated_total = sum(int(row["windows_simulated"]) for row in per_market)
    covered_total = sum(int(row["windows_covered"]) for row in per_market)
    if simulated_total == 0:
        status = "not_observed"
    elif covered_total == simulated_total:
        status = "covered"
    elif covered_total:
        status = "partial"
    else:
        status = "insufficient_coverage"
    return {
        "status": status,
        "windows_simulated": simulated_total,
        "windows_covered": covered_total,
        "coverage_ratio": round(covered_total / simulated_total, 6) if simulated_total else None,
        "per_market": per_market,
    }


def _collection_coverage(cfg: EngineConfig, portfolio: list[dict[str, Any]], regime_days: float) -> dict[str, Any]:
    current = {str(entry["condition_id"]) for entry in portfolio}
    rows = [
        row
        for row in _iter_csv_any(
            cfg.output_root / "maker_carry" / "maker_replay_collection_windows.csv"
        )
        if str(row.get("condition_id") or "") in current
    ]
    stamps = [stamp for row in rows if (stamp := _stamp(row.get("collected_at_utc"))) is not None]
    cutoff = max(stamps) - regime_days * 86400.0 if stamps else None
    recent = [row for row in rows if cutoff is not None and (_stamp(row.get("collected_at_utc")) or 0.0) >= cutoff]
    prior = [row for row in rows if cutoff is not None and (_stamp(row.get("collected_at_utc")) or 0.0) < cutoff]
    return {
        **_coverage_slice(rows, portfolio),
        "regime_cut": {
            "cutoff_utc": (
                time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff)) if cutoff is not None else None
            ),
            "last_7_days": _coverage_slice(recent, portfolio),
            "prior_to_last_7_days": _coverage_slice(prior, portfolio),
        },
    }


def _study_charge_by_condition(
    cfg: EngineConfig, portfolio: list[dict[str, Any]]
) -> dict[str, float]:
    candidates = _candidate_map(cfg)
    charges: dict[str, float] = {}
    for entry in portfolio:
        condition_id = str(entry["condition_id"])
        candidate = candidates.get(condition_id, {})
        charge = safe_float(candidate.get("adverse_selection_usd_per_day")) or 0.0
        size_multiple = safe_float(entry.get("size_multiple")) or 1.0
        charges[condition_id] = charges.get(condition_id, 0.0) + charge * size_multiple
    return charges


def _replay_against_states(
    *,
    source: str,
    states_by_token: dict[str, list[dict[str, Any]]],
    trades: list[dict[str, Any]],
    portfolio: list[dict[str, Any]],
    study_charge: float,
    study_charge_by_condition: dict[str, float],
    max_state_lag_seconds: float,
    quote_sheet_generated_stamp: float | None = None,
    quoting_basis: str = "static_sheet",
    start_stamp: float | None = None,
    end_stamp: float | None = None,
) -> dict[str, Any]:
    portfolio_by_token = {row["token_id"]: row for row in portfolio}
    coverage_by_token: dict[str, dict[str, Any]] = {
        str(row["token_id"]): {
            "condition_id": str(row["condition_id"]),
            "asset_id": str(row["token_id"]),
            "question": str(row.get("question") or ""),
            "simulated_fill_opportunities": 0,
            "last_in_queue_evaluable_opportunities": 0,
            "queue_depth_unavailable_opportunities": 0,
            "no_contemporaneous_state_opportunities": 0,
            "confirmed_fills": 0,
            "windows_simulated": 0,
            "windows_covered": 0,
            "book_history_span_days": 0.0,
            "by_horizon": {
                f"{horizon}m": {"windows_simulated": 0, "windows_covered": 0}
                for horizon in HORIZONS_MINUTES
            },
        }
        for row in portfolio
    }
    # WO-113: record the observed official-book span per market so audits can see
    # how deep the archive is next to the (window-aligned) coverage ratio.
    for _token, _cov in coverage_by_token.items():
        _states = states_by_token.get(_token) or []
        if len(_states) >= 2:
            _cov["book_history_span_days"] = round(
                (_states[-1]["stamp"] - _states[0]["stamp"]) / 86400.0, 4
            )
    fills: list[dict[str, Any]] = []
    simulated_fill_opportunities = 0
    last_in_queue_evaluable_opportunities = 0
    no_contemporaneous_state_opportunities = 0
    relevant_trades: list[dict[str, Any]] = []
    # WO-149.2: entry-side book-state staleness at the fill join, measured
    # (never gated - max_book_state_lag_seconds is unchanged at 1800). Only
    # finite lags enter the percentile population; A2 excludes None/non-finite
    # lags and counts them separately so a corrupt timestamp can never read as
    # "fresh".
    entry_state_lags: list[float] = []
    entry_state_lag_unmeasurable = 0
    fills_beyond_legacy_lag = 0
    for trade in trades:
        if start_stamp is not None and trade["stamp"] < start_stamp:
            continue
        if end_stamp is not None and trade["stamp"] >= end_stamp:
            continue
        entry = portfolio_by_token.get(trade["token_id"])
        if entry is None:
            continue
        relevant_trades.append(trade)
        states = states_by_token.get(trade["token_id"]) or []
        state = _state_at_or_before(states, trade["stamp"])
        if state is not None and trade["stamp"] - state["stamp"] > max_state_lag_seconds:
            state = None
        historical = (
            quoting_basis == "contemporaneous"
            and (
                quote_sheet_generated_stamp is None
                or trade["stamp"] < quote_sheet_generated_stamp
            )
        )
        quote_rounding = "sheet_absolute_prices"
        if historical:
            if state is None:
                no_contemporaneous_state_opportunities += 1
                coverage_by_token[str(entry["token_id"])][
                    "no_contemporaneous_state_opportunities"
                ] += 1
                continue
            bid_quote = state["midpoint"] - float(entry["quote_distance"])
            ask_quote = state["midpoint"] + float(entry["quote_distance"])
            tick = safe_float(entry.get("order_price_min_tick_size"))
            if tick is not None and tick > 0:
                tick_decimal = Decimal(str(tick))
                bid_quote = float(
                    (Decimal(str(bid_quote)) / tick_decimal).to_integral_value(
                        rounding=ROUND_FLOOR
                    )
                    * tick_decimal
                )
                ask_quote = float(
                    (Decimal(str(ask_quote)) / tick_decimal).to_integral_value(
                        rounding=ROUND_CEILING
                    )
                    * tick_decimal
                )
                quote_rounding = "order_price_min_tick_size_outward"
            else:
                quote_rounding = "raw_midpoint_distance"
        else:
            bid_quote = safe_float(entry.get("quote_bid_price"))
            ask_quote = safe_float(entry.get("quote_ask_price"))
            if (bid_quote is None or ask_quote is None) and state is not None:
                bid_quote = state["midpoint"] - float(entry["quote_distance"])
                ask_quote = state["midpoint"] + float(entry["quote_distance"])
                quote_rounding = "raw_midpoint_distance_fallback"
        if bid_quote is None or ask_quote is None or ask_quote <= bid_quote:
            continue
        if trade["side"] == "SELL" and trade["price"] <= bid_quote:
            fill_price = bid_quote
            direction = "bid_fill"
        elif trade["side"] == "BUY" and trade["price"] >= ask_quote:
            fill_price = ask_quote
            direction = "ask_fill"
        else:
            continue
        simulated_fill_opportunities += 1
        market_coverage = coverage_by_token[str(entry["token_id"])]
        market_coverage["simulated_fill_opportunities"] += 1
        later_by_horizon: dict[int, dict[str, float]] = {}
        observed_last = states[-1]["stamp"] if states else None
        for horizon in HORIZONS_MINUTES:
            key = f"{horizon}m"
            target_stamp = trade["stamp"] + horizon * 60.0
            # WO-113 coverage-window alignment: only windows the OBSERVED official-
            # book history can bracket enter the denominator. A window needs a
            # book state at/before the fill (queue depth, `state`) AND observed
            # history reaching the markout horizon (`observed_last >= target`).
            # Fills outside that span are physically unmeasurable, so counting
            # them as "simulated-but-uncovered" made the 80% coverage minimum
            # unreachable for weeks after a market entered the portfolio.
            if state is None or observed_last is None or observed_last < target_stamp:
                continue
            market_coverage["windows_simulated"] += 1
            market_coverage["by_horizon"][key]["windows_simulated"] += 1
            later = _state_at_or_after(states, target_stamp)
            if later is None or later["stamp"] - target_stamp > max_state_lag_seconds:
                continue
            later_by_horizon[horizon] = later
            market_coverage["windows_covered"] += 1
            market_coverage["by_horizon"][key]["windows_covered"] += 1

        if state is None:
            market_coverage["queue_depth_unavailable_opportunities"] += 1
            continue
        depth_ahead, queue_depth_source = _queue_depth_at_quote(
            state,
            direction=direction,
            quote=fill_price,
        )
        if depth_ahead is None:
            market_coverage["queue_depth_unavailable_opportunities"] += 1
            continue
        last_in_queue_evaluable_opportunities += 1
        market_coverage["last_in_queue_evaluable_opportunities"] += 1
        fillable = trade["size"] - depth_ahead
        if fillable <= 0:
            continue
        fill_size = min(float(entry["quote_size_shares"]), fillable)
        if fill_size <= 0:
            continue
        market_coverage["confirmed_fills"] += 1
        markouts: dict[str, float] = {}
        adverse_usd: dict[str, float] = {}
        for horizon in HORIZONS_MINUTES:
            later = later_by_horizon.get(horizon)
            if later is None:
                continue
            if direction == "bid_fill":
                per_share = fill_price - later["midpoint"]
            else:
                per_share = later["midpoint"] - fill_price
            markouts[f"{horizon}m"] = round(per_share, 6)
            adverse_usd[f"{horizon}m"] = round(per_share * fill_size, 6)

        # WO-149.2: entry-side book-state staleness for THIS confirmed fill.
        # `state` is guaranteed not None here (both `continue`s above already
        # require it); the `state is not None` guard is A2 defence-in-depth
        # against a non-finite stamp, not dead code for the documented path.
        # A2: None or non-finite excludes the lag from the percentile
        # population and counts it in `entry_state_lag_unmeasurable` - never
        # coerced to zero, which would read as perfect freshness.
        entry_state_lag_seconds: float | None = None
        if state is not None:
            raw_entry_lag = trade["stamp"] - state["stamp"]
            if isfinite(raw_entry_lag):
                entry_state_lag_seconds = round(raw_entry_lag, 6)
                entry_state_lags.append(raw_entry_lag)
                # Fixed 1800.0 literal, independent of `max_state_lag_seconds`:
                # this counts fills against the LEGACY tolerance so a future
                # widening of the deployed value is measured against today's
                # bound by the same code, not a moving target.
                if raw_entry_lag > 1800.0:
                    fills_beyond_legacy_lag += 1
            else:
                entry_state_lag_unmeasurable += 1

        # WO-149.2: markout-leg staleness per covered horizon - how stale the
        # LATER state was relative to its markout target. Already bounded by
        # `max_state_lag_seconds` above (the tolerance gates the markout leg
        # too); recorded here for observability, not a new gate.
        later_state_lag_seconds: dict[str, float] = {}
        for horizon in HORIZONS_MINUTES:
            later = later_by_horizon.get(horizon)
            if later is None:
                continue
            target_stamp = trade["stamp"] + horizon * 60.0
            raw_later_lag = later["stamp"] - target_stamp
            if isfinite(raw_later_lag):
                later_state_lag_seconds[f"{horizon}m"] = round(raw_later_lag, 6)

        fills.append(
            {
                "source": source,
                "condition_id": entry["condition_id"],
                "token_id": entry["token_id"],
                "direction": direction,
                "stamp": trade["stamp"],
                "fill_price": round(fill_price, 6),
                "fill_size": round(fill_size, 6),
                "depth_ahead": round(depth_ahead, 6),
                "queue_depth_source": queue_depth_source,
                "quoting_basis": "contemporaneous" if historical else "static_sheet",
                "quote_rounding": quote_rounding,
                "trade_size": trade["size"],
                "markout_per_share": markouts,
                "adverse_usd": adverse_usd,
                "entry_state_lag_seconds": entry_state_lag_seconds,
                "later_state_lag_seconds": later_state_lag_seconds,
            }
        )

    all_stamps = [
        row["stamp"]
        for rows in states_by_token.values()
        for row in rows
        if (start_stamp is None or row["stamp"] >= start_stamp - max_state_lag_seconds)
        and (end_stamp is None or row["stamp"] < end_stamp + 60 * max(HORIZONS_MINUTES) + max_state_lag_seconds)
    ]
    if not all_stamps:
        all_stamps = [trade["stamp"] for trade in relevant_trades]
    span_days = max((max(all_stamps) - min(all_stamps)) / 86400.0, 1.0 / 1440.0) if all_stamps else 1.0
    adverse_5m = sum((fill.get("adverse_usd") or {}).get("5m", 0.0) for fill in fills)
    realized_adverse = round(max(0.0, adverse_5m / span_days), 6)

    per_market_coverage: list[dict[str, Any]] = []
    for row in coverage_by_token.values():
        opportunities = int(row["simulated_fill_opportunities"])
        evaluable = int(row["last_in_queue_evaluable_opportunities"])
        confirmed = int(row["confirmed_fills"])
        simulated_windows = int(row["windows_simulated"])
        covered_windows = int(row["windows_covered"])
        for horizon in HORIZONS_MINUTES:
            horizon_row = row["by_horizon"][f"{horizon}m"]
            denominator = int(horizon_row["windows_simulated"])
            horizon_row["coverage_ratio"] = (
                round(int(horizon_row["windows_covered"]) / denominator, 6) if denominator else None
            )
        haircut_windows = int(row["by_horizon"]["5m"]["windows_covered"])
        row["confirmed_fill_ratio"] = round(confirmed / evaluable, 6) if evaluable else None
        row["confirmed_fill_ratio_status"] = "observed" if evaluable else (
            "insufficient_coverage" if opportunities else "no_simulated_fill_opportunities"
        )
        row["coverage_ratio"] = round(covered_windows / simulated_windows, 6) if simulated_windows else None
        if opportunities and haircut_windows == 0:
            row["coverage_status"] = "insufficient_coverage"
        elif opportunities and covered_windows < simulated_windows:
            row["coverage_status"] = "partial"
        elif opportunities:
            row["coverage_status"] = "covered"
        else:
            row["coverage_status"] = "no_simulated_fill_opportunities"
        token_fills = [
            fill for fill in fills if str(fill.get("token_id") or "") == str(row["asset_id"])
        ]
        row["realized_markout_distribution"] = {
            f"{horizon}m": _distribution(
                (fill.get("markout_per_share") or {}).get(f"{horizon}m")
                for fill in token_fills
                if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
            )
            for horizon in HORIZONS_MINUTES
        }
        market_adverse_5m = sum(
            (fill.get("adverse_usd") or {}).get("5m", 0.0) for fill in token_fills
        )
        market_stamps = [
            state["stamp"] for state in states_by_token.get(str(row["asset_id"]), [])
        ]
        market_stamps.extend(
            trade["stamp"]
            for trade in relevant_trades
            if str(trade.get("token_id") or "") == str(row["asset_id"])
        )
        market_span_days = (
            max((max(market_stamps) - min(market_stamps)) / 86400.0, 1.0 / 1440.0)
            if market_stamps
            else 1.0
        )
        market_realized_adverse = round(
            max(0.0, market_adverse_5m / market_span_days), 6
        )
        market_study_charge = round(
            float(study_charge_by_condition.get(str(row["condition_id"]), 0.0)), 6
        )
        row["realized_adverse_usd_per_day"] = market_realized_adverse
        row["simulated_adverse_charge_usd_per_day"] = market_study_charge
        row["replay_span_days"] = round(market_span_days, 6)
        row["simulation_to_reality_haircut"] = (
            round(market_realized_adverse / market_study_charge, 6)
            if market_study_charge > 0 and haircut_windows > 0
            else None
        )
        per_market_coverage.append(row)

    windows_simulated = sum(int(row["windows_simulated"]) for row in per_market_coverage)
    windows_covered = sum(int(row["windows_covered"]) for row in per_market_coverage)
    by_horizon = {
        f"{horizon}m": {
            "windows_simulated": sum(
                int(row["by_horizon"][f"{horizon}m"]["windows_simulated"])
                for row in per_market_coverage
            ),
            "windows_covered": sum(
                int(row["by_horizon"][f"{horizon}m"]["windows_covered"])
                for row in per_market_coverage
            ),
        }
        for horizon in HORIZONS_MINUTES
    }
    for row in by_horizon.values():
        row["coverage_ratio"] = (
            round(row["windows_covered"] / row["windows_simulated"], 6)
            if row["windows_simulated"]
            else None
        )
    haircut_coverage = int(by_horizon["5m"]["windows_covered"])
    insufficient = (
        simulated_fill_opportunities > 0 and haircut_coverage == 0
    ) or (
        quoting_basis == "contemporaneous"
        and no_contemporaneous_state_opportunities > 0
        and simulated_fill_opportunities == 0
    )
    if insufficient:
        coverage_status = "insufficient_coverage"
        haircut: float | str | None = "insufficient_coverage"
    else:
        coverage_status = (
            "covered"
            if windows_simulated and windows_covered == windows_simulated
            else ("partial" if windows_covered else "no_simulated_fill_opportunities")
        )
        haircut = round(realized_adverse / study_charge, 6) if study_charge > 0 else None

    markout_distribution = {
        f"{horizon}m": _distribution(
            (fill.get("markout_per_share") or {}).get(f"{horizon}m")
            for fill in fills
            if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
        )
        for horizon in HORIZONS_MINUTES
    }
    return {
        "status": "insufficient_coverage" if insufficient else "ok",
        "source": source,
        "book_states": sum(len(rows) for rows in states_by_token.values()),
        "simulated_fill_opportunities": simulated_fill_opportunities,
        "last_in_queue_evaluable_opportunities": last_in_queue_evaluable_opportunities,
        "queue_depth_unavailable_opportunities": (
            simulated_fill_opportunities - last_in_queue_evaluable_opportunities
        ),
        "no_contemporaneous_state_opportunities": no_contemporaneous_state_opportunities,
        # WO-149.2: the 23.2%-class figure published rather than hand-computed.
        # `None` when there are no relevant trades to rate (never a spurious 0.0).
        "no_contemporaneous_state_rate": (
            round(no_contemporaneous_state_opportunities / len(relevant_trades), 6)
            if relevant_trades
            else None
        ),
        # WO-149.2: entry-side book-state staleness at the join, over the
        # CONFIRMED fills. Nearest-rank percentiles (no interpolation) so
        # tests can hand-compute them; `None` (never `0.0`) on an empty
        # population. `fills_beyond_legacy_lag` is measured against the fixed
        # 1800.0 literal regardless of the configured tolerance, so a future
        # widening of `max_book_state_lag_seconds` is already counted by this
        # same code.
        "entry_state_lag_seconds_p50": _nearest_rank_percentile(entry_state_lags, 0.5),
        "entry_state_lag_seconds_p90": _nearest_rank_percentile(entry_state_lags, 0.9),
        "entry_state_lag_seconds_max": round(max(entry_state_lags), 6) if entry_state_lags else None,
        "entry_state_lag_unmeasurable": entry_state_lag_unmeasurable,
        "fills_beyond_legacy_lag": fills_beyond_legacy_lag,
        "quoting_basis": quoting_basis,
        "queue_depth_standard": "full_book_levels_or_quote_aligned_depth_only",
        "confirmed_fills": len(fills),
        "confirmed_fill_ratio": (
            round(len(fills) / last_in_queue_evaluable_opportunities, 6)
            if last_in_queue_evaluable_opportunities
            else None
        ),
        "confirmed_fill_ratio_status": (
            "observed"
            if last_in_queue_evaluable_opportunities
            else ("insufficient_coverage" if simulated_fill_opportunities else "no_simulated_fill_opportunities")
        ),
        # Backward-compatible names: these have always represented fills that
        # survive the last-in-queue depth test, not all crossing opportunities.
        "simulated_fills": len(fills),
        "simulated_fills_per_day": round(len(fills) / span_days, 6),
        "markout_per_fill": {
            f"{horizon}m": _mean(
                (fill.get("markout_per_share") or {}).get(f"{horizon}m")
                for fill in fills
                if (fill.get("markout_per_share") or {}).get(f"{horizon}m") is not None
            )
            for horizon in HORIZONS_MINUTES
        },
        "realized_markout_distribution": markout_distribution,
        "realized_adverse_usd_per_day": realized_adverse,
        "simulated_adverse_charge_usd_per_day": round(study_charge, 6),
        "simulation_to_reality_haircut": haircut,
        "coverage_status": coverage_status,
        "coverage": {
            "windows_simulated": windows_simulated,
            "windows_covered": windows_covered,
            "coverage_ratio": round(windows_covered / windows_simulated, 6) if windows_simulated else None,
            "by_horizon": by_horizon,
        },
        "per_market_coverage": per_market_coverage,
        "implied_adverse_usd_per_day": realized_adverse,
        "study_adverse_usd_per_day": round(study_charge, 6),
        "realism_ratio": haircut,
        "fills_preview": fills[:20],
    }


def run_maker_fill_replay(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    summary_path = out_root / "maker_fill_replay.json"
    generated_at = now_utc()
    payload: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": generated_at,
        "work_order": "WO-40/WO-44/WO-83",
        "quoting_basis": "contemporaneous",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, payload)
        return payload

    maker_summary = read_json(out_root / "maker_carry_study.json", default={}) or {}
    if not isinstance(maker_summary, dict):
        maker_summary = {}
    payload["portfolio_generated_at_utc"] = str(maker_summary.get("generated_at_utc") or "")
    portfolio = _portfolio(maker_summary, _candidate_map(cfg), int(settings["max_markets"]))
    if not portfolio:
        payload.update(
            {
                "status": "no_portfolio",
                "portfolio_markets": 0,
                "simulated_fills": 0,
                "simulated_fills_per_day": 0.0,
                "implied_adverse_usd_per_day": 0.0,
                "realism_ratio": None,
                "simulation_to_reality_haircut": None,
                "coverage_status": "no_portfolio",
                "note": "No current maker-carry quote-sheet portfolio with token IDs was available to replay.",
            }
        )
        write_json(summary_path, payload)
        return payload

    token_ids = {row["token_id"] for row in portfolio}
    markets = {row["condition_id"] for row in portfolio}
    trades = _trades(cfg, markets, token_ids)
    collection_coverage = _collection_coverage(cfg, portfolio, float(settings["regime_days"]))
    requested_source = str(settings.get("book_source") or "both").strip().lower()
    if requested_source not in {"archive", "official", "both"}:
        requested_source = "both"
    states_by_source: dict[str, dict[str, list[dict[str, float]]]] = {}
    if requested_source in {"archive", "both"}:
        states_by_source["archive"] = _book_states(cfg, token_ids, 0.0)
    if requested_source in {"official", "both"}:
        states_by_source["official"] = _official_book_states(cfg, token_ids, 0.0)
    if not trades:
        payload.update(
            {
                "status": "no_replay_data",
                "portfolio_markets": len(portfolio),
                "simulated_fills": 0,
                "simulated_fills_per_day": 0.0,
                "implied_adverse_usd_per_day": 0.0,
                "realism_ratio": None,
                "simulation_to_reality_haircut": None,
                "coverage_status": "no_trade_prints",
                "collection_coverage": collection_coverage,
                "note": "No public trade prints were recorded for the current quote-sheet portfolio.",
            }
        )
        write_json(summary_path, payload)
        return payload

    study_charge_by_condition = _study_charge_by_condition(cfg, portfolio)
    study_charge = sum(study_charge_by_condition.values())
    max_state_lag = float(settings["max_book_state_lag_seconds"])
    quote_sheet_generated_stamp = _stamp(payload["portfolio_generated_at_utc"])
    source_results = {
        source: _replay_against_states(
            source=source,
            states_by_token=states,
            trades=trades,
            portfolio=portfolio,
            study_charge=study_charge,
            study_charge_by_condition=study_charge_by_condition,
            max_state_lag_seconds=max_state_lag,
            quote_sheet_generated_stamp=quote_sheet_generated_stamp,
            quoting_basis="contemporaneous",
        )
        for source, states in states_by_source.items()
    }
    static_sheet_results = {
        source: _replay_against_states(
            source=source,
            states_by_token=states,
            trades=trades,
            portfolio=portfolio,
            study_charge=study_charge,
            study_charge_by_condition=study_charge_by_condition,
            max_state_lag_seconds=max_state_lag,
            quote_sheet_generated_stamp=quote_sheet_generated_stamp,
            quoting_basis="static_sheet",
        )
        for source, states in states_by_source.items()
    }
    if requested_source == "archive":
        primary_source = "archive"
    else:
        # WO-83: archive evidence remains diagnostic, but it may not mask a
        # missing official-book validation window.
        primary_source = "official"
    primary = source_results[primary_source]
    source_agreement = None
    if "archive" in source_results and "official" in source_results:
        archive_fills = float(source_results["archive"]["simulated_fills_per_day"])
        official_fills = float(source_results["official"]["simulated_fills_per_day"])
        source_agreement = {
            "archive_fills_per_day": archive_fills,
            "official_fills_per_day": official_fills,
            "fills_per_day_divergence": round(abs(archive_fills - official_fills), 6),
        }

    observed_stamps = [trade["stamp"] for trade in trades]
    observed_stamps.extend(
        row["stamp"] for states in states_by_source.values() for rows in states.values() for row in rows
    )
    cutoff = max(observed_stamps) - float(settings["regime_days"]) * 86400.0
    regime_cut_by_source = {
        source: {
            "last_7_days": _replay_against_states(
                source=source,
                states_by_token=states,
                trades=trades,
                portfolio=portfolio,
                study_charge=study_charge,
                study_charge_by_condition=study_charge_by_condition,
                max_state_lag_seconds=max_state_lag,
                quote_sheet_generated_stamp=quote_sheet_generated_stamp,
                quoting_basis="contemporaneous",
                start_stamp=cutoff,
            ),
            "prior_to_last_7_days": _replay_against_states(
                source=source,
                states_by_token=states,
                trades=trades,
                portfolio=portfolio,
                study_charge=study_charge,
                study_charge_by_condition=study_charge_by_condition,
                max_state_lag_seconds=max_state_lag,
                quote_sheet_generated_stamp=quote_sheet_generated_stamp,
                quoting_basis="contemporaneous",
                end_stamp=cutoff,
            ),
        }
        for source, states in states_by_source.items()
    }
    cutoff_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff))
    payload.update(
        {
            "status": primary["status"],
            "portfolio_markets": len(portfolio),
            "replay_days": float(settings["regime_days"]),
            "book_source_requested": requested_source,
            "primary_book_source": primary_source,
            "available_book_sources": sorted(
                source for source, states in states_by_source.items() if any(states.values())
            ),
            "evaluated_book_sources": sorted(source_results),
            "official_snapshot": read_json(out_root / "official_book_snapshot.json", default={}) or {},
            "book_states": primary["book_states"],
            "trade_prints_seen": len(trades),
            "quoting_basis": "contemporaneous",
            "simulated_fill_opportunities": primary["simulated_fill_opportunities"],
            "no_contemporaneous_state_opportunities": primary[
                "no_contemporaneous_state_opportunities"
            ],
            # WO-149.2: the join-lag measurement that lets a future widening
            # of max_book_state_lag_seconds (unchanged, still 1800) be argued
            # from a distribution rather than a hope.
            "no_contemporaneous_state_rate": primary["no_contemporaneous_state_rate"],
            "entry_state_lag_seconds_p50": primary["entry_state_lag_seconds_p50"],
            "entry_state_lag_seconds_p90": primary["entry_state_lag_seconds_p90"],
            "entry_state_lag_seconds_max": primary["entry_state_lag_seconds_max"],
            "entry_state_lag_unmeasurable": primary["entry_state_lag_unmeasurable"],
            "fills_beyond_legacy_lag": primary["fills_beyond_legacy_lag"],
            "last_in_queue_evaluable_opportunities": primary[
                "last_in_queue_evaluable_opportunities"
            ],
            "confirmed_fills": primary["confirmed_fills"],
            "confirmed_fill_ratio": primary["confirmed_fill_ratio"],
            "confirmed_fill_ratio_status": primary["confirmed_fill_ratio_status"],
            "simulated_fills": primary["simulated_fills"],
            "simulated_fills_per_day": primary["simulated_fills_per_day"],
            "markout_per_fill": primary["markout_per_fill"],
            "realized_markout_distribution": primary["realized_markout_distribution"],
            "realized_adverse_usd_per_day": primary["realized_adverse_usd_per_day"],
            "simulated_adverse_charge_usd_per_day": primary["simulated_adverse_charge_usd_per_day"],
            "simulation_to_reality_haircut": primary["simulation_to_reality_haircut"],
            "coverage_status": primary["coverage_status"],
            "coverage": primary["coverage"],
            "per_market_coverage": primary["per_market_coverage"],
            "collection_coverage": collection_coverage,
            "implied_adverse_usd_per_day": primary["implied_adverse_usd_per_day"],
            "study_adverse_usd_per_day": primary["study_adverse_usd_per_day"],
            "realism_ratio": primary["realism_ratio"],
            # WO-136 one-release audit bridge. These deliberately retain the
            # pre-WO-136 static replay, but no policy or sizing path reads them.
            "static_sheet_realism_ratio": static_sheet_results[primary_source][
                "realism_ratio"
            ],
            "static_sheet_fills_per_day": static_sheet_results[primary_source][
                "simulated_fills_per_day"
            ],
            "realism_ratio_by_source": {source: result["realism_ratio"] for source, result in source_results.items()},
            "source_results": source_results,
            "source_agreement": source_agreement,
            "regime_cut": {
                "cutoff_utc": cutoff_utc,
                "last_7_days": regime_cut_by_source[primary_source]["last_7_days"],
                "prior_to_last_7_days": regime_cut_by_source[primary_source]["prior_to_last_7_days"],
                "by_source": regime_cut_by_source,
            },
            "haircut_policy": {
                "reported_only": True,
                "automatically_applied_to_gate": False,
                "tightening_requires": "dated M-B governance amendment",
                "permitted_direction": "tighten_only",
                "gate_changed_by_this_report": False,
            },
            "fills_preview": primary["fills_preview"],
            "note": (
                "Tier-0 last-in-queue replay. A numeric haircut above 1 means the maker-carry study may "
                "undercharge adverse selection. The haircut is reporting-only and can only support a future "
                "dated tighten-only amendment; it never changes M-B automatically."
            ),
        }
    )
    write_json(summary_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return run_maker_fill_replay(load_config(config_path))
