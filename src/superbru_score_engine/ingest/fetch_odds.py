from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from superbru_score_engine.config import AppConfig, env_value
from superbru_score_engine.ingest.cache import JsonCache
from superbru_score_engine.ingest.http import get_json
from superbru_score_engine.ingest.normalise import normalise_the_odds_api_events


FIXTURE_COLUMNS = (
    "match_id",
    "commence_time",
    "home_team",
    "away_team",
    "neutral",
    "venue_country",
    "stage",
    "dead_rubber_flag",
    "manual_override_scoreline",
)


def run_fetch_odds(args, config: AppConfig) -> int:
    provider_config = dict(config.providers.the_odds_api)
    _override_if_supplied(provider_config, "sport_key", args.sport)
    _override_if_supplied(provider_config, "regions", args.regions)
    _override_if_supplied(provider_config, "markets", args.markets)
    _override_if_supplied(provider_config, "bookmakers", args.bookmakers)
    _override_if_supplied(provider_config, "commence_time_from", args.commence_time_from)
    _override_if_supplied(provider_config, "commence_time_to", args.commence_time_to)
    _override_if_supplied(provider_config, "odds_format", args.odds_format)

    api_key = args.api_key or env_value(provider_config)
    if not api_key:
        raise ValueError(
            "The Odds API key is not configured. Set THE_ODDS_API_KEY or pass --api-key. "
            "Avoid committing API keys to config.yaml."
        )

    sport_key = provider_config.get("sport_key", "soccer_fifa_world_cup")
    base_url = provider_config.get("base_url", "https://api.the-odds-api.com")
    endpoint = f"{base_url.rstrip('/')}/v4/sports/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "regions": provider_config.get("regions", "eu,uk,us,au"),
        "markets": provider_config.get("markets", "h2h,totals"),
        "oddsFormat": provider_config.get("odds_format", "decimal"),
        "dateFormat": "iso",
        "commenceTimeFrom": provider_config.get("commence_time_from"),
        "commenceTimeTo": provider_config.get("commence_time_to"),
        "bookmakers": provider_config.get("bookmakers"),
    }

    cache = None if args.no_cache else JsonCache(config.paths.cache_dir)
    data = get_json(
        endpoint,
        params=params,
        cache=cache,
        namespace="the_odds_api_live_fetch",
        rate_limit_seconds=float(provider_config.get("rate_limit_seconds", 0.0)),
    )
    if not isinstance(data, list):
        raise ValueError("The Odds API response was not a list of events")

    odds_path = Path(args.out_odds)
    fixtures_path = Path(args.out_fixtures)
    odds_path.parent.mkdir(parents=True, exist_ok=True)
    fixtures_path.parent.mkdir(parents=True, exist_ok=True)
    odds_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    matches = normalise_the_odds_api_events(data)
    rows = [
        {
            "match_id": match.match_id,
            "commence_time": match.commence_time,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "neutral": args.neutral,
            "venue_country": args.venue_country or match.venue_country or "",
            "stage": args.stage or match.stage or "",
            "dead_rubber_flag": False,
            "manual_override_scoreline": "",
        }
        for match in matches
    ]
    pd.DataFrame(rows, columns=FIXTURE_COLUMNS).to_csv(fixtures_path, index=False)

    print(f"Fetched {len(data)} event(s) from The Odds API.")
    print(f"Wrote {fixtures_path}")
    print(f"Wrote {odds_path}")
    if not data:
        print("No events were returned. Check the sport key, date filters, regions, markets, and whether the event is currently listed.")
    return 0


def _override_if_supplied(config: dict, key: str, value: str | None) -> None:
    if value not in (None, ""):
        config[key] = value
