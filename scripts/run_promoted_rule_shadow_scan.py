#!/usr/bin/env python3
"""Collect live shadow evidence for historically promoted rule families.

This is deliberately paper/shadow only. It reads the transparent historical
edge-search output, scans matching live Polymarket markets, and records small
hypothetical entries through the shared shadow cohort ledger.
"""
from __future__ import annotations

import argparse
import dataclasses
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_mispricing_bot as scanner  # noqa: E402
from polymarket_predictive_engine.cohort_validation import write_signal_cohort_pnl  # noqa: E402
from polymarket_predictive_engine.config import EngineConfig, load_config  # noqa: E402
from polymarket_predictive_engine.dashboard import render_dashboard  # noqa: E402
from polymarket_predictive_engine.shadow_cohort import update_shadow_cohort_evidence  # noqa: E402
from polymarket_predictive_engine.storage import connect_db  # noqa: E402
from polymarket_predictive_engine.strategy_search import _market_family  # noqa: E402
from polymarket_predictive_engine.utils import (  # noqa: E402
    now_utc,
    parse_timestamp,
    read_csv_rows,
    read_json,
    safe_float,
    write_csv,
    write_json,
)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("promoted_rule_shadow", {}) or {}


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            out.append(clean)
    return out


def _rule_queries(rule: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    family = str(rule.get("family") or "").strip()
    overrides = settings.get("query_overrides", {}) or {}
    queries: list[str] = []
    if family in overrides:
        queries.append(str(overrides[family]))
    if family.startswith("tennis_"):
        if "itf" in family:
            queries.extend(["itf tennis", "tennis"])
        elif "atp" in family:
            queries.extend(["atp tennis", "tennis"])
        elif "wta" in family:
            queries.extend(["wta tennis", "tennis"])
        else:
            queries.append("tennis")
        return _unique(queries)
    if family.startswith("crypto_"):
        queries.append("updown")
    queries.append(family.replace("_", " "))
    return _unique(queries)


def _crypto_assets_for_family(family: str, settings: dict[str, Any]) -> list[str]:
    asset_map = {
        "btc": "bitcoin",
        "eth": "ethereum",
        "sol": "solana",
        "xrp": "xrp",
    }
    for key, asset in asset_map.items():
        if f"crypto_{key}_" in family:
            return [asset]
    configured = settings.get("crypto_updown_public_search_assets", ["bitcoin", "ethereum", "solana", "xrp"]) or []
    if not isinstance(configured, list):
        configured = [configured]
    return [str(asset or "").strip() for asset in configured if str(asset or "").strip()]


def _public_search_queries_for_rule(rule: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    family = str(rule.get("family") or "")
    if not family.startswith("crypto_") or "updown" not in family:
        return []
    days_ahead = int(settings.get("crypto_updown_public_search_days_ahead", 2))
    now = datetime.now(timezone.utc)
    queries: list[str] = []
    for offset in range(0, max(0, days_ahead) + 1):
        day = now + timedelta(days=offset)
        date_text = f"{day.strftime('%B')} {day.day} {day.year}"
        for asset in _crypto_assets_for_family(family, settings):
            queries.append(f"{asset} up or down {date_text}")
    if settings.get("crypto_updown_intraday_search_enabled", True):
        hours_ahead = float(settings.get("crypto_updown_intraday_search_hours_ahead", 2.0))
        step_minutes = max(5, int(settings.get("crypto_updown_intraday_search_step_minutes", 10)))
        now_et = now.astimezone(ZoneInfo("America/New_York"))
        base_minute = (now_et.minute // step_minutes) * step_minutes
        slot = now_et.replace(minute=base_minute, second=0, microsecond=0)
        max_minutes = int(max(0.0, hours_ahead) * 60)
        for offset_minutes in range(0, max_minutes + step_minutes, step_minutes):
            slot_et = slot + timedelta(minutes=offset_minutes)
            hour = str(int(slot_et.strftime("%I")))
            minute = slot_et.strftime("%M")
            ampm = slot_et.strftime("%p")
            time_text = f"{hour}:{minute}{ampm}"
            date_text = f"{slot_et.strftime('%B')} {slot_et.day}"
            for asset in _crypto_assets_for_family(family, settings):
                queries.append(f"{asset} up or down {date_text} {time_text} ET")
    return _unique(queries)


def _discover_events_by_public_search(
    bot_config: scanner.BotConfig,
    *,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    params = {
        "q": query,
        "limit": str(limit),
        "search_profiles": "false",
        "search_tags": "false",
    }
    payload = scanner.http_json(f"{bot_config.gamma_host}/public-search?{urlencode(params)}")
    if not isinstance(payload, dict):
        return []
    events = payload.get("events") or []
    return events if isinstance(events, list) else []


def _series_ids_for_rule(rule: dict[str, Any], settings: dict[str, Any]) -> list[str]:
    family = str(rule.get("family") or "").strip()
    configured = settings.get("series_id_overrides", {}) or {}
    ids: list[str] = []
    if family in configured:
        value = configured[family]
        if isinstance(value, list):
            ids.extend(str(item) for item in value)
        else:
            ids.append(str(value))
    defaults = {
        "tennis_itf_total": "11634",
        "tennis_itf_set": "11634",
        "tennis_itf": "11634",
        "tennis_atp_total": "10365",
        "tennis_atp_set": "10365",
        "tennis_atp": "10365",
        "tennis_wta_total": "10366",
        "tennis_wta_set": "10366",
        "tennis_wta": "10366",
    }
    if family in defaults:
        ids.append(defaults[family])
    return _unique(ids)


def _discover_events_by_series(
    bot_config: scanner.BotConfig,
    *,
    series_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    params = {
        "active": "true",
        "closed": "false",
        "limit": str(limit),
        "order": "volume_24hr",
        "ascending": "false",
        "series_id": str(series_id),
    }
    payload = scanner.http_json(f"{bot_config.gamma_host}/events?{urlencode(params)}")
    return payload if isinstance(payload, list) else []


def _normalise_outcome(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _token_row(token: scanner.OutcomeToken) -> dict[str, Any]:
    return {
        "market_id": token.condition_id,
        "market_slug": token.market_slug,
        "question": token.question,
        "category": "tennis" if token.market_slug.lower().startswith(("itf-", "atp-", "wta-")) else "",
        "event_id": token.event_slug,
        "event_title": token.event_title,
        "close_time": token.close_time,
        "outcome": token.outcome,
        "token_id": token.token_id,
    }


def _token_close(token: scanner.OutcomeToken) -> datetime | None:
    parsed = parse_timestamp(token.close_time)
    return parsed.astimezone(timezone.utc) if parsed else None


def _token_sort_key(token: scanner.OutcomeToken) -> tuple[int, datetime, str]:
    close_time = _token_close(token)
    if close_time is None:
        return (1, datetime.max.replace(tzinfo=timezone.utc), token.market_slug)
    return (0, close_time, token.market_slug)


_CRYPTO_MODEL_CACHE: dict[tuple[str, int, int, int], dict[str, Any]] = {}


def _crypto_slug_parts(slug: str) -> tuple[str, int, datetime] | None:
    match = re.match(r"^(btc|eth|sol|xrp)-updown-(5m|15m)-(\d+)$", slug.lower())
    if not match:
        return None
    asset_key, interval_raw, timestamp_raw = match.groups()
    return asset_key, 15 if interval_raw == "15m" else 5, datetime.fromtimestamp(int(timestamp_raw), tz=timezone.utc)


def _crypto_symbol(asset_key: str) -> str:
    return {
        "btc": "BTCUSDT",
        "eth": "ETHUSDT",
        "sol": "SOLUSDT",
        "xrp": "XRPUSDT",
    }.get(asset_key, "")


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(0.0, variance))


def _fetch_crypto_model_snapshot(
    *,
    asset_key: str,
    interval_minutes: int,
    start_utc: datetime,
    settings: dict[str, Any],
) -> dict[str, Any]:
    now_dt = datetime.now(timezone.utc)
    cache_bucket = int(now_dt.timestamp() // 10)
    cache_key = (asset_key, interval_minutes, int(start_utc.timestamp()), cache_bucket)
    cached = _CRYPTO_MODEL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    symbol = _crypto_symbol(asset_key)
    if not symbol:
        return {"status": "unsupported_asset"}
    start_ms = int(start_utc.timestamp() * 1000)
    interval = "15m" if interval_minutes == 15 else "5m"
    kline_url = (
        "https://api.binance.com/api/v3/klines?"
        + urlencode({"symbol": symbol, "interval": interval, "startTime": str(start_ms), "limit": "1"})
    )
    ticker_url = "https://api.binance.com/api/v3/ticker/price?" + urlencode({"symbol": symbol})
    lookback = int(settings.get("crypto_updown_model_volatility_lookback_minutes", 90))
    vol_url = (
        "https://api.binance.com/api/v3/klines?"
        + urlencode({"symbol": symbol, "interval": "1m", "limit": str(max(20, min(1000, lookback)))})
    )
    try:
        candle_payload = scanner.http_json(kline_url)
        ticker_payload = scanner.http_json(ticker_url)
        vol_payload = scanner.http_json(vol_url)
    except Exception as exc:  # noqa: BLE001 - live model should fail closed
        snapshot = {"status": f"price_fetch_error:{type(exc).__name__}"}
        _CRYPTO_MODEL_CACHE[cache_key] = snapshot
        return snapshot
    if not isinstance(candle_payload, list) or not candle_payload:
        snapshot = {"status": "missing_start_candle"}
        _CRYPTO_MODEL_CACHE[cache_key] = snapshot
        return snapshot
    candle = candle_payload[0]
    start_price = safe_float(candle[1] if isinstance(candle, list) and len(candle) > 1 else None)
    current_price = safe_float(ticker_payload.get("price") if isinstance(ticker_payload, dict) else None)
    closes = [
        safe_float(row[4])
        for row in vol_payload
        if isinstance(row, list) and len(row) > 4 and safe_float(row[4]) is not None
    ] if isinstance(vol_payload, list) else []
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i] is not None and closes[i - 1] is not None and closes[i] > 0 and closes[i - 1] > 0
    ]
    if start_price is None or current_price is None or start_price <= 0 or current_price <= 0:
        snapshot = {"status": "invalid_price_snapshot"}
        _CRYPTO_MODEL_CACHE[cache_key] = snapshot
        return snapshot
    sigma_per_sqrt_second = _stddev(returns) / math.sqrt(60.0) if returns else 0.0
    sigma_floor = float(settings.get("crypto_updown_model_sigma_floor_per_sqrt_second", 0.00002))
    snapshot = {
        "status": "scored",
        "asset_key": asset_key,
        "symbol": symbol,
        "start_price": start_price,
        "current_price": current_price,
        "sigma_per_sqrt_second": max(sigma_floor, sigma_per_sqrt_second),
        "volatility_return_rows": len(returns),
        "price_source": "binance",
    }
    _CRYPTO_MODEL_CACHE[cache_key] = snapshot
    return snapshot


def _score_crypto_updown_live_model(
    *,
    token: scanner.OutcomeToken,
    book: scanner.Book,
    settings: dict[str, Any],
) -> dict[str, Any]:
    if not settings.get("crypto_updown_live_model_enabled", True):
        return {"crypto_model_status": "disabled"}
    parts = _crypto_slug_parts(token.market_slug)
    if parts is None:
        return {"crypto_model_status": "not_crypto_updown"}
    asset_key, interval_minutes, start_utc = parts
    now_dt = datetime.now(timezone.utc)
    close_utc = start_utc + timedelta(minutes=interval_minutes)
    elapsed_seconds = (now_dt - start_utc).total_seconds()
    remaining_seconds = (close_utc - now_dt).total_seconds()
    min_elapsed = float(settings.get("crypto_updown_model_min_elapsed_seconds", 20.0))
    min_remaining = float(settings.get("crypto_updown_model_min_remaining_seconds", 20.0))
    if elapsed_seconds < min_elapsed:
        return {
            "crypto_model_status": "before_model_window",
            "crypto_model_elapsed_seconds": elapsed_seconds,
            "crypto_model_remaining_seconds": remaining_seconds,
        }
    if remaining_seconds < min_remaining:
        return {
            "crypto_model_status": "too_close_to_resolution",
            "crypto_model_elapsed_seconds": elapsed_seconds,
            "crypto_model_remaining_seconds": remaining_seconds,
        }
    snapshot = _fetch_crypto_model_snapshot(
        asset_key=asset_key,
        interval_minutes=interval_minutes,
        start_utc=start_utc,
        settings=settings,
    )
    if snapshot.get("status") != "scored":
        return {
            "crypto_model_status": snapshot.get("status", "unscored"),
            "crypto_model_elapsed_seconds": elapsed_seconds,
            "crypto_model_remaining_seconds": remaining_seconds,
        }
    start_price = float(snapshot["start_price"])
    current_price = float(snapshot["current_price"])
    sigma = float(snapshot["sigma_per_sqrt_second"])
    distance = math.log(current_price / start_price)
    z_score = distance / max(1e-12, sigma * math.sqrt(max(1.0, remaining_seconds)))
    p_up = min(0.999, max(0.001, _normal_cdf(z_score)))
    outcome = _normalise_outcome(token.outcome)
    probability = p_up if outcome == "up" else 1.0 - p_up if outcome == "down" else 0.5
    price = float(book.best_ask or 0.0)
    spread = safe_float(book.spread) or 0.0
    model_edge = probability - price
    edge_after_cost = model_edge - (spread / 2.0)
    return {
        "crypto_model_status": "scored",
        "crypto_model_probability": probability,
        "crypto_model_p_up": p_up,
        "crypto_model_edge": model_edge,
        "crypto_model_edge_after_cost": edge_after_cost,
        "crypto_model_z_score": z_score,
        "crypto_model_start_price": start_price,
        "crypto_model_current_price": current_price,
        "crypto_model_distance_log": distance,
        "crypto_model_sigma_per_sqrt_second": sigma,
        "crypto_model_elapsed_seconds": elapsed_seconds,
        "crypto_model_remaining_seconds": remaining_seconds,
        "crypto_model_price_source": snapshot.get("price_source", ""),
        "crypto_model_volatility_return_rows": snapshot.get("volatility_return_rows", 0),
    }


def _matches_rule(token: scanner.OutcomeToken, rule: dict[str, Any]) -> bool:
    row = _token_row(token)
    family = _market_family(row)
    rule_family = str(rule.get("family") or "")
    family_match = family == rule_family
    if not family_match and rule_family.startswith("crypto_updown_"):
        suffix = rule_family.removeprefix("crypto_updown_")
        family_match = family.startswith("crypto_") and family.endswith(f"_updown_{suffix}")
    if not family_match:
        return False
    expected_outcome = _normalise_outcome(rule.get("outcome"))
    if expected_outcome in {"", "unknown"}:
        return True
    return _normalise_outcome(token.outcome) == expected_outcome


def _candidate_from_book(
    *,
    token: scanner.OutcomeToken,
    book: scanner.Book,
    rule: dict[str, Any],
    timestamp: str,
) -> dict[str, Any] | None:
    if book.best_ask is None or book.best_bid is None:
        return None
    price = float(book.best_ask)
    spread = safe_float(book.spread)
    liquidity = float(book.bid_size + book.ask_size)
    relative_spread = (spread / price) if spread is not None and price > 0 else None
    token_family = _market_family(_token_row(token))
    rule_family = str(rule.get("family") or "")
    token_outcome = _normalise_outcome(token.outcome)
    crypto_model = _score_crypto_updown_live_model(token=token, book=book, settings=rule.get("_settings", {}))
    if rule_family.startswith("crypto_updown_"):
        rule_value = f"{token_family}|outcome={token_outcome}"
    else:
        rule_value = str(rule.get("rule_value") or "")
    rule_scope = str(rule.get("rule_scope") or ("promoted" if rule.get("promotable") else "exploratory"))
    family = token_family if rule_family.startswith("crypto_updown_") else rule_family
    category = "crypto" if family.startswith("crypto_") else "tennis" if family.startswith("tennis_") else family.split("_", 1)[0]
    close_time = _token_close(token)
    time_to_close_hours = (
        (close_time - datetime.now(timezone.utc)).total_seconds() / 3600.0
        if close_time is not None
        else None
    )
    return {
        "market_id": token.condition_id,
        "market_slug": token.market_slug,
        "question": token.question,
        "category": category,
        "event_id": token.event_slug,
        "event_title": token.event_title,
        "close_time": token.close_time,
        "correlation_key": rule_value,
        "signal_cohort": f"{rule_scope}_historical_rule|{rule_value}",
        "outcome": token.outcome,
        "token_id": token.token_id,
        "prediction_timestamp": timestamp,
        "model_version": f"{rule_scope}-historical-rule-shadow-v1",
        "rule_scope": rule_scope,
        "family": family,
        "source_rule_family": rule_family,
        "source_rule_value": rule.get("rule_value", ""),
        "rule_family": rule.get("rule_family", ""),
        "rule_value": rule_value,
        "historical_dev_roi": rule.get("dev_roi", ""),
        "historical_holdout_roi": rule.get("holdout_roi", ""),
        "historical_rows": rule.get("rows", ""),
        "historical_holdout_rows": rule.get("holdout_rows", ""),
        "market_midpoint": (book.best_bid + book.best_ask) / 2.0,
        "executable_price": price,
        "best_bid": book.best_bid,
        "best_ask": book.best_ask,
        "spread": "" if spread is None else spread,
        "relative_spread": "" if relative_spread is None else relative_spread,
        "time_to_close_hours": "" if time_to_close_hours is None else time_to_close_hours,
        "liquidity": liquidity,
        "bid_size": book.bid_size,
        "ask_size": book.ask_size,
        "edge_lower_bound": crypto_model.get("crypto_model_edge_after_cost", rule.get("holdout_roi", 0.0)),
        "shadow_trade_candidate": True,
        "shadow_candidate_reason": (
            "crypto_updown_live_model" if crypto_model.get("crypto_model_status") == "scored" else "promoted_historical_rule"
        ),
        "shadow_priority_score": crypto_model.get("crypto_model_edge_after_cost", rule.get("holdout_roi", 0.0)),
        **crypto_model,
    }


def _passes_live_filters(row: dict[str, Any], settings: dict[str, Any]) -> tuple[bool, str]:
    liquidity = safe_float(row.get("liquidity")) or 0.0
    spread = safe_float(row.get("spread"))
    relative_spread = safe_float(row.get("relative_spread"))
    time_to_close_hours = safe_float(row.get("time_to_close_hours"))
    if str(row.get("rule_scope") or "").startswith("exploratory"):
        min_hours = float(settings.get("exploratory_min_time_to_close_hours", 0.0))
        max_hours_raw = settings.get("exploratory_max_time_to_close_hours", "")
        max_hours = safe_float(max_hours_raw)
        if time_to_close_hours is not None and time_to_close_hours < min_hours:
            return False, "time_to_close_below_exploratory_minimum"
        if max_hours is not None and time_to_close_hours is not None and time_to_close_hours > max_hours:
            return False, "time_to_close_above_exploratory_limit"
    if settings.get("crypto_updown_live_model_enabled", True) and str(row.get("family") or "").startswith("crypto_"):
        if row.get("crypto_model_status") != "scored":
            return False, f"crypto_model_{row.get('crypto_model_status') or 'unscored'}"
        probability = safe_float(row.get("crypto_model_probability")) or 0.0
        edge_after_cost = safe_float(row.get("crypto_model_edge_after_cost")) or -999.0
        if probability < float(settings.get("crypto_updown_model_min_probability", 0.57)):
            return False, "crypto_model_probability_below_minimum"
        if edge_after_cost < float(settings.get("crypto_updown_model_min_edge_after_cost", 0.03)):
            return False, "crypto_model_edge_below_minimum"
    if liquidity < float(settings.get("min_liquidity", 50)):
        return False, "liquidity_below_rule_shadow_minimum"
    if spread is None or spread > float(settings.get("max_spread", 0.08)):
        return False, "spread_above_rule_shadow_limit"
    if relative_spread is None or relative_spread > float(settings.get("max_relative_spread", 0.25)):
        return False, "relative_spread_above_rule_shadow_limit"
    return True, "rule_shadow_eligible"


def _scan_rule(cfg: EngineConfig, rule: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    event_limit = max(
        int(settings.get("event_limit", 250)),
        int(getattr(scanner.BotConfig.from_env(), "limit", 50)),
    )
    bot_config = dataclasses.replace(
        scanner.BotConfig.from_env(),
        mode="scan",
        execute_live=False,
        event_slug="",
        limit=event_limit,
    )
    events: list[dict[str, Any]] = []
    tokens: list[scanner.OutcomeToken] = []
    seen_events: set[str] = set()
    seen_tokens: set[str] = set()
    query_summaries: list[dict[str, Any]] = []
    for series_id in _series_ids_for_rule(rule, settings):
        query_events = _discover_events_by_series(bot_config, series_id=series_id, limit=event_limit)
        query_tokens = [token for token in scanner.extract_tokens(query_events) if _matches_rule(token, rule)]
        query_summaries.append(
            {"query": f"series_id:{series_id}", "events": len(query_events), "matching_tokens": len(query_tokens)}
        )
        for event in query_events:
            event_key = str(event.get("slug") or event.get("id") or event.get("ticker") or "")
            if event_key and event_key not in seen_events:
                seen_events.add(event_key)
                events.append(event)
        for token in query_tokens:
            token_key = token.token_id or f"{token.condition_id}|{token.outcome}"
            if token_key not in seen_tokens:
                seen_tokens.add(token_key)
                tokens.append(token)
    for query in _rule_queries(rule, settings):
        query_config = dataclasses.replace(
            bot_config,
            mode="scan",
            execute_live=False,
            query=query,
            event_slug="",
            output_dir=cfg.output_root
            / "polymarket_promoted_rule_shadow"
            / _slugify(str(rule.get("rule_value") or "rule")),
        )
        query_events = scanner.discover_events(query_config)
        query_tokens = [token for token in scanner.extract_tokens(query_events) if _matches_rule(token, rule)]
        query_summaries.append(
            {"query": query, "events": len(query_events), "matching_tokens": len(query_tokens)}
        )
        for event in query_events:
            event_key = str(event.get("slug") or event.get("id") or event.get("ticker") or "")
            if event_key and event_key not in seen_events:
                seen_events.add(event_key)
                events.append(event)
        for token in query_tokens:
            token_key = token.token_id or f"{token.condition_id}|{token.outcome}"
            if token_key not in seen_tokens:
                seen_tokens.add(token_key)
                tokens.append(token)
    public_limit = int(settings.get("public_search_limit", 8))
    for query in _public_search_queries_for_rule(rule, settings):
        query_events = _discover_events_by_public_search(bot_config, query=query, limit=public_limit)
        query_tokens = [token for token in scanner.extract_tokens(query_events) if _matches_rule(token, rule)]
        query_summaries.append(
            {"query": f"public_search:{query}", "events": len(query_events), "matching_tokens": len(query_tokens)}
        )
        for event in query_events:
            event_key = str(event.get("slug") or event.get("id") or event.get("ticker") or "")
            if event_key and event_key not in seen_events:
                seen_events.add(event_key)
                events.append(event)
        for token in query_tokens:
            token_key = token.token_id or f"{token.condition_id}|{token.outcome}"
            if token_key not in seen_tokens:
                seen_tokens.add(token_key)
                tokens.append(token)
    total_matching_tokens = len(tokens)
    now_dt = datetime.now(timezone.utc)
    fresh_tokens: list[scanner.OutcomeToken] = []
    stale_rejected: list[dict[str, Any]] = []
    for token in tokens:
        close_time = _token_close(token)
        if close_time is not None and close_time <= now_dt:
            stale_rejected.append({**_token_row(token), "rejection_reason": "market_past_close"})
        else:
            fresh_tokens.append(token)
    stale_count = len(stale_rejected)
    max_tokens = int(settings.get("max_tokens_per_rule", 80))
    tokens = sorted(fresh_tokens, key=_token_sort_key)[:max_tokens]
    timestamp = now_utc()
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = list(stale_rejected)
    book_checks = 0
    orderbook_errors = 0
    missing_orderbooks = 0
    missing_bid_or_ask = 0
    live_filter_rejections = 0
    for token in tokens:
        book_checks += 1
        try:
            book = scanner.get_orderbook(bot_config, token)
        except Exception as exc:  # noqa: BLE001 - one bad book must not block the scan
            orderbook_errors += 1
            rejected.append({**_token_row(token), "rejection_reason": f"orderbook_error: {type(exc).__name__}: {exc}"})
            continue
        if book is None:
            missing_orderbooks += 1
            rejected.append({**_token_row(token), "rejection_reason": "missing_orderbook"})
            continue
        row = _candidate_from_book(token=token, book=book, rule={**rule, "_settings": settings}, timestamp=timestamp)
        if row is None:
            missing_bid_or_ask += 1
            rejected.append({**_token_row(token), "rejection_reason": "missing_bid_or_ask"})
            continue
        passed, reason = _passes_live_filters(row, settings)
        row["rule_shadow_filter_reason"] = reason
        if passed:
            candidates.append(row)
        else:
            live_filter_rejections += 1
            rejected.append({**row, "rejection_reason": reason})
    rejection_reason_counts = dict(Counter(str(row.get("rejection_reason") or "unknown") for row in rejected))
    crypto_model_status_counts = dict(
        Counter(
            str(row.get("crypto_model_status") or "missing")
            for row in rejected + candidates
            if str(row.get("family") or "").startswith("crypto_")
        )
    )
    return {
        "rule_value": rule.get("rule_value", ""),
        "rule_scope": rule.get("rule_scope", "promoted" if rule.get("promotable") else "exploratory"),
        "family": rule.get("family", ""),
        "outcome": rule.get("outcome", ""),
        "query": ", ".join(item["query"] for item in query_summaries),
        "query_summaries": query_summaries,
        "events": len(events),
        "matching_tokens": len(tokens),
        "matching_tokens_total": total_matching_tokens,
        "stale_tokens_rejected": stale_count,
        "book_checks": book_checks,
        "orderbook_errors": orderbook_errors,
        "missing_orderbooks": missing_orderbooks,
        "missing_bid_or_ask": missing_bid_or_ask,
        "live_filter_rejections": live_filter_rejections,
        "rejection_reason_counts": rejection_reason_counts,
        "crypto_model_status_counts": crypto_model_status_counts,
        "candidates": candidates,
        "rejected": rejected,
    }


def _as_float(value: Any) -> float:
    return safe_float(value) or 0.0


def _as_int(value: Any) -> int:
    return int(safe_float(value) or 0)


def _select_exploratory_rules(edge_search: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
    if not settings.get("exploratory_rules_enabled", True):
        return []
    top_rules = edge_search.get("top_rules", [])
    if not isinstance(top_rules, list):
        return []
    prefixes = settings.get("exploratory_family_prefixes", ["crypto_"]) or []
    if not isinstance(prefixes, list):
        prefixes = [prefixes]
    prefixes = [str(prefix or "") for prefix in prefixes if str(prefix or "")]
    min_dev_rows = int(settings.get("exploratory_min_dev_rows", 20))
    min_dev_markets = int(settings.get("exploratory_min_dev_markets", 3))
    min_holdout_rows = int(settings.get("exploratory_min_holdout_rows", 1))
    min_dev_roi = float(settings.get("exploratory_min_dev_roi", 0.02))
    min_holdout_roi = float(settings.get("exploratory_min_holdout_roi", 0.02))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in top_rules:
        if not isinstance(rule, dict) or rule.get("promotable"):
            continue
        family = str(rule.get("family") or "")
        if prefixes and not any(family.startswith(prefix) for prefix in prefixes):
            continue
        if _as_int(rule.get("dev_rows")) < min_dev_rows:
            continue
        if _as_int(rule.get("dev_markets")) < min_dev_markets:
            continue
        if _as_int(rule.get("holdout_rows")) < min_holdout_rows:
            continue
        if _as_float(rule.get("dev_roi")) < min_dev_roi:
            continue
        if _as_float(rule.get("holdout_roi")) < min_holdout_roi:
            continue
        key = f"{family}|{rule.get('outcome') or ''}"
        if key in seen:
            continue
        seen.add(key)
        copy = dict(rule)
        copy["rule_scope"] = "exploratory"
        selected.append(copy)
        if settings.get("exploratory_inverse_rules_enabled", True) and family.startswith("crypto_"):
            outcome = _normalise_outcome(rule.get("outcome"))
            inverse = {"up": "down", "down": "up"}.get(outcome)
            if inverse:
                inverse_key = f"{family}|{inverse}"
                if inverse_key not in seen:
                    seen.add(inverse_key)
                    inverse_rule = dict(rule)
                    inverse_rule["outcome"] = inverse
                    inverse_rule["rule_value"] = f"{family}|outcome={inverse}"
                    inverse_rule["rule_scope"] = "exploratory_inverse"
                    inverse_rule["promotable"] = False
                    selected.append(inverse_rule)
        if len(selected) >= int(settings.get("exploratory_max_rules", 3)):
            break
    return selected


def run_promoted_rule_shadow_scan(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    if not settings.get("enabled", True):
        payload = {"status": "disabled", "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "promoted_rule_shadow_summary.json", payload)
        return payload

    edge_search = read_json(cfg.governance_root / "edge_strategy_search_summary.json", default={}) or {}
    promoted_rules = edge_search.get("promoted_rules", []) if isinstance(edge_search, dict) else []
    if not isinstance(promoted_rules, list):
        promoted_rules = []
    promoted_rules = [dict(rule, rule_scope="promoted") for rule in promoted_rules if isinstance(rule, dict)][
        : int(settings.get("max_rules", 3))
    ]
    exploratory_rules = _select_exploratory_rules(edge_search if isinstance(edge_search, dict) else {}, settings)
    rules = promoted_rules + exploratory_rules
    scans = [_scan_rule(cfg, rule, settings) for rule in rules]
    candidates = [row for scan in scans for row in scan.get("candidates", [])]
    rejected = [row for scan in scans for row in scan.get("rejected", [])]

    out = cfg.output_root / "polymarket_promoted_rule_shadow"
    write_csv(out / "promoted_rule_shadow_candidates.csv", candidates)
    write_csv(out / "promoted_rule_shadow_rejected.csv", rejected)

    current_predictions = read_csv_rows(cfg.output_root / "polymarket_predictions" / "predictions.csv")
    shadow = update_shadow_cohort_evidence(cfg, current_predictions + candidates)
    con = connect_db(cfg.database_path)
    try:
        cohort = write_signal_cohort_pnl(con, cfg)
    finally:
        con.close()

    payload = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "promoted_rules": len(promoted_rules),
        "exploratory_rules": len(exploratory_rules),
        "rules_scanned": len(rules),
        "scans": [
            {
                key: value
                for key, value in scan.items()
                if key not in {"candidates", "rejected"}
            }
            | {"candidates": len(scan.get("candidates", [])), "rejected": len(scan.get("rejected", []))}
            for scan in scans
        ],
        "candidates": len(candidates),
        "rejected": len(rejected),
        "shadow": shadow,
        # WO-143b.1 F4: status surfacing only. This call site reports no
        # forwarded-candidate count -- `candidates`/`rejected` above are
        # rule-scan output written to their own CSVs regardless of the shadow
        # outcome, not a claim about what reached the shadow ledger -- so no
        # count changes here. The skip is lifted to a top-level field so it is
        # greppable in the artifact for the day-after check rather than only
        # reachable by walking into the nested `shadow` payload.
        "shadow_update_status": (
            shadow.get("status") if isinstance(shadow, dict) and shadow.get("status") else "not_run"
        ),
        "cohort_pnl": cohort,
        "candidate_file": str(out / "promoted_rule_shadow_candidates.csv"),
        "rejected_file": str(out / "promoted_rule_shadow_rejected.csv"),
    }
    write_json(cfg.governance_root / "promoted_rule_shadow_summary.json", payload)
    try:
        render_dashboard(cfg)
    except Exception as exc:  # noqa: BLE001
        payload["dashboard_error"] = f"{type(exc).__name__}: {exc}"
        write_json(cfg.governance_root / "promoted_rule_shadow_summary.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_promoted_rule_shadow_scan.py")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = run_promoted_rule_shadow_scan(load_config(args.config))
    print(payload)
    return 0 if payload.get("status") in {"computed", "disabled"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
