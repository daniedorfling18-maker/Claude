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
    assert "bid_momentum_tight_shadow" in names
    assert "mid_momentum_tight_shadow" in names
    assert "spread_compression_shadow" in names
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


def test_bid_momentum_tight_shadow_emits_only_after_previous_bid_move(tmp_path: Path):
    strategy = get_strategy("bid_momentum_tight_shadow")
    context = _context(tmp_path, algo={"min_bid_move": 0.01, "tight_spread_maximum": 0.02, "shadow_stake_usdc": 2.0})

    first, notes = emit_intents(
        strategy,
        _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50, best_ask=0.51, midpoint=0.505, spread=0.01),
        context,
    )
    assert first == []
    assert notes == []
    second, notes = emit_intents(
        strategy,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.515, best_ask=0.525, midpoint=0.52, spread=0.01),
        context,
    )

    assert notes == []
    assert len(second) == 1
    intent = second[0]
    assert intent.mode == "shadow"
    assert intent.source_strategy == "bid_momentum_tight_shadow"
    assert intent.execution_policy == "join_bid"
    assert intent.limit_price == pytest.approx(0.515)
    assert intent.quantity == pytest.approx(round(2.0 / 0.515, 6))
    assert intent.expire_at_utc == "2026-07-02T10:31:00Z"

    repeat_strategy = get_strategy("bid_momentum_tight_shadow")
    emit_intents(
        repeat_strategy,
        _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50, best_ask=0.51, midpoint=0.505, spread=0.01),
        context,
    )
    repeat, _ = emit_intents(
        repeat_strategy,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.515, best_ask=0.525, midpoint=0.52, spread=0.01),
        context,
    )
    assert repeat[0].intent_id == intent.intent_id


def test_bid_momentum_tight_shadow_respects_move_spread_and_missing_fields(tmp_path: Path):
    context = _context(tmp_path, algo={"min_bid_move": 0.01, "tight_spread_maximum": 0.02})

    below = get_strategy("bid_momentum_tight_shadow")
    emit_intents(below, _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50), context)
    intents, _ = emit_intents(below, _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.505), context)
    assert intents == []

    wide = get_strategy("bid_momentum_tight_shadow")
    emit_intents(wide, _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50), context)
    intents, _ = emit_intents(
        wide,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.52, best_ask=0.56, midpoint=0.54, spread=0.04),
        context,
    )
    assert intents == []

    missing = get_strategy("bid_momentum_tight_shadow")
    emit_intents(missing, _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50), context)
    intents, _ = emit_intents(
        missing,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid="", best_ask=0.53, midpoint=0.52, spread=0.01),
        context,
    )
    assert intents == []


def test_mid_momentum_tight_shadow_emits_only_after_previous_mid_move(tmp_path: Path):
    strategy = get_strategy("mid_momentum_tight_shadow")
    context = _context(tmp_path, algo={"min_mid_move": 0.01, "tight_spread_maximum": 0.02, "shadow_stake_usdc": 2.0})

    first, _ = emit_intents(
        strategy,
        _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50, best_ask=0.51, midpoint=0.505, spread=0.01),
        context,
    )
    assert first == []
    second, notes = emit_intents(
        strategy,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.51, best_ask=0.52, midpoint=0.515, spread=0.01),
        context,
    )

    assert notes == []
    assert len(second) == 1
    intent = second[0]
    assert intent.mode == "shadow"
    assert intent.source_strategy == "mid_momentum_tight_shadow"
    assert intent.execution_policy == "join_bid"
    assert intent.limit_price == pytest.approx(0.51)
    assert intent.quantity == pytest.approx(round(2.0 / 0.51, 6))

    repeat_strategy = get_strategy("mid_momentum_tight_shadow")
    emit_intents(
        repeat_strategy,
        _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.50, best_ask=0.51, midpoint=0.505, spread=0.01),
        context,
    )
    repeat, _ = emit_intents(
        repeat_strategy,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.51, best_ask=0.52, midpoint=0.515, spread=0.01),
        context,
    )
    assert repeat[0].intent_id == intent.intent_id


