from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderUnavailable(RuntimeError):
    """Raised when a configured provider cannot supply structured odds."""


@dataclass(frozen=True)
class OutcomeOdds:
    name: str
    price: float
    point: float | None = None
    description: str | None = None


@dataclass(frozen=True)
class MarketOdds:
    key: str
    bookmaker: str
    outcomes: tuple[OutcomeOdds, ...]
    last_update: str | None = None


@dataclass(frozen=True)
class MatchOdds:
    match_id: str
    commence_time: str
    home_team: str
    away_team: str
    markets: dict[str, tuple[MarketOdds, ...]]
    neutral: bool = True
    venue_country: str | None = None
    stage: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def market(self, key: str) -> tuple[MarketOdds, ...]:
        return self.markets.get(key, ())


class OddsProvider(Protocol):
    name: str

    def fetch_odds(self) -> list[MatchOdds]:
        ...

