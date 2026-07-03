from __future__ import annotations

import re
from typing import Any

from .config import EngineConfig, kill_switch_active
from .execution_costs import estimate_execution_cost
from .utils import boolish, safe_float


FAST_UPDOWN_SLUG_RE = re.compile(r"-updown-(?:5m|15m)-\d+", re.IGNORECASE)


def kelly_fraction(probability: float, price: float, cap: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    b = (1 - price) / price
    q = 1 - probability
    raw = (b * probability - q) / b if b else 0.0
    return max(0.0, min(cap, raw))


def shrunk_kelly_fraction(probability: float, price: float, cap: float, *, shrinkage: float = 0.0) -> float:
    """Kelly with the model probability shrunk toward the market-implied price.

    Shrinkage in [0, 1] pulls the probability toward the no-edge prior (the
    market price) before sizing, hedging estimation error in the model
    probability. shrinkage=0 reproduces kelly_fraction exactly; any positive
    value only ever sizes smaller, so this can tighten sizing but never relax it.
    """
    if price <= 0 or price >= 1:
        return 0.0
    s = max(0.0, min(1.0, shrinkage))
    shrunk_probability = price + (probability - price) * (1.0 - s)
    return min(
        kelly_fraction(probability, price, cap),
        kelly_fraction(shrunk_probability, price, cap),
    )


def _as_set(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, (list, tuple, set)):
        return {str(value) for value in values if str(value)}
    return {str(values)}


def _number(mapping: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    value = safe_float((mapping or {}).get(key))
    return default if value is None else value


def _time_to_close_minutes(signal: dict[str, Any]) -> float | None:
    value = safe_float(signal.get("time_to_close_minutes"))
    if value is not None:
        return value
    hours = safe_float(signal.get("time_to_close_hours"))
    if hours is None:
        hours = safe_float(signal.get("time_to_close"))
    return None if hours is None else hours * 60


def _directional_confidence(probability: float) -> float:
    return max(0.0, min(1.0, 2.0 * abs(probability - 0.5)))


def _is_fast_market_signal(signal: dict[str, Any]) -> bool:
    slug = str(signal.get("market_slug") or "").strip().lower()
    return bool(FAST_UPDOWN_SLUG_RE.search(slug))


def _is_price_action_signal(signal: dict[str, Any]) -> bool:
    return boolish(signal.get("price_action_signal")) or str(signal.get("strategy_name") or "") == "price_action_round_trip"


def _risk_value(
    risk: dict[str, Any],
    fast_overrides: dict[str, Any],
    fast_market: bool,
    price_action_overrides: dict[str, Any],
    price_action: bool,
    key: str,
    default: float,
) -> float:
    if price_action and key in price_action_overrides:
        source = price_action_overrides
    elif fast_market and key in fast_overrides:
        source = fast_overrides
    else:
        source = risk
    return float(source.get(key, default))


def _fast_market_stake_cap(fast_overrides: dict[str, Any], signal: dict[str, Any], fast_market: bool) -> float | None:
    if not fast_market:
        return None
    cap = safe_float(fast_overrides.get("maximum_stake_usdc"))
    promoted = str(signal.get("cohort_promotion_status") or "").strip().lower() == "promoted"
    if promoted:
        promoted_cap = safe_float(fast_overrides.get("promoted_maximum_stake_usdc"))
        if promoted_cap is not None and promoted_cap > 0:
            return promoted_cap
    return cap


def _override_stake_cap(overrides: dict[str, Any], signal: dict[str, Any], active: bool) -> float | None:
    if not active:
        return None
    cap = safe_float(overrides.get("maximum_stake_usdc"))
    promoted = str(signal.get("cohort_promotion_status") or signal.get("price_action_evidence_status") or "").strip().lower() in {
        "promoted",
        "cohort_bid_ask_round_trip_approved",
    }
    if promoted:
        promoted_cap = safe_float(overrides.get("promoted_maximum_stake_usdc"))
        if promoted_cap is not None and promoted_cap > 0:
            return promoted_cap
    return cap


def risk_decision(
    cfg: EngineConfig,
    signal: dict[str, Any],
    portfolio: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply every configured pre-trade control and return explicit USDC/share units."""
    risk = cfg.raw.get("risk", {})
    fast_overrides = risk.get("fast_market_overrides", {}) or {}
    price_action_overrides = risk.get("price_action_overrides", {}) or {}
    fast_market = boolish(fast_overrides.get("enabled", False)) and _is_fast_market_signal(signal)
    price_action = boolish(price_action_overrides.get("enabled", False)) and _is_price_action_signal(signal)
    portfolio = portfolio or {}
    bankroll = _number(portfolio, "bankroll", float(risk.get("bankroll", 1000)))
    cash = _number(portfolio, "cash", bankroll)
    edge = _number(signal, "edge")
    spread = _number(signal, "spread", float("inf"))
    liquidity = _number(signal, "liquidity")
    price = safe_float(signal.get("executable_price"))
    if price is None:
        price = safe_float(signal.get("market_price"))
    probability = safe_float(signal.get("alpha_probability"))
    if probability is None:
        probability = safe_float(signal.get("calibrated_probability"))
    if probability is None:
        probability = safe_float(signal.get("model_probability"))
    confidence = safe_float(signal.get("model_confidence"))
    if confidence is None:
        confidence = safe_float(signal.get("confidence"))
    if confidence is None and probability is not None:
        confidence = _directional_confidence(probability)
    confidence = 0.0 if confidence is None else confidence
    flat_slippage = _number(signal, "slippage", _number(signal, "expected_slippage"))
    slippage = flat_slippage
    resolution_risk = _number(signal, "resolution_risk")
    time_to_close_minutes = _time_to_close_minutes(signal)

    def reject(reason: str, max_size: float = 0.0) -> dict[str, Any]:
        return {
            "approved": False,
            "reason": reason,
            "size": 0.0,
            "stake_usdc": 0.0,
            "quantity": 0.0,
            "limit_price": price if price is not None and 0 < price < 1 else 0.0,
            "max_size": round(max_size, 6),
        }

    if kill_switch_active():
        return reject("kill switch active")
    if price is None or not 0 < price < 1:
        return reject("invalid executable price")
    # Entry-price band: buying near-certain favourites (e.g. 0.95 on a
    # short-horizon binary) risks the full stake to win pennies and needs a
    # win rate the market already prices in - no room for edge after spread.
    # Deep longshots carry outsized resolution ambiguity. Base-config only on
    # purpose: override profiles must not be able to widen the band.
    minimum_entry_price = float(risk.get("minimum_entry_price", 0.05))
    maximum_entry_price = float(risk.get("maximum_entry_price", 0.90))
    if not minimum_entry_price <= price <= maximum_entry_price:
        return reject(
            f"entry price {price:.4f} outside configured band [{minimum_entry_price:.2f}, {maximum_entry_price:.2f}]"
        )
    if probability is None or not 0 <= probability <= 1:
        return reject("invalid model probability")
    if str(signal.get("market_id", "")) in _as_set(risk.get("market_blacklist")):
        return reject("market is blacklisted")
    if str(signal.get("event_id", "")) in _as_set(risk.get("event_blacklist")):
        return reject("event is blacklisted")
    if str(signal.get("category", "")) in _as_set(risk.get("category_blacklist")):
        return reject("category is blacklisted")

    checks = [
        (edge >= _risk_value(risk, fast_overrides, fast_market, price_action_overrides, price_action, "minimum_edge", 0.03), "edge below minimum"),
        (
            confidence >= _risk_value(risk, fast_overrides, fast_market, price_action_overrides, price_action, "minimum_confidence", 0.65),
            "confidence below minimum",
        ),
        (
            spread <= _risk_value(risk, fast_overrides, fast_market, price_action_overrides, price_action, "maximum_spread", 0.08),
            "spread above maximum",
        ),
        (
            liquidity >= _risk_value(risk, fast_overrides, fast_market, price_action_overrides, price_action, "minimum_liquidity", 50),
            "liquidity below minimum",
        ),
        (resolution_risk <= float(risk.get("maximum_resolution_risk", 0.25)), "resolution risk above maximum"),
        (
            _number(portfolio, "daily_loss") <= float(risk.get("maximum_daily_loss", 0.03)) * bankroll,
            "daily loss above maximum",
        ),
        (
            _number(portfolio, "drawdown") <= float(risk.get("maximum_drawdown", 0.1)),
            "drawdown above maximum",
        ),
        (
            _number(portfolio, "open_orders") < float(risk.get("maximum_open_orders", 10)),
            "open orders above maximum",
        ),
        (
            _number(portfolio, "order_rate_per_minute") < float(risk.get("maximum_order_rate_per_minute", 3)),
            "order rate above maximum",
        ),
    ]
    minimum_time = _risk_value(
        risk,
        fast_overrides,
        fast_market,
        price_action_overrides,
        price_action,
        "minimum_time_to_close_minutes",
        15,
    )
    require_time = str(risk.get("require_time_to_close", False)).strip().lower() in {"1", "true", "yes"}
    if time_to_close_minutes is None:
        if require_time:
            checks.append((False, "time to close missing"))
    else:
        checks.append((time_to_close_minutes >= minimum_time, "time to close below minimum"))

    for passed, reason in checks:
        if not passed:
            return reject(reason)

    max_single = float(risk.get("maximum_single_market_exposure", 0.02)) * bankroll
    max_category = float(risk.get("maximum_category_exposure", 0.1)) * bankroll
    max_correlated = float(risk.get("maximum_correlated_exposure", 0.1)) * bankroll
    current_market = _number(portfolio, "current_market_exposure")
    current_category = _number(portfolio, "current_category_exposure")
    current_correlated = _number(portfolio, "current_correlated_exposure")
    liquidity_cap = liquidity * float(risk.get("liquidity_cap_fraction", 0.05))
    kelly_shrinkage = _risk_value(risk, fast_overrides, fast_market, price_action_overrides, price_action, "kelly_shrinkage", 0.0)
    kelly = shrunk_kelly_fraction(probability, price, float(risk.get("kelly_cap", 0.005)), shrinkage=kelly_shrinkage)
    signal_cap = safe_float(signal.get("max_stake_usdc"))
    fast_market_cap = _fast_market_stake_cap(fast_overrides, signal, fast_market)
    price_action_cap = _override_stake_cap(price_action_overrides, signal, price_action)
    stake_usdc = min(
        kelly * bankroll,
        max(0.0, max_single - current_market),
        max(0.0, max_category - current_category),
        max(0.0, max_correlated - current_correlated),
        liquidity_cap,
        cash,
        fast_market_cap if fast_market_cap is not None and fast_market_cap > 0 else cash,
        price_action_cap if price_action_cap is not None and price_action_cap > 0 else cash,
        signal_cap if signal_cap is not None and signal_cap > 0 else cash,
    )
    execution_estimate = estimate_execution_cost(
        signal,
        stake_usdc=stake_usdc,
        flat_slippage=flat_slippage,
    )
    impact_cap = safe_float(execution_estimate.get("max_stake_at_acceptable_impact_usdc"))
    if impact_cap is not None and impact_cap > 0:
        stake_usdc = min(stake_usdc, impact_cap)
        execution_estimate = estimate_execution_cost(
            signal,
            stake_usdc=stake_usdc,
            flat_slippage=flat_slippage,
        )
    slippage = safe_float(execution_estimate.get("expected_slippage"))
    slippage = flat_slippage if slippage is None else slippage
    if slippage > float(risk.get("maximum_slippage", 0.02)):
        return reject("slippage above maximum")
    if stake_usdc <= 0:
        return reject("kelly sizing or exposure capacity is zero", max_single)
    quantity = stake_usdc / price
    return {
        "approved": True,
        "reason": "risk controls approved",
        "size": round(stake_usdc, 6),
        "stake_usdc": round(stake_usdc, 6),
        "quantity": round(quantity, 6),
        "limit_price": round(price, 6),
        "max_size": round(max_single, 6),
        "kelly_fraction": round(kelly, 8),
        "kelly_shrinkage": round(kelly_shrinkage, 4),
        "risk_profile": (
            "price_action_paper_probe"
            if price_action
            else (
                "fast_market_promoted"
                if str(signal.get("cohort_promotion_status") or "").strip().lower() == "promoted"
                else "fast_market_paper_probe"
            )
            if fast_market
            else "standard"
        ),
        "risk_checks": {
            "time_to_close_minutes": time_to_close_minutes,
            "resolution_risk": resolution_risk,
            "slippage": slippage,
            "execution_cost_estimate": execution_estimate,
            "execution_depth_stake_cap_usdc": impact_cap if impact_cap is not None else "",
            "category_capacity_remaining": max(0.0, max_category - current_category),
            "correlated_capacity_remaining": max(0.0, max_correlated - current_correlated),
            "signal_stake_cap_usdc": signal_cap if signal_cap is not None else "",
        },
    }
