"""Live game mispricing + spread scanner driven by the WebSocket order book.

Joins the live book features written by ``websocket_normaliser`` (best bid/ask/spread per
asset) to our calculated fair probability per game, and emits dry-run signals:

  - BUY_YES  - our fair is above the ask by >= min_edge (the outcome looks underpriced),
  - SELL_YES - our fair is below the bid by >= min_edge (overpriced),
  - MAKE     - no directional edge but the spread is wide enough to quote inside and earn it.

Directional logic reuses the tested ``long_short.directional_signal``; spread capture reuses
``market_make_quote``. HONEST NOTE: the audit showed our game fair has ~no edge over the
market (it is a de-vigged mirror), so directional signals are for forward paper evidence, not
expected profit; the spread/market-make leg is the market-neutral part. Everything here is
paper-only - it never places an order.
"""
from __future__ import annotations

from typing import Any

from superbru_score_engine.betting.long_short import directional_signal, market_make_quote

from .config import EngineConfig, load_config
from .utils import find_first_column, now_utc, read_csv_rows, safe_float, write_csv, write_json

ACTION_BY_SIDE = {"LONG": "BUY_YES", "SHORT": "SELL_YES", "NONE": "NONE"}


def evaluate_book(fair: float, best_bid: float, best_ask: float, *, stake_usdc: float = 10.0,
                  min_edge: float = 0.02, max_spread: float = 0.10, min_mm_spread: float = 0.04,
                  tick: float = 0.01) -> dict[str, Any]:
    """Turn one live book + our fair into a dry-run signal (directional or market-make)."""
    spread = round(best_ask - best_bid, 6)
    ds = directional_signal(fair, best_bid, best_ask, stake_usdc, min_edge=min_edge)
    action = ACTION_BY_SIDE[ds.action]
    mm_bid: Any = ""
    mm_ask: Any = ""
    expected_capture: Any = ""

    # Only quote a spread when there is no directional edge and the book is wide but sane.
    if ds.action == "NONE" and min_mm_spread <= spread <= max_spread:
        half = max(tick, spread / 2 - tick)
        shares = stake_usdc / max(0.01, fair)
        quote = market_make_quote(fair, half, shares)
        if quote.bid > best_bid and quote.ask < best_ask:  # quote must land strictly inside
            action = "MAKE"
            mm_bid, mm_ask, expected_capture = quote.bid, quote.ask, round(quote.expected_capture_usdc, 4)

    return {
        "action": action,
        "directional": ds.action,
        "fair": round(fair, 6),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "midpoint": round((best_bid + best_ask) / 2.0, 6),
        "spread": spread,
        "edge": round(ds.raw_edge, 6),
        "mm_bid": mm_bid,
        "mm_ask": mm_ask,
        "expected_capture_usdc": expected_capture,
        "reason": ds.reason,
    }


def _load_fairs(path: str) -> dict[str, tuple[float, str]]:
    rows = read_csv_rows(path)
    if not rows:
        return {}
    cols = list(rows[0].keys())
    token_col = find_first_column(cols, ["asset_id", "token_id", "outcome_token_id", "clob_token_id"])
    prob_col = find_first_column(cols, ["fair_probability", "model_probability", "probability", "fair", "prob"])
    label_col = find_first_column(cols, ["question", "market", "market_slug", "title", "outcome"])
    fairs: dict[str, tuple[float, str]] = {}
    for row in rows:
        token = row.get(token_col or "", "")
        prob = safe_float(row.get(prob_col or ""))
        if token and prob is not None and 0.0 <= prob <= 1.0:
            fairs[token] = (prob, row.get(label_col or "", ""))
    return fairs


def scan_live_mispricing(cfg: EngineConfig, fairs_path: str | None = None, stake_usdc: float = 10.0,
                         min_edge: float = 0.02, max_spread: float = 0.10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    settings = cfg.raw.get("live_mispricing", {})
    stake_usdc = float(settings.get("stake_usdc", stake_usdc))
    min_edge = float(settings.get("min_edge", min_edge))
    max_spread = float(settings.get("max_spread", max_spread))
    fairs_path = fairs_path or settings.get("fairs_file") or "inputs/polymarket/model_probabilities.csv"

    features = read_csv_rows(cfg.output_root / "polymarket_training" / "websocket_market_features.csv")
    latest: dict[str, dict[str, str]] = {}
    for row in features:
        asset = row.get("asset_id", "")
        if not asset:
            continue
        if asset not in latest or row.get("collected_at_utc", "") >= latest[asset].get("collected_at_utc", ""):
            latest[asset] = row

    fairs = _load_fairs(str(fairs_path))

    signals: list[dict[str, Any]] = []
    skipped = 0
    for asset, book in latest.items():
        best_bid = safe_float(book.get("best_bid"))
        best_ask = safe_float(book.get("best_ask"))
        fair_entry = fairs.get(asset)
        if fair_entry is None or best_bid is None or best_ask is None or best_ask <= best_bid:
            skipped += 1
            continue
        fair, question = fair_entry
        ev = evaluate_book(fair, best_bid, best_ask, stake_usdc=stake_usdc, min_edge=min_edge, max_spread=max_spread)
        if ev["action"] == "NONE":
            continue
        signals.append({"asset_id": asset, "market": book.get("market", ""), "question": question,
                        "collected_at_utc": book.get("collected_at_utc", ""), **ev})

    out_dir = cfg.output_root / "polymarket_live_mispricing"
    write_csv(out_dir / "live_mispricing_signals.csv", signals)
    summary = {
        "status": "paper_only",
        "live_trading": False,
        "generated_at_utc": now_utc(),
        "books_scanned": len(latest),
        "fairs_available": len(fairs),
        "signals": len(signals),
        "buy_yes": sum(1 for s in signals if s["action"] == "BUY_YES"),
        "sell_yes": sum(1 for s in signals if s["action"] == "SELL_YES"),
        "market_make": sum(1 for s in signals if s["action"] == "MAKE"),
        "skipped_no_fair_or_book": skipped,
        "stake_usdc": stake_usdc,
        "min_edge": min_edge,
        "max_spread": max_spread,
        "fairs_file": str(fairs_path),
        "note": "Directional edge vs our fair is ~0 backtested (de-vigged market mirror); run paper "
                "to gather forward evidence. The MAKE/spread leg is the market-neutral part.",
    }
    write_json(out_dir / "live_mispricing_summary.json", summary)
    return signals, summary


def run_live_mispricing(cfg: EngineConfig, websocket_seconds: int = 30) -> dict[str, Any]:
    """One live cycle: capture the WebSocket book, normalise it, then scan for mispricing.

    Paper-only end to end; it never touches an order path.
    """
    from .websocket_collector import collect_websocket
    from .websocket_normaliser import normalize_websocket_file

    collect = collect_websocket(cfg, websocket_seconds=websocket_seconds)
    normalise: dict[str, Any] = {}
    if collect.get("status") == "collected":
        _, _, normalise = normalize_websocket_file(cfg)
    signals, scan = scan_live_mispricing(cfg)
    return {"live_trading": False, "collect": collect, "normalise": normalise, "scan": scan, "signals": len(signals)}


def main(config_path: str) -> dict[str, Any]:
    _, summary = scan_live_mispricing(load_config(config_path))
    return summary
