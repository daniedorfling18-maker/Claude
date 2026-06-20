from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script():
    path = ROOT / "scripts/fit_chaser_profiles_from_round_summary.py"
    spec = importlib.util.spec_from_file_location("fit_chaser_profiles_from_round_summary", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prepare_rounds_derives_rates_and_wrong_count():
    script = load_script()
    frame = pd.DataFrame([
        {"round": 1, "player": "Player A", "round_points": 16.5, "total_matches": 24, "exact_count": 2, "close_count": 5, "result_count": 3}
    ])
    prepared = script.prepare_rounds(frame)
    row = prepared.iloc[0]
    assert row["wrong_count"] == 14
    assert abs(row["points_per_match"] - 0.6875) < 1e-9
    assert abs(row["weighted_accuracy"] - (16.5 / 72)) < 1e-9


def test_behaviour_between_rounds_flags_improvement():
    script = load_script()
    rounds = script.prepare_rounds(pd.DataFrame([
        {"round": 1, "player": "Player A", "round_points": 16.5, "total_matches": 24, "exact_count": 2, "close_count": 5, "result_count": 3},
        {"round": 2, "player": "Player A", "round_points": 12.5, "total_matches": 8, "exact_count": 3, "close_count": 1, "result_count": 2},
    ]))
    trends = script.behaviour_between_rounds(rounds)
    round2 = trends[trends["round"] == "2"].iloc[0]
    assert round2["trend"] == "improved"
    assert round2["delta_points_per_match"] > 0
    assert round2["delta_wrong_rate"] < 0


def test_fit_round_summary_profiles_weights_sum_to_one():
    script = load_script()
    rounds = script.prepare_rounds(pd.DataFrame([
        {"round": 1, "player": "Player A", "round_points": 16.5, "total_matches": 24, "exact_count": 2, "close_count": 5, "result_count": 3},
        {"round": 2, "player": "Player A", "round_points": 12.5, "total_matches": 8, "exact_count": 3, "close_count": 1, "result_count": 2},
    ]))
    profiles = script.fit_round_summary_profiles(rounds, laplace=0.25, min_rounds=2, min_matches=8)
    row = profiles.iloc[0]
    total = sum(float(row[col]) for col in script.PROFILE_COLUMNS)
    assert abs(total - 1.0) < 1e-5
    assert row["low_sample_flag"] == 0
