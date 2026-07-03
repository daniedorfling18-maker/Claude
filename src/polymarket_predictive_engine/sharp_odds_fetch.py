"""Automated sharp-odds fetch -> the sharp_anchor input contract.

Pinnacle and Betfair Exchange have no free public API, but The Odds API aggregates both (and is
already used elsewhere in this repo via ``THE_ODDS_API_KEY``). This module pulls odds for the
configured sports/markets, keeps the **sharpest available book per event** (Pinnacle preferred,
Betfair Exchange as fallback), and writes ``market_slug,outcome,decimal_odds`` - exactly what
``build-sharp-anchor`` de-vigs into the mispricing-alpha fundamental slot.

Flow:  fetch-sharp-odds  ->  inputs/polymarket/sharp_odds.csv  ->  build-sharp-anchor  ->  fundamental.

The JSON parser is pure and unit-tested; the network call is a thin ``requests.get``. The API key is
read from the environment and never committed. The Odds API charges usage credits per request, so
the sport list and regions are kept tight by default.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests

from .config import EngineConfig, load_config
from .utils import normalize_slug, now_utc, parse_timestamp, read_csv_rows, read_json, safe_float, write_csv, write_json

DEFAULT_BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_BOOKMAKER_PRIORITY = ("pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair")
SHARP_ODDS_FIELDS = [
    "market_slug",
    "outcome",
    "decimal_odds",
    "bookmaker",
    "sport",
    "sport_title",
    "market_key",
    "commence_time",
    "home_team",
    "away_team",
    "token_id",
    "anchor_source",
    "anchor_timestamp_utc",
]
DEFAULT_FALLBACK_INPUT_PATHS = (
    "inputs/polymarket/sharp_odds_fallback.csv",
    "inputs/polymarket/manual_sharp_odds.csv",
)
FALLBACK_REJECTION_FIELDS = [
    "path",
    "row_number",
    "market_slug",
    "outcome",
    "decimal_odds",
    "bookmaker",
    "anchor_timestamp_utc",
    "reasons",
]


def redact_fetch_error(message: object, api_key: str | None = None) -> str:
    """Remove provider credentials from network error strings before writing audit files."""
    text = str(message)
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    return re.sub(r"(?i)(apiKey=)[^&\s]+", r"\1[REDACTED]", text)


def event_market_slug(home: str, away: str) -> str:
    """A venue-neutral, join-friendly market key for a fixture (order-independent of casing)."""
    return normalize_slug(f"{home} vs {away}")


def _select_bookmaker(bookmakers: Sequence[Mapping[str, Any]], priority: Sequence[str]) -> Mapping[str, Any] | None:
    """First bookmaker present in priority order (Pinnacle, then Betfair Exchange)."""
    by_key = {str(b.get("key", "")).lower(): b for b in bookmakers if isinstance(b, Mapping)}
    for key in priority:
        if key in by_key:
            return by_key[key]
    return None


def _market_outcomes(bookmaker: Mapping[str, Any], market_key: str) -> list[Mapping[str, Any]]:
    for market in bookmaker.get("markets", []) or []:
        if str(market.get("key", "")).lower() == market_key:
            return [o for o in market.get("outcomes", []) or [] if isinstance(o, Mapping)]
    return []


def parse_odds_api_events(
    events: Iterable[Mapping[str, Any]],
    *,
    bookmaker_priority: Sequence[str] = DEFAULT_BOOKMAKER_PRIORITY,
    market_key: str = "h2h",
) -> list[dict[str, Any]]:
    """Flatten The Odds API events into sharp_anchor input rows.

    One complete market per event from the sharpest book available, so ``build-sharp-anchor`` can
    de-vig the whole mutually-exclusive set together. Events with no priority book or no priced
    outcomes are skipped.

    Match odds (``h2h``) are keyed by fixture. Outright/futures odds (``outrights``) usually have
    no home/away teams, so they are keyed by sport/title and the downstream sharp anchor maps each
    team outcome onto the corresponding Polymarket World Cup winner YES token.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        home = str(event.get("home_team", "")).strip()
        away = str(event.get("away_team", "")).strip()
        sport = str(event.get("sport_key", "")).strip()
        sport_title = str(event.get("sport_title", "") or event.get("title", "")).strip()
        book = _select_bookmaker(event.get("bookmakers", []) or [], bookmaker_priority)
        if book is None:
            continue
        outcomes = _market_outcomes(book, market_key)
        if home and away:
            slug = event_market_slug(home, away)
        elif str(market_key).lower() == "outrights":
            slug = normalize_slug(sport or sport_title or str(event.get("id", "")) or "outrights")
        else:
            slug = normalize_slug(str(event.get("id", "")))
        for outcome in outcomes:
            price = safe_float(outcome.get("price"))
            name = str(outcome.get("name", "")).strip()
            if price is None or price <= 1.0 or not name:
                continue
            rows.append({
                "market_slug": slug,
                "outcome": name,
                "decimal_odds": price,
                "bookmaker": str(book.get("key", "")),
                "sport": sport,
                "sport_title": sport_title,
                "market_key": market_key,
                "commence_time": str(event.get("commence_time", "")),
                "home_team": home,
                "away_team": away,
            })
    return rows


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    return cfg.raw.get("sharp_odds_fetch", {}) or {}


