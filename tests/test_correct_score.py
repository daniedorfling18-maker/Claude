from __future__ import annotations

import numpy as np

from superbru_score_engine.config import ModelConfig, RatingsConfig
from superbru_score_engine.ingest import MarketOdds, MatchOdds, OutcomeOdds
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.devig import FairCorrectScoreMarket, extract_correct_score_market
from superbru_score_engine.model.poisson import independent_poisson_matrix
from superbru_score_engine.model.ratings import RatingsStore


def _cs_match(other: bool = True) -> MatchOdds:
    cs = [
        OutcomeOdds("1 - 0", 6.0), OutcomeOdds("2 - 0", 7.5), OutcomeOdds("2 - 1", 8.0),
        OutcomeOdds("1 - 1", 7.0), OutcomeOdds("0 - 0", 11.0), OutcomeOdds("0 - 1", 13.0),
    ]
    if other:
        cs.append(OutcomeOdds("Any Other Score", 3.5))
    return MatchOdds(
        match_id="cs", commence_time="2026-06-16T18:00:00Z", home_team="Brazil", away_team="Japan",
        markets={
            "h2h": (MarketOdds("h2h", "b", (OutcomeOdds("Brazil", 1.6), OutcomeOdds("Draw", 4.0), OutcomeOdds("Japan", 6.0))),),
            "correct_score": (MarketOdds("correct_score", "b", tuple(cs)),),
        },
    )


def test_extract_correct_score_market_keeps_other_as_residual() -> None:
    market = extract_correct_score_market(_cs_match(other=True), max_goals=10)
    assert market is not None and market.count == 1
    assert market.other_mass > 0.0
    assert abs(sum(market.cell_probs.values()) + market.other_mass - 1.0) < 1e-9
    assert max(market.cell_probs, key=market.cell_probs.get) == (1, 0)

    # With no residual bucket the quoted cells de-vig to sum to 1 on their own.
    no_other = extract_correct_score_market(_cs_match(other=False), max_goals=10)
    assert no_other.other_mass == 0.0
    assert abs(sum(no_other.cell_probs.values()) - 1.0) < 1e-9


def test_to_matrix_spreads_residual_over_tail_from_model() -> None:
    model = independent_poisson_matrix(1.4, 1.0, 10)
    market = FairCorrectScoreMarket(cell_probs={(1, 0): 0.3, (0, 0): 0.2}, other_mass=0.5, count=1)
    matrix = market.to_matrix(10, model_matrix=model)
    assert abs(matrix.sum() - 1.0) < 1e-9
    assert abs(matrix[1, 0] - 0.3) < 1e-9 and abs(matrix[0, 0] - 0.2) < 1e-9  # quoted preserved
    assert matrix[2, 2] > 0.0 and matrix[3, 1] > 0.0  # residual fills the tail, not zeros


def test_to_matrix_borrows_tail_size_from_model_when_no_other_bucket() -> None:
    model = independent_poisson_matrix(1.4, 1.0, 10)
    market = FairCorrectScoreMarket(cell_probs={(1, 0): 0.6, (0, 0): 0.4}, other_mass=0.0, count=1)
    matrix = market.to_matrix(10, model_matrix=model)
    assert abs(matrix.sum() - 1.0) < 1e-9
    assert matrix[2, 1] > 0.0  # tail is NOT nuked even without an explicit residual bucket
    assert matrix[1, 0] < 0.6 and matrix[0, 0] < 0.4  # quoted scaled down to make room
    assert matrix[1, 0] > matrix[0, 0]  # relative shape preserved


def test_to_matrix_uniform_tail_without_model() -> None:
    market = FairCorrectScoreMarket(cell_probs={(1, 0): 0.5, (0, 0): 0.3}, other_mass=0.2, count=1)
    matrix = market.to_matrix(3, model_matrix=None)
    assert abs(matrix.sum() - 1.0) < 1e-9
    tail = [matrix[h, a] for h in range(4) for a in range(4) if (h, a) not in {(1, 0), (0, 0)}]
    assert min(tail) > 0.0 and (max(tail) - min(tail)) < 1e-9  # uniform residual


def test_build_distribution_blends_correct_score_and_preserves_tail() -> None:
    ratings = RatingsStore(config=RatingsConfig(use_as_fallback_only=True))
    blended_model = OddsToScorelineModel(
        ModelConfig(correct_score_blend_weight=0.6, odds_weight=1.0, ratings_weight=0.0, dixon_coles_rho=-0.08), ratings
    )
    distribution = blended_model.build_distribution(_cs_match(other=True))
    diag = distribution.diagnostics
    assert diag["correct_score_blended"] is True
    assert diag["correct_score_cells_quoted"] == 6
    assert diag["correct_score_total_variation_shift"] > 0.0
    assert abs(float(distribution.matrix.sum()) - 1.0) < 1e-9
    assert distribution.matrix[3, 2] > 0.0  # unquoted scoreline keeps positive probability

    # With the weight at 0 the blend is off but the grid is still surfaced for visibility.
    off_model = OddsToScorelineModel(
        ModelConfig(correct_score_blend_weight=0.0, odds_weight=1.0, ratings_weight=0.0, dixon_coles_rho=-0.08), ratings
    )
    off = off_model.build_distribution(_cs_match(other=True))
    assert off.diagnostics["correct_score_blended"] is False
    assert off.diagnostics["correct_score_cells_quoted"] == 6
