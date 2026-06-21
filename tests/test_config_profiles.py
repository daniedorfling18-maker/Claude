from __future__ import annotations

from pathlib import Path

from superbru_score_engine.config import load_config, validate_calibration_profile


def test_active_config_matches_big5_profile() -> None:
    config = load_config("config.yaml")
    check = validate_calibration_profile(config, "calibration_profiles.yaml")
    assert check.profile_found
    assert check.matches_config
    assert check.active_profile == "big5_h2h_totals"


def test_active_config_loads_sensitivity_and_ratings_sections() -> None:
    config = load_config("config.yaml")
    assert config.sensitivity.enabled is True
    assert config.sensitivity.lambda_pct == 0.05
    assert config.sensitivity.exact_chase_weights == (1.0, 1.5, 2.0)
    assert config.ratings.enabled is True
    assert config.ratings.source == "internal_results_elo"
    assert config.ratings.k_factor == 24.0
    assert config.ratings.min_matches_full_confidence == 8


def test_mismatched_profile_is_flagged(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model:
  calibration_profile: demo
  devig_method: power
  dixon_coles_rho: -0.04
  odds_weight: 1.0
  ratings_weight: 0.0
""".strip(),
        encoding="utf-8",
    )
    profiles_path = tmp_path / "calibration_profiles.yaml"
    profiles_path.write_text(
        """
calibration_profiles:
  demo:
    devig_method: multiplicative
    dixon_coles_rho: -0.08
    odds_weight: 0.95
    ratings_weight: 0.05
""".strip(),
        encoding="utf-8",
    )
    config = load_config(config_path)
    check = validate_calibration_profile(config, profiles_path)
    assert check.profile_found
    assert not check.matches_config
    assert any("devig_method" in mismatch for mismatch in check.mismatches)
