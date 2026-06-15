from __future__ import annotations

from dataclasses import dataclass

from superbru_score_engine.config import env_value

from .base import MatchOdds, ProviderUnavailable
from .cache import JsonCache
from .http import get_json
from .normalise import normalise_the_odds_api_events


@dataclass
class TheOddsAPIProvider:
    config: dict
    cache: JsonCache | None = None

    name: str = "the_odds_api"

    def fetch_odds(self) -> list[MatchOdds]:
        api_key = env_value(self.config)
        if not api_key:
            raise ProviderUnavailable("The Odds API key is not configured")

        sport_key = self.config.get("sport_key", "soccer_fifa_world_cup")
        base_url = self.config.get("base_url", "https://api.the-odds-api.com")
        endpoint = f"{base_url.rstrip('/')}/v4/sports/{sport_key}/odds/"
        params = {
            "apiKey": api_key,
            "regions": self.config.get("regions", "eu,uk,us,au"),
            "markets": self.config.get("markets", "h2h,totals"),
            "oddsFormat": self.config.get("odds_format", "decimal"),
            "dateFormat": "iso",
            "commenceTimeFrom": self.config.get("commence_time_from"),
            "commenceTimeTo": self.config.get("commence_time_to"),
            "bookmakers": self.config.get("bookmakers"),
        }
        data = get_json(
            endpoint,
            params=params,
            cache=self.cache,
            namespace=self.name,
            rate_limit_seconds=float(self.config.get("rate_limit_seconds", 0.0)),
        )
        if not isinstance(data, list):
            raise ProviderUnavailable("The Odds API response was not a list of events")
        return normalise_the_odds_api_events(data)

