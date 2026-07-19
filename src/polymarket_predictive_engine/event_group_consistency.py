"""WO-34 event-group sum-constraint detector (shadow measurement only).

A Polymarket negRisk event is a mutually exclusive, exhaustive outcome group:
exactly one leg resolves YES, so the legs must price to $1. Deviations are
structural mispricings with a model-free payoff:

- buy-all-YES: cost sum(best asks) + taker fees, payout $1
  -> net = 1 - sum_ask - fees, positive when the group trades cheap;
- sell-all-YES (buy every NO): receive sum(best bids) - fees against a $1
  payout -> net = sum_bid - 1 - fees, positive when the group trades rich.

This is the second registered edge class next to the sharp anchor, and it
needs NO external odds feed - the quota blocker cannot touch it. The detector
answers "how often is the book internally inconsistent, by how much, and for
how long?" by scanning the top event groups and appending flagged deviations
to a timestamped ledger; persistence shows up as the same event recurring
across 15-minute scans. Live probe at registration (2026-07-09): World Cup
Winner group bids summed to 1.003 - deviations exist but sit near fee-size,
which is exactly why the fee charge below is applied per leg.

Fees follow the verified live schedule (docs.polymarket.com/trading/fees):
takers pay rate x p x (1-p) per share; ``feesEnabled: false`` groups
(politics/geopolitics) pay zero. Basket completeness: the buy side is only
scored when EVERY active leg has an ask < 1, the sell side only when every
leg has a bid > 0 - a one-legged "arb" is a quoting gap, not a basket.

Detection only: no labels, no gates, no order placement of any kind. Any
future trading lane for this edge class requires its own pre-registered
verdict gates first (see WO-34).
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from .config import EngineConfig, load_config
from .utils import now_utc, read_csv_rows, safe_float, write_csv, write_json

DEFAULT_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"
DEFAULT_CLOB_BASE_URL = "https://clob.polymarket.com"

LEDGER_FIELDS = [
    "scanned_at_utc",
    "event_slug",
    "event_title",
    "neg_risk",
    "leg_count",
    "sum_ask",
    "sum_bid",
    "fee_charge_buy_basket",
    "fee_charge_sell_basket",
    "net_buy_all_yes_per_basket",
    "net_sell_all_yes_per_basket",
    "flagged_side",
    "executable_basket_usd",
    "depth_weighted_net",
    "book_fetch_ok",
    "fee_type",
    "fees_enabled",
    "volume_24h_usd",
]


def _settings(cfg: EngineConfig) -> dict[str, Any]:
    raw = cfg.raw.get("event_group_consistency", {}) if isinstance(cfg.raw.get("event_group_consistency"), dict) else {}
    merged = {
        "enabled": True,
        "gamma_base_url": DEFAULT_GAMMA_BASE_URL,
        "clob_base_url": DEFAULT_CLOB_BASE_URL,
        "event_pages": 3,
        "page_size": 100,
        "min_leg_count": 3,
        # Net-of-fee deviation (per $1 basket) beyond which a scan row is
        # appended to the ledger. 0.002 keeps near-misses visible without
        # recording every noisy tick.
        "deviation_threshold_per_basket": 0.002,
        # Canonical fees page (2026-07-10 assimilation): sports takers pay
        # 0.05 x p x (1-p) per share. Unknown-but-enabled types charge the
        # crypto worst case.
        "fee_rate_by_type": {"sports_fees_v2": 0.05},
        "fee_rate_when_enabled_unknown": 0.07,
        "max_ledger_rows": 100000,
        "request_timeout_seconds": 20,
        "request_pause_seconds": 0.1,
    }
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def _fee_rate(market: dict[str, Any], settings: dict[str, Any]) -> float:
    if not market.get("feesEnabled"):
        return 0.0
    by_type = settings.get("fee_rate_by_type") or {}
    fee_type = str(market.get("feeType") or "")
    rate = safe_float(by_type.get(fee_type))
    if rate is not None:
        return rate
    return float(settings["fee_rate_when_enabled_unknown"])


def _taker_fee_per_share(rate: float, price: float) -> float:
    return rate * price * (1.0 - price)


def _first_token_id(market: dict[str, Any]) -> str:
    raw = market.get("clobTokenIds") or market.get("clob_token_ids") or market.get("token_id")
    if isinstance(raw, str):
        raw = raw.strip()
        if raw.startswith("["):
            try:
                tokens = json.loads(raw)
            except (TypeError, ValueError):
                tokens = []
        else:
            tokens = [raw]
    elif isinstance(raw, list):
        tokens = raw
    else:
        tokens = []
    return str(tokens[0]).strip() if tokens else ""


def _book_levels(
    settings: dict[str, Any],
    token_id: str,
    *,
    side: str,
) -> list[tuple[float, float]] | None:
    try:
        response = requests.get(
            f"{str(settings['clob_base_url']).rstrip('/')}/book",
            params={"token_id": token_id},
            timeout=float(settings["request_timeout_seconds"]),
        )
        response.raise_for_status()
        book = response.json()
    except Exception:
        return None
    levels: list[tuple[float, float]] = []
    for level in book.get(side) or []:
        if not isinstance(level, dict):
            continue
        price = safe_float(level.get("price"))
        size = safe_float(level.get("size"))
        if price is not None and size is not None and 0 < price < 1 and size > 0:
            levels.append((price, size))
    levels.sort(key=lambda item: item[0], reverse=side == "bids")
    return levels


def _book_asks(settings: dict[str, Any], token_id: str) -> list[tuple[float, float]] | None:
    return _book_levels(settings, token_id, side="asks")


def _book_bids(settings: dict[str, Any], token_id: str) -> list[tuple[float, float]] | None:
    return _book_levels(settings, token_id, side="bids")


def _cost_for_quantity(levels: list[tuple[float, float]], quantity: float, fee_rate: float) -> float | None:
    remaining = quantity
    total = 0.0
    for price, size in levels:
        take = min(size, remaining)
        total += take * (price + _taker_fee_per_share(fee_rate, price))
        remaining -= take
        if remaining <= 1e-9:
            return total
    return None


def _proceeds_for_quantity(
    levels: list[tuple[float, float]],
    quantity: float,
    fee_rate: float,
) -> float | None:
    remaining = quantity
    total = 0.0
    for price, size in levels:
        take = min(size, remaining)
        total += take * (price - _taker_fee_per_share(fee_rate, price))
        remaining -= take
        if remaining <= 1e-9:
            return total
    return None


def _executable_buy_all_yes_depth(
    markets: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Depth check for a flagged buy-all-YES basket.

    The basket is executable only while buying one share of every leg remains
    net-positive after taker fees. We consume asks on every leg up to common
    basket quantities and report the largest positive quantity's notional plus
    its depth-weighted net per $1 basket.
    """
    leg_books: list[tuple[list[tuple[float, float]], float]] = []
    candidate_quantities: set[float] = set()
    for market in markets:
        token_id = _first_token_id(market)
        if not token_id:
            return {"book_fetch_ok": False, "executable_basket_usd": None, "depth_weighted_net": None}
        asks = _book_asks(settings, token_id)
        if not asks:
            return {"book_fetch_ok": False, "executable_basket_usd": None, "depth_weighted_net": None}
        running = 0.0
        for _, size in asks:
            running += size
            candidate_quantities.add(round(running, 10))
        leg_books.append((asks, _fee_rate(market, settings)))

    best: dict[str, Any] | None = None
    for quantity in sorted(candidate_quantities):
        leg_costs: list[float] = []
        for asks, rate in leg_books:
            cost = _cost_for_quantity(asks, quantity, rate)
            if cost is None:
                leg_costs = []
                break
            leg_costs.append(cost)
        if not leg_costs:
            continue
        basket_cost = sum(leg_costs)
        cost_per_basket = basket_cost / quantity
        net_per_basket = 1.0 - cost_per_basket
        if net_per_basket <= 0:
            continue
        best = {
            "book_fetch_ok": True,
            "executable_basket_usd": round(basket_cost, 2),
            "depth_weighted_net": round(net_per_basket, 6),
        }
    if best is not None:
        return best
    return {"book_fetch_ok": True, "executable_basket_usd": 0.0, "depth_weighted_net": 0.0}


