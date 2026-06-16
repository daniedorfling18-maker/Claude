from __future__ import annotations

import json
from pathlib import Path

from superbru_score_engine.config import RatingsConfig
from superbru_score_engine.model.ratings import MatchResult, RatingsStore


def test_ratings_store_saves_metadata(tmp_path: Path) -> None:
    path = tmp_path / "ratings.json"
    config = RatingsConfig(source="unit_test", source_url="https://example.test/ratings", cutoff_date="2026-06-16", k_factor=20.0)
    ratings = RatingsStore(path, config)
    applied, skipped = ratings.update_results([MatchResult("A", "B", 2, 0, match_id="m1")])
    ratings.save()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert applied == 1
    assert skipped == 0
    assert payload["_metadata"]["source"] == "unit_test"
    assert payload["_metadata"]["source_url"] == "https://example.test/ratings"
    assert payload["_metadata"]["cutoff_date"] == "2026-06-16"
    assert payload["_metadata"]["k_factor"] == 20.0
    assert payload["_metadata"]["number_of_applied_results"] == 1
    assert "_teams" in payload


def test_ratings_prior_lambdas_use_configurable_total_and_scale() -> None:
    ratings = RatingsStore(config=RatingsConfig(base_total_goals=3.0, elo_goal_scale=500.0, min_matches_full_confidence=1))
    ratings.update_results([MatchResult("A", "B", 4, 0, match_id="m1")])
    home, away = ratings.prior_lambdas("A", "B")
    assert home + away == 3.0
    assert home > away


def test_disabled_ratings_do_not_update() -> None:
    ratings = RatingsStore(config=RatingsConfig(enabled=False))
    applied, skipped = ratings.update_results([MatchResult("A", "B", 1, 0, match_id="m1")])
    assert applied == 0
    assert skipped == 0
    assert ratings.diagnostics()["number_of_applied_results"] == 0
