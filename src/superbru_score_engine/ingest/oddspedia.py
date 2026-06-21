from __future__ import annotations

from dataclasses import dataclass

from superbru_score_engine.config import env_value

from .base import MatchOdds, ProviderUnavailable
from .cache import JsonCache
from .http import get_json
from .normalise import normalise_generic_events


@dataclass
class OddspediaProvider:
    config: dict
    cache: JsonCache | None = None

    name: str = "oddspedia"

    def fetch_odds(self) -> list[MatchOdds]:
        base_url = str(self.config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderUnavailable("Oddspedia JSON endpoint is not configured")

        headers = {
            "Accept": "application/json",
            "User-Agent": str(
                self.config.get(
                    "user_agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                )
            ),
        }
        headers.update({str(key): str(value) for key, value in dict(self.config.get("headers", {})).items()})
        api_key = env_value(self.config)
        params = dict(self.config.get("params", {}))
        api_key_param = str(self.config.get("api_key_param") or "").strip()
        auth_header = self.config.get("auth_header", "Authorization")
        if api_key_param and not api_key:
            env_name = self.config.get("api_key_env", "ODDSPEDIA_API_TOKEN")
            raise ProviderUnavailable(f"{env_name} is not set in this PowerShell session")
        if api_key and api_key_param:
            params[api_key_param] = api_key
        elif api_key:
            if auth_header.lower() == "authorization":
                headers[auth_header] = f"Bearer {api_key}"
            else:
                headers[auth_header] = api_key

        data = get_json(
            base_url,
            params=params,
            headers=headers,
            cache=self.cache,
            namespace=self.name,
            rate_limit_seconds=float(self.config.get("rate_limit_seconds", 0.0)),
        )
        try:
            matches = normalise_generic_events(data)
        except (TypeError, ValueError, KeyError) as exc:
            raise ProviderUnavailable("Oddspedia response was JSON but did not match a supported odds schema") from exc
        if matches and not any(match.markets for match in matches):
            raise ProviderUnavailable("Oddspedia endpoint returned fixtures/match IDs but no bookmaker odds")
        return matches
