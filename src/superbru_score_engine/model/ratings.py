from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from superbru_score_engine.config import RatingsConfig

from .team_names import canonical_team_name, team_key


@dataclass
class TeamRating:
    elo: float = 1500.0
    matches: int = 0
    goals_for: int = 0
    goals_against: int = 0


@dataclass(frozen=True)
class MatchResult:
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    venue_country: str | None = None
    match_id: str | None = None
    commence_time: str | None = None

    def identity(self) -> str:
        home = team_key(self.home_team)
        away = team_key(self.away_team)
        if self.match_id:
            return f"id:{self.match_id}"
        if self.commence_time:
            return f"time:{self.commence_time}:{home}:{away}"
        return f"score:{home}:{away}:{self.home_goals}:{self.away_goals}"


class RatingsStore:
    def __init__(self, path: str | Path | None = None, config: RatingsConfig | None = None) -> None:
        self.path = Path(path) if path else None
        self.config = config or RatingsConfig()
        self.teams: dict[str, TeamRating] = {}
        self.applied_results: set[str] = set()
        self.metadata: dict[str, object] = self._default_metadata()
        if self.path and self.path.exists():
            self.load()

    def _default_metadata(self) -> dict[str, object]:
        timestamp = _utc_now()
        return {
            "created_at": timestamp,
            "updated_at": timestamp,
            "source": self.config.source,
            "source_url": self.config.source_url,
            "cutoff_date": self.config.cutoff_date,
            "update_method": self.config.update_method,
            "k_factor": self.config.k_factor,
            "base_rating": self.config.base_rating,
            "base_total_goals": self.config.base_total_goals,
            "elo_goal_scale": self.config.elo_goal_scale,
            "min_matches_full_confidence": self.config.min_matches_full_confidence,
            "use_as_fallback_only": self.config.use_as_fallback_only,
            "number_of_applied_results": 0,
        }

    def diagnostics(self) -> dict[str, object]:
        data = dict(self.metadata)
        data["number_of_teams"] = len(self.teams)
        data["number_of_applied_results"] = len(self.applied_results)
        return data

    def get(self, team: str) -> TeamRating:
        team = canonical_team_name(team)
        if team not in self.teams:
            self.teams[team] = TeamRating(elo=self.config.base_rating)
        return self.teams[team]

    def load(self) -> None:
        assert self.path is not None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if "_teams" in payload:
            team_payload = payload.get("_teams", {})
            self.applied_results = set(payload.get("_applied_results", []))
            stored_metadata = payload.get("_metadata", {})
            if isinstance(stored_metadata, dict):
                self.metadata.update(stored_metadata)
        else:
            team_payload = {team: values for team, values in payload.items() if not str(team).startswith("_")}
            self.applied_results = set(payload.get("_applied_results", [])) if isinstance(payload, dict) else set()
        self.metadata.setdefault("created_at", _utc_now())
        self.metadata["updated_at"] = str(self.metadata.get("updated_at") or self.metadata["created_at"])
        self.metadata["number_of_applied_results"] = len(self.applied_results)
        self.teams = {canonical_team_name(team): TeamRating(**values) for team, values in team_payload.items()}

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata["updated_at"] = _utc_now()
        self.metadata["number_of_applied_results"] = len(self.applied_results)
        payload = {
            "_metadata": self.metadata,
            "_applied_results": sorted(self.applied_results),
            "_teams": {team: asdict(rating) for team, rating in sorted(self.teams.items())},
        }
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def update_results(self, results: Iterable[MatchResult], k_factor: float | None = None) -> tuple[int, int]:
        if not self.config.enabled:
            return 0, 0
        applied = 0
        skipped = 0
        k = self.config.k_factor if k_factor is None else float(k_factor)
        for result in results:
            identity = result.identity()
            if identity in self.applied_results:
                skipped += 1
                continue
            home_team = canonical_team_name(result.home_team)
            away_team = canonical_team_name(result.away_team)
            home = self.get(home_team)
            away = self.get(away_team)
            expected_home = 1.0 / (1.0 + 10 ** (-(home.elo - away.elo) / 400.0))
            actual_home = 1.0 if result.home_goals > result.away_goals else 0.0 if result.home_goals < result.away_goals else 0.5
            margin = abs(result.home_goals - result.away_goals)
            margin_multiplier = math.log(max(1, margin) + 1.0)
            change = k * margin_multiplier * (actual_home - expected_home)

            home.elo += change
            away.elo -= change
            home.matches += 1
            away.matches += 1
            home.goals_for += result.home_goals
            home.goals_against += result.away_goals
            away.goals_for += result.away_goals
            away.goals_against += result.home_goals
            self.applied_results.add(identity)
            applied += 1
        if applied:
            self.metadata["updated_at"] = _utc_now()
            self.metadata["number_of_applied_results"] = len(self.applied_results)
            self.metadata["k_factor"] = k
        return applied, skipped

    def prior_lambdas(
        self,
        home_team: str,
        away_team: str,
        neutral: bool = True,
        venue_country: str | None = None,
        host_teams: Iterable[str] = (),
        home_advantage_goals: float = 0.18,
        apply_home_advantage: bool = True,
        base_total_goals: float | None = None,
        elo_goal_scale: float | None = None,
        min_matches_full_confidence: int | None = None,
    ) -> tuple[float, float]:
        home = self.get(home_team)
        away = self.get(away_team)
        full_confidence = max(1, int(min_matches_full_confidence or self.config.min_matches_full_confidence))
        confidence = min(1.0, (home.matches + away.matches) / float(full_confidence))
        elo_diff = (home.elo - away.elo) * confidence

        home_team = canonical_team_name(home_team)
        away_team = canonical_team_name(away_team)
        host_set = {canonical_team_name(team) for team in host_teams}
        venue = team_key(venue_country or "")
        home_adv = 0.0
        if apply_home_advantage:
            if venue and home_team in host_set and _host_matches_venue(home_team, venue):
                home_adv += home_advantage_goals
            elif venue and away_team in host_set and _host_matches_venue(away_team, venue):
                home_adv -= home_advantage_goals
            elif not neutral:
                home_adv += home_advantage_goals

        scale = float(elo_goal_scale or self.config.elo_goal_scale)
        total = float(base_total_goals or self.config.base_total_goals)
        log_ratio = elo_diff / scale + home_adv
        home_share = 1.0 / (1.0 + math.exp(-log_ratio))
        return max(0.05, total * home_share), max(0.05, total * (1.0 - home_share))


def _host_matches_venue(team: str, venue: str) -> bool:
    text = canonical_team_name(team)
    return (
        text == "United States" and venue in {"usa", "us", "united states", "united states of america"}
        or text == "Canada" and venue == "canada"
        or text == "Mexico" and venue == "mexico"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
