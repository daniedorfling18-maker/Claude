"""Strategy registry and the shadow-enforcement wrapper.

The registry is the only sanctioned way to obtain a strategy: everything it
returns has its emitted intents validated, and any non-shadow intent is
downgraded to shadow unless the config explicitly allows paper intents AND
lists the strategy as paper-approved. Fail closed: the default configuration
downgrades everything to shadow.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from ..execution.intents import OrderIntent, downgrade_to_shadow, require_valid
from ..utils import boolish
from .base import AlgoStrategy, StrategyContext
from .events import QuoteEvent

_REGISTRY: dict[str, type] = {}


def register(strategy_cls: type) -> type:
    instance_name = getattr(strategy_cls, "name", "")
    if not instance_name:
        raise ValueError(f"strategy class {strategy_cls!r} has no name")
    _REGISTRY[str(instance_name)] = strategy_cls
    return strategy_cls


def available_strategies() -> list[str]:
    return sorted(_REGISTRY)


def get_strategy(name: str) -> AlgoStrategy:
    if name not in _REGISTRY:
        raise KeyError(f"unknown algo strategy {name!r}; available: {available_strategies()}")
    return _REGISTRY[name]()


def _paper_intents_allowed(context: StrategyContext, strategy_name: str) -> bool:
    if not boolish(context.algo_setting("allow_paper_intents", False)):
        return False
    approved = context.algo_setting("paper_approved_strategies", []) or []
    if not isinstance(approved, (list, tuple, set)):
        approved = [approved]
    return strategy_name in {str(item) for item in approved}


def emit_intents(
    strategy: AlgoStrategy,
    event: QuoteEvent,
    context: StrategyContext,
) -> tuple[list[OrderIntent], list[dict[str, Any]]]:
    """Run a strategy on one event, enforcing validity and shadow-by-default.

    Returns (intents, notes). Every returned intent is valid; any intent the
    strategy emitted in non-shadow mode is downgraded to shadow unless the
    config explicitly approves the strategy for paper, and the downgrade is
    recorded in notes.
    """
    intents: list[OrderIntent] = []
    notes: list[dict[str, Any]] = []
    for intent in strategy.on_quote(event, context):
        require_valid(intent)
        if intent.mode != "shadow" and not _paper_intents_allowed(context, strategy.name):
            notes.append(
                {
                    "intent_id": intent.intent_id,
                    "action": "downgraded_to_shadow",
                    "reason": "algo.allow_paper_intents is off or strategy is not paper-approved",
                    "requested_mode": intent.mode,
                }
            )
            intent = downgrade_to_shadow(intent)
        intents.append(intent)
    return intents, notes


def _stable_intent_id(*parts: str) -> str:
    return "intent_" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


@register
class NullStrategy:
    """The safety baseline: consumes every event, never trades."""

    name = "null"

    def on_quote(self, event: QuoteEvent, context: StrategyContext) -> list[OrderIntent]:
        return []

    def on_fill(self, fill: dict[str, Any], context: StrategyContext) -> None:
        return None


@register
class TightSpreadJoinBidShadow:
    """Shadow probe of the tight-book bid-momentum hypothesis.

    When the book is tight and bid-heavy, rest a small shadow BUY at the
    current best bid (join, never cross). This exists to generate replayable
    shadow evidence for an existing microstructure hypothesis; it emits
    shadow-mode intents only.
    """

    name = "tight_spread_join_bid_shadow"

    def on_quote(self, event: QuoteEvent, context: StrategyContext) -> list[OrderIntent]:
        if event.best_bid is None or event.best_ask is None or event.spread is None:
            return []
        max_spread = float(context.algo_setting("tight_spread_maximum", 0.02))
        min_imbalance = float(context.algo_setting("minimum_book_imbalance", 0.6))
        stake = float(context.algo_setting("shadow_stake_usdc", 1.0))
        ttl_minutes = float(context.algo_setting("join_bid_ttl_minutes", 30.0))
        if event.spread > max_spread:
            return []
        if event.book_imbalance is None or event.book_imbalance < min_imbalance:
            return []
        if stake <= 0 or event.best_bid <= 0:
            return []
        expire = (event.timestamp + timedelta(minutes=ttl_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = OrderIntent(
            intent_id=_stable_intent_id(self.name, event.asset_id, event.timestamp_utc),
            created_at_utc=event.timestamp_utc,
            market_id=event.market_id,
            token_id=event.asset_id,
            side="BUY",
            quantity=round(stake / event.best_bid, 6),
            limit_price=event.best_bid,
            time_in_force="GTD",
            expire_at_utc=expire,
            execution_policy="join_bid",
            max_slippage=0.0,
            mode="shadow",
            source_strategy=self.name,
            signal_ref=f"{event.market_id}|{event.asset_id}|{event.timestamp_utc}",
        )
        return [intent]

    def on_fill(self, fill: dict[str, Any], context: StrategyContext) -> None:
        return None
