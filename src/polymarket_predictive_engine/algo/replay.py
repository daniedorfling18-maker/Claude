"""Chronological replay of recorded websocket quotes through an algo strategy.

This is the event-driven backtester that closes the algo loop offline. It is a
research instrument, not an execution path: it refuses to run if any emitted
intent is non-shadow, and its fill simulation is deliberately conservative:

- a ``cross_spread`` IOC BUY fills at the event's best ask only when the limit
  crosses it; otherwise it cancels;
- resting orders (``join_bid`` / ``work_midpoint``, GTD) fill at their limit
  price only when a **later** event on the same asset shows the ask crossing
  down to the limit; they expire past ``expire_at_utc``;
- one open order/position per asset (no pyramiding);
- open positions are marked to the latest best bid, never the midpoint.

No wall-clock reads inside the loop (event timestamps drive everything), no
network, deterministic outputs.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ..config import EngineConfig, load_config
from ..execution.intents import OrderIntent
from ..utils import parse_timestamp, read_csv_rows, write_csv, write_json
from .base import StrategyContext
from .events import QuoteEvent
from .registry import emit_intents, get_strategy

FILL_FIELDS = [
    "intent_id",
    "filled_at_utc",
    "market_id",
    "token_id",
    "category",
    "side",
    "fill_price",
    "quantity",
    "notional_usdc",
    "execution_policy",
    "source_strategy",
]


def iter_quote_events(features_path: str | Path) -> list[QuoteEvent]:
    events = []
    for row in read_csv_rows(features_path):
        event = QuoteEvent.from_feature_row(row)
        if event is not None:
            events.append(event)
    events.sort(key=lambda event: (event.timestamp, event.asset_id))
    return events


def _fill(intent: OrderIntent, price: float, event: QuoteEvent) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "filled_at_utc": event.timestamp_utc,
        "market_id": intent.market_id,
        "token_id": intent.token_id,
        "category": event.category,
        "side": intent.side,
        "fill_price": round(price, 6),
        "quantity": round(intent.quantity, 6),
        "notional_usdc": round(price * intent.quantity, 6),
        "execution_policy": intent.execution_policy,
        "source_strategy": intent.source_strategy,
    }


def run_replay(
    cfg: EngineConfig,
    strategy_name: str,
    *,
    features_input: str | Path | None = None,
    events: list[QuoteEvent] | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    strategy = get_strategy(strategy_name)
    features_path = Path(features_input) if features_input else cfg.output_root / "polymarket_training" / "websocket_market_features.csv"
    if events is None:
        events = iter_quote_events(features_path)

    resting: dict[str, tuple[OrderIntent, QuoteEvent]] = {}
    positions: dict[str, dict[str, Any]] = {}
    latest_bid: dict[str, float] = {}
    fills: list[dict[str, Any]] = []
    intents_emitted = 0
    cancels = 0
    expiries = 0
    skipped_open_exposure = 0
    downgrade_notes: list[dict[str, Any]] = []

    for event in events:
        asset = event.asset_id
        # 1. Work resting orders for this asset before anything else.
        rested = resting.get(asset)
        if rested is not None:
            intent, _ = rested
            expire = parse_timestamp(intent.expire_at_utc)
            if expire is not None and event.timestamp > expire:
                expiries += 1
                resting.pop(asset)
            elif event.best_ask is not None and event.best_ask <= intent.limit_price:
                fill = _fill(intent, intent.limit_price, event)
                fills.append(fill)
                position = positions.setdefault(
                    asset,
                    {"market_id": intent.market_id, "token_id": asset, "category": event.category, "quantity": 0.0, "cost_usdc": 0.0},
                )
                position["quantity"] += intent.quantity
                position["cost_usdc"] += fill["notional_usdc"]
                strategy.on_fill(fill, StrategyContext(config=cfg, now_utc=event.timestamp_utc))
                resting.pop(asset)

        # 2. Track the conservative mark.
        if event.best_bid is not None:
            latest_bid[asset] = event.best_bid

        # 3. Let the strategy react to the quote.
        context = StrategyContext(config=cfg, now_utc=event.timestamp_utc, open_positions=tuple(positions.values()))
        new_intents, notes = emit_intents(strategy, event, context)
        downgrade_notes.extend(notes)
        for intent in new_intents:
            if intent.mode != "shadow":
                return {
                    "status": "refused",
                    "reason": "replay only accepts shadow intents; a non-shadow intent survived the registry wrapper",
                    "strategy": strategy_name,
                    "intent_id": intent.intent_id,
                    "paper_trading_invoked": False,
                    "live_trading_invoked": False,
                }
            if intent.side != "BUY":
                cancels += 1
                continue
            if intent.token_id in resting or intent.token_id in positions:
                skipped_open_exposure += 1
                continue
            intents_emitted += 1
            if intent.time_in_force == "IOC":
                if intent.execution_policy == "cross_spread" and event.best_ask is not None and intent.limit_price >= event.best_ask:
                    fill = _fill(intent, event.best_ask, event)
                    fills.append(fill)
                    position = positions.setdefault(
                        intent.token_id,
                        {"market_id": intent.market_id, "token_id": intent.token_id, "category": event.category, "quantity": 0.0, "cost_usdc": 0.0},
                    )
                    position["quantity"] += intent.quantity
                    position["cost_usdc"] += fill["notional_usdc"]
                    strategy.on_fill(fill, context)
                else:
                    cancels += 1
            else:
                resting[intent.token_id] = (intent, event)

    open_positions = []
    unrealised = 0.0
    total_cost = 0.0
    by_category: dict[str, dict[str, float]] = defaultdict(lambda: {"positions": 0, "cost_usdc": 0.0, "mark_pnl_usdc": 0.0})
    for asset, position in sorted(positions.items()):
        mark = latest_bid.get(asset)
        mark_value = position["quantity"] * mark if mark is not None else 0.0
        pnl = mark_value - position["cost_usdc"]
        unrealised += pnl
        total_cost += position["cost_usdc"]
        stats = by_category[position["category"]]
        stats["positions"] += 1
        stats["cost_usdc"] += position["cost_usdc"]
        stats["mark_pnl_usdc"] += pnl
        open_positions.append(
            {
                **position,
                "quantity": round(position["quantity"], 6),
                "cost_usdc": round(position["cost_usdc"], 6),
                "latest_bid": mark,
                "mark_pnl_usdc": round(pnl, 6),
            }
        )

    summary = {
        "status": "ok",
        "strategy": strategy_name,
        "features_input": str(features_path),
        "events_processed": len(events),
        "intents_emitted": intents_emitted,
        "fills": len(fills),
        "cancels": cancels,
        "expiries": expiries,
        "skipped_open_exposure": skipped_open_exposure,
        "resting_orders_at_end": len(resting),
        "downgraded_intents": len(downgrade_notes),
        "open_positions": open_positions,
        "total_cost_usdc": round(total_cost, 6),
        "unrealised_mark_to_bid_pnl_usdc": round(unrealised, 6),
        "by_category": {name: {k: round(v, 6) for k, v in stats.items()} for name, stats in sorted(by_category.items())},
        "fill_semantics": {
            "cross_spread": "IOC; fills at event best ask only when the limit crosses it",
            "join_bid/work_midpoint": "GTD; fills at limit only when a later ask crosses down; expires past expire_at_utc",
            "marking": "open positions marked to latest best bid, never midpoint",
        },
        "paper_trading_invoked": False,
        "live_trading_invoked": False,
    }
    if write_artifacts:
        out_root = cfg.output_root / "polymarket_algo"
        write_json(out_root / f"replay_{strategy_name}_summary.json", summary)
        write_csv(out_root / f"replay_{strategy_name}_fills.csv", fills, fieldnames=FILL_FIELDS)
    return summary


def main(config_path: str, strategy_name: str = "null") -> dict[str, Any]:
    return run_replay(load_config(config_path), strategy_name)
