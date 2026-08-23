from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import EngineConfig
from .crypto_updown_settlement import (
    crypto_updown_proxy_settlement_price as _crypto_updown_proxy_settlement_price,
    crypto_updown_slug_close_time as _crypto_updown_slug_close_time,
    crypto_updown_slug_window as _crypto_updown_slug_window,
)
from .execution_costs import estimate_execution_cost
from .resolution_collector import fetch_gamma_market, infer_market_resolution_rows
from .runtime_lock import runtime_lock_with_heartbeat
from .utils import (
    append_csv_rows_matching_existing_header,
    boolish,
    now_utc,
    parse_timestamp,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)
from .worldcup_validation import normalised_correlation_key, signal_cohort


# WO-119: shadow_fills.csv is enrolled append_only in the WO-61 anchor
# registry, so its writer must never rewrite historical bytes. Canonical
# fill schema (the _append_fill keys); a legacy narrower on-disk header is
# tolerated by appending under it instead of rewriting.
SHADOW_FILL_FIELDS = [
    "shadow_fill_id",
    "shadow_position_id",
    "created_at",
    "side",
    "market_id",
    "token_id",
    "market_slug",
    "question",
    "category",
    "correlation_key",
    "signal_cohort",
    "shadow_source",
    "price",
    "quantity",
    "gross_notional_usdc",
    "reason",
]


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("shadow_cohort_validation", {}) or {}


def _root(cfg: EngineConfig) -> Path:
    root = cfg.output_root / "polymarket_shadow"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _positions_path(cfg: EngineConfig) -> Path:
    return _root(cfg) / str(_settings(cfg).get("positions_file", "shadow_positions.csv"))


def _fills_path(cfg: EngineConfig) -> Path:
    return _root(cfg) / str(_settings(cfg).get("fills_file", "shadow_fills.csv"))


def _write_shadow_pnl_history(cfg: EngineConfig, summary: dict[str, Any]) -> None:
    """Keep one latest cohort P&L snapshot per UTC day for WO-60.

    The JSON remains the current snapshot. The existing CSV becomes the dated,
    deduplicated daily series its name implied, preserving legacy undated rows
    while future factsheet runs accrue real history instead of overwriting it.
    """
    path = cfg.governance_root / "shadow_signal_cohort_pnl.csv"
    generated = str(summary.get("generated_at_utc") or now_utc())
    day = generated[:10]
    current = [
        {"generated_at_utc": generated, **row}
        for row in (summary.get("cohorts") or [])
        if isinstance(row, dict)
    ]
    prior = read_csv_rows(path)
    current_keys = {
        (day, str(row.get("signal_cohort") or "unknown"))
        for row in current
    }
    kept = [
        row
        for row in prior
        if (str(row.get("generated_at_utc") or "")[:10], str(row.get("signal_cohort") or "unknown"))
        not in current_keys
    ]
    combined = kept + current
    max_rows = 200_000
    if len(combined) > max_rows:
        overflow = combined[:-max_rows]
        archive_path = cfg.output_root / "polymarket_training_archive" / "shadow_signal_cohort_pnl_overflow.csv"
        archived = read_csv_rows(archive_path)
        write_csv(archive_path, archived + overflow)
        combined = combined[-max_rows:]
    write_csv(path, combined)


def _shadow_slippage(cfg: EngineConfig, prediction: dict[str, Any], *, stake_usdc: float | None = None) -> float:
    configured = max(0.0, float(cfg.raw.get("costs", {}).get("slippage", 0.0)))
    estimate = estimate_execution_cost(
        prediction,
        stake_usdc=stake_usdc if stake_usdc is not None else float(_settings(cfg).get("stake_usdc", 10.0)),
        flat_slippage=configured,
    )
    value = safe_float(estimate.get("expected_slippage"))
    return configured if value is None else value


def _entry_price_band(cfg: EngineConfig) -> tuple[float, float]:
    """Mirror the paper risk entry band for shadow evidence quality.

    Shadow fills are not paper/live orders, but they can later influence
    promotion readiness.  Keeping the same base entry band prevents research
    evidence from being dominated by simulated buys that paper trading would
    deterministically reject.
    """
    risk = cfg.raw.get("risk", {})
    if not isinstance(risk, dict):
        risk = {}
    minimum_entry_price = safe_float(risk.get("minimum_entry_price"))
    maximum_entry_price = safe_float(risk.get("maximum_entry_price"))
    return (
        float(minimum_entry_price) if minimum_entry_price is not None else 0.05,
        float(maximum_entry_price) if maximum_entry_price is not None else 0.90,
    )


def _mark_price(prediction: dict[str, Any] | None, fallback: float) -> float:
    if prediction:
        for key in ("best_bid", "bid", "executable_sell_price"):
            value = safe_float(prediction.get(key))
            if value is not None and 0 < value < 1:
                return value
        ask = safe_float(prediction.get("executable_price"))
        spread = safe_float(prediction.get("spread"))
        if ask is not None and spread is not None:
            return max(1e-6, min(0.999999, ask - max(0.0, spread)))
        for key in ("market_midpoint", "market_probability", "midpoint"):
            value = safe_float(prediction.get(key))
            if value is not None and 0 < value < 1:
                return value
    return max(1e-6, min(0.999999, fallback))


def _row_json(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("source_signal_json") or "{}"
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str) or not payload.strip():
        return {}
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _cohort_name(row: dict[str, Any]) -> str:
    return str(row.get("signal_cohort") or signal_cohort(row) or "unknown")


