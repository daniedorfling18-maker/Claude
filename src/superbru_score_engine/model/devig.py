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


def extract_correct_score_matrix(match: MatchOdds, max_goals: int, method: str = "power") -> np.ndarray | None:
    matrices: list[np.ndarray] = []
    for market in match.market("correct_score"):
        cells: dict[tuple[int, int], float] = {}
        for outcome in market.outcomes:
            score = parse_scoreline_label(outcome.name)
            if score is None:
                continue
            home_goals, away_goals = score
            if home_goals <= max_goals and away_goals <= max_goals:
                cells[(home_goals, away_goals)] = outcome.price
        if not cells:
            continue
        probs = decimal_implied_probabilities(list(cells.values()), method)
        matrix = np.zeros((max_goals + 1, max_goals + 1), dtype=float)
        for (cell, prob) in zip(cells.keys(), probs):
            matrix[cell] = prob
        if matrix.sum() > 0:
            matrices.append(matrix / matrix.sum())
    if not matrices:
        return None
    blended = np.stack(matrices).mean(axis=0)
    return blended / blended.sum()


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
    for _ in range(100):
        mid = (low + high) / 2.0
        total = float(np.power(implied, mid).sum())
        if total > 1.0:
            low = mid
        else:
            high = mid
    adjusted = np.power(implied, (low + high) / 2.0)
    return adjusted / adjusted.sum()
