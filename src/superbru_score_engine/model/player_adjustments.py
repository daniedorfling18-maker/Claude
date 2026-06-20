"""Player-level adjustment extension point.

The repository does NOT currently contain clean, pre-match, structured
player-level data (xG/xA/minutes/lineups). So this is an extension point only:

* The default adjustment for every team is ZERO.
* No fake player metrics or hard-coded xG values are introduced.

NOTE: this module is a standalone extension point and is NOT yet wired into the
CLI or ``OddsToScorelineModel`` — there is currently no ``--player-adjustments``
flag and nothing consumes these adjustments in the prediction path. A caller can
opt in directly via ``PlayerAdjustmentStore.from_csv`` and apply the effective
adjustments themselves; values are shrunk by a stated confidence first.

CSV columns: team, attack_adjustment_goals, defence_adjustment_goals,
goalkeeper_adjustment_goals, confidence, source, source_timestamp, note.

Effective adjustment = confidence * raw adjustment (simple credibility shrink).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from .team_names import canonical_team_name


@dataclass(frozen=True)
class PlayerAdjustment:
    team: str
    attack_adjustment_goals: float = 0.0
    defence_adjustment_goals: float = 0.0
    goalkeeper_adjustment_goals: float = 0.0
    confidence: float = 0.0
    source: str = ""
    source_timestamp: str = ""
    note: str = ""

    @property
    def effective_attack(self) -> float:
        return self.confidence * self.attack_adjustment_goals

    @property
    def effective_defence(self) -> float:
        # goalkeeper quality is part of how many goals a team concedes
        return self.confidence * (self.defence_adjustment_goals + self.goalkeeper_adjustment_goals)


# A zero adjustment used whenever no data is supplied for a team.
ZERO_ADJUSTMENT = PlayerAdjustment(team="", confidence=0.0)


class PlayerAdjustmentStore:
    """Lookup of team -> PlayerAdjustment. Empty unless a CSV is loaded."""

    def __init__(self) -> None:
        self._by_team: dict[str, PlayerAdjustment] = {}

    @property
    def applied(self) -> bool:
        return bool(self._by_team)

    def get(self, team: str) -> PlayerAdjustment:
        return self._by_team.get(canonical_team_name(team), ZERO_ADJUSTMENT)

    @classmethod
    def from_csv(cls, path: str | Path) -> "PlayerAdjustmentStore":
        store = cls()
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"player adjustments file not found: {path}")
        with p.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                team = (row.get("team") or "").strip()
                if not team:
                    continue
                store._by_team[canonical_team_name(team)] = PlayerAdjustment(
                    team=team,
                    attack_adjustment_goals=_f(row.get("attack_adjustment_goals")),
                    defence_adjustment_goals=_f(row.get("defence_adjustment_goals")),
                    goalkeeper_adjustment_goals=_f(row.get("goalkeeper_adjustment_goals")),
                    confidence=_f(row.get("confidence")),
                    source=(row.get("source") or "").strip(),
                    source_timestamp=(row.get("source_timestamp") or "").strip(),
                    note=(row.get("note") or "").strip(),
                )
        return store


def _f(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