def _split_markets(value: Any, default: Any = "h2h") -> list[str]:
    raw = value if value not in (None, "") else default
    if isinstance(raw, (list, tuple, set)):
        parts = raw
    else:
        parts = str(raw).split(",")
    out: list[str] = []
    for part in parts:
        text = str(part or "").strip().lower()
        if text and text not in out:
            out.append(text)
    return out or ["h2h"]


def _sport_configs(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return configured sport fetches while preserving old ``sports: [key]`` config."""
    default_markets = settings.get("markets", "h2h")
    configs: list[dict[str, Any]] = []
    for item in settings.get("sports", []) or []:
        if isinstance(item, Mapping):
            key = str(item.get("key") or item.get("sport") or item.get("sport_key") or "").strip()
            markets = _split_markets(item.get("markets", item.get("market", default_markets)), default_markets)
        else:
            key = str(item or "").strip()
            markets = _split_markets(default_markets, "h2h")
        if not key:
            continue
        configs.append({"sport": key, "markets": markets})
    return configs


def _fetch_summary_path(cfg: EngineConfig) -> Path:
    return cfg.governance_root / "sharp_odds_fetch_summary.json"


def _budget_skip_summary(cfg: EngineConfig, settings: Mapping[str, Any]) -> dict[str, Any] | None:
    interval_minutes = safe_float(settings.get("fetch_interval_minutes"))
    if interval_minutes is None:
        interval_minutes = 60.0
    if interval_minutes <= 0:
        return None
    previous = read_json(_fetch_summary_path(cfg), default={}) or {}
    if not isinstance(previous, dict) or not previous:
        return None
    if previous.get("provider_status") != "attempted":
        return None
    previous_time = parse_timestamp(previous.get("budget_reference_at_utc") or previous.get("generated_at_utc"))
    if previous_time is None:
        return None
    now = datetime.now(timezone.utc)
    age_seconds = max(0.0, (now - previous_time.astimezone(timezone.utc)).total_seconds())
    wait_seconds = float(interval_minutes) * 60.0 - age_seconds
    if wait_seconds <= 0:
        return None
    return {
        "status": "skipped_budget",
        "rows": int(safe_float(previous.get("rows")) or 0),
        "markets": int(safe_float(previous.get("markets")) or 0),
        "books_used": previous.get("books_used", []),
        "errors": 0,
        "provider_status": "skipped_budget",
        "previous_status": previous.get("status"),
        "previous_generated_at_utc": previous.get("generated_at_utc"),
        "fetch_interval_minutes": interval_minutes,
        "seconds_until_next_fetch": round(wait_seconds, 3),
        "per_sport": [],
        "fallback_rows": 0,
        "fallback_sources": [],
        "fallback_rejected_rows": 0,
        "fallback_rejection_reasons": {},
        "fallback_rejections_path": str(cfg.governance_root / "sharp_odds_fallback_rejections.csv"),
        "output_path": str(settings.get("output_path") or "inputs/polymarket/sharp_odds.csv"),
        "note": "Skipped provider fetch to preserve The Odds API request budget; using existing sharp odds input.",
        "generated_at_utc": now_utc(),
    }


def _provider_sports(
    *,
    base_url: str,
    api_key: str,
    timeout: int,
) -> tuple[set[str] | None, dict[str, Any]]:
    try:
        response = requests.get(f"{base_url}/sports", params={"apiKey": api_key}, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - provider sport-list failure should not stop odds fetches
        return None, {"status": "error", "error": redact_fetch_error(exc, api_key)}
    keys = {
        str(item.get("key") or "").strip()
        for item in (payload if isinstance(payload, list) else [])
        if isinstance(item, Mapping) and str(item.get("key") or "").strip()
    }
    return keys, {
        "status": "ok",
        "sports": len(keys),
        "requests_remaining": response.headers.get("x-requests-remaining", ""),
    }


def _fallback_paths(settings: Mapping[str, Any]) -> list[Path]:
    configured = settings.get("fallback_input_paths") or settings.get("manual_input_paths")
    if configured:
        return [Path(str(path)) for path in configured]
    return [Path(path) for path in DEFAULT_FALLBACK_INPUT_PATHS]


def _setting_bool(settings: Mapping[str, Any], key: str, default: bool) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalise_fallback_row(
    row: Mapping[str, Any],
    *,
    source_path: Path,
    row_number: int,
    settings: Mapping[str, Any],
    checked_at: datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    market_slug = str(row.get("market_slug") or row.get("market") or row.get("event_slug") or "").strip()
    outcome = str(row.get("outcome") or row.get("selection") or row.get("team") or row.get("runner") or "").strip()
    decimal_odds = safe_float(row.get("decimal_odds") or row.get("odds") or row.get("price_decimal") or row.get("decimal"))
    bookmaker = str(row.get("bookmaker") or row.get("source") or "").strip()
    timestamp_text = str(row.get("anchor_timestamp_utc") or row.get("timestamp") or "").strip()

    reasons: list[str] = []
    if not market_slug:
        reasons.append("missing_market_slug")
    if not outcome:
        reasons.append("missing_outcome")
    if decimal_odds is None:
        reasons.append("missing_or_invalid_decimal_odds")
    elif decimal_odds <= 1.0:
        reasons.append("decimal_odds_not_greater_than_one")
    else:
        max_decimal_odds = safe_float(settings.get("fallback_max_decimal_odds")) or 1001.0
        if decimal_odds > max_decimal_odds:
            reasons.append("decimal_odds_above_sanity_ceiling")

    if _setting_bool(settings, "fallback_require_bookmaker", True) and not bookmaker:
        reasons.append("missing_bookmaker_or_source")

    parsed_timestamp = parse_timestamp(timestamp_text)
    if _setting_bool(settings, "fallback_require_timestamp", True) and not timestamp_text:
        reasons.append("missing_anchor_timestamp_utc")
    elif timestamp_text and parsed_timestamp is None:
        reasons.append("invalid_anchor_timestamp_utc")
    elif parsed_timestamp is not None:
        parsed_timestamp = parsed_timestamp.astimezone(timezone.utc)
        age_hours = (checked_at - parsed_timestamp).total_seconds() / 3600.0
        if age_hours < -0.25:
            reasons.append("anchor_timestamp_in_future")
        max_age_hours = safe_float(settings.get("fallback_max_age_hours"))
        if max_age_hours is None:
            max_age_hours = 24.0
        if max_age_hours > 0 and age_hours > max_age_hours and _setting_bool(settings, "fallback_reject_stale", True):
            reasons.append("stale_anchor_timestamp")

    if reasons:
        return None, {
            "path": str(source_path),
            "row_number": row_number,
            "market_slug": market_slug,
            "outcome": outcome,
            "decimal_odds": "" if decimal_odds is None else decimal_odds,
            "bookmaker": bookmaker,
            "anchor_timestamp_utc": timestamp_text,
            "reasons": "; ".join(reasons),
        }

    return {
        "market_slug": market_slug,
        "outcome": outcome,
        "decimal_odds": decimal_odds,
        "bookmaker": bookmaker,
        "sport": str(row.get("sport") or "").strip(),
        "sport_title": str(row.get("sport_title") or "").strip(),
        "market_key": str(row.get("market_key") or row.get("market_type") or "").strip(),
        "commence_time": str(row.get("commence_time") or row.get("start_time") or "").strip(),
        "home_team": str(row.get("home_team") or "").strip(),
        "away_team": str(row.get("away_team") or "").strip(),
        "token_id": str(row.get("token_id") or row.get("asset_id") or row.get("clob_token_id") or "").strip(),
        "anchor_source": str(row.get("anchor_source") or row.get("source") or bookmaker or source_path.name).strip(),
        "anchor_timestamp_utc": timestamp_text,
    }, None


def load_fallback_sharp_odds(
    settings: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    checked_at = datetime.now(timezone.utc)
    for path in _fallback_paths(settings):
        raw_rows = read_csv_rows(path)
        accepted = 0
        path_rejections: list[dict[str, Any]] = []
        for row_number, raw in enumerate(raw_rows, start=2):
            normalised, rejection = _normalise_fallback_row(
                raw,
                source_path=path,
                row_number=row_number,
                settings=settings,
                checked_at=checked_at,
            )
            if rejection is not None:
                rejections.append(rejection)
                path_rejections.append(rejection)
            if normalised is None:
                continue
            rows.append(normalised)
            accepted += 1
        if raw_rows or path.exists():
            reason_counts = Counter(
                reason.strip()
                for rejection in path_rejections
                for reason in str(rejection.get("reasons") or "").split(";")
                if reason.strip()
            )
            sources.append(
                {
                    "path": str(path),
                    "rows_in": len(raw_rows),
                    "rows": accepted,
                    "rejected_rows": len(path_rejections),
                    "top_rejection_reasons": dict(reason_counts.most_common(5)),
                }
            )
    return rows, sources, rejections


def fetch_sharp_odds(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    base_url = str(settings.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    api_key = os.getenv(str(settings.get("api_key_env", "THE_ODDS_API_KEY")), "").strip()
    sport_configs = _sport_configs(settings)
    regions = str(settings.get("regions", "eu,uk"))
    odds_format = str(settings.get("odds_format", "decimal"))
    priority = tuple(settings.get("bookmaker_priority", DEFAULT_BOOKMAKER_PRIORITY))
    timeout = int(settings.get("request_timeout_seconds", 20))
    out_path = Path(settings.get("output_path") or "inputs/polymarket/sharp_odds.csv")
    max_requests = int(safe_float(settings.get("max_requests_per_run")) or 5)
    max_requests = max(0, max_requests)

    rows: list[dict[str, Any]] = []
    per_sport: list[dict[str, Any]] = []
    provider_status = "not_attempted"
    provider_sports_status: dict[str, Any] = {"status": "not_checked"}
    requests_used = 0
    skipped_unknown_sports: list[str] = []
    skipped_budget_sports: list[str] = []
    if api_key and sport_configs:
        budget_skip = _budget_skip_summary(cfg, settings)
        if budget_skip is not None:
            write_json(_fetch_summary_path(cfg), budget_skip)
            return budget_skip
        provider_status = "attempted"
        available_sports: set[str] | None = None
        if _setting_bool(settings, "validate_sports", True):
            available_sports, provider_sports_status = _provider_sports(base_url=base_url, api_key=api_key, timeout=timeout)
        for config in sport_configs:
            sport = str(config.get("sport") or "").strip()
            markets = _split_markets(config.get("markets"), settings.get("markets", "h2h"))
            if available_sports is not None and sport not in available_sports:
                skipped_unknown_sports.append(sport)
                per_sport.append({"sport": sport, "markets": ",".join(markets), "status": "skipped_unknown_sport", "rows": 0})
                continue
            if requests_used >= max_requests:
                skipped_budget_sports.append(sport)
                per_sport.append({"sport": sport, "markets": ",".join(markets), "status": "skipped_budget", "rows": 0})
                continue
            requests_used += 1
            try:
                response = requests.get(
                    f"{base_url}/sports/{sport}/odds",
                    params={
                        "apiKey": api_key,
                        "regions": regions,
                        "markets": ",".join(markets),
                        "oddsFormat": odds_format,
                        "bookmakers": ",".join(priority),
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                events = response.json()
                sport_rows: list[dict[str, Any]] = []
                for market_key in markets:
                    sport_rows.extend(parse_odds_api_events(events, bookmaker_priority=priority, market_key=market_key))
                rows.extend(sport_rows)
                per_sport.append(
                    {
                        "sport": sport,
                        "markets": ",".join(markets),
                        "status": "ok",
                        "events": len(events) if isinstance(events, list) else 0,
                        "rows": len(sport_rows),
                        "requests_remaining": response.headers.get("x-requests-remaining", ""),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - network/parse errors are per-sport, not fatal
                per_sport.append(
                    {
                        "sport": sport,
                        "markets": ",".join(markets),
                        "status": "error",
                        "error": redact_fetch_error(exc, api_key),
                        "rows": 0,
                    }
                )
    elif not api_key:
        provider_status = "missing_api_key"
    elif not sport_configs:
        provider_status = "no_sports_configured"

    fallback_rows: list[dict[str, Any]] = []
    fallback_sources: list[dict[str, Any]] = []
    fallback_rejections: list[dict[str, Any]] = []
    if not rows:
        fallback_rows, fallback_sources, fallback_rejections = load_fallback_sharp_odds(settings)
        rows.extend(fallback_rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out_path, rows, fieldnames=SHARP_ODDS_FIELDS)
    fallback_rejections_path = cfg.governance_root / "sharp_odds_fallback_rejections.csv"
    write_csv(fallback_rejections_path, fallback_rejections, fieldnames=FALLBACK_REJECTION_FIELDS)
    books_used = sorted({str(r.get("bookmaker", "")) for r in rows if r.get("bookmaker")})
    error_count = sum(1 for item in per_sport if item.get("status") == "error")
    provider_request_statuses = [
        str(item.get("status") or "")
        for item in per_sport
        if str(item.get("status") or "") not in {"skipped_unknown_sport", "skipped_budget"}
    ]
    if fallback_rows:
        status = "fallback_loaded"
    elif provider_status == "missing_api_key":
        status = "missing_api_key"
    elif provider_status == "no_sports_configured":
        status = "no_sports_configured"
    elif per_sport and not provider_request_statuses and skipped_budget_sports:
        status = "skipped_budget"
    elif per_sport and not provider_request_statuses and skipped_unknown_sports:
        status = "no_available_sports_configured"
    elif provider_request_statuses and error_count == len(provider_request_statuses):
        status = "error"
    elif error_count or skipped_budget_sports or skipped_unknown_sports:
        status = "partial"
    else:
        status = "fetched"
    summary = {
        "status": status,
        "rows": len(rows),
        "markets": len({r["market_slug"] for r in rows}),
        "books_used": books_used,
        "errors": error_count,
        "provider_status": provider_status,
        "provider_sports_status": provider_sports_status,
        "per_sport": per_sport,
        "configured_sports": sport_configs,
        "max_requests_per_run": max_requests,
        "requests_used": requests_used,
        "skipped_unknown_sports": skipped_unknown_sports,
        "skipped_budget_sports": skipped_budget_sports,
        "fallback_rows": len(fallback_rows),
        "fallback_sources": fallback_sources,
        "fallback_rejected_rows": len(fallback_rejections),
        "fallback_rejection_reasons": dict(
            Counter(
                reason.strip()
                for rejection in fallback_rejections
                for reason in str(rejection.get("reasons") or "").split(";")
                if reason.strip()
            ).most_common(10)
        ),
        "fallback_rejections_path": str(fallback_rejections_path),
        "output_path": str(out_path),
        "note": "Run build-sharp-anchor next to de-vig these into the fundamental slot. "
                "If the provider fails, fallback_input_paths can feed manually validated bookmaker odds.",
        "budget_reference_at_utc": now_utc() if provider_status == "attempted" and requests_used > 0 else None,
        "generated_at_utc": now_utc(),
    }
    write_json(_fetch_summary_path(cfg), summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return fetch_sharp_odds(load_config(config_path))
