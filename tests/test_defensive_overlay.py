from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from superbru_score_engine.game_theory.defensive import (
    _chalk_scores,
    _outcome,
    _score_tuple,
    build_chaser_distribution,
    build_leader_candidates,
    estimate_candidate_ev,
    evaluate_match,
    run_defensive_model,
    should_exclude,
)


def _row(
    recommended="1-0",
    top1="1-0",
    top2="2-0",
    top3="2-1",
    private_chase="0-0",
    modal="1-1",
    expected_points=1.2,
    ev_gap=0.05,
    chase_loss=0.04,
    risk="medium",
    confidence="medium",
    stability=0.75,
    home="TeamA",
    away="TeamB",
    commence="2026-06-25T18:00:00Z",
) -> dict:
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "recommended_scoreline": recommended,
        "top1_scoreline": top1,
        "top2_scoreline": top2,
        "top3_scoreline": top3,
        "private_chase_scoreline": private_chase,
        "modal_scoreline": modal,
        "expected_points": expected_points,
        "ev_gap_to_second": ev_gap,
        "private_chase_ev_loss": chase_loss,
        "risk_tier": risk,
        "confidence_tier": confidence,
        "sensitivity_stability": stability,
    }


def test_score_tuple_valid():
    assert _score_tuple("1-0") == (1, 0)
    assert _score_tuple("2-1") == (2, 1)
    assert _score_tuple("10-3") == (10, 3)


def test_score_tuple_invalid():
    assert _score_tuple("") is None
    assert _score_tuple("nan") is None
    assert _score_tuple("abc") is None
    assert _score_tuple("1:0") is None


def test_outcome():
    assert _outcome("2-0") == "home"
    assert _outcome("0-1") == "away"
    assert _outcome("1-1") == "draw"
    assert _outcome("") == ""


def test_chalk_scores_home():
    scores = _chalk_scores("2-0")
    assert "2-0" in scores and "1-0" in scores


def test_chalk_scores_away():
    scores = _chalk_scores("0-2")
    assert "0-2" in scores and "0-1" in scores


def test_chalk_scores_draw():
    scores = _chalk_scores("1-1")
    assert "1-1" in scores and "0-0" in scores


def test_build_chaser_distribution_sums_to_one():
    row = _row()
    dist = build_chaser_distribution(row)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in dist.values())


def test_build_chaser_distribution_high_risk_boosts_chase():
    low_dist = build_chaser_distribution(_row(risk="low"))
    high_dist = build_chaser_distribution(_row(risk="high"))
    # High-risk rows should weight private_chase more
    low_chase = low_dist.get("0-0", 0.0)
    high_chase = high_dist.get("0-0", 0.0)
    assert high_chase >= low_chase


def test_build_leader_candidates_no_dupes():
    row = _row(recommended="1-0", top1="1-0", top2="2-0")
    candidates = build_leader_candidates(row)
    assert len(candidates) == len(set(candidates))
    assert "1-0" in candidates


def test_estimate_candidate_ev_recommended_returns_base():
    row = _row(expected_points=1.2)
    assert estimate_candidate_ev(row, "1-0") == pytest.approx(1.2)


def test_estimate_candidate_ev_private_chase_reduces():
    row = _row(expected_points=1.2, chase_loss=0.1)
    ev = estimate_candidate_ev(row, "0-0")
    assert ev == pytest.approx(max(0.0, 1.2 - 0.1))


def test_estimate_candidate_ev_top1_minimal_reduction():
    row = _row(expected_points=1.2, ev_gap=0.03)
    ev = estimate_candidate_ev(row, "1-0")
    assert ev == pytest.approx(1.2)  # top1 == recommended


def test_estimate_candidate_ev_unknown_bigger_reduction():
    row = _row(expected_points=1.2)
    ev = estimate_candidate_ev(row, "9-0")
    assert ev == pytest.approx(max(0.0, 1.2 - 0.12))


def test_evaluate_match_returns_expected_keys():
    row = _row()
    result = evaluate_match(row, leader_gap=3.0, chasers=4)
    required = {
        "leader_defensive_scoreline",
        "leader_expected_points",
        "raw_ev_recommended",
        "ev_cost_vs_recommended",
        "defensive_score",
        "defensive_reason",
        "model_recommended_scoreline",
    }
    assert required.issubset(result.keys())


