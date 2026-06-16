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
    calibration_profile: str = ""
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
    # Strategy layer. Defaults reproduce the raw expected-points pick exactly.
    # modes: raw_ev | conservative | exact_chase | contrarian | risk_adjusted
    strategy_mode: str = "raw_ev"
    exact_chase_weight: float = 1.0       # >1 tilts toward the modal (high-P_exact) score
    public_pick_weight: float = 0.0       # alpha: reward EV above the synthetic field EV
    differentiation_weight: float = 0.0   # delta: reward low synthetic public-pick share
    risk_aversion: float = 0.0            # beta: penalise P(zero points)
    variance_penalty: float = 0.0         # gamma: penalise points variance


@dataclass(frozen=True)
class SensitivityConfig:
    enabled: bool = True
    lambda_pct: float = 0.05
    rho_delta: float = 0.02
    total_goals_delta: float = 0.15
    public_bias_pct: float = 0.20
    exact_chase_weights: tuple[float, ...] = (1.0, 1.5, 2.0)
    stability_warning_threshold: float = 0.70


@dataclass(frozen=True)
class RatingsConfig:
    enabled: bool = True
    source: str = "internal_results_elo"
    source_url: str = ""
    cutoff_date: str = ""
    update_method: str = "elo_margin_log_multiplier"
    k_factor: float = 24.0
    base_rating: float = 1500.0
    base_total_goals: float = 2.45
    elo_goal_scale: float = 650.0
    min_matches_full_confidence: int = 8
    use_as_fallback_only: bool = True


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


_DEFAULT_COMMON_SCORELINE_WEIGHTS = {
    "1-0": 1.15, "2-0": 1.30, "2-1": 1.25, "1-1": 0.85, "0-0": 0.60,
    "3-0": 1.15, "0-1": 0.85, "0-2": 0.75, "1-2": 0.80, "3-1": 0.95, "0-3": 0.70,
}
_DEFAULT_FAMOUS_TEAMS = (
    "Brazil", "Argentina", "France", "England", "Germany", "Spain",
    "Portugal", "Netherlands", "Italy", "United States", "Mexico",
)


@dataclass(frozen=True)
class PublicPickConfig:
    """Heuristic, transparent model of how a Superbru pool *probably* picks.

    SYNTHETIC: real pool picks are never visible before lock. Outputs are
    estimates only and are labelled as such; the default raw-EV pick never uses them.
    """

    enabled: bool = True
    favourite_bias: float = 1.25
    famous_team_bias: float = 1.15
    clean_sheet_win_bias: float = 1.10
    draw_penalty: float = 0.75
    nil_nil_penalty: float = 0.65
    underdog_penalty: float = 0.80
    default_template_weight: float = 0.45
    common_scoreline_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_COMMON_SCORELINE_WEIGHTS)
    )
    famous_teams: tuple[str, ...] = _DEFAULT_FAMOUS_TEAMS


@dataclass(frozen=True)
class AppConfig:
    seed: int = 20260611
    providers: ProviderConfig = field(default_factory=ProviderConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    superbru: SuperbruConfig = field(default_factory=SuperbruConfig)
    sensitivity: SensitivityConfig = field(default_factory=SensitivityConfig)
    ratings: RatingsConfig = field(default_factory=RatingsConfig)
    betting: BettingConfig = field(default_factory=BettingConfig)
    paths: PathConfig = field(default_factory=PathConfig)
    public_pick: PublicPickConfig = field(default_factory=PublicPickConfig)


@dataclass(frozen=True)
class CalibrationProfileCheck:
    active_profile: str
    profile_found: bool
    matches_config: bool
    mismatches: tuple[str, ...]
    profile_values: dict[str, Any]
    evidence_note: str = ""


def load_config(path: str | Path | None) -> AppConfig:
    raw: dict[str, Any] = {}
    if path:
        config_path = Path(path)
        if config_path.exists():
            raw = load_mapping_file(config_path)

    providers = raw.get("providers", {})
    model = raw.get("model", {})
    superbru = raw.get("superbru", {})
    sensitivity = raw.get("sensitivity", {})
    ratings = raw.get("ratings", {})
    betting = raw.get("betting", {})
    paths = raw.get("paths", {})
    public_pick = raw.get("public_pick", {})

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
            calibration_profile=str(model.get("calibration_profile", raw.get("active_calibration_profile", ""))),
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
            strategy_mode=str(superbru.get("strategy_mode", "raw_ev")).strip().lower(),
            exact_chase_weight=float(superbru.get("exact_chase_weight", 1.0)),
            public_pick_weight=float(superbru.get("public_pick_weight", 0.0)),
            differentiation_weight=float(superbru.get("differentiation_weight", 0.0)),
            risk_aversion=float(superbru.get("risk_aversion", 0.0)),
            variance_penalty=float(superbru.get("variance_penalty", 0.0)),
        ),
        sensitivity=SensitivityConfig(
            enabled=bool(sensitivity.get("enabled", True)),
            lambda_pct=float(sensitivity.get("lambda_pct", 0.05)),
            rho_delta=float(sensitivity.get("rho_delta", 0.02)),
            total_goals_delta=float(sensitivity.get("total_goals_delta", 0.15)),
            public_bias_pct=float(sensitivity.get("public_bias_pct", 0.20)),
            exact_chase_weights=tuple(float(v) for v in sensitivity.get("exact_chase_weights", (1.0, 1.5, 2.0))),
            stability_warning_threshold=float(sensitivity.get("stability_warning_threshold", 0.70)),
        ),
        ratings=RatingsConfig(
            enabled=bool(ratings.get("enabled", True)),
            source=str(ratings.get("source", "internal_results_elo")),
            source_url=str(ratings.get("source_url", "")),
            cutoff_date=str(ratings.get("cutoff_date", "")),
            update_method=str(ratings.get("update_method", "elo_margin_log_multiplier")),
            k_factor=float(ratings.get("k_factor", 24.0)),
            base_rating=float(ratings.get("base_rating", 1500.0)),
            base_total_goals=float(ratings.get("base_total_goals", 2.45)),
            elo_goal_scale=float(ratings.get("elo_goal_scale", 650.0)),
            min_matches_full_confidence=int(ratings.get("min_matches_full_confidence", 8)),
            use_as_fallback_only=bool(ratings.get("use_as_fallback_only", True)),
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
        public_pick=PublicPickConfig(
            enabled=bool(public_pick.get("enabled", True)),
            favourite_bias=float(public_pick.get("favourite_bias", 1.25)),
            famous_team_bias=float(public_pick.get("famous_team_bias", 1.15)),
            clean_sheet_win_bias=float(public_pick.get("clean_sheet_win_bias", 1.10)),
            draw_penalty=float(public_pick.get("draw_penalty", 0.75)),
            nil_nil_penalty=float(public_pick.get("nil_nil_penalty", 0.65)),
            underdog_penalty=float(public_pick.get("underdog_penalty", 0.80)),
            default_template_weight=float(public_pick.get("default_template_weight", 0.45)),
            common_scoreline_weights={
                str(k): float(v)
                for k, v in (public_pick.get("common_scoreline_weights") or _DEFAULT_COMMON_SCORELINE_WEIGHTS).items()
            },
            famous_teams=tuple(public_pick.get("famous_teams", _DEFAULT_FAMOUS_TEAMS)),
        ),
    )


