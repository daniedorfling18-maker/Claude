from __future__ import annotations

from collections import defaultdict
from typing import Any

from superbru_score_engine.model.team_names import canonical_team_name

from .base import MatchOdds, MarketOdds, OutcomeOdds


MARKET_ALIASES = {
    "h2h": "h2h",
    "h2h_3_way": "h2h",
    "match_winner": "h2h",
    "1x2": "h2h",
    "totals": "totals",
    "alternate_totals": "totals",
    "over_under": "totals",
    "correct_score": "correct_score",
    "exact_score": "correct_score",
    "score": "correct_score",
}


def normalise_market_key(key: str) -> str:
    return MARKET_ALIASES.get(key.strip().lower(), key.strip().lower())


def _coalesce(*values: Any) -> Any:
    """First value that is genuinely present (not None/empty string).

    Unlike ``a or b``, this keeps a legitimate ``0`` — e.g. a level/pick'em Asian
    handicap line of ``0`` must not be discarded as if it were missing.
    """
    for value in values:
        if value is not None and value != "":
            return value
    return None


def parse_outcome(outcome: dict[str, Any]) -> OutcomeOdds:
    return OutcomeOdds(
        name=str(_coalesce(outcome.get("name"), outcome.get("label"), outcome.get("outcome"))),
        price=float(_coalesce(outcome.get("price"), outcome.get("odds"), outcome.get("decimal_odds"))),
        point=_optional_float(_coalesce(outcome.get("point"), outcome.get("line"), outcome.get("handicap"))),
        description=_optional_str(outcome.get("description")),
    )


def normalise_the_odds_api_events(data: list[dict[str, Any]]) -> list[MatchOdds]:
    matches: list[MatchOdds] = []
    for event in data:
        grouped: dict[str, list[MarketOdds]] = defaultdict(list)
        for bookmaker in event.get("bookmakers", []):
            bookmaker_name = str(bookmaker.get("title") or bookmaker.get("key") or "bookmaker")
            last_update = _optional_str(bookmaker.get("last_update"))
            for market in bookmaker.get("markets", []):
                key = normalise_market_key(str(market.get("key", "")))
                outcomes = tuple(parse_outcome(outcome) for outcome in market.get("outcomes", []))
                if outcomes:
                    grouped[key].append(
                        MarketOdds(
                            key=key,
                            bookmaker=bookmaker_name,
                            outcomes=outcomes,
                            last_update=last_update,
                        )
                    )
        matches.append(
            MatchOdds(
                match_id=str(event.get("id") or f"{event.get('home_team')}-{event.get('away_team')}-{event.get('commence_time')}"),
                commence_time=str(event.get("commence_time") or ""),
                home_team=canonical_team_name(str(event.get("home_team") or event.get("home") or "")),
                away_team=canonical_team_name(str(event.get("away_team") or event.get("away") or "")),
                neutral=bool(event.get("neutral", True)),
                venue_country=_optional_str(event.get("venue_country")),
                stage=_optional_str(event.get("stage")),
                markets={key: tuple(value) for key, value in grouped.items()},
                raw=event,
            )
        )
    return matches


def normalise_generic_events(data: Any) -> list[MatchOdds]:
    if isinstance(data, dict):
        if "match_list" in data and isinstance(data["match_list"], list):
            return normalise_oddspedia_match_list(data)
        if "data" in data and isinstance(data["data"], list):
            data = data["data"]
        elif "events" in data and isinstance(data["events"], list):
            data = data["events"]
        elif "matches" in data and isinstance(data["matches"], list):
            data = data["matches"]
    if not isinstance(data, list):
        raise ValueError("Expected a list of event odds or a wrapper with data/events/matches")
    return normalise_the_odds_api_events(data)


def normalise_oddspedia_match_list(data: dict[str, Any]) -> list[MatchOdds]:
    matches: list[MatchOdds] = []
    for sport in data.get("match_list", []):
        if "id" in sport and ("ht" in sport or "home_team" in sport):
            matches.append(_normalise_flat_oddspedia_match(sport))
            continue
        sport_name = sport.get("sport_name")
        for category in sport.get("categories", []):
            category_name = category.get("category_name")
            for league in category.get("leagues", []):
                league_name = league.get("league_name")
                league_id = league.get("league_id")
                for item in league.get("matches", []):
                    raw = dict(item)
                    raw.update(
                        {
                            "sport_name": sport_name,
                            "category_name": category_name,
                            "league_name": league_name,
                            "league_id": league_id,
                        }
                    )
                    matches.append(
                        MatchOdds(
                            match_id=str(item.get("id") or ""),
                            commence_time=str(item.get("md") or ""),
                            home_team=canonical_team_name(str(item.get("ht") or "")),
                            away_team=canonical_team_name(str(item.get("at") or "")),
                            markets={},
                            neutral=True,
                            venue_country=None,
                            stage=str(league_name or ""),
                            raw=raw,
                        )
                    )
    return matches


def _normalise_flat_oddspedia_match(item: dict[str, Any]) -> MatchOdds:
    return MatchOdds(
        match_id=str(item.get("id") or ""),
        commence_time=str(item.get("md") or item.get("commence_time") or ""),
        home_team=canonical_team_name(str(item.get("ht") or item.get("home_team") or "")),
        away_team=canonical_team_name(str(item.get("at") or item.get("away_team") or "")),
        markets={},
        neutral=True,
        venue_country=None,
        stage=str(item.get("tournament_name") or item.get("league_name") or ""),
        raw=dict(item),
    )


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
