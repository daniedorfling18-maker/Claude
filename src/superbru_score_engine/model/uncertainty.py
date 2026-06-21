"""Market-disagreement uncertainty: how much do the bookmakers actually agree?

The consensus fair 1X2 is an average across books. When books disagree, that average
- and the lambda and pick it implies - rests on weaker ground. These helpers quantify
the cross-book dispersion so a pick built on a tight market can be distinguished from
one built on a market that cannot make up its mind.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from .devig import FairOutcomeMarket


def market_disagreement_1x2(books: Sequence[FairOutcomeMarket]) -> dict[str, object]:
    """Cross-bookmaker dispersion of the fair 1X2 probabilities.

    ``disagreement_index`` is the mean standard deviation across the home/draw/away
    probabilities; ``max_book_spread`` is the widest single-outcome max-minus-min. Both
    are 0 when fewer than two books are available (nothing to disagree about).
    """
    n_books = len(books)
    if n_books < 2:
        return {
            "n_books": n_books,
            "home_std": 0.0,
            "draw_std": 0.0,
            "away_std": 0.0,
            "max_book_spread": 0.0,
            "disagreement_index": 0.0,
        }
    matrix = np.vstack([book.as_array() for book in books])
    stds = matrix.std(axis=0, ddof=0)
    spread = float((matrix.max(axis=0) - matrix.min(axis=0)).max())
    return {
        "n_books": n_books,
        "home_std": float(stds[0]),
        "draw_std": float(stds[1]),
        "away_std": float(stds[2]),
        "max_book_spread": spread,
        "disagreement_index": float(stds.mean()),
    }