def load_mapping_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8-sig")
    if file_path.suffix.lower() == ".json":
        payload = json.loads(text)
    elif yaml is None:
        payload = minimal_yaml_load(text)
    else:
        payload = yaml.safe_load(text) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a mapping in {file_path}")
    return payload


def validate_calibration_profile(
    config: AppConfig,
    profiles_path: str | Path = "calibration_profiles.yaml",
    *,
    tolerance: float = 1e-9,
) -> CalibrationProfileCheck:
    """Compare the loaded model config with the named calibration profile.

    This is deliberately a governance check, not a modelling transform: the engine
    still reads the flat config values, but this check prevents silent drift from
    the documented calibration profile.
    """

    active_profile = config.model.calibration_profile
    if not active_profile:
        return CalibrationProfileCheck(
            active_profile="",
            profile_found=False,
            matches_config=False,
            mismatches=("model.calibration_profile is not set",),
            profile_values={},
        )

    path = Path(profiles_path)
    if not path.exists():
        return CalibrationProfileCheck(
            active_profile=active_profile,
            profile_found=False,
            matches_config=False,
            mismatches=(f"calibration profile file not found: {path}",),
            profile_values={},
        )

    payload = load_mapping_file(path)
    profiles = payload.get("calibration_profiles", {})
    if not isinstance(profiles, dict) or active_profile not in profiles:
        return CalibrationProfileCheck(
            active_profile=active_profile,
            profile_found=False,
            matches_config=False,
            mismatches=(f"profile not found: {active_profile}",),
            profile_values={},
        )

    profile = dict(profiles[active_profile])
    evidence_note = str(profile.get("evidence_note", ""))
    comparable_keys = ("devig_method", "dixon_coles_rho", "odds_weight", "ratings_weight")
    mismatches: list[str] = []
    for key in comparable_keys:
        if key not in profile:
            mismatches.append(f"{key}: missing from profile")
            continue
        config_value = getattr(config.model, key)
        profile_value = profile[key]
        if isinstance(config_value, float) or isinstance(profile_value, float):
            if abs(float(config_value) - float(profile_value)) > tolerance:
                mismatches.append(f"{key}: config={config_value!r}, profile={profile_value!r}")
        elif str(config_value).lower() != str(profile_value).lower():
            mismatches.append(f"{key}: config={config_value!r}, profile={profile_value!r}")

    return CalibrationProfileCheck(
        active_profile=active_profile,
        profile_found=True,
        matches_config=not mismatches,
        mismatches=tuple(mismatches),
        profile_values=profile,
        evidence_note=evidence_note,
    )


def calibration_check_rows(check: CalibrationProfileCheck) -> list[dict[str, Any]]:
    rows = [
        {"field": "active_profile", "value": check.active_profile},
        {"field": "profile_found", "value": check.profile_found},
        {"field": "matches_config", "value": check.matches_config},
    ]
    for key in ("devig_method", "dixon_coles_rho", "odds_weight", "ratings_weight"):
        if key in check.profile_values:
            rows.append({"field": f"profile.{key}", "value": check.profile_values[key]})
    if check.evidence_note:
        rows.append({"field": "evidence_note", "value": check.evidence_note})
    for mismatch in check.mismatches:
        rows.append({"field": "mismatch", "value": mismatch})
    return rows


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
