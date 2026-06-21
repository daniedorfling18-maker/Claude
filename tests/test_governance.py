from __future__ import annotations

import dataclasses

from superbru_score_engine.config import AppConfig, ModelConfig
from superbru_score_engine.governance import (
    assumption_register,
    config_fingerprint,
    environment_provenance,
    fingerprint_file,
    git_provenance,
    run_manifest,
)


def test_config_fingerprint_is_deterministic_and_sensitive() -> None:
    config = AppConfig()
    assert config_fingerprint(config) == config_fingerprint(AppConfig())
    changed = dataclasses.replace(config, model=dataclasses.replace(config.model, dixon_coles_rho=-0.12))
    assert config_fingerprint(changed) != config_fingerprint(config)


def test_run_manifest_has_reproducibility_fields() -> None:
    manifest = run_manifest(AppConfig(), seed=123)
    assert manifest["seed"] == 123
    assert len(manifest["config_fingerprint_sha256"]) == 64
    assert "generated_at_utc" in manifest
    assert set(manifest["git"]).issuperset({"available", "commit", "dirty"})
    assert "python" in manifest["environment"]
    assert manifest["inputs"] == []


def test_run_manifest_fingerprints_named_inputs(tmp_path) -> None:
    data = tmp_path / "odds.json"
    data.write_text("{}", encoding="utf-8")
    manifest = run_manifest(AppConfig(), inputs=[data, tmp_path / "missing.csv"])
    present, missing = manifest["inputs"]
    assert present["exists"] is True and len(present["sha256"]) == 64
    assert missing["exists"] is False and missing["sha256"] is None


def test_assumption_register_documents_key_choices() -> None:
    config = dataclasses.replace(AppConfig(), model=ModelConfig(devig_method="multiplicative", dixon_coles_rho=-0.08))
    register = assumption_register(config)
    assert all({"assumption", "value", "rationale"} <= set(entry) for entry in register)
    by_name = {entry["assumption"]: entry for entry in register}
    assert by_name["de-vig method"]["value"] == "multiplicative"
    assert by_name["Dixon-Coles low-score correction (rho)"]["value"] == -0.08
    assert "Superbru closeness-index cutoff" in by_name
    assert "Asian handicap objective weight" in by_name


def test_provenance_helpers_are_safe() -> None:
    git = git_provenance()
    assert "available" in git  # never raises, even off a checkout
    assert "python" in environment_provenance()
    missing = fingerprint_file("definitely/not/here.csv")
    assert missing["exists"] is False
