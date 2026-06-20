from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from superbru_score_engine.ingest import MatchOdds, MarketOdds
from superbru_score_engine.model.team_names import canonical_team_key


@dataclass(frozen=True)
class FairOutcomeMarket:
    home: float
    draw: float
    away: float

    def as_array(self) -> np.ndarray:
        return np.array([self.home, self.draw, self.away], dtype=float)


@dataclass(frozen=True)
class FairTotalMarket:
    line: float
    over: float
    under: float
    count: int


@dataclass(frozen=True)
class FairAsianHandicapMarket:
    """Two-sided Asian handicap market from the home team's line perspective.

    ``line`` is the handicap applied to the home team's goal difference. For
    example, ``-0.5`` means the home side must win, while ``+0.5`` means the
    home side covers with a win or draw.
    """

    line: float
    home: float
    away: float
    count: int


@dataclass(frozen=True)
class FairCorrectScoreMarket:
    """De-vigged correct-score grid: fair probability per quoted scoreline plus the
    residual ``other_mass`` for any 'Any Other Score'/field bucket.

    ``to_matrix`` turns this into a full scoreline matrix, filling the unquoted tail from
    a model prior so a sparse grid never asserts that unquoted scores are impossible.
    """

    cell_probs: dict[tuple[int, int], float]
    other_mass: float
    count: int

    def to_matrix(self, max_goals: int, model_matrix: np.ndarray | None = None) -> np.ndarray:
        size = max_goals + 1
        matrix = np.zeros((size, size), dtype=float)
        quoted_mask = np.zeros((size, size), dtype=bool)
        for (home_goals, away_goals), prob in self.cell_probs.items():
            if 0 <= home_goals <= max_goals and 0 <= away_goals <= max_goals:
                matrix[home_goals, away_goals] = prob
                quoted_mask[home_goals, away_goals] = True
        unquoted = ~quoted_mask
        use_model = model_matrix is not None and model_matrix.shape == matrix.shape
        tail_weights = np.where(unquoted, np.clip(model_matrix, 0.0, None), 0.0) if use_model else unquoted.astype(float)
        tail_total = float(tail_weights.sum())
        residual = max(0.0, self.other_mass)
        if residual <= 0.0 and use_model and tail_total > 0.0:
            # No explicit 'Any Other Score' bucket: borrow the tail SIZE from the model so
            # the grid does not assert P(unquoted) = 0, and keep the quoted cells' shape.
            residual = tail_total
            quoted_sum = float(matrix.sum())
            if quoted_sum > 0.0 and 0.0 < residual < 1.0:
                matrix = matrix * (1.0 - residual) / quoted_sum
        if residual > 0.0 and tail_total > 0.0:
            matrix = matrix + residual * tail_weights / tail_total
        total = float(matrix.sum())
        return matrix / total if total > 0.0 else matrix


def decimal_implied_probabilities(prices: list[float], method: str = "power") -> np.ndarray:
    implied = np.array([1.0 / price for price in prices], dtype=float)
    return devig_implied_probabilities(implied, method)


def devig_implied_probabilities(implied: np.ndarray, method: str = "power") -> np.ndarray:
    method = (method or "power").strip().lower()
    total = float(implied.sum())
    if total <= 0:
        raise ValueError("Cannot de-vig an empty or non-positive market")
    if method in {"multiplicative", "normalize", "normalise", "basic"}:
        return implied / total
    if method == "additive":
        margin = total - 1.0
        adjusted = implied - margin / len(implied)
        if np.any(adjusted <= 0):
            return implied / total
        return adjusted / adjusted.sum()
    if method == "power":
        return _power_devig(implied)
    raise ValueError(f"Unsupported de-vig method: {method}")


def extract_fair_1x2_book_level(match: MatchOdds, method: str = "power") -> list[FairOutcomeMarket]:
    """One de-vigged fair 1X2 estimate per bookmaker h2h market, before averaging.

    The spread of these per-book estimates is the raw material for market-disagreement
    based uncertainty: when books disagree, the consensus lambda (and the pick it
    implies) rests on weaker ground.
    """
    books: list[FairOutcomeMarket] = []
    for market in match.market("h2h"):
        mapped = _map_1x2_market(match, market, method)
        if mapped is not None:
            books.append(FairOutcomeMarket(home=float(mapped[0]), draw=float(mapped[1]), away=float(mapped[2])))
    return books


