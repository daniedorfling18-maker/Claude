"""Model governance and reproducibility: run manifests and an assumption register.

Actuarial sign-off needs more than a number. It needs to know *which model* produced
it (code version, config fingerprint, seed, environment) so a result can be reproduced
or repudiated, and it needs the modelling assumptions written down with their rationale
rather than buried in code. This module provides both as plain dictionaries that callers
can serialise alongside their outputs.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import AppConfig


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def config_to_dict(config: AppConfig) -> dict[str, Any]:
    """Deterministic, JSON-safe view of the resolved configuration."""
    return _json_safe(dataclasses.asdict(config))


def config_fingerprint(config: AppConfig) -> str:
    """Stable sha256 over the resolved config - changes iff the effective config changes."""
    serialised = json.dumps(config_to_dict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def git_provenance() -> dict[str, Any]:
    """Best-effort git commit + dirty-tree flag; ``available`` is False off a checkout."""
    def _git(*args: str) -> str | None:
        try:
            result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    if commit is None:
        return {"available": False, "commit": None, "dirty": None, "branch": None}
    return {
        "available": True,
        "commit": commit,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(status) if status is not None else None,
    }


def environment_provenance() -> dict[str, Any]:
    """Python and key numeric-library versions for reproducibility."""
    versions: dict[str, Any] = {"python": sys.version.split()[0]}
    for package in ("numpy", "pandas", "scipy"):
        try:
            module = __import__(package)
            versions[package] = getattr(module, "__version__", None)
        except ImportError:
            versions[package] = None
    return versions


def fingerprint_file(path: str | Path) -> dict[str, Any]:
    """sha256 + byte size of an input file, for data provenance (``exists`` False if missing)."""
    file_path = Path(path)
    if not file_path.is_file():
        return {"path": str(file_path), "exists": False, "sha256": None, "bytes": None}
    data = file_path.read_bytes()
    return {
        "path": str(file_path),
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }


def run_manifest(config: AppConfig, seed: int | None = None, inputs: list[str | Path] | None = None) -> dict[str, Any]:
    """A reproducibility stamp: timestamp, config fingerprint, code version, environment, inputs."""
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": int(seed if seed is not None else config.seed),
        "calibration_profile": config.model.calibration_profile,
        "config_fingerprint_sha256": config_fingerprint(config),
        "git": git_provenance(),
        "environment": environment_provenance(),
        "inputs": [fingerprint_file(path) for path in (inputs or [])],
    }


def assumption_register(config: AppConfig) -> list[dict[str, Any]]:
    """The model's load-bearing assumptions, each with its value and rationale.

    Written so a reviewer can challenge each choice without reading the source. Values are
    pulled live from the resolved config, so the register can never drift from the run.
    """
    model = config.model
    superbru = config.superbru
    ratings = config.ratings
    return [
        {
            "assumption": "de-vig method",
            "value": model.devig_method,
            "rationale": "How bookmaker over-round is removed to recover fair probabilities; "
            "calibrated against historical league results.",
        },
        {
            "assumption": "Dixon-Coles low-score correction (rho)",
            "value": model.dixon_coles_rho,
            "rationale": "Adjusts independence of the two Poisson scores for 0-0/1-0/0-1/1-1; "
            "negative rho lifts low-scoring draws, fitted on league data.",
        },
        {
            "assumption": "odds vs ratings blend",
            "value": {"odds_weight": model.odds_weight, "ratings_weight": model.ratings_weight,
                      "ratings_use_as_fallback_only": ratings.use_as_fallback_only},
            "rationale": "Market odds are assumed to dominate; internal Elo ratings are a "
            "fallback only when no usable 1X2 market exists.",
        },
        {
            "assumption": "totals constraint",
            "value": "all quoted over/under lines fit jointly (count-weighted budget)",
            "rationale": "Multiple lines sharpen the goal-distribution shape; collapses to the "
            "single 2.5-line fit when only one line is quoted.",
        },
        {
            "assumption": "Asian handicap objective weight",
            "value": model.asian_handicap_weight,
            "rationale": "Weight on matching the fair Asian-handicap line in the lambda solve; "
            "0 disables it (default), as backtests showed no Superbru-points gain.",
        },
        {
            "assumption": "manual home/host advantage (goals)",
            "value": model.home_advantage_goals,
            "rationale": "Applied only on the ratings-only fallback path and venue-conditioned "
            "for host nations; market odds are assumed to already price venue.",
        },
        {
            "assumption": "Superbru closeness-index cutoff",
            "value": superbru.ci_cutoff,
            "rationale": "|goal-diff error| + |total error|/2 <= cutoff counts as a close (1.5pt) "
            "pick; defines the close-result scoring boundary.",
        },
        {
            "assumption": "pick strategy mode",
            "value": superbru.strategy_mode,
            "rationale": "raw_ev maximises expected Superbru points; alternative modes trade "
            "expected points for pool-rank variance and are off by default.",
        },
        {
            "assumption": "scoreline grids (candidate/model/solver goals)",
            "value": {"candidate": model.candidate_grid_goals, "model": model.model_grid_goals,
                      "solver": model.solver_grid_goals},
            "rationale": "Truncation limits for the score matrix; mass outside the grid is "
            "tracked and renormalised in diagnostics.",
        },
        {
            "assumption": "public-pick model is synthetic",
            "value": config.public_pick.enabled,
            "rationale": "Real pool picks are never visible before lock; the public-pick shares "
            "are a transparent heuristic and never affect the default raw-EV pick.",
        },
    ]
