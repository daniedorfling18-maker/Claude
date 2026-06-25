from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .market_relative_validation import validate_market_relative_model
from .models.skill_model import train_skill_model


def _skill_approved(summary: dict[str, Any]) -> bool:
    oos = summary.get("oos_vs_market", {}) if isinstance(summary, dict) else {}
    return bool(oos.get("beats_market_significantly"))


def validate_model(cfg: EngineConfig) -> dict[str, Any]:
    """Canonical validation route: skill model first, market-relative audit second.

    The midpoint-shrinkage baseline is useful as a calibration artefact, but it cannot
    prove independent alpha. Promotion decisions should therefore read the skill-model
    result as the canonical answer and use the market-relative validator as supporting
    audit evidence.
    """
    skill_summary = train_skill_model(cfg)
    market_summary = validate_market_relative_model(cfg)
    approved = _skill_approved(skill_summary) and not bool(market_summary.get("model_copies_market_midpoint"))
    return {
        "canonical_validation": "skill_model_market_relative",
        "skill_model": skill_summary,
        "market_relative_holdout": market_summary,
        "approved_for_paper_trading": approved,
        "approved_for_live_trading": False,
        "verdict": "skill model beats market OOS" if approved else "no canonical skill-model edge over the market OOS",
    }


def main(config_path: str) -> dict[str, Any]:
    return validate_model(load_config(config_path))
