from __future__ import annotations

from pytest import approx

from polymarket_predictive_engine.execution_costs import estimate_execution_cost


def test_execution_cost_missing_depth_preserves_flat_slippage():
    estimate = estimate_execution_cost(
        {"best_ask": 0.5, "spread": 0.02},
        stake_usdc=10,
        flat_slippage=0.015,
    )

    assert estimate["status"] == "missing_depth"
    assert estimate["expected_slippage"] == approx(0.015)
    assert estimate["max_stake_at_acceptable_impact_usdc"] == ""


def test_execution_cost_deep_top_book_can_reduce_flat_slippage():
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
    assert estimate["depth_is_demonstrably_deep"] is True
    assert estimate["expected_slippage"] == 0.0
    assert estimate["max_stake_at_acceptable_impact_usdc"] == approx(75.0)


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
