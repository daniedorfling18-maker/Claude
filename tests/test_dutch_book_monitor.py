from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket_predictive_engine.dutch_book_monitor import (
    COMPLETE,
    DUPLICATE,
    INCOMPLETE,
    evaluate_dutch_book_candidate,
    fetch_complete_outcome_universe,
    latest_live_books,
)


def _market(*, outcomes=None, token_ids=None, end_days=365, closed=False):
    outcomes = outcomes or ["Alice", "Bob"]
    token_ids = token_ids or ["t1", "t2"]
    return {
        "slug": "demo-election",
        "question": "Who wins the demo election?",
        "category": "election",
        "closed": closed,
        "active": not closed,
        "archived": False,
        "endDate": (datetime.now(timezone.utc) + timedelta(days=end_days)).isoformat(),
        "outcomes": str(outcomes).replace("'", '"'),
        "clobTokenIds": str(token_ids).replace("'", '"'),
    }


def _books(asks, *, stale=False, depth=100):
    collected = datetime.now(timezone.utc) - timedelta(seconds=9999 if stale else 1)
    rows = []
    for token_id, ask in asks.items():
        rows.append({
            "asset_id": token_id,
            "collected_at_utc": collected.isoformat(),
            "best_bid": ask - 0.01,
            "best_ask": ask,
            "top_ask_size": depth,
            "ask_depth_1pct": depth,
        })
    return latest_live_books(rows, stale_after_seconds=60)


def test_incomplete_outcome_set_is_rejected() -> None:
    universe = fetch_complete_outcome_universe(
        {"market_slug": "demo"},
        fetcher=lambda _: _market(outcomes=["Alice", "Bob"], token_ids=["t1"]),
    )
    assert universe["status"] == INCOMPLETE


def test_duplicate_outcomes_are_rejected() -> None:
    universe = fetch_complete_outcome_universe(
        {"market_slug": "demo"},
        fetcher=lambda _: _market(outcomes=["Alice", "Alice"], token_ids=["t1", "t2"]),
    )
    assert universe["status"] == DUPLICATE


def test_complete_set_with_ask_sum_below_one_is_detected() -> None:
    candidate, rejected, status = evaluate_dutch_book_candidate(
        {"market_slug": "demo"},
        _books({"t1": 0.45, "t2": 0.45}, depth=1000),
        fetcher=lambda _: _market(end_days=365),
        settings={
            "slippage_buffer": 0.0,
            "fee_buffer": 0.0,
            "bankroll": 1000,
            "maximum_event_exposure": 0.1,
            "liquidity_cap_fraction": 1.0,
            "minimum_capital_required": 1.0,
            "minimum_annualized_return": 0.01,
            "maximum_days_to_resolution": 1000,
        },
    )
    assert rejected == {}
    assert candidate is not None
    assert candidate["ask_sum"] == pytest.approx(0.90)
    assert candidate["net_locked_profit"] > 0
    assert status["completeness_status"] == COMPLETE


def test_complete_set_with_ask_sum_at_or_above_one_is_not_detected() -> None:
    candidate, rejected, _ = evaluate_dutch_book_candidate(
        {"market_slug": "demo"},
        _books({"t1": 0.51, "t2": 0.50}, depth=1000),
        fetcher=lambda _: _market(),
        settings={"slippage_buffer": 0.0, "fee_buffer": 0.0, "liquidity_cap_fraction": 1.0},
    )
    assert candidate is None
    assert "ask_sum >= 1" in rejected["rejection_reason"]


def test_stale_books_are_rejected() -> None:
    candidate, rejected, _ = evaluate_dutch_book_candidate(
        {"market_slug": "demo"},
        _books({"t1": 0.45, "t2": 0.45}, stale=True),
        fetcher=lambda _: _market(),
        settings={"slippage_buffer": 0.0, "fee_buffer": 0.0},
    )
    assert candidate is None
    assert "stale" in rejected["rejection_reason"]


def test_low_depth_locks_are_capped_or_rejected() -> None:
    candidate, rejected, _ = evaluate_dutch_book_candidate(
        {"market_slug": "demo"},
        _books({"t1": 0.45, "t2": 0.45}, depth=0.01),
        fetcher=lambda _: _market(),
        settings={
            "slippage_buffer": 0.0,
            "fee_buffer": 0.0,
            "bankroll": 1000,
            "maximum_event_exposure": 0.1,
            "liquidity_cap_fraction": 1.0,
            "minimum_capital_required": 1.0,
        },
    )
    assert candidate is None
    assert "low-depth" in rejected["rejection_reason"]


def test_long_horizon_locks_show_annualized_return() -> None:
    candidate, rejected, _ = evaluate_dutch_book_candidate(
        {"market_slug": "demo"},
        _books({"t1": 0.45, "t2": 0.45}, depth=1000),
        fetcher=lambda _: _market(end_days=900),
        settings={
            "slippage_buffer": 0.0,
            "fee_buffer": 0.0,
            "bankroll": 1000,
            "maximum_event_exposure": 0.1,
            "liquidity_cap_fraction": 1.0,
            "minimum_capital_required": 1.0,
            "minimum_annualized_return": 0.001,
            "maximum_days_to_resolution": 1000,
        },
    )
    assert rejected == {}
    assert candidate is not None
    assert candidate["days_to_resolution"] > 800
    assert candidate["expected_annualized_return"] is not None


def test_no_order_placement_is_possible() -> None:
    candidate, rejected, _ = evaluate_dutch_book_candidate(
        {"market_slug": "demo"},
        _books({"t1": 0.45, "t2": 0.45}, depth=1000),
        fetcher=lambda _: _market(),
        settings={"slippage_buffer": 0.0, "fee_buffer": 0.0, "liquidity_cap_fraction": 1.0, "minimum_annualized_return": 0.001},
    )
    assert rejected == {}
    assert candidate is not None
    assert candidate["dry_run_only"] is True
    assert candidate["orders_placed"] == 0
