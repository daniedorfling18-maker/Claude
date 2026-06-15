from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from superbru_score_engine.config import BettingConfig
from superbru_score_engine.ingest import MatchOdds, OutcomeOdds
from superbru_score_engine.model import DistributionResult
from superbru_score_engine.model.devig import parse_scoreline_label
from superbru_score_engine.model.team_names import canonical_team_key


@dataclass(frozen=True)
class BettingSuggestion:
    match_id: str
    commence_time: str
    home_team: str
    away_team: str
    market: str
    selection: str
    bookmaker: str
    decimal_odds: float
    model_probability: float
    expected_return: float
    break_even_odds: float
    kelly_fraction: float


def find_betting_suggestions(distribution: DistributionResult, config: BettingConfig) -> list[BettingSuggestion]:
    match = distribution.match
    suggestions: list[BettingSuggestion] = []
    suggestions.extend(_h2h_suggestions(match, distribution.matrix, config))
    if not config.match_winners_only:
        suggestions.extend(_totals_suggestions(match, distribution.matrix, config))
        suggestions.extend(_correct_score_suggestions(match, distribution.matrix, config))
    return sorted(suggestions, key=lambda item: item.expected_return, reverse=True)


def _h2h_suggestions(match: MatchOdds, matrix: np.ndarray, config: BettingConfig) -> list[BettingSuggestion]:
    outcome_probabilities = _market_anchor_1x2(match, matrix)
    rows: list[BettingSuggestion] = []
    for market in match.market("h2h"):
        for offered in market.outcomes:
            side = _map_h2h_outcome(match, offered)
            if side is None:
                continue
            if side == "draw" and not config.include_draws:
                continue
            rows.extend(
                _suggestion_if_value(
                    match=match,
                    market="1X2",
                    selection=_selection_name(match, side),
                    bookmaker=market.bookmaker,
                    price=offered.price,
                    model_probability=outcome_probabilities[side],
                    config=config,
                )
            )
    return rows


def _totals_suggestions(match: MatchOdds, matrix: np.ndarray, config: BettingConfig) -> list[BettingSuggestion]:
    rows: list[BettingSuggestion] = []
    total_goals = np.add.outer(np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
    market_anchor_totals = _market_anchor_totals(match)
    for market in match.market("totals"):
        for offered in market.outcomes:
            if offered.point is None:
                continue
            name = offered.name.strip().lower()
            anchored = market_anchor_totals.get(float(offered.point))
            if name.startswith("over"):
                probability = anchored[0] if anchored else float(matrix[total_goals > offered.point].sum())
                selection = f"Over {offered.point:g}"
            elif name.startswith("under"):
                probability = anchored[1] if anchored else float(matrix[total_goals < offered.point].sum())
                selection = f"Under {offered.point:g}"
            else:
                continue
            rows.extend(
                _suggestion_if_value(
                    match=match,
                    market="Totals",
                    selection=selection,
                    bookmaker=market.bookmaker,
                    price=offered.price,
                    model_probability=probability,
                    config=config,
                )
            )
    return rows


def _correct_score_suggestions(match: MatchOdds, matrix: np.ndarray, config: BettingConfig) -> list[BettingSuggestion]:
    rows: list[BettingSuggestion] = []
    for market in match.market("correct_score"):
        for offered in market.outcomes:
            score = parse_scoreline_label(offered.name)
            if score is None:
                continue
            home_goals, away_goals = score
            if home_goals >= matrix.shape[0] or away_goals >= matrix.shape[1]:
                continue
            rows.extend(
                _suggestion_if_value(
                    match=match,
                    market="Correct Score",
                    selection=f"{home_goals}-{away_goals}",
                    bookmaker=market.bookmaker,
                    price=offered.price,
                    model_probability=float(matrix[home_goals, away_goals]),
                    config=config,
                )
            )
    return rows


def _suggestion_if_value(
    match: MatchOdds,
    market: str,
    selection: str,
    bookmaker: str,
    price: float,
    model_probability: float,
    config: BettingConfig,
) -> list[BettingSuggestion]:
    if price <= 1.0 or model_probability <= 0:
        return []
    if model_probability < config.min_model_probability:
        return []
    if price > config.max_decimal_odds:
        return []
    expected_return = model_probability * price - 1.0
    if expected_return < config.min_expected_return:
        return []
    break_even_odds = 1.0 / model_probability
    kelly_fraction = expected_return / (price - 1.0)
    kelly_fraction = max(0.0, min(config.max_kelly_fraction, kelly_fraction))
    return [
        BettingSuggestion(
            match_id=match.match_id,
            commence_time=match.commence_time,
            home_team=match.home_team,
            away_team=match.away_team,
            market=market,
            selection=selection,
            bookmaker=bookmaker,
            decimal_odds=float(price),
            model_probability=float(model_probability),
            expected_return=float(expected_return),
            break_even_odds=float(break_even_odds),
            kelly_fraction=float(kelly_fraction),
        )
    ]


def _map_h2h_outcome(match: MatchOdds, offered: OutcomeOdds) -> str | None:
    name = offered.name.strip().lower()
    name_key = canonical_team_key(offered.name)
    if name in {"draw", "x", "tie"}:
        return "draw"
    if name_key == canonical_team_key(match.home_team) or (offered.description or "").strip().lower() == "home":
        return "home"
    if name_key == canonical_team_key(match.away_team) or (offered.description or "").strip().lower() == "away":
        return "away"
    return None


def _market_anchor_1x2(match: MatchOdds, matrix: np.ndarray) -> dict[str, float]:
    from superbru_score_engine.model.devig import extract_fair_1x2

    fair = extract_fair_1x2(match)
    if fair is not None:
        return {"home": fair.home, "draw": fair.draw, "away": fair.away}
    return {
        "home": float(np.tril(matrix, k=-1).sum()),
        "draw": float(np.trace(matrix)),
        "away": float(np.triu(matrix, k=1).sum()),
    }


def _market_anchor_totals(match: MatchOdds) -> dict[float, tuple[float, float]]:
    from collections import defaultdict

    from superbru_score_engine.model.devig import decimal_implied_probabilities

    grouped: dict[float, list[tuple[float, float]]] = defaultdict(list)
    for market in match.market("totals"):
        prices: dict[float, dict[str, float]] = defaultdict(dict)
        for offered in market.outcomes:
            if offered.point is None:
                continue
            name = offered.name.strip().lower()
            if name.startswith("over"):
                prices[float(offered.point)]["over"] = offered.price
            elif name.startswith("under"):
                prices[float(offered.point)]["under"] = offered.price
        for line, line_prices in prices.items():
            if "over" in line_prices and "under" in line_prices:
                probs = decimal_implied_probabilities([line_prices["over"], line_prices["under"]])
                grouped[line].append((float(probs[0]), float(probs[1])))
    return {
        line: (
            sum(item[0] for item in values) / len(values),
            sum(item[1] for item in values) / len(values),
        )
        for line, values in grouped.items()
        if values
    }


def _selection_name(match: MatchOdds, side: str) -> str:
    if side == "home":
        return match.home_team
    if side == "away":
        return match.away_team
    return "Draw"
