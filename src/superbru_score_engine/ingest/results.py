from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from superbru_score_engine.config import env_value
from superbru_score_engine.model.ratings import MatchResult
from superbru_score_engine.model.team_names import canonical_team_name

from .base import MatchOdds, ProviderUnavailable
from .cache import JsonCache
from .http import get_json
from .normalise import normalise_generic_events


@dataclass
class OddspediaResultsProvider:
    config: dict[str, Any]
    cache: JsonCache | None = None

    name: str = "oddspedia_results"

    def fetch_results(self) -> list[MatchResult]:
        base_url = str(self.config.get("base_url") or "").strip()
        if not base_url:
            raise ProviderUnavailable("Oddspedia results endpoint is not configured")

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
        return results_from_json(data, self.config)


def load_results_csv(path: str | Path) -> list[MatchResult]:
    frame = pd.read_csv(path).fillna("")
    results: list[MatchResult] = []
    for _, row in frame.iterrows():
        home_goals = _score_value(row, ("home_goals", "hscore", "home_score"))
        away_goals = _score_value(row, ("away_goals", "ascore", "away_score"))
        if home_goals is None or away_goals is None:
            continue
        results.append(
            MatchResult(
                match_id=_optional_text(row.get("match_id")),
                commence_time=_optional_text(row.get("commence_time") or row.get("md")),
                home_team=canonical_team_name(str(row["home_team"])),
                away_team=canonical_team_name(str(row["away_team"])),
                home_goals=home_goals,
                away_goals=away_goals,
                venue_country=_optional_text(row.get("venue_country")),
            )
        )
    return results


def load_results_json(path: str | Path, config: dict[str, Any] | None = None) -> list[MatchResult]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "_cached_at" in payload and "data" in payload:
        payload = payload["data"]
    return results_from_json(payload, config or {})


def results_from_json(data: Any, config: dict[str, Any] | None = None) -> list[MatchResult]:
    config = config or {}
    try:
        matches = normalise_generic_events(data)
    except (TypeError, ValueError, KeyError) as exc:
        raise ProviderUnavailable("Results response did not match a supported fixture/results schema") from exc

    results = [
        result
        for match in matches
        if (result := result_from_match(match, config)) is not None
    ]
    return filter_results(results, config)


def result_from_match(match: MatchOdds, config: dict[str, Any] | None = None) -> MatchResult | None:
    config = config or {}
    raw = match.raw
    home_goals = _score_value(raw, ("home_goals", "hscore", "home_score", "homeScore"))
    away_goals = _score_value(raw, ("away_goals", "ascore", "away_score", "awayScore"))
    if home_goals is None or away_goals is None:
        return None
    if not _is_completed(raw, config):
        return None
    return MatchResult(
        match_id=_optional_text(raw.get("match_id") or raw.get("id") or match.match_id),
        commence_time=_optional_text(raw.get("commence_time") or raw.get("md") or match.commence_time),
        home_team=canonical_team_name(match.home_team),
        away_team=canonical_team_name(match.away_team),
        home_goals=home_goals,
        away_goals=away_goals,
        venue_country=_optional_text(raw.get("venue_country")),
    )


def filter_results(results: Iterable[MatchResult], config: dict[str, Any]) -> list[MatchResult]:
    minimum = _parse_time(config.get("min_commence_time"))
    maximum = _parse_time(config.get("max_commence_time"))
    filtered: list[MatchResult] = []
    for result in results:
        when = _parse_time(result.commence_time)
        if (minimum or maximum) and when is None:
            continue
        if minimum and when and when < minimum:
            continue
        if maximum and when and when > maximum:
            continue
        filtered.append(result)
    return filtered


def result_rows(results: Iterable[MatchResult]) -> list[dict[str, Any]]:
    return [
        {
            "match_id": result.match_id or "",
            "commence_time": result.commence_time or "",
            "home_team": result.home_team,
            "away_team": result.away_team,
            "home_goals": result.home_goals,
            "away_goals": result.away_goals,
            "venue_country": result.venue_country or "",
        }
        for result in results
    ]


def write_results_csv(results: list[MatchResult], out_dir: str | Path) -> Path:
    output_dir = Path(out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "results.csv"
    pd.DataFrame(result_rows(results)).to_csv(path, index=False)
    return path


def results_console_table(results: list[MatchResult], limit: int = 40) -> str:
    rows = result_rows(results[:limit])
    if not rows:
        return "No completed results found."
    return pd.DataFrame(rows)[
        ["commence_time", "home_team", "away_team", "home_goals", "away_goals"]
    ].to_string(index=False)


def _is_completed(raw: Any, config: dict[str, Any]) -> bool:
    statuses = config.get("completed_statuses", (16, "16", "finished", "complete", "completed", "full-time", "ft"))
    status = raw.get("status") if hasattr(raw, "get") else None
    if status in ("", None):
        return True
    status_text = str(status).strip().lower()
    return status in statuses or status_text in {str(item).strip().lower() for item in statuses}


def _score_value(source: Any, keys: tuple[str, ...]) -> int | None:
    getter = source.get if hasattr(source, "get") else None
    if getter is None:
        return None
    for key in keys:
        value = getter(key)
        if value in ("", None):
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            continue
    return None


def _optional_text(value: Any) -> str | None:
    if value in ("", None):
        return None
    return str(value)


def _parse_time(value: Any) -> datetime | None:
    if value in ("", None):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
