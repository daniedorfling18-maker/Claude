"""
Extended data-driven tests using outputs/, inputs/, and calibration artefacts
that were not covered by test_data_driven.py.

Covers:
- Component validation report invariants (market probs, scores, pick format)
- Market odds validation report invariants
- Leaderboard MC candidate tests (probabilities, EV loss, z-scores)
- Round summary scoring arithmetic
- Football-Data backtest results per-match metric invariants
- Oddspedia probability grids (alt source, same invariants as auto)
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent

COMPONENT_VALIDATION = REPO / "outputs" / "component_validation" / "component_validation_report.csv"
MARKET_ODDS_VALIDATION = REPO / "outputs" / "market_odds_validation" / "market_odds_validation_report.csv"
LEADER_MC_CANDIDATES = REPO / "outputs" / "leaderboard_mc" / "leader_mc_candidate_tests.csv"
LEADER_MC_SUMMARY = REPO / "outputs" / "leaderboard_mc" / "leader_mc_summary.json"
ROUND_BEHAVIOUR = REPO / "inputs" / "round_summary_behaviour.csv"
BACKTEST_RESULTS = REPO / "outputs" / "calibration" / "current-engine-train" / "football_data_league_backtest_results.csv"
SMARTBET_GRID_ALT = REPO / "inputs" / "smartbet_grids" / "oddspedia_probability_grids_alt.csv"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def cv_rows():
    with open(COMPONENT_VALIDATION, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def mov_rows():
    with open(MARKET_ODDS_VALIDATION, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def mc_candidate_rows():
    with open(LEADER_MC_CANDIDATES, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def mc_summary():
    with open(LEADER_MC_SUMMARY) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def round_rows():
    with open(ROUND_BEHAVIOUR, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def backtest_rows():
    with open(BACKTEST_RESULTS, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def alt_grid_rows():
    with open(SMARTBET_GRID_ALT, newline="") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# 1. Component validation report
# ---------------------------------------------------------------------------


def test_cv_market_h2h_probs_sum_to_one(cv_rows):
    for row in cv_rows:
        if row["market_available"].strip().lower() != "true":
            continue
        total = float(row["market_p_home"]) + float(row["market_p_draw"]) + float(row["market_p_away"])
        assert abs(total - 1.0) < 1e-4, (
            f"{row['home_team']} vs {row['away_team']}: h2h probs sum to {total}"
        )


def test_cv_market_totals_probs_sum_to_one(cv_rows):
    for row in cv_rows:
        if row["totals_available"].strip().lower() != "true":
            continue
        total = float(row["market_p_over"]) + float(row["market_p_under"])
        assert abs(total - 1.0) < 1e-4, (
            f"{row['home_team']} vs {row['away_team']}: over+under sum to {total}"
        )


def test_cv_alignment_score_in_range(cv_rows):
    for row in cv_rows:
        score = float(row["market_alignment_score"])
        assert 0.0 <= score <= 1.0, (
            f"{row['home_team']} vs {row['away_team']}: alignment_score={score}"
        )


def test_cv_validation_score_in_range(cv_rows):
    for row in cv_rows:
        score = float(row["validation_score"])
        assert 0.0 <= score <= 1.0, (
            f"{row['home_team']} vs {row['away_team']}: validation_score={score}"
        )


def test_cv_component_score_in_range(cv_rows):
    for row in cv_rows:
        score = float(row["component_score"])
        assert 0.0 <= score <= 1.0, (
            f"{row['home_team']} vs {row['away_team']}: component_score={score}"
        )


def test_cv_favourite_prob_above_third(cv_rows):
    """Market favourite should have probability above 1/3 (else it's not really a favourite)."""
    for row in cv_rows:
        if row["market_available"].strip().lower() != "true":
            continue
        fav_prob = float(row["market_favourite_prob"])
        assert fav_prob > 1 / 3 - 1e-4, (
            f"{row['home_team']} vs {row['away_team']}: favourite_prob={fav_prob}"
        )


def test_cv_final_pick_is_valid_scoreline(cv_rows):
    for row in cv_rows:
        pick = row["final_pick"].strip()
        parts = pick.split("-")
        assert len(parts) == 2 and all(p.isdigit() for p in parts), (
            f"Invalid pick format: {pick!r}"
        )


def test_cv_has_40_rows(cv_rows):
    assert len(cv_rows) == 40


# ---------------------------------------------------------------------------
# 2. Market odds validation report
# ---------------------------------------------------------------------------


def test_mov_h2h_probs_sum_to_one(mov_rows):
    for row in mov_rows:
        if row["market_available"].strip().lower() != "true":
            continue
        total = float(row["market_p_home"]) + float(row["market_p_draw"]) + float(row["market_p_away"])
        assert abs(total - 1.0) < 1e-4, (
            f"{row['home_team']} vs {row['away_team']}: h2h probs sum to {total}"
        )


def test_mov_totals_probs_sum_to_one(mov_rows):
    for row in mov_rows:
        if row["totals_available"].strip().lower() != "true":
            continue
        total = float(row["market_p_over"]) + float(row["market_p_under"])
        assert abs(total - 1.0) < 1e-4, (
            f"{row['home_team']} vs {row['away_team']}: over+under sum to {total}"
        )


def test_mov_alignment_score_in_range(mov_rows):
    for row in mov_rows:
        score = float(row["market_alignment_score"])
        assert 0.0 <= score <= 1.0


def test_mov_validation_score_in_range(mov_rows):
    for row in mov_rows:
        score = float(row["validation_score"])
        assert 0.0 <= score <= 1.0


def test_mov_has_40_rows(mov_rows):
    assert len(mov_rows) == 40


# ---------------------------------------------------------------------------
# 3. Leaderboard MC candidate tests
# ---------------------------------------------------------------------------


def test_mc_baseline_p_in_range(mc_candidate_rows):
    for row in mc_candidate_rows:
        p = float(row["baseline_p_finish_first"])
        assert 0.0 <= p <= 1.0, f"baseline_p_finish_first={p}"


def test_mc_candidate_p_in_range(mc_candidate_rows):
    for row in mc_candidate_rows:
        p = float(row["candidate_p_finish_first"])
        assert 0.0 <= p <= 1.0, f"candidate_p_finish_first={p}"


def test_mc_delta_equals_candidate_minus_baseline(mc_candidate_rows):
    for row in mc_candidate_rows:
        delta = float(row["delta_p_finish_first"])
        expected = float(row["candidate_p_finish_first"]) - float(row["baseline_p_finish_first"])
        assert abs(delta - expected) < 1e-6, (
            f"delta={delta} but candidate−baseline={expected}"
        )


def test_mc_ev_loss_nonnegative(mc_candidate_rows):
    """ev_loss is raw_ev − candidate_ev; a switch always costs some EV."""
    for row in mc_candidate_rows:
        ev_loss = float(row["ev_loss"])
        assert ev_loss >= -1e-6, f"ev_loss={ev_loss} is unexpectedly negative"


def test_mc_z_score_positive_when_switch_passes(mc_candidate_rows):
    """A switch that passes the hard rule must have a positive z-score."""
    for row in mc_candidate_rows:
        if row["passes_hard_rule"].strip().lower() == "true":
            z = float(row["z_score"])
            assert z > 0, f"passes_hard_rule=True but z_score={z}"


def test_mc_summary_p_finish_first_in_range(mc_summary):
    assert 0.0 <= mc_summary["baseline_p_finish_first"] <= 1.0
    assert 0.0 <= mc_summary["final_p_finish_first"] <= 1.0


def test_mc_summary_delta_matches(mc_summary):
    delta = mc_summary["delta_p_finish_first"]
    expected = mc_summary["final_p_finish_first"] - mc_summary["baseline_p_finish_first"]
    assert abs(delta - expected) < 1e-6


def test_mc_summary_has_accepted_switches(mc_summary):
    assert isinstance(mc_summary.get("accepted_switches"), list)


# ---------------------------------------------------------------------------
# 4. Round summary scoring arithmetic
# ---------------------------------------------------------------------------


def test_round_scoring_arithmetic_high_confidence(round_rows):
    """For high-confidence rows the Superbru scoring formula should hold exactly."""
    for row in round_rows:
        if row["source_confidence"].strip().lower() != "high":
            continue
        exact = int(row["exact_count"])
        close = int(row["close_count"])
        result = int(row["result_count"])
        wrong = int(row["wrong_count"])
        expected_pts = exact * 3.0 + close * 1.5 + result * 1.0
        actual_pts = float(row["round_points"])
        assert abs(expected_pts - actual_pts) < 0.01, (
            f"{row['player']} round {row['round']}: "
            f"computed {expected_pts} but recorded {actual_pts}"
        )


def test_round_totals_match_total_matches(round_rows):
    for row in round_rows:
        total = int(row["exact_count"]) + int(row["close_count"]) + int(row["result_count"]) + int(row["wrong_count"])
        expected = int(row["total_matches"])
        assert total == expected, (
            f"{row['player']} round {row['round']}: "
            f"exact+close+result+wrong={total} but total_matches={expected}"
        )


def test_round_points_nonnegative(round_rows):
    for row in round_rows:
        assert float(row["round_points"]) >= 0, f"{row['player']} round {row['round']}: negative points"


def test_round_counts_nonnegative(round_rows):
    for row in round_rows:
        for col in ("exact_count", "close_count", "result_count", "wrong_count"):
            assert int(row[col]) >= 0, f"{row['player']} round {row['round']}: {col}={row[col]}"


# ---------------------------------------------------------------------------
# 5. Calibration backtest results per-match invariants
# ---------------------------------------------------------------------------


def test_backtest_model_points_valid_values(backtest_rows):
    valid = {0.0, 1.0, 1.5, 3.0}
    for row in backtest_rows:
        pts = float(row["model_points"])
        assert pts in valid, f"model_points={pts} is not in {valid}"


def test_backtest_rps_in_range(backtest_rows):
    for row in backtest_rows:
        rps = float(row["rps_1x2"])
        assert 0.0 <= rps <= 1.0 + 1e-9, f"rps_1x2={rps}"


def test_backtest_model_probs_sum_to_one(backtest_rows):
    for row in backtest_rows:
        total = float(row["model_home_win"]) + float(row["model_draw"]) + float(row["model_away_win"])
        assert abs(total - 1.0) < 1e-4, (
            f"{row['home_team']} vs {row['away_team']}: model probs sum to {total}"
        )


def test_backtest_lambda_positive(backtest_rows):
    for row in backtest_rows:
        assert float(row["lambda_home"]) > 0, f"lambda_home={row['lambda_home']}"
        assert float(row["lambda_away"]) > 0, f"lambda_away={row['lambda_away']}"


def test_backtest_exact_hit_means_scorelines_match(backtest_rows):
    """When model_points == 3.0 the model and actual scorelines must be identical."""
    for row in backtest_rows:
        if float(row["model_points"]) == 3.0:
            assert row["model_scoreline"] == row["actual_scoreline"], (
                f"3.0 pts but model={row['model_scoreline']} actual={row['actual_scoreline']}"
            )


def test_backtest_total_goals_crps_positive(backtest_rows):
    for row in backtest_rows:
        crps = float(row["total_goals_crps"])
        assert crps >= 0.0, f"total_goals_crps={crps}"


def test_backtest_actual_result_valid(backtest_rows):
    valid = {"home", "draw", "away"}
    for row in backtest_rows:
        assert row["actual_result"] in valid, f"actual_result={row['actual_result']!r}"


def test_backtest_naive_points_valid_values(backtest_rows):
    valid = {0.0, 1.0, 1.5, 3.0}
    for row in backtest_rows:
        pts = float(row["naive_points"])
        assert pts in valid, f"naive_points={pts}"


def test_backtest_covers_two_modes(backtest_rows):
    modes = {row["market_mode"] for row in backtest_rows}
    assert "h2h" in modes and "h2h_totals" in modes


# ---------------------------------------------------------------------------
# 6. Oddspedia probability grids (alt source)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def alt_grid_by_match(alt_grid_rows):
    grouped = defaultdict(list)
    for row in alt_grid_rows:
        grouped[row["match_id"]].append(row)
    return grouped


def test_alt_grid_winner_probs_sum_near_100(alt_grid_by_match):
    for match_id, rows in alt_grid_by_match.items():
        row = rows[0]
        total = float(row["winner_home_pct"]) + float(row["winner_draw_pct"]) + float(row["winner_away_pct"])
        assert abs(total - 100.0) <= 1.0, f"{match_id}: winner pcts sum to {total}"


def test_alt_grid_exact_probs_positive(alt_grid_by_match):
    for match_id, rows in alt_grid_by_match.items():
        for row in rows:
            if row["score_bucket"] == "exact":
                prob = float(row["probability_pct"])
                assert prob > 0, f"{match_id} score {row['score_key']}: probability_pct={prob}"


def test_alt_grid_exact_probs_leq_100(alt_grid_by_match):
    for match_id, rows in alt_grid_by_match.items():
        total = sum(float(r["probability_pct"]) for r in rows if r["score_bucket"] == "exact")
        assert total <= 100.0 + 1e-6, f"{match_id}: exact probs sum to {total}%"


def test_alt_grid_nonempty(alt_grid_rows):
    assert len(alt_grid_rows) > 0, "Alt grid file is empty"


def test_alt_grid_team_names_nonempty(alt_grid_rows):
    for row in alt_grid_rows:
        assert row["home_team"].strip(), f"Empty home_team for match {row['match_id']}"
        assert row["away_team"].strip(), f"Empty away_team for match {row['match_id']}"
