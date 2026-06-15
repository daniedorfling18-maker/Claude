from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only in minimal runtimes.
    yaml = None


@dataclass(frozen=True)
class ProviderConfig:
    preferred: str = "oddspedia"
    oddspedia: dict[str, Any] = field(default_factory=dict)
    the_odds_api: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelConfig:
    candidate_grid_goals: int = 6
    model_grid_goals: int = 10
    solver_grid_goals: int = 14
    dixon_coles_rho: float = -0.04
    devig_method: str = "power"
    correct_score_blend_weight: float = 0.0
    odds_weight: float = 1.0
    ratings_weight: float = 0.0
    home_advantage_goals: float = 0.18
    host_teams: tuple[str, ...] = ("United States", "USA", "Canada", "Mexico")
    low_data_prior_sigma: float = 0.25


@dataclass(frozen=True)
class SuperbruConfig:
    ci_cutoff: float = 1.5
    tie_epsilon: float = 0.005
    contrarian: bool = False
    contrarian_weight: float = 0.0
    knockout_result_basis: str = "regular_or_extra_time_if_drawn"


@dataclass(frozen=True)
class BettingConfig:
    enabled: bool = False
    min_expected_return: float = 0.02
    max_kelly_fraction: float = 0.02
    min_model_probability: float = 0.55
    max_decimal_odds: float = 2.10
    match_winners_only: bool = True
    include_draws: bool = False


@dataclass(frozen=True)
class PathConfig:
    cache_dir: Path = Path("work/cache")
    ratings_store: Path = Path("work/ratings.json")


@dataclass(frozen=True)
class AppConfig:
    seed: int = 20260611
    providers: ProviderConfig = field(default_factory=ProviderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    superbru: SuperbruConfig = field(default_factory=SuperbruConfig)
    betting: BettingConfig = field(default_factory=BettingConfig)
    paths: PathConfig = field(default_factory=PathConfig)


def load_config(path: str | Path | None) -> AppConfig:
    raw: dict[str, Any] = {}
    if path:
        config_path = Path(path)
        if config_path.exists():
            text = config_path.read_text(encoding="utf-8")
            if config_path.suffix.lower() == ".json":
                raw = json.loads(text)
            elif yaml is None:
                raw = minimal_yaml_load(text)
            else:
                raw = yaml.safe_load(text) or {}

    providers = raw.get("providers", {})
    model = raw.get("model", {})
    superbru = raw.get("superbru", {})
    betting = raw.get("betting", {})
    paths = raw.get("paths", {})

    return AppConfig(
        seed=int(raw.get("seed", 20260611)),
        providers=ProviderConfig(
            preferred=str(providers.get("preferred", "oddspedia")).lower(),
            oddspedia=dict(providers.get("oddspedia", {})),
            the_odds_api=dict(providers.get("the_odds_api", {})),
            results=dict(providers.get("results", {})),
        ),
        model=ModelConfig(
            candidate_grid_goals=int(model.get("candidate_grid_goals", 6)),
            model_grid_goals=int(model.get("model_grid_goals", 10)),
            solver_grid_goals=int(model.get("solver_grid_goals", 14)),
            dixon_coles_rho=float(model.get("dixon_coles_rho", -0.04)),
            devig_method=str(model.get("devig_method", "power")).lower(),
            correct_score_blend_weight=float(model.get("correct_score_blend_weight", 0.0)),
            odds_weight=float(model.get("odds_weight", 1.0)),
            ratings_weight=float(model.get("ratings_weight", 0.0)),
            home_advantage_goals=float(model.get("home_advantage_goals", 0.18)),
            host_teams=tuple(model.get("host_teams", ("United States", "USA", "Canada", "Mexico"))),
            low_data_prior_sigma=float(model.get("low_data_prior_sigma", 0.25)),
        ),
        superbru=SuperbruConfig(
            ci_cutoff=float(superbru.get("ci_cutoff", 1.5)),
            tie_epsilon=float(superbru.get("tie_epsilon", 0.005)),
            contrarian=bool(superbru.get("contrarian", False)),
            contrarian_weight=float(superbru.get("contrarian_weight", 0.0)),
            knockout_result_basis=str(superbru.get("knockout_result_basis", "regular_or_extra_time_if_drawn")),
        ),
        betting=BettingConfig(
            enabled=bool(betting.get("enabled", False)),
            min_expected_return=float(betting.get("min_expected_return", 0.02)),
            max_kelly_fraction=float(betting.get("max_kelly_fraction", 0.02)),
            min_model_probability=float(betting.get("min_model_probability", 0.55)),
            max_decimal_odds=float(betting.get("max_decimal_odds", 2.10)),
            match_winners_only=bool(betting.get("match_winners_only", True)),
            include_draws=bool(betting.get("include_draws", False)),
        ),
        paths=PathConfig(
            cache_dir=Path(paths.get("cache_dir", "work/cache")),
            ratings_store=Path(paths.get("ratings_store", "work/ratings.json")),
        ),
    )


def env_value(config: dict[str, Any], key_name: str = "api_key") -> str | None:
    explicit = config.get(key_name)
    if explicit:
        return str(explicit)
    env_name = config.get(f"{key_name}_env")
    if env_name:
        return os.environ.get(str(env_name))
    return None


def minimal_yaml_load(text: str) -> dict[str, Any]:
    lines = _yaml_lines(text)
    if not lines:
        return {}
    value, _ = _parse_yaml_block(lines, 0, lines[0][0])
    if not isinstance(value, dict):
        raise ValueError("Top-level config must be a mapping")
    return value


def _yaml_lines(text: str) -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        no_comment = raw.split("#", 1)[0].rstrip()
        if not no_comment.strip():
            continue
        indent = len(no_comment) - len(no_comment.lstrip(" "))
        rows.append((indent, no_comment.strip()))
    return rows


def _parse_yaml_block(lines: list[tuple[int, str]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    is_list = lines[index][1].startswith("- ")
    if is_list:
        values: list[Any] = []
        while index < len(lines) and lines[index][0] == indent and lines[index][1].startswith("- "):
            item = lines[index][1][2:].strip()
            if item:
                values.append(_parse_scalar(item))
                index += 1
            else:
                value, index = _parse_yaml_block(lines, index + 1, lines[index + 1][0])
                values.append(value)
        return values, index

    mapping: dict[str, Any] = {}
    while index < len(lines) and lines[index][0] == indent and not lines[index][1].startswith("- "):
        content = lines[index][1]
        if ":" not in content:
            raise ValueError(f"Invalid config line: {content}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            mapping[key] = _parse_scalar(raw_value)
        elif index < len(lines) and lines[index][0] > indent:
            value, index = _parse_yaml_block(lines, index, lines[index][0])
            mapping[key] = value
        else:
            mapping[key] = {}
    return mapping, index


def _parse_scalar(value: str) -> Any:
    if value in {'""', "''"}:
        return ""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if any(char in value for char in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value.strip("\"'")
