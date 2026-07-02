from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_predictive_engine.algo.base import StrategyContext
from polymarket_predictive_engine.algo.events import QuoteEvent
from polymarket_predictive_engine.algo.registry import available_strategies, emit_intents, get_strategy
from polymarket_predictive_engine.config import EngineConfig
from polymarket_predictive_engine.execution.intents import OrderIntent


def _event(**overrides) -> QuoteEvent:
    row = {
        "source_timestamp": "2026-07-02T10:00:00Z",
        "market": "m1",
        "asset_id": "t1",
        "market_slug": "slug-1",
        "question": "Question?",
        "category": "sports_other",
        "close_time": "2026-07-03T00:00:00Z",
        "best_bid": 0.50,
        "best_ask": 0.51,
        "midpoint": 0.505,
        "spread": 0.01,
        "book_imbalance": 0.8,
        "top_bid_size": 500,
        "top_ask_size": 100,
    }
    row.update(overrides)
    event = QuoteEvent.from_feature_row(row)
    assert event is not None
    return event


def _context(tmp_path: Path, algo: dict | None = None) -> StrategyContext:
    raw = {"paths": {"output_root": str(tmp_path / "outputs")}}
    if algo is not None:
        raw["algo"] = algo
    return StrategyContext(config=EngineConfig(raw=raw, path=tmp_path / "cfg.yaml"), now_utc="2026-07-02T10:00:00Z")


def test_registry_has_builtins_and_rejects_unknown():
    names = available_strategies()
    assert "null" in names
    assert "tight_spread_join_bid_shadow" in names
    with pytest.raises(KeyError, match="unknown algo strategy"):
        get_strategy("does_not_exist")


def test_null_strategy_never_trades(tmp_path: Path):
    intents, notes = emit_intents(get_strategy("null"), _event(), _context(tmp_path))
    assert intents == []
    assert notes == []


def test_tight_spread_strategy_emits_expected_shadow_intent(tmp_path: Path):
    strategy = get_strategy("tight_spread_join_bid_shadow")
    intents, notes = emit_intents(strategy, _event(), _context(tmp_path))
    assert notes == []
    assert len(intents) == 1
    intent = intents[0]
    assert intent.mode == "shadow"
    assert intent.side == "BUY"
    assert intent.execution_policy == "join_bid"
    assert intent.limit_price == 0.50
    assert intent.quantity == pytest.approx(2.0)  # 1 USDC / 0.50 bid
    assert intent.time_in_force == "GTD"
    assert intent.expire_at_utc == "2026-07-02T10:30:00Z"
    # Deterministic: same event, same intent id.
    again, _ = emit_intents(strategy, _event(), _context(tmp_path))
    assert again[0].intent_id == intent.intent_id


def test_tight_spread_strategy_respects_filters(tmp_path: Path):
    strategy = get_strategy("tight_spread_join_bid_shadow")
    context = _context(tmp_path)
    wide, _ = emit_intents(strategy, _event(spread=0.05), context)
    assert wide == []
    weak, _ = emit_intents(strategy, _event(book_imbalance=0.2), context)
    assert weak == []
    no_book, _ = emit_intents(strategy, _event(best_bid="", best_ask="", midpoint=0.5, spread=""), context)
    assert no_book == []


def test_wrapper_downgrades_paper_intents_by_default(tmp_path: Path):
    class PaperWanting:
        name = "paper_wanting"

        def on_quote(self, event, context):
            base = get_strategy("tight_spread_join_bid_shadow").on_quote(event, context)[0]
            from dataclasses import replace

            return [replace(base, mode="paper", source_strategy=self.name)]

        def on_fill(self, fill, context):
            return None

    intents, notes = emit_intents(PaperWanting(), _event(), _context(tmp_path))
    assert intents[0].mode == "shadow"
    assert notes[0]["action"] == "downgraded_to_shadow"
    assert notes[0]["requested_mode"] == "paper"

    # Explicit config approval keeps the paper mode.
    approved_context = _context(tmp_path, algo={"allow_paper_intents": True, "paper_approved_strategies": ["paper_wanting"]})
    intents, notes = emit_intents(PaperWanting(), _event(), approved_context)
    assert intents[0].mode == "paper"
    assert notes == []


def test_wrapper_rejects_invalid_intents(tmp_path: Path):
    class Broken:
        name = "broken"

        def on_quote(self, event, context):
            return [
                OrderIntent(
                    intent_id="x",
                    created_at_utc=event.timestamp_utc,
                    market_id=event.market_id,
                    token_id=event.asset_id,
                    side="BUY",
                    quantity=-1.0,
                    limit_price=0.5,
                    time_in_force="IOC",
                    expire_at_utc="",
                    execution_policy="cross_spread",
                    max_slippage=0.0,
                    mode="shadow",
                    source_strategy="broken",
                    signal_ref="x",
                )
            ]

        def on_fill(self, fill, context):
            return None

    with pytest.raises(ValueError, match="quantity"):
        emit_intents(Broken(), _event(), _context(tmp_path))
