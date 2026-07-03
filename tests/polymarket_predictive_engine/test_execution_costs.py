from __future__ import annotations

from pytest import approx

from polymarket_predictive_engine.execution_costs import estimate_execution_cost


def test_execution_cost_missing_depth_fails_closed():
    estimate = estimate_execution_cost(
        {"best_ask": 0.5, "spread": 0.02},
        stake_usdc=10,
        flat_slippage=0.015,
    )

    assert estimate["status"] == "missing_depth"
    assert estimate["expected_slippage"] == approx(0.025)
    assert estimate["max_stake_at_acceptable_impact_usdc"] == 0.0
    assert estimate["quote_is_fresh"] is False


def test_execution_cost_deep_top_book_with_fresh_quote_can_reduce_flat_slippage():
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 100,
            "ask_depth_1pct": 150,
            "ask_depth_5pct": 200,
        },
        stake_usdc=10,
        flat_slippage=0.015,
        quote_age_seconds=30,
    )

    assert estimate["status"] == "top_of_book_fill"
    assert estimate["depth_is_demonstrably_deep"] is True
    assert estimate["quote_is_fresh"] is True
    assert estimate["expected_slippage"] == 0.0
    assert estimate["max_stake_at_acceptable_impact_usdc"] == approx(75.0)


def test_execution_cost_unknown_quote_age_never_goes_below_flat():
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 100,
            "ask_depth_1pct": 150,
            "ask_depth_5pct": 200,
        },
        stake_usdc=10,
        flat_slippage=0.015,
    )

    assert estimate["status"] == "top_of_book_fill"
    assert estimate["depth_is_demonstrably_deep"] is False
    assert estimate["quote_is_fresh"] is False
    assert estimate["expected_slippage"] == approx(0.015)


def test_execution_cost_stale_quote_never_goes_below_flat():
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 100,
            "ask_depth_1pct": 150,
            "ask_depth_5pct": 200,
            "websocket_quote_age_seconds": 900,
        },
        stake_usdc=10,
        flat_slippage=0.015,
    )

    assert estimate["quote_is_fresh"] is False
    assert estimate["depth_is_demonstrably_deep"] is False
    assert estimate["expected_slippage"] == approx(0.015)


def test_execution_cost_reads_row_quote_age_field():
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 100,
            "ask_depth_1pct": 150,
            "ask_depth_5pct": 200,
            "websocket_quote_age_seconds": 45,
        },
        stake_usdc=10,
        flat_slippage=0.015,
    )

    assert estimate["quote_is_fresh"] is True
    assert estimate["quote_age_seconds"] == approx(45.0)
    assert estimate["expected_slippage"] == 0.0


def test_execution_cost_freshness_never_relaxes_shallow_books():
    # Freshness only unlocks below-flat discounts; it must not soften the
    # shallow-book escalation.
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 2,
            "ask_depth_1pct": 5,
            "ask_depth_5pct": 8,
        },
        stake_usdc=10,
        flat_slippage=0.0,
        quote_age_seconds=5,
    )

    assert estimate["status"] == "insufficient_depth"
    assert estimate["expected_slippage"] >= 0.025


def test_execution_cost_shallow_book_increases_slippage_and_caps_stake():
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 2,
            "ask_depth_1pct": 5,
            "ask_depth_5pct": 8,
        },
        stake_usdc=10,
        flat_slippage=0.0,
    )

    assert estimate["status"] == "insufficient_depth"
    assert estimate["expected_slippage"] >= 0.025
    assert estimate["max_stake_at_acceptable_impact_usdc"] == approx(2.5)


def test_execution_cost_within_depth_never_goes_below_flat_unless_deep():
    estimate = estimate_execution_cost(
        {
            "best_ask": 0.5,
            "spread": 0.02,
            "top_ask_size": 5,
            "ask_depth_1pct": 50,
            "ask_depth_5pct": 60,
        },
        stake_usdc=10,
        flat_slippage=0.015,
    )

    assert estimate["status"] == "within_1pct_depth"
    assert estimate["depth_is_demonstrably_deep"] is False
    assert estimate["expected_slippage"] == approx(0.015)
