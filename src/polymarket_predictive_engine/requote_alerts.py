"""WO-66 keyless execution assistant: human requote/pull/STOP alerts only.

The module reads the generated maker quote sheet, websocket top-of-book
features, flow toxicity, public Gamma resolution state, and the registered
decision-policy kill artifact. It never authenticates, signs, places, amends,
or cancels an order. Its strongest output is a human instruction to pull.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

import requests

from .config import EngineConfig, load_config
from .maker_carry_study import _market_url, _outcomes, _quote_prices, _token_ids
from .utils import (
    normalize_external_timestamp,
    now_utc,
    read_csv_rows,
    read_json,
    safe_float,
    write_json,
    write_text_atomic,
)

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"
ALERT_PRIORITY = {
    "quotes_ok": 0,
    "requote_advised": 1,
    "pull_quotes_now": 2,
    "STOP": 3,
}
RESOLUTION_PULL_STATES = {
    "proposed",
    "disputed",
    "challenged",
    "resolved",
}


def _boolish(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("requote_alerts", {}) if isinstance(cfg.raw.get("requote_alerts"), dict) else {}
    maker = (
        cfg.raw.get("maker_carry_study", {})
        if isinstance(cfg.raw.get("maker_carry_study"), dict)
        else {}
    )
    # Every override is tighten-only. Earlier pull windows are larger; stale
    # and drift tolerances are smaller; the toxicity trigger cannot exceed the
    # registered standing-rule value of 0.9.
    scheduled_raw = safe_float(raw.get("scheduled_event_hours"))
    quote_age_raw = safe_float(raw.get("max_live_quote_age_seconds"))
    drift_raw = safe_float(raw.get("mid_drift_distance_multiple"))
    toxicity_raw = safe_float(raw.get("toxicity_threshold"))
    band_min_raw = safe_float(maker.get("share_model_mid_band_min"))
    band_max_raw = safe_float(maker.get("share_model_mid_band_max"))
    scheduled_hours = max(24.0, scheduled_raw if scheduled_raw is not None else 24.0)
    quote_age = min(1800.0, quote_age_raw if quote_age_raw is not None else 1800.0)
    drift_multiple = min(1.0, drift_raw if drift_raw is not None else 1.0)
    toxicity = min(0.9, toxicity_raw if toxicity_raw is not None else 0.9)
    band_min = max(0.10, band_min_raw if band_min_raw is not None else 0.10)
    band_max = min(0.90, band_max_raw if band_max_raw is not None else 0.90)
    return {
        "enabled": _boolish(raw.get("enabled"), True),
        "scheduled_event_hours": scheduled_hours,
        "max_live_quote_age_seconds": max(1.0, quote_age),
        "mid_drift_distance_multiple": max(0.0, drift_multiple),
        "toxicity_threshold": max(0.0, toxicity),
        "quote_band_min": band_min,
        "quote_band_max": band_max,
        "gamma_base_url": str(raw.get("gamma_base_url") or DEFAULT_GAMMA_BASE_URL).rstrip("/"),
        "clob_base_url": str(raw.get("clob_base_url") or DEFAULT_CLOB_BASE_URL).rstrip("/"),
        "request_timeout_seconds": max(
            1.0,
            safe_float(raw.get("request_timeout_seconds")) or 10.0,
        ),
        "rest_book_fallback_enabled": _boolish(raw.get("rest_book_fallback_enabled"), True),
        # One POST /books call covers the bounded token set. The hard ceiling
        # prevents a malformed/stale sheet from turning the safety fallback
        # into an unbounded poller; deferred tokens stay visibly fail-closed.
        "rest_book_fallback_max_tokens_per_cycle": min(
            16,
            max(1, int(safe_float(raw.get("rest_book_fallback_max_tokens_per_cycle")) or 8)),
        ),
        "query_resolution_status": _boolish(raw.get("query_resolution_status"), True),
        "notification_enabled": _boolish(raw.get("notification_enabled"), False),
    }


def _as_utc(value: Any) -> datetime | None:
    seconds = normalize_external_timestamp(value)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _latest_quotes(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    path = cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    latest_complete: dict[str, dict[str, Any]] = {}
    latest_any: dict[str, dict[str, Any]] = {}
    for row in read_csv_rows(path):
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        stamp = _as_utc(row.get("source_timestamp") or row.get("collected_at_utc"))
        if not token or stamp is None:
            continue
        current = latest_any.get(token)
        if current is None or stamp > current["_stamp"]:
            latest_any[token] = {**row, "_stamp": stamp}
        if safe_float(row.get("best_bid")) is None or safe_float(row.get("best_ask")) is None:
            continue
        current = latest_complete.get(token)
        if current is None or stamp > current["_stamp"]:
            latest_complete[token] = {**row, "_stamp": stamp}
    return {token: latest_complete.get(token, row) for token, row in latest_any.items()}


def _toxicity_by_market(cfg: EngineConfig) -> dict[str, dict[str, Any]]:
    """Per-market toxicity: percentile score, absolute raw imbalance, and the
    WO-102 composite block flag, keyed by every market/token identifier."""
    values: dict[str, dict[str, Any]] = {}
    for row in read_csv_rows(cfg.output_root / "maker_carry" / "flow_toxicity.csv"):
        record = {
            "score": safe_float(row.get("toxicity_score")),
            "vpin_raw": safe_float(row.get("vpin_raw")),
            "toxic_blocked": str(row.get("toxic_blocked") or "").strip().lower() in {"true", "1", "yes"},
        }
        for key in (row.get("market"), row.get("asset_id"), row.get("condition_id")):
            identifier = str(key or "").strip()
            if identifier:
                values[identifier] = record
    return values


def _resolved_markets(cfg: EngineConfig) -> set[str]:
    resolved: set[str] = set()
    path = cfg.output_root / "polymarket_websocket" / "resolution_events.csv"
    for row in read_csv_rows(path):
        for key in ("market", "condition_id", "winning_asset_id"):
            identifier = str(row.get(key) or "").strip()
            if identifier:
                resolved.add(identifier)
    return resolved


def _gamma_resolution_state(
    settings: Mapping[str, Any],
    condition_ids: list[str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    if not condition_ids or not settings["query_resolution_status"]:
        return {}, []
    try:
        response = requests.get(
            f"{settings['gamma_base_url']}/markets",
            params={"condition_ids": condition_ids, "limit": len(condition_ids)},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - public resolution lookup fails soft into visible diagnostics
        return {}, [f"{type(exc).__name__}: {exc}"]
    rows = payload if isinstance(payload, list) else []
    by_condition: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        condition = str(row.get("conditionId") or "").strip()
        if condition:
            by_condition[condition] = row
    return by_condition, []


def _book_snapshot(payload: Mapping[str, Any], *, as_of: datetime) -> dict[str, Any] | None:
    """Normalise one public CLOB book into the live-quote contract.

    CLOB currently returns bids low-to-high and asks high-to-low, but the
    contract deliberately computes extrema instead of depending on ordering.
    """

    bids = [
        value
        for row in (payload.get("bids") if isinstance(payload.get("bids"), list) else [])
        if isinstance(row, Mapping) and (value := safe_float(row.get("price"))) is not None
    ]
    asks = [
        value
        for row in (payload.get("asks") if isinstance(payload.get("asks"), list) else [])
        if isinstance(row, Mapping) and (value := safe_float(row.get("price"))) is not None
    ]
    if not bids or not asks:
        return None
    bid = max(bids)
    ask = min(asks)
    if not 0 < bid < ask < 1:
        return None
    return {
        "asset_id": str(payload.get("asset_id") or payload.get("token_id") or "").strip(),
        "market": str(payload.get("market") or "").strip(),
        "best_bid": bid,
        "best_ask": ask,
        "midpoint": (bid + ask) / 2.0,
        "tick_size": safe_float(payload.get("tick_size")),
        "source_timestamp": as_of.isoformat().replace("+00:00", "Z"),
        "_stamp": as_of,
        "_source": "rest_book_fallback",
    }


def _rest_book_snapshots(
    settings: Mapping[str, Any],
    token_ids: list[str],
    *,
    as_of: datetime,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Fetch uncovered quote-sheet books with at most one public REST call."""

    unique = list(dict.fromkeys(str(token).strip() for token in token_ids if str(token).strip()))
    limit = int(settings["rest_book_fallback_max_tokens_per_cycle"])
    selected = unique[:limit]
    diagnostics: dict[str, Any] = {
        "enabled": bool(settings["rest_book_fallback_enabled"]),
        "request_limit_per_cycle": 1,
        "requests_made": 0,
        "tokens_needed": len(unique),
        "tokens_requested": len(selected),
        "tokens_deferred": unique[limit:],
        "books_received": 0,
        "errors": [],
    }
    if not selected or not settings["rest_book_fallback_enabled"]:
        return {}, diagnostics
    try:
        diagnostics["requests_made"] = 1
        response = requests.post(
            f"{settings['clob_base_url']}/books",
            json=[{"token_id": token} for token in selected],
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - missing public book remains visibly fail-closed
        diagnostics["errors"] = [f"{type(exc).__name__}: {exc}"]
        return {}, diagnostics

    rows = payload if isinstance(payload, list) else []
    books: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        token = str(row.get("asset_id") or row.get("token_id") or "").strip()
        if not token and index < len(selected):
            token = selected[index]
        snapshot = _book_snapshot(row, as_of=as_of)
        if token and snapshot is not None:
            snapshot["asset_id"] = token
            books[token] = snapshot
    diagnostics["books_received"] = len(books)
    diagnostics["missing_tokens"] = [token for token in selected if token not in books]
    return books, diagnostics


def _enrich_ticket_metadata(
    ticket: Mapping[str, Any],
    gamma_market: Mapping[str, Any],
) -> dict[str, Any]:
    """Fill legacy WO-66 ticket metadata from the same public Gamma row."""

    enriched = dict(ticket)
    sources: list[str] = ["maker_carry_study"]
    tokens = _token_ids(dict(gamma_market))
    outcomes = _outcomes(dict(gamma_market))
    token = str(enriched.get("token_id") or "").strip()
    outcome = str(enriched.get("outcome") or "").strip()
    index: int | None = None
    if token and token in tokens:
        index = tokens.index(token)
    elif outcome and outcome in outcomes:
        index = outcomes.index(outcome)
    elif tokens:
        index = 0
    if not token and index is not None and index < len(tokens):
        enriched["token_id"] = tokens[index]
        token = tokens[index]
        sources.append("gamma_token")
    if not outcome and index is not None and index < len(outcomes):
        enriched["outcome"] = outcomes[index]
        sources.append("gamma_outcome")

    if not str(enriched.get("market_url") or "").strip():
        url = _market_url(dict(gamma_market))
        if url:
            enriched["market_url"] = url
            sources.append("gamma_url")
    if safe_float(enriched.get("order_price_min_tick_size")) is None:
        tick = safe_float(
            gamma_market.get("orderPriceMinTickSize")
            or gamma_market.get("minimumTickSize")
        )
        if tick is not None:
            enriched["order_price_min_tick_size"] = tick
            sources.append("gamma_tick")
    if not str(enriched.get("end_date_utc") or "").strip():
        end_date = gamma_market.get("endDate") or gamma_market.get("endDateIso")
        if end_date:
            enriched["end_date_utc"] = end_date
    if not str(enriched.get("event_start_time_utc") or "").strip():
        event_start = gamma_market.get("gameStartTime") or gamma_market.get("eventStartTime")
        if event_start:
            enriched["event_start_time_utc"] = event_start
    enriched["ticket_metadata_sources"] = sources
    return enriched


def _complete_ticket_from_live(
    ticket: Mapping[str, Any],
    live: Mapping[str, Any],
) -> dict[str, Any]:
    """Complete quote prices conservatively and recompute the exactness flag."""

    enriched = dict(ticket)
    tick = safe_float(enriched.get("order_price_min_tick_size"))
    if tick is None:
        tick = safe_float(live.get("tick_size"))
        if tick is not None:
            enriched["order_price_min_tick_size"] = tick
    bid = safe_float(live.get("best_bid"))
    ask = safe_float(live.get("best_ask"))
    midpoint = safe_float(live.get("midpoint"))
    if midpoint is None and bid is not None and ask is not None:
        midpoint = (bid + ask) / 2.0
    if safe_float(enriched.get("reference_mid_price")) is None and midpoint is not None:
        enriched["reference_mid_price"] = midpoint
    if safe_float(enriched.get("reference_best_bid")) is None and bid is not None:
        enriched["reference_best_bid"] = bid
    if safe_float(enriched.get("reference_best_ask")) is None and ask is not None:
        enriched["reference_best_ask"] = ask

    quote_bid = safe_float(enriched.get("quote_bid_price"))
    quote_ask = safe_float(enriched.get("quote_ask_price"))
    distance = safe_float(enriched.get("quote_distance"))
    if (quote_bid is None or quote_ask is None) and midpoint is not None and distance is not None:
        quote_bid, quote_ask = _quote_prices(midpoint, distance, tick)
        enriched["quote_bid_price"] = quote_bid
        enriched["quote_ask_price"] = quote_ask

    exact = bool(
        str(enriched.get("market_url") or "").strip()
        and str(enriched.get("token_id") or "").strip()
        and str(enriched.get("outcome") or "").strip()
        and tick is not None
        and 0 < tick < 1
        and safe_float(enriched.get("quote_bid_price")) is not None
        and safe_float(enriched.get("quote_ask_price")) is not None
    )
    enriched["order_ticket_status"] = (
        "exact" if exact else "incomplete_missing_public_market_metadata_tick_or_quotes"
    )
    return enriched


def _market_state(current: str, proposed: str) -> str:
    return proposed if ALERT_PRIORITY[proposed] > ALERT_PRIORITY[current] else current


def _alert(
    alerts: list[dict[str, Any]],
    *,
    state: str,
    rule: str,
    message: str,
    value: Any = None,
    threshold: Any = None,
) -> None:
    alerts.append(
        {
            "state": state,
            "rule": rule,
            "message": message,
            "value": value,
            "threshold": threshold,
        }
    )


def _scheduled_time(ticket: Mapping[str, Any], live: Mapping[str, Any]) -> datetime | None:
    for value in (
        ticket.get("event_start_time_utc"),
        live.get("close_time"),
        ticket.get("end_date_utc"),
    ):
        stamp = _as_utc(value)
        if stamp is not None:
            return stamp
    return None


def _ticket_alerts(
    ticket: Mapping[str, Any],
    *,
    live: Mapping[str, Any],
    toxicity: Mapping[str, float],
    gamma_market: Mapping[str, Any],
    resolved: set[str],
    settings: Mapping[str, Any],
    as_of: datetime,
) -> dict[str, Any]:
    condition_id = str(ticket.get("condition_id") or "").strip()
    token_id = str(ticket.get("token_id") or "").strip()
    alerts: list[dict[str, Any]] = []
    state = "quotes_ok"

    if str(ticket.get("order_ticket_status") or "") != "exact":
        _alert(
            alerts,
            state="pull_quotes_now",
            rule="incomplete_order_ticket",
            message="The generated ticket is missing a public URL, outcome/token, tick, bid, or ask.",
        )

    live_stamp = live.get("_stamp") if isinstance(live.get("_stamp"), datetime) else None
    bid = safe_float(live.get("best_bid"))
    ask = safe_float(live.get("best_ask"))
    midpoint = safe_float(live.get("midpoint"))
    if midpoint is None and bid is not None and ask is not None:
        midpoint = (bid + ask) / 2.0
    if live_stamp is None or bid is None or ask is None or midpoint is None:
        # WO-104 item 5: fail closed. A maker that cannot see a current book
        # cannot manage resting-quote risk, so missing bid/ask is a PULL, not
        # advice to requote.
        _alert(
            alerts,
            state="pull_quotes_now",
            rule="missing_live_bid_ask",
            message=(
                "No complete websocket bid/ask or bounded public REST book snapshot "
                "is available; pull resting quotes until the book is visible again."
            ),
        )
        quote_age = None
    else:
        quote_age = max(0.0, (as_of - live_stamp).total_seconds())
        if quote_age > float(settings["max_live_quote_age_seconds"]):
            _alert(
                alerts,
                state="requote_advised",
                rule="stale_live_bid_ask",
                message="The latest websocket bid/ask is too old to trust.",
                value=round(quote_age, 3),
                threshold=settings["max_live_quote_age_seconds"],
            )
        if not float(settings["quote_band_min"]) <= midpoint <= float(settings["quote_band_max"]):
            _alert(
                alerts,
                state="pull_quotes_now",
                rule="mid_outside_registered_band",
                message="The live midpoint left the registered maker-reward price band.",
                value=round(midpoint, 6),
                threshold=[settings["quote_band_min"], settings["quote_band_max"]],
            )
        reference_mid = safe_float(ticket.get("reference_mid_price"))
        quote_distance = safe_float(ticket.get("quote_distance"))
        if reference_mid is not None and quote_distance is not None:
            drift_limit = quote_distance * float(settings["mid_drift_distance_multiple"])
            drift = abs(midpoint - reference_mid)
            if drift > drift_limit:
                _alert(
                    alerts,
                    state="requote_advised",
                    rule="mid_drifted_beyond_quote_band",
                    message="The live midpoint moved beyond the ticket's registered quote-distance band.",
                    value=round(drift, 6),
                    threshold=round(drift_limit, 6),
                )

    event_time = _scheduled_time(ticket, live)
    hours_to_event = None
    if event_time is not None:
        hours_to_event = (event_time - as_of).total_seconds() / 3600.0
        if hours_to_event <= float(settings["scheduled_event_hours"]):
            _alert(
                alerts,
                state="pull_quotes_now",
                rule="scheduled_event_within_window",
                message="The quoted market is inside the mandatory pre-event pull window.",
                value=round(hours_to_event, 3),
                threshold=settings["scheduled_event_hours"],
            )

    tox_record = next(
        (toxicity[key] for key in (condition_id, token_id) if key and key in toxicity),
        None,
    )
    toxicity_score = tox_record.get("score") if tox_record else None
    raw_imbalance = tox_record.get("vpin_raw") if tox_record else None
    if toxicity_score is not None and toxicity_score > float(settings["toxicity_threshold"]):
        _alert(
            alerts,
            state="pull_quotes_now",
            rule="flow_toxicity_above_limit",
            message="Flow toxicity exceeded the registered standing-rule threshold.",
            value=toxicity_score,
            threshold=settings["toxicity_threshold"],
        )
    # WO-104 item 5 + WO-102: the requote screen must also honour the absolute
    # raw-imbalance floor and the composite block, and must fail closed when
    # toxicity is unmeasured for a market we are quoting.
    if raw_imbalance is not None and raw_imbalance >= float(settings["toxicity_threshold"]):
        _alert(
            alerts,
            state="pull_quotes_now",
            rule="flow_toxicity_absolute_floor",
            message="Absolute raw order-flow imbalance is at or above the registered floor.",
            value=raw_imbalance,
            threshold=settings["toxicity_threshold"],
        )
    if tox_record is not None and bool(tox_record.get("toxic_blocked")):
        _alert(
            alerts,
            state="pull_quotes_now",
            rule="flow_toxicity_composite_block",
            message="WO-102 composite toxicity screen flagged this market.",
        )

    uma_status = str(
        gamma_market.get("umaResolutionStatus")
        or ticket.get("uma_resolution_status")
        or ""
    ).strip().lower()
    resolution_hit = bool({condition_id, token_id}.intersection(resolved))
    if uma_status in RESOLUTION_PULL_STATES or resolution_hit or _boolish(gamma_market.get("closed")):
        _alert(
            alerts,
            state="pull_quotes_now",
            rule="resolution_proposal_or_resolution_detected",
            message="A public UMA proposal/dispute or authoritative resolution exists for this market.",
            value=uma_status or "market_resolved_websocket_event",
        )

    for item in alerts:
        state = _market_state(state, str(item["state"]))
    return {
        "condition_id": condition_id,
        "token_id": token_id,
        "market_url": ticket.get("market_url", ""),
        "question": ticket.get("question", ""),
        "outcome": ticket.get("outcome", ""),
        "order_price_min_tick_size": safe_float(ticket.get("order_price_min_tick_size")),
        "quote_bid_price": safe_float(ticket.get("quote_bid_price")),
        "quote_ask_price": safe_float(ticket.get("quote_ask_price")),
        "quote_size_shares": safe_float(ticket.get("quote_size_shares")),
        "capital_usd": safe_float(ticket.get("capital_usd")),
        "order_ticket_status": ticket.get("order_ticket_status", ""),
        "ticket_metadata_sources": ticket.get("ticket_metadata_sources", []),
        "end_date_utc": ticket.get("end_date_utc", ""),
        "event_start_time_utc": ticket.get("event_start_time_utc", ""),
        "alert_state": state,
        "alerts": alerts,
        "live_bid": bid,
        "live_ask": ask,
        "live_midpoint": midpoint,
        "live_price_source": live.get("_source", "websocket" if live else "missing"),
        "live_quote_age_seconds": round(quote_age, 3) if quote_age is not None else None,
        "hours_to_scheduled_event": round(hours_to_event, 3) if hours_to_event is not None else None,
        "toxicity_score": toxicity_score,
        "uma_resolution_status": uma_status,
    }


def _notification(
    cfg: EngineConfig,
    payload: Mapping[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    out_root = cfg.output_root / "maker_carry"
    state_path = out_root / "requote_alert_notification_state.json"
    body_path = out_root / "requote_alert_notification.md"
    critical = str(payload.get("alert_state") or "") in {"pull_quotes_now", "STOP"}
    digest_input = {
        "notification_enabled": enabled,
        "alert_state": payload.get("alert_state"),
        "markets": [
            {
                "condition_id": row.get("condition_id"),
                "alert_state": row.get("alert_state"),
                "rules": [alert.get("rule") for alert in row.get("alerts", [])],
            }
            for row in payload.get("markets", [])
            if isinstance(row, Mapping)
        ],
        "kill_criteria": payload.get("kill_criteria_triggered", []),
    }
    digest = hashlib.sha256(
        json.dumps(digest_input, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    previous = read_json(state_path, default={}) or {}
    state_changed = str(previous.get("digest") or "") != digest
    notify = bool(enabled and critical and state_changed)
    lines = [
        "# Polymarket maker quote alert",
        "",
        f"State: **{payload.get('alert_state', 'quotes_ok')}**",
        f"Generated: `{payload.get('generated_at_utc', '')}`",
        "",
    ]
    for row in payload.get("markets", []):
        if not isinstance(row, Mapping) or row.get("alert_state") == "quotes_ok":
            continue
        lines.append(f"- {row.get('question') or row.get('condition_id')}: **{row.get('alert_state')}**")
        for item in row.get("alerts", []):
            if isinstance(item, Mapping):
                lines.append(f"  - {item.get('rule')}: {item.get('message')}")
    if payload.get("kill_criteria_triggered"):
        lines.append(f"- Kill criteria: {', '.join(payload['kill_criteria_triggered'])}")
    lines += ["", "Human action only. This system cannot place, amend, cancel, or sign orders."]
    write_text_atomic(body_path, "\n".join(lines) + "\n")
    write_json(
        state_path,
        {
            "digest": digest,
            "alert_state": payload.get("alert_state"),
            "generated_at_utc": payload.get("generated_at_utc"),
        },
    )
    return {
        "enabled": enabled,
        "eligible": critical,
        "notify": notify,
        "state_changed": state_changed,
        "subject": f"Polymarket maker quotes: {payload.get('alert_state', 'quotes_ok')}",
        "body_file": str(body_path),
        "pattern": "superbru_score_change_state_digest",
    }


def _patch_quote_sheet(path: Path, payload: Mapping[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    start = "<!-- requote-alerts:start -->"
    end = "<!-- requote-alerts:end -->"
    block = "\n".join(
        [
            start,
            f"> **WO-66 live quote alert: {payload.get('alert_state', 'quotes_ok')}** — "
            f"{payload.get('headline', '')}",
            end,
            "",
        ]
    )
    pattern = re.compile(r"<!-- requote-alerts:start -->.*?<!-- requote-alerts:end -->\n?", re.S)
    if start in text and end in text:
        text = pattern.sub(block, text)
    else:
        parts = text.split("\n", 2)
        text = "\n".join(parts[:2]) + "\n\n" + block + (parts[2] if len(parts) > 2 else "")
    write_text_atomic(path, text)


def build_requote_alerts(
    cfg: EngineConfig,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "maker_carry"
    output_path = out_root / "requote_alerts.json"
    quote_sheet_path = out_root / "maker_quote_sheet.md"
    generated_at = now_utc()
    base: dict[str, Any] = {
        "status": "disabled",
        "alert_state": "quotes_ok",
        "generated_at_utc": generated_at,
        "work_order": "WO-66",
        "ticket_completeness_work_order": "WO-77",
        "read_only": True,
        "keyless": True,
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
        "order_placement_invoked": False,
        "order_amendment_invoked": False,
        "order_cancellation_invoked": False,
    }
    if not settings["enabled"]:
        write_json(output_path, base)
        return base
    if not quote_sheet_path.exists():
        base.update(
            {
                "status": "inert_no_quote_sheet",
                "headline": "No maker quote sheet exists; no alert or notification was emitted.",
                "markets": [],
                "notification": {
                    "enabled": settings["notification_enabled"],
                    "eligible": False,
                    "notify": False,
                },
            }
        )
        write_json(output_path, base)
        return base

    study = read_json(out_root / "maker_carry_study.json", default={}) or {}
    portfolio = study.get("portfolio") if isinstance(study.get("portfolio"), list) else []
    if not portfolio:
        base.update(
            {
                "status": "inert_empty_quote_sheet",
                "headline": "The maker quote sheet has no portfolio tickets.",
                "markets": [],
                "notification": {
                    "enabled": settings["notification_enabled"],
                    "eligible": False,
                    "notify": False,
                },
            }
        )
        write_json(output_path, base)
        return base

    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
    latest_quotes = _latest_quotes(cfg)
    toxicity = _toxicity_by_market(cfg)
    resolved = _resolved_markets(cfg)
    condition_ids = sorted(
        {
            str(row.get("condition_id") or "").strip()
            for row in portfolio
            if isinstance(row, Mapping) and str(row.get("condition_id") or "").strip()
        }
    )
    gamma_markets, resolution_errors = _gamma_resolution_state(settings, condition_ids)
    enriched_tickets = [
        _enrich_ticket_metadata(
            ticket,
            gamma_markets.get(str(ticket.get("condition_id") or "").strip(), {}),
        )
        for ticket in portfolio
        if isinstance(ticket, Mapping)
    ]
    missing_tokens = []
    for ticket in enriched_tickets:
        token = str(ticket.get("token_id") or "").strip()
        live = latest_quotes.get(token, {})
        if token and (
            safe_float(live.get("best_bid")) is None
            or safe_float(live.get("best_ask")) is None
        ):
            missing_tokens.append(token)
    rest_books, rest_diagnostics = _rest_book_snapshots(
        settings,
        missing_tokens,
        as_of=now,
    )
    market_rows: list[dict[str, Any]] = []
    state = "quotes_ok"
    for ticket in enriched_tickets:
        token_id = str(ticket.get("token_id") or "").strip()
        condition_id = str(ticket.get("condition_id") or "").strip()
        live = latest_quotes.get(token_id, {})
        if (
            safe_float(live.get("best_bid")) is None
            or safe_float(live.get("best_ask")) is None
        ):
            live = rest_books.get(token_id, {})
        ticket = _complete_ticket_from_live(ticket, live)
        row = _ticket_alerts(
            ticket,
            live=live,
            toxicity=toxicity,
            gamma_market=gamma_markets.get(condition_id, {}),
            resolved=resolved,
            settings=settings,
            as_of=now,
        )
        market_rows.append(row)
        state = _market_state(state, str(row["alert_state"]))

    decision = read_json(out_root / "decision_policy.json", default={}) or {}
    kill = decision.get("kill_criteria_status") if isinstance(decision.get("kill_criteria_status"), dict) else {}
    triggered = kill.get("triggered") if isinstance(kill.get("triggered"), list) else []
    if str(kill.get("status") or "").lower() == "triggered" or triggered:
        state = "STOP"

    non_ok = sum(row["alert_state"] != "quotes_ok" for row in market_rows)
    headline = {
        "quotes_ok": "All quoted markets pass the current read-only checks.",
        "requote_advised": "One or more tickets need a human price refresh before remaining in market.",
        "pull_quotes_now": "Pull the flagged human-entered quotes now and review before resuming.",
        "STOP": "Registered kill criteria fired: stop quoting and review before any resume.",
    }[state]
    payload = {
        **base,
        "status": (
            "ok"
            if not resolution_errors and not rest_diagnostics.get("errors")
            else "partial_public_lookup_error"
        ),
        "alert_state": state,
        "headline": headline,
        "quote_sheet_path": str(quote_sheet_path),
        "quote_sheet_generated_at_utc": study.get("generated_at_utc"),
        "markets": market_rows,
        "markets_checked": len(market_rows),
        "markets_requiring_action": non_ok,
        "kill_criteria_triggered": triggered,
        "resolution_lookup_errors": resolution_errors,
        "rest_book_fallback": rest_diagnostics,
        "thresholds": {
            key: settings[key]
            for key in (
                "scheduled_event_hours",
                "max_live_quote_age_seconds",
                "mid_drift_distance_multiple",
                "toxicity_threshold",
                "quote_band_min",
                "quote_band_max",
            )
        },
        "decision_use": "human decision support only; never order authorisation or execution",
    }
    payload["notification"] = _notification(
        cfg,
        payload,
        enabled=bool(settings["notification_enabled"]),
    )
    # Match the existing SuperBru score-change notifier contract so an
    # external mail/local-notification wrapper can consume this artifact
    # without giving the trading engine SMTP credentials.
    payload["notify"] = payload["notification"]["notify"]
    payload["body_file"] = payload["notification"]["body_file"]
    payload["state_changed"] = payload["notification"]["state_changed"]
    write_json(output_path, payload)
    _patch_quote_sheet(quote_sheet_path, payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return build_requote_alerts(load_config(config_path))
