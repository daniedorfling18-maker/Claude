from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts/fit_chaser_profiles_from_points.py"
    spec = importlib.util.spec_from_file_location("fit_chaser_profiles_from_points", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_points_inference_allocates_exact_to_matching_bucket():
    script = load_script()
    row = pd.Series(
        {
            "recommended_scoreline": "2-0",
            "raw_ev_scoreline": "2-0",
            "top1_scoreline": "2-0",
            "top2_scoreline": "2-1",
            "top3_scoreline": "3-0",
            "modal_scoreline": "1-0",
            "private_chase_scoreline": "3-0",
        }
    )

    likelihoods = script.bucket_likelihoods(row, "2-0", 3.0, 1.5)

    assert likelihoods["raw_weight"] == 1.0
    assert likelihoods["top1_weight"] == 1.0
    assert likelihoods["top2_weight"] == 0.0
    assert likelihoods["modal_weight"] == 0.0
    assert likelihoods["chalk_weight"] > 0.0


def test_normalize_wide_points_uses_match_order_mapping():
    script = load_script()
    points = pd.DataFrame(
        [
            {"round": "1", "player": "Danie", "match_1": 3, "match_2": 1.5},
            {"round": "1", "player": "Dheben", "match_1": 1, "match_2": 0},
        ]
    )
    order = pd.DataFrame(
        [
            {"round": "1", "match_order": 1, "home_team": "Mexico", "away_team": "South Africa", "actual_scoreline": "2-0"},
            {"round": "1", "match_order": 2, "home_team": "South Korea", "away_team": "Czech Republic", "actual_scoreline": "2-1"},
        ]
    )

    normalized = script.normalize_points(points, order)

    assert len(normalized) == 4
    assert set(normalized["player"]) == {"Danie", "Dheben"}
    assert set(normalized["actual_scoreline"]) == {"2-0", "2-1"}


def test_fit_profiles_from_points_returns_weights_that_sum_to_one():
    script = load_script()
    merged = pd.DataFrame(
        [
            {
                "player": "Danie",
                "points": 3.0,
                "actual_scoreline": "2-0",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "recommended_scoreline": "2-0",
                "raw_ev_scoreline": "2-0",
                "top1_scoreline": "2-0",
                "top2_scoreline": "2-1",
                "top3_scoreline": "3-0",
                "modal_scoreline": "1-0",
                "private_chase_scoreline": "3-0",
            },
            {
                "player": "Danie",
                "points": 1.5,
                "actual_scoreline": "2-1",
                "home_team": "South Korea",
                "away_team": "Czech Republic",
                "recommended_scoreline": "2-0",
                "raw_ev_scoreline": "2-0",
                "top1_scoreline": "2-0",
                "top2_scoreline": "2-1",
                "top3_scoreline": "3-0",
                "modal_scoreline": "1-0",
                "private_chase_scoreline": "3-0",
            },
        ]
    )

    profiles, detail, summary = script.fit_profiles(merged, ci_cutoff=1.5, laplace=0.5, min_rows=1)

    assert len(detail) == 2
    assert summary["matched_point_rows"] == 2
    row = profiles.iloc[0]
    total = sum(float(row[col]) for col in script.PROFILE_COLUMNS)
    assert abs(total - 1.0) < 1e-5