def _executable_sell_all_yes_depth(
    markets: list[dict[str, Any]],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """Depth check for selling one complete YES basket into live bids.

    Every leg must fill the same quantity. Proceeds are reduced by taker fees,
    then compared with the $1 complete-set cost; no ask-side proxy is used.
    """

    leg_books: list[tuple[list[tuple[float, float]], float]] = []
    candidate_quantities: set[float] = set()
    for market in markets:
        token_id = _first_token_id(market)
        if not token_id:
            return {"book_fetch_ok": False, "executable_basket_usd": None, "depth_weighted_net": None}
        bids = _book_bids(settings, token_id)
        if not bids:
            return {"book_fetch_ok": False, "executable_basket_usd": None, "depth_weighted_net": None}
        running = 0.0
        for _, size in bids:
            running += size
            candidate_quantities.add(round(running, 10))
        leg_books.append((bids, _fee_rate(market, settings)))

    best: dict[str, Any] | None = None
    for quantity in sorted(candidate_quantities):
        leg_proceeds: list[float] = []
        for bids, rate in leg_books:
            proceeds = _proceeds_for_quantity(bids, quantity, rate)
            if proceeds is None:
                leg_proceeds = []
                break
            leg_proceeds.append(proceeds)
        if not leg_proceeds:
            continue
        basket_proceeds = sum(leg_proceeds)
        net_per_basket = basket_proceeds / quantity - 1.0
        if net_per_basket <= 0:
            continue
        best = {
            "book_fetch_ok": True,
            "executable_basket_usd": round(basket_proceeds, 2),
            "depth_weighted_net": round(net_per_basket, 6),
        }
    if best is not None:
        return best
    return {"book_fetch_ok": True, "executable_basket_usd": 0.0, "depth_weighted_net": 0.0}


def _scan_event(event: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any] | None:
    markets = [m for m in event.get("markets") or [] if isinstance(m, dict) and not m.get("closed")]
    if len(markets) < int(settings["min_leg_count"]):
        return None
    asks: list[float] = []
    bids: list[float] = []
    fee_buy = 0.0
    fee_sell = 0.0
    ask_complete = True
    bid_complete = True
    for market in markets:
        rate = _fee_rate(market, settings)
        ask = safe_float(market.get("bestAsk"))
        bid = safe_float(market.get("bestBid"))
        if ask is None or not 0 < ask < 1:
            ask_complete = False
        else:
            asks.append(ask)
            fee_buy += _taker_fee_per_share(rate, ask)
        if bid is None or not 0 < bid < 1:
            bid_complete = False
        else:
            bids.append(bid)
            fee_sell += _taker_fee_per_share(rate, bid)
    if not ask_complete and not bid_complete:
        return None
    sum_ask = round(sum(asks), 4) if ask_complete else None
    sum_bid = round(sum(bids), 4) if bid_complete else None
    net_buy = round(1.0 - sum_ask - fee_buy, 6) if sum_ask is not None else None
    net_sell = round(sum_bid - 1.0 - fee_sell, 6) if sum_bid is not None else None
    threshold = float(settings["deviation_threshold_per_basket"])
    flagged_side = ""
    if net_buy is not None and net_buy > threshold:
        flagged_side = "buy_all_yes"
    if net_sell is not None and net_sell > threshold and (net_buy is None or net_sell > net_buy):
        flagged_side = "sell_all_yes"
    depth = {"executable_basket_usd": None, "depth_weighted_net": None, "book_fetch_ok": None}
    if flagged_side == "buy_all_yes":
        depth = _executable_buy_all_yes_depth(markets, settings)
    elif flagged_side == "sell_all_yes":
        depth = _executable_sell_all_yes_depth(markets, settings)
    first = markets[0]
    return {
        "scanned_at_utc": now_utc(),
        "event_slug": str(event.get("slug") or "").strip(),
        "event_title": str(event.get("title") or "").strip(),
        "neg_risk": bool(event.get("negRisk")),
        "leg_count": len(markets),
        "sum_ask": sum_ask,
        "sum_bid": sum_bid,
        "fee_charge_buy_basket": round(fee_buy, 6) if sum_ask is not None else None,
        "fee_charge_sell_basket": round(fee_sell, 6) if sum_bid is not None else None,
        "net_buy_all_yes_per_basket": net_buy,
        "net_sell_all_yes_per_basket": net_sell,
        "flagged_side": flagged_side,
        **depth,
        "fee_type": str(first.get("feeType") or ""),
        "fees_enabled": bool(first.get("feesEnabled")),
        "volume_24h_usd": round(safe_float(event.get("volume24hr")) or 0.0, 2),
    }


def scan_event_groups(cfg: EngineConfig) -> dict[str, Any]:
    settings = _settings(cfg)
    out_root = cfg.output_root / "event_group_consistency"
    ledger_path = out_root / "event_group_deviations.csv"
    summary_path = out_root / "event_group_scan.json"
    summary: dict[str, Any] = {
        "status": "disabled",
        "generated_at_utc": now_utc(),
        "work_order": "WO-34",
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if str(settings.get("enabled", True)).strip().lower() in {"0", "false", "no"}:
        write_json(summary_path, summary)
        return summary

    base = str(settings["gamma_base_url"]).rstrip("/")
    timeout = float(settings["request_timeout_seconds"])
    pause = float(settings["request_pause_seconds"])
    scans: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(int(settings["event_pages"])):
        try:
            response = requests.get(
                f"{base}/events",
                params={
                    "closed": "false",
                    "order": "volume24hr",
                    "ascending": "false",
                    "limit": int(settings["page_size"]),
                    "offset": page * int(settings["page_size"]),
                },
                timeout=timeout,
            )
            response.raise_for_status()
            events = response.json()
        except Exception as exc:
            errors.append(f"gamma events page {page}: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(events, list) or not events:
            break
        for event in events:
            if not isinstance(event, dict) or not event.get("negRisk"):
                continue
            row = _scan_event(event, settings)
            if row is not None:
                scans.append(row)
        if pause:
            time.sleep(pause)

    flagged = [row for row in scans if row["flagged_side"]]
    ledger = read_csv_rows(ledger_path)
    ledger.extend(flagged)
    max_rows = int(settings["max_ledger_rows"])
    if max_rows > 0 and len(ledger) > max_rows:
        ledger = ledger[-max_rows:]
    write_csv(ledger_path, ledger, fieldnames=LEDGER_FIELDS)

    net_buys = [row["net_buy_all_yes_per_basket"] for row in scans if row["net_buy_all_yes_per_basket"] is not None]
    net_sells = [row["net_sell_all_yes_per_basket"] for row in scans if row["net_sell_all_yes_per_basket"] is not None]
    summary.update(
        {
            "status": "ok" if scans or not errors else "failed",
            "neg_risk_groups_scanned": len(scans),
            "groups_with_complete_ask_side": len(net_buys),
            "groups_with_complete_bid_side": len(net_sells),
            "flagged_deviations": len(flagged),
            "flagged_with_executable_depth": sum(
                1
                for row in flagged
                if row.get("book_fetch_ok") is True and (safe_float(row.get("executable_basket_usd")) or 0.0) > 0
            ),
            "max_executable_basket_usd": max(
                [safe_float(row.get("executable_basket_usd")) or 0.0 for row in flagged],
                default=0.0,
            ),
            "flagged_events": [row["event_slug"] for row in flagged][:20],
            "best_net_buy_all_yes": max(net_buys) if net_buys else None,
            "best_net_sell_all_yes": max(net_sells) if net_sells else None,
            "deviation_threshold_per_basket": float(settings["deviation_threshold_per_basket"]),
            "ledger_rows": len(ledger),
            "ledger_path": str(ledger_path),
            "note": (
                "Measurement of internal-consistency deviations net of live taker fees. Persistence = the same "
                "event recurring across scans. Any trading lane for this edge class requires its own "
                "pre-registered gates first (WO-34)."
            ),
            "errors": errors[:10],
        }
    )
    write_json(summary_path, summary)
    return summary


def main(config_path: str) -> dict[str, Any]:
    return scan_event_groups(load_config(config_path))
