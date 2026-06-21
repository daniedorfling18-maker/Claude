from __future__ import annotations

from superbru_score_engine.config import ModelConfig, RatingsConfig, SuperbruConfig
from superbru_score_engine.decision.superbru import SuperbruDecisionEngine
from superbru_score_engine.decision.uncertainty import book_ensemble_uncertainty
from superbru_score_engine.ingest import MarketOdds, MatchOdds, OutcomeOdds
from superbru_score_engine.model import OddsToScorelineModel
from superbru_score_engine.model.devig import FairOutcomeMarket
from superbru_score_engine.model.ratings import RatingsStore
from superbru_score_engine.model.uncertainty import market_disagreement_1x2


def _book(home: float, draw: float, away: float) -> MarketOdds:
    return MarketOdds(
        key="h2h",
        bookmaker="book",
        outcomes=(OutcomeOdds("Brazil", home), OutcomeOdds("Draw", draw), OutcomeOdds("Japan", away)),
    )


def _match(books: list[MarketOdds]) -> MatchOdds:
    return MatchOdds(
        match_id="m1",
        commence_time="2026-06-16T18:00:00Z",
        home_team="Brazil",
        away_team="Japan",
        markets={"h2h": tuple(books)},
    )


def _engines():
    model = OddsToScorelineModel(
        ModelConfig(odds_weight=1.0, ratings_weight=0.0, dixon_coles_rho=-0.08),
        RatingsStore(config=RatingsConfig(use_as_fallback_only=True)),
    )
    decision = SuperbruDecisionEngine(SuperbruConfig(), 6)
    return model, decision


def test_market_disagreement_is_zero_without_two_books() -> None:
    assert market_disagreement_1x2([])["disagreement_index"] == 0.0
    single = market_disagreement_1x2([FairOutcomeMarket(0.5, 0.3, 0.2)])
    assert single["n_books"] == 1 and single["disagreement_index"] == 0.0


def test_market_disagreement_grows_with_spread() -> None:
    tight = market_disagreement_1x2([FairOutcomeMarket(0.50, 0.30, 0.20), FairOutcomeMarket(0.51, 0.29, 0.20)])
    wide = market_disagreement_1x2([FairOutcomeMarket(0.40, 0.30, 0.30), FairOutcomeMarket(0.70, 0.20, 0.10)])
    assert wide["disagreement_index"] > tight["disagreement_index"]
    assert abs(wide["max_book_spread"] - 0.30) < 1e-9  # home prob ranges 0.40..0.70


def test_book_ensemble_stable_when_books_agree() -> None:
    model, decision = _engines()
    books = [_book(1.80, 3.50, 4.40), _book(1.82, 3.45, 4.50), _book(1.79, 3.55, 4.35)]
    report = book_ensemble_uncertainty(_match(books), model, decision)
    assert report["ensemble_evaluated"] is True
    assert report["n_books"] == 3
    assert report["pick_stability"] == 1.0  # near-identical books -> same pick every time
    assert report["distinct_picks"] == 1
    assert report["high_market_disagreement"] is False
    assert report["expected_points_spread"] >= 0.0


def test_book_ensemble_flags_disagreement_when_books_diverge() -> None:
    model, decision = _engines()
    # One book sees a strong home favourite, another sees an away favourite.
    books = [_book(1.30, 4.50, 9.00), _book(4.50, 3.80, 1.75), _book(2.20, 3.30, 3.20)]
    report = book_ensemble_uncertainty(_match(books), model, decision)
    assert report["ensemble_evaluated"] is True
    assert report["disagreement_index"] > 0.05
    assert report["high_market_disagreement"] is True
    assert report["pick_stability"] <= 1.0
    assert report["lambda_home_std"] > 0.0