def test_evaluate_match_scoreline_is_valid():
    row = _row()
    result = evaluate_match(row, leader_gap=3.0, chasers=4)
    scoreline = result["leader_defensive_scoreline"]
    parts = scoreline.split("-")
    assert len(parts) == 2
    assert all(p.isdigit() for p in parts)


def test_evaluate_match_no_negative_ev():
    row = _row(expected_points=0.5)
    result = evaluate_match(row, leader_gap=0.5, chasers=8)
    assert result["leader_expected_points"] >= 0.0


def test_evaluate_match_large_gap_keeps_raw_ev():
    """With a large leader gap the recommended pick should pass through unchanged."""
    row = _row(recommended="2-1", expected_points=1.5, ev_gap=0.3)
    result = evaluate_match(row, leader_gap=50.0, chasers=0)
    assert result["model_recommended_scoreline"] == "2-1"


def test_should_exclude_match():
    assert should_exclude({"home_team": "Brazil", "away_team": "Germany"}, ["brazil"])
    assert should_exclude({"home_team": "Brazil", "away_team": "Germany"}, ["germany"])
    assert not should_exclude({"home_team": "Brazil", "away_team": "Germany"}, ["france"])
    assert not should_exclude({"home_team": "Brazil", "away_team": "Germany"}, [])


def test_run_defensive_model_basic():
    rows = [
        {
            "home_team": "A", "away_team": "B", "commence_time": "2026-06-25T18:00:00Z",
            "recommended_scoreline": "1-0", "top1_scoreline": "1-0", "top2_scoreline": "2-0",
            "top3_scoreline": "2-1", "private_chase_scoreline": "0-0", "modal_scoreline": "1-1",
            "expected_points": 1.2, "ev_gap_to_second": 0.05, "private_chase_ev_loss": 0.04,
            "risk_tier": "medium", "confidence_tier": "medium", "sensitivity_stability": 0.75,
        },
        {
            "home_team": "C", "away_team": "D", "commence_time": "2026-06-25T21:00:00Z",
            "recommended_scoreline": "0-1", "top1_scoreline": "0-1", "top2_scoreline": "0-2",
            "top3_scoreline": "1-1", "private_chase_scoreline": "1-0", "modal_scoreline": "0-0",
            "expected_points": 1.0, "ev_gap_to_second": 0.03, "private_chase_ev_loss": 0.08,
            "risk_tier": "high", "confidence_tier": "fragile", "sensitivity_stability": 0.60,
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "preds.csv"
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        out = Path(tmpdir) / "out"
        result = run_defensive_model(csv_path, out_dir=out, leader_gap=4.0, chasers=3)
        assert len(result) == 2
        assert (out / "defensive_picks.csv").exists()
        assert (out / "defensive_picks.json").exists()
        summary = json.loads((out / "defensive_summary.json").read_text())
        assert summary["matches"] == 2
        assert summary["leader_gap"] == 4.0


def test_run_defensive_model_exclude():
    rows = [
        {
            "home_team": "Brazil", "away_team": "Germany", "commence_time": "2026-06-25T18:00:00Z",
            "recommended_scoreline": "1-0", "top1_scoreline": "1-0", "top2_scoreline": "2-0",
            "top3_scoreline": "2-1", "private_chase_scoreline": "0-0", "modal_scoreline": "1-1",
            "expected_points": 1.2, "ev_gap_to_second": 0.05, "private_chase_ev_loss": 0.04,
            "risk_tier": "medium", "confidence_tier": "medium", "sensitivity_stability": 0.75,
        },
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "preds.csv"
        pd.DataFrame(rows).to_csv(csv_path, index=False)
        out = Path(tmpdir) / "out"
        result = run_defensive_model(csv_path, out_dir=out, leader_gap=3.0, chasers=2, exclude_matches=["brazil"])
        assert len(result) == 0


def test_run_defensive_model_file_not_found():
    with pytest.raises(FileNotFoundError):
        run_defensive_model("/nonexistent/file.csv")
