from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_superbru_points_vector_exact_close_outcome_wrong():
    mc = load_script("leaderboard_mc", "scripts/run_leaderboard_monte_carlo.py")

    actual_home = np.array([2, 2, 1, 0])
    actual_away = np.array([0, 1, 0, 1])

    points = mc.superbru_points_vector(
        pred_home=2,
        pred_away=0,
        actual_home=actual_home,
        actual_away=actual_away,
        ci_cutoff=1.5,
    )

    assert points.tolist() == [3.0, 1.5, 1.5, 0.0]


def test_paired_delta_stats_detects_positive_delta():
    mc = load_script("leaderboard_mc", "scripts/run_leaderboard_monte_carlo.py")

    base = np.array([True, False, False, True, False])
    candidate = np.array([True, True, False, True, True])

    delta, se, z = mc.paired_delta_stats(base, candidate)

    assert delta > 0
    assert se > 0
    assert z > 0


def test_final_policy_requires_base_stress_and_confirmation_agreement():
    final_decision = load_script("final_decision", "scripts/run_final_leader_decision.py")

    confirmation_picks = pd.DataFrame(
        [
            {
                "commence_time": "2026-06-21T00:00:00Z",
                "home_team": "Ecuador",
                "away_team": "Curacao",
                "raw_pick": "2-0",
                "leader_mc_pick": "3-0",
                "switched": True,
                "ev_loss": 0.01,
                "risk_tier": "low",
                "confidence_tier": "medium",
            }
        ]
    )
    base_summary = {
        "accepted_switches": [
            {"home_team": "Ecuador", "away_team": "Curacao", "raw_pick": "2-0", "candidate_pick": "3-0"}
        ]
    }
    confirmation_summary = {
        "accepted_switches": [
            {"home_team": "Ecuador", "away_team": "Curacao", "raw_pick": "2-0", "candidate_pick": "3-0"}
        ]
    }
    stress_support = pd.DataFrame(
        [
            {
                "home_team": "Ecuador",
                "away_team": "Curacao",
                "raw_pick": "2-0",
                "candidate_pick": "3-0",
                "robust_switch": False,
            }
        ]
    )

    final_picks, accepted, rejected = final_decision.build_final_picks(
        confirmation_picks=confirmation_picks,
        base_summary=base_summary,
        stress_support=stress_support,
        confirmation_summary=confirmation_summary,
    )

    assert accepted == []
    assert len(rejected) == 2
    assert final_picks.loc[0, "final_pick"] == "2-0"
    assert bool(final_picks.loc[0, "final_switched"]) is False


def test_final_policy_accepts_only_when_all_three_layers_agree():
    final_decision = load_script("final_decision", "scripts/run_final_leader_decision.py")

    confirmation_picks = pd.DataFrame(
        [
            {
                "commence_time": "2026-06-21T00:00:00Z",
                "home_team": "Ecuador",
                "away_team": "Curacao",
                "raw_pick": "2-0",
                "leader_mc_pick": "3-0",
                "switched": True,
                "ev_loss": 0.01,
                "risk_tier": "low",
                "confidence_tier": "medium",
            }
        ]
    )
    switch = {"home_team": "Ecuador", "away_team": "Curacao", "raw_pick": "2-0", "candidate_pick": "3-0"}
    base_summary = {"accepted_switches": [switch]}
    confirmation_summary = {"accepted_switches": [switch]}
    stress_support = pd.DataFrame([{**switch, "robust_switch": True}])

    final_picks, accepted, rejected = final_decision.build_final_picks(
        confirmation_picks=confirmation_picks,
        base_summary=base_summary,
        stress_support=stress_support,
        confirmation_summary=confirmation_summary,
    )

    assert len(accepted) == 1
    assert rejected == []
    assert final_picks.loc[0, "final_pick"] == "3-0"
    assert bool(final_picks.loc[0, "final_switched"]) is True