def _time_to_close_hours(row: dict[str, Any]) -> float | None:
    payload = _row_payload(row)
    for key in ("close_time", "end_time", "endDate", "endDateIso"):
        parsed = parse_timestamp(row.get(key) or payload.get(key))
        if parsed is not None:
            return (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds() / 3600.0
    for key in ("time_to_close_hours", "hours_to_close"):
        value = safe_float(row.get(key) or payload.get(key))
        if value is not None:
            return value
    return None


def _is_fast_feedback(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    max_hours = float(settings.get("fast_feedback_max_time_to_close_hours", 6.0))
    time_to_close = _time_to_close_hours(row)
    return time_to_close is not None and 0.0 <= time_to_close <= max_hours


def _is_long_horizon(row: dict[str, Any], settings: dict[str, Any]) -> bool:
    min_hours = float(settings.get("long_horizon_min_time_to_close_hours", 24.0))
    time_to_close = _time_to_close_hours(row)
    return time_to_close is None or time_to_close >= min_hours


def _is_fast_crypto_updown_position(position: dict[str, Any]) -> bool:
    window = _crypto_updown_slug_window(position.get("market_slug"))
    return bool(window and window[1] in {5, 15})


def _settlement_only_shadow_position(position: dict[str, Any], settings: dict[str, Any]) -> bool:
    if not boolish(settings.get("settlement_only_fast_crypto_updown", True)):
        return False
    return _is_fast_crypto_updown_position(position)


def _soonest_first_score(row: dict[str, Any]) -> float:
    time_to_close = _time_to_close_hours(row)
    if time_to_close is None:
        return -999999.0
    return -max(0.0, time_to_close)


def _is_past_close(row: dict[str, Any]) -> bool:
    time_to_close = _time_to_close_hours(row)
    return time_to_close is not None and time_to_close < 0.0


def _entry_price_in_band(row: dict[str, Any], minimum_entry_price: float, maximum_entry_price: float) -> bool:
    entry_price = safe_float(row.get("executable_price"))
    return entry_price is not None and minimum_entry_price <= entry_price <= maximum_entry_price


def _normalise_position_row(position: dict[str, Any]) -> None:
    """Backfill newly introduced explicit columns from the embedded signal payload."""
    payload = _row_payload(position)
    for key in ("outcome", "close_time", "rule_scope", "time_to_close_hours"):
        if not str(position.get(key) or "").strip() and payload.get(key) not in (None, ""):
            position[key] = payload.get(key)
    # 2026-07-10: non-crypto positions were born without close_time (the Gate A
    # pipe bug) - accept any end-date alias the signal payload carries.
    if not str(position.get("close_time") or "").strip():
        for alias in ("end_time", "endDate", "endDateIso", "end_date", "end_date_iso", "closedTime", "closed_time"):
            value = position.get(alias) or payload.get(alias)
            if value not in (None, ""):
                position["close_time"] = value
                break
    if not str(position.get("close_time") or "").strip():
        inferred_close = _crypto_updown_slug_close_time(position.get("market_slug"))
        if inferred_close:
            position["close_time"] = inferred_close
    if _is_fast_crypto_updown_position(position):
        position.setdefault("settlement_policy", "final_settlement_only")


def _age_hours(opened_at: Any) -> float:
    opened = parse_timestamp(opened_at)
    if opened is None:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _minutes_since(timestamp: Any) -> float:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 60.0)


def _prediction_index(predictions: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in predictions:
        key = (str(row.get("market_id") or ""), str(row.get("token_id") or ""))
        if key[0] or key[1]:
            index[key] = row
    return index


def _near_miss_shadow_row(row: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    if not boolish(settings.get("allow_near_miss_learning_candidates", False)):
        return None
    if not boolish(row.get("near_miss_learning_candidate")):
        return None
    cohort_prefix = str(settings.get("near_miss_cohort_prefix", "near_miss_learning") or "near_miss_learning")
    base_cohort = _cohort_name(row)
    shadow_row = dict(row)
    shadow_row["shadow_trade_candidate"] = True
    shadow_row["shadow_candidate_reason"] = "near_miss_shadow_evidence"
    shadow_row["shadow_source"] = "near_miss_learning"
    shadow_row["signal_cohort"] = f"{cohort_prefix}|{base_cohort}"
    if not str(shadow_row.get("shadow_priority_score") or "").strip():
        shadow_row["shadow_priority_score"] = row.get("near_miss_priority_score", "")
    return shadow_row


def _alpha_candidate_learning_shadow_row(row: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a governed alpha candidate into shadow-only forward evidence.

    This does not approve a paper trade. It only lets candidates that already
    passed the alpha validation/microstructure checks enter the same simulated
    bid/ask P&L loop used for promotion evidence. Paper/live gates remain
    downstream and unchanged.
    """
    if not boolish(settings.get("allow_alpha_candidate_learning_candidates", False)):
        return None
    if not boolish(row.get("alpha_trade_candidate")):
        return None
    if not boolish(row.get("validation_layer_pass")):
        return None
    if not boolish(row.get("microstructure_filter_pass")):
        return None
    if not boolish(row.get("bookmaker_cross_check_pass", True)):
        return None
    shadow_row = dict(row)
    shadow_row["shadow_trade_candidate"] = True
    shadow_row["shadow_candidate_reason"] = "alpha_candidate_shadow_evidence"
    shadow_row["shadow_source"] = "alpha_candidate_learning"
    if not str(shadow_row.get("shadow_priority_score") or "").strip():
        shadow_row["shadow_priority_score"] = row.get("edge_lower_bound") or row.get("alpha_score") or ""
    return shadow_row


def _shadow_candidate_row(row: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    if boolish(row.get("shadow_trade_candidate")):
        candidate = dict(row)
        candidate.setdefault("shadow_source", "shadow_trade_candidate")
        return candidate
    near_miss = _near_miss_shadow_row(row, settings)
    if near_miss is not None:
        return near_miss
    return _alpha_candidate_learning_shadow_row(row, settings)


def _quarantined_cohorts(cfg: EngineConfig, positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    settings = _settings(cfg)
    if not boolish(settings.get("quarantine_negative_cohorts", True)):
        return {}
    min_closed = int(settings.get("quarantine_min_closed_positions", 3))
    max_roi = float(settings.get("quarantine_max_closed_roi", -0.05))
    max_pnl = float(settings.get("quarantine_max_closed_pnl_usdc", -1.0))
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "closed_positions": 0,
            "closed_cost_basis_usdc": 0.0,
            "closed_realised_pnl_usdc": 0.0,
        }
    )
    for position in positions:
        if str(position.get("status") or "").lower() != "closed":
            continue
        cohort = _cohort_name(position)
        stats[cohort]["closed_positions"] += 1
        stats[cohort]["closed_cost_basis_usdc"] += safe_float(position.get("cost_basis_usdc")) or 0.0
        stats[cohort]["closed_realised_pnl_usdc"] += safe_float(position.get("realised_pnl_usdc")) or 0.0

    quarantined: dict[str, dict[str, Any]] = {}
    for cohort, row in stats.items():
        cost = float(row["closed_cost_basis_usdc"])
        pnl = float(row["closed_realised_pnl_usdc"])
        roi = pnl / cost if cost > 0 else 0.0
        if int(row["closed_positions"]) >= min_closed and (roi <= max_roi or pnl <= max_pnl):
            quarantined[cohort] = {
                **row,
                "closed_roi": roi,
                "quarantine_reason": (
                    f"closed evidence below threshold: roi={roi:.4f}, pnl={pnl:.2f}, "
                    f"closed_positions={int(row['closed_positions'])}"
                ),
            }
    return quarantined


def _hours_since_first_evidence(first_timestamp: Any) -> float:
    parsed = parse_timestamp(first_timestamp)
    if parsed is None:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _monthly_run_rate_usdc(pnl: float, first_timestamp: Any) -> float:
    elapsed_hours = _hours_since_first_evidence(first_timestamp)
    if elapsed_hours <= 0:
        return 0.0
    return pnl * (24.0 * 30.0 / elapsed_hours)


def _position_close_time(position: dict[str, Any]) -> datetime | None:
    payload = _row_payload(position)
    for key in ("close_time", "end_time", "endDate", "endDateIso"):
        parsed = parse_timestamp(position.get(key) or payload.get(key))
        if parsed is not None:
            return parsed.astimezone(timezone.utc)
    return None


def _should_check_settlement(position: dict[str, Any], *, grace_minutes: float) -> bool:
    close_time = _position_close_time(position)
    if close_time is None:
        return False
    now_dt = datetime.now(timezone.utc)
    return (now_dt - close_time).total_seconds() >= grace_minutes * 60.0


def _public_search_market_by_slug(slug: str, *, timeout_seconds: int) -> dict[str, Any] | None:
    queries = [slug, slug.replace("-", " "), *_public_search_queries_for_slug(slug)]
    seen: set[str] = set()
    for query in queries:
        clean_query = query.strip()
        if not clean_query or clean_query.lower() in seen:
            continue
        seen.add(clean_query.lower())
        params = urllib.parse.urlencode(
            {
                "q": clean_query,
                "limit": "8",
                "search_profiles": "false",
                "search_tags": "false",
            }
        )
        request = urllib.request.Request(
            f"https://gamma-api.polymarket.com/public-search?{params}",
            headers={
                "User-Agent": "superbru-polymarket-mispricing-bot/0.1",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # nosec B310 - public API
            payload = json.loads(response.read().decode("utf-8"))
        events = payload.get("events") if isinstance(payload, dict) else []
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict):
                continue
            markets = event.get("markets") or []
            if isinstance(markets, list):
                for market in markets:
                    if isinstance(market, dict) and str(market.get("slug") or "") == slug:
                        return market
            if str(event.get("slug") or "") == slug:
                if isinstance(markets, list) and markets and isinstance(markets[0], dict):
                    return markets[0]
                return event
    return None


def _public_search_queries_for_slug(slug: str) -> list[str]:
    match = re.match(r"^(btc|eth|sol|xrp)-updown-(5m|15m)-(\d+)$", slug)
    if not match:
        return []
    asset_key, interval_raw, timestamp_raw = match.groups()
    asset_name = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "xrp": "xrp",
    }.get(asset_key, asset_key)
    try:
        start_utc = datetime.fromtimestamp(int(timestamp_raw), tz=timezone.utc)
        start_et = start_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        return [f"{asset_name} up or down"]
    date_text = f"{start_et.strftime('%B')} {start_et.day} {start_et.year}"
    interval_minutes = 15 if interval_raw == "15m" else 5
    end_et = start_et + timedelta(minutes=interval_minutes)

    def _time_text(dt: datetime) -> str:
        return f"{int(dt.strftime('%I'))}:{dt.strftime('%M')}{dt.strftime('%p')}"

    short_date_text = f"{start_et.strftime('%B')} {start_et.day}"
    return [
        slug,
        slug.replace("-", " "),
        f"{asset_name} up or down {short_date_text} {_time_text(end_et)} ET",
        f"{asset_name} up or down {short_date_text} {_time_text(start_et)}-{_time_text(end_et)} ET",
        f"{asset_name} up or down {date_text}",
        f"{asset_name} updown {date_text}",
    ]


def _fetch_resolution_market(slug: str, *, timeout_seconds: int) -> dict[str, Any] | None:
    market = fetch_gamma_market(slug, timeout_seconds=timeout_seconds)
    if market:
        return market
    return _public_search_market_by_slug(slug, timeout_seconds=timeout_seconds)


def _settlement_price_for_position(
    cfg: EngineConfig,
    position: dict[str, Any],
    *,
    timeout_seconds: int,
) -> tuple[float | None, str]:
    slug = str(position.get("market_slug") or "").strip()
    token_id = str(position.get("token_id") or "").strip()
    if not slug or not token_id:
        return None, "missing_market_slug_or_token"
    proxy_price, proxy_reason = _crypto_updown_proxy_settlement_price(position, timeout_seconds=timeout_seconds)
    if proxy_price is not None:
        return proxy_price, proxy_reason
    if proxy_reason != "not_crypto_updown_slug":
        return None, proxy_reason
    market = _fetch_resolution_market(slug, timeout_seconds=timeout_seconds)
    if not market:
        return None, "gamma_market_not_found"
    rows, quality = infer_market_resolution_rows(market, category_hint=str(position.get("category") or ""))
    quality_label = str(quality[0].get("resolution_quality") if quality else "")
    if quality_label != "clean_settlement":
        return None, quality_label or "unclean_settlement"
    for row in rows:
        if str(row.get("token_id") or "") == token_id:
            target = safe_float(row.get("target"))
            if target in {0.0, 1.0}:
                return float(target), "clean_settlement"
            return None, "missing_token_target"
    return None, "token_not_in_settlement"


def _settlement_checkpoints_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "shadow_settlement_checkpoints.json"


def _read_settlement_checkpoints(cfg: EngineConfig) -> dict[str, str]:
    """Per-position last-settlement-check times (WO-143b.1 F1 anti-starvation).

    Held in a governance SIDECAR rather than as a `last_settlement_check_utc`
    column on ``shadow_positions.csv``. The register permits "a per-position
    ``last_settlement_check_utc`` with oldest-first ordering (or an equivalent
    rotating offset)"; this is that equivalent. A new ledger column would
    change ``shadow_positions.csv``'s bytes on every settling pass and so
    break the byte-identity regression this same amendment requires (tests 1
    and 11), and it would widen an enrolled ledger's schema for pure
    scheduling bookkeeping. The sidecar carries no evidence, only ordering
    state, and can be deleted without loss -- a missing file just means
    "nothing checked yet", which fails safe to today's file order.
    """
    payload = read_json(_settlement_checkpoints_path(cfg), default={}) or {}
    if not isinstance(payload, dict):
        return {}
    checkpoints = payload.get("last_settlement_check_utc")
    if not isinstance(checkpoints, dict):
        return {}
    return {str(key): str(value) for key, value in checkpoints.items() if value}


def _settlement_check_sort_key(position: dict[str, Any], checkpoints: dict[str, str]) -> tuple[int, str, str]:
    """Oldest-checked-first ordering for settlement (WO-143b.1 F1).

    Never-checked positions sort first, then by ascending last-checked time.
    Without this, `_settle_due_positions` iterated in file order every pass, so
    on a degraded day a budget that reaches only the first N due positions
    would never reach the tail at all -- those positions age past
    ``maximum_holding_hours`` and close as ``shadow_time_exit`` at a stale mark
    instead of ``shadow_clean_settlement`` at the true 0/1 outcome. That is a
    systematic distortion of shadow P&L, not merely deferred work.
    """
    position_id = str(position.get("shadow_position_id") or "")
    checked_at = checkpoints.get(position_id, "")
    return (1, checked_at, position_id) if checked_at else (0, "", position_id)


def _settle_due_positions(
    cfg: EngineConfig,
    positions: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    *,
    timestamp: str,
    budget_seconds: float | None = None,
    heartbeat: Any = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    if not boolish(settings.get("settle_resolved_markets", True)):
        return {
            "settlement_enabled": False,
            "settlement_checks": 0,
            "settled_positions": 0,
            "settlement_errors": 0,
            "settlement_positions_abandoned": 0,
            "settlement_abandoned_ids": [],
            "settlement_budget_expired": False,
        }
    max_checks = int(settings.get("settlement_max_positions_per_cycle", 25))
    grace_minutes = float(settings.get("settlement_grace_minutes", 1.0))
    timeout_seconds = int(settings.get("settlement_request_timeout_seconds", 20))
    if budget_seconds is None:
        budget_seconds = float(
            settings.get("settlement_budget_seconds", _DEFAULT_SETTLEMENT_BUDGET_SECONDS)
        )
    started_monotonic = time.monotonic()
    checks = 0
    settled = 0
    unresolved = 0
    errors = 0
    abandoned = 0
    budget_expired = False
    # STAGED settlements, applied only if the pass completes (Codex P1 wave-35
    # on #416). The registered contract for this budget is "abandons remaining
    # positions with a partial, fail-closed status and NO partial write", and
    # mutating the position plus appending its SELL_SHADOW fill inline IS the
    # partial write: a later lookup pushes the pass over budget and the caller
    # publishes both ledgers carrying settlements from a pass recorded as
    # incomplete. The existing test missed it because every stubbed lookup
    # returned unresolved, so nothing was ever staged to leak.
    staged: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    # Checkpoints are staged alongside, because a DISCARDED settlement whose
    # checkpoint had been persisted would sort LAST next pass -- permanently
    # starving the position it just resolved, the exact failure F1's
    # oldest-first rotation exists to prevent.
    checked_now: dict[str, str] = {}
    reasons: dict[str, int] = defaultdict(int)
    # WO-143b.1 F1: oldest-checked-first, so a budget that cannot reach every
    # due position rotates through them across passes instead of starving the
    # tail forever.
    checkpoints = _read_settlement_checkpoints(cfg)
    due = [
        position
        for position in sorted(positions, key=lambda row: _settlement_check_sort_key(row, checkpoints))
        if str(position.get("status") or "").lower() == "open"
        and _should_check_settlement(position, grace_minutes=grace_minutes)
    ]
    abandoned_ids: list[str] = []
    for index, position in enumerate(due):
        # THE BUDGET IS CHECKED FIRST, before the position cap (Codex P1
        # wave-36). With more due positions than max_checks, a final permitted
        # lookup that pushed elapsed time past the budget was met on the next
        # iteration by the CAP branch instead: budget_expired stayed false, the
        # staged settlements were published, and the pass reported "complete"
        # while leaving positions unprocessed after exceeding its own
        # fail-closed budget. The cap ordering bypassed the staging rollback
        # entirely -- the rollback was correct and simply never ran.
        #
        # It cannot bound a single unbounded urlopen read -- that is precisely
        # why the progress-derived heartbeat, not this budget, is the primary
        # defence -- but it does stop a pass of individually-completing-but-slow
        # lookups from running past the stale window.
        if budget_seconds > 0 and (time.monotonic() - started_monotonic) >= budget_seconds:
            budget_expired = True
            abandoned = len(due) - index
            abandoned_ids = [
                str(row.get("shadow_position_id") or "") for row in due[index:]
            ]
            break
        if checks >= max_checks:
            abandoned = len(due) - index
            abandoned_ids = [
                str(row.get("shadow_position_id") or "") for row in due[index:]
            ]
            break
        if str(position.get("status") or "").lower() != "open":
            continue
        checks += 1
        checked_now[str(position.get("shadow_position_id") or "")] = timestamp
        if heartbeat is not None:
            heartbeat.note_progress("settlement_position")
        try:
            settlement_price, reason = _settlement_price_for_position(
                cfg,
                position,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - one failed resolution lookup must not block the loop
            errors += 1
            reasons[f"settlement_error:{type(exc).__name__}"] += 1
            continue
        reasons[reason] += 1
        if settlement_price is None:
            unresolved += 1
            continue
        quantity = safe_float(position.get("quantity")) or 0.0
        cost = safe_float(position.get("cost_basis_usdc")) or 0.0
        proceeds = quantity * settlement_price
        pnl = proceeds - cost
        staged.append(
            (
                position,
                {
                    "latest_mark_price": settlement_price,
                    "status": "closed",
                    "closed_at": timestamp,
                    "updated_at": timestamp,
                    "close_reason": "shadow_clean_settlement",
                    "exit_price": settlement_price,
                    "realised_pnl_usdc": pnl,
                    "unrealised_pnl_usdc": 0.0,
                    "return_pct": pnl / cost if cost > 0 else 0.0,
                },
                {
                    "position_id": str(position.get("shadow_position_id")),
                    "side": "SELL_SHADOW",
                    "timestamp": timestamp,
                    "price": settlement_price,
                    "quantity": quantity,
                    "notional": proceeds,
                    "reason": "shadow_clean_settlement",
                },
            )
        )
    staged_ids = {str(row.get("shadow_position_id") or "") for row, _, _ in staged}
    discarded = 0
    if budget_expired:
        discarded = len(staged)
        # A position whose settlement was RESOLVED and then discarded is
        # abandoned for this pass too: its true exit price is the settlement,
        # and it must not be closed on a stale mark before the next pass can
        # write it.
        abandoned_ids.extend(sorted(staged_ids))
        # The COUNT must agree with the LIST (Codex P2 wave-38). Extending the
        # ids without recomputing the count let the artifact report, say, two
        # abandoned positions while carrying five abandoned ids -- and the
        # registered day-after check reads the COUNT, so it understated the
        # deferred workload by exactly the settlements this pass resolved and
        # then correctly refused to write.
        abandoned = len(abandoned_ids)
        staged = []
        # EVERY position checked this pass advances in the rotation, including
        # the discarded ones (Codex P1 wave-40). Holding their checkpoints back
        # was meant to keep them first in line, but it created a NEW starvation:
        # if the same lookup repeatedly runs past the budget, it is repeatedly
        # resolved and discarded while later due positions are never attempted
        # at all -- exactly the permanent starvation F1's oldest-first rotation
        # exists to prevent, reintroduced by the rollback that fixed the partial
        # write.
        #
        # The wave-35 worry was wrong. Advancing the checkpoint does not starve
        # the discarded position; it defers it by ONE rotation, which is what
        # rotation means. Settlement is idempotent, the position stays open, and
        # it is re-resolved when its turn comes round -- while everyone else
        # gets a turn in the meantime. A checkpoint is a SCHEDULING record, not
        # a publication record.
        checkpoints.update(checked_now)
    else:
        for position, updates, fill_kwargs in staged:
            position.update(updates)
            _append_fill(fills, row=position, **fill_kwargs)
            settled += 1
        checkpoints.update(checked_now)
    # Persist the rotation state even when the budget expired -- especially
    # then, since that is exactly the pass whose unreached tail must be first
    # in line next time.
    if checks:
        write_json(
            _settlement_checkpoints_path(cfg),
            {"generated_at_utc": timestamp, "last_settlement_check_utc": checkpoints},
        )
    return {
        "settlement_enabled": True,
        "settlement_checks": checks,
        "settled_positions": settled,
        "unresolved_positions_checked": unresolved,
        "settlement_errors": errors,
        "settlement_reason_counts": dict(reasons),
        # WO-143b.1 F1: a budget-expired pass is a PARTIAL pass. It is recorded
        # as such rather than looking like a complete one that simply found
        # less to do, and the abandoned count is named in the day-after check.
        "settlement_positions_abandoned": abandoned,
        # NAMED, not just counted (Codex P1 wave-35 on #416). The oldest-first
        # rotation only helps across PASSES; within this one an abandoned
        # position already past maximum_holding_hours was closed as
        # shadow_time_exit at a stale mark immediately below, before the
        # rotation could ever prioritise it -- recreating precisely the P&L
        # distortion _settlement_check_sort_key's docstring says it exists to
        # prevent. The rotation test hid it by setting the holding limit to ten
        # million hours.
        "settlement_abandoned_ids": abandoned_ids,
        # Settlements this pass RESOLVED and then threw away on expiry. Named
        # so a reader can tell a pass that found nothing from one that found
        # something and correctly refused to write it.
        "settlement_resolved_discarded_on_expiry": discarded,
        "settlement_budget_expired": budget_expired,
        "settlement_status": "partial_budget_expired" if budget_expired else "complete",
    }


def _candidate_rows(cfg: EngineConfig, predictions: list[dict[str, Any]], positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    settings = _settings(cfg)
    if not boolish(settings.get("enabled", True)):
        return []
    minimum_entry_price, maximum_entry_price = _entry_price_band(cfg)
    quarantined = _quarantined_cohorts(cfg, positions)
    open_keys = {
        (str(row.get("market_id") or ""), str(row.get("token_id") or ""))
        for row in positions
        if str(row.get("status") or "").lower() == "open"
    }
    cooldown = float(settings.get("minimum_reentry_minutes_after_exit", 240.0))
    last_close_by_key: dict[tuple[str, str], str] = {}
    for row in positions:
        if str(row.get("status") or "").lower() != "closed":
            continue
        key = (str(row.get("market_id") or ""), str(row.get("token_id") or ""))
        closed_at = str(row.get("closed_at") or "")
        if closed_at:
            last_close_by_key[key] = max(last_close_by_key.get(key, ""), closed_at)

    candidates: list[dict[str, Any]] = []
    for source_row in predictions:
        row = _shadow_candidate_row(source_row, settings)
        if row is None:
            continue
        if _is_past_close(row):
            continue
        if _cohort_name(row) in quarantined:
            continue
        key = (str(row.get("market_id") or ""), str(row.get("token_id") or ""))
        if key in open_keys:
            continue
        if cooldown > 0 and _minutes_since(last_close_by_key.get(key, "")) < cooldown:
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            1 if _entry_price_in_band(row, minimum_entry_price, maximum_entry_price) else 0,
            1 if _is_fast_feedback(row, settings) else 0,
            _soonest_first_score(row),
            safe_float(row.get("shadow_priority_score")) or 0.0,
            safe_float(row.get("fundamental_edge_after_haircut")) or 0.0,
            safe_float(row.get("edge_lower_bound")) or 0.0,
        ),
        reverse=True,
    )
    candidate_limit = int(settings.get("candidate_limit_per_cycle", 8))
    near_miss_limit = int(settings.get("near_miss_candidate_limit_per_cycle", candidate_limit) or candidate_limit)
    alpha_learning_limit = int(
        settings.get("alpha_candidate_learning_candidate_limit_per_cycle", candidate_limit) or candidate_limit
    )
    limited: list[dict[str, Any]] = []
    near_miss_count = 0
    alpha_learning_count = 0
    for row in candidates:
        if str(row.get("shadow_source") or "") == "near_miss_learning":
            if near_miss_count >= near_miss_limit:
                continue
            near_miss_count += 1
        if str(row.get("shadow_source") or "") == "alpha_candidate_learning":
            if alpha_learning_count >= alpha_learning_limit:
                continue
            alpha_learning_count += 1
        limited.append(row)
        if len(limited) >= candidate_limit:
            break
    return limited


def _append_fill(
    fills: list[dict[str, Any]],
    *,
    position_id: str,
    side: str,
    timestamp: str,
    price: float,
    quantity: float,
    notional: float,
    row: dict[str, Any],
    reason: str,
) -> None:
    fills.append(
        {
            "shadow_fill_id": _stable_id("shadow_fill", f"{position_id}|{side}|{timestamp}|{reason}"),
            "shadow_position_id": position_id,
            "created_at": timestamp,
            "side": side,
            "market_id": row.get("market_id", ""),
            "token_id": row.get("token_id", ""),
            "market_slug": row.get("market_slug", ""),
            "question": row.get("question", ""),
            "category": row.get("category", ""),
            "correlation_key": row.get("correlation_key") or normalised_correlation_key(row),
            "signal_cohort": row.get("signal_cohort") or signal_cohort(row),
            "shadow_source": row.get("shadow_source", ""),
            "price": price,
            "quantity": quantity,
            "gross_notional_usdc": notional,
            "reason": reason,
        }
    )


def _summarise_shadow(cfg: EngineConfig, positions: list[dict[str, Any]], fills: list[dict[str, Any]]) -> dict[str, Any]:
    settings = _settings(cfg)
    minimum_fills = int((cfg.raw.get("cohort_promotion", {}) or {}).get("minimum_filled_orders", 5))
    minimum_settled = int((cfg.raw.get("cohort_promotion", {}) or {}).get("minimum_settled_orders", 1))
    minimum_pnl = float((cfg.raw.get("cohort_promotion", {}) or {}).get("minimum_pnl_usdc", 0.0))
    minimum_roi = float((cfg.raw.get("cohort_promotion", {}) or {}).get("minimum_roi", 0.0))
    minimum_monthly_run_rate = float(
        (cfg.raw.get("cohort_promotion", {}) or {}).get("minimum_monthly_run_rate_usdc", 0.0)
    )
    minimum_tracking_hours = float(
        (cfg.raw.get("cohort_promotion", {}) or {}).get("minimum_tracking_hours_for_promotion", 0.0)
    )
    quarantined = _quarantined_cohorts(cfg, positions)
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "shadow_fills": 0,
            "shadow_sell_fills": 0,
            "shadow_open_positions": 0,
            "shadow_total_buy_cost_usdc": 0.0,
            "shadow_sell_proceeds_usdc": 0.0,
            "shadow_open_mark_value_usdc": 0.0,
            "shadow_open_cost_basis_usdc": 0.0,
            "first_evidence_at_utc": "",
            "last_evidence_at_utc": "",
        }
    )
    for fill in fills:
        cohort = str(fill.get("signal_cohort") or "unknown")
        notional = safe_float(fill.get("gross_notional_usdc")) or 0.0
        created_at = str(fill.get("created_at") or "")
        if created_at:
            current_first = str(stats[cohort].get("first_evidence_at_utc") or "")
            current_last = str(stats[cohort].get("last_evidence_at_utc") or "")
            if not current_first or created_at < current_first:
                stats[cohort]["first_evidence_at_utc"] = created_at
            if not current_last or created_at > current_last:
                stats[cohort]["last_evidence_at_utc"] = created_at
        if str(fill.get("side")) == "BUY_SHADOW":
            stats[cohort]["shadow_fills"] += 1
            stats[cohort]["shadow_total_buy_cost_usdc"] += notional
        elif str(fill.get("side")) == "SELL_SHADOW":
            stats[cohort]["shadow_sell_fills"] += 1
            stats[cohort]["shadow_sell_proceeds_usdc"] += notional
    for position in positions:
        if str(position.get("status") or "").lower() != "open":
            continue
        cohort = str(position.get("signal_cohort") or "unknown")
        quantity = safe_float(position.get("quantity")) or 0.0
        mark = safe_float(position.get("latest_mark_price")) or safe_float(position.get("entry_price")) or 0.0
        cost = safe_float(position.get("cost_basis_usdc")) or 0.0
        stats[cohort]["shadow_open_positions"] += 1
        stats[cohort]["shadow_open_mark_value_usdc"] += quantity * mark
        stats[cohort]["shadow_open_cost_basis_usdc"] += cost

    cohorts: list[dict[str, Any]] = []
    for cohort, row in sorted(stats.items()):
        pnl = (
            float(row["shadow_sell_proceeds_usdc"])
            + float(row["shadow_open_mark_value_usdc"])
            - float(row["shadow_total_buy_cost_usdc"])
        )
        at_risk = float(row["shadow_total_buy_cost_usdc"])
        roi = pnl / at_risk if at_risk > 0 else 0.0
        elapsed_hours = _hours_since_first_evidence(row.get("first_evidence_at_utc"))
        monthly_run_rate = _monthly_run_rate_usdc(pnl, row.get("first_evidence_at_utc"))
        readiness = {
            "fills_remaining": max(0, minimum_fills - int(row["shadow_fills"])),
            "settled_fills_remaining": max(0, minimum_settled - int(row["shadow_sell_fills"])),
            "pnl_remaining_usdc": max(0.0, minimum_pnl - pnl),
            "roi_remaining": max(0.0, minimum_roi - roi),
            "tracking_hours_remaining": max(0.0, minimum_tracking_hours - elapsed_hours),
            "monthly_run_rate_remaining_usdc": max(0.0, minimum_monthly_run_rate - monthly_run_rate),
        }
        cohorts.append(
            {
                "signal_cohort": cohort,
                **row,
                "shadow_total_pnl_usdc": pnl,
                "shadow_roi": roi,
                "evidence_elapsed_hours": elapsed_hours,
                "shadow_monthly_run_rate_usdc": monthly_run_rate,
                "promotion_readiness": readiness,
                "promotion_ready_score": sum(1 for value in readiness.values() if value <= 0),
                "promotion_ready_checks": len(readiness),
                "shadow_promoted": bool(
                    row["shadow_fills"] >= minimum_fills
                    and row["shadow_sell_fills"] >= minimum_settled
                    and pnl > minimum_pnl
                    and roi >= minimum_roi
                    and elapsed_hours >= minimum_tracking_hours
                    and monthly_run_rate >= minimum_monthly_run_rate
                ),
            }
        )

    promotion_watchlist = [
        {
            "signal_cohort": row.get("signal_cohort"),
            "shadow_promoted": bool(row.get("shadow_promoted")),
            "promotion_ready_score": int(row.get("promotion_ready_score") or 0),
            "promotion_ready_checks": int(row.get("promotion_ready_checks") or 0),
            "promotion_readiness": row.get("promotion_readiness") or {},
            "shadow_total_pnl_usdc": float(row.get("shadow_total_pnl_usdc") or 0.0),
            "shadow_roi": float(row.get("shadow_roi") or 0.0),
            "shadow_monthly_run_rate_usdc": float(row.get("shadow_monthly_run_rate_usdc") or 0.0),
            "shadow_fills": int(row.get("shadow_fills") or 0),
            "shadow_sell_fills": int(row.get("shadow_sell_fills") or 0),
            "shadow_open_positions": int(row.get("shadow_open_positions") or 0),
        }
        for row in sorted(
            cohorts,
            key=lambda item: (
                int(item.get("promotion_ready_score") or 0),
                float(item.get("shadow_monthly_run_rate_usdc") or 0.0),
                float(item.get("shadow_total_pnl_usdc") or 0.0),
                float(item.get("shadow_roi") or 0.0),
            ),
            reverse=True,
        )
        if not row.get("shadow_promoted")
    ][:10]

    return {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "settings": {
            "policy_version": str(settings.get("policy_version", "shadow-cohort-v1")),
            "stake_usdc": float(settings.get("stake_usdc", 10.0)),
            "candidate_limit_per_cycle": int(settings.get("candidate_limit_per_cycle", 8)),
            "maximum_open_positions": int(settings.get("maximum_open_positions", 25)),
            "maximum_open_positions_per_cohort": int(settings.get("maximum_open_positions_per_cohort", 0) or 0),
            "maximum_long_horizon_open_positions": int(settings.get("maximum_long_horizon_open_positions", 0) or 0),
            "maximum_near_miss_open_positions": int(settings.get("maximum_near_miss_open_positions", 0) or 0),
            "fast_feedback_max_time_to_close_hours": float(settings.get("fast_feedback_max_time_to_close_hours", 6.0)),
            "minimum_fast_feedback_slots": int(settings.get("minimum_fast_feedback_slots", 0) or 0),
            "quarantine_negative_cohorts": boolish(settings.get("quarantine_negative_cohorts", True)),
            "settlement_only_fast_crypto_updown": boolish(settings.get("settlement_only_fast_crypto_updown", True)),
        },
        "cohorts": cohorts,
        "promotion_watchlist": promotion_watchlist,
        "promoted_cohorts": [row["signal_cohort"] for row in cohorts if row.get("shadow_promoted")],
        "quarantined_cohorts": [
            {"signal_cohort": cohort, **details}
            for cohort, details in sorted(quarantined.items())
        ],
    }


def read_shadow_signal_cohort_pnl(cfg: EngineConfig) -> dict[str, Any]:
    summary_file = str(_settings(cfg).get("summary_file", "shadow_signal_cohort_pnl.json"))
    payload = read_json(cfg.governance_root / summary_file, default={}) or {}
    return payload if isinstance(payload, dict) else {}


# --- WO-143b.1 F1: wall-clock budgets, heartbeat and stale window ----------
#
# Registered literals with stated bases (see the config file and §143b.1).
# `remainder_budget_seconds` is ADVISORY: it only sizes `heartbeat_cap_seconds`
# and is never enforced at runtime, unlike `settlement_budget_seconds` which
# abandons remaining work.
_DEFAULT_SETTLEMENT_BUDGET_SECONDS = 900.0
_DEFAULT_REMAINDER_BUDGET_SECONDS = 300.0  # sizing only, never enforced
_DEFAULT_HEARTBEAT_MARGIN_SECONDS = 120.0
_DEFAULT_HEARTBEAT_CAP_SECONDS = 1800.0
_DEFAULT_CRITICAL_SECTION_MAX_SECONDS = 120.0
_DEFAULT_SHADOW_COHORT_STALE_AFTER_SECONDS = 2400.0


class ShadowCohortTimingConfigError(ValueError):
    """Raised when the registered timing ordering is violated."""


def shadow_cohort_timings(cfg: EngineConfig) -> dict[str, float]:
    """Resolve and VALIDATE the F1 timing constants at load time.

    The four registered relations are checked together and a violation raises
    rather than silently inverting. Relation 3 in particular is the one a later
    edit would break unnoticed: the effective maximum hold is
    ``heartbeat_cap + critical_section_max`` because the cap does not fire
    inside the critical section, so a raised ``critical_section_max_seconds``
    re-opens the reclaim-mid-critical-section hole that the section exists to
    close -- while a three-relation check would still pass.
    """
    settings = _settings(cfg)

    def _read(key: str, default: float) -> float:
        raw = settings.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ShadowCohortTimingConfigError(f"{key} is not a number: {raw!r}") from exc
        return value

    timings = {
        "settlement_budget_seconds": _read("settlement_budget_seconds", _DEFAULT_SETTLEMENT_BUDGET_SECONDS),
        "remainder_budget_seconds": _read("remainder_budget_seconds", _DEFAULT_REMAINDER_BUDGET_SECONDS),
        "heartbeat_margin_seconds": _read("heartbeat_margin_seconds", _DEFAULT_HEARTBEAT_MARGIN_SECONDS),
        "heartbeat_cap_seconds": _read("heartbeat_cap_seconds", _DEFAULT_HEARTBEAT_CAP_SECONDS),
        "critical_section_max_seconds": _read(
            "critical_section_max_seconds", _DEFAULT_CRITICAL_SECTION_MAX_SECONDS
        ),
        "shadow_cohort_stale_after_seconds": _read(
            "shadow_cohort_stale_after_seconds", _DEFAULT_SHADOW_COHORT_STALE_AFTER_SECONDS
        ),
    }

    # Relation 4: every constant positive and finite.
    for key, value in timings.items():
        if not math.isfinite(value) or value <= 0:
            raise ShadowCohortTimingConfigError(f"{key} must be positive and finite, got {value!r}")

    phases = (
        timings["settlement_budget_seconds"]
        + timings["remainder_budget_seconds"]
        + timings["heartbeat_margin_seconds"]
    )
    # Relation 1: a legitimately slow pass is never cut off by the cap.
    if not phases < timings["heartbeat_cap_seconds"]:
        raise ShadowCohortTimingConfigError(
            "settlement_budget_seconds + remainder_budget_seconds + heartbeat_margin_seconds "
            f"({phases:g}) must be < heartbeat_cap_seconds ({timings['heartbeat_cap_seconds']:g})"
        )
    # Relation 2: the beat always stops before the lock could be reclaimed.
    if not timings["heartbeat_cap_seconds"] < timings["shadow_cohort_stale_after_seconds"]:
        raise ShadowCohortTimingConfigError(
            f"heartbeat_cap_seconds ({timings['heartbeat_cap_seconds']:g}) must be < "
            f"shadow_cohort_stale_after_seconds ({timings['shadow_cohort_stale_after_seconds']:g})"
        )
    # Relation 3: the effective maximum hold still fits inside the window.
    effective_max_hold = timings["heartbeat_cap_seconds"] + timings["critical_section_max_seconds"]
    if not effective_max_hold < timings["shadow_cohort_stale_after_seconds"]:
        raise ShadowCohortTimingConfigError(
            "heartbeat_cap_seconds + critical_section_max_seconds "
            f"({effective_max_hold:g}) must be < shadow_cohort_stale_after_seconds "
            f"({timings['shadow_cohort_stale_after_seconds']:g})"
        )
    return timings


def update_shadow_cohort_evidence(cfg: EngineConfig, predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Update shadow ledgers, serialized by this function's own runtime lock.

    This function acquires an internal ``shadow_cohort`` runtime lock at
    entry and releases it at exit -- including when the body raises -- so
    any two concurrent callers (for example the full paper cycle, which
    calls this while holding ``prediction_cycle``, and the live loop's
    per-tick shadow maintenance, which does not) are serialized against each
    other instead of racing the append-only ``shadow_fills.csv``.

    ``shadow_cohort`` is a distinct, independent lock from
    ``prediction_cycle``: holding ``prediction_cycle`` neither satisfies nor
    blocks it, so a caller already holding ``prediction_cycle`` can still
    call this function without deadlocking, and callers no longer need to
    hold ``prediction_cycle`` for this function's own serialization.

    When a foreign writer already holds ``shadow_cohort``, this call
    performs NO writes and returns
    ``{"status": "skipped_shadow_lock_held", ...}`` instead of racing it.
    """

    timings = shadow_cohort_timings(cfg)
    with runtime_lock_with_heartbeat(
        cfg,
        "shadow_cohort",
        stale_after_seconds=timings["shadow_cohort_stale_after_seconds"],
        heartbeat_cap_seconds=timings["heartbeat_cap_seconds"],
        critical_section_max_seconds=timings["critical_section_max_seconds"],
    ) as (lock, heartbeat):
        if not lock.acquired:
            return {
                "status": "skipped_shadow_lock_held",
                "generated_at_utc": now_utc(),
                "runtime_lock": lock.as_dict(),
            }
        return _update_shadow_cohort_evidence_locked(
            cfg, predictions, heartbeat=heartbeat, timings=timings
        )


def _update_shadow_cohort_evidence_locked(
    cfg: EngineConfig,
    predictions: list[dict[str, Any]],
    *,
    heartbeat: Any = None,
    timings: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Compute and write shadow ledgers. Caller must hold the ``shadow_cohort`` lock."""

    settings = _settings(cfg)
    if not boolish(settings.get("enabled", True)):
        payload = {"status": "disabled", "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / str(settings.get("summary_file", "shadow_signal_cohort_pnl.json")), payload)
        return payload

    if timings is None:
        timings = shadow_cohort_timings(cfg)

    def _progress(label: str) -> None:
        # WO-143b.1 F1 requirement 1: the progress counter must advance at
        # every PHASE BOUNDARY and every ledger-write step, not only inside
        # settlement. The settlement position counter stops advancing once
        # `_settle_due_positions` returns, which would silence the heartbeat
        # for exactly the remainder phase that performs the ledger writes --
        # and a reclaim there is the lost-update corruption F1 exists to
        # prevent.
        if heartbeat is not None:
            heartbeat.note_progress(label)

    _progress("read_positions")
    positions = read_csv_rows(_positions_path(cfg))
    for position in positions:
        _normalise_position_row(position)
    fills = read_csv_rows(_fills_path(cfg))
    _progress("read_fills")
    # WO-119: everything before this index is already on disk; only rows
    # appended in this cycle may be written, and only by appending.
    preexisting_fill_count = len(fills)
    predictions_by_key = _prediction_index(predictions)
    now = now_utc()
    take_profit = float(settings.get("take_profit_return", 0.25))
    stop_loss = float(settings.get("stop_loss_return", 0.35))
    max_holding_hours = float(settings.get("maximum_holding_hours", 72.0))

    closed_this_cycle = 0
    settlement = _settle_due_positions(
        cfg,
        positions,
        fills,
        timestamp=now,
        budget_seconds=timings["settlement_budget_seconds"],
        heartbeat=heartbeat,
    )
    closed_this_cycle += int(settlement.get("settled_positions") or 0)
    _progress("settlement_complete")
    # Positions this pass could not settle are held OPEN for the next one. They
    # are still marked to market below -- that is reporting -- but no mark-based
    # rule may CLOSE them, because their true exit is the settlement price and
    # closing at a stale mark is the distortion the rotation exists to prevent.
    settlement_deferred = {
        str(identifier) for identifier in (settlement.get("settlement_abandoned_ids") or []) if identifier
    }
    for position in positions:
        if str(position.get("status") or "").lower() != "open":
            continue
        key = (str(position.get("market_id") or ""), str(position.get("token_id") or ""))
        prediction = predictions_by_key.get(key)
        entry = safe_float(position.get("entry_price")) or 0.0
        quantity = safe_float(position.get("quantity")) or 0.0
        cost = safe_float(position.get("cost_basis_usdc")) or (entry * quantity)
        previous_mark = safe_float(position.get("latest_mark_price")) or entry
        mark = _mark_price(prediction, fallback=previous_mark)
        pnl = quantity * mark - cost
        return_pct = pnl / cost if cost > 0 else 0.0
        position["latest_mark_price"] = mark
        position["unrealised_pnl_usdc"] = pnl
        position["return_pct"] = return_pct
        position["updated_at"] = now
        close_reason = ""
        if str(position.get("shadow_position_id") or "") in settlement_deferred:
            # Marked, disclosed, and deliberately not closed this pass.
            position["settlement_policy"] = "deferred_to_next_settlement_pass"
        elif _settlement_only_shadow_position(position, settings):
            position["settlement_policy"] = "final_settlement_only"
        elif return_pct >= take_profit:
            close_reason = "shadow_take_profit"
        elif return_pct <= -abs(stop_loss):
            close_reason = "shadow_stop_loss"
        elif _age_hours(position.get("opened_at")) >= max_holding_hours:
            close_reason = "shadow_time_exit"
        if close_reason:
            proceeds = quantity * mark
            position["status"] = "closed"
            position["closed_at"] = now
            position["close_reason"] = close_reason
            position["exit_price"] = mark
            position["realised_pnl_usdc"] = pnl
            position["unrealised_pnl_usdc"] = 0.0
            _append_fill(
                fills,
                position_id=str(position.get("shadow_position_id")),
                side="SELL_SHADOW",
                timestamp=now,
                price=mark,
                quantity=quantity,
                notional=proceeds,
                row=position,
                reason=close_reason,
            )
            closed_this_cycle += 1

    open_count = sum(1 for row in positions if str(row.get("status") or "").lower() == "open")
    max_open = int(settings.get("maximum_open_positions", 25))
    max_per_cohort = int(settings.get("maximum_open_positions_per_cohort", 0) or 0)
    max_long_horizon = int(settings.get("maximum_long_horizon_open_positions", 0) or 0)
    max_near_miss_open = int(settings.get("maximum_near_miss_open_positions", 0) or 0)
    fast_feedback_slots = int(settings.get("minimum_fast_feedback_slots", 0) or 0)
    cohort_open_counts: dict[str, int] = defaultdict(int)
    long_horizon_open_count = 0
    near_miss_open_count = 0
    alpha_learning_open_count = 0
    for position in positions:
        if str(position.get("status") or "").lower() != "open":
            continue
        cohort_open_counts[_cohort_name(position)] += 1
        if _is_long_horizon(position, settings):
            long_horizon_open_count += 1
        if str(position.get("shadow_source") or "") == "near_miss_learning":
            near_miss_open_count += 1
        if str(position.get("shadow_source") or "") == "alpha_candidate_learning":
            alpha_learning_open_count += 1
    opened_this_cycle = 0
    near_miss_opened_this_cycle = 0
    alpha_learning_opened_this_cycle = 0
    entry_price_band_skipped = 0
    stake = float(settings.get("stake_usdc", 10.0))
    minimum_entry_price, maximum_entry_price = _entry_price_band(cfg)
    for row in _candidate_rows(cfg, predictions, positions):
        if open_count >= max_open:
            break
        cohort = _cohort_name(row)
        is_fast = _is_fast_feedback(row, settings)
        is_long = _is_long_horizon(row, settings)
        is_near_miss = str(row.get("shadow_source") or "") == "near_miss_learning"
        if max_per_cohort > 0 and cohort_open_counts[cohort] >= max_per_cohort:
            continue
        if max_near_miss_open > 0 and is_near_miss and near_miss_open_count >= max_near_miss_open:
            continue
        if max_long_horizon > 0 and is_long and long_horizon_open_count >= max_long_horizon and not is_near_miss:
            continue
        if fast_feedback_slots > 0 and not is_fast and open_count >= max(0, max_open - fast_feedback_slots):
            continue
        entry_price = safe_float(row.get("executable_price"))
        if entry_price is None or not 0 < entry_price < 1:
            continue
        slippage = _shadow_slippage(cfg, row, stake_usdc=stake)
        fill_price = min(0.999999, entry_price + slippage)
        if not minimum_entry_price <= fill_price <= maximum_entry_price:
            entry_price_band_skipped += 1
            continue
        quantity = stake / fill_price if fill_price > 0 else 0.0
        if quantity <= 0:
            continue
        policy_version = str(settings.get("policy_version", "shadow-cohort-v1"))
        key = "|".join(
            [
                str(row.get("market_id") or ""),
                str(row.get("token_id") or ""),
                str(row.get("prediction_timestamp") or ""),
                str(row.get("model_version") or ""),
                policy_version,
            ]
        )
        position_id = _stable_id("shadow_position", key)
        if any(str(position.get("shadow_position_id")) == position_id for position in positions):
            continue
        close_time = row.get("close_time", "") or _crypto_updown_slug_close_time(row.get("market_slug"))
        position = {
            "shadow_position_id": position_id,
            "policy_version": policy_version,
            "shadow_source": row.get("shadow_source", "shadow_trade_candidate"),
            "opened_at": now,
            "updated_at": now,
            "closed_at": "",
            "status": "open",
            "market_id": row.get("market_id", ""),
            "token_id": row.get("token_id", ""),
            "market_slug": row.get("market_slug", ""),
            "question": row.get("question", ""),
            "category": row.get("category", ""),
            "outcome": row.get("outcome", ""),
            "close_time": close_time,
            "settlement_policy": "final_settlement_only" if _is_fast_crypto_updown_position(row) else "mark_exit_allowed",
            "rule_scope": row.get("rule_scope", ""),
            "correlation_key": normalised_correlation_key(row),
            "signal_cohort": cohort,
            "entry_price": fill_price,
            "quantity": quantity,
            "stake_usdc": stake,
            "cost_basis_usdc": stake,
            "latest_mark_price": _mark_price(row, fallback=fill_price),
            "unrealised_pnl_usdc": 0.0,
            "realised_pnl_usdc": 0.0,
            "return_pct": 0.0,
            "exit_price": "",
            "close_reason": "",
            "entry_edge_lower_bound": row.get("edge_lower_bound", ""),
            "entry_fundamental_edge_after_haircut": row.get("fundamental_edge_after_haircut", ""),
            "entry_shadow_priority_score": row.get("shadow_priority_score", ""),
            "source_signal_json": _row_json(row),
        }
        position["unrealised_pnl_usdc"] = quantity * float(position["latest_mark_price"]) - stake
        position["return_pct"] = float(position["unrealised_pnl_usdc"]) / stake if stake > 0 else 0.0
        positions.append(position)
        _append_fill(
            fills,
            position_id=position_id,
            side="BUY_SHADOW",
            timestamp=now,
            price=fill_price,
            quantity=quantity,
            notional=stake,
            row=position,
            reason="shadow_entry",
        )
        open_count += 1
        cohort_open_counts[cohort] += 1
        if is_long:
            long_horizon_open_count += 1
        if is_near_miss:
            near_miss_open_count += 1
        if str(row.get("shadow_source") or "") == "alpha_candidate_learning":
            alpha_learning_open_count += 1
        opened_this_cycle += 1
        if is_near_miss:
            near_miss_opened_this_cycle += 1
        if str(row.get("shadow_source") or "") == "alpha_candidate_learning":
            alpha_learning_opened_this_cycle += 1

    summary = _summarise_shadow(cfg, positions, fills)
    summary.update(
        {
            "opened_this_cycle": opened_this_cycle,
            "near_miss_opened_this_cycle": near_miss_opened_this_cycle,
            "alpha_candidate_learning_opened_this_cycle": alpha_learning_opened_this_cycle,
            "entry_price_band_skipped": entry_price_band_skipped,
            "entry_price_band": {
                "minimum_entry_price": minimum_entry_price,
                "maximum_entry_price": maximum_entry_price,
            },
            "closed_this_cycle": closed_this_cycle,
            **settlement,
            "open_positions": sum(1 for row in positions if str(row.get("status") or "").lower() == "open"),
            "shadow_candidates_seen": sum(1 for row in predictions if boolish(row.get("shadow_trade_candidate"))),
            "near_miss_candidates_seen": sum(1 for row in predictions if boolish(row.get("near_miss_learning_candidate"))),
            "alpha_candidate_learning_candidates_seen": sum(
                1
                for row in predictions
                if boolish(row.get("alpha_trade_candidate"))
                and boolish(row.get("validation_layer_pass"))
                and boolish(row.get("microstructure_filter_pass"))
                and boolish(row.get("bookmaker_cross_check_pass", True))
            ),
            "near_miss_open_positions": sum(
                1
                for row in positions
                if str(row.get("status") or "").lower() == "open"
                and str(row.get("shadow_source") or "") == "near_miss_learning"
            ),
            "alpha_candidate_learning_open_positions": sum(
                1
                for row in positions
                if str(row.get("status") or "").lower() == "open"
                and str(row.get("shadow_source") or "") == "alpha_candidate_learning"
            ),
        }
    )
    # --- WO-143b.1 F1: bounded, SHRUNK ledger-write critical section --------
    #
    # The content of both ledgers is rendered into sibling temp files OUTSIDE
    # the critical section, reusing the same tested writers (so the resulting
    # bytes are identical to a direct write). The section itself then contains
    # only the atomic `os.replace` publishes, which makes it
    # near-instantaneous by construction -- dissolving the "hung write_csv
    # wedges the lane forever" problem rather than capping it. `./outputs` is
    # a shared bind mount, so an unconditional never-abandon carve-out would
    # have moved the wedge from a hung urlopen to a hung write.
    _progress("render_ledgers")
    positions_path = _positions_path(cfg)
    fills_path = _fills_path(cfg)
    staged_positions = positions_path.with_name(
        f".{positions_path.name}.{os.getpid()}.{time.time_ns()}.staged"
    )
    staged_fills = fills_path.with_name(f".{fills_path.name}.{os.getpid()}.{time.time_ns()}.staged")
    new_fill_rows = fills[preexisting_fill_count:]
    try:
        write_csv(staged_positions, positions)
        # WO-119: shadow_positions.csv is snapshot-enrolled (rewrite is fine);
        # shadow_fills.csv is append_only-enrolled, and a full rewrite here
        # re-serialises history whenever the fill schema widens - the exact
        # WO-115 chain-break class. Copy the existing bytes VERBATIM and append
        # under the header already on disk, so every previously anchored byte
        # survives the staged rewrite unchanged. With nothing to append the
        # ledger is not staged or republished at all: the file keeps its exact
        # existing bytes and inode, which is strictly safer for an append_only
        # ledger than a no-op rewrite.
        if new_fill_rows:
            if fills_path.exists():
                shutil.copyfile(fills_path, staged_fills)
            fill_append = append_csv_rows_matching_existing_header(
                staged_fills,
                new_fill_rows,
                fieldnames=SHADOW_FILL_FIELDS,
            )
        else:
            fill_append = None
        _progress("ledgers_staged")
        critical_section = (
            heartbeat.critical_section() if heartbeat is not None else contextlib.nullcontext()
        )
        # NOTHING BUT THE REPLACES INSIDE (Codex P1 wave-38). `_progress` reaches
        # `heartbeat.note_progress`, which creates a temp file, writes it,
        # FSYNCS it and replaces the lock payload. Beating between the two
        # ledger replaces put all of that I/O inside the one section that is
        # supposed to be two atomic renames: a slow or stuck filesystem there
        # leaves the positions snapshot published WITHOUT its matching
        # append-only fill -- the torn pair this lock exists to prevent -- and
        # makes the "replace-only" section unbounded, which is exactly what its
        # own critical_section_max_seconds bound assumes it is not.
        #
        # The beats move to either side. Progress is still recorded for both
        # phases; it is simply not recorded from inside the window where the
        # ledgers are momentarily inconsistent with each other.
        with critical_section:
            os.replace(staged_positions, positions_path)
            if new_fill_rows:
                os.replace(staged_fills, fills_path)
        _progress("positions_published")
        if new_fill_rows:
            _progress("fills_published")
    finally:
        for staged in (staged_positions, staged_fills):
            try:
                os.unlink(staged)
            except FileNotFoundError:
                pass
    if heartbeat is not None and heartbeat.critical_section_overran:
        # A bounded wedge, recorded loudly rather than looking like a normal
        # long hold.
        summary["shadow_ledger_critical_section_overran"] = True
    if fill_append is not None and fill_append.dropped_fields:
        # WO-128.3: the legacy-header tolerance (WO-119) is kept, but a field
        # it could not persist is recorded in the reported result rather than
        # vanishing - the visible trace that a versioned ledger path is due.
        summary["shadow_fill_fields_dropped_by_legacy_header"] = list(fill_append.dropped_fields)
    if heartbeat is not None:
        summary["shadow_lock_heartbeat"] = heartbeat.as_dict()
    # THE HEARTBEAT CONTINUES THROUGH THE TRAILING WRITES (Codex P2 wave-36).
    # `fills_published` was the last progress notification in the function, but
    # three writes follow it. Individually progressing yet cumulatively past the
    # stale window, they let the lock age out while this writer was still
    # advancing -- a contender could then reclaim, write a NEWER summary and
    # history, and be overwritten moments later when this writer finished with
    # its older snapshot. That is two writers on the same artifacts, which is
    # the failure this lock exists to prevent, reached through the one phase the
    # whole-function heartbeat did not cover.
    summary_file = str(settings.get("summary_file", "shadow_signal_cohort_pnl.json"))
    write_json(cfg.governance_root / summary_file, summary)
    _progress("summary_published")
    _write_shadow_pnl_history(cfg, summary)
    _progress("pnl_history_published")
    write_json(cfg.governance_root / "shadow_cohort_update_summary.json", summary)
    _progress("update_summary_published")
    return summary
