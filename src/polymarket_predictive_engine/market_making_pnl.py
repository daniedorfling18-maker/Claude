"""Realistic market-making P&L with adverse selection.

A naive maker thinks it earns the full spread. It does not: it gets filled on the side the
market is moving toward (informed/latency flow picks the stale quote), so each fill carries an
adverse mark-out. Net edge per filled share is roughly ``half_spread - adverse_markout``; the
maker only profits when the half-spread it quotes exceeds the short-horizon mark-out plus costs.

This module provides:
  - ``market_making_pnl`` - net P&L given fills, half-spread, adverse mark-out, and rebate;
  - ``breakeven_markout`` - the mark-out at which a quote breaks even;
  - ``estimate_adverse_markout`` - the empirical short-horizon |midpoint move| from the pulled
    price history, used as the adverse-selection benchmark a maker faces;
  - ``evaluate_market_making`` - joins live book spreads to that benchmark and ranks markets by
    realistic net maker edge, so the P&L is honest rather than gross.

Everything is paper analysis - it never places an order.
"""
from __future__ import annotations

from statistics import median
from typing import Any

from .config import EngineConfig, load_config
from .utils import now_utc, parse_timestamp, read_csv_rows, safe_float, write_csv, write_json


def market_making_pnl(half_spread: float, adverse_markout: float, fills: float, shares_per_fill: float,
                      *, maker_rebate_rate: float = 0.0, mid: float = 0.5) -> dict[str, Any]:
    """Net maker P&L. Each fill earns the half-spread but pays the adverse mark-out."""
    volume = max(0.0, fills) * max(0.0, shares_per_fill)
    gross_capture = half_spread * volume
    adverse_cost = adverse_markout * volume
    rebate = maker_rebate_rate * mid * volume
    net_per_share = half_spread - adverse_markout + maker_rebate_rate * mid
    net_pnl = gross_capture - adverse_cost + rebate
    return {
        "half_spread": round(half_spread, 6),
        "adverse_markout": round(adverse_markout, 6),
        "net_edge_per_share": round(net_per_share, 6),
        "gross_capture_usdc": round(gross_capture, 4),
        "adverse_cost_usdc": round(adverse_cost, 4),
        "rebate_usdc": round(rebate, 4),
        "net_pnl_usdc": round(net_pnl, 4),
        "profitable": net_per_share > 0,
    }


def breakeven_markout(half_spread: float, maker_rebate_rate: float = 0.0, mid: float = 0.5) -> float:
    """Adverse mark-out at which a maker quote breaks even."""
    return round(half_spread + maker_rebate_rate * mid, 6)


def estimate_adverse_markout(cfg: EngineConfig, horizon_steps: int = 1) -> dict[str, Any]:
    """Empirical adverse-selection benchmark = median |midpoint move| over the next
    ``horizon_steps`` snapshots, computed per token from the pulled price history and
    aggregated, plus a breakdown by price band (mark-out is larger mid-book)."""
    rows = read_csv_rows(cfg.output_root / "polymarket_training" / "historical_price_snapshots.csv")
    by_token: dict[str, list[tuple[Any, float]]] = {}
    for r in rows:
        token = r.get("token_id", "")
        ts = parse_timestamp(r.get("timestamp"))
        mid = safe_float(r.get("midpoint"))
        if token and ts and mid is not None:
            by_token.setdefault(token, []).append((ts, mid))

    moves: list[float] = []
    band_moves: dict[str, list[float]] = {"0.0-0.2": [], "0.2-0.4": [], "0.4-0.6": [], "0.6-0.8": [], "0.8-1.0": []}
    for series in by_token.values():
        series.sort(key=lambda x: x[0])
        prices = [p for _, p in series]
        for i in range(len(prices) - horizon_steps):
            move = abs(prices[i + horizon_steps] - prices[i])
            moves.append(move)
            p = prices[i]
            band = "0.0-0.2" if p < 0.2 else "0.2-0.4" if p < 0.4 else "0.4-0.6" if p < 0.6 else "0.6-0.8" if p < 0.8 else "0.8-1.0"
            band_moves[band].append(move)

    return {
        "tokens": len(by_token),
        "observations": len(moves),
        "horizon_steps": horizon_steps,
        "median_markout": round(median(moves), 6) if moves else None,
        "mean_markout": round(sum(moves) / len(moves), 6) if moves else None,
        "markout_by_price_band": {b: round(median(v), 6) for b, v in band_moves.items() if v},
    }


def evaluate_market_making(cfg: EngineConfig, *, fills_per_market: float = 10.0, shares_per_fill: float = 10.0,
                           maker_rebate_rate: float = 0.0, adverse_markout: float | None = None,
                           adverse_multiplier: float = 2.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rank live markets by realistic net maker edge: half the live spread minus the
    empirical adverse mark-out. ``adverse_markout`` defaults to the historical mean move
    scaled by ``adverse_multiplier`` - the unconditional move understates adverse selection
    (which is conditional on a fill by informed flow), so we scale it up to stay conservative."""
    bench = estimate_adverse_markout(cfg)
    if adverse_markout is None:
        base = bench.get("mean_markout") or 0.002
        adverse_markout = base * adverse_multiplier

    features = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    latest: dict[str, dict[str, str]] = {}
    for row in features:
        asset = row.get("asset_id", "")
        if asset and (asset not in latest or row.get("collected_at_utc", "") >= latest[asset].get("collected_at_utc", "")):
            latest[asset] = row

    rows: list[dict[str, Any]] = []
    for asset, book in latest.items():
        bid = safe_float(book.get("best_bid"))
        ask = safe_float(book.get("best_ask"))
        if bid is None or ask is None or ask <= bid:
            continue
        spread = ask - bid
        half = spread / 2.0
        mid = (bid + ask) / 2.0
        pnl = market_making_pnl(half, adverse_markout, fills_per_market, shares_per_fill,
                                maker_rebate_rate=maker_rebate_rate, mid=mid)
        rows.append({"asset_id": asset, "market": book.get("market", ""), "spread": round(spread, 6),
                     "half_spread": round(half, 6), "midpoint": round(mid, 6), **pnl})

    rows.sort(key=lambda r: -r["net_pnl_usdc"])
    profitable = [r for r in rows if r["profitable"]]
    out_dir = cfg.output_root / "polymarket_market_making"
    write_csv(out_dir / "market_making_pnl.csv", rows)
    summary = {
        "status": "paper_analysis",
        "live_trading": False,
        "generated_at_utc": now_utc(),
        "adverse_markout_used": round(adverse_markout, 6),
        "adverse_markout_benchmark": bench,
        "markets_evaluated": len(rows),
        "profitable_markets": len(profitable),
        "profitable_fraction": round(len(profitable) / len(rows), 4) if rows else None,
        "mean_net_edge_per_share": round(sum(r["net_edge_per_share"] for r in rows) / len(rows), 6) if rows else None,
        "total_potential_net_pnl_usdc": round(sum(r["net_pnl_usdc"] for r in profitable), 2),
        "note": "Net maker edge = half live spread - empirical short-horizon |mid move|. Positive only where "
                "the quoted half-spread beats the adverse mark-out; adverse selection is the real MM cost.",
    }
    write_json(out_dir / "market_making_summary.json", summary)
    return rows, summary


def main(config_path: str) -> dict[str, Any]:
    _, summary = evaluate_market_making(load_config(config_path))
    return summary
