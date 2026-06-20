"""
Data-driven tests that load real committed data files from examples/, inputs/, and
outputs/ and assert invariants on the pipeline outputs.

These tests catch regressions that unit tests miss because they exercise the full
pipeline against the same fixtures the production smoke run uses.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from superbru_score_engine.config import ModelConfig, SuperbruConfig
from superbru_score_engine.decision.superbru import score_actual_prediction
from superbru_score_engine.ingest.offline import OfflineJsonProvider
from superbru_score_engine.model.devig import (
    extract_correct_score_market,
    extract_fair_1x2,
    extract_fair_totals_all,
)
from superbru_score_engine.model.odds_to_scoreline import OddsToScorelineModel

REPO = Path(__file__).parent.parent
ODDS_SNAPSHOT = REPO / "examples" / "odds_snapshot.json"
BACKTEST_MATCHES = REPO / "examples" / "backtest_matches.csv"
SMARTBET_GRID = REPO / "inputs" / "smartbet_grids" / "oddspedia_probability_grids_auto.csv"
SMOKE_PREDICTIONS = REPO / "outputs" / "smoke" / "predictions.csv"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def snapshot_matches():
    return OfflineJsonProvider(ODDS_SNAPSHOT).fetch_odds()


@pytest.fixture(scope="module")
def smoke_rows():
    with open(SMOKE_PREDICTIONS, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def backtest_rows():
    with open(BACKTEST_MATCHES, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def smartbet_rows():
    with open(SMARTBET_GRID, newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def model_config():
    return ModelConfig(
        calibration_profile="big5_h2h_totals",
        dixon_coles_rho=-0.08,
        devig_method="multiplicative",
        odds_weight=1.0,
        ratings_weight=0.0,
        model_grid_goals=10,
        solver_grid_goals=14,
        candidate_grid_goals=6,
    )


@pytest.fixture(scope="module")
def distributions(snapshot_matches, model_config):
    engine = OddsToScorelineModel(config=model_config)
    return [engine.build_distribution(match) for match in snapshot_matches]


# ---------------------------------------------------------------------------
# 1. Odds snapshot: parse correctness
# ---------------------------------------------------------------------------


def test_snapshot_loads_three_matches(snapshot_matches):
    assert len(snapshot_matches) == 3


def test_snapshot_match_ids(snapshot_matches):
    ids = {m.match_id for m in snapshot_matches}
    assert ids == {"wc26-001", "wc26-002", "wc26-003"}


def test_snapshot_all_have_h2h_market(snapshot_matches):
    for match in snapshot_matches:
        assert len(match.market("h2h")) >= 1, f"{match.match_id} has no h2h market"


def test_snapshot_all_have_totals_market(snapshot_matches):
    for match in snapshot_matches:
        assert len(match.market("totals")) >= 1, f"{match.match_id} has no totals market"


def test_snapshot_mexico_sa_has_correct_score(snapshot_matches):
    mex_sa = next(m for m in snapshot_matches if m.match_id == "wc26-001")
    assert len(mex_sa.market("correct_score")) >= 1


# ---------------------------------------------------------------------------
# 2. De-vig outputs for snapshot odds
# ---------------------------------------------------------------------------


def test_fair_1x2_sums_to_one(snapshot_matches):
    for match in snapshot_matches:
        fair = extract_fair_1x2(match, "multiplicative")
        assert fair is not None, f"{match.match_id}: extract_fair_1x2 returned None"
        total = fair.home + fair.draw + fair.away
        assert abs(total - 1.0) < 1e-6, f"{match.match_id}: 1X2 probs sum to {total}"


def test_fair_1x2_all_positive(snapshot_matches):
    for match in snapshot_matches:
        fair = extract_fair_1x2(match, "multiplicative")
        assert fair is not None
        assert fair.home > 0 and fair.draw > 0 and fair.away > 0


def test_fair_1x2_mexico_is_favourite(snapshot_matches):
    mex_sa = next(m for m in snapshot_matches if m.match_id == "wc26-001")
    fair = extract_fair_1x2(mex_sa, "multiplicative")
    assert fair is not None
    assert fair.home > fair.away, "Mexico should be favourite over South Africa"


def test_fair_totals_all_returns_one_line_per_match(snapshot_matches):
    for match in snapshot_matches:
        markets = extract_fair_totals_all(match, "multiplicative")
        assert len(markets) >= 1, f"{match.match_id}: no totals market"


def test_fair_totals_over_under_sum_to_one(snapshot_matches):
    for match in snapshot_matches:
        for market in extract_fair_totals_all(match, "multiplicative"):
            total = market.over + market.under
            assert abs(total - 1.0) < 1e-6, f"{match.match_id} line {market.line}: over+under = {total}"


def test_fair_correct_score_sums_to_one(snapshot_matches):
    mex_sa = next(m for m in snapshot_matches if m.match_id == "wc26-001")
    market = extract_correct_score_market(mex_sa, 10, "multiplicative")
    assert market is not None
    total = sum(market.cell_probs.values()) + market.other_mass
    assert abs(total - 1.0) < 1e-6, f"correct-score total = {total}"


def test_fair_correct_score_all_cells_positive(snapshot_matches):
    mex_sa = next(m for m in snapshot_matches if m.match_id == "wc26-001")
    market = extract_correct_score_market(mex_sa, 10, "multiplicative")
    assert market is not None
    for cell, prob in market.cell_probs.items():
        assert prob > 0, f"Cell {cell} has non-positive probability {prob}"


def test_correct_score_to_matrix_sums_to_one(snapshot_matches):
    mex_sa = next(m for m in snapshot_matches if m.match_id == "wc26-001")
    market = extract_correct_score_market(mex_sa, 10, "multiplicative")
    assert market is not None
    mat = market.to_matrix(10)
    assert abs(mat.sum() - 1.0) < 1e-6


# ---------------------------------------------------------------------------
# 3. Full distribution build
# ---------------------------------------------------------------------------


def test_distributions_lambda_positive(distributions):
    for dist in distributions:
        assert dist.lambda_home > 0, f"{dist.match.match_id}: lambda_home <= 0"
        assert dist.lambda_away > 0, f"{dist.match.match_id}: lambda_away <= 0"


def test_distributions_matrix_sums_to_one(distributions):
    for dist in distributions:
        total = float(dist.matrix.sum())
        assert abs(total - 1.0) < 1e-6, f"{dist.match.match_id}: matrix sums to {total}"


def test_distributions_matrix_all_nonnegative(distributions):
    for dist in distributions:
        assert np.all(dist.matrix >= 0), f"{dist.match.match_id}: matrix has negative cells"


def test_distributions_model_probs_sum_to_one(distributions):
    for dist in distributions:
        home = float(dist.diagnostics["model_home_win"])
        draw = float(dist.diagnostics["model_draw"])
        away = float(dist.diagnostics["model_away_win"])
        total = home + draw + away
        assert abs(total - 1.0) < 1e-5, f"{dist.match.match_id}: model 1X2 probs sum to {total}"


def test_distributions_lambda_home_mexico_above_sa(distributions):
    mex_sa = next(d for d in distributions if d.match.match_id == "wc26-001")
    assert mex_sa.lambda_home > mex_sa.lambda_away, "Mexico should have higher lambda than South Africa"


def test_distributions_canada_japan_close_lambdas(distributions):
    can_jp = next(d for d in distributions if d.match.match_id == "wc26-002")
    assert abs(can_jp.lambda_home - can_jp.lambda_away) < 0.4, "Canada vs Japan should have similar lambdas"


def test_distributions_lambda_matches_smoke_within_tolerance(distributions):
    """Lambdas from the pipeline should be in the same ballpark as the committed smoke outputs."""
    with open(SMOKE_PREDICTIONS, newline="") as fh:
        smoke = {row["match_id"]: row for row in csv.DictReader(fh)}
    for dist in distributions:
        mid = dist.match.match_id
        if mid not in smoke:
            continue
        expected_lh = float(smoke[mid]["lambda_home"])
        expected_la = float(smoke[mid]["lambda_away"])
        # 10% relative tolerance: lambdas shouldn't differ by more than this
        # across model runs unless config changed.
        assert abs(dist.lambda_home - expected_lh) / max(expected_lh, 0.1) < 0.10, (
            f"{mid}: lambda_home {dist.lambda_home:.4f} vs smoke {expected_lh:.4f}"
        )
        assert abs(dist.lambda_away - expected_la) / max(expected_la, 0.1) < 0.10, (
            f"{mid}: lambda_away {dist.lambda_away:.4f} vs smoke {expected_la:.4f}"
        )


# ---------------------------------------------------------------------------
# 4. Smoke prediction output invariants
# ---------------------------------------------------------------------------


def test_smoke_predictions_count(smoke_rows):
    assert len(smoke_rows) == 3


def test_smoke_expected_points_in_valid_range(smoke_rows):
    for row in smoke_rows:
        ev = float(row["expected_points"])
        assert 0 < ev < 3.0, f"{row['match_id']}: expected_points={ev} out of range"


def test_smoke_lambda_positive(smoke_rows):
    for row in smoke_rows:
        lh = float(row["lambda_home"])
        la = float(row["lambda_away"])
        assert lh > 0, f"{row['match_id']}: lambda_home={lh}"
        assert la > 0, f"{row['match_id']}: lambda_away={la}"


def test_smoke_model_probs_sum_to_one(smoke_rows):
    for row in smoke_rows:
        total = float(row["model_home_win"]) + float(row["model_draw"]) + float(row["model_away_win"])
        assert abs(total - 1.0) < 1e-4, f"{row['match_id']}: model probs sum to {total}"


def test_smoke_p_exact_leq_p_close_leq_p_outcome(smoke_rows):
    for row in smoke_rows:
        p_exact = float(row["p_exact"])
        p_close = float(row["p_close"])
        p_outcome = float(row["p_outcome"])
        assert p_exact <= p_close + 1e-9, f"{row['match_id']}: p_exact > p_close"
        assert p_close <= p_outcome + 1e-9, f"{row['match_id']}: p_close > p_outcome"


def test_smoke_p_outcome_leq_one(smoke_rows):
    for row in smoke_rows:
        assert float(row["p_outcome"]) <= 1.0 + 1e-9, f"{row['match_id']}: p_outcome > 1"


def test_smoke_variance_nonnegative(smoke_rows):
    for row in smoke_rows:
        assert float(row["variance_points"]) >= 0, f"{row['match_id']}: variance_points < 0"


def test_smoke_sensitivity_stability_in_range(smoke_rows):
    for row in smoke_rows:
        if row.get("sensitivity_enabled", "").lower() == "true":
            stab = float(row["sensitivity_stability"])
            assert 0.0 <= stab <= 1.0, f"{row['match_id']}: sensitivity_stability={stab}"


def test_smoke_fair_probs_sum_to_one(smoke_rows):
    for row in smoke_rows:
        total = float(row["fair_home_win"]) + float(row["fair_draw"]) + float(row["fair_away_win"])
        assert abs(total - 1.0) < 1e-4, f"{row['match_id']}: fair 1X2 probs sum to {total}"


def test_smoke_total_goals_mean_positive(smoke_rows):
    for row in smoke_rows:
        mean = float(row["total_goals_mean"])
        assert mean > 0, f"{row['match_id']}: total_goals_mean={mean}"


def test_smoke_solver_success(smoke_rows):
    for row in smoke_rows:
        val = row["solver_success"].strip().lower()
        # CSV serialises the bool as "True"/"False" or as a float "1.0"/"0.0"
        success = val in {"true", "1", "1.0"}
        assert success, f"{row['match_id']}: solver_success={row['solver_success']!r}"


# ---------------------------------------------------------------------------
# 5. Backtest scoring against known results
# ---------------------------------------------------------------------------


def _smoke_pick(match_id: str) -> tuple[int, int]:
    with open(SMOKE_PREDICTIONS, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["match_id"] == match_id:
                h, a = row["recommended_scoreline"].split("-")
                return int(h), int(a)
    raise KeyError(match_id)


def test_scoring_exact_match(backtest_rows):
    """Brazil vs Germany smoke pick 2-1, actual 2-1 → exact → 3.0 pts."""
    row = next(r for r in backtest_rows if r["match_id"] == "wc26-003")
    pred_h, pred_a = _smoke_pick("wc26-003")
    actual_h, actual_a = int(row["home_goals"]), int(row["away_goals"])
    pts = score_actual_prediction(pred_h, pred_a, actual_h, actual_a, ci_cutoff=1.5)
    assert pts == 3.0, f"Expected exact match (3.0), got {pts}"


def test_scoring_close_match(backtest_rows):
    """Mexico vs SA smoke pick 2-0, actual 1-0 → right outcome, CI=1.5 ≤ cutoff → 1.5 pts."""
    row = next(r for r in backtest_rows if r["match_id"] == "wc26-001")
    pred_h, pred_a = _smoke_pick("wc26-001")
    actual_h, actual_a = int(row["home_goals"]), int(row["away_goals"])
    pts = score_actual_prediction(pred_h, pred_a, actual_h, actual_a, ci_cutoff=1.5)
    assert pts == 1.5, f"Expected close match (1.5), got {pts}"


def test_scoring_wrong_outcome(backtest_rows):
    """Canada vs Japan smoke pick 2-1 (home win), actual 1-1 (draw) → 0.0 pts."""
    row = next(r for r in backtest_rows if r["match_id"] == "wc26-002")
    pred_h, pred_a = _smoke_pick("wc26-002")
    actual_h, actual_a = int(row["home_goals"]), int(row["away_goals"])
    pts = score_actual_prediction(pred_h, pred_a, actual_h, actual_a, ci_cutoff=1.5)
    assert pts == 0.0, f"Expected wrong outcome (0.0), got {pts}"


def test_backtest_total_points_from_smoke_picks(backtest_rows):
    """End-to-end: sum of points from smoke picks over 3 known results is 4.5."""
    total = 0.0
    for row in backtest_rows:
        mid = row["match_id"]
        pred_h, pred_a = _smoke_pick(mid)
        actual_h, actual_a = int(row["home_goals"]), int(row["away_goals"])
        total += score_actual_prediction(pred_h, pred_a, actual_h, actual_a, ci_cutoff=1.5)
    assert abs(total - 4.5) < 1e-9, f"Expected total 4.5, got {total}"


# ---------------------------------------------------------------------------
# 6. Smartbet grid internal consistency
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def smartbet_by_match(smartbet_rows):
    grouped = defaultdict(list)
    for row in smartbet_rows:
        grouped[row["match_id"]].append(row)
    return grouped


def test_smartbet_winner_probs_sum_near_100(smartbet_by_match):
    """winner_home + winner_draw + winner_away should be within 1pp of 100 for all rows."""
    for match_id, rows in smartbet_by_match.items():
        row = rows[0]  # all rows for a match share the same header probabilities
        total = float(row["winner_home_pct"]) + float(row["winner_draw_pct"]) + float(row["winner_away_pct"])
        assert abs(total - 100.0) <= 1.0, f"{match_id}: winner pcts sum to {total}"


def test_smartbet_exact_probs_positive(smartbet_by_match):
    """All exact scoreline probabilities should be positive."""
    for match_id, rows in smartbet_by_match.items():
        exact_rows = [r for r in rows if r["score_bucket"] == "exact"]
        for row in exact_rows:
            prob = float(row["probability_pct"])
            assert prob > 0, f"{match_id} score {row['score_key']}: probability_pct={prob}"


def test_smartbet_exact_probs_leq_100_per_match(smartbet_by_match):
    """Sum of quoted exact-score probabilities should not exceed 100% per match."""
    for match_id, rows in smartbet_by_match.items():
        exact_sum = sum(float(r["probability_pct"]) for r in rows if r["score_bucket"] == "exact")
        assert exact_sum <= 100.0 + 1e-6, f"{match_id}: exact probs sum to {exact_sum}%"


def test_smartbet_covers_16_matches(smartbet_by_match):
    assert len(smartbet_by_match) == 16


def test_smartbet_all_matches_have_at_least_one_exact_row(smartbet_by_match):
    for match_id, rows in smartbet_by_match.items():
        exact_rows = [r for r in rows if r["score_bucket"] == "exact"]
        assert len(exact_rows) >= 1, f"{match_id}: no exact scoreline rows"


def test_smartbet_home_team_nonempty(smartbet_rows):
    for row in smartbet_rows:
        assert row["home_team"].strip(), f"Row with match_id={row['match_id']} has empty home_team"


def test_smartbet_away_team_nonempty(smartbet_rows):
    for row in smartbet_rows:
        assert row["away_team"].strip(), f"Row with match_id={row['match_id']} has empty away_team"
