from __future__ import annotations

from typing import Any

from .utils import safe_float


def _price(row: dict[str, Any], side: str) -> float | None:
    keys = (
        ("best_ask", "executable_price", "executable_buy_price", "market_price")
        if side.upper() == "BUY"
        else ("best_bid", "executable_sell_price", "market_price")
    )
    for key in keys:
        value = safe_float(row.get(key))
        if value is not None and 0 < value < 1:
            return value
    return None


def _depth_fields(row: dict[str, Any], side: str) -> tuple[float | None, float | None, float | None]:
    if side.upper() == "BUY":
        return (
            safe_float(row.get("top_ask_size") or row.get("ask_size")),
            safe_float(row.get("ask_depth_1pct")),
            safe_float(row.get("ask_depth_5pct")),
        )
    return (
        safe_float(row.get("top_bid_size") or row.get("bid_size")),
        safe_float(row.get("bid_depth_1pct")),
        safe_float(row.get("bid_depth_5pct")),
    )


def estimate_execution_cost(
    row: dict[str, Any],
    *,
    stake_usdc: float,
    side: str = "BUY",
    flat_slippage: float = 0.0,
    acceptable_impact_pct: float = 0.01,
) -> dict[str, Any]:
    """Estimate conservative fill cost from top-of-book depth fields.

    The estimator deliberately fails closed:
    - if depth is missing, it returns the old flat slippage assumption unchanged;
    - shallow books increase expected slippage and reduce the acceptable stake cap;
    - lower-than-flat slippage is only allowed when the top/depth can fill the
      requested stake, i.e. when the book is demonstrably deep enough.
    """
    side = side.upper()
    flat = max(0.0, float(flat_slippage or 0.0))
    stake = max(0.0, float(stake_usdc or 0.0))
    price = _price(row, side)
    spread = safe_float(row.get("spread"))
    top_size, depth_1pct, depth_5pct = _depth_fields(row, side)
    if price is None or price <= 0:
        return {
            "status": "missing_price",
            "side": side,
            "reference_price": price,
            "stake_usdc": stake,
            "quantity": 0.0,
            "expected_fill_price": price,
            "expected_slippage": flat,
            "flat_slippage": flat,
            "max_stake_at_acceptable_impact_usdc": "",
            "depth_is_demonstrably_deep": False,
            "reason": "price missing; using flat slippage",
        }
    if top_size is None and depth_1pct is None and depth_5pct is None:
        return {
            "status": "missing_depth",
            "side": side,
            "reference_price": price,
            "stake_usdc": stake,
            "quantity": stake / price if price > 0 else 0.0,
            "expected_fill_price": min(0.999999, price + flat) if side == "BUY" else max(0.000001, price - flat),
            "expected_slippage": flat,
            "flat_slippage": flat,
            "spread": spread,
            "top_size": "",
            "depth_1pct": "",
            "depth_5pct": "",
            "max_stake_at_acceptable_impact_usdc": "",
            "depth_is_demonstrably_deep": False,
            "reason": "depth missing; using flat slippage",
        }

    top = max(0.0, top_size or 0.0)
    d1 = max(top, depth_1pct or 0.0)
    d5 = max(d1, depth_5pct or 0.0)
    quantity = stake / price if price > 0 else 0.0
    impact_pct = max(0.0, float(acceptable_impact_pct or 0.0))
    acceptable_depth = d1 if impact_pct <= 0.01 else d5
    max_stake = acceptable_depth * price if acceptable_depth > 0 else 0.0

    demonstrably_deep = bool(stake > 0 and top >= quantity and d1 >= quantity)
    if quantity <= 0:
        model_slippage = flat
        status = "no_stake"
        reason = "zero stake; using flat slippage"
    elif top >= quantity:
        model_slippage = 0.0
        status = "top_of_book_fill"
        reason = "top of book can fill requested stake"
    elif d1 >= quantity:
        filled_beyond_top = max(0.0, quantity - top)
        band = max(1e-9, d1 - top)
        model_slippage = price * 0.005 * min(1.0, filled_beyond_top / band)
        status = "within_1pct_depth"
        reason = "1pct book depth can fill requested stake"
    elif d5 >= quantity:
        filled_beyond_1pct = max(0.0, quantity - d1)
        band = max(1e-9, d5 - d1)
        model_slippage = price * (0.01 + 0.04 * min(1.0, filled_beyond_1pct / band))
        status = "within_5pct_depth"
        reason = "5pct book depth can fill requested stake, with material impact"
    else:
        spread_floor = max(0.0, (spread or 0.0) / 2.0)
        model_slippage = max(price * 0.05, spread_floor)
        status = "insufficient_depth"
        reason = "requested stake exceeds known 5pct depth"

    expected_slippage = model_slippage if demonstrably_deep and model_slippage < flat else max(flat, model_slippage)
    fill_price = min(0.999999, price + expected_slippage) if side == "BUY" else max(0.000001, price - expected_slippage)
    return {
        "status": status,
        "side": side,
        "reference_price": round(price, 6),
        "stake_usdc": round(stake, 6),
        "quantity": round(quantity, 6),
        "expected_fill_price": round(fill_price, 6),
        "expected_slippage": round(expected_slippage, 6),
        "flat_slippage": round(flat, 6),
        "spread": "" if spread is None else round(spread, 6),
        "top_size": round(top, 6),
        "depth_1pct": round(d1, 6),
        "depth_5pct": round(d5, 6),
        "max_stake_at_acceptable_impact_usdc": round(max_stake, 6),
        "depth_is_demonstrably_deep": demonstrably_deep,
        "reason": reason,
    }
