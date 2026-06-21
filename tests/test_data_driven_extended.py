"""
Extended data-driven tests using outputs/, inputs/, and calibration artefacts
that were not covered by test_data_driven.py.

The WC 2026 domain-validation artifact is intentionally invalidated until it is
rebuilt from trusted completed results. Tests assert that guard instead of
accepting leaked scoreline validation.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).parent.parent

COMPONENT_VALIDATION = REPO / "outputs" / "component_validation" / "component_validation_report.csv"
MARKET_ODDS_VALIDATION = REPO / "outputs" / "market_odds_validation" / "market_odds_validation_report.csv"
LEADER_MC_CANDIDATES = REPO / "outputs" / "leaderboard_mc" / "leader_mc_candidate_tests.csv"
LEADER_MC_SUMMARY = REPO / "outputs" / "leaderboard_mc" / "leader_mc_summary.json"
ROUND_BEHAVIOUR = REPO / "inputs" / "round_summary_behaviour.csv"
BACKTEST_RESULTS = REPO / "outputs" / "calibration" / "current-engine-train" / "football_data_league_backtest_results.csv"
SMARTBET_GRID_ALT = REPO / "inputs" / "smartbet_grids" / "oddspedia_probability_grids_alt.csv"
WC2026_RESULTS = REPO / "outputs" / "calibration" / "wc2026-domain-validation" / "wc2026_domain_validation_results.csv"
WC2026_SUMMARY = REPO / "outputs" / "calibration" / "wc2026-domain-validation" / "wc2026_domain_validation_summary.json"


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def test_cv_market_h2h_probs_sum_to_one() -> None:
    for row in read_csv(COMPONENT_VALIDATION):
        if row["market_available"].strip().lower() != "true":
            continue
        total = float(row["market_p_home"]) + float(row["market_p_draw"]) + float(row["market_p_away"])
        assert abs(total - 1.0) < 1e-4


def test_cv_scores_in_range_and_pick_format() -> None:
    rows = read_csv(COMPONENT_VALIDATION)
    assert len(rows) == 40
    for row in rows:
        assert 0.0 <= float(row["market_alignment_score"]) <= 1.0
        assert 0.0 <= float(row["validation_score"]) <= 1.0
        assert 0.0 <= float(row["component_score"]) <= 1.0
        parts = row["final_pick"].strip().split("-")
        assert len(parts) == 2 and all(part.isdigit() for part in parts)


def test_market_odds_validation_probs_and_rows() -> None:
    rows = read_csv(MARKET_ODDS_VALIDATION)
    assert len(rows) == 40
    for row in rows:
        assert 0.0 <= float(row["market_alignment_score"]) <= 1.0
        assert 0.0 <= float(row["validation_score"]) <= 1.0
        if row["market_available"].strip().lower() == "true":
            total = float(row["market_p_home"]) + float(row["market_p_draw"]) + float(row["market_p_away"])
            assert abs(total - 1.0) < 1e-4
        if row["totals_available"].strip().lower() == "true":
            total = float(row["market_p_over"]) + float(row["market_p_under"])
            assert abs(total - 1.0) < 1e-4


def test_leader_mc_candidate_probabilities_and_summary() -> None:
    rows = read_csv(LEADER_MC_CANDIDATES)
    for row in rows:
        baseline = float(row["baseline_p_finish_first"])
        candidate = float(row["candidate_p_finish_first"])
        delta = float(row["delta_p_finish_first"])
        assert 0.0 <= baseline <= 1.0
        assert 0.0 <= candidate <= 1.0
        assert abs(delta - (candidate - baseline)) < 1e-6
        assert float(row["ev_loss"]) >= -1e-6
        if row["passes_hard_rule"].strip().lower() == "true":
            assert float(row["z_score"]) > 0

    summary = read_json(LEADER_MC_SUMMARY)
    assert 0.0 <= summary["baseline_p_finish_first"] <= 1.0
    assert 0.0 <= summary["final_p_finish_first"] <= 1.0
    assert abs(summary["delta_p_finish_first"] - (summary["final_p_finish_first"] - summary["baseline_p_finish_first"])) < 1e-6
    assert isinstance(summary.get("accepted_switches"), list)


def test_round_summary_scoring_arithmetic() -> None:
    for row in read_csv(ROUND_BEHAVIOUR):
        total = int(row["exact_count"]) + int(row["close_count"]) + int(row["result_count"]) + int(row["wrong_count"])
        assert total == int(row["total_matches"])
        assert float(row["round_points"]) >= 0.0
        if row["source_confidence"].strip().lower() == "high":
            expected = int(row["exact_count"]) * 3.0 + int(row["close_count"]) * 1.5 + int(row["result_count"]) * 1.0
            assert abs(expected - float(row["round_points"])) < 0.01


def test_calibration_backtest_rows_are_internally_valid() -> None:
    rows = read_csv(BACKTEST_RESULTS)
    valid_points = {0.0, 1.0, 1.5, 3.0}
    modes = {row["market_mode"] for row in rows}
    assert "h2h" in modes and "h2h_totals" in modes
    for row in rows:
        assert float(row["model_points"]) in valid_points
        assert float(row["naive_points"]) in valid_points
        assert 0.0 <= float(row["rps_1x2"]) <= 1.0 + 1e-9
        assert float(row["lambda_home"]) > 0
        assert float(row["lambda_away"]) > 0
        total = float(row["model_home_win"]) + float(row["model_draw"]) + float(row["model_away_win"])
        assert abs(total - 1.0) < 1e-4
        if float(row["model_points"]) == 3.0:
            assert row["model_scoreline"] == row["actual_scoreline"]


def test_alt_grid_internal_consistency() -> None:
    rows = read_csv(SMARTBET_GRID_ALT)
    assert len(rows) > 0
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        assert row["home_team"].strip()
        assert row["away_team"].strip()
        grouped[row["match_id"]].append(row)
    for match_id, match_rows in grouped.items():
        header = match_rows[0]
        winner_total = float(header["winner_home_pct"]) + float(header["winner_draw_pct"]) + float(header["winner_away_pct"])
        assert abs(winner_total - 100.0) <= 1.0, match_id
        exact_total = sum(float(row["probability_pct"]) for row in match_rows if row["score_bucket"] == "exact")
        assert exact_total <= 100.0 + 1e-6, match_id
        for row in match_rows:
            if row["score_bucket"] == "exact":
                assert float(row["probability_pct"]) > 0


def test_wc2026_domain_validation_is_invalidated_until_trusted_results_join() -> None:
    summary = read_json(WC2026_SUMMARY)
    rows = read_csv(WC2026_RESULTS)

    assert summary["validation_status"] == "invalidated"
    assert summary["matches"] == 0 or summary.get("invalidated_metrics")
    assert "trusted" in summary["contamination_guard"].lower()
    assert "market_odds_history" in summary["reason"]
    assert rows[0]["validation_status"] == "invalidated"