def test_mid_momentum_tight_shadow_respects_move_spread_and_missing_fields(tmp_path: Path):
    context = _context(tmp_path, algo={"min_mid_move": 0.01, "tight_spread_maximum": 0.02})

    below = get_strategy("mid_momentum_tight_shadow")
    emit_intents(below, _event(source_timestamp="2026-07-02T10:00:00Z", midpoint=0.505), context)
    intents, _ = emit_intents(below, _event(source_timestamp="2026-07-02T10:01:00Z", midpoint=0.510), context)
    assert intents == []

    wide = get_strategy("mid_momentum_tight_shadow")
    emit_intents(wide, _event(source_timestamp="2026-07-02T10:00:00Z", midpoint=0.505), context)
    intents, _ = emit_intents(wide, _event(source_timestamp="2026-07-02T10:01:00Z", midpoint=0.525, spread=0.04), context)
    assert intents == []

    missing = get_strategy("mid_momentum_tight_shadow")
    emit_intents(missing, _event(source_timestamp="2026-07-02T10:00:00Z", midpoint=0.505), context)
    intents, _ = emit_intents(missing, _event(source_timestamp="2026-07-02T10:01:00Z", midpoint="", best_bid=0.52), context)
    assert intents == []


def test_spread_compression_shadow_emits_on_compression_with_bid_heavy_book(tmp_path: Path):
    strategy = get_strategy("spread_compression_shadow")
    context = _context(tmp_path, algo={"min_spread_compression": 0.01, "minimum_book_imbalance": 0.6, "shadow_stake_usdc": 2.0})

    first, _ = emit_intents(
        strategy,
        _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.48, best_ask=0.52, midpoint=0.50, spread=0.04, book_imbalance=0.7),
        context,
    )
    assert first == []
    second, notes = emit_intents(
        strategy,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.50, best_ask=0.52, midpoint=0.51, spread=0.02, book_imbalance=0.7),
        context,
    )

    assert notes == []
    assert len(second) == 1
    intent = second[0]
    assert intent.mode == "shadow"
    assert intent.source_strategy == "spread_compression_shadow"
    assert intent.execution_policy == "join_bid"
    assert intent.limit_price == pytest.approx(0.50)
    assert intent.quantity == pytest.approx(4.0)

    repeat_strategy = get_strategy("spread_compression_shadow")
    emit_intents(
        repeat_strategy,
        _event(source_timestamp="2026-07-02T10:00:00Z", best_bid=0.48, best_ask=0.52, midpoint=0.50, spread=0.04, book_imbalance=0.7),
        context,
    )
    repeat, _ = emit_intents(
        repeat_strategy,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid=0.50, best_ask=0.52, midpoint=0.51, spread=0.02, book_imbalance=0.7),
        context,
    )
    assert repeat[0].intent_id == intent.intent_id


def test_spread_compression_shadow_respects_compression_imbalance_and_missing_fields(tmp_path: Path):
    context = _context(tmp_path, algo={"min_spread_compression": 0.01, "minimum_book_imbalance": 0.6})

    too_small = get_strategy("spread_compression_shadow")
    emit_intents(too_small, _event(source_timestamp="2026-07-02T10:00:00Z", spread=0.03), context)
    intents, _ = emit_intents(too_small, _event(source_timestamp="2026-07-02T10:01:00Z", spread=0.025, book_imbalance=0.7), context)
    assert intents == []

    weak_book = get_strategy("spread_compression_shadow")
    emit_intents(weak_book, _event(source_timestamp="2026-07-02T10:00:00Z", spread=0.04), context)
    intents, _ = emit_intents(weak_book, _event(source_timestamp="2026-07-02T10:01:00Z", spread=0.02, book_imbalance=0.4), context)
    assert intents == []

    missing = get_strategy("spread_compression_shadow")
    emit_intents(missing, _event(source_timestamp="2026-07-02T10:00:00Z", spread=0.04), context)
    intents, _ = emit_intents(
        missing,
        _event(source_timestamp="2026-07-02T10:01:00Z", best_bid="", best_ask="", midpoint=0.50, spread="", book_imbalance=0.7),
        context,
    )
    assert intents == []


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