def extract_fair_1x2(match: MatchOdds, method: str = "power") -> FairOutcomeMarket | None:
    books = extract_fair_1x2_book_level(match, method)
    if not books:
        return None
    avg = np.vstack([book.as_array() for book in books]).mean(axis=0)
    avg = avg / avg.sum()
    return FairOutcomeMarket(home=float(avg[0]), draw=float(avg[1]), away=float(avg[2]))


def extract_fair_totals_all(match: MatchOdds, method: str = "power") -> list[FairTotalMarket]:
    """De-vig *every* viable over/under line (e.g. 1.5, 2.5, 3.5), one per distinct point.

    Each line that has both an over and an under price from at least one bookmaker is
    averaged across books and returned as its own ``FairTotalMarket``. Feeding several
    lines into the lambda solver constrains the *shape* of the goal distribution more
    tightly than a single line can. Markets are returned sorted by line ascending.
    """
    by_line: dict[float, list[np.ndarray]] = defaultdict(list)
    for market in match.market("totals"):
        grouped: dict[float, dict[str, float]] = defaultdict(dict)
        for outcome in market.outcomes:
            if outcome.point is None:
                continue
            name = outcome.name.strip().lower()
            if name.startswith("over"):
                grouped[float(outcome.point)]["over"] = outcome.price
            elif name.startswith("under"):
                grouped[float(outcome.point)]["under"] = outcome.price
        for line, prices in grouped.items():
            if "over" in prices and "under" in prices:
                probs = decimal_implied_probabilities([prices["over"], prices["under"]], method)
                by_line[line].append(probs)

    markets: list[FairTotalMarket] = []
    for line in sorted(by_line):
        avg = np.vstack(by_line[line]).mean(axis=0)
        avg = avg / avg.sum()
        markets.append(
            FairTotalMarket(line=float(line), over=float(avg[0]), under=float(avg[1]), count=len(by_line[line]))
        )
    return markets


def primary_total_market(markets: Sequence[FairTotalMarket]) -> FairTotalMarket | None:
    """The single most-reliable line: most books, then closest to the 2.5 main line."""
    if not markets:
        return None
    return sorted(markets, key=lambda m: (-m.count, abs(m.line - 2.5), m.line))[0]


def extract_fair_totals(match: MatchOdds, method: str = "power") -> FairTotalMarket | None:
    """Backwards-compatible single-line accessor: the primary over/under line, or ``None``."""
    return primary_total_market(extract_fair_totals_all(match, method))


def extract_fair_asian_handicap(match: MatchOdds, method: str = "power") -> FairAsianHandicapMarket | None:
    """De-vig a two-sided Asian handicap/spread market.

    Markets are normalised to the home-team handicap line. Generic providers often
    attach opposite points to the home and away outcomes. For example, home -0.5 and
    away +0.5 are grouped under ``line=-0.5``.
    """
    home_key = canonical_team_key(match.home_team)
    away_key = canonical_team_key(match.away_team)
    by_line: dict[float, list[np.ndarray]] = defaultdict(list)

    for key in ("spreads", "asian_handicap", "asian_handicap_spread"):
        for market in match.market(key):
            grouped: dict[float, dict[str, float]] = defaultdict(dict)
            for outcome in market.outcomes:
                if outcome.point is None:
                    continue
                name_key = canonical_team_key(outcome.name)
                description = (outcome.description or "").strip().lower()
                if name_key == home_key or description == "home":
                    grouped[float(outcome.point)]["home"] = outcome.price
                elif name_key == away_key or description == "away":
                    grouped[-float(outcome.point)]["away"] = outcome.price
            for line, prices in grouped.items():
                if "home" in prices and "away" in prices:
                    probs = decimal_implied_probabilities([prices["home"], prices["away"]], method)
                    by_line[float(line)].append(probs)

    if not by_line:
        return None

    line = sorted(by_line, key=lambda value: (-len(by_line[value]), abs(value), value))[0]
    avg = np.vstack(by_line[line]).mean(axis=0)
    avg = avg / avg.sum()
    return FairAsianHandicapMarket(line=float(line), home=float(avg[0]), away=float(avg[1]), count=len(by_line[line]))


