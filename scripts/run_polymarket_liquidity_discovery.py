#!/usr/bin/env python3
"""Discover live Polymarket books that are liquid enough to be model targets.

This is read-only and paper-safe. It scans the top active Gamma events plus any
configured queries, pulls CLOB books, and writes a live liquidity watchlist for
the dashboard and future model expansion.
"""
from __future__ import annotations

import argparse
import dataclasses
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path
import traceback
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import polymarket_mispricing_bot as scanner  # noqa: E402
from polymarket_predictive_engine.config import EngineConfig, load_config  # noqa: E402
from polymarket_predictive_engine.strategy_search import _market_family  # noqa: E402
from polymarket_predictive_engine.utils import (  # noqa: E402
    now_utc,
    parse_timestamp,
    read_json,
    safe_float,
    write_csv,
    write_json,
)

_DEFAULT_FAST_FEEDBACK_EXCLUDED_FAMILIES = {
    "unknown",
    "crypto_updown_5m",
    "crypto_btc_updown_5m",
    "crypto_sol_updown_5m",
    "crypto_xrp_updown_5m",
}

# Broad by default: the scanner should look for any plausible money-making
# Polymarket opportunity, not just crypto up/down. Config can override or extend
# this list with liquidity_discovery.broad_queries.
_DEFAULT_BROAD_DISCOVERY_QUERIES = [
    "",
    "sports",
    "soccer",
    "football",
    "basketball",
    "baseball",
    "tennis",
    "golf",
    "ufc",
    "esports",
    "world cup",
    "politics",
    "election",
    "trump",
    "fed",
    "inflation",
    "economy",
    "stocks",
    "crypto",
    "bitcoin",
    "ethereum",
    "solana",
    "xrp",
    "ai",
    "openai",
    "spacex",
    "weather",
    "culture",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("liquidity_discovery", {}) or {}


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def _dedupe_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _broad_queries(settings: dict[str, Any]) -> list[str]:
    if not settings.get("broad_discovery_enabled", True):
        return []
    configured = _string_list(settings.get("broad_queries"))
    return configured or list(_DEFAULT_BROAD_DISCOVERY_QUERIES)


def _event_queries(settings: dict[str, Any]) -> list[str]:
    configured = settings.get("queries", [""]) or [""]
    if not isinstance(configured, list):
        configured = [configured]
    configured_queries = [str(query or "").strip() for query in configured]
    return _dedupe_keep_order([*configured_queries, *_broad_queries(settings)])


def _fast_feedback_excluded_families(settings: dict[str, Any]) -> set[str]:
    configured = settings.get("fast_feedback_excluded_families")
    if configured is None:
        return set(_DEFAULT_FAST_FEEDBACK_EXCLUDED_FAMILIES)
    if not isinstance(configured, list):
        configured = [configured]
    families = {str(item or "").strip() for item in configured if str(item or "").strip()}
    return families or set(_DEFAULT_FAST_FEEDBACK_EXCLUDED_FAMILIES)


def _is_fresh(token: scanner.OutcomeToken) -> bool:
    close_time = parse_timestamp(token.close_time)
    if close_time is None:
        return True
    return close_time.astimezone(timezone.utc) > datetime.now(timezone.utc)


def _time_to_close_hours(close_time_value: Any) -> float | None:
    close_time = parse_timestamp(close_time_value)
    if close_time is None:
        return None
    delta = close_time.astimezone(timezone.utc) - datetime.now(timezone.utc)
    return delta.total_seconds() / 3600.0


def _token_key(token: scanner.OutcomeToken) -> str:
    return token.token_id or f"{token.condition_id}|{token.outcome}"


def _base_config(settings: dict[str, Any]) -> scanner.BotConfig:
    return dataclasses.replace(
        scanner.BotConfig.from_env(),
        mode="scan",
        execute_live=False,
        event_slug="",
        tag_id="",
        query="",
        limit=int(settings.get("event_limit", 60)),
    )


def _public_search_events(bot_config: scanner.BotConfig, query: str, *, limit: int) -> list[dict[str, Any]]:
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


def _crypto_updown_public_queries(settings: dict[str, Any]) -> list[str]:
    search = settings.get("crypto_updown_date_search", {}) or {}
    if not search.get("enabled", True):
        return []
    assets = search.get("assets", ["bitcoin", "ethereum", "solana", "xrp"]) or []
    if not isinstance(assets, list):
        assets = [assets]
    days_ahead = int(search.get("days_ahead", 2))
    now = datetime.now(timezone.utc)
    queries: list[str] = []
    for day_offset in range(0, max(0, days_ahead) + 1):
        day = now + timedelta(days=day_offset)
        date_text = f"{day.strftime('%B')} {day.day} {day.year}"
        for asset in assets:
            clean_asset = str(asset or "").strip()
            if clean_asset:
                queries.append(f"{clean_asset} up or down {date_text}")
    return queries


def _public_search_queries(settings: dict[str, Any]) -> list[str]:
    queries = _string_list(settings.get("public_search_queries"))
    return _dedupe_keep_order([*queries, *_broad_queries(settings), *_crypto_updown_public_queries(settings)])


def _discover_tokens(settings: dict[str, Any]) -> tuple[list[scanner.OutcomeToken], list[dict[str, Any]]]:
    base = _base_config(settings)
    queries = _event_queries(settings)
    summaries: list[dict[str, Any]] = []
    query_batches: list[list[scanner.OutcomeToken]] = []
    for query in queries:
        clean_query = str(query or "").strip()
        cfg = dataclasses.replace(base, query=clean_query)
        events = scanner.discover_events(cfg)
        query_tokens = [token for token in scanner.extract_tokens(events) if _is_fresh(token)]
        query_batches.append(query_tokens)
        summaries.append(
            {"source": "events", "query": clean_query or "top_active", "events": len(events), "tokens": len(query_tokens)}
        )

    if settings.get("public_search_enabled", True):
        public_limit = int(settings.get("public_search_limit", 8))
        for public_query in _public_search_queries(settings):
            events = _public_search_events(base, public_query, limit=public_limit)
            query_tokens = [token for token in scanner.extract_tokens(events) if _is_fresh(token)]
            query_batches.append(query_tokens)
            summaries.append(
                {"source": "public_search", "query": public_query, "events": len(events), "tokens": len(query_tokens)}
            )

    # Sample fairly across configured queries so broad top-active scans do not
    # crowd out specific candidate families such as crypto, sports, or tennis.
    token_limit = int(settings.get("token_limit", 120))
    tokens: list[scanner.OutcomeToken] = []
    seen: set[str] = set()
    offsets = [0 for _ in query_batches]
    while len(tokens) < token_limit:
        added_this_round = False
        any_remaining = False
        for idx, batch in enumerate(query_batches):
            while offsets[idx] < len(batch):
                any_remaining = True
                token = batch[offsets[idx]]
                offsets[idx] += 1
                key = _token_key(token)
                if key in seen:
                    continue
                seen.add(key)
                tokens.append(token)
                added_this_round = True
                break
            if len(tokens) >= token_limit:
                break
        if not any_remaining or not added_this_round:
            break
    return tokens, summaries


def _row_from_book(token: scanner.OutcomeToken, book: scanner.Book | None, settings: dict[str, Any]) -> dict[str, Any]:
    bid = None if book is None else book.best_bid
    ask = None if book is None else book.best_ask
    spread = None if book is None else book.spread
    bid_size = 0.0 if book is None else float(book.bid_size)
    ask_size = 0.0 if book is None else float(book.ask_size)
    liquidity = bid_size + ask_size
    midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else token.gamma_price
    relative_spread = (spread / ask) if spread is not None and ask and ask > 0 else None
    family = _market_family(
        {
            "market_slug": token.market_slug,
            "question": token.question,
            "category": "",
            "outcome": token.outcome,
        }
    )
    time_to_close_hours = _time_to_close_hours(token.close_time)
    fast_feedback_max_hours = float(settings.get("fast_feedback_max_time_to_close_hours", 24.0))
    fast_feedback_excluded_families = _fast_feedback_excluded_families(settings)
    fast_feedback_family_excluded = family in fast_feedback_excluded_families
    min_liquidity = float(settings.get("min_liquidity", 250))
    max_spread = float(settings.get("max_spread", 0.04))
    max_relative_spread = float(settings.get("max_relative_spread", 0.15))
    min_price = float(settings.get("min_price", 0.02))
    if bid is None or ask is None:
        tradable = False
        reason = "missing_bid_or_ask"
    elif ask < min_price or ask > (1 - min_price):
        tradable = False
        reason = "price_too_extreme"
    elif liquidity < min_liquidity:
        tradable = False
        reason = "liquidity_below_minimum"
    elif spread is None or spread > max_spread:
        tradable = False
        reason = "spread_above_limit"
    elif relative_spread is None or relative_spread > max_relative_spread:
        tradable = False
        reason = "relative_spread_above_limit"
    else:
        tradable = True
        reason = "liquid_tight_book"
    if not tradable:
        fast_feedback = False
        fast_feedback_reason = reason
    elif fast_feedback_family_excluded:
        fast_feedback = False
        fast_feedback_reason = "family_excluded_from_fast_feedback"
    elif time_to_close_hours is None:
        fast_feedback = False
        fast_feedback_reason = "missing_close_time"
    elif time_to_close_hours <= 0.0:
        fast_feedback = False
        fast_feedback_reason = "already_closed_or_closing"
    elif time_to_close_hours > fast_feedback_max_hours:
        fast_feedback = False
        fast_feedback_reason = "outside_fast_feedback_window"
    else:
        fast_feedback = True
        fast_feedback_reason = "liquid_tight_book_closes_soon"
    return {
        "timestamp": now_utc(),
        "event_slug": token.event_slug,
        "event_title": token.event_title,
        "market_slug": token.market_slug,
        "market_id": token.condition_id,
        "question": token.question,
        "outcome": token.outcome,
        "token_id": token.token_id,
        "close_time": token.close_time,
        "time_to_close_hours": "" if time_to_close_hours is None else time_to_close_hours,
        "fast_feedback_liquidity_candidate": fast_feedback,
        "fast_feedback_filter_reason": fast_feedback_reason,
        "family": family,
        "gamma_price": "" if token.gamma_price is None else token.gamma_price,
        "best_bid": "" if bid is None else bid,
        "best_ask": "" if ask is None else ask,
        "midpoint": "" if midpoint is None else midpoint,
        "spread": "" if spread is None else spread,
        "relative_spread": "" if relative_spread is None else relative_spread,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "liquidity": liquidity,
        "tradable_liquidity_candidate": tradable,
        "liquidity_filter_reason": reason,
    }


def _family_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        family = str(row.get("family") or "unknown")
        item = grouped.setdefault(
            family,
            {
                "family": family,
                "tokens": 0,
                "tradable_tokens": 0,
                "fast_feedback_tradable_tokens": 0,
                "max_liquidity": 0.0,
                "min_spread": None,
                "shortest_time_to_close_hours": None,
            },
        )
        item["tokens"] += 1
        if str(row.get("tradable_liquidity_candidate")).lower() == "true":
            item["tradable_tokens"] += 1
        if str(row.get("fast_feedback_liquidity_candidate")).lower() == "true":
            item["fast_feedback_tradable_tokens"] += 1
        liquidity = safe_float(row.get("liquidity")) or 0.0
        item["max_liquidity"] = max(float(item["max_liquidity"]), liquidity)
        spread = safe_float(row.get("spread"))
        if spread is not None:
            item["min_spread"] = spread if item["min_spread"] is None else min(float(item["min_spread"]), spread)
        hours = safe_float(row.get("time_to_close_hours"))
        if hours is not None and hours > 0:
            item["shortest_time_to_close_hours"] = (
                hours
                if item["shortest_time_to_close_hours"] is None
                else min(float(item["shortest_time_to_close_hours"]), hours)
            )
    return sorted(
        grouped.values(),
        key=lambda item: (
            item["fast_feedback_tradable_tokens"],
            item["tradable_tokens"],
            item["max_liquidity"],
        ),
        reverse=True,
    )


def _rule_family(rule: dict[str, Any]) -> str:
    return str(rule.get("family") or rule.get("rule_value") or "").split("|", 1)[0].strip() or "unknown"


def _best_rule_for_family(rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rules:
        return None
    return sorted(
        rules,
        key=lambda rule: (
            str(rule.get("promotable")).lower() == "true",
            safe_float(rule.get("holdout_roi")) or -999.0,
            safe_float(rule.get("dev_roi")) or -999.0,
            safe_float(rule.get("holdout_rows")) or 0.0,
        ),
        reverse=True,
    )[0]


def _model_target_queue(cfg: EngineConfig, family_summary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edge_summary = read_json(cfg.governance_root / "edge_strategy_search_summary.json", default={}) or {}
    rules_by_family: dict[str, list[dict[str, Any]]] = {}
    for rule in edge_summary.get("top_rules", []) or []:
        if isinstance(rule, dict):
            rules_by_family.setdefault(_rule_family(rule), []).append(rule)

    queue: list[dict[str, Any]] = []
    for family in family_summary:
        family_name = str(family.get("family") or "unknown")
        tradable_tokens = int(family.get("tradable_tokens") or 0)
        fast_feedback_tokens = int(family.get("fast_feedback_tradable_tokens") or 0)
        best_rule = _best_rule_for_family(rules_by_family.get(family_name, []))
        promotable = bool(best_rule and str(best_rule.get("promotable")).lower() == "true")
        if tradable_tokens <= 0:
            status = "wait_for_liquidity"
            recommendation = "Do not model live yet; no tight liquid books passed the filter."
        elif promotable:
            status = "live_shadow_priority"
            recommendation = "Has historical edge and live liquidity; prioritize forward shadow evidence."
        elif best_rule:
            status = "needs_more_evidence"
            recommendation = "Liquid, but historical rule is not promotable yet; collect labels before trading."
        elif family_name == "worldcup_2026_winner":
            status = "fundamental_model_only"
            recommendation = "Liquid, but current value model rejects it; keep bookmaker/fundamental gate."
        elif fast_feedback_tokens > 0:
            status = "fast_feedback_research_candidate"
            recommendation = "Liquid and closes soon; useful for faster shadow evidence, but still requires model validation."
        else:
            status = "needs_model_research"
            recommendation = "Liquid, but no proprietary edge evidence yet; build/import a family-specific model first."
        queue.append(
            {
                "family": family_name,
                "tradable_tokens": tradable_tokens,
                "fast_feedback_tradable_tokens": fast_feedback_tokens,
                "shortest_time_to_close_hours": family.get("shortest_time_to_close_hours"),
                "tokens_scanned": int(family.get("tokens") or 0),
                "max_liquidity": family.get("max_liquidity"),
                "min_spread": family.get("min_spread"),
                "status": status,
                "recommendation": recommendation,
                "top_rule": None if best_rule is None else best_rule.get("rule_value"),
                "top_rule_promotable": promotable,
                "top_rule_holdout_roi": None if best_rule is None else best_rule.get("holdout_roi"),
                "top_rule_holdout_rows": None if best_rule is None else best_rule.get("holdout_rows"),
                "top_rule_reason": None if best_rule is None else best_rule.get("promotion_reason"),
            }
        )
    return sorted(
        queue,
        key=lambda row: (
            row["status"] == "live_shadow_priority",
            row["fast_feedback_tradable_tokens"],
            row["status"] in {"needs_more_evidence", "fundamental_model_only", "fast_feedback_research_candidate", "needs_model_research"},
            row["tradable_tokens"],
            safe_float(row.get("max_liquidity")) or 0.0,
        ),
        reverse=True,
    )


def run_liquidity_discovery(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    if not settings.get("enabled", True):
        payload = {"status": "disabled", "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "liquidity_discovery_summary.json", payload)
        return payload
    tokens, query_summaries = _discover_tokens(settings)
    base = _base_config(settings)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for token in tokens:
        try:
            book = scanner.get_orderbook(base, token)
        except Exception as exc:  # noqa: BLE001
            book = None
            errors.append(
                {
                    "token_id": token.token_id,
                    "market_slug": token.market_slug,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        rows.append(_row_from_book(token, book, settings))
    rows.sort(
        key=lambda row: (
            str(row.get("tradable_liquidity_candidate")).lower() == "true",
            str(row.get("fast_feedback_liquidity_candidate")).lower() == "true",
            safe_float(row.get("liquidity")) or 0.0,
            -(safe_float(row.get("spread")) or 999.0),
            -(safe_float(row.get("time_to_close_hours")) or 999999.0),
        ),
        reverse=True,
    )
    out = cfg.output_root / "polymarket_liquidity_discovery"
    write_csv(out / "liquidity_watchlist.csv", rows)
    tradable = [row for row in rows if str(row.get("tradable_liquidity_candidate")).lower() == "true"]
    fast_feedback = [row for row in tradable if str(row.get("fast_feedback_liquidity_candidate")).lower() == "true"]
    family_summary = _family_summary(rows)[:25]
    payload = {
        "status": "computed",
        "generated_at_utc": now_utc(),
        "settings": {
            "event_limit": int(settings.get("event_limit", 60)),
            "token_limit": int(settings.get("token_limit", 120)),
            "selection_strategy": "broad_round_robin_across_queries_fast_feedback_priority",
            "broad_discovery_enabled": bool(settings.get("broad_discovery_enabled", True)),
            "broad_query_count": len(_broad_queries(settings)),
            "min_liquidity": float(settings.get("min_liquidity", 250)),
            "max_spread": float(settings.get("max_spread", 0.04)),
            "max_relative_spread": float(settings.get("max_relative_spread", 0.15)),
            "fast_feedback_max_time_to_close_hours": float(settings.get("fast_feedback_max_time_to_close_hours", 24.0)),
            "fast_feedback_excluded_families": sorted(_fast_feedback_excluded_families(settings)),
        },
        "query_summaries": query_summaries,
        "tokens_scanned": len(tokens),
        "tradable_tokens": len(tradable),
        "fast_feedback_tradable_tokens": len(fast_feedback),
        "errors": len(errors),
        "top_tradable": tradable[:20],
        "top_fast_feedback": fast_feedback[:20],
        "family_summary": family_summary,
        "model_target_queue": _model_target_queue(cfg, family_summary),
        "watchlist_file": str(out / "liquidity_watchlist.csv"),
    }
    write_json(cfg.governance_root / "liquidity_discovery_summary.json", payload)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_polymarket_liquidity_discovery.py")
    parser.add_argument("--config", default="polymarket_predictive_config.example.yaml")
    return parser


def _write_error_summary(config_path: str, payload: dict[str, Any]) -> None:
    try:
        cfg = load_config(config_path)
        write_json(cfg.governance_root / "liquidity_discovery_summary.json", payload)
    except Exception:
        fallback = ROOT / "outputs" / "polymarket_model_governance"
        fallback.mkdir(parents=True, exist_ok=True)
        write_json(fallback / "liquidity_discovery_summary.json", payload)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_liquidity_discovery(load_config(args.config))
    except Exception as exc:  # noqa: BLE001 - preserve diagnostics for PowerShell wrappers
        trace = traceback.format_exc()
        payload = {
            "status": "error",
            "generated_at_utc": now_utc(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": trace,
            "diagnostic_note": "Liquidity discovery failed before producing a watchlist. Full traceback is preserved here because some PowerShell wrappers only log the first traceback line.",
        }
        _write_error_summary(args.config, payload)
        print(trace, file=sys.stderr)
        print(payload)
        return 2
    print(payload)
    return 0 if payload.get("status") in {"computed", "disabled"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
