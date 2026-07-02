"""Typed order intents: the single language between strategies and brokers.

An :class:`OrderIntent` is what a strategy *wants* to do, independent of which
broker (shadow ledger, paper broker, replay simulator) executes it. Keeping
one validated schema here means strategies, the replay harness, and the paper
path cannot drift apart.

Safety by construction:

- ``mode`` is restricted to ``"shadow"`` and ``"paper"``. There is **no**
  ``"live"`` mode value in this schema at all; a live order cannot even be
  represented here, let alone routed.
- Adapters never bypass risk: ``intent_from_risk_decision`` only accepts an
  approved :func:`polymarket_predictive_engine.risk.risk_decision` result, and
  ``intent_to_paper_signal`` carries the intent's stake as ``max_stake_usdc``
  so the paper broker still applies its own risk controls on submission.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from ..utils import now_utc, parse_timestamp, safe_float

SIDES = ("BUY", "SELL")
TIME_IN_FORCE = ("IOC", "GTD")
EXECUTION_POLICIES = ("cross_spread", "join_bid", "work_midpoint")
MODES = ("shadow", "paper")  # deliberately no "live"

_ROUND = 6


@dataclass(frozen=True)
class OrderIntent:
    intent_id: str
    created_at_utc: str
    market_id: str
    token_id: str
    side: str
    quantity: float
    limit_price: float
    time_in_force: str
    expire_at_utc: str
    execution_policy: str
    max_slippage: float
    mode: str
    source_strategy: str
    signal_ref: str


def validate_intent(intent: OrderIntent) -> list[str]:
    """Return human-readable violations; an empty list means the intent is valid."""
    violations: list[str] = []
    if not intent.intent_id:
        violations.append("intent_id is empty")
    if not intent.market_id or not intent.token_id:
        violations.append("market_id and token_id are required")
    if intent.side not in SIDES:
        violations.append(f"side must be one of {SIDES}, got {intent.side!r}")
    quantity = safe_float(intent.quantity)
    if quantity is None or quantity <= 0:
        violations.append(f"quantity must be > 0, got {intent.quantity!r}")
    price = safe_float(intent.limit_price)
    if price is None or not 0 < price < 1:
        violations.append(f"limit_price must be in (0, 1) exclusive, got {intent.limit_price!r}")
    if intent.time_in_force not in TIME_IN_FORCE:
        violations.append(f"time_in_force must be one of {TIME_IN_FORCE}, got {intent.time_in_force!r}")
    if intent.time_in_force == "GTD":
        if not intent.expire_at_utc:
            violations.append("GTD intents require expire_at_utc")
        elif parse_timestamp(intent.expire_at_utc) is None:
            violations.append(f"expire_at_utc is not a parseable timestamp: {intent.expire_at_utc!r}")
    if intent.execution_policy not in EXECUTION_POLICIES:
        violations.append(f"execution_policy must be one of {EXECUTION_POLICIES}, got {intent.execution_policy!r}")
    slippage = safe_float(intent.max_slippage)
    if slippage is None or slippage < 0:
        violations.append(f"max_slippage must be >= 0, got {intent.max_slippage!r}")
    if intent.mode not in MODES:
        violations.append(f"mode must be one of {MODES} (live orders cannot be represented), got {intent.mode!r}")
    if not intent.source_strategy:
        violations.append("source_strategy is empty")
    return violations


def require_valid(intent: OrderIntent) -> OrderIntent:
    violations = validate_intent(intent)
    if violations:
        raise ValueError("invalid order intent: " + "; ".join(violations))
    return intent


def downgrade_to_shadow(intent: OrderIntent) -> OrderIntent:
    return intent if intent.mode == "shadow" else replace(intent, mode="shadow")


def intent_to_dict(intent: OrderIntent) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "created_at_utc": intent.created_at_utc,
        "market_id": intent.market_id,
        "token_id": intent.token_id,
        "side": intent.side,
        "quantity": round(float(intent.quantity), _ROUND),
        "limit_price": round(float(intent.limit_price), _ROUND),
        "time_in_force": intent.time_in_force,
        "expire_at_utc": intent.expire_at_utc,
        "execution_policy": intent.execution_policy,
        "max_slippage": round(float(intent.max_slippage), _ROUND),
        "mode": intent.mode,
        "source_strategy": intent.source_strategy,
        "signal_ref": intent.signal_ref,
    }


def intent_from_dict(payload: dict[str, Any]) -> OrderIntent:
    return OrderIntent(
        intent_id=str(payload.get("intent_id", "")),
        created_at_utc=str(payload.get("created_at_utc", "")),
        market_id=str(payload.get("market_id", "")),
        token_id=str(payload.get("token_id", "")),
        side=str(payload.get("side", "")),
        quantity=float(safe_float(payload.get("quantity")) or 0.0),
        limit_price=float(safe_float(payload.get("limit_price")) or 0.0),
        time_in_force=str(payload.get("time_in_force", "")),
        expire_at_utc=str(payload.get("expire_at_utc", "")),
        execution_policy=str(payload.get("execution_policy", "")),
        max_slippage=float(safe_float(payload.get("max_slippage")) or 0.0),
        mode=str(payload.get("mode", "")),
        source_strategy=str(payload.get("source_strategy", "")),
        signal_ref=str(payload.get("signal_ref", "")),
    )


def _stable_intent_id(*parts: str) -> str:
    return "intent_" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:24]


def intent_from_risk_decision(
    signal: dict[str, Any],
    decision: dict[str, Any],
    *,
    mode: str = "shadow",
    source_strategy: str = "risk_decision_bridge",
    created_at_utc: str | None = None,
) -> OrderIntent | None:
    """Build a BUY intent from an approved risk decision, or None when rejected.

    This adapter does not check governance gates itself: passing ``mode="paper"``
    is the caller's assertion that governance already approved the originating
    signal. Anything else should stay ``"shadow"`` (the default).
    """
    if not decision.get("approved"):
        return None
    created = created_at_utc or now_utc()
    market_id = str(signal.get("market_id", ""))
    token_id = str(signal.get("token_id", ""))
    prediction_timestamp = str(signal.get("data_snapshot_timestamp") or signal.get("prediction_timestamp") or "")
    slippage = safe_float(signal.get("slippage"))
    if slippage is None:
        slippage = safe_float(signal.get("expected_slippage")) or 0.0
    intent = OrderIntent(
        intent_id=_stable_intent_id(market_id, token_id, prediction_timestamp, source_strategy, mode),
        created_at_utc=created,
        market_id=market_id,
        token_id=token_id,
        side="BUY",
        quantity=float(decision.get("quantity") or 0.0),
        limit_price=float(decision.get("limit_price") or 0.0),
        time_in_force="IOC",
        expire_at_utc="",
        execution_policy="cross_spread",
        max_slippage=float(slippage),
        mode=mode,
        source_strategy=source_strategy,
        signal_ref=f"{market_id}|{token_id}|{prediction_timestamp}",
    )
    return require_valid(intent)


def intent_to_paper_signal(intent: OrderIntent) -> dict[str, Any]:
    """Shape an intent like the signal rows the paper broker reads today.

    The stake travels as ``max_stake_usdc`` so ``submit_paper_signal`` still
    runs its own ``risk_decision`` and can only size smaller, never larger.
    """
    require_valid(intent)
    return {
        "market_id": intent.market_id,
        "token_id": intent.token_id,
        "side": "BUY_YES" if intent.side == "BUY" else "SELL_YES",
        "prediction_timestamp": intent.created_at_utc,
        "strategy_name": intent.source_strategy,
        "model_version": intent.source_strategy,
        "executable_price": round(float(intent.limit_price), _ROUND),
        "slippage": round(float(intent.max_slippage), _ROUND),
        "max_stake_usdc": round(float(intent.quantity) * float(intent.limit_price), _ROUND),
        "source_intent_id": intent.intent_id,
        "source_intent_mode": intent.mode,
    }
