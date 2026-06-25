from __future__ import annotations

from typing import Any

from .config import EngineConfig, kill_switch_active
from .utils import safe_float


def kelly_fraction(probability: float, price: float, cap: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    b = (1 - price) / price
    q = 1 - probability
    raw = (b * probability - q) / b if b else 0.0
    return max(0.0, min(cap, raw))


def _as_set(values: Any) -> set[str]:
    if not values:
        return set()
    if isinstance(values, (list, tuple, set)):
        return {str(v) for v in values if str(v)}
    return {str(values)}


def _time_to_close_minutes(signal: dict[str, Any]) -> float | None:
    value = safe_float(signal.get("time_to_close_minutes"))
    if value is not None:
        return value
    hours = safe_float(signal.get("time_to_close_hours")) or safe_float(signal.get("time_to_close"))
    return hours * 60 if hours is not None else None


def _portfolio_number(portfolio: dict[str, Any] | None, key: str, default: float = 0.0) -> float:
    return safe_float((portfolio or {}).get(key)) or default


def _directional_confidence(probability: float) -> float:
    return max(0.0, min(1.0, 2 * abs(probability - 0.5)))


def risk_decision(cfg: EngineConfig, signal: dict[str, Any], portfolio: dict[str, Any] | None = None) -> dict[str, Any]:
    risk = cfg.raw.get("risk", {})
    portfolio = portfolio or {}
    bankroll = _portfolio_number(portfolio, "bankroll", float(risk.get("bankroll", 1000)))
    cash = _portfolio_number(portfolio, "cash", bankroll)
    edge = safe_float(signal.get("edge")) or 0.0
    spread = safe_float(signal.get("spread")) or 0.0
    liquidity = safe_float(signal.get("liquidity")) or 0.0
    price = safe_float(signal.get("executable_price")) or safe_float(signal.get("market_price")) or 1.0
    prob = safe_float(signal.get("calibrated_probability")) or safe_float(signal.get("model_probability")) or 0.0
    confidence = safe_float(signal.get("model_confidence"))
    if confidence is None:
        confidence = safe_float(signal.get("confidence"))
    if confidence is None:
        confidence = _directional_confidence(prob)
    slippage = safe_float(signal.get("slippage")) or safe_float(signal.get("expected_slippage")) or 0.0
    resolution_risk = safe_float(signal.get("resolution_risk")) or 0.0
    time_to_close_minutes = _time_to_close_minutes(signal)

    def reject(reason: str, max_size: float = 0.0) -> dict[str, Any]:
        return {
            "approved": False,
            "reason": reason,
            "size": 0.0,
            "stake_usdc": 0.0,
            "quantity": 0.0,
            "limit_price": price if 0 < price < 1 else 0.0,
            "max_size": round(max_size, 6),
        }

    if kill_switch_active():
        return reject("kill switch active")
    if price <= 0 or price >= 1:
        return reject("invalid executable price")
    if str(signal.get("market_id", "")) in _as_set(risk.get("market_blacklist")):
        return reject("market is blacklisted")
    if str(signal.get("event_id", "")) in _as_set(risk.get("event_blacklist")):
        return reject("event is blacklisted")
    if str(signal.get("category", "")) in _as_set(risk.get("category_blacklist")):
        return reject("category is blacklisted")

    checks = [
        (edge >= float(risk.get("minimum_edge", 0.03)), "edge below minimum"),
        (confidence >= float(risk.get("minimum_confidence", 0.65)), "confidence below minimum"),
        (spread <= float(risk.get("maximum_spread", 0.08)), "spread above maximum"),
        (liquidity >= float(risk.get("minimum_liquidity", 50)), "liquidity below minimum"),
        (resolution_risk <= float(risk.get("maximum_resolution_risk", 0.25)), "resolution risk above maximum"),
        (slippage <= float(risk.get("maximum_slippage", 0.02)), "slippage above maximum"),
        (_portfolio_number(portfolio, "daily_loss", 0.0) <= float(risk.get("maximum_daily_loss", 0.03)) * bankroll, "daily loss above maximum"),
        (_portfolio_number(portfolio, "drawdown", 0.0) <= float(risk.get("maximum_drawdown", 0.1)), "drawdown above maximum"),
        (_portfolio_number(portfolio, "open_orders", 0.0) < float(risk.get("maximum_open_orders", 10)), "open orders above maximum"),
        (_portfolio_number(portfolio, "order_rate_per_minute", 0.0) < float(risk.get("maximum_order_rate_per_minute", 3)), "order rate above maximum"),
    ]
    min_ttc = float(risk.get("minimum_time_to_close_minutes", 15))
    require_ttc = str(risk.get("require_time_to_close", False)).strip().lower() in {"1", "true", "yes"}
    if time_to_close_minutes is None:
        if require_ttc:
            checks.append((False, "time to close missing"))
    else:
        checks.append((time_to_close_minutes >= min_ttc, "time to close below minimum"))

    for ok, reason in checks:
        if not ok:
            return reject(reason)

    cap = float(risk.get("kelly_cap", 0.005))
    kelly = kelly_fraction(prob, price, cap)
    max_single = float(risk.get("maximum_single_market_exposure", 0.02)) * bankroll
    max_category = float(risk.get("maximum_category_exposure", 0.1)) * bankroll
    max_correlated = float(risk.get("maximum_correlated_exposure", 0.1)) * bankroll

    current_market = _portfolio_number(portfolio, "current_market_exposure", 0.0)
    current_category = _portfolio_number(portfolio, "current_category_exposure", 0.0)
    current_correlated = _portfolio_number(portfolio, "current_correlated_exposure", 0.0)

    liquidity_cap = liquidity * float(risk.get("liquidity_cap_fraction", 0.05))
    stake_usdc = min(
        kelly * bankroll,
        max(0.0, max_single - current_market),
        max(0.0, max_category - current_category),
        max(0.0, max_correlated - current_correlated),
        liquidity_cap,
        cash,
    )
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
        "risk_checks": {
            "time_to_close_minutes": time_to_close_minutes,
            "resolution_risk": resolution_risk,
            "slippage": slippage,
            "category_capacity_remaining": max(0.0, max_category - current_category),
            "correlated_capacity_remaining": max(0.0, max_correlated - current_correlated),
        },
    }

