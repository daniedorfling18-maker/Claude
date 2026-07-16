from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from polymarket_predictive_engine.discovery_policy import (
    FROZEN_UPDOWN_REASON,
    PRIMARY_HYPOTHESES,
    is_frozen_updown_record,
    is_frozen_updown_text,
    partition_active_queries,
    primary_hypothesis_coverage,
    primary_hypothesis_lanes,
    primary_hypothesis_targets,
)


FIXTURE = Path(__file__).parents[1] / "fixtures" / "recorded" / "vps_discovery_starvation_2026-07-16.json"


def test_recorded_starvation_queries_are_hard_partitioned() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    observed = [
        *payload["live_scan"]["selected_queries"],
        *payload["liquidity_discovery"]["adaptive_collection_queries"],
        *payload["liquidity_discovery"]["first_expanded_queries"],
    ]

    active, excluded = partition_active_queries(observed)

    assert active == ["bitcoin", "world cup", "tennis", "economy", "fed", "mlb", "nba", "esports"]
    assert all(not is_frozen_updown_text(query) for query in active)
    assert {row["query"] for row in excluded} >= {
        "btc updown",
        "eth updown",
        "xrp updown",
        "solana updown",
        "bitcoin up or down",
        "btc updown 5m",
    }
    assert {row["reason"] for row in excluded} == {FROZEN_UPDOWN_REASON}


def test_updown_classifier_covers_query_slug_and_underscore_cohort_shapes() -> None:
    frozen_values = (
        "BTC Up or Down",
        "btc-updown-5m-1780000000",
        "crypto_btc_updown_daily",
        "crypto/eth/up_down/15m",
        "SOL UP-DOWN",
    )

    assert all(is_frozen_updown_text(value) for value in frozen_values)
    assert not is_frozen_updown_text("Will Bitcoin exceed $100,000?")
    assert is_frozen_updown_record({"signal_cohort": "model|crypto_xrp_updown_event|outcome=up"})
    assert is_frozen_updown_record(SimpleNamespace(market_slug="sol-up-or-down-july-16"))
    assert not is_frozen_updown_record({"question": "Will the Fed cut rates?"})


def test_configured_updown_values_cannot_restore_a_primary_lane() -> None:
    raw = {
        "research_surface_discovery": {
            "primary_hypothesis_queries": {
                hypothesis: ["btc updown", "eth updown"]
                for hypothesis in PRIMARY_HYPOTHESES
            }
        }
    }

    lanes = primary_hypothesis_lanes(raw)
    targets = primary_hypothesis_targets(raw, scan_sequence=1)

    assert all(lanes[hypothesis] for hypothesis in PRIMARY_HYPOTHESES)
    assert all(not is_frozen_updown_text(query) for values in lanes.values() for query in values)
    assert targets == {
        "h1_sharp_anchor": "world cup",
        "h2_dutch_consistency": "sports",
        "h3_structural_smart_flow": "politics",
    }


def test_primary_hypothesis_targets_rotate_deterministically() -> None:
    raw: dict[str, object] = {}

    assert primary_hypothesis_targets(raw, scan_sequence=1) == {
        "h1_sharp_anchor": "world cup",
        "h2_dutch_consistency": "sports",
        "h3_structural_smart_flow": "politics",
    }
    assert primary_hypothesis_targets(raw, scan_sequence=2) == {
        "h1_sharp_anchor": "tennis",
        "h2_dutch_consistency": "sports",
        "h3_structural_smart_flow": "elections",
    }
    assert primary_hypothesis_targets(raw, scan_sequence=6) == {
        "h1_sharp_anchor": "world cup",
        "h2_dutch_consistency": "sports",
        "h3_structural_smart_flow": "politics",
    }


def test_coverage_reports_starvation_mechanically() -> None:
    complete = primary_hypothesis_coverage(["tennis", "sports", "fed"], {})
    starved = primary_hypothesis_coverage(["world cup", "tennis"], {})

    assert complete["status"] == "ok"
    assert complete["missing_hypotheses"] == []
    assert starved["status"] == "starved"
    assert starved["missing_hypotheses"] == ["h2_dutch_consistency", "h3_structural_smart_flow"]
