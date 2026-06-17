"""Propagate cross-bookmaker disagreement all the way through to the pick.

The deterministic sensitivity sweep perturbs lambda/rho by fixed amounts. This instead
asks a market-driven question: if you had trusted each bookmaker *alone*, how often would
you have made the same pick, and how far would the implied lambdas and expected points
have moved? Low pick stability is a genuine, data-grounded fragility signal.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from superbru_score_engine.ingest import MatchOdds
from superbru_score_engine.model.devig import extract_fair_1x2_book_level
from superbru_score_engine.model.uncertainty import market_disagreement_1x2


def book_ensemble_uncertainty(match: MatchOdds, model, decision, disagreement_threshold: float = 0.04) -> dict[str, object]:
    """Rebuild the distribution and EV pick per single bookmaker and summarise the spread.

    ``model`` is an ``OddsToScorelineModel`` and ``decision`` a ``SuperbruDecisionEngine``.
    Each book's h2h market is evaluated on its own (sharing the totals ladder), so the
    returned pick stability, lambda dispersion and expected-points range reflect real
    market disagreement rather than an assumed perturbation size.
    """
    books = match.market("h2h")
    n_books = len(books)
    disagreement = market_disagreement_1x2(extract_fair_1x2_book_level(match, model.config.devig_method))

    if n_books < 2:
        return {
            **disagreement,
            "ensemble_evaluated": False,
            "consensus_pick": None,
            "pick_stability": None,
            "distinct_picks": int(n_books >= 1),
            "high_market_disagreement": False,
        }

    pick_counts: dict[str, int] = {}
    lambda_home: list[float] = []
    lambda_away: list[float] = []
    expected_points: list[float] = []
    for book_market in books:
        sub_match = replace(match, markets={**match.markets, "h2h": (book_market,)})
        distribution = model.build_distribution(sub_match)
        recommended = decision.predict(distribution).recommended
        pick_counts[recommended.scoreline] = pick_counts.get(recommended.scoreline, 0) + 1
        lambda_home.append(float(distribution.lambda_home))
        lambda_away.append(float(distribution.lambda_away))
        expected_points.append(float(recommended.expected_points))

    consensus_pick = max(pick_counts, key=lambda key: pick_counts[key])
    return {
        **disagreement,
        "ensemble_evaluated": True,
        "consensus_pick": consensus_pick,
        "pick_stability": pick_counts[consensus_pick] / n_books,
        "distinct_picks": len(pick_counts),
        "lambda_home_std": float(np.std(lambda_home, ddof=0)),
        "lambda_away_std": float(np.std(lambda_away, ddof=0)),
        "expected_points_min": float(min(expected_points)),
        "expected_points_max": float(max(expected_points)),
        "expected_points_spread": float(max(expected_points) - min(expected_points)),
        "high_market_disagreement": bool(disagreement["disagreement_index"] >= disagreement_threshold),
    }
