from __future__ import annotations

from polymarket_predictive_engine.anchored_edge import _candidate_status, _edge_after_penalties


def test_edge_after_penalties_haircuts_raw_anchor_edge() -> None:
    edge, parts = _edge_after_penalties(
        anchor_probability=0.62,
        executable_price=0.55,
        spread=0.02,
        liquidity=500.0,
        settings={
            "spread_penalty_weight": 0.50,
            "liquidity_penalty_weight": 0.02,
            "uncertainty_penalty": 0.005,
            "reference_liquidity": 1000.0,
        },
    )

    assert round(parts["anchor_raw_edge"], 6) == 0.07
    assert round(parts["spread_penalty"], 6) == 0.01
    assert round(parts["liquidity_penalty"], 6) == 0.01
    assert round(parts["uncertainty_penalty"], 6) == 0.005
    assert round(edge, 6) == 0.045


def test_unknown_family_is_rejected_even_with_anchor() -> None:
    status, blockers = _candidate_status(
        family="unknown",
        rule=None,
        anchor={"anchor_fair_probability": 0.7},
        price=0.5,
        spread=0.01,
        relative_spread=0.02,
        liquidity=1000.0,
        edge_after_penalty=0.12,
        settings={
            "watchlist_min_edge_after_penalty": 0.03,
            "shadow_min_edge_after_penalty": 0.05,
        },
    )

    assert status == "rejected"
    assert "family_unknown" in blockers
    assert "family_not_accepted" in blockers


def test_shadow_candidate_requires_clean_anchor_edge_and_microstructure() -> None:
    rule = {
        "status": "accepted",
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    }
    status, blockers = _candidate_status(
        family="macro_rates",
        rule=rule,
        anchor={"anchor_fair_probability": 0.7},
        price=0.6,
        spread=0.01,
        relative_spread=0.0167,
        liquidity=1000.0,
        edge_after_penalty=0.06,
        settings={
            "watchlist_min_edge_after_penalty": 0.03,
            "shadow_min_edge_after_penalty": 0.05,
        },
    )

    assert status == "shadow_candidate"
    assert blockers == []


def test_research_only_family_is_not_actionable() -> None:
    rule = {
        "status": "research_only_until_anchor_methodology_defined",
        "research_only": True,
        "normal_max_spread": 0.02,
        "normal_max_relative_spread": 0.15,
        "normal_min_liquidity": 250.0,
    }
    status, blockers = _candidate_status(
        family="ai_model_leader",
        rule=rule,
        anchor={"anchor_fair_probability": 0.7},
        price=0.6,
        spread=0.01,
        relative_spread=0.0167,
        liquidity=1000.0,
        edge_after_penalty=0.06,
        settings={
            "watchlist_min_edge_after_penalty": 0.03,
            "shadow_min_edge_after_penalty": 0.05,
        },
    )

    assert status == "rejected"
    assert "family_research_only_until_anchor_methodology_defined" in blockers