def extract_correct_score_market(match: MatchOdds, max_goals: int, method: str = "power") -> FairCorrectScoreMarket | None:
    """De-vig a correct-score grid, keeping any 'Any Other Score'/field bucket as residual.

    The implied probabilities of the quoted cells *and* the residual bucket are de-vigged
    together, so the quoted cells are not inflated by the vig the residual would have
    absorbed. Per-cell fair probabilities are averaged across books.
    """
    per_book_cells: list[dict[tuple[int, int], float]] = []
    per_book_other: list[float] = []
    for market in match.market("correct_score"):
        quoted_implied: dict[tuple[int, int], float] = {}
        other_implied = 0.0
        has_other = False
        for outcome in market.outcomes:
            if not outcome.price or float(outcome.price) <= 0:
                continue
            score = parse_scoreline_label(outcome.name)
            if score is None:
                text = outcome.name.strip().lower()
                if "other" in text or "field" in text:
                    other_implied += 1.0 / float(outcome.price)
                    has_other = True
                continue
            home_goals, away_goals = score
            if home_goals <= max_goals and away_goals <= max_goals:
                quoted_implied[(home_goals, away_goals)] = 1.0 / float(outcome.price)
        if not quoted_implied:
            continue
        cells = list(quoted_implied)
        implied = np.array([quoted_implied[cell] for cell in cells] + ([other_implied] if has_other else []), dtype=float)
        fair = devig_implied_probabilities(implied, method)
        per_book_cells.append({cell: float(fair[idx]) for idx, cell in enumerate(cells)})
        per_book_other.append(float(fair[-1]) if has_other else 0.0)

    if not per_book_cells:
        return None

    all_cells = set().union(*(set(book) for book in per_book_cells))
    averaged = {cell: float(np.mean([book[cell] for book in per_book_cells if cell in book])) for cell in all_cells}
    other_mass = float(np.mean(per_book_other))
    total = sum(averaged.values()) + other_mass
    if total <= 0:
        return None
    return FairCorrectScoreMarket(
        cell_probs={cell: prob / total for cell, prob in averaged.items()},
        other_mass=other_mass / total,
        count=len(per_book_cells),
    )


def extract_correct_score_matrix(match: MatchOdds, max_goals: int, method: str = "power") -> np.ndarray | None:
    """Backwards-compatible full matrix with a uniform tail (no model prior)."""
    market = extract_correct_score_market(match, max_goals, method)
    if market is None:
        return None
    return market.to_matrix(max_goals)


def parse_scoreline_label(label: str) -> tuple[int, int] | None:
    text = label.strip().lower()
    if "other" in text or "field" in text:
        return None
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", text)
    if not match:
        match = re.search(r"\b(\d+)\D+(\d+)\b", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _map_1x2_market(match: MatchOdds, market: MarketOdds, method: str) -> np.ndarray | None:
    prices: dict[str, float] = {}
    home_key = canonical_team_key(match.home_team)
    away_key = canonical_team_key(match.away_team)
    for outcome in market.outcomes:
        name = outcome.name.strip().lower()
        name_key = canonical_team_key(outcome.name)
        if name in {"draw", "x", "tie"}:
            prices["draw"] = outcome.price
        elif name_key == home_key or (outcome.description or "").strip().lower() == "home":
            prices["home"] = outcome.price
        elif name_key == away_key or (outcome.description or "").strip().lower() == "away":
            prices["away"] = outcome.price

    if {"home", "draw", "away"} <= prices.keys():
        return decimal_implied_probabilities([prices["home"], prices["draw"], prices["away"]], method)

    if len(market.outcomes) == 3:
        ordered = list(market.outcomes)
        draw_idx = next((idx for idx, outcome in enumerate(ordered) if outcome.name.strip().lower() in {"draw", "x", "tie"}), None)
        if draw_idx is not None:
            remaining = [idx for idx in range(3) if idx != draw_idx]
            mapped_prices = [ordered[remaining[0]].price, ordered[draw_idx].price, ordered[remaining[1]].price]
            return decimal_implied_probabilities(mapped_prices, method)

    return None


def _power_devig(implied: np.ndarray) -> np.ndarray:
    low, high = 0.01, 10.0
    # 60 bisections over a width-10 bracket already converges to float precision.
    for _ in range(60):
        mid = (low + high) / 2.0
        total = float(np.power(implied, mid).sum())
        if total > 1.0:
            low = mid
        else:
            high = mid
    adjusted = np.power(implied, (low + high) / 2.0)
    return adjusted / adjusted.sum()
