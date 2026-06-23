from __future__ import annotations

from typing import Any

from ..config import EngineConfig, load_config
from ..utils import write_json


def train_category_calibration(cfg: EngineConfig) -> dict[str, Any]:
    output_dir = cfg.output_root / "polymarket_models"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"status": "not_enough_category_rows", "trained_categories": 0}
    write_json(output_dir / "category_calibration_v2.json", payload)
    return payload


def main(config_path: str) -> dict[str, Any]:
    return train_category_calibration(load_config(config_path))
