"""Synthetic public-pick model.

We never see Superbru pool picks before lock, so any "differentiation" or
"edge" logic must rely on an ESTIMATE of how the field picks. This is a
transparent heuristic, not real pool data, and every output is labelled
synthetic. It encodes well-known public biases: over-picking favourites,
famous teams and clean-sheet wins; under-picking draws (especially 0-0) and
underdog wins.

Shares are normalised to sum to 1 over the supplied candidate scorelines.
"""
from __future__ import annotations

from dataclasses import dataclass

from superbru_score_engine.config import PublicPickConfig
from superbru_score_engine.model.team_names import canonical_team_name


@dataclass(frozen=True)
class PublicPickEstimate:
    home_goals: int
    away_goals: int
    public_pick_share: float  # SYNTHETIC estimate, not real pool data

    @property
    def scoreline(self) -> str:
        return f"{self.home_goals}-{self.away_goals}"

    @property
    def differentiation(self) -> float:
        return 1.0 - self.public_pick_share


def _candidate_outcome(home: int, away: int) -> str:
    if home > away:
        return "home"
    if home < away:
        return "away"
    return "draw"


def _raw_public_score(
    home_goals: int,
    away_goals: int,
    *,
    favourite_outcome: str,
    home_famous: bool,
    away_famous: bool,
    config: PublicPickConfig,
) -> float:
    key = f"{home_goals}-{away_goals}"
    weight = config.common_scoreline_weights.get(key, config.default_template_weight)

    outcome = _candidate_outcome(home_goals, away_goals)
    if outcome == "draw":
        weight *= config.draw_penalty
        if home_goals == 0 and away_goals == 0:
            weight *= config.nil_nil_penalty
    elif outcome == favourite_outcome:
        weight *= config.favourite_bias
    else:  # the non-favourite team winning = underdog pick
        weight *= config.underdog_penalty

    # reputation bias attaches to the winning side only
    if outcome == "home" and home_famous:
        weight *= config.famous_team_bias
    elif outcome == "away" and away_famous:
        weight *= config.famous_team_bias

    # clean-sheet win bias (the loser is kept to 0)
    if outcome == "home" and away_goals == 0:
        weight *= config.clean_sheet_win_bias
    elif outcome == "away" and home_goals == 0:
        weight *= config.clean_sheet_win_bias

    return max(weight, 1e-9)


def estimate_public_pick_shares(
    candidates: list[tuple[int, int]],
    *,
    favourite_outcome: str,
    home_team: str = "",
    away_team: str = "",
    config: PublicPickConfig | None = None,
) -> dict[tuple[int, int], PublicPickEstimate]:
    """Return a SYNTHETIC public-pick share per candidate scoreline (sums to 1)."""
    config = config or PublicPickConfig()
    famous = {canonical_team_name(t) for t in config.famous_teams}
    home_famous = canonical_team_name(home_team) in famous if home_team else False
    away_famous = canonical_team_name(away_team) in famous if away_team else False

    raw = {
        (h, a): _raw_public_score(
            h, a,
            favourite_outcome=favourite_outcome,
            home_famous=home_famous,
            away_famous=away_famous,
            config=config,
        )
        for (h, a) in candidates
    }
    total = sum(raw.values()) or 1.0
    return {
        (h, a): PublicPickEstimate(home_goals=h, away_goals=a, public_pick_share=score / total)
        for (h, a), score in raw.items()
    }
