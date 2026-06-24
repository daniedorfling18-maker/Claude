from __future__ import annotations

from typing import Any

from .config import EngineConfig, load_config
from .market_relative_validation import validate_market_relative_model


def validate_model(cfg: EngineConfig) -> dict[str, Any]:
    return validate_market_relative_model(cfg)


def main(config_path: str) -> dict[str, Any]:
    return validate_model(load_config(config_path))
