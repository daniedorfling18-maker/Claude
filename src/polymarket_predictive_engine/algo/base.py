"""Strategy protocol for the event-driven algo layer.

Strategies MUST be pure and deterministic given (event, context): no network,
no file I/O, no wall-clock reads, no mutable global state. Anything a strategy
needs must arrive through the :class:`QuoteEvent` or :class:`StrategyContext`.
That discipline is what makes replay results trustworthy: the same recorded
events always produce the same intents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..config import EngineConfig
from ..execution.intents import OrderIntent
from .events import QuoteEvent


@dataclass(frozen=True)
class StrategyContext:
    """Read-only view of the world a strategy is allowed to see."""

    config: EngineConfig
    now_utc: str
    open_positions: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def algo_setting(self, key: str, default: Any = None) -> Any:
        settings = self.config.raw.get("algo", {}) or {}
        return settings.get(key, default)


@runtime_checkable
class AlgoStrategy(Protocol):
    name: str

    def on_quote(self, event: QuoteEvent, context: StrategyContext) -> list[OrderIntent]: ...

    def on_fill(self, fill: dict[str, Any], context: StrategyContext) -> None: ...
