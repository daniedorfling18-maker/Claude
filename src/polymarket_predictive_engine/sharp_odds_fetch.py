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

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import requests

from .config import EngineConfig, load_config
from .utils import normalize_slug, now_utc, safe_float, write_csv, write_json

DEFAULT_BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_BOOKMAKER_PRIORITY = ("pinnacle", "betfair_ex_eu", "betfair_ex_uk", "betfair")


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


def fetch_sharp_odds(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    base_url = str(settings.get("base_url", DEFAULT_BASE_URL)).rstrip("/")
    api_key = os.getenv(str(settings.get("api_key_env", "THE_ODDS_API_KEY")), "").strip()
    sports = list(settings.get("sports", []) or [])
    regions = str(settings.get("regions", "eu,uk"))
    market_key = str(settings.get("markets", "h2h"))
    odds_format = str(settings.get("odds_format", "decimal"))
    priority = tuple(settings.get("bookmaker_priority", DEFAULT_BOOKMAKER_PRIORITY))
    timeout = int(settings.get("request_timeout_seconds", 20))
    out_path = Path(settings.get("output_path") or "inputs/polymarket/sharp_odds.csv")

    if not api_key:
        summary = {"status": "missing_api_key", "api_key_env": settings.get("api_key_env", "THE_ODDS_API_KEY"),
                   "rows": 0, "note": "Set the odds API key in the environment to enable fetching.",
                   "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "sharp_odds_fetch_summary.json", summary)
        return summary
    if not sports:
        summary = {"status": "no_sports_configured", "rows": 0,
                   "note": "Add sport keys under sharp_odds_fetch.sports.", "generated_at_utc": now_utc()}
        write_json(cfg.governance_root / "sharp_odds_fetch_summary.json", summary)
        return summary

    rows: list[dict[str, Any]] = []
    per_sport: list[dict[str, Any]] = []
    for sport in sports:
        try:
            response = requests.get(
                f"{base_url}/sports/{sport}/odds",
                params={"apiKey": api_key, "regions": regions, "markets": market_key,
                        "oddsFormat": odds_format, "bookmakers": ",".join(priority)},
                timeout=timeout,
            )
            response.raise_for_status()
            events = response.json()
            sport_rows = parse_odds_api_events(events, bookmaker_priority=priority, market_key=market_key)
            rows.extend(sport_rows)
            per_sport.append({"sport": sport, "status": "ok", "events": len(events) if isinstance(events, list) else 0,
                              "rows": len(sport_rows),
                              "requests_remaining": response.headers.get("x-requests-remaining", "")})
        except Exception as exc:  # noqa: BLE001 - network/parse errors are per-sport, not fatal
            per_sport.append({"sport": sport, "status": "error", "error": redact_fetch_error(exc, api_key), "rows": 0})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out_path, rows, fieldnames=["market_slug", "outcome", "decimal_odds", "bookmaker",
                                          "sport", "sport_title", "market_key", "commence_time",
                                          "home_team", "away_team"])
    books_used = sorted({str(r.get("bookmaker", "")) for r in rows if r.get("bookmaker")})
    error_count = sum(1 for item in per_sport if item.get("status") == "error")
    if per_sport and error_count == len(per_sport):
        status = "error"
    elif error_count:
        status = "partial"
    else:
        status = "fetched"
    summary = {
        "status": status,
        "rows": len(rows),
        "markets": len({r["market_slug"] for r in rows}),
        "books_used": books_used,
        "errors": error_count,
        "per_sport": per_sport,
        "output_path": str(out_path),
        "note": "Run build-sharp-anchor next to de-vig these into the fundamental slot.",
        "generated_at_utc": now_utc(),
    }
    write_json(cfg.governance_root / "sharp_odds_fetch_summary.json", summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return fetch_sharp_odds(load_config(config_path))
