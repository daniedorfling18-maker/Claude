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


def risk_decision(cfg: EngineConfig, signal: dict[str, Any], portfolio: dict[str, Any] | None = None) -> dict[str, Any]:
    risk = cfg.raw.get("risk", {})
    bankroll = safe_float((portfolio or {}).get("bankroll")) or float(risk.get("bankroll", 1000))
    edge = safe_float(signal.get("edge")) or 0.0
    confidence = safe_float(signal.get("confidence")) or 0.0
    spread = safe_float(signal.get("spread")) or 0.0
    liquidity = safe_float(signal.get("liquidity")) or 0.0
    price = safe_float(signal.get("executable_price")) or safe_float(signal.get("market_price")) or 1.0
    prob = safe_float(signal.get("calibrated_probability")) or safe_float(signal.get("model_probability")) or 0.0
    if kill_switch_active():
        return {"approved": False, "reason": "kill switch active", "size": 0.0, "max_size": 0.0}
    checks = [
        (edge >= float(risk.get("minimum_edge", 0.03)), "edge below minimum"),
        (confidence >= float(risk.get("minimum_confidence", 0.65)), "confidence below minimum"),
        (spread <= float(risk.get("maximum_spread", 0.08)), "spread above maximum"),
        (liquidity >= float(risk.get("minimum_liquidity", 50)), "liquidity below minimum"),
    ]
    for ok, reason in checks:
        if not ok:
            return {"approved": False, "reason": reason, "size": 0.0, "max_size": 0.0}
    cap = float(risk.get("kelly_cap", 0.005))
    kelly = kelly_fraction(prob, price, cap)
    max_single = float(risk.get("maximum_single_market_exposure", 0.02)) * bankroll
    size = min(kelly * bankroll, max_single, liquidity * 0.05)
    if size <= 0:
        return {"approved": False, "reason": "kelly sizing is zero", "size": 0.0, "max_size": max_single}
    return {"approved": True, "reason": "risk controls approved", "size": round(size, 6), "max_size": round(max_single, 6)}
